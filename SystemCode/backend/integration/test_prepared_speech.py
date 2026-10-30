"""Validate startup audio identity, guest admission and unchanged non-startup requests."""
import asyncio
import json
import uuid
from contextlib import asynccontextmanager

from asgiref.testing import ApplicationCommunicator
from django.test import SimpleTestCase, override_settings

from .prepared_speech import TEXTS, PreparedSpeechApplication, load_clips


@override_settings(CAMERA_ALLOWED_ORIGINS=("http://127.0.0.1:8766",))
class PreparedSpeechTests(SimpleTestCase):
    def setUp(self):
        self.forwarded = []

        async def fallback(scope, receive, send):
            self.forwarded.extend([await receive(), await receive()])
            await send({"type": "websocket.accept"})
            await send({"type": "websocket.close", "code": 1000})

        self.app = PreparedSpeechApplication(fallback)

    async def connect(self, origin=b"http://127.0.0.1:8766"):
        peer = ApplicationCommunicator(self.app, {
            "type": "websocket", "path": "/ws/tts/", "headers": [
                (b"host", b"127.0.0.1:8766"), (b"origin", origin),
            ],
        })
        await peer.send_input({"type": "websocket.connect"})
        return peer, await peer.receive_output()

    async def test_verified_bilingual_pcm(self):
        self.assertEqual(len(load_clips()), 2)
        for language, text in TEXTS.items():
            peer, accepted = await self.connect()
            self.assertEqual(accepted["type"], "websocket.accept")
            request_id = str(uuid.uuid4())
            await peer.send_input({"type": "websocket.receive", "text": json.dumps({
                "type": "synthesize", "text": text, "language": language,
                "rate": 2, "request_id": request_id,
            })})
            config = json.loads((await peer.receive_output())["text"])
            self.assertEqual(config["request_id"], request_id)
            self.assertEqual(config["sample_rate"], 24000)
            pcm = bytearray()
            while True:
                item = await peer.receive_output()
                if "bytes" in item:
                    pcm.extend(item["bytes"])
                else:
                    complete = json.loads(item["text"])
                    break
            self.assertEqual(complete["audio_bytes"], len(pcm))
            self.assertEqual(complete["request_id"], request_id)
            self.assertTrue(any(pcm))
            self.assertEqual((await peer.receive_output())["code"], 1000)
            await peer.wait()
        self.assertFalse(self.forwarded)

    async def test_foreign_origin_rejected(self):
        peer, closed = await self.connect(b"https://evil.example")
        self.assertEqual(closed, {"type": "websocket.close", "code": 1008})
        await peer.wait()
        self.assertFalse(self.forwarded)

    async def test_uncovered_wording_preserves_original_request(self):
        peer, _ = await self.connect()
        message = {"type": "websocket.receive", "text": json.dumps({
            "type": "synthesize", "request_id": str(uuid.uuid4()),
            "text": "测试未覆盖语句", "language": "zh-CN", "rate": 2,
        })}
        await peer.send_input(message)
        self.assertEqual((await peer.receive_output())["type"], "websocket.close")
        await peer.wait()
        self.assertEqual(self.forwarded, [{"type": "websocket.connect"}, message])

    async def test_binary_request_rejected(self):
        peer, _ = await self.connect()
        await peer.send_input({"type": "websocket.receive", "bytes": b"invalid"})
        self.assertEqual(json.loads((await peer.receive_output())["text"])["type"], "error")
        self.assertEqual((await peer.receive_output())["code"], 1008)
        await peer.wait()

    def configure_upstream(self, messages, *, hold=False):
        self.upstream_sent, self.upstream_options = [], []
        self.upstream_closed = asyncio.Event()
        self.upstream_opened = asyncio.Event()
        owner = self

        class Upstream:
            async def send(self, raw):
                owner.upstream_sent.append(raw)

            async def recv(self):
                if hold:
                    await asyncio.Future()
                return messages.pop(0)

        @asynccontextmanager
        async def opener(url, **kwargs):
            owner.upstream_options.append((url, kwargs))
            owner.upstream_opened.set()
            try:
                yield Upstream()
            finally:
                owner.upstream_closed.set()

        self.app = PreparedSpeechApplication(self.app.application,
            upstream_origin="https://speech.example", opener=opener)

    async def submit_uncovered(self):
        peer, accepted = await self.connect()
        self.assertEqual(accepted["type"], "websocket.accept")
        raw = json.dumps({"type": "synthesize", "request_id": str(uuid.uuid4()),
                          "text": "警告，前方有椅子。", "language": "zh-CN", "rate": 2})
        await peer.send_input({"type": "websocket.receive", "text": raw})
        return peer, raw

    async def test_explicit_upstream_preserves_request_and_valid_pcm(self):
        messages = []
        self.configure_upstream(messages)
        peer, raw = await self.submit_uncovered()
        request_id = json.loads(raw)["request_id"]
        messages.extend([json.dumps({"type": "audio_config", "request_id": request_id,
                                    "sample_rate": 24000, "format": "pcm_s16le_mono"}),
                         b"\x01\x00" * 8,
                         json.dumps({"type": "complete", "request_id": request_id,
                                     "audio_bytes": 16})])
        self.assertEqual(json.loads((await peer.receive_output())["text"])["type"], "audio_config")
        self.assertEqual((await peer.receive_output())["bytes"], b"\x01\x00" * 8)
        self.assertEqual(json.loads((await peer.receive_output())["text"])["audio_bytes"], 16)
        self.assertEqual((await peer.receive_output())["code"], 1000)
        await peer.wait()
        self.assertEqual(self.upstream_sent, [raw])
        url, options = self.upstream_options[0]
        self.assertEqual(url, "wss://speech.example/mvp/ws/tts/")
        self.assertEqual(options["origin"], "https://speech.example")
        self.assertIsNone(options["proxy"])
        self.assertTrue(self.upstream_closed.is_set())

    async def test_upstream_identity_and_audio_protocol_rejected(self):
        for fault in ("identity", "format", "length", "pcm_before_config"):
            messages = []
            self.configure_upstream(messages)
            peer, raw = await self.submit_uncovered()
            request_id = json.loads(raw)["request_id"]
            config = {"type": "audio_config", "request_id": request_id,
                      "sample_rate": 24000, "format": "pcm_s16le_mono"}
            if fault == "identity":
                config["request_id"] = "wrong"
            elif fault == "format":
                config["sample_rate"] = 48000
            if fault == "pcm_before_config":
                messages.append(b"\x01\x00")
            else:
                messages.append(json.dumps(config))
            if fault == "length":
                messages.extend([b"\x01\x00", json.dumps({"type": "complete",
                                 "request_id": request_id, "audio_bytes": 4})])
                await peer.receive_output()
                await peer.receive_output()
            error = json.loads((await peer.receive_output())["text"])
            self.assertEqual(error["code"], "tts_upstream_unavailable", fault)
            self.assertEqual((await peer.receive_output())["code"], 1011)
            await peer.wait()
            self.assertTrue(self.upstream_closed.is_set())

    async def test_disconnect_cancels_owned_upstream(self):
        self.configure_upstream([], hold=True)
        peer, _ = await self.submit_uncovered()
        await asyncio.wait_for(self.upstream_opened.wait(), 1)
        await peer.send_input({"type": "websocket.disconnect", "code": 1000})
        await peer.wait()
        self.assertTrue(self.upstream_closed.is_set())

    async def test_fixed_startup_never_contacts_upstream(self):
        self.configure_upstream([])
        await self.test_verified_bilingual_pcm()
        self.assertFalse(self.upstream_options)
