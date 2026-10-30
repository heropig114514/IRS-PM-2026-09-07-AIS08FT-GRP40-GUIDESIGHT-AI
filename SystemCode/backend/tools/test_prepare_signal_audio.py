"""Responsibilities: Verify fixed signal-speech preparation without provider calls or playback.
Implementation: Explicit fake TTS frames and temporary exclusive file outputs.
Related Modules: tools.prepare_signal_audio; frontend/assets/signal-audio.
Declaration Index: transport; transport.Socket; transport.Socket.send; transport.Socket.recv;
transport.open_peer; SignalPreparationTests; SignalPreparationTests.test_endpoint_boundaries;
SignalPreparationTests.test_exact_command_and_pcm; SignalPreparationTests.test_protocol_failures;
SignalPreparationTests.test_cancellation_closes_peer; SignalPreparationTests.test_audio_descriptor;
SignalPreparationTests.test_exclusive_generation; SignalPreparationTests.test_failure_no_manifest.
SignalPreparationTests.test_exclusive_generation.collector;
SignalPreparationTests.test_failure_no_manifest.collector.
Variable Index: PCM is nonzero synthetic audio used only by tests; no actual model/audio claim.
"""

import asyncio
import hashlib
import io
import json
import tempfile
import wave
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from .prepare_signal_audio import (
    MAX_CLIP_BYTES,
    TEXTS,
    SignalAudioError,
    collect,
    describe_audio,
    endpoint,
    generate,
)

PCM = b"\x01\x00\x02\x00\x03\x00"
URL = "wss://signal.example/mvp/ws/tts/"


def transport(change=None, hold=False):
    """Functionality: Inject a deterministic TTS peer at the existing WebSocket boundary.
    Inputs: response transform and optional cancellation barrier. Outputs: observed opener/state.
    Logic: echo request UUID into config/completion. Constraints: no network, voice or device use.
    """
    state = SimpleNamespace(sent=[], options=None, closed=False, started=asyncio.Event())

    class Socket:
        """Functionality: Provide explicit protocol responses. Logic: FIFO frames.
        Constraints: synthetic sample bytes test integrity only, not speech quality.
        """

        async def send(self, raw):
            """Functionality: Observe the exact command and construct a fixed response.
            Inputs: JSON command. Outputs: queued protocol frames. Logic: reuse request identity.
            Constraints: no request body leaves test memory.
            """
            request = json.loads(raw)
            state.sent.append(request)
            self.frames = [json.dumps({"type": "audio_config", "request_id": request["request_id"],
                                       "sample_rate": 24000, "format": "pcm_s16le_mono"}),
                           PCM, json.dumps({"type": "complete",
                                            "request_id": request["request_id"],
                                            "audio_bytes": len(PCM)})]
            if change:
                self.frames = change(self.frames, request)
            state.started.set()

        async def recv(self):
            """Functionality: Return one fixture frame or wait for explicit cancellation.
            Inputs: retained FIFO. Outputs: protocol frame. Logic: optional empty future barrier.
            Constraints: no timer substitutes for production protocol deadlines.
            """
            if hold:
                await asyncio.Future()
            return self.frames.pop(0)

    @asynccontextmanager
    async def open_peer(url, **kwargs):
        """Functionality: Observe endpoint options and peer release.
        Inputs: explicit URL/options. Outputs: fake peer. Logic: finally marks ownership release.
        Constraints: no connector fallback or real network request.
        """
        state.options = (url, kwargs)
        try:
            yield Socket()
        finally:
            state.closed = True

    return open_peer, state


class SignalPreparationTests(SimpleTestCase):
    """Functionality: Preserve fixed wording, PCM and safe preparation lifecycle.
    Logic: real validators/writer with explicit adapters. Constraints: no actual TTS/GPU work.
    """

    def test_endpoint_boundaries(self):
        """Functionality: Require verified remote TLS or explicit loopback WS.
        Inputs: canonical and unsafe endpoint variants. Outputs: exact URL/Origin or fixed error.
        Logic: authority/path validation. Constraints: no DNS, authentication or connection.
        """
        self.assertEqual(endpoint(URL), (URL, "https://signal.example"))
        self.assertEqual(endpoint("ws://127.0.0.1:8766/ws/tts/"),
                         ("ws://127.0.0.1:8766/ws/tts/", "http://127.0.0.1:8766"))
        for value in ("ws://remote.example/ws/tts/", URL + "?value=x", URL + "#x",
                      "wss://user:password@signal.example/ws/tts/", "https://signal.example/ws/tts/",
                      "wss://signal.example:0/ws/tts/", "wss://signal.example/private/", None):
            with self.assertRaises(SignalAudioError):
                endpoint(value)

    async def test_exact_command_and_pcm(self):
        """Functionality: Preserve the original five-field TTS command and complete PCM.
        Inputs: each approved state/language. Outputs: exact bytes and verified ownership cleanup.
        Logic: fixture emits matching config and completion. Constraints: no new voice/model field.
        """
        for language, phrases in TEXTS.items():
            for text in phrases.values():
                opener, state = transport()
                self.assertEqual(await collect(URL, language, text, opener=opener), PCM)
                self.assertEqual(set(state.sent[0]),
                                 {"type", "request_id", "text", "language", "rate"})
                self.assertEqual(state.sent[0]["text"], text)
                self.assertEqual(state.sent[0]["rate"], 2)
                self.assertEqual(state.options[1]["origin"], "https://signal.example")
                self.assertIsNone(state.options[1]["proxy"])
                self.assertTrue(state.closed)
        with self.assertRaisesRegex(SignalAudioError, "invalid_signal_phrase"):
            await collect(URL, "zh-CN", "Unapproved text", opener=transport()[0])

    async def test_protocol_failures(self):
        """Functionality: Reject malformed identity/audio/completion without partial output.
        Inputs: explicit damaged response frames. Outputs: safe errors and one request only.
        Logic: independently corrupt each contract. Constraints: no retry or provider body logging.
        """
        variants = [
            (lambda frames, _: frames[1:], "invalid_tts_audio"),
            (lambda frames, _: [frames[0], b"\x01"], "invalid_tts_audio"),
            (lambda frames, _: [frames[0], b"\x00" * len(PCM), frames[2]], "empty_signal_audio"),
            (lambda frames, _: [frames[0], b"\x01" * (MAX_CLIP_BYTES + 2)], "signal_audio_limit"),
            (lambda _frames, _: ["not JSON"], "invalid_tts_protocol"),
            (lambda _frames, _: [json.dumps({"type": "complete", "request_id": "wrong"})],
             "invalid_tts_protocol"),
            (lambda _frames, req: [json.dumps({"type": "error", "request_id": req["request_id"],
                                               "code": "tts_not_configured"})],
             "tts_not_configured"),
            (lambda frames, req: [frames[0], PCM,
                                 json.dumps({"type": "complete", "request_id": req["request_id"],
                                             "audio_bytes": True})], "invalid_tts_protocol"),
        ]
        for change, error in variants:
            opener, state = transport(change)
            with self.assertRaisesRegex(SignalAudioError, error):
                await collect(URL, "zh-CN", TEXTS["zh-CN"]["red"], opener=opener)
            self.assertEqual(len(state.sent), 1)
            self.assertTrue(state.closed)

    async def test_cancellation_closes_peer(self):
        """Functionality: Release an in-progress peer on cancellation without partial audio.
        Inputs: a blocked response and cancellation. Outputs: cancelled task and closed context.
        Logic: wait for request submission first. Constraints: no retry or replacement request.
        """
        opener, state = transport(hold=True)
        task = asyncio.create_task(collect(URL, "zh-CN", TEXTS["zh-CN"]["red"], opener=opener))
        await state.started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(state.closed)
        self.assertEqual(len(state.sent), 1)

    def test_audio_descriptor(self):
        """Functionality: Add a WAV header while preserving exact PCM and separate hashes.
        Inputs: nonzero PCM and invalid samples. Outputs: resource descriptor or explicit failure.
        Logic: read the resulting canonical container. Constraints: no sound or sample edits.
        """
        audio, item = describe_audio("zh-CN", "green", PCM)
        self.assertEqual(len(audio), 44 + len(PCM))
        with wave.open(io.BytesIO(audio), "rb") as reader:
            self.assertEqual((reader.getnchannels(), reader.getsampwidth(), reader.getframerate()),
                             (1, 2, 24000))
            self.assertEqual(reader.readframes(reader.getnframes()), PCM)
        self.assertEqual(item["sha256"], hashlib.sha256(audio).hexdigest())
        self.assertEqual(item["pcm_sha256"], hashlib.sha256(PCM).hexdigest())
        for invalid in (b"", b"\x01", b"\x00\x00", b"\x01" * (MAX_CLIP_BYTES + 2)):
            with self.assertRaises(SignalAudioError):
                describe_audio("zh-CN", "green", invalid)

    async def test_exclusive_generation(self):
        """Functionality: Publish exactly four complete clips and reject an existing output.
        Inputs: owned temporary root and deterministic collector. Outputs: manifest/WAVs once.
        Logic: preserve every file on a second invocation. Constraints: fixture PCM is not shipped.
        """
        calls = []

        async def collector(url, language, text):
            """Functionality: Record explicit preparation. Inputs: fixed corpus. Outputs: test PCM.
            Logic: no I/O. Constraints: only this generation test uses synthetic samples.
            """
            calls.append((url, language, text))
            return PCM

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "new-pack"
            with patch("tools.prepare_signal_audio.PROJECT", root):
                manifest = await generate(URL, target, collector=collector)
                self.assertEqual(len(manifest["clips"]), 4)
                self.assertEqual(len(calls), 4)
                original = (target / "manifest.json").read_bytes()
                with self.assertRaises(FileExistsError):
                    await generate(URL, target, collector=collector)
                self.assertEqual((target / "manifest.json").read_bytes(), original)
                self.assertEqual(len(calls), 4)

    async def test_failure_no_manifest(self):
        """Functionality: Stop after one provider failure and leave no playable partial manifest.
        Inputs: collector failing on the second phrase.
        Outputs: explicit failure, first clip retained.
        Logic: serial requests make cancellation/partial output unambiguous. Constraints: no retry.
        """
        calls = []

        async def collector(_url, _language, _text):
            """Functionality: Fail the second controlled request. Inputs: fixed corpus.
            Outputs: PCM once then error. Logic: explicit failure seam.
            Constraints: no provider call.
            """
            calls.append(1)
            if len(calls) == 2:
                raise SignalAudioError("tts_timeout")
            return PCM

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("tools.prepare_signal_audio.PROJECT", root):
                target = root / "partial"
                with self.assertRaisesRegex(SignalAudioError, "tts_timeout"):
                    await generate(URL, target, collector=collector)
                self.assertFalse((target / "manifest.json").exists())
                self.assertEqual(len(calls), 2)
