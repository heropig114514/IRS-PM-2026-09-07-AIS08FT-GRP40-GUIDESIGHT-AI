"""Responsibilities: Verify agent boundaries, MCP-driven execution and failure semantics.
Implementation: Real in-process MCP; mock only Qwen/browser/network boundaries explicitly.
Related Modules: core.agent.*, frontend/tools/check_agent_browser.mjs.
Declaration Index: AgentTests; AgentTests.setUp; AgentTests.test_skill_catalog_and_paths;
AgentTests.test_mcp_schema_binding; AgentTests.test_runtime_uses_evidence_and_memory;
AgentTests.test_tool_failure_stops_without_retry;
AgentTests.test_failure_fallback_after_model_error;
AgentTests.test_route_error_is_explained;
AgentTests.test_english_failure_explanation_ignores_chinese_history;
AgentTests.test_tool_error_normalization; AgentTests.test_localized_failure_fallback;
AgentTests.test_empty_failure_explanation_uses_fallback;
AgentTests.test_declined_action_blocks_batched_mutations; AgentTests.test_cancel_and_deduplicate;
AgentTests.test_input_validation; AgentTests.test_audio_validation;
AgentTests.test_provider_contract; AgentTests.test_provider_contract.handler;
AgentTests.test_geocoder_contract; AgentTests.test_start_observes_actual_state;
AgentTests.test_skill_reference_context; AgentTests.test_geocoder_country_override;
AgentTests.test_geocoder_contract.handler; AgentTests.test_geocoder_rejects_unknown_country;
AgentTests.test_cancel_before_start; AgentTests.test_socket_binding_and_heartbeat;
AgentTests.test_socket_rejects_wrong_owner;
tool_message; wav_input.
Variable Index: None; test fixtures are call-local and never access physical devices/services.
"""

import asyncio
import base64
import io
import json
import tempfile
import uuid
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx2
from asgiref.testing import ApplicationCommunicator
from django.test import SimpleTestCase
from mcp.client import Client

from core.agent.places import search_context, search_places
from core.agent.provider import AgentError, completion, transcribe
from core.agent.runtime import (
    Conversation,
    conversations,
    failure_reply,
    tool_exception,
    tool_failure,
)
from core.agent.skills import catalog, fingerprint, instructions, read_skill
from core.agent.tools import BrowserTools, create_server
from core.agent.transport import agent_socket, handle_message


def tool_message(name, arguments=None):
    """Functionality: Build a synthetic model tool request. Inputs: name/arguments.
    Outputs: assistant message; Logic: real API shape; Constraints: no model quality claim.
    """
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": uuid.uuid4().hex,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments or {})},
            }
        ],
    }


def wav_input(rate=16000, duration=0.2):
    """Functionality: Produce explicit silent test WAV. Inputs: rate/duration.
    Outputs: base64 PCM; Logic: standard WAV writer; Constraints: not human speech/ASR evidence.
    """
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        audio.writeframes(b"\0\0" * int(rate * duration))
    return base64.b64encode(output.getvalue()).decode()


class AgentTests(SimpleTestCase):
    """Functionality: Check application contracts without external services or database writes.
    Logic: Inject only named boundary doubles and inspect actual MCP/tool/runtime behavior.
    Constraints: These tests do not establish Qwen accuracy, GPS or microphone quality.
    """

    def setUp(self):
        """Functionality: Isolate the classifier's provider boundary for execution tests.
        Inputs: fixed navigation decision; Outputs: scoped Qwen double restored after each test.
        Logic: Run actual catalog validation; individual domain tests override the model result.
        Constraints: No semantic-accuracy claim; intent-specific tests cover classification errors.
        """
        self.router = AsyncMock(return_value=tool_message("route_intent", {
            "route": "system", "intent_ids": ["navigation.start"], "language": "zh",
        }))
        self.enterContext(patch("core.agent.intents.completion", self.router))

    def test_skill_catalog_and_paths(self):
        """Functionality: Verify discoverability/path isolation. Inputs: repository skills.
        Outputs: assertions; Logic: valid metadata, traversal refusal and stable digest.
        Constraints: read-only filesystem test, not subjective workflow quality assessment.
        """
        self.assertEqual(set(catalog()), {"navigation", "camera", "outputs"})
        self.assertIn("maps_locate", read_skill("navigation"))
        self.assertEqual(len(fingerprint()), 64)
        with self.assertRaises(ValueError):
            read_skill("../../.env")

    async def test_mcp_schema_binding(self):
        """Functionality: Verify real MCP discovery and tab binding. Inputs: fake browser call.
        Outputs: schemas/results; Logic: explicit whitelist and absent session parameters.
        Constraints: real MCP transport with synthetic browser response, no physical action.
        """
        with patch(
            "core.ai_mcp.browser_call", AsyncMock(return_value={"state": "ready"})
        ) as browser:
            async with Client(create_server("owned-tab")) as client:
                tools = (await client.list_tools()).tools
                self.assertEqual(len(tools), 24)
                self.assertFalse(any("database" in tool.name for tool in tools))
                for tool in tools:
                    self.assertNotIn("session_id", tool.input_schema.get("properties", {}))
                result = await client.call_tool("maps_status", {})
                self.assertFalse(result.is_error)
                self.assertEqual(result.structured_content, {"state": "ready"})
                invalid = await client.call_tool("camera_start", {"processing": "other"})
                self.assertTrue(invalid.is_error)
                invalid = await client.call_tool("places_search", {
                    "text": "NUS", "original_text": "NUS", "country": "SG",
                })
                self.assertTrue(invalid.is_error)
        browser.assert_awaited_once_with("owned-tab", "maps_status", None, 30)

    def test_skill_reference_context(self):
        """Functionality: Verify on-demand knowledge, configuration provenance and size bounds.
        Inputs: temporary trusted skill/reference files and explicit region configuration.
        Outputs: discovery excludes reference body, loading includes it, fingerprint tracks it.
        Logic: Exercise actual loader and navigation tool response with no model or network.
        Constraints: Does not establish real user location or correctness of external map data.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "navigation/references").mkdir(parents=True)
            (root / "system.md").write_text("System", encoding="utf-8")
            (root / "navigation/SKILL.md").write_text(
                "---\nname: navigation\ndescription: Workflow\n---\nInstructions",
                encoding="utf-8",
            )
            reference = root / "navigation/references/places.md"
            reference.write_text("Unique reference content", encoding="utf-8")
            with patch("core.agent.skills.ROOT", root):
                self.assertNotIn("Unique reference content", instructions())
                self.assertIn("Unique reference content", read_skill("navigation"))
                before = fingerprint()
                reference.write_text("Updated reference", encoding="utf-8")
                self.assertNotEqual(before, fingerprint())
                reference.write_text("x" * 16001, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "too large"):
                    read_skill("navigation")
        with patch.dict("os.environ", {"PRS_AGENT_SEARCH_COUNTRY": "SGP"}):
            result = BrowserTools("owned-tab").read_skill("navigation")
            self.assertEqual(result["environment_context"]["default_search_country"], "SGP")
            self.assertFalse(result["environment_context"]["is_device_location"])
            self.assertIn("places_categories", result["tools"])
        with patch.dict("os.environ", {"PRS_AGENT_SEARCH_COUNTRY": ""}):
            with self.assertRaises(AgentError):
                search_context()

    async def test_runtime_uses_evidence_and_memory(self):
        """Functionality: Verify skill→observation→reply and multi-turn retention.
        Inputs: scripted model replies and browser state; Outputs: actual ordered events/history.
        Logic: Real MCP executes selected calls; model receives tool result before final reply.
        Constraints: synthetic language model, not an accuracy benchmark.
        """
        replies = [
            tool_message("discover_tools"),
            tool_message("read_skill", {"name": "navigation"}),
            tool_message("maps_status"),
            {"role": "assistant", "content": "你想去哪里？"},
            tool_message("discover_tools"),
            tool_message("read_skill", {"name": "navigation"}),
            {"role": "assistant", "content": "收到补充终点。"},
        ]
        conversation = Conversation("owned-tab", "token")
        with patch("core.agent.runtime.completion", AsyncMock(side_effect=replies)) as model:
            with patch("core.ai_mcp.browser_call", AsyncMock(return_value={"destination": None})):
                await conversation.run("request-1", "开启导航", None)
                await conversation.run("request-2", "图书馆", None)
        self.assertEqual(len(conversation.turns), 2)
        messages = model.await_args_list[3].args[0]
        self.assertEqual(json.loads(messages[-1]["content"]), {"destination": None})
        self.assertEqual(
            [event["name"] for event in conversation.events if event["type"] == "tool_start"],
            ["discover_tools", "read_skill", "maps_status", "discover_tools", "read_skill"],
        )
        self.assertEqual(conversation.events[-1]["type"], "idle")

    async def test_tool_failure_stops_without_retry(self):
        """Functionality: Preserve unknown completion on transport failure.
        Inputs: one scripted mutation and failing browser; Outputs: localized explanation,
        no replay.
        Logic: Real MCP error becomes bounded evidence for one final model response.
        Constraints: fake transport failure only; no provider quality claim.
        """
        self.router.return_value = tool_message("route_intent", {
            "route": "system", "intent_ids": ["camera.start"], "language": "en",
        })
        conversation = Conversation("owned-tab", "token")
        with patch(
            "core.agent.runtime.completion", AsyncMock(side_effect=[
                tool_message("discover_tools"), tool_message("read_skill", {"name": "camera"}),
                tool_message("camera_start"),
                {"role": "assistant", "content":
                 "I couldn't confirm the camera started; its state is unknown."},
            ])
        ) as model:
            with patch("core.ai_mcp.browser_call", AsyncMock(side_effect=TimeoutError)) as browser:
                await conversation.run("request", "Start my camera", None)
        self.assertEqual(model.await_count, 4)
        self.assertEqual(browser.await_count, 1)
        results = [event["result"] for event in conversation.events
                   if event["type"] == "tool_result"]
        self.assertEqual(results[-1]["completion"], "unknown")
        self.assertEqual(results[-1]["reason"], "operation_failed")
        replies = [event["text"] for event in conversation.events if event["type"] == "reply"]
        self.assertEqual(replies, ["I couldn't confirm the camera started; its state is unknown."])
        self.assertFalse(any(event["type"] == "error" for event in conversation.events))
        self.assertEqual(len(conversation.turns), 1)

    async def test_route_error_is_explained(self):
        """Functionality: Replace a raw route error with localized, bounded evidence.
        Inputs: Camera navigation request and MCP tool raising the public not_found code.
        Outputs: Model explanation receives route_unavailable, never the JSON error body.
        Logic: Run actual in-process MCP encoding and inspect the next completion context.
        Constraints: Synthetic relay failure; does not exercise the remote proxy.
        """
        self.router.return_value = tool_message("route_intent", {
            "route": "system", "intent_ids": ["camera.start"], "language": "en",
        })
        conversation = Conversation("owned-tab", "token")
        with patch("core.agent.runtime.completion", AsyncMock(side_effect=[
            tool_message("discover_tools"), tool_message("read_skill", {"name": "camera"}),
            tool_message("ui_navigate", {"page": "camera"}),
            {"role": "assistant", "content": "The camera page is unavailable in this app."},
        ])) as model:
            with patch("core.ai_mcp.browser_call", AsyncMock(
                side_effect=ValueError('{"code":"not_found"}')
            )):
                await conversation.run("request", "Open my camera", None)
        messages = model.await_args_list[3].args[0]
        tool_result = next(message["content"] for message in reversed(messages)
                           if message.get("role") == "tool")
        self.assertIn('"reason": "operation_failed"', tool_result)
        self.assertIn('"completion": "unknown"', tool_result)
        self.assertNotIn('"code":"not_found"', tool_result)
        self.assertEqual(conversation.events[-2]["type"], "reply")
        self.assertFalse(any(event["type"] == "error" for event in conversation.events))

    async def test_english_failure_explanation_ignores_chinese_history(self):
        """Functionality: Keep a failed action explanation aligned with the current English turn.
        Inputs: Chinese history, an English navigation request and a failed navigation tool.
        Outputs: Language policy in the model context and one bounded failure response. Logic:
        Exercise real MCP dispatch with synthetic classifier/provider and relay boundaries.
        Constraints: Verifies prompt wiring, not live provider language quality or public routing.
        """
        self.router.return_value = tool_message("route_intent", {
            "route": "system", "intent_ids": ["navigation.start"], "language": "en",
        })
        conversation = Conversation("owned-tab", "token")
        conversation.turns = [[
            {"role": "user", "content": "带我去附近的超市。"},
            {"role": "assistant", "content": "正在查找附近的超市。"},
        ]]
        with patch("core.agent.runtime.completion", AsyncMock(side_effect=[
            tool_message("discover_tools"), tool_message("read_skill", {"name": "navigation"}),
            tool_message("maps_status"),
            {"role": "assistant", "content": "The maps page is not ready."},
        ])) as model:
            with patch("core.ai_mcp.browser_call", AsyncMock(return_value={
                "page": "maps", "navigation": {"route": None}, "destination": None,
            })) as browser:
                await conversation.run(
                    "request", "I want to go to the nearest supermarket now.", None,
                )
        self.assertEqual(model.await_count, 4)
        self.assertEqual(browser.await_count, 1)
        final_system_messages = [
            item["content"] for item in model.await_args_list[3].args[0]
            if item.get("role") == "system"
        ]
        self.assertTrue(any("Required response language for this turn: English (en)" in item
                            and "Respond only in the required language" in item
                            for item in final_system_messages))
        self.assertTrue(any("tool output" in item and "page/device locale" in item
                            for item in final_system_messages))
        self.assertEqual(
            next(event["language"] for event in conversation.events if event["type"] == "intent"),
            "en",
        )
        self.assertEqual(
            [event["name"] for event in conversation.events if event["type"] == "tool_start"],
            ["discover_tools", "read_skill", "maps_status"],
        )

    async def test_failure_fallback_after_model_error(self):
        """Functionality: Keep failed-action explanations available when the model is unavailable.
        Inputs: Tool failure followed by a failed final completion.
        Outputs: Static localized reply and one mutation dispatch.
        Logic: Exercise the runtime's emergency rendering branch after failure evidence is saved.
        Constraints: Synthetic provider/transport errors; no model-quality claim.
        """
        self.router.return_value = tool_message("route_intent", {
            "route": "system", "intent_ids": ["camera.start"], "language": "en",
        })
        conversation = Conversation("owned-tab", "token")
        with patch("core.agent.runtime.completion", AsyncMock(side_effect=[
            tool_message("discover_tools"), tool_message("read_skill", {"name": "camera"}),
            tool_message("camera_start"), RuntimeError("provider unavailable"),
        ])):
            with patch("core.ai_mcp.browser_call", AsyncMock(side_effect=TimeoutError)) as browser:
                await conversation.run("request", "Start my camera", None)
        browser.assert_awaited_once()
        replies = [event["text"] for event in conversation.events if event["type"] == "reply"]
        self.assertEqual(len(replies), 1)
        self.assertIn("final state is unknown", replies[0])
        self.assertIn("I did not retry it", replies[0])
        self.assertFalse(any(event["type"] == "error" for event in conversation.events))

    def test_tool_error_normalization(self):
        """Functionality: Map explicit MCP error codes while withholding raw payloads.
        Inputs: Small synthetic CallToolResult content objects.
        Outputs: Route-unavailable failure with a known failed completion.
        Logic: Exercise the production normalizer without an SDK that wraps exception text.
        Constraints: Does not establish browser or public-relay error wording.
        """
        result = SimpleNamespace(content=[SimpleNamespace(text='{"code":"not_found"}')])
        normalized = tool_failure("ui_navigate", result)
        self.assertEqual(normalized, {
            "outcome": "failed", "tool": "ui_navigate", "reason": "route_unavailable",
            "completion": "failed",
        })
        read_error = SimpleNamespace(content=[SimpleNamespace(text="internal error")])
        self.assertEqual(tool_failure("camera_status", read_error)["completion"], "not_started")
        timeout = tool_exception("camera_start", TimeoutError("sensitive detail"))
        self.assertEqual(timeout, {
            "outcome": "failed", "tool": "camera_start", "reason": "operation_timeout",
            "completion": "unknown",
        })
        self.assertEqual(
            tool_exception("camera_status", RuntimeError("sensitive detail"))["completion"],
            "not_started",
        )

    def test_localized_failure_fallback(self):
        """Functionality: Keep the emergency tool-failure explanation concise and localized.
        Inputs: Normalized missing-camera outcome; Outputs: safe English and Chinese replies.
        Logic: Check cause, action boundary, and absence of raw tool payload.
        Constraints: Static rendering does not measure model language quality.
        """
        failure = {
            "tool": "camera_start", "reason": "camera_not_found", "completion": "failed",
        }
        english = failure_reply(failure, "en")
        chinese = failure_reply(failure, "zh")
        self.assertIn("No matching camera", english)
        self.assertIn("没有找到符合条件的摄像头", chinese)
        self.assertNotIn("{\"code\"", english)

    async def test_empty_failure_explanation_uses_fallback(self):
        """Functionality: Replace an empty model response after a failure with localized guidance.
        Inputs: Camera request, failed camera tool, and blank final completion.
        Outputs: A nonempty safe explanation without additional browser dispatch.
        Logic: Exercise the normal failure-evidence and final-reply path.
        Constraints: Synthetic provider/transport state; no real camera permission is requested.
        """
        self.router.return_value = tool_message("route_intent", {
            "route": "system", "intent_ids": ["camera.start"], "language": "en",
        })
        conversation = Conversation("owned-tab", "token")
        with patch("core.agent.runtime.completion", AsyncMock(side_effect=[
            tool_message("discover_tools"), tool_message("read_skill", {"name": "camera"}),
            tool_message("camera_start"), {"role": "assistant", "content": ""},
        ])):
            with patch("core.ai_mcp.browser_call", AsyncMock(side_effect=TimeoutError)) as browser:
                await conversation.run("request", "Start my camera", None)
        browser.assert_awaited_once()
        replies = [event["text"] for event in conversation.events if event["type"] == "reply"]
        self.assertEqual(len(replies), 1)
        self.assertTrue(replies[0].strip())
        self.assertIn("final state is unknown", replies[0])
        self.assertFalse(any(event["type"] == "error" for event in conversation.events))

    async def test_declined_action_blocks_batched_mutations(self):
        """Functionality: Stop every later action in a model batch after a negative outcome.
        Inputs: Camera start/stop tool batch and an explicit browser refusal.
        Outputs: One browser call plus a not_executed result for the batched stop.
        Logic: Preserve MCP call/result pairing while ending the action phase immediately.
        Constraints: Synthetic page state; no physical camera is requested.
        """
        self.router.return_value = tool_message("route_intent", {
            "route": "system", "intent_ids": ["camera.start"], "language": "en",
        })
        batch = tool_message("camera_start")
        batch["tool_calls"].append({
            "id": uuid.uuid4().hex, "type": "function",
            "function": {"name": "camera_stop", "arguments": "{}"},
        })
        conversation = Conversation("owned-tab", "token")
        with patch("core.agent.runtime.completion", AsyncMock(side_effect=[
            tool_message("discover_tools"), tool_message("read_skill", {"name": "camera"}),
            batch, {"role": "assistant", "content": "The browser could not find a camera."},
        ])) as model:
            with patch("core.ai_mcp.browser_call", AsyncMock(return_value={
                "available": True, "accepted": False, "reason": "camera_not_found",
                "state": "stopped",
            })) as browser:
                await conversation.run("request", "Start my camera", None)
        browser.assert_awaited_once()
        self.assertEqual(browser.await_args.args[1], "camera_start")
        final_context = model.await_args_list[3].args[0]
        paired_results = [message for message in final_context if message.get("role") == "tool"]
        self.assertEqual(json.loads(paired_results[-1]["content"]), {
            "outcome": "not_executed", "reason": "previous_action_failed",
        })
        self.assertEqual(conversation.events[-2]["type"], "reply")

    async def test_cancel_and_deduplicate(self):
        """Functionality: Prevent duplicate/overlapping tasks and cancel future scheduling.
        Inputs: pending mock model; Outputs: cancellations/explicit rejections.
        Logic: Admit once, reject replay and busy, then cancel.
        Constraints: No device rollback claim.
        """
        conversation = Conversation("owned-tab", "token")
        blocker = asyncio.Event()
        with patch("core.agent.runtime.completion",
                   side_effect=lambda *args, **kwargs: blocker.wait()):
            conversation.submit("one", "开始")
            await asyncio.sleep(0.01)
            with self.assertRaisesRegex(AgentError, "重复"):
                conversation.submit("one", "开始")
            with self.assertRaisesRegex(AgentError, "执行"):
                conversation.submit("two", "停止")
            conversation.cancel()
            await conversation.task
        self.assertTrue(any(event["type"] == "cancelled" for event in conversation.events))

    def test_input_validation(self):
        """Functionality: Reject malformed external submissions before model/device work.
        Inputs: invalid messages; Outputs: AgentError; Logic: explicit schema and UUID checks.
        Constraints: no network or task scheduling occurs.
        """
        conversation = Conversation("owned-tab", "token")
        for message in [
            [],
            {},
            {"type": "submit", "request_id": "bad", "text": "x"},
            {"type": "submit", "request_id": str(uuid.uuid4()), "text": " "},
            {"type": "submit", "request_id": str(uuid.uuid4()), "text": "x", "audio": "x"},
        ]:
            with self.assertRaises(AgentError):
                handle_message(conversation, message)

    async def test_audio_validation(self):
        """Functionality: Validate audio before ASR. Inputs: valid/invalid generated WAVs.
        Outputs: transcript or error; Logic: only one valid input reaches mocked provider.
        Constraints: silence is a fixture, no speech recognition claim.
        """
        with patch(
            "core.agent.provider.completion", AsyncMock(return_value={"content": "停止导航"})
        ) as model:
            self.assertEqual(await transcribe(wav_input()), "停止导航")
            for audio in [
                "not base64",
                wav_input(8000),
                wav_input(duration=0.05),
                wav_input(duration=21),
            ]:
                with self.assertRaises(AgentError):
                    await transcribe(audio)
        self.assertEqual(model.await_count, 1)

    async def test_provider_contract(self):
        """Functionality: Verify request format, secret handling and HTTP failure without retry.
        Inputs: fake HTTP responses and test-only key; Outputs: request/body and error assertions.
        Logic: Real HTTP client against MockTransport; Constraints: not a Qwen service test.
        """
        requests = []

        def handler(request):
            """Functionality: Capture fake request. Inputs: HTTP request; Outputs: canned response.
            Logic: Fail second request; Constraints: in-memory test boundary, no external I/O.
            """
            requests.append(request)
            return (
                httpx2.Response(
                    200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]}
                )
                if len(requests) == 1
                else httpx2.Response(401)
            )

        client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
        config = {
            "key": "test-only-secret",
            "url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "model": "qwen-plus",
            "asr_model": "qwen3-asr-flash",
        }
        with patch("core.agent.provider.configuration", return_value=config):
            with patch("core.agent.provider.httpx2.AsyncClient", return_value=client):
                result = await completion([{"role": "user", "content": "test"}])
            self.assertEqual(result["content"], "ok")
            client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
            with patch("core.agent.provider.httpx2.AsyncClient", return_value=client):
                with self.assertRaisesRegex(AgentError, "401"):
                    await completion([{"role": "user", "content": "test"}])
        self.assertEqual(len(requests), 2)
        body = json.loads(requests[0].content)
        self.assertFalse(body["enable_thinking"])
        self.assertNotIn("test-only-secret", requests[0].url.query.decode())

    async def test_geocoder_contract(self):
        """Functionality: Verify applied country/focus and preservation of place identity.
        Inputs: mocked ORS with in-country and out-of-country candidates; Outputs: filtered
        candidates, explicit exclusion count and request query; Logic: one HTTP request only.
        Constraints: fake geocoder, not proof a named place exists.
        """
        requests = []

        def handler(request):
            """Functionality: Return explicit mixed-country candidates at the HTTP boundary.
            Inputs: request; Outputs: JSON response and recorded request.
            Logic: Same synthetic name in two countries; Constraints: no actual geocoder access.
            """
            requests.append(request)
            return httpx2.Response(200, json={"features": [
                {"geometry": {"type": "Point", "coordinates": point},
                 "properties": {"label": "Synthetic test library", "name": "Test Library",
                                "country_a": code, "layer": "venue", "accuracy": "point"}}
                for code, point in (("SGP", [103.77, 1.3]), ("CHN", [120.74, 31.26]))
            ]})

        transport = httpx2.MockTransport(handler)
        client = httpx2.AsyncClient(transport=transport)
        with patch.dict("os.environ", {"ORS_API_KEY": "test-only",
                                       "PRS_AGENT_SEARCH_COUNTRY": "SGP"}):
            with patch("core.agent.places.httpx2.AsyncClient", return_value=client):
                result = await search_places("library", [103.77, 1.3])
        self.assertEqual(result["candidates"][0]["coordinates"], [103.77, 1.3])
        self.assertEqual(result["candidates"][0]["layer"], "venue")
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["excluded_country_count"], 1)
        self.assertEqual(result["search_context"]["country_source"], "deployment")
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].url.params["boundary.country"], "SGP")
        self.assertEqual(requests[0].url.params["focus.point.lon"], "103.77")
        self.assertNotIn("test-only", str(requests[0].url))

    async def test_geocoder_country_override(self):
        """Functionality: Preserve an explicit overseas destination despite deployment defaults.
        Inputs: CHN override and mocked country-matching branch; Outputs: actual request/candidate.
        Logic: Explicit country replaces only the default search boundary.
        Constraints: No user GPS inference, language-based country selection or external traffic.
        """
        requests = []
        transport = httpx2.MockTransport(lambda request: requests.append(request)
                                        or httpx2.Response(
            200, json={"features": [{"geometry": {"type": "Point", "coordinates": [120.74, 31.26]},
                                     "properties": {"label": "Synthetic overseas branch",
                                                    "country_a": "CHN"}}]},
        ))
        client = httpx2.AsyncClient(transport=transport)
        with patch.dict("os.environ", {"ORS_API_KEY": "test-only",
                                       "PRS_AGENT_SEARCH_COUNTRY": "SGP"}):
            with patch("core.agent.places.httpx2.AsyncClient", return_value=client):
                result = await search_places("Overseas branch", country="CHN")
        self.assertEqual(result["candidates"][0]["country_code"], "CHN")
        self.assertEqual(result["search_context"]["country_source"], "request")
        self.assertEqual(requests[0].url.params["boundary.country"], "CHN")
        self.assertNotIn("focus.point.lon", requests[0].url.params)

    async def test_geocoder_rejects_unknown_country(self):
        """Functionality: Fail visibly for invalid scope or missing country provenance.
        Inputs: invalid arguments and one mocked malformed ORS response; Outputs: typed errors.
        Logic: Validate input before creating a client, validate response before use.
        Constraints: No geographic guessing, unrestricted retry or real external calls.
        """
        with patch("core.agent.places.httpx2.AsyncClient") as factory:
            with self.assertRaises(AgentError):
                await search_places("Library", country="Singapore")
            factory.assert_not_called()
        client = httpx2.AsyncClient(transport=httpx2.MockTransport(lambda request: httpx2.Response(
            200, json={"features": [{"geometry": {"type": "Point", "coordinates": [103.77, 1.3]},
                                     "properties": {"label": "Country missing"}}]},
        )))
        with patch.dict("os.environ", {"ORS_API_KEY": "test-only",
                                       "PRS_AGENT_SEARCH_COUNTRY": "SGP"}):
            with patch("core.agent.places.httpx2.AsyncClient", return_value=client):
                with self.assertRaises(AgentError) as error:
                    await search_places("Library")
        self.assertEqual(error.exception.code, "geocoder_invalid_response")

    async def test_start_observes_actual_state(self):
        """Functionality: Prevent accepted-only success from pending or failed device startup.
        Inputs: explicit mocked start/status boundaries; Outputs: verification flags and counts.
        Logic: One mutation then observation; Constraints: no physical-device success claim.
        """
        adapter = BrowserTools("owned-tab")
        with patch(
            "core.ai_mcp.navigation_start", AsyncMock(return_value={"accepted": True})
        ) as start:
            with patch(
                "core.ai_mcp.browser_call",
                AsyncMock(return_value={"state": "error", "active": False}),
            ):
                result = await adapter.navigation_start()
        start.assert_awaited_once_with("owned-tab")
        self.assertFalse(result["verified"])
        self.assertFalse(result["vibration_guidance"])
        with patch(
            "core.ai_mcp.browser_call",
            AsyncMock(
                side_effect=[
                    {"accepted": True, "state": "starting"},
                    {"state": "streaming", "active": True},
                ]
            ),
        ) as browser:
            result = await adapter.camera_start()
        self.assertTrue(result["verified"])
        self.assertEqual(
            [call.args[1] for call in browser.await_args_list], ["camera_start", "camera_status"]
        )

    async def test_cancel_before_start(self):
        """Functionality: Complete UI cancellation even before the task coroutine starts.
        Inputs: submit/cancel in one event-loop tick; Outputs: terminal idle with no model call.
        Logic: Done callback handles never-entered task; Constraints: no device interaction.
        """
        conversation = Conversation("owned-tab", "token")
        with patch("core.agent.runtime.completion", AsyncMock()) as model:
            conversation.submit("immediate", "test")
            conversation.cancel()
            await asyncio.gather(conversation.task, return_exceptions=True)
            await asyncio.sleep(0)
        model.assert_not_called()
        self.assertEqual(conversation.events[-1]["type"], "idle")

    async def test_socket_binding_and_heartbeat(self):
        """Functionality: Authenticate a tab and keep an idle proxy connection alive.
        Inputs: real ASGI communicator, synthetic control-channel ownership proof.
        Outputs: atomic replay and pong; Logic: session binding without user/model data.
        Constraints: No real network/proxy; ownership browser response is explicitly mocked.
        """
        token = str(uuid.uuid4())
        scope = {
            "type": "websocket",
            "path": "/ws/agent/",
            "headers": [(b"origin", b"http://127.0.0.1:8766"), (b"host", b"127.0.0.1:8766")],
        }
        with patch.dict(conversations, {}, clear=True):
            with patch(
                "core.agent.transport.hub.command", AsyncMock(return_value={"token": token})
            ):
                connection = ApplicationCommunicator(agent_socket, scope)
                await connection.send_input({"type": "websocket.connect"})
                self.assertEqual((await connection.receive_output())["type"], "websocket.accept")
                await connection.send_input(
                    {
                        "type": "websocket.receive",
                        "text": json.dumps(
                            {"type": "hello", "session_id": "owned-tab", "token": token}
                        ),
                    }
                )
                ready = json.loads((await connection.receive_output())["text"])
                self.assertEqual(ready, {"type": "ready", "busy": False, "events": []})
                await connection.send_input(
                    {"type": "websocket.receive", "text": '{"type":"ping"}'}
                )
                self.assertEqual(
                    json.loads((await connection.receive_output())["text"]), {"type": "pong"}
                )
                await connection.send_input({"type": "websocket.disconnect"})
                await connection.wait()

    async def test_socket_rejects_wrong_owner(self):
        """Functionality: Refuse forged origin and mismatched tab token.
        Inputs: ASGI requests and synthetic ownership proof; Outputs: 1008 closes.
        Logic: Both checks occur before conversation access.
        Constraints: No real browser auth claim.
        """
        scope = {
            "type": "websocket",
            "path": "/ws/agent/",
            "headers": [(b"origin", b"https://untrusted.invalid"), (b"host", b"127.0.0.1:8766")],
        }
        connection = ApplicationCommunicator(agent_socket, scope)
        self.assertEqual((await connection.receive_output())["code"], 1008)
        await connection.wait()
        scope["headers"][0] = (b"origin", b"http://127.0.0.1:8766")
        with patch(
            "core.agent.transport.hub.command", AsyncMock(return_value={"token": str(uuid.uuid4())})
        ):
            connection = ApplicationCommunicator(agent_socket, scope)
            await connection.send_input({"type": "websocket.connect"})
            await connection.receive_output()
            await connection.send_input(
                {
                    "type": "websocket.receive",
                    "text": json.dumps(
                        {"type": "hello", "session_id": "owned-tab", "token": str(uuid.uuid4())}
                    ),
                }
            )
            self.assertEqual((await connection.receive_output())["code"], 1008)
            await connection.wait()
