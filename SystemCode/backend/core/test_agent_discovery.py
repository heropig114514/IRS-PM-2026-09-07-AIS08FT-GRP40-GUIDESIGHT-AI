"""Responsibilities: Verify registry-driven scope enforcement and provider-driven POI discovery.
Implementation: Real MCP registry/runtime; mock Qwen, browser and HTTP boundaries only; exercise
serialized-context accounting, lossless category compaction and a complete simulated navigation.
Related Modules: agent.skills/runtime/tools/poi; test_agent supplies model-message fixtures.
Declaration Index: DiscoveryTests; DiscoveryTests.setUp; DiscoveryTests.test_registry_is_source;
DiscoveryTests.test_scope_before_side_effects; DiscoveryTests.test_selected_scope;
DiscoveryTests.test_category_discovery_requires_live_origin;
DiscoveryTests.test_scope_validation; DiscoveryTests.test_live_category_contract;
DiscoveryTests.test_category_required; DiscoveryTests.test_nearby_contract;
DiscoveryTests.test_invalid_poi_and_failure; DiscoveryTests.test_registry_is_source.probe.
DiscoveryTests.test_history_preserves_action_evidence.
DiscoveryTests.test_search_limit_blocks_requery.
DiscoveryTests.test_result_counts_end_actions.
DiscoveryTests.test_context_limit_includes_tool_schemas.
DiscoveryTests.test_navigation_workflow_completes_with_exact_candidate_coordinates;
DiscoveryTests.test_navigation_workflow_completes_with_exact_candidate_coordinates.browser_call.
Variable Index: None; all service/device evidence in this file is synthetic.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx2
from django.test import SimpleTestCase
from mcp.client import Client

from core.agent import poi
from core.agent.provider import AgentError, enforce_context_limit
from core.agent.runtime import Conversation, history_context
from core.agent.skills import fingerprint, tool_policy
from core.agent.tools import BrowserTools, create_server
from core.test_agent import tool_message


class DiscoveryTests(SimpleTestCase):
    """Functionality: Verify generic discovery and execution boundaries without real services.
    Logic: Assert denied calls cause no side effects; POI IDs/names come from HTTP responses.
    Constraints: Passing mocks does not establish provider coverage or model interpretation.
    """

    def setUp(self):
        """Functionality: Keep discovery tests independent of external intent inference.
        Inputs: explicit navigation provider result; Outputs: per-test model boundary double.
        Logic: Actual classifier validates the fixture; runtime and MCP remain real.
        Constraints: Does not assert natural-language classification quality.
        """
        decision = tool_message("route_intent", {
            "route": "system", "intent_ids": ["navigation.start"], "language": "en",
        })
        self.enterContext(patch(
            "core.agent.intents.completion", AsyncMock(return_value=decision),
        ))

    async def test_registry_is_source(self):
        """Functionality: Verify discovery follows registration, including a new capability.
        Inputs: actual server plus test-only probe; Outputs: exact descriptor parity.
        Logic: Add a callable via public SDK, inspect catalog via real MCP.
        Constraints: Registration alone does not authorize it for a loaded workflow.
        """
        server = create_server("owned-tab")

        def probe(value: int) -> dict:
            """Functionality: Supply an independently registered test capability.
            Inputs: value; Outputs: unchanged integer in JSON. Logic: identity.
            Constraints: Never interacts with a browser or production service.
            """
            return {"value": value}

        server.add_tool(probe, name="test_probe", description="Synthetic probe")
        async with Client(server) as client:
            actual = (await client.list_tools()).tools
            result = (await client.call_tool("discover_tools", {})).structured_content
        self.assertEqual(
            {tool.name: tool.description for tool in actual},
            {tool["name"]: tool["description"] for tool in result["tools"]},
        )
        self.assertFalse(result["readiness_verified"])
        for skill in result["skills"]:
            self.assertNotIn("test_probe", skill["tools"])
            self.assertEqual(skill["missing_tools"], [])

    async def test_scope_before_side_effects(self):
        """Functionality: Reject skipped discovery, skipped skill and cross-domain operations.
        Inputs: three scripted protocol violations; Outputs: error without any browser command.
        Logic: Real runtime validates before dispatch even when model ignores forced tool choice.
        Constraints: No corrective model retry or implicit substitution is expected.
        """
        cases = [
            [],
            [tool_message("discover_tools")],
            [tool_message("discover_tools"), tool_message("read_skill", {"name": "navigation"})],
        ]
        for prefix in cases:
            with self.subTest(prefix_length=len(prefix)):
                conversation = Conversation("owned-tab", "token")
                replies = [*prefix, tool_message("camera_start")]
                with patch("core.agent.runtime.completion", AsyncMock(side_effect=replies)):
                    with patch("core.ai_mcp.browser_call", AsyncMock()) as browser:
                        await conversation.run("test", "request", None)
                browser.assert_not_awaited()
                self.assertIn(
                    "invalid_tool_call", [event.get("code") for event in conversation.events]
                )
        conversation = Conversation("owned-tab", "token")
        with patch(
            "core.agent.runtime.completion",
            AsyncMock(
                return_value={
                    "role": "assistant",
                    "content": "Done without discovery",
                }
            ),
        ):
            await conversation.run("test", "request", None)
        self.assertIn("discovery_required", [event.get("code") for event in conversation.events])

    async def test_category_discovery_requires_live_origin(self):
        """Functionality: Deny POI category lookup until this turn obtains live device origin.
        Inputs: synthetic protocol call that skips maps_locate; Outputs: invalid-tool event and
        bounded rejection log identifying the missing prerequisite.
        Logic: Exercise the real runtime policy and ensure no category-provider request occurs.
        Constraints: Browser state, Qwen and the place provider are not contacted externally.
        """
        replies = [
            tool_message("discover_tools"),
            tool_message("read_skill", {"name": "navigation"}),
            tool_message("maps_status"),
            tool_message("places_categories"),
        ]
        conversation = Conversation("owned-tab", "token")
        with patch("core.agent.runtime.completion", AsyncMock(side_effect=replies)):
            with patch("core.ai_mcp.browser_call", AsyncMock(return_value={
                "available": True, "ready": True, "origin": [103.77, 1.3],
            })) as browser:
                with patch("core.agent.poi.categories", AsyncMock()) as categories:
                    with self.assertLogs("core.agent.runtime", level="WARNING") as logs:
                        await conversation.run("nearby supermarket", "request", None)

        browser.assert_awaited_once()
        categories.assert_not_awaited()
        self.assertIn(
            "invalid_tool_call", [event.get("code") for event in conversation.events],
        )
        self.assertTrue(any(
            "name=places_categories" in line
            and "reason=prerequisite_missing" in line
            and "missing_prerequisites=['maps_locate']" in line
            for line in logs.output
        ))

    def test_history_preserves_action_evidence(self):
        """Functionality: Refresh repeatable observations without losing actionable history.
        Inputs: Synthetic MCP groups with snapshots, map state, categories, candidates and outcomes.
        Outputs: compact model history, unchanged saved history and preserved tool-call pairing.
        Logic: Mark successful reloadable observations; preserve failures and action evidence.
        Exact saved results remain unchanged.
        Constraints: No real provider, browser state or place data is used.
        """
        messages, original_results = [], []
        for name, data in (
            ("discover_tools", {"tools": ["large catalog"]}),
            ("read_skill", {"name": "navigation", "instructions": "long workflow"}),
            ("ui_snapshot", {"snapshot": {"text": "old page"}}),
            ("maps_status", {"ready": True, "origin": [103.77, 1.3]}),
            ("places_categories", {"categories": [{"id": 17, "name": "shop", "group": "retail"}]}),
            ("places_search", {"candidates": [{"coordinates": [103.77, 1.3]}]}),
            ("maps_plan", {"accepted": True, "destination": [103.77, 1.3]}),
            ("ui_snapshot", {"available": False, "error_code": "page_unavailable"}),
            ("navigation_start", {"verified": True, "state": "navigating"}),
        ):
            call = tool_message(name)
            result_message = {
                "role": "tool",
                "tool_call_id": call["tool_calls"][0]["id"],
                "content": json.dumps(data),
            }
            messages.extend(
                [call, result_message]
            )
            original_results.append(result_message)
        original = json.dumps(messages)
        history = history_context([messages])
        compact_results = [item for item in history if item["role"] == "tool"]
        self.assertEqual(json.dumps(messages), original)
        for index in range(5):
            marker = json.loads(compact_results[index]["content"])
            self.assertTrue(marker["refresh_required_this_turn"])
        self.assertEqual(json.loads(compact_results[1]["content"])["skill"], "navigation")
        for index in (5, 6, 7, 8):
            self.assertEqual(compact_results[index], original_results[index])
        self.assertEqual(
            [item.get("tool_call_id") for item in history],
            [item.get("tool_call_id") for item in messages],
        )

    def test_context_limit_includes_tool_schemas(self):
        """Functionality: Enforce the final request budget and keep diagnostics content-free.
        Inputs: synthetic messages and an oversized visible tool schema. Outputs: context_limit.
        Logic: Count the serialized payload and inspect only safe size metadata in the warning.
        Constraints: No HTTP/provider call or original context threshold change is involved.
        """
        secret_text = "SENSITIVE_USER_SENTINEL"
        payload = {
            "model": "fixture",
            "messages": [{"role": "user", "content": secret_text}],
            "tools": [{"type": "function", "function": {
                "name": "synthetic", "description": "x" * 500,
                "parameters": {"type": "object", "properties": {}},
            }}],
        }
        with patch("core.agent.provider.MAX_CONTEXT_CHARS", 200):
            with self.assertLogs("core.agent.provider", level="WARNING") as captured:
                with self.assertRaises(AgentError) as raised:
                    enforce_context_limit(payload)
        self.assertEqual(raised.exception.code, "context_limit")
        self.assertIn("tool_schema_chars=", captured.output[0])
        self.assertNotIn(secret_text, captured.output[0])
        self.assertLess(
            len(json.dumps(payload["messages"], ensure_ascii=False, separators=(",", ":"))),
            200,
        )

    async def test_navigation_workflow_completes_with_exact_candidate_coordinates(self):
        """Functionality: Complete category discovery, nearby search, planning and navigation.
        Inputs: 333 synthetic categories, scripted model tool calls and mocked ORS/browser state.
        Outputs: verified navigating state and exact candidate coordinates in maps_plan.
        Logic: Execute the real intent validator, MCP registry, skill policy and runtime sequencing.
        Constraints: Qwen, ORS, GPS, route rendering and physical navigation are simulated.
        """
        category_id = 90043
        categories = [
            {"id": 90000 + index, "name": f"category-{index}", "group": f"group-{index % 7}"}
            for index in range(1, 334)
        ]
        categories[42] = {"id": category_id, "name": "supermarket", "group": "shopping"}
        destination = [103.8198, 1.3521]
        origin = [103.7764, 1.2966]
        candidate = {
            "name": "Synthetic Market", "label": "Synthetic Market",
            "coordinates": destination, "distance_m": 120.0,
            "category_ids": [category_id],
        }
        replies = [
            tool_message("discover_tools"),
            tool_message("read_skill", {"name": "navigation"}),
            tool_message("maps_status"),
            tool_message("maps_locate"),
            tool_message("places_categories"),
            tool_message("places_nearby", {"focus": origin, "category_ids": [category_id]}),
            tool_message("maps_plan", {"destination": destination}),
            tool_message("navigation_start"),
            {"role": "assistant", "content": "Navigation is active."},
        ]
        conversation = Conversation("navigation-fixture", "token")
        browser_calls = []

        async def browser_call(session_id, action, arguments=None, timeout=30):
            """Functionality: Supply deterministic browser state for each MCP browser operation.
            Inputs: session, action, arguments and timeout; Outputs: action-specific state fixture.
            Logic: Capture plan arguments and return observed route/navigation state.
            Constraints: No page, GPS receiver or route engine is contacted.
            """
            browser_calls.append((action, arguments))
            if action == "maps_status":
                return {"available": True, "ready": True, "origin": origin}
            if action == "maps_locate":
                return {"accepted": True, "origin": origin, "source": "device"}
            if action == "maps_plan":
                return {
                    "accepted": True, "state": "planned",
                    "destination": arguments["destination"],
                }
            if action == "agent_navigation_status":
                return {"available": True, "state": "navigating", "active": True}
            raise AssertionError(f"Unexpected browser action: {action}")

        with patch("core.agent.runtime.completion", AsyncMock(side_effect=replies)) as model:
            with patch("core.agent.poi.categories", AsyncMock(return_value={
                "categories": categories, "source": "synthetic", "coverage_verified": False,
            })):
                with patch("core.agent.poi.nearby", AsyncMock(return_value={
                    "candidates": [candidate], "coverage_complete": False,
                })) as nearby:
                    with patch("core.ai_mcp.browser_call", side_effect=browser_call):
                        with patch("core.ai_mcp.navigation_start", AsyncMock(return_value={
                            "accepted": True, "state": "locating",
                        })) as start:
                            await conversation.run(
                                "navigation-request", "Navigate to a nearby supermarket", None,
                            )

        nearby.assert_awaited_once_with(origin, [category_id], 2000)
        start.assert_awaited_once_with("navigation-fixture")
        self.assertEqual(
            [item[0] for item in browser_calls],
            ["maps_status", "maps_locate", "maps_plan", "agent_navigation_status"],
        )
        planned = next(arguments for action, arguments in browser_calls if action == "maps_plan")
        self.assertEqual(planned["destination"], candidate["coordinates"])
        tool_names = [
            event["name"] for event in conversation.events if event["type"] == "tool_start"
        ]
        self.assertEqual(tool_names, [
            "discover_tools", "read_skill", "maps_status", "maps_locate", "places_categories",
            "places_nearby", "maps_plan", "navigation_start",
        ])
        self.assertEqual(
            [event["text"] for event in conversation.events if event["type"] == "reply"],
            ["Navigation is active."],
        )
        self.assertFalse(any(event["type"] == "error" for event in conversation.events))

        final_request = model.await_args_list[8].args[0]
        start_result = next(
            json.loads(message["content"])
            for message in final_request if message.get("role") == "tool"
            and message.get("content", "").startswith('{"accepted"')
            and '"vibration_guidance"' in message["content"]
        )
        self.assertTrue(start_result["verified"])
        self.assertEqual(start_result["state"], "navigating")

        category_message = next(
            message for message in model.await_args_list[5].args[0]
            if message.get("role") == "tool" and "category_fields" in message.get("content", "")
        )
        compact_catalog = json.loads(category_message["content"])
        self.assertEqual(compact_catalog["category_fields"], ["id", "name", "group"])
        self.assertEqual(compact_catalog["categories"], [
            [item["id"], item["name"], item["group"]] for item in categories
        ])
        self.assertLess(
            len(json.dumps(compact_catalog, ensure_ascii=False, separators=(",", ":"))),
            len(json.dumps({"categories": categories}, ensure_ascii=False, separators=(",", ":"))),
        )

    async def test_selected_scope(self):
        """Functionality: Limit model schemas to discovered tools in the loaded skill.
        Inputs: scripted navigation and synthetic category catalog; Outputs: forced stages and
        dependency-filtered schema subsets.
        Logic: Inspect actual completion arguments after each protocol step.
        Constraints: Does not prove natural-language completion correctness.
        """
        replies = [
            tool_message("discover_tools"),
            tool_message("read_skill", {"name": "navigation"}),
            tool_message("maps_status"),
            tool_message("maps_locate"),
            tool_message("places_categories"),
            {"role": "assistant", "content": "Ready"},
        ]
        with patch("core.agent.runtime.completion", AsyncMock(side_effect=replies)) as model:
            with patch("core.ai_mcp.browser_call", AsyncMock(return_value={
                "ready": True, "accepted": True, "origin": [103.77, 1.3],
            })):
                with patch(
                    "core.agent.poi.categories",
                    AsyncMock(
                        return_value={
                            "categories": [{
                                "id": 90001, "name": "test_category", "group": "test_group",
                            }],
                        }
                    ),
                ):
                    await Conversation("owned-tab", "token").run("test", "map status", None)
        self.assertEqual(model.await_args_list[0].kwargs["required_tool"], "discover_tools")
        self.assertEqual(model.await_args_list[1].kwargs["required_tool"], "read_skill")
        names = {tool["function"]["name"] for tool in model.await_args_list[2].args[1]}
        self.assertNotIn("places_categories", names)
        self.assertNotIn("places_nearby", names)
        self.assertNotIn("places_search", names)
        located_names = {
            tool["function"]["name"] for tool in model.await_args_list[4].args[1]
        }
        self.assertIn("places_categories", located_names)
        self.assertNotIn("places_nearby", located_names)
        self.assertIn(
            "places_nearby", {tool["function"]["name"] for tool in model.await_args_list[5].args[1]}
        )
        self.assertNotIn("camera_start", names)
        self.assertNotIn("system_request", names)

    def test_scope_validation(self):
        """Functionality: Validate declarative scope and include it in reproducibility hashes.
        Inputs: temporary trusted skill/JSON; Outputs: valid names or explicit rejection.
        Logic: Check duplicate/invalid names and changes to the JSON fingerprint.
        Constraints: No executable code or arbitrary path is loaded from a scope.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "demo").mkdir()
            (root / "demo/SKILL.md").write_text("---\nname: demo\ndescription: Demo\n---\nWorkflow")
            scope = root / "demo/tools.json"
            scope.write_text(
                json.dumps(
                    {"tools": ["maps_status"], "requires": {}, "limits": {}, "result_counts": {}}
                )
            )
            with patch("core.agent.skills.ROOT", root):
                self.assertEqual(tool_policy("demo")["tools"], ["maps_status"])
                before = fingerprint()
                scope.write_text(
                    json.dumps(
                        {
                            "tools": ["maps_locate"],
                            "requires": {},
                            "limits": {},
                            "result_counts": {},
                        }
                    )
                )
                self.assertNotEqual(before, fingerprint())
                for value in ("[]", '["maps_status","maps_status"]', '["../secret"]', "[true]"):
                    scope.write_text(value)
                    with self.assertRaises(ValueError):
                        tool_policy("demo")
                for policy in (
                    {"tools": ["a", "a"], "requires": {}, "limits": {}, "result_counts": {}},
                    {"tools": ["../secret"], "requires": {}, "limits": {}, "result_counts": {}},
                    {
                        "tools": ["a", "b"],
                        "requires": {"a": ["b"], "b": ["a"]},
                        "limits": {},
                        "result_counts": {},
                    },
                    {"tools": ["a"], "requires": {}, "limits": {"a": 0}, "result_counts": {}},
                    {
                        "tools": ["a"],
                        "requires": {"a": ["missing"]},
                        "limits": {},
                        "result_counts": {},
                    },
                ):
                    scope.write_text(json.dumps(policy))
                    with self.assertRaises(ValueError):
                        tool_policy("demo")

    async def test_search_limit_blocks_requery(self):
        """Functionality: Enforce skill limits even when the model requests a translated requery.
        Inputs: scripted first/second names and empty service response; Outputs: one HTTP boundary
        call and explicit protocol error. Logic: Limit lives in skill JSON and removes the schema.
        Constraints: No implicit retry, query replacement or change to the configured provider.
        """
        replies = [
            tool_message("discover_tools"),
            tool_message("read_skill", {"name": "navigation"}),
            tool_message("maps_locate"),
            tool_message("places_search", {"text": "first query", "original_text": "first query"}),
            tool_message("places_search", {"text": "translated", "original_text": "first query"}),
        ]
        conversation = Conversation("owned-tab", "token")
        with patch("core.agent.runtime.completion", AsyncMock(side_effect=replies)) as model:
            with patch(
                "core.agent.tools.resolve_places",
                AsyncMock(
                    return_value={
                        "candidates": [{"label": "Synthetic unique result"}],
                    }
                ),
            ) as search, patch("core.ai_mcp.browser_call", AsyncMock(return_value={
                "accepted": True, "origin": [103.77, 1.3], "source": "device",
            })):
                await conversation.run("test", "find a destination", None)
        search.assert_awaited_once()
        self.assertNotIn(
            "places_search", {tool["function"]["name"] for tool in model.await_args_list[4].args[1]}
        )
        self.assertIn("invalid_tool_call", [event.get("code") for event in conversation.events])

    async def test_result_counts_end_actions(self):
        """Functionality: Stop actions on empty or ambiguous search evidence independently of LLM.
        Inputs: zero/two synthetic candidates, scripted attempted planning or clarification.
        Outputs: no planning side effect; empty results close tools;
        choices render deterministically.
        Logic: Execute real MCP/runtime with only the provider/browser/model mocked.
        Constraints: No candidate-count heuristic is claimed to resolve real place identity.
        """
        for count in (0, 2):
            for last in (
                tool_message("maps_plan", {"destination": [103.77, 1.3]}),
                {"role": "assistant", "content": "请提供具体地点。"},
            ):
                replies = [
                    tool_message("discover_tools"),
                    tool_message("read_skill", {"name": "navigation"}),
                    tool_message("maps_locate"),
                    tool_message("places_search", {
                        "text": "synthetic", "original_text": "synthetic",
                    }),
                    last,
                ]
                conversation = Conversation("owned-tab", "token")
                with patch(
                    "core.agent.runtime.completion", AsyncMock(side_effect=replies)
                ) as model:
                    with patch(
                        "core.agent.tools.resolve_places",
                        AsyncMock(
                            return_value={
                                "candidates": [
                                    {"label": f"Test {index}"} for index in range(count)
                                ],
                            }
                        ),
                    ):
                        with patch("core.ai_mcp.browser_call", AsyncMock(return_value={
                            "accepted": True, "origin": [103.77, 1.3], "source": "device",
                        })) as browser:
                            await conversation.run("test", "navigate", None)
                browser.assert_awaited_once()
                self.assertEqual(browser.await_args.args[1], "maps_locate")
                if count:
                    self.assertEqual(model.await_count, 4)
                    self.assertIn("1. Test 0", conversation.turns[-1][-1]["content"])
                else:
                    self.assertEqual(model.await_args_list[4].args[1], [])
                    self.assertIn('"count": 0', model.await_args_list[4].args[0][-1]["content"])

    async def test_live_category_contract(self):
        """Functionality: Obtain semantic categories from provider JSON, not local IDs.
        Inputs: mock HTTP response with deliberately nonstandard IDs; Outputs: flattened catalog.
        Logic: Real HTTP transport serialization and parser; secret stays in authorization header.
        Constraints: Synthetic service IDs are not claims about ORS categories.
        """
        requests = []
        client = httpx2.AsyncClient(
            transport=httpx2.MockTransport(
                lambda request: (
                    requests.append(request)
                    or httpx2.Response(
                        200,
                        json={
                            "test_group": {
                                "children": {"shop": {"supermarket": 90001, "pharmacy": 90002}}
                            }
                        },
                    )
                )
            )
        )
        with patch.dict("os.environ", {"ORS_API_KEY": "test-secret"}):
            with patch("core.agent.poi.httpx2.AsyncClient", return_value=client):
                result = await poi.categories()
        self.assertEqual([item["id"] for item in result["categories"]], [90001, 90002])
        self.assertEqual(json.loads(requests[0].content), {"request": "list"})
        self.assertNotIn("test-secret", str(requests[0].url))

    async def test_category_required(self):
        """Functionality: Reject guessed IDs until discovery succeeds in this server instance.
        Inputs: fake category service and search boundary; Outputs: one permitted query only.
        Logic: Verify membership before external work; Constraints: no cached catalog fallback.
        """
        adapter = BrowserTools("owned-tab")
        with patch("core.agent.poi.nearby", AsyncMock(return_value={"candidates": []})) as query:
            with self.assertRaises(AgentError):
                await adapter.places_nearby([103.77, 1.3], [90001])
            with patch(
                "core.agent.poi.categories",
                AsyncMock(
                    return_value={
                        "categories": [{"id": 90001, "name": "test_category"}],
                    }
                ),
            ):
                await adapter.places_categories()
            with self.assertRaises(AgentError):
                await adapter.places_nearby([103.77, 1.3], [90002])
            await adapter.places_nearby([103.77, 1.3], [90001])
        query.assert_awaited_once_with([103.77, 1.3], [90001], 2000)

    async def test_nearby_contract(self):
        """Functionality: Preserve returned POI identities and explicit empty results.
        Inputs: synthetic point/empty responses; Outputs: bounded circle request and candidates.
        Logic: Real HTTP serialization/parser; check radius, order, distance interpretation.
        Constraints: No real shop, operating hours, coverage or route-distance claims.
        """
        feature = {
            "geometry": {"type": "Point", "coordinates": [103.771, 1.3]},
            "properties": {
                "distance": 112,
                "category_ids": {"90001": {}},
                "osm_tags": {"name": "Synthetic Store"},
            },
        }
        for features in ([feature], []):
            requests = []
            client = httpx2.AsyncClient(
                transport=httpx2.MockTransport(
                    lambda request, requests=requests, features=features: (
                        requests.append(request)
                        or httpx2.Response(200, json={"features": features})
                    )
                )
            )
            with patch.dict("os.environ", {"ORS_API_KEY": "test-secret"}):
                with patch("core.agent.poi.httpx2.AsyncClient", return_value=client):
                    result = await poi.nearby([103.77, 1.3], [90001])
            self.assertEqual(len(result["candidates"]), len(features))
            self.assertFalse(result["coverage_complete"])
            self.assertEqual(result["search_context"]["distance_kind"], "straight_line")
            self.assertEqual(len(requests), 1)
            payload = json.loads(requests[0].content)
            self.assertEqual(payload["geometry"]["buffer"], 2000)
            self.assertEqual(payload["filters"]["category_ids"], [90001])

    async def test_invalid_poi_and_failure(self):
        """Functionality: Keep invalid inputs, malformed results and upstream errors explicit.
        Inputs: out-of-bounds radius, missing data and HTTP403; Outputs: typed failures.
        Logic: No request for invalid scope, one request on service failure, no retry.
        Constraints: Mock failures do not imply production service unavailability.
        """
        with patch("core.agent.poi.request_pois", AsyncMock(return_value={})) as request:
            with self.assertRaises(AgentError):
                await poi.nearby([103.77, 1.3], [90001], 2001)
            request.assert_not_awaited()
            with self.assertRaises(AgentError):
                await poi.nearby([103.77, 1.3], [90001])
        requests = []
        client = httpx2.AsyncClient(
            transport=httpx2.MockTransport(
                lambda request: requests.append(request) or httpx2.Response(403)
            )
        )
        with patch.dict("os.environ", {"ORS_API_KEY": "test-secret"}):
            with patch("core.agent.poi.httpx2.AsyncClient", return_value=client):
                with self.assertRaises(AgentError) as caught:
                    await poi.categories()
        self.assertEqual(caught.exception.code, "poi_http_error")
        self.assertEqual(caught.exception.http_status, 403)
        self.assertEqual(len(requests), 1)
