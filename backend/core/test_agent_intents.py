"""Responsibilities: Verify intent admission, mapped-skill enforcement and non-action routes.
Implementation: Real catalog/classifier/runtime/MCP with explicit Qwen/browser boundary doubles.
Related Modules: core.agent.intents/runtime; test_agent.tool_message.
Declaration Index: IntentTests; IntentTests.test_catalog_validation;
IntentTests.test_classification_contract; IntentTests.test_invalid_decisions;
IntentTests.test_non_action_routes; IntentTests.test_general_policy;
IntentTests.test_general_cannot_request_tools; IntentTests.test_router_failure_no_fallback;
IntentTests.test_unmapped_skill_blocked; IntentTests.test_multi_skill_loading;
IntentTests.test_dialogue_and_help; IntentTests.test_model_call_budget.
Variable Index: None; test inputs are synthetic and never validate external model accuracy.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase

from core.agent import intents
from core.agent.provider import AgentError
from core.agent.runtime import MAX_CALLS, Conversation
from core.test_agent import tool_message


class IntentTests(SimpleTestCase):
    """Functionality: Exercise admission boundaries before system actions.
    Logic: Inspect schema, tool ordering, errors and absence of MCP construction on rejection.
    Constraints: Qwen interpretation and real hardware require separate live evaluation.
    """

    def test_catalog_validation(self):
        """Functionality: Reject invalid trusted configuration instead of weakening policy.
        Inputs: repository catalog and malformed temporary variants; Outputs: explicit failures.
        Logic: Check missing skill, duplicates, extra fields and policy enum.
        Constraints: Temporary root patches skill discovery only, no source mutation.
        """
        config = intents.load_catalog()
        self.assertEqual(config["unknown_policy"], "reject")
        self.assertEqual(len(config["intents"]), 22)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            variants = [
                {**config, "unknown_policy": "automatic"},
                {**config, "max_intents": True},
                {**config, "extra": "ignored?"},
                {**config, "intents": [config["intents"][0]] * 2},
                {**config, "intents": [{**config["intents"][0], "skills": ["missing"]}]},
            ]
            with patch("core.agent.skills.ROOT", path), patch(
                "core.agent.skills.catalog", return_value={"navigation": "test"}
            ):
                for variant in variants:
                    with self.subTest(variant=variant.keys()):
                        (path / "intents.json").write_text(json.dumps(variant), encoding="utf-8")
                        with self.assertRaises(ValueError):
                            intents.load_catalog()

    async def test_classification_contract(self):
        """Functionality: Validate structured intent mapping and follow-up dialogue transfer.
        Inputs: scripted navigation match, prior clarification and tool-only evidence.
        Outputs: mapped skill, forced classifier schema and preserved conversation text.
        Logic: Tool data is excluded from router; workflow receives it separately in runtime.
        Constraints: Missing-slot semantic accuracy is measured by live fixtures, not this mock.
        """
        history = [[{"role": "user", "content": "开启导航"},
                    {"role": "assistant", "content": "你想去哪里？"},
                    {"role": "tool", "content": "untrusted tool prose"}]]
        reply = tool_message("route_intent", {
            "route": "system", "intent_ids": ["navigation.start"], "language": "zh",
        })
        with patch("core.agent.intents.completion", AsyncMock(return_value=reply)) as model:
            decision = await intents.classify("附近的超市", history)
        self.assertEqual(decision.skills, {"navigation"})
        self.assertIsNone(intents.static_reply(decision))
        self.assertEqual(model.await_args.kwargs["required_tool"], "route_intent")
        self.assertEqual(model.await_args.args[0][-2]["content"], "你想去哪里？")
        self.assertNotIn("untrusted tool prose", json.dumps(model.await_args.args[0]))
        enums = model.await_args.args[1][0]["function"]["parameters"]["properties"][
            "intent_ids"]["items"]["enum"]
        self.assertEqual(set(enums), {item["id"] for item in decision.catalog["intents"]})

    async def test_invalid_decisions(self):
        """Functionality: Prevent malformed or fabricated route results from admitting actions.
        Inputs: invalid classifier messages; Outputs: one explicit error per case, no retries.
        Logic: Cover invented ID, mixed route, duplicates, unknown fields and multiple results.
        Constraints: Provider boundary only; no live requests or browser effects.
        """
        base = {"route": "system", "intent_ids": ["camera.start"], "language": "en"}
        replies = [tool_message("route_intent", item) for item in (
            {**base, "intent_ids": ["system.delete"]}, {**base, "intent_ids": []},
            {**base, "intent_ids": ["camera.start"] * 2}, {**base, "route": "general"},
            {**base, "skills": ["camera"]}, {**base, "language": "invented"},
        )]
        replies += [{"role": "assistant", "content": "Done"}, tool_message("camera_start")]
        duplicate = tool_message("route_intent", base)
        duplicate["tool_calls"] *= 2
        replies.append(duplicate)
        for reply in replies:
            with self.subTest(reply=reply), patch(
                "core.agent.intents.completion", AsyncMock(return_value=reply)
            ) as model:
                with self.assertRaises(AgentError) as result:
                    await intents.classify("synthetic input", [])
                self.assertEqual(result.exception.code, "invalid_intent_decision")
                model.assert_awaited_once()

    async def test_non_action_routes(self):
        """Functionality: Rejection, ambiguity and help cannot construct any MCP server.
        Inputs: explicit route results; Outputs: replies/events and zero tool/controller activity.
        Logic: Run actual classifier and runtime under a guarded server factory.
        Constraints: Confirms enforcement for supplied decisions, not classifier accuracy.
        """
        for route, ids in (("unsupported", []), ("general", []),
                           ("clarify", ["camera.stop", "navigation.stop"]),
                           ("system", ["assistant.help"])):
            with self.subTest(route=route):
                reply = tool_message("route_intent", {
                    "route": route, "intent_ids": ids, "language": "zh",
                })
                conversation = Conversation("test-tab", "token")
                with patch("core.agent.intents.completion", AsyncMock(return_value=reply)), patch(
                    "core.agent.runtime.create_server"
                ) as server, patch("core.agent.runtime.completion", AsyncMock()) as actions:
                    await conversation.run("test", "synthetic request", None)
                server.assert_not_called()
                actions.assert_not_awaited()
                self.assertTrue(any(event["type"] == "intent" for event in conversation.events))
                self.assertTrue(any(event["type"] == "reply" for event in conversation.events))
                self.assertFalse(any(
                    event["type"] == "tool_start" for event in conversation.events
                ))

    async def test_general_policy(self):
        """Functionality: Explicit general mode answers questions but never unsupported actions.
        Inputs: overridden trusted policy and synthetic Qwen outputs; Outputs: isolated answer.
        Logic: No MCP exists; general call has no tool schemas and no execution tool results.
        Constraints: Policy override is test-local; production remains reject.
        """
        config = {**intents.load_catalog(), "unknown_policy": "general"}
        for route in ("general", "unsupported"):
            with self.subTest(route=route):
                replies = [tool_message("route_intent", {
                    "route": route, "intent_ids": [], "language": "en",
                }), {"role": "assistant", "content": "A triangle has three sides."}]
                with patch("core.agent.intents.load_catalog", return_value=config), patch(
                    "core.agent.intents.completion", AsyncMock(side_effect=replies)
                ) as model, patch("core.agent.runtime.create_server") as server:
                    await Conversation("tab", "token").run("test", "synthetic request", None)
                server.assert_not_called()
                self.assertEqual(model.await_count, 2 if route == "general" else 1)
                if route == "general":
                    self.assertEqual(len(model.await_args.args), 1)

    async def test_general_cannot_request_tools(self):
        """Functionality: Reject an unsolicited general-mode action without executing it.
        Inputs: model tool call despite absent schemas; Outputs: explicit answer error.
        Logic: Validate response before returning; Constraints: no corrective retry.
        """
        with patch("core.agent.intents.completion", AsyncMock(
            return_value=tool_message("camera_start")
        )):
            with self.assertRaises(AgentError) as result:
                await intents.general_answer("question", [])
        self.assertEqual(result.exception.code, "invalid_general_answer")

    async def test_router_failure_no_fallback(self):
        """Functionality: Preserve provider failures without converting them to general chat.
        Inputs: classifier timeout error; Outputs: terminal error and no tools/general call.
        Logic: One attempt through real runtime; Constraints: no user action was dispatched.
        """
        conversation = Conversation("tab", "token")
        with patch("core.agent.intents.completion", AsyncMock(side_effect=AgentError(
            "provider_test_failure", "Test failure"
        ))) as model, patch("core.agent.runtime.create_server") as server:
            await conversation.run("test", "request", None)
        model.assert_awaited_once()
        server.assert_not_called()
        self.assertIn("provider_test_failure", [item.get("code") for item in conversation.events])

    async def test_unmapped_skill_blocked(self):
        """Functionality: Block a valid installed skill when absent from this turn's mapping.
        Inputs: navigation classification then camera read request; Outputs: no camera workflow.
        Logic: Enum hints and runtime validation independently reject scope expansion.
        Constraints: Real discovery is read-only; no browser boundary should be reached.
        """
        route = tool_message("route_intent", {
            "route": "system", "intent_ids": ["navigation.start"], "language": "zh",
        })
        conversation = Conversation("tab", "token")
        with patch("core.agent.intents.completion", AsyncMock(return_value=route)), patch(
            "core.agent.runtime.completion", AsyncMock(side_effect=[
                tool_message("discover_tools"), tool_message("read_skill", {"name": "camera"}),
            ])
        ) as model, patch("core.ai_mcp.browser_call", AsyncMock()) as browser:
            await conversation.run("test", "开启导航", None)
        browser.assert_not_awaited()
        self.assertIn("invalid_tool_call", [item.get("code") for item in conversation.events])
        schema = model.await_args.args[1][0]["function"]["parameters"]
        self.assertEqual(schema["properties"]["name"]["enum"], ["navigation"])

    async def test_multi_skill_loading(self):
        """Functionality: Load all mapped domains before admitting their operations.
        Inputs: navigation/camera stop decision and real MCP sequence; Outputs: forced loading.
        Logic: Inspect scopes before and after both skills; Constraints: no real device operation.
        """
        route = tool_message("route_intent", {
            "route": "system", "intent_ids": ["navigation.stop", "camera.stop"], "language": "en",
        })
        replies = [tool_message("discover_tools"),
                   tool_message("read_skill", {"name": "navigation"}),
                   tool_message("read_skill", {"name": "camera"}),
                   {"role": "assistant", "content": "Synthetic final response"}]
        with patch("core.agent.intents.completion", AsyncMock(return_value=route)), patch(
            "core.agent.runtime.completion", AsyncMock(side_effect=replies)
        ) as model:
            await Conversation("tab", "token").run("test", "synthetic combined request", None)
        third = model.await_args_list[2]
        self.assertEqual(third.kwargs["required_tool"], "read_skill")
        self.assertEqual([s["function"]["name"] for s in third.args[1]], ["read_skill"])
        last = {s["function"]["name"] for s in model.await_args.args[1]}
        self.assertTrue({"camera_stop", "navigation_stop"}.issubset(last))
        self.assertNotIn("read_skill", last)
        self.assertNotIn("speech_speak", last)

    def test_dialogue_and_help(self):
        """Functionality: Bound dialogue and derive help directly from catalog definitions.
        Inputs: oversize prior dialogue, help decision and configurable count limit.
        Outputs: context error and accurate labels/limit; Logic: no separate stale feature list.
        Constraints: Help does not promise runtime readiness or grant tool access.
        """
        with self.assertRaises(AgentError):
            intents.dialogue([[{"role": "user", "content": "x" * 120001}]], "next")
        config = intents.load_catalog()
        reply = intents.static_reply(intents.IntentDecision(
            "system", ("assistant.help",), "en", config,
        ))
        for entry in config["intents"]:
            if entry["skills"]:
                self.assertIn(entry["labels"]["en"], reply)
        config["max_intents"] = 1
        self.assertIn("最多1项", intents.static_reply(intents.IntentDecision(
            "clarify", (), "zh", config,
        )))

    async def test_model_call_budget(self):
        """Functionality: Include classification in the existing total model-call budget.
        Inputs: endless read-only model loop after skill load; Outputs: bounded terminal error.
        Logic: Actual MCP discovery followed by repeated snapshots; Constraints: fake browser only.
        """
        route = tool_message("route_intent", {
            "route": "system", "intent_ids": ["navigation.status"], "language": "en",
        })
        replies = [tool_message("discover_tools"),
                   tool_message("read_skill", {"name": "navigation"})] + [
                       tool_message("maps_status") for _ in range(MAX_CALLS)]
        conversation = Conversation("tab", "token")
        with patch("core.agent.intents.completion", AsyncMock(return_value=route)) as router, patch(
            "core.agent.runtime.completion", AsyncMock(side_effect=replies)
        ) as model, patch("core.ai_mcp.browser_call", AsyncMock(return_value={"ready": True})):
            await conversation.run("test", "status", None)
        self.assertEqual(router.await_count + model.await_count, MAX_CALLS)
        self.assertIn("model_step_limit", [item.get("code") for item in conversation.events])
