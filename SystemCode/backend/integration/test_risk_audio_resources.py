"""Responsibilities: Guard offline risk-audio generation without real TTS or local services.
Implementation: Explicit full-corpus plan fixtures, scripted WebSockets and temporary output folders
verify protocol identity, lossless containers/composition, exclusive writes and failure admission.
Related Modules: tools.prepare_risk_audio; frontend/tools/risk-audio-plan.mjs has separate
formatter tests.
Declaration Index: plan_fixture; FakePeer; FakePeer.__init__; FakePeer.__aenter__;
FakePeer.__aexit__; FakePeer.send; FakePeer.recv; FakeOpener; FakeOpener.__init__;
FakeOpener.__call__; timeout_wait; RiskAudioResourceTests;
RiskAudioResourceTests.test_real_plan_shape_and_invalid_corpora;
RiskAudioResourceTests.test_endpoint_and_resource_path_boundaries;
RiskAudioResourceTests.test_pcm_wav_integrity;
RiskAudioResourceTests.test_complete_wire_protocol_without_retry;
RiskAudioResourceTests.test_invalid_backend_frames_never_become_resources;
RiskAudioResourceTests.test_timeout_and_cancellation_close_the_owned_transport;
RiskAudioResourceTests.test_complete_pack_manifest_and_previews;
RiskAudioResourceTests.test_complete_pack_manifest_and_previews.record_open;
RiskAudioResourceTests.test_manifest_hash_and_record_membership;
RiskAudioResourceTests.test_exclusive_outputs_never_start_synthesis;
RiskAudioResourceTests.test_partial_failure_has_no_manifest_or_retry;
RiskAudioResourceTests.test_generation_cancellation_leaves_no_manifest.
Variable Index: URL/PCM are nonprivate loopback/audio fixtures; no provider credentials are used.
All PCM is arbitrary test data; tests prove neither provider availability nor audible quality.
"""

import asyncio
import copy
import hashlib
import io
import itertools
import json
import wave
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import SimpleTestCase

from tools.prepare_risk_audio import (
    CLASSES,
    DIRECTIONS,
    FORMAT,
    GRADES,
    LANGUAGES,
    MAX_CLIP_BYTES,
    SAMPLE_RATE,
    ResourceError,
    audio_record,
    build_manifest,
    collect_clip,
    generate,
    safe_path,
    validate_plan,
    validate_url,
    wav_bytes,
)

URL = "ws://127.0.0.1:8766/mvp/ws/tts/"
PCM = b"\x00\x00\xff\x7f\x00\x80"


def plan_fixture():
    """Functionality: Build an independent complete structural plan fixture.
    Inputs: None. Outputs: Sixty-six clips and all 144 valid composition identities.
    Logic: Reuse two labeled fragments per composition, covering every declared clip.
    Constraints: Synthetic wording tests structure; production wording is tested separately.
    """
    clips, compositions = [], []
    for language, count in [("zh-CN", 39), ("en-US", 27)]:
        local = [
            {
                "id": f"{language}-fixture-{index}",
                "language": language,
                "text": f"Public fragment {index}. ",
                "kind": "clause",
                "path": f"{language}/clip-{index}.wav",
            }
            for index in range(count)
        ]
        clips.extend(local)
        for index, (label, direction, grade, approach) in enumerate(
            itertools.product(CLASSES, DIRECTIONS, GRADES, [False, True])
        ):
            parts = [local[index % count], local[(index + 1) % count]]
            compositions.append(
                {
                    "id": f"{language}-composition-{index}",
                    "language": language,
                    "label": label,
                    "direction": direction,
                    "risk_level": grade,
                    "approaching": approach,
                    "text": "".join(part["text"] for part in parts),
                    "clip_ids": [part["id"] for part in parts],
                }
            )
    return {
        "schema_version": 1,
        "voice": "Cherry",
        "rate": 2,
        "sample_rate": 24000,
        "format": FORMAT,
        "classes": CLASSES[:],
        "directions": DIRECTIONS[:],
        "clips": clips,
        "compositions": compositions,
    }


class FakePeer:
    """Functionality: Supply independently scripted request-bound TTS frames.
    Logic: Original outgoing UUID becomes the fixture identity; each recv yields to other workers.
    Constraints: No sockets, actual voice synthesis, provider access or audio device.
    """

    def __init__(self, owner, mode):
        """Functionality: Initialize one transport fixture.
        Inputs: Owner/mode. Outputs: Closed counters.
        Logic: Explicit steps and a cancellation event. Constraints: No implicit retries.
        """
        self.owner, self.mode, self.step = owner, mode, 0
        self.request, self.closed = None, False
        self.waiting = asyncio.Event()

    async def __aenter__(self):
        """Functionality: Enter one owned transport. Inputs: None. Outputs: This peer.
        Logic: Count actual concurrent contexts. Constraints: No network activity.
        """
        self.owner.active += 1
        self.owner.maximum = max(self.owner.maximum, self.owner.active)
        return self

    async def __aexit__(self, *_):
        """Functionality: Release one owned transport.
        Inputs: Context outcome. Outputs: Closed state.
        Logic: Account exactly one exit. Constraints: Does not invent completion frames.
        """
        self.closed = True
        self.owner.active -= 1

    async def send(self, raw):
        """Functionality: Capture the exact outbound protocol.
        Inputs: JSON. Outputs: Parsed command.
        Logic: Keep fixture-local data only. Constraints: Production tool never logs the text.
        """
        self.request = json.loads(raw)

    async def recv(self):
        """Functionality: Supply the next explicit fixture frame.
        Inputs: Script mode/step. Outputs: Config, PCM, complete or deliberate corruption.
        Logic: Bind controls to the outgoing UUID. Constraints: Arbitrary PCM is not real speech.
        """
        await asyncio.sleep(0)
        if self.mode == "wait":
            await self.waiting.wait()
        request_id = self.request["request_id"]
        step = self.step
        self.step += 1
        if self.mode == "error":
            return json.dumps(
                {
                    "type": "error",
                    "request_id": request_id,
                    "code": "tts_upstream_error",
                    "detail": "private-provider-body",
                }
            )
        if step == 0:
            if self.mode == "bad_json":
                return "{"
            if self.mode == "audio_first":
                return PCM
            if self.mode == "wrong_id":
                request_id = "another-request"
            return json.dumps(
                {
                    "type": "audio_config",
                    "request_id": request_id,
                    "sample_rate": 22050 if self.mode == "wrong_rate" else SAMPLE_RATE,
                    "format": "mp3" if self.mode == "wrong_format" else FORMAT,
                }
            )
        if step == 1:
            if self.mode == "empty":
                return b""
            if self.mode == "odd":
                return b"x"
            if self.mode == "oversize":
                return b"x" * (MAX_CLIP_BYTES + 2)
            if self.mode == "duplicate_config":
                return json.dumps(
                    {
                        "type": "audio_config",
                        "request_id": request_id,
                        "sample_rate": SAMPLE_RATE,
                        "format": FORMAT,
                    }
                )
            return PCM
        return json.dumps(
            {
                "type": "complete",
                "request_id": request_id,
                "audio_bytes": len(PCM) + 2 if self.mode == "wrong_length" else len(PCM),
            }
        )


class FakeOpener:
    """Functionality: Count explicit transport attempts with bounded fixture concurrency.
    Logic: One context per call, independently selectable modes.
    Constraints: This is not a real WebSocket connector.
    """

    def __init__(self, modes=None):
        """Functionality: Initialize script/counters. Inputs: Optional modes. Outputs: Empty owner.
        Logic: Unspecified calls succeed. Constraints: No automatic retry behavior in fixture.
        """
        self.modes = modes or []
        self.peers, self.options = [], []
        self.active = self.maximum = 0

    def __call__(self, address, **kwargs):
        """Functionality: Create one selected fixture peer. Inputs: Original URL/options.
        Outputs: Context fixture. Logic: Record attempts once. Constraints: No real DNS/socket.
        """
        index = len(self.peers)
        peer = FakePeer(self, self.modes[index] if index < len(self.modes) else "valid")
        self.peers.append(peer)
        self.options.append((address, kwargs))
        return peer


async def timeout_wait(awaitable, _timeout):
    """Functionality: Inject one bounded timeout without leaving an unawaited coroutine.
    Inputs: Receive coroutine/deadline. Outputs: TimeoutError. Logic: Close before raising.
    Constraints: No real waiting or transport retry.
    """
    awaitable.close()
    raise TimeoutError


class RiskAudioResourceTests(SimpleTestCase):
    """Functionality: Validate resource preparation without external services.
    Logic: Independently constructed plans and wire/file evidence exercise production functions.
    Constraints: No actual provider, speaker, browser, model or performance validation.
    """

    def test_real_plan_shape_and_invalid_corpora(self):
        """Functionality: Require full explicit coverage and exact fragment text composition.
        Inputs: Complete fixture and invalid mutations. Outputs: Detached data or fixed error.
        Logic: Corrupt counts, identity, language, parts and transcript separately.
        Constraints: No production formatter implementation is duplicated.
        """
        original = plan_fixture()
        validated = validate_plan(original)
        self.assertEqual(validated, original)
        self.assertIsNot(validated, original)
        invalid = []
        value = copy.deepcopy(original)
        value["clips"].pop()
        invalid.append(value)
        value = copy.deepcopy(original)
        value["compositions"].pop()
        invalid.append(value)
        value = copy.deepcopy(original)
        value["clips"][0]["path"] = "../outside.wav"
        invalid.append(value)
        value = copy.deepcopy(original)
        value["clips"][0]["id"] = value["clips"][1]["id"]
        invalid.append(value)
        value = copy.deepcopy(original)
        value["compositions"][0]["text"] += "changed"
        invalid.append(value)
        value = copy.deepcopy(original)
        value["compositions"][0]["clip_ids"] = ["missing"]
        invalid.append(value)
        value = copy.deepcopy(original)
        value["compositions"][0]["approaching"] = 1
        invalid.append(value)
        value = copy.deepcopy(original)
        value["schema_version"] = True
        invalid.append(value)
        value = copy.deepcopy(original)
        value["rate"] = True
        invalid.append(value)
        value = copy.deepcopy(original)
        value["clips"][0]["text"] = "\ud800"
        invalid.append(value)
        for value in invalid:
            with self.assertRaises(ResourceError) as error:
                validate_plan(value)
            self.assertEqual(error.exception.code, "invalid_plan")

    def test_endpoint_and_resource_path_boundaries(self):
        """Functionality: Refuse external URLs and path traversal before any resource/network work.
        Inputs: Local authority and malformed alternatives. Outputs: Admission or fixed errors.
        Logic: Explicit port/path and portable WAV paths only.
        Constraints: No normalization/fallback.
        """
        self.assertEqual(validate_url(URL), (URL, "http://127.0.0.1:8766"))
        for value in [
            "wss://127.0.0.1:8766/ws/tts/",
            "ws://example.org:8766/ws/tts/",
            "ws://127.0.0.1/ws/tts/",
            URL + "?key=private",
            URL + "#x",
            "ws://u:p@127.0.0.1:8766/ws/tts/",
            "ws://127.0.0.1:8766/ws/agent/",
        ]:
            with self.assertRaises(ResourceError):
                validate_url(value)
        self.assertEqual(safe_path("zh-CN/part.wav"), "zh-CN/part.wav")
        for value in [
            "../part.wav",
            "/part.wav",
            "C:/part.wav",
            "a\\part.wav",
            "a//part.wav",
            "a/../part.wav",
            "con.wav",
            "a/COM1.wav",
            "part.mp3",
        ]:
            with self.assertRaises(ResourceError):
                safe_path(value)

    def test_pcm_wav_integrity(self):
        """Functionality: Preserve every PCM sample while adding only a standard WAV header.
        Inputs: Signed PCM fixture. Outputs: Exact bytes, format, hashes and duration.
        Logic: Parse output with an independent wave reader. Constraints: No audio rendering.
        """
        audio, record = audio_record("sample", "part.wav", PCM)
        with wave.open(io.BytesIO(audio), "rb") as reader:
            self.assertEqual(reader.getparams()[:4], (1, 2, SAMPLE_RATE, len(PCM) // 2))
            self.assertEqual(reader.readframes(reader.getnframes()), PCM)
        self.assertEqual(record["byte_length"], len(audio))
        self.assertEqual(record["samples"], len(PCM) // 2)
        self.assertEqual(record["duration_ms"], 0.125)
        self.assertEqual(record["sha256"], hashlib.sha256(audio).hexdigest())
        self.assertEqual(record["pcm_sha256"], hashlib.sha256(PCM).hexdigest())
        for value in [b"", b"x", "PCM"]:
            with self.assertRaises(ResourceError):
                wav_bytes(value)

    async def test_complete_wire_protocol_without_retry(self):
        """Functionality: Receive one exact existing TTS command/complete stream.
        Inputs: Scripted valid connection. Outputs: Unchanged PCM and one local attempt.
        Logic: Validate UUID-bound format and completion bytes. Constraints: No provider secrets.
        """
        opener = FakeOpener()
        result = await collect_clip(URL, plan_fixture()["clips"][0], opener=opener)
        self.assertEqual(result, PCM)
        self.assertEqual(len(opener.peers), 1)
        self.assertEqual(
            set(opener.peers[0].request), {"type", "request_id", "text", "language", "rate"}
        )
        self.assertEqual(opener.peers[0].request["rate"], 2)
        self.assertTrue(opener.peers[0].closed)
        self.assertEqual(opener.options[0][1]["origin"], "http://127.0.0.1:8766")
        self.assertIsNone(opener.options[0][1]["proxy"])

    async def test_invalid_backend_frames_never_become_resources(self):
        """Functionality: Reject identity, format, byte-count and provider failures.
        Inputs: Independent malformed wire scripts. Outputs: Safe fixed errors, no retry.
        Logic: Exactly one native attempt per case.
        Constraints: Provider details never enter errors.
        """
        for mode in [
            "bad_json",
            "audio_first",
            "wrong_id",
            "wrong_rate",
            "wrong_format",
            "empty",
            "odd",
            "oversize",
            "duplicate_config",
            "wrong_length",
            "error",
        ]:
            opener = FakeOpener([mode])
            with self.assertRaises(ResourceError) as error:
                await collect_clip(URL, plan_fixture()["clips"][0], opener=opener)
            self.assertEqual(len(opener.peers), 1)
            self.assertTrue(opener.peers[0].closed)
            self.assertNotIn("private", str(error.exception))

    async def test_timeout_and_cancellation_close_the_owned_transport(self):
        """Functionality: Stop the exact active synthesis on timeout/caller cancellation.
        Inputs: Explicit receive timeout and blocked peer. Outputs: One closed context/no reconnect.
        Logic: Cancellation propagates after resource cleanup. Constraints: No wall-clock sleep.
        """
        opener = FakeOpener()
        with patch("tools.prepare_risk_audio.asyncio.wait_for", timeout_wait):
            with self.assertRaises(ResourceError) as error:
                await collect_clip(URL, plan_fixture()["clips"][0], opener=opener)
        self.assertEqual(error.exception.code, "tts_timeout")
        self.assertTrue(opener.peers[0].closed)
        blocked = FakeOpener(["wait"])
        task = asyncio.create_task(collect_clip(URL, plan_fixture()["clips"][0], opener=blocked))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(len(blocked.peers), 1)
        self.assertTrue(blocked.peers[0].closed)

    async def test_complete_pack_manifest_and_previews(self):
        """Functionality: Publish complete, lossless resources before the final manifest.
        Inputs: Full corpus, scripted PCM and two worker slots.
        Outputs: Verified 66 clips/two demos.
        Logic: Independently read WAVs, compare samples/hashes and observe exclusive write order.
        Constraints: Temporary resources and fixture PCM do not establish actual speech quality.
        """
        plan, opener = plan_fixture(), FakeOpener()
        opened = []
        original_open = Path.open

        def record_open(path, *args, **kwargs):
            """Functionality: Observe publication order without changing filesystem behavior.
            Inputs: Original open arguments. Outputs: Original file handle.
            Logic: Record exclusive write names only. Constraints: No injected I/O outcome.
            """
            mode = args[0] if args else kwargs.get("mode", "r")
            if mode in {"x", "xb"}:
                opened.append(path.name)
            return original_open(path, *args, **kwargs)

        with TemporaryDirectory() as directory:
            root = Path(directory)
            output, report_path = root / "pack", root / "report.json"
            with (
                patch("tools.prepare_risk_audio.Path.open", record_open),
                redirect_stdout(io.StringIO()),
            ):
                report = await generate(
                    plan, URL, output, report_path, concurrency=2, opener=opener
                )
            self.assertTrue(report["complete"])
            self.assertEqual(report["completed"], 66)
            self.assertEqual(report["unstarted"], 0)
            self.assertEqual(report["failures"], [])
            self.assertEqual(len(opener.peers), 66)
            self.assertEqual(opener.maximum, 2)
            self.assertEqual(opener.active, 0)
            self.assertTrue(all(peer.closed for peer in opener.peers))
            self.assertEqual(opened[-1], "manifest.json")
            self.assertEqual(json.loads(report_path.read_text(encoding="utf-8")), report)
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["compositions"], plan["compositions"])
            self.assertEqual(len(manifest["clips"]), 66)
            self.assertEqual(len(manifest["demos"]), len(LANGUAGES))
            self.assertEqual(len(list(output.rglob("*.wav"))), 68)
            source_pcm = {}
            for record in manifest["clips"] + manifest["demos"]:
                audio = (output / record["path"]).read_bytes()
                with wave.open(io.BytesIO(audio), "rb") as reader:
                    self.assertEqual(reader.getnchannels(), 1)
                    self.assertEqual(reader.getsampwidth(), 2)
                    self.assertEqual(reader.getframerate(), SAMPLE_RATE)
                    pcm = reader.readframes(reader.getnframes())
                self.assertEqual(record["byte_length"], len(audio))
                self.assertEqual(record["pcm_byte_length"], len(pcm))
                self.assertEqual(record["samples"], len(pcm) // 2)
                self.assertEqual(record["sha256"], hashlib.sha256(audio).hexdigest())
                self.assertEqual(record["pcm_sha256"], hashlib.sha256(pcm).hexdigest())
                source_pcm[record["id"]] = pcm
            for clip in manifest["clips"]:
                self.assertEqual(source_pcm[clip["id"]], PCM)
            for demo in manifest["demos"]:
                self.assertEqual(
                    source_pcm[demo["id"]], b"".join(source_pcm[key] for key in demo["clip_ids"])
                )
            digest = manifest.pop("pack_sha256")
            canonical = json.dumps(
                manifest, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            ).encode("utf-8")
            self.assertEqual(digest, hashlib.sha256(canonical).hexdigest())

    def test_manifest_hash_and_record_membership(self):
        """Functionality: Bind manifest membership and its digest to completed resource records.
        Inputs: Complete/reordered records and incomplete/duplicate alternatives.
        Outputs: Fixed hash. Logic: Preserve plan order and reject missing/duplicate records.
        Constraints: Metadata fixture hashes are not a provider or listening assertion.
        """
        plan = plan_fixture()
        records = [audio_record(clip["id"], clip["path"], PCM)[1] for clip in plan["clips"]]
        demos = [
            {**audio_record("demo_" + language, f"demos/{language}.wav", PCM * 2)[1]}
            for language in LANGUAGES
        ]
        manifest = build_manifest(plan, records, demos)
        self.assertEqual(manifest, build_manifest(plan, list(reversed(records)), demos))
        self.assertEqual(
            [item["id"] for item in manifest["clips"]], [c["id"] for c in plan["clips"]]
        )
        for incomplete in [records[:-1], records + [records[0]], []]:
            with self.assertRaises(ResourceError) as error:
                build_manifest(plan, incomplete, demos)
            self.assertEqual(error.exception.code, "resource_integrity_failed")

    async def test_exclusive_outputs_never_start_synthesis(self):
        """Functionality: Preserve existing files and reject unsafe report ownership before TTS.
        Inputs: Existing output/report paths and a report inside the pack. Outputs: Refusal/no call.
        Logic: Inspect unchanged sentinel bytes and absence of new resources.
        Constraints: Tests never delete or overwrite a user-owned directory.
        """
        plan, opener = plan_fixture(), FakeOpener()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "existing"
            output.mkdir()
            marker = output / "sentinel.bin"
            marker.write_bytes(b"keep existing resource")
            with self.assertRaises(ResourceError) as error:
                await generate(plan, URL, output, root / "unused-report.json", opener=opener)
            self.assertEqual(error.exception.code, "output_exists")
            self.assertEqual(marker.read_bytes(), b"keep existing resource")
            self.assertFalse((root / "unused-report.json").exists())
            report_path = root / "existing-report.json"
            report_path.write_bytes(b"keep existing report")
            with self.assertRaises(ResourceError) as error:
                await generate(plan, URL, root / "new-pack", report_path, opener=opener)
            self.assertEqual(error.exception.code, "report_exists")
            self.assertEqual(report_path.read_bytes(), b"keep existing report")
            self.assertFalse((root / "new-pack").exists())
            with self.assertRaises(ResourceError) as error:
                await generate(
                    plan, URL, root / "unsafe-pack", root / "unsafe-pack/report.json", opener=opener
                )
            self.assertEqual(error.exception.code, "invalid_output")
            self.assertFalse((root / "unsafe-pack").exists())
        self.assertEqual(opener.peers, [])

    async def test_partial_failure_has_no_manifest_or_retry(self):
        """Functionality: Preserve completed clips without publishing a failed pack as complete.
        Inputs: First-worker provider error and one already admitted successful peer.
        Outputs: One preserved WAV, safe report, no manifest or later synthesis calls.
        Logic: The second admitted worker may finish; the remaining 64 clips never start.
        Constraints: Scripted outcomes prove failure admission and cleanup, not real TTS.
        """
        plan, opener = plan_fixture(), FakeOpener(["error", "valid"])
        with TemporaryDirectory() as directory:
            root = Path(directory)
            output, report_path = root / "pack", root / "report.json"
            with redirect_stdout(io.StringIO()):
                report = await generate(
                    plan, URL, output, report_path, concurrency=2, opener=opener
                )
            self.assertFalse(report["complete"])
            self.assertEqual(len(opener.peers), 2)
            self.assertEqual(report["completed"], 1)
            self.assertEqual(report["unstarted"], 64)
            self.assertEqual(report["failures"][0]["code"], "tts_upstream_error")
            self.assertFalse((output / "manifest.json").exists())
            self.assertFalse((output / "demos").exists())
            self.assertEqual(len(list(output.rglob("*.wav"))), 1)
            self.assertTrue(all(peer.closed for peer in opener.peers))
            self.assertEqual(opener.active, 0)
            saved = report_path.read_text(encoding="utf-8")
            self.assertEqual(json.loads(saved), report)
            self.assertNotIn("private-provider-body", saved)

    async def test_generation_cancellation_leaves_no_manifest(self):
        """Functionality: Bound caller-cancelled generation and preserve its failure evidence.
        Inputs: Two blocked transport fixtures. Outputs: Closed peers and incomplete report.
        Logic: Cancel the aggregate operation after both peers enter; require context cleanup.
        Constraints: No real sleep, connection, audio asset or automatic retry is involved.
        """
        opener = FakeOpener(["wait", "wait"])
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with redirect_stdout(io.StringIO()):
                task = asyncio.create_task(
                    generate(
                        plan_fixture(), URL, root / "pack", root / "report.json", opener=opener
                    )
                )
                for _ in range(20):
                    if len(opener.peers) == 2:
                        break
                    await asyncio.sleep(0)
                self.assertEqual(len(opener.peers), 2)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assertTrue(all(peer.closed for peer in opener.peers))
            self.assertEqual(opener.active, 0)
            self.assertFalse((root / "pack/manifest.json").exists())
            report = json.loads((root / "report.json").read_text(encoding="utf-8"))
            self.assertFalse(report["complete"])
            self.assertIn({"code": "generation_cancelled"}, report["failures"])
