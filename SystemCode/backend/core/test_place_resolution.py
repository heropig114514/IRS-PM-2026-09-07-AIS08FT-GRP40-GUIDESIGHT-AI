"""Responsibilities: Verify pronunciation-hypothesis lookup and mandatory candidate confirmation.
Implementation: Synthetic geocoder responses; real resolver/runtime/MCP policy boundaries.
Related Modules: agent.place_resolution, tools, runtime, navigation/tools.json.
Declaration Index: PlaceResolutionTests; PlaceResolutionTests.test_exact_path;
PlaceResolutionTests.test_variants_and_provenance; PlaceResolutionTests.test_invalid_queries;
PlaceResolutionTests.test_provider_error_no_retry;
PlaceResolutionTests.test_confirmation_blocks_plan;
PlaceResolutionTests.test_empty_hypotheses_remain_empty.
PlaceResolutionTests.test_rewritten_name_requires_confirmation;
PlaceResolutionTests.test_selection_preserves_entity_identity.
PlaceResolutionTests.test_selection_grammar; PlaceResolutionTests.test_persistent_selection_guard.
PlaceResolutionTests.test_duplicate_hypotheses_are_explicitly_normalized.
Variable Index: None; all names, positions and query results are explicit test fixtures.
"""

from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase

from core.agent.place_resolution import resolve_places
from core.agent.provider import AgentError
from core.agent.runtime import Conversation, selection_reply
from core.agent.selection import selected_index
from core.agent.skills import tool_policy
from core.test_agent import tool_message


class PlaceResolutionTests(SimpleTestCase):
    """Functionality: Verify bounded lookup and safe use of uncertain linguistic candidates.
    Logic: Assert exact query provenance, unchanged scope, real-result-only merging and pauses.
    Constraints: Mocks do not establish actual sound similarity, map coverage or user intent.
    """

    async def test_exact_path(self):
        """Functionality: Preserve the existing one-query path without phonetic hypotheses.
        Inputs: explicit name and synthetic response; Outputs: identical result, one service call.
        Logic: No added confirmation or implicit rewrite; Constraints: service boundary is mocked.
        """
        result = {"candidates": [{"label": "Example"}]}
        with patch("core.agent.place_resolution.search_places", AsyncMock(
            return_value=result
        )) as search:
            self.assertIs(await resolve_places("Example", "Example"), result)
        search.assert_awaited_once_with("Example", focus=None, country=None)

    async def test_variants_and_provenance(self):
        """Functionality: Merge only provider places and retain each queried spelling.
        Inputs: original near-sound text, two hypotheses and duplicate synthetic place results.
        Outputs: one real candidate, query provenance, explicit user-confirmation marker.
        Logic: Compare call scope/ordering and coordinate-name deduplication.
        Constraints: Phonetic guesses are not verified aliases or numerical confidence scores.
        """
        candidate = {"name": "Clementi Mall", "label": "Clementi Mall, Singapore",
                     "coordinates": [103.7647, 1.3147], "country_code": "SGP"}
        replies = [{"candidates": [], "search_context": {"query": "克莱门提摩尔"}},
                   {"candidates": [candidate]}, {"candidates": [candidate]}]
        with patch("core.agent.place_resolution.search_places", AsyncMock(
            side_effect=replies
        )) as search:
            result = await resolve_places("克莱门提摩尔", "克莱门提摩尔", [103.7766, 1.2971], "SGP",
                                          ["Clementi Mall", "Clementi"])
        self.assertEqual(search.await_count, 3)
        self.assertEqual([call.args[0] for call in search.await_args_list],
                         ["克莱门提摩尔", "Clementi Mall", "Clementi"])
        for call in search.await_args_list:
            self.assertEqual(call.kwargs, {"focus": [103.7766, 1.2971], "country": "SGP"})
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["candidates"][0]["matched_queries"], ["Clementi Mall", "Clementi"])
        self.assertEqual(result["original_query"], "克莱门提摩尔")
        self.assertTrue(result["requires_confirmation"])
        self.assertFalse(result["hypotheses_verified_as_aliases"])

    async def test_invalid_queries(self):
        """Functionality: Reject invalid hypotheses before any external request.
        Inputs: too many, blank or malformed variants; Outputs: explicit errors.
        Logic: Validate entire batch before primary query; Constraints: no silent truncation.
        """
        with patch("core.agent.place_resolution.search_places", AsyncMock()) as search:
            for variants in (["a", "b", "c"], [""], "abc", [42]):
                with self.subTest(variants=variants), self.assertRaises(AgentError):
                    await resolve_places("Example", "Example", pronunciation_queries=variants)
        search.assert_not_awaited()

    async def test_duplicate_hypotheses_are_explicitly_normalized(self):
        """Functionality: Repeated model spellings do not create duplicate provider requests.
        Inputs: duplicate case-insensitive hypotheses or a duplicate of the primary query.
        Outputs: one call per distinct query and an explicit omitted count.
        Logic: Normalize without inventing/replacing any search term.
        Constraints: errors still stop.
        """
        for variants, count in ((["mall", "MALL"], 2), (["heard"], 1)):
            with self.subTest(variants=variants), patch(
                "core.agent.place_resolution.search_places",
                AsyncMock(return_value={"candidates": []}),
            ) as search:
                result = await resolve_places("heard", "heard", pronunciation_queries=variants)
            self.assertEqual(search.await_count, count)
            self.assertEqual(result["duplicate_queries_omitted"], 1)
            self.assertTrue(result["requires_confirmation"])

    async def test_provider_error_no_retry(self):
        """Functionality: Preserve provider errors without treating them as misspellings.
        Inputs: error on primary or first hypothesis; Outputs: same error and no later request.
        Logic: No partial-result success or retry; Constraints: synthetic external failure.
        """
        for prefix in ([], [{"candidates": []}]):
            with self.subTest(prefix=len(prefix)), patch(
                "core.agent.place_resolution.search_places", AsyncMock(side_effect=[
                    *prefix, AgentError("geocoder_http_error", "HTTP test failure"),
                ])
            ) as search:
                with self.assertRaises(AgentError):
                    await resolve_places(
                        "heard", "heard", pronunciation_queries=["variant", "another"],
                    )
                self.assertEqual(search.await_count, len(prefix) + 1)

    async def test_confirmation_blocks_plan(self):
        """Functionality: Resolve a likely ASR substitution from conversation context safely.
        Inputs: Prior Sheng Siong mention, current Samsung transcript, and one provider candidate.
        Outputs: Workflow receives context; sound-alike queries still require confirmation.
        Logic: Verify the query reaches the resolver and runtime blocks route planning.
        Constraints: Scripted output verifies the contract, not live recognition accuracy.
        """
        route = tool_message("route_intent", {
            "route": "system", "intent_ids": ["navigation.start"], "language": "en",
        })
        replies = [tool_message("discover_tools"),
                   tool_message("read_skill", {"name": "navigation"}),
                   tool_message("maps_locate"),
                   tool_message("places_search", {
                       "text": "Samsung supermarket",
                       "original_text": "Samsung supermarket",
                       "pronunciation_queries": ["Sheng Siong supermarket"],
                   }),
                   tool_message("maps_plan", {"destination": [103.77, 1.3]})]
        conversation = Conversation("tab", "token")
        conversation.turns.append([
            {"role": "user", "content": "I want to go to Sheng Siong supermarket."},
            {"role": "assistant", "content": "You want to go to Sheng Siong supermarket."},
        ])
        with patch("core.agent.intents.completion", AsyncMock(return_value=route)), patch(
            "core.agent.runtime.completion", AsyncMock(side_effect=replies)
        ) as model, patch("core.agent.tools.resolve_places", AsyncMock(return_value={
            "candidates": [{"label": "Verified test place", "coordinates": [103.77, 1.3]}],
            "requires_confirmation": True,
        })) as resolver, patch("core.ai_mcp.browser_call", AsyncMock(return_value={
            "accepted": True, "origin": [103.77, 1.3], "source": "device",
        })) as browser:
            await conversation.run("test", "Navigate to Samsung supermarket", None)
        browser.assert_awaited_once()
        self.assertEqual(browser.await_args.args[1], "maps_locate")
        self.assertEqual(model.await_count, 4)
        resolver.assert_awaited_once_with(
            "Samsung supermarket", "Samsung supermarket", None, None, ["Sheng Siong supermarket"],
        )
        workflow_messages = model.await_args_list[3].args[0]
        workflow_context = " ".join(item["content"] for item in workflow_messages)
        self.assertIn("Sheng Siong supermarket", workflow_context)
        self.assertIn("Samsung", workflow_context)
        self.assertIn(
            "include that exact contextual business name as the first pronunciation query",
            workflow_context,
        )
        self.assertIn("1. Verified test place", conversation.turns[-1][-1]["content"])

    async def test_empty_hypotheses_remain_empty(self):
        """Functionality: No real results means no invented destination or broadened search.
        Inputs: two empty service responses; Outputs: empty candidates and exact query counts.
        Logic: Preserve provenance, do not create coordinates from query guesses.
        Constraints: Empty coverage says nothing about whether the real-world place exists.
        """
        with patch("core.agent.place_resolution.search_places", AsyncMock(
            return_value={"candidates": []}
        )) as search:
            result = await resolve_places("unknown", "unknown", pronunciation_queries=["possible"])
        self.assertEqual(result["candidates"], [])
        self.assertEqual(search.await_count, 2)

    async def test_rewritten_name_requires_confirmation(self):
        """Functionality: Do not let implicit phonetic correction masquerade as certain translation.
        Inputs: different original/search names and one synthetic provider candidate.
        Outputs: explicit confirmation even without additional pronunciation queries.
        Logic: Literal provenance difference triggers the marker; Constraints: no extra HTTP query.
        """
        with patch("core.agent.place_resolution.search_places", AsyncMock(return_value={
            "candidates": [{"label": "Bridge connecting to the mall"}],
        })) as search:
            result = await resolve_places("Clementi Mall", "克莱门提摩尔")
        search.assert_awaited_once()
        self.assertTrue(result["requires_confirmation"])
        self.assertEqual(result["original_query"], "克莱门提摩尔")

    def test_selection_preserves_entity_identity(self):
        """Functionality: Never shorten an adjacent bridge into the requested mall identity.
        Inputs: provider label and invalid label variants; Outputs: verbatim display or error.
        Logic: Use trusted selection template and actual data; Constraints: no model paraphrasing.
        """
        policy = tool_policy("navigation")["result_counts"]["places_search"]["selection"]
        label = "Bridge inside Clement MRT Station that connects to Clementi Mall, Singapore"
        reply = selection_reply([{"label": label}], policy, "zh")
        self.assertIn("1. " + label, reply)
        for invalid in ({"label": ""}, {"name": "invented"}, "text"):
            with self.assertRaises(AgentError):
                selection_reply([invalid], policy, "en")

    def test_selection_grammar(self):
        """Functionality: Accept explicit option slots, never infer a related entity alias.
        Inputs: displayed bridge/store labels, Chinese/English selections and ambiguous phrases.
        Outputs: unique index or None; Logic: whole-utterance grammar and exact name matching.
        Constraints: Deliberately requires number/full name; free paraphrases remain unconfirmed.
        """
        items = [{"label": "Bridge to Example Mall, Singapore", "name": "Bridge to Example Mall"},
                 {"label": "Store, Singapore", "name": "Store"}]
        for text in ("第一个", "选择第一个", "1", "first", "select the first one",
                     "Bridge to Example Mall"):
            self.assertEqual(selected_index(text, items), 0)
        self.assertEqual(selected_index("第二个", items), 1)
        for text in ("Example Mall", "是商场", "去附近的商场", "第三个", "1号超市"):
            self.assertIsNone(selected_index(text, items))

    async def test_persistent_selection_guard(self):
        """Functionality: Enforce confirmation and exact chosen coordinates across turns.
        Inputs: pending provider candidate, scripted plan calls and real MCP transport.
        Outputs: unmatched name/wrong coordinates never dispatch; correct numbered choice can plan.
        Logic: Actual scope removal, argument binding and release only on accepted planning.
        Constraints: Synthetic browser acceptance is not actual navigation/hardware verification.
        """
        policy = tool_policy("navigation")["result_counts"]["places_search"]["selection"]
        route = tool_message("route_intent", {
            "route": "system", "intent_ids": ["navigation.start"], "language": "zh",
        })
        for text, coordinates, allowed in (("Example Mall", [103.77, 1.3], False),
                                           ("第一个", [103.78, 1.3], False),
                                           ("第一个", [103.77, 1.3], True)):
            with self.subTest(text=text, coordinates=coordinates):
                conversation = Conversation("tab", "token", selection={
                    "items": [{"label": "Bridge to Example Mall", "name": "Bridge to Example Mall",
                               "coordinates": [103.77, 1.3]}], "policy": policy, "index": None,
                })
                replies = [tool_message("discover_tools"),
                           tool_message("read_skill", {"name": "navigation"}),
                           tool_message("maps_plan", {"destination": coordinates}),
                           {"role": "assistant", "content": "Test final reply"}]
                with patch("core.agent.intents.completion", AsyncMock(return_value=route)), patch(
                    "core.agent.runtime.completion", AsyncMock(side_effect=replies)
                ) as model, patch("core.ai_mcp.browser_call", AsyncMock(
                    return_value={"accepted": True}
                )) as browser:
                    await conversation.run("test", text, None)
                if allowed:
                    browser.assert_awaited_once()
                    self.assertIsNone(conversation.selection)
                    self.assertIn("navigation_start", {
                        s["function"]["name"] for s in model.await_args.args[1]
                    })
                else:
                    browser.assert_not_awaited()
                    self.assertTrue(any(e["type"] == "error" for e in conversation.events))
                conversation.clear()
                self.assertIsNone(conversation.selection)
