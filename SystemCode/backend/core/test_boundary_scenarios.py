"""Responsibilities: Exercise low-coverage protocol and configuration boundaries with data matrices.
Implementation: Use temporary trusted skill packages, in-memory ASGI queues and bounded provider
fixtures to drive real adapters without external network, browser, GPU or speech hardware.
Related Modules: core.agent.skills/transport; core.ai_mcp; core.echo; core.hf_video_relay.
Declaration Index: SkillBoundaryTests; SkillBoundaryTests.test_skill_package_matrix;
ControlBoundaryTests; ControlBoundaryTests.test_mcp_failure_and_validation_matrix;
ControlBoundaryTests.test_transport_message_matrix; EchoBoundaryTests;
EchoBoundaryTests.test_echo_exception_and_timeout_matrix; LiveBoundaryTests;
LiveBoundaryTests.test_live_configuration_and_allocation_matrix.
Variable Index: VALID_POLICY — minimal declarative skill policy used by temporary packages;
ORIGIN — same-origin ASGI fixture; TOKEN — synthetic HF identity used only in memory.
"""

import asyncio
import io
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from mcp.server.mcpserver.exceptions import ToolError

from core import ai_mcp, echo, hf_video_relay
from core.agent import skills, transport
from core.agent.provider import AgentError
from core.detector import DetectionError

ORIGIN = "http://127.0.0.1:8766"
TOKEN = "hf_synthetic_boundary_token"
VALID_POLICY = {"tools": ["maps_status"], "requires": {}, "limits": {},
                "result_counts": {}}


class SkillBoundaryTests(SimpleTestCase):
    """Functionality: Validate skill discovery against malformed package and policy matrices.
    Logic: Each mutation is loaded through the production parser using an isolated ROOT.
    Constraints: Temporary files never execute code and contain no user or provider data.
    """

    def _package(self):
        """Functionality: Build one temporary valid skill package.
        Inputs: None. Outputs: temporary root, package directory and policy path.
        Logic: Write the smallest accepted frontmatter, workflow and policy. Constraints: caller
        owns cleanup through TemporaryDirectory and must keep the ROOT patch active while reading.
        """
        directory = tempfile.TemporaryDirectory()
        root = Path(directory.name)
        package = root / "demo"
        package.mkdir()
        (root / "system.md").write_text("System instructions", encoding="utf-8")
        (package / "SKILL.md").write_text(
            "---\nname: demo\ndescription: Demo workflow\n---\nWorkflow\n",
            encoding="utf-8",
        )
        policy = package / "tools.json"
        policy.write_text(json.dumps(VALID_POLICY), encoding="utf-8")
        return directory, root, package, policy

    def test_skill_package_matrix(self):
        """Functionality: Cover valid discovery and malformed skill/policy/reference branches.
        Inputs: temporary frontmatter, JSON policies and reference files with boundary mutations.
        Outputs: accepted metadata or ValueError before any workflow content is trusted.
        Logic: Table-driven mutations preserve the real parser and containment checks.
        Constraints: No production skill files are changed and no executable content is loaded.
        """
        directory, root, package, policy = self._package()
        try:
            with patch.object(skills, "ROOT", root):
                self.assertEqual(skills.catalog(), {"demo": "Demo workflow"})
                self.assertIn("Available skills:", skills.instructions())
                before = skills.fingerprint()
                (package / "references").mkdir()
                reference = package / "references/context.md"
                reference.write_text("Reference", encoding="utf-8")
                self.assertNotEqual(before, skills.fingerprint())
                self.assertIn("Reference: context.md", skills.read_skill("demo"))

                for source in (
                    "missing frontmatter",
                    "---\nname: other\ndescription: Demo\n---\n",
                    "---\nname: demo\ndescription: Demo\n---\n" + "x" * skills.MAX_SKILL_CHARS,
                ):
                    with self.subTest(frontmatter=source[:20]):
                        (package / "SKILL.md").write_text(source, encoding="utf-8")
                        with self.assertRaises(ValueError):
                            skills.catalog()
                (package / "SKILL.md").write_text(
                    "---\nname: demo\ndescription: Demo workflow\n---\nWorkflow\n",
                    encoding="utf-8",
                )

                for source in (
                    "not-json",
                    json.dumps({"tools": ["maps_status"]}),
                    json.dumps({**VALID_POLICY, "tools": []}),
                    json.dumps({**VALID_POLICY, "tools": ["Maps"]}),
                    json.dumps({**VALID_POLICY, "tools": ["maps_status", "maps_status"]}),
                    json.dumps({**VALID_POLICY, "requires": {"maps_status": "bad"}}),
                    json.dumps({"tools": ["a", "b"], "requires": {"a": ["b"], "b": ["a"]},
                                "limits": {}, "result_counts": {}}),
                    json.dumps({**VALID_POLICY, "limits": {"maps_status": 0}}),
                    json.dumps({**VALID_POLICY, "result_counts": []}),
                    json.dumps({**VALID_POLICY, "result_counts": {
                        "maps_status": {"field": "bad-field", "min": 0, "max": 1}
                    }}),
                    json.dumps({**VALID_POLICY, "result_counts": {
                        "maps_status": {"field": "count", "min": 1, "max": 0}
                    }}),
                    json.dumps({**VALID_POLICY, "result_counts": {
                        "maps_status": {"field": "count", "min": 0, "max": 1,
                                         "confirm_if": "bad-field"}
                    }}),
                    json.dumps({**VALID_POLICY, "result_counts": {
                        "maps_status": {"field": "count", "min": 0, "max": 1,
                                         "selection": {}}
                    }}),
                ):
                    policy.write_text(source, encoding="utf-8")
                    with self.subTest(policy=source[:30]), self.assertRaises(ValueError):
                        skills.tool_policy("demo")

                reference.write_text("x" * (skills.MAX_SKILL_CHARS * 4 + 1), encoding="utf-8")
                with self.assertRaises(ValueError):
                    skills.read_skill("demo")
                reference.write_text("x" * skills.MAX_SKILL_CHARS, encoding="utf-8")
                with self.assertRaises(ValueError):
                    skills.read_skill("demo")
                with self.assertRaises(ValueError):
                    skills.read_skill("missing")
        finally:
            directory.cleanup()


class ControlBoundaryTests(SimpleTestCase):
    """Functionality: Cover MCP adapter errors and Agent transport message branches.
    Logic: Invoke real async adapters with explicit exception and queue fixtures.
    Constraints: Browser ownership and model configuration are mocked; no external side effects.
    """

    async def test_mcp_failure_and_validation_matrix(self):
        """Functionality: Normalize database/browser/API failures and argument boundaries.
        Inputs: ValueError, ValidationError, transport exceptions and invalid timeouts/paths.
        Outputs: ToolError with stable safe diagnostics and no retry.
        Logic: Directly call production MCP functions so each exception branch is observable.
        Constraints: Django API requests use the in-process test application only.
        """
        for error in (
            ValueError("invalid"),
            ValidationError({"name": ["required"]}),
            RuntimeError("unexpected"),
        ):
            with self.subTest(error=type(error).__name__), self.assertRaises(ToolError):
                await ai_mcp.database_call(
                    "matrix", lambda error=error: (_ for _ in ()).throw(error)
                )
        for error in (ValueError("missing"), TimeoutError(), ConnectionError("gone")):
            with self.subTest(error=type(error).__name__), self.assertRaises(ToolError):
                with patch.object(ai_mcp.hub, "command", AsyncMock(side_effect=error)):
                    await ai_mcp.browser_call("missing", "snapshot")

        for path in ("relative", "//double", "https://external.invalid/path"):
            with self.subTest(path=path), self.assertRaises(ToolError):
                await ai_mcp.system_request(path)
        response = await ai_mcp.system_request("/live/")
        self.assertEqual(response["status"], 200)
        self.assertIn("text/html", response["content_type"])
        with patch("core.ai_mcp.httpx2.AsyncClient", side_effect=RuntimeError("fixture")):
            with self.assertRaises(ToolError):
                await ai_mcp.system_request("/live/")

        with self.assertRaises(ToolError):
            await ai_mcp.ui_navigate("missing", "camera", timeout=0)
        with patch("core.ai_mcp.hub.connection", return_value=object()), \
                patch("core.ai_mcp.browser_call", AsyncMock(return_value=[])):
            with self.assertRaises(ToolError):
                await ai_mcp.ui_navigate("tab", "camera")
        with patch("core.ai_mcp.hub.connection", return_value=object()), \
                patch("core.ai_mcp.browser_call", AsyncMock(side_effect=TimeoutError)):
            with self.assertRaises(ToolError):
                await ai_mcp.ui_navigate("tab", "camera")
        with self.assertRaises(ToolError):
            await ai_mcp.ui_action("tab", "click", "#button", timeout=0)
        with self.assertRaises(ToolError):
            await ai_mcp.speech_speak("tab", "   ")

    async def test_transport_message_matrix(self):
        """Functionality: Exercise Agent ping/cancel/clear, valid submissions and parser failures.
        Inputs: text/audio messages, malformed IDs, unknown fields and a queued pump item.
        Outputs: state calls, submission arguments, typed AgentError and one streamed reply.
        Logic: Use a mock conversation for pure message validation and a real queue for pump.
        Constraints: configuration is explicitly mocked; no model task is scheduled.
        """
        conversation = MagicMock()
        with patch("core.agent.transport.configuration"):
            self.assertEqual(transport.handle_message(conversation, {"type": "ping"}),
                             {"type": "pong"})
            self.assertIsNone(transport.handle_message(conversation, {"type": "cancel"}))
            self.assertIsNone(transport.handle_message(conversation, {"type": "clear"}))
            request_id = "00000000-0000-4000-8000-000000000001"
            transport.handle_message(conversation, {
                "type": "submit", "request_id": request_id, "text": "  hello  "
            })
            transport.handle_message(conversation, {
                "type": "submit", "request_id": request_id, "audio": "base64"
            })
        conversation.cancel.assert_called_once()
        conversation.clear.assert_called_once()
        self.assertEqual(conversation.submit.call_args_list[0].args, (request_id, "hello", None))
        self.assertEqual(conversation.submit.call_args_list[1].args, (request_id, None, "base64"))

        invalid = [
            None, [], {}, {"type": "submit", "request_id": "bad", "text": "x"},
            {"type": "submit", "request_id": request_id, "text": " "},
            {"type": "submit", "request_id": request_id, "audio": ""},
            {"type": "ping", "extra": 1},
        ]
        with patch("core.agent.transport.configuration"):
            for message in invalid:
                with self.subTest(message=message), self.assertRaises(AgentError):
                    transport.handle_message(conversation, message)

        queue = asyncio.Queue()
        sent = []

        async def send(message):
            """Functionality: Capture one pump frame. Inputs: ASGI message; Outputs: append.
            Logic: Record exactly one queue item. Constraints: no network transport is used.
            """
            sent.append(message)

        task = asyncio.create_task(transport.pump(queue, send))
        queue.put_nowait({"type": "evidence", "text": "hello"})
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(json.loads(sent[0]["text"]), {"type": "evidence", "text": "hello"})


class EchoBoundaryTests(SimpleTestCase):
    """Functionality: Exercise echo no-connect, timeout and unexpected-send cleanup branches.
    Logic: Drive the actual ASGI handler with deterministic receive/send callables.
    Constraints: Payloads are synthetic and no real browser media is decoded.
    """

    async def _run(self, events, send=None):
        """Functionality: Run echo_socket against a finite event queue.
        Inputs: ASGI events and optional send callback. Outputs: collected ASGI messages.
        Logic: Use an in-memory queue with the configured same-origin scope. Constraints: caller
        must keep tests isolated because active_connections is process-local.
        """
        queue = asyncio.Queue()
        for event in events:
            queue.put_nowait(event)
        output = []

        async def receive():
            """Functionality: Return the next fixture event. Inputs: None; Outputs: ASGI event."""
            return await queue.get()

        async def default_send(message):
            """Functionality: Record one fixture output. Inputs: ASGI message; Outputs: None."""
            output.append(message)

        await echo.echo_socket(
            {"type": "websocket", "headers": [
                (b"origin", ORIGIN.encode()), (b"host", b"127.0.0.1:8766")
            ]},
            receive,
            send or default_send,
        )
        return output

    async def test_echo_exception_and_timeout_matrix(self):
        """Functionality: Preserve close semantics for malformed protocol and idle sessions.
        Inputs: non-connect event, malformed control, unexpected ASGI type, oversized text and a
        short idle deadline. Outputs: no-connect no-op, 1008/1009 validation closes and 1011 error.
        Logic: Table-driven direct ASGI calls; each accepted session is released in finally.
        Constraints: Timeout is patched only for this test and does not alter production limits.
        """
        self.assertEqual(await self._run([{"type": "websocket.receive"}]), [])
        cases = [
            ([{"type": "websocket.connect"}, {"type": "websocket.receive", "text": "bad"}],
             1008),
            ([{"type": "websocket.connect"}, {"type": "websocket.other"}], 1011),
            ([{"type": "websocket.connect"}, {"type": "websocket.receive",
              "text": "x" * (echo.MAX_TEXT_BYTES + 1)}], 1009),
        ]
        for events, code in cases:
            with self.subTest(code=code):
                output = await self._run(events)
                self.assertEqual(output[-1]["code"], code)

        with patch.object(echo, "IDLE_SECONDS", 0.001):
            output = await self._run([{"type": "websocket.connect"}])
        self.assertEqual(output[-1], {"type": "websocket.close", "code": 1008})

        calls = []

        async def failing_send(message):
            """Functionality: Fail only while reporting the outer protocol error.
            Inputs: ASGI message; Outputs: accepted frame or OSError. Logic: trigger close-failure
            cleanup branch. Constraints: failure is synthetic and contains no payload.
            """
            calls.append(message)
            if message.get("code") == 1011:
                raise OSError("peer closed")

        await self._run([
            {"type": "websocket.connect"}, {"type": "websocket.other"}
        ], send=failing_send)
        self.assertEqual(calls[0]["type"], "websocket.accept")
        self.assertEqual(echo.active_connections, 0)


class LiveBoundaryTests(SimpleTestCase):
    """Functionality: Cover private HF live token, quota and allocation response contracts.
    Logic: Mock urllib/exchange at their network boundaries and validate every parser outcome.
    Constraints: Synthetic tokens and event IDs never authenticate or allocate real GPU work.
    """

    def test_live_configuration_and_allocation_matrix(self):
        """Functionality: Validate token, quota body and live completion counters.
        Inputs: missing/whitespace tokens, oversized or malformed quota bodies and event summaries.
        Outputs: typed DetectionError/ValueError or one normalized successful summary.
        Logic: Use real production parsers with no retry and assert request sequencing.
        Constraints: No HF endpoint, quota account or GPU session is contacted.
        """
        with patch.dict(os.environ, {"HF_INFERENCE_TOKEN": "hf_valid"}, clear=True):
            self.assertEqual(hf_video_relay.read_token(), "hf_valid")
        for value in ("", "plain", "hf bad", "hf_" + "x" * 4097):
            with patch.dict(os.environ, {"HF_INFERENCE_TOKEN": value}, clear=True):
                with self.subTest(token=value[:12]), self.assertRaises(DetectionError):
                    hf_video_relay.read_token()

        for body in (b"x" * 65537, b"not-json"):
            response = io.BytesIO(body)
            with patch("urllib.request.urlopen", return_value=response):
                with self.subTest(body=len(body)), self.assertRaises(ValueError):
                    hf_video_relay.read_free_quota(TOKEN)

        with patch("core.hf_video_relay.read_token", return_value=TOKEN), \
                patch("core.hf_video_relay.exchange") as exchange:
            with self.assertRaises(ValueError):
                hf_video_relay.allocate_live("capability", "other-token")
            exchange.assert_not_called()

        cases = [
            ({"event_id": "bad event"}, None, 1),
            ({"event_id": "event"}, [], 2),
            ({"event_id": "event"}, [{"frames": -1, "gpu_session_ms": 1}], 2),
            ({"event_id": "event"}, [{"frames": 1, "gpu_session_ms": float("nan")}], 2),
        ]
        for first, second, expected_calls in cases:
            with self.subTest(first=first, second=second), \
                    patch("core.hf_video_relay.read_token", return_value=TOKEN), \
                    patch("core.hf_video_relay.exchange", side_effect=[first, second]) as exchange:
                with self.assertRaises(ValueError):
                    hf_video_relay.allocate_live("capability", TOKEN)
                self.assertEqual(exchange.call_count, expected_calls)

        with patch("core.hf_video_relay.read_token", return_value=TOKEN), \
                patch("core.hf_video_relay.exchange", side_effect=[
                    {"event_id": "event_1"}, [{"frames": 1, "gpu_session_ms": 2.5}]
                ]) as exchange:
            result = hf_video_relay.allocate_live("capability", TOKEN)
        self.assertEqual(result["frames"], 1)
        self.assertEqual(exchange.call_count, 2)
