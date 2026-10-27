"""Responsibilities: Verify backend TTS validation, provider protocol and WebSocket boundaries.
Implementation: Exercise the real handler with deterministic provider/client ASGI fixtures.
Related Modules: core.agent.tts/provider; config.asgi; integration.mobile.
Declaration Index: FakePeer; FakePeer.__init__; FakePeer.__aenter__; FakePeer.__aexit__;
FakePeer.send; FakePeer.recv; FakeConnector; FakeConnector.__call__; BlockingPeer;
BlockingPeer.recv; AgentTtsTests; AgentTtsTests.request;
AgentTtsTests.test_request_validation; AgentTtsTests.test_streaming_bilingual_pcm;
AgentTtsTests.test_invalid_request_never_connects; AgentTtsTests.test_origin_is_required;
AgentTtsTests.test_browser_disconnect_cancels_provider;
AgentTtsTests.test_asgi_route_dispatches_tts.
Variable Index: PCM_FIXTURE — two deterministic little-endian samples; FakePeer.sent/events/closed
— only the emulated provider protocol state; no real API key, provider request, recording or speaker
test.
"""

import asyncio
import base64
from functools import partial
import json
import uuid
from unittest.mock import AsyncMock, patch

from asgiref.testing import ApplicationCommunicator
from django.test import SimpleTestCase, override_settings

from .agent import tts

PCM_FIXTURE = b"\x00\x00\x10\x00"


class FakePeer:
    """Functionality: Model the minimum Qwen realtime session used by one test.
    Logic: Return a fixed event sequence and retain outbound session/text events for assertions.
    Constraints: No socket, credentials, provider billing, audio decoding or playback.
    """

    def __init__(self, events):
        """Functionality: Initialize a test peer. Inputs: ordered provider event mappings.
        Outputs: asynchronous context manager with independent event queues.
        Logic: Copy the event sequence; Constraints: fixture values only.
        """
        self.events = list(events)
        self.sent = []
        self.closed = False

    async def __aenter__(self):
        """Functionality: Enter the fake provider context. Inputs: None. Outputs: self.
        Logic: Match the websockets connection API; Constraints: no network action.
        """
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        """Functionality: Close the fake context. Inputs: exception tuple. Outputs: false.
        Logic: Propagate handler errors; Constraints: does not suppress cancellation.
        """
        self.closed = True
        return False

    async def send(self, value):
        """Functionality: Record one upstream text event. Inputs: serialized JSON.
        Outputs: None. Logic: Parse and store the event for protocol assertions.
        Constraints: No provider side effects.
        """
        self.sent.append(json.loads(value))

    async def recv(self):
        """Functionality: Return the next fixed upstream event. Inputs: None. Outputs: JSON text.
        Logic: Pop in protocol order. Constraints: Test sequence exhaustion is an assertion failure.
        """
        return json.dumps(self.events.pop(0))


class BlockingPeer(FakePeer):
    """Functionality: Hold provider audio until the browser explicitly disconnects.
    Logic: Consume handshake acknowledgements, then block on an awaitable socket read.
    Constraints: No network or provider request; cancellation state is asserted by the test.
    """

    async def recv(self):
        """Functionality: Return handshake fixtures then wait indefinitely for provider audio.
        Inputs: None. Outputs: JSON acknowledgement or a cancellable wait.
        Logic: The handler's client-disconnect monitor must cancel this receive.
        Constraints: No timer or real provider dependency.
        """
        if self.events:
            return await super().recv()
        await asyncio.Future()


class FakeConnector:
    """Functionality: Capture the fixed upstream address and connection options.
    Logic: Return a supplied FakePeer as an async context manager.
    Constraints: The only test of actual provider networking is a separate deployed smoke test.
    """

    def __init__(self, peer):
        """Functionality: Bind one fake peer. Inputs: FakePeer. Outputs: connector fixture.
        Logic: retain object identity for assertions; Constraints: no global state.
        """
        self.peer = peer
        self.calls = []

    def __call__(self, url, **kwargs):
        """Functionality: Capture one connection request. Inputs: URL and websocket options.
        Outputs: FakePeer context manager. Logic: record arguments once.
        Constraints: never open a real connection.
        """
        self.calls.append((url, kwargs))
        return self.peer


class AgentTtsTests(SimpleTestCase):
    """Functionality: Check the exact browser/server speech contract and bilingual stream.
    Logic: Run the ASGI boundary and upstream bridge in memory with a fixed provider double.
    Constraints: Assertions prove protocol plumbing only, not TTS provider availability or
    audibility.
    """

    @staticmethod
    def request(language="zh-CN", text="你好"):
        """Functionality: Build a valid browser request for one ASGI case.
        Inputs: supported language and fixture text. Outputs: JSON text with a fresh UUID.
        Logic: match the strict client schema; Constraints: test-only content.
        """
        return json.dumps({"type": "synthesize", "request_id": str(uuid.uuid4()),
            "text": text, "language": language, "rate": 1.25}, ensure_ascii=False)

    def test_request_validation(self):
        """Functionality: Accept Chinese, English and auto modes; reject malformed boundaries.
        Inputs: deterministic JSON cases. Outputs: normalized language values or AgentError.
        Logic: verify exact field validation, Unicode length, rate and ID parsing.
        Constraints: No upstream connection is part of this unit test.
        """
        for language, text, expected in (("zh-CN", "中文", "Chinese"),
            ("en-US", "English", "English"), ("auto", "Hello 中文", "Auto")):
            request = tts.validate_request(self.request(language, text))
            self.assertEqual(request["language"], expected)
            self.assertEqual(request["text"], text)
        base = {"type": "synthesize", "request_id": str(uuid.uuid4()), "text": "valid",
            "language": "en-US", "rate": 1}
        invalid = [None, "{}", json.dumps({**base, "extra": True}),
            json.dumps({**base, "request_id": "wrong"}), json.dumps({**base, "text": " "}),
            json.dumps({**base, "language": "fr-FR"}), json.dumps({**base, "rate": True}),
            json.dumps({**base, "rate": 2.1}), json.dumps({**base, "text": "x" * 1001}),
            json.dumps({**base, "text": "\ud800"})]
        for value in invalid:
            with self.subTest(value=value if isinstance(value, str) else type(value).__name__):
                with self.assertRaises(tts.AgentError):
                    tts.validate_request(value)

    @override_settings(CAMERA_ALLOWED_ORIGINS={"http://127.0.0.1:8766"})
    async def test_streaming_bilingual_pcm(self):
        """Functionality: Verify the same-origin endpoint streams Chinese and English PCM.
        Inputs: two ASGI requests and provider event fixtures. Outputs: binary payload plus terminal
        metadata.
        Logic: assert explicit region mapping, language/rate configuration, input text and exact
        bytes.
        Constraints: Provider, model availability and hardware speaker remain mocked/unverified.
        """
        for language, text, language_type in (("zh-CN", "你好", "Chinese"),
            ("en-US", "Turn left", "English")):
            peer = FakePeer([
                {"type": "session.created"}, {"type": "session.updated"},
                {"type": "response.audio.delta", "delta": base64.b64encode(PCM_FIXTURE).decode()},
                {"type": "response.done"},
            ])
            connector = FakeConnector(peer)
            scope = {"type": "websocket", "path": "/ws/tts/", "headers": [
                (b"origin", b"http://127.0.0.1:8766"), (b"host", b"127.0.0.1:8766")]}
            app = ApplicationCommunicator(partial(tts.tts_socket, opener=connector), scope)
            with patch("core.agent.tts.configuration", return_value={
                "key": "fixture-only", "url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"}):
                await app.send_input({"type": "websocket.connect"})
                self.assertEqual((await app.receive_output())["type"], "websocket.accept")
                await app.send_input(
                    {"type": "websocket.receive", "text": self.request(language, text)}
                )
                config = json.loads((await app.receive_output())["text"])
                audio = await app.receive_output()
                complete = json.loads((await app.receive_output())["text"])
                close = await app.receive_output()
                await app.wait()
            self.assertEqual(config["sample_rate"], tts.SAMPLE_RATE)
            self.assertEqual(audio["bytes"], PCM_FIXTURE)
            self.assertEqual(complete["audio_bytes"], len(PCM_FIXTURE))
            self.assertEqual(close["type"], "websocket.close")
            self.assertEqual(connector.calls[0][0], tts.REGIONAL_URLS[
                "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"])
            self.assertEqual(connector.calls[0][1]["additional_headers"]["Authorization"],
                "Bearer fixture-only")
            self.assertEqual(peer.sent[0]["session"]["language_type"], language_type)
            self.assertEqual(peer.sent[0]["session"]["speech_rate"], 1.25)
            self.assertEqual(peer.sent[1]["text"], text)
            self.assertEqual([event["type"] for event in peer.sent], [
                "session.update", "input_text_buffer.append", "input_text_buffer.commit"])

    @override_settings(CAMERA_ALLOWED_ORIGINS={"http://127.0.0.1:8766"})
    async def test_invalid_request_never_connects(self):
        """Functionality: Reject malformed request before provider configuration or networking.
        Inputs: accepted same-origin socket and invalid JSON. Outputs: explicit error and policy
        close.
        Logic: assert provider connector and config remain untouched.
        Constraints: No model request, retry or user text log.
        """
        connector = FakeConnector(FakePeer([]))
        scope = {"type": "websocket", "path": "/ws/tts/", "headers": [
            (b"origin", b"http://127.0.0.1:8766"), (b"host", b"127.0.0.1:8766")]}
        app = ApplicationCommunicator(partial(tts.tts_socket, opener=connector), scope)
        await app.send_input({"type": "websocket.connect"})
        await app.receive_output()
        with patch("core.agent.tts.configuration") as config:
            await app.send_input({"type": "websocket.receive", "text": "{}"})
            error = json.loads((await app.receive_output())["text"])
            await app.receive_output()
            await app.wait()
        self.assertEqual(error["code"], "invalid_speech_request")
        config.assert_not_called()
        self.assertEqual(connector.calls, [])

    @override_settings(CAMERA_ALLOWED_ORIGINS={"http://127.0.0.1:8766"})
    async def test_origin_is_required(self):
        """Functionality: Refuse a foreign-origin TTS socket before accepting or loading config.
        Inputs: WebSocket scope with a foreign Origin. Outputs: policy close.
        Logic: verify origin/host admission occurs before reading the request.
        Constraints: This is not identity authentication or a public network test.
        """
        scope = {"type": "websocket", "path": "/ws/tts/", "headers": [
            (b"origin", b"https://untrusted.invalid"), (b"host", b"127.0.0.1:8766")]}
        app = ApplicationCommunicator(tts.tts_socket, scope)
        self.assertEqual((await app.receive_output())["code"], 1008)
        await app.wait()

    @override_settings(CAMERA_ALLOWED_ORIGINS={"http://127.0.0.1:8766"})
    async def test_browser_disconnect_cancels_provider(self):
        """Functionality: Ensure a closed browser immediately cancels pending upstream synthesis.
        Inputs: valid ASGI request and provider peer stalled after session setup. Outputs: closed
        provider context.
        Logic: send disconnect only after request submission and audio metadata, then await handler
        cleanup.
        Constraints: Provider cancellation behavior is modeled; live upstream behavior is verified
        separately.
        """
        peer = BlockingPeer([{"type": "session.created"}, {"type": "session.updated"}])
        connector = FakeConnector(peer)
        scope = {"type": "websocket", "path": "/ws/tts/", "headers": [
            (b"origin", b"http://127.0.0.1:8766"), (b"host", b"127.0.0.1:8766")]}
        app = ApplicationCommunicator(partial(tts.tts_socket, opener=connector), scope)
        with patch("core.agent.tts.configuration", return_value={
            "key": "fixture-only", "url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"}):
            await app.send_input({"type": "websocket.connect"})
            await app.receive_output()
            await app.send_input(
                {"type": "websocket.receive", "text": self.request("en-US", "Turn left")}
            )
            audio_config = json.loads((await app.receive_output())["text"])
            self.assertEqual(audio_config["type"], "audio_config")
            await app.send_input({"type": "websocket.disconnect", "code": 1000})
            await app.wait()
        self.assertTrue(peer.closed)
        self.assertEqual(len(connector.calls), 1)

    async def test_asgi_route_dispatches_tts(self):
        """Functionality: Route the public TTS WebSocket path to its provider handler.
        Inputs: One `/ws/tts/` ASGI scope and async channels. Outputs: one awaited handler call.
        Logic: replace only the target handler; Constraints: no real socket or provider call.
        """
        from config import asgi

        scope, receive, send = {"type": "websocket", "path": "/ws/tts/"}, AsyncMock(), AsyncMock()
        with patch("config.asgi.tts_socket", new_callable=AsyncMock) as handler:
            await asgi.application(scope, receive, send)
        handler.assert_awaited_once_with(scope, receive, send)
