"""Responsibilities: Verify registry-driven scope enforcement and provider-driven POI discovery.
Implementation: Real MCP registry/runtime; mock Qwen, browser and HTTP boundaries only.
Related Modules: agent.skills/runtime/tools/poi; test_agent supplies model-message fixtures.
Declaration Index: DiscoveryTests; DiscoveryTests.setUp; DiscoveryTests.test_registry_is_source;
DiscoveryTests.test_scope_before_side_effects; DiscoveryTests.test_selected_scope;
DiscoveryTests.test_scope_validation; DiscoveryTests.test_live_category_contract;
DiscoveryTests.test_category_required; DiscoveryTests.test_nearby_contract;
DiscoveryTests.test_invalid_poi_and_failure; DiscoveryTests.test_registry_is_source.probe.
DiscoveryTests.test_history_preserves_action_evidence.
DiscoveryTests.test_search_limit_blocks_requery.
DiscoveryTests.test_result_counts_end_actions.
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
from core.agent.provider import AgentError
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

    def test_history_preserves_action_evidence(self):
        """Functionality: Remove duplicate reloadable metadata without losing real evidence.
        Inputs: complete synthetic MCP groups including a destination and mutation result.
        Outputs: compact model history, unchanged saved history and tool-call pairing.
        Logic: Only catalog and skill body results become explicit refresh markers.
        Constraints: Candidate coordinates and action outcomes must remain byte-identical.
        """
        messages = []
        for name, data in (
            ("discover_tools", {"tools": ["large catalog"]}),
            ("read_skill", {"name": "navigation", "instructions": "long workflow"}),
            ("places_search", {"candidates": [{"coordinates": [103.77, 1.3]}]}),
            ("navigation_start", {"verified": True, "state": "navigating"}),
        ):
            call = tool_message(name)
            messages.extend(
                [
                    call,
                    {
                        "role": "tool",
                        "tool_call_id": call["tool_calls"][0]["id"],
                        "content": json.dumps(data),
                    },
                ]
            )
        original = json.dumps(messages)
        history = history_context([messages])
        self.assertEqual(json.dumps(messages), original)
        self.assertEqual(history[4:], messages[4:])
        self.assertTrue(json.loads(history[1]["content"])["refresh_required_this_turn"])
        self.assertEqual(json.loads(history[3]["content"])["skill"], "navigation")
        self.assertEqual(
            [item.get("tool_call_id") for item in history],
            [item.get("tool_call_id") for item in messages],
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
            tool_message("places_categories"),
            tool_message("maps_status"),
            {"role": "assistant", "content": "Ready"},
        ]
        with patch("core.agent.runtime.completion", AsyncMock(side_effect=replies)) as model:
            with patch("core.ai_mcp.browser_call", AsyncMock(return_value={"ready": True})):
                with patch(
                    "core.agent.poi.categories",
                    AsyncMock(
                        return_value={
                            "categories": [{"id": 90001, "name": "test_category"}],
                        }
                    ),
                ):
                    await Conversation("owned-tab", "token").run("test", "map status", None)
        self.assertEqual(model.await_args_list[0].kwargs["required_tool"], "discover_tools")
        self.assertEqual(model.await_args_list[1].kwargs["required_tool"], "read_skill")
        names = {tool["function"]["name"] for tool in model.await_args_list[2].args[1]}
        self.assertNotIn("places_nearby", names)
        self.assertIn(
            "places_nearby", {tool["function"]["name"] for tool in model.await_args_list[3].args[1]}
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
            ) as search:
                await conversation.run("test", "find a destination", None)
        search.assert_awaited_once()
        self.assertNotIn(
            "places_search", {tool["function"]["name"] for tool in model.await_args_list[3].args[1]}
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
                        with patch("core.ai_mcp.browser_call", AsyncMock()) as browser:
                            await conversation.run("test", "navigate", None)
                browser.assert_not_awaited()
                if count:
                    self.assertEqual(model.await_count, 3)
                    self.assertIn("1. Test 0", conversation.turns[-1][-1]["content"])
                else:
                    self.assertEqual(model.await_args_list[3].args[1], [])
                    self.assertIn('"count": 0', model.await_args_list[3].args[0][-1]["content"])

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
                with self.assertRaisesRegex(AgentError, "403"):
                    await poi.categories()
        self.assertEqual(len(requests), 1)
