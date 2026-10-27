"""Responsibilities: Verify isolated local transport/lifecycle without model substitutes in
production.
Implementation: Actual packet parsers and ASGI communicators; explicit synthetic runtime/peer
fixtures and isolated video/navigation speech asset delivery.
Related Modules: integration.protocol/services/inference.services; realtime.protocol;
core.test_frames.
Declaration Index: TrackerFixture; TrackerFixture.__init__; TrackerFixture.reset;
DetectorFixture; DetectorFixture.__init__; DetectorFixture.predict; fixture_runtime; socket_scope;
open_worker; terminal; PeerFixture; PeerFixture.__init__; PeerFixture.recv; PeerFixture.send;
PeerFixture.context; ProtocolTests; ProtocolTests.test_source_clock_and_byte_boundaries;
ProtocolTests.test_identity_and_destination; ProtocolTests.test_explicit_distance_identity;
RuntimeTests;
RuntimeTests.test_load_once_reset_and_source;
RuntimeTests.test_failed_initialization_latches; RuntimeTests.test_status_does_not_load;
WorkerTests; WorkerTests.test_ordered_results_and_second_session;
WorkerTests.test_disconnect_holds_capacity_until_native_completion;
WorkerTests.test_stop_waits_and_excess_frames_fail; WorkerTests.test_geometry_and_source_rejections;
WorkerTests.test_failure_does_not_disclose_exception; WorkerTests.test_nonlocal_origin_rejected;
WorkerTests.test_deadline_drains_native_work; WorkerTests.test_cancel_drains_native_work;
GatewayTests; GatewayTests.test_relay_identity_and_stop; GatewayTests.test_provider_corruption;
GatewayTests.test_origin_capacity_and_invalid_source;
GatewayTests.test_stop_suppressed_result_counts; GatewayTests.test_early_result_confirms_send;
GatewayTests.test_invalid_terminal_counts; GatewayTests.test_repeated_cancel_preserves_cleanup;
WrapperTests; WrapperTests.test_delegation_disabled_routes_and_status;
WrapperTests.test_page_injection_and_asset_allowlist; WrapperTests.test_video_entry_isolated;
WrapperTests.test_navigation_entry_isolated;
WrapperTests.test_inference_health_is_lazy.
Variable Index: ORIGIN — explicit local browser origin; MANIFEST — valid original identity fixture;
factory — Mock callable, never automatic fallback; gate/started — native-work cancellation controls.
"""

import asyncio
import hashlib
import json
import struct
import threading
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock, patch

from asgiref.testing import ApplicationCommunicator
from django.test import SimpleTestCase

from core.detector import MAX_FRAME_BYTES, decode_frame
from core.test_frames import jpeg
from realtime.protocol import pack_result, unpack_result

from .errors import IntegrationError
from .inference.services import InferenceRuntime, InferenceWorker
from .protocol import (
    MAX_INFLIGHT,
    REQUEST_HEADER,
    inference_url,
    model_contract,
    pack_request,
    read_manifest,
    unpack_request,
    validate_contract,
    validate_result,
)
from .services import IntegrationApplication, LocalGateway

ORIGIN = "http://127.0.0.1:8766"
MANIFEST = read_manifest()


class TrackerFixture:
    """Functionality: Observe explicit sequence reset independently of real BoT-SORT.
    Logic: count reset calls. Constraints: synthetic state does not prove algorithm behavior.
    """

    def __init__(self):
        """Functionality: Initialize reset observations.
        Inputs: None. Outputs: zero count. Logic: no I/O. Constraints: test fixture only.
        """
        self.resets = 0

    def reset(self):
        """Functionality: Record an explicit reset call.
        Inputs: None. Outputs: incremented count. Logic: no algorithm. Constraints: test only.
        """
        self.resets += 1


class DetectorFixture:
    """Functionality: Supply explicit synthetic complete-pipeline metadata for transport tests.
    Logic: return original JPEG and source observations. Constraints: no real model or GPU claim.
    """

    def __init__(self, gate=None, started=None, failure=None):
        """Functionality: Initialize injectable native delay/failure and observed state.
        Inputs: events/exception. Outputs: fake detector. Logic: no model work.
        Constraints: tests inject this explicitly; production has no substitute path.
        """
        self.depth_estimator, self.object_tracker = object(), TrackerFixture()
        self.calls, self.threads = [], []
        self.gate, self.started, self.failure = gate, started, failure

    def predict(self, raw, *, timestamp):
        """Functionality: Provide deterministic frames while observing real executor completion.
        Inputs: JPEG/source seconds. Outputs: valid metadata/JPEG or explicit synthetic failure.
        Logic: optional native event gate; actual JPEG dimensions/fingerprint; complete pipeline
        fields.
        Constraints: synthetic timings/zero boxes are protocol fixtures, not inference evidence.
        """
        self.calls.append(timestamp)
        self.threads.append(threading.get_ident())
        if self.started is not None:
            self.started.set()
        if self.gate is not None and not self.gate.wait(3):
            raise TimeoutError("Synthetic native gate timed out")
        if self.failure is not None:
            raise self.failure
        width, height = decode_frame(raw).size
        metadata = {
            **model_contract("a" * 32, MANIFEST, True),
            "schema_version": 1,
            "width": width,
            "height": height,
            "count": 0,
            "boxes": [],
            "inference_ms": 1.0,
            "server_ms": 1.0,
            "graph_setup_ms": 0.0,
            "jpeg_sha256": hashlib.sha256(raw).hexdigest(),
            "tracking_status": "active",
            "tracking_epoch": self.object_tracker.resets,
            "gmc_method": "sparseOptFlow",
            "with_reid": False,
            "tracking_ms": 0.1,
            "tracking_timestamp_s": timestamp,
            "depth_ms": 0.1,
            "risk_ms": 0.1,
        }
        metadata.pop("kind")
        return metadata, raw


def fixture_runtime(detector=None, factory=None):
    """Functionality: Build an explicit synthetic runtime without touching local model assets.
    Inputs: fake detector or custom mock factory. Outputs: runtime/detector/factory.
    Logic: valid manifest and recorded factory calls. Constraints: no real CUDA or downloads.
    """
    detector = detector if detector is not None else DetectorFixture()
    factory = (
        factory
        if factory is not None
        else Mock(
            return_value=(
                detector,
                {"detection_sha256": MANIFEST["source_sha256"], "depth_sha256": "b" * 64},
            )
        )
    )
    return InferenceRuntime(factory=factory, manifest_reader=lambda: MANIFEST), detector, factory


def socket_scope(path="/ws/infer/", origin=ORIGIN):
    """Functionality: Describe an explicit loopback socket with matching local browser origin.
    Inputs: route/origin. Outputs: ASGI scope. Logic: fixed peer/server addresses.
    Constraints: in-memory fixture does not test proxy/TLS/browser camera permissions.
    """
    return {
        "type": "websocket",
        "path": path,
        "query_string": b"",
        "client": ("127.0.0.1", 1234),
        "server": ("127.0.0.1", 8767),
        "headers": [(b"origin", origin.encode()), (b"host", b"127.0.0.1:8766")],
    }


async def open_worker(app, scope=None):
    """Functionality: Complete the real ASGI connect/hello lifecycle.
    Inputs: callable/optional scope. Outputs: communicator/parsed hello.
    Logic: assert accept then parse session. Constraints: caller closes and awaits communicator.
    """
    peer = ApplicationCommunicator(app, scope or socket_scope())
    await peer.send_input({"type": "websocket.connect"})
    assert (await peer.receive_output())["type"] == "websocket.accept"
    hello = json.loads((await peer.receive_output())["text"])
    return peer, hello


async def terminal(peer, kind="ended"):
    """Functionality: Observe public terminal and actual ASGI task completion.
    Inputs: communicator/expected kind. Outputs: terminal dictionary.
    Logic: consume text then close and wait. Constraints: no fabricated native completion.
    """
    message = json.loads((await peer.receive_output())["text"])
    assert message["kind"] == kind
    assert (await peer.receive_output())["type"] == "websocket.close"
    await peer.wait(timeout=2)
    return message


class PeerFixture:
    """Functionality: Emulate an explicitly selected provider at the real packet boundary.
    Logic: receive source bytes, return source-correlated PRS1, stop terminal.
    Constraints: not an actual network/GPU fixture and never installed as a production fallback.
    """

    def __init__(
        self,
        corrupt=False,
        *,
        result_gate=None,
        send_gate=None,
        terminal_gate=None,
        terminal_override=None,
    ):
        """Functionality: Prepare a valid hello and optional tamper injection.
        Inputs: corruption flag and explicit transport/lifecycle gates or terminal override.
        Outputs: queued provider handshake plus observable result/stop events.
        Logic: use original manifest. Constraints: private endpoints are not returned in hello.
        """
        self.outgoing, self.sent, self.count = asyncio.Queue(), [], 0
        self.contract = model_contract("a" * 32, MANIFEST)
        self.outgoing.put_nowait(json.dumps(self.contract))
        self.corrupt = corrupt
        self.result_gate, self.send_gate, self.terminal_gate = result_gate, send_gate, terminal_gate
        self.terminal_override = terminal_override
        self.result_ready, self.stop_seen = asyncio.Event(), asyncio.Event()

    async def recv(self):
        """Functionality: Supply queued provider packets under async backpressure.
        Inputs: outgoing queue. Outputs: next packet. Logic: await queue.
        Constraints: fixtures are bounded by test inputs, no network measurements.
        """
        packet = await self.outgoing.get()
        if isinstance(packet, bytes) and self.result_gate is not None:
            await self.result_gate.wait()
        return packet

    async def send(self, packet):
        """Functionality: Exercise request parsing and inject source-matched results/terminal.
        Inputs: PRF2 or stop. Outputs: queued PRS1/ended.
        Logic: count exact requests; corruption changes capture time, leaving JPEG hash valid.
        Constraints: deliberate injection proves correlation validation, not detection accuracy.
        """
        self.sent.append(packet)
        if isinstance(packet, str):
            self.stop_seen.set()
            if self.terminal_gate is not None:
                await self.terminal_gate.wait()
            self.outgoing.put_nowait(
                json.dumps(
                    self.terminal_override
                    if (self.terminal_override is not None)
                    else {
                        "kind": "ended",
                        "frames": self.count,
                        "received": self.count,
                        "delivered": self.count,
                    }
                )
            )
        else:
            timestamp, raw = unpack_request(packet, self.count)
            metadata, rendered = DetectorFixture().predict(raw, timestamp=timestamp)
            metadata.update(
                index=self.count,
                session_id=self.contract["session_id"],
                capture_time_s=timestamp + int(self.corrupt),
                timestamp_source="browser_capture",
            )
            self.outgoing.put_nowait(pack_result(metadata, rendered))
            self.count += 1
            self.result_ready.set()
            if self.send_gate is not None:
                await self.send_gate.wait()

    @asynccontextmanager
    async def context(self, url, **kwargs):
        """Functionality: Inject this provider in place of the selected transport for one test.
        Inputs: ignored URL/options. Outputs: peer context. Logic: no I/O.
        Constraints: only explicit constructor test seam uses this method.
        """
        yield self


class ProtocolTests(SimpleTestCase):
    """Functionality: Verify versioned source and destination contracts.
    Logic: actual parser boundaries. Constraints: no service or model execution.
    """

    def test_source_clock_and_byte_boundaries(self):
        """Functionality: Reject nonfinite/nonincreasing clocks, foreign protocols and excess bytes.
        Inputs: actual header/JPEG fixtures. Outputs: stable typed failures.
        Logic: construct invalid packets independently. Constraints: no image transformations.
        """
        raw = jpeg()
        self.assertEqual(REQUEST_HEADER.size, 16)
        self.assertEqual(unpack_request(pack_request(0, 2.25, raw), 0), (2.25, raw))
        for timestamp in (-1, float("nan"), float("inf"), 2.0):
            with self.subTest(timestamp=timestamp), self.assertRaises(IntegrationError):
                unpack_request(struct.pack("!4sId", b"PRF2", 0, timestamp) + raw, 0, 2.0)
        for packet in (
            struct.pack("!4sId", b"PRF1", 0, 3.0) + raw,
            pack_request(1, 3.0, raw),
            b"x" * (MAX_FRAME_BYTES + 17),
        ):
            with self.assertRaises(IntegrationError):
                unpack_request(packet, 0)

    def test_identity_and_destination(self):
        """Functionality: Reject borrowed quality evidence and unsafe worker destinations.
        Inputs: real contract and explicit environment overrides. Outputs: acceptance/rejection.
        Logic: test credentials/query/remote plaintext boundaries. Constraints: no network call.
        """
        hello = model_contract("a" * 32, MANIFEST)
        self.assertIs(validate_contract(hello), hello)
        self.assertNotIn("validation", hello)
        with self.assertRaises(IntegrationError):
            validate_contract({**hello, "validation": {"baseline_ap": 0.9}})
        for url in (
            "ws://example.com/ws/infer/",
            "ws://user:pass@127.0.0.1:8767/ws/infer/",
            "ws://127.0.0.1:8767/ws/infer/?token=secret",
        ):
            with (
                patch.dict("os.environ", {"LOCAL_INFERENCE_URL": url}),
                self.assertRaises(IntegrationError),
            ):
                inference_url()

    def test_explicit_distance_identity(self):
        """Functionality: Require one canonical distance identity in every session and result.
        Inputs: all supported strategies and independently omitted identity fields.
        Outputs: exact mode-specific intervals or typed rejection.
        Logic: validate producer and correlated result records at their shared boundary.
        Constraints: synthetic metadata verifies protocol rules, not distance accuracy.
        """
        for mode, interval in (("depth", 1), ("geometry", 0), ("temporal_depth", 3)):
            hello = model_contract("a" * 32, MANIFEST, distance_mode=mode)
            self.assertEqual((hello["distance_mode"], hello["distance_units"],
                              hello["depth_interval"]), (mode, "m", interval))
            self.assertIs(validate_contract(hello), hello)
            result = {**hello, "index": 0, "capture_time_s": 1.0,
                      "timestamp_source": "browser_capture"}
            validate_result(result, hello, 0, 1.0)
            for key in ("distance_mode", "distance_units", "depth_interval"):
                missing_hello = dict(hello)
                missing_hello.pop(key)
                with self.subTest(mode=mode, missing=key), self.assertRaises(IntegrationError):
                    validate_contract(missing_hello)
                missing_result = dict(result)
                missing_result.pop(key)
                with self.subTest(mode=mode, result_missing=key):
                    with self.assertRaises(IntegrationError):
                        validate_result(missing_result, hello, 0, 1.0)


class RuntimeTests(SimpleTestCase):
    """Functionality: Verify the unchanged algorithm ownership/lifecycle adapter.
    Logic: actual executor and explicit synthetic factory. Constraints: no real CUDA evidence.
    """

    async def test_load_once_reset_and_source(self):
        """Functionality: Keep models/thread resident while resetting only sequence state.
        Inputs: two session identities/source times. Outputs: one load and one reset.
        Logic: actual executor calls observe same thread and exact forwarded timestamp.
        Constraints: fake algorithm asserts adaptation only, not physical speed or tracking.
        """
        runtime, detector, factory = fixture_runtime()
        try:
            await runtime.submit(jpeg(), 10.125, "a" * 32, 0)
            metadata, _ = await runtime.submit(jpeg(), 0.125, "b" * 32, 0)
            self.assertEqual(factory.call_count, 1)
            self.assertEqual(detector.object_tracker.resets, 1)
            self.assertEqual(detector.calls, [10.125, 0.125])
            self.assertEqual(len(set(detector.threads)), 1)
            self.assertNotEqual(detector.threads[0], threading.get_ident())
            self.assertEqual(metadata["capture_time_s"], 0.125)
            self.assertTrue(runtime.status()["loaded"])
        finally:
            await runtime.close()

    async def test_failed_initialization_latches(self):
        """Functionality: Prevent repeated process-global initialization after native load failure.
        Inputs: failing explicit factory/two sessions. Outputs: one attempt and safe failed status.
        Logic: subsequent explicit session observes latch until operator restart.
        Constraints: no silent retry, alternative model or thread-setting workaround.
        """
        factory = Mock(side_effect=RuntimeError("private dependency contents"))
        runtime, _, _ = fixture_runtime(factory=factory)
        try:
            for identity in ("a" * 32, "b" * 32):
                with self.assertRaises(IntegrationError):
                    await runtime.submit(jpeg(), 1.0, identity, 0)
            self.assertEqual(factory.call_count, 1)
            self.assertFalse(runtime.status()["ready"])
            self.assertNotIn("private", json.dumps(runtime.status()))
        finally:
            await runtime.close()

    async def test_status_does_not_load(self):
        """Functionality: Distinguish admission health from actual loaded/inference readiness.
        Inputs: fresh runtime status/contract. Outputs: unloaded true-admission and zero loads.
        Logic: query public lightweight surfaces. Constraints: no Torch import or GPU setup needed.
        """
        runtime, _, factory = fixture_runtime()
        try:
            self.assertFalse(runtime.status()["loaded"])
            self.assertFalse(runtime.status()["ready"])
            self.assertTrue(runtime.status()["accept_available"])
            self.assertIsNone(runtime.status()["last_inference"])
            self.assertFalse(runtime.contract("a" * 32)["loaded"])
            factory.assert_not_called()
        finally:
            await runtime.close()


class WorkerTests(SimpleTestCase):
    """Functionality: Verify ordered sessions and uncancellable native work capacity.
    Logic: actual ASGI lifecycle plus deterministic native gates. Constraints: synthetic GPU only.
    """

    async def test_ordered_results_and_second_session(self):
        """Functionality: Preserve complete timestamped frames and reset between sessions.
        Inputs: two sequential ASGI sessions. Outputs: matched fingerprints/identity and one load.
        Logic: drive actual parser/executor/packer. Constraints: transport proof, not GPU inference.
        """
        runtime, detector, factory = fixture_runtime()
        worker = InferenceWorker(runtime)
        try:
            for _ in range(2):
                peer, hello = await open_worker(worker)
                validate_contract(hello)
                for index in range(2):
                    await peer.send_input(
                        {
                            "type": "websocket.receive",
                            "bytes": pack_request(index, 0.125 + index, jpeg()),
                        }
                    )
                    metadata, raw = unpack_result((await peer.receive_output())["bytes"], index)
                    self.assertEqual(metadata["session_id"], hello["session_id"])
                    self.assertEqual(metadata["capture_time_s"], 0.125 + index)
                    self.assertEqual(raw, jpeg())
                await peer.send_input({"type": "websocket.receive", "text": '{"kind":"stop"}'})
                self.assertEqual((await terminal(peer))["frames"], 2)
            self.assertEqual(factory.call_count, 1)
            self.assertEqual(detector.object_tracker.resets, 1)
        finally:
            await runtime.close()

    async def test_disconnect_holds_capacity_until_native_completion(self):
        """Functionality: Prevent new admission while disconnected GPU work still runs.
        Inputs: native event gate/disconnect/second connection. Outputs: busy then actual release.
        Logic: assert future remains active without ASGI response; release gate before final wait.
        Constraints: thread cancellation substitute is explicit and no wall-time GPU claim is made.
        """
        gate, started = threading.Event(), threading.Event()
        runtime, _, _ = fixture_runtime(DetectorFixture(gate, started))
        worker = InferenceWorker(runtime)
        try:
            peer, _ = await open_worker(worker)
            await peer.send_input(
                {"type": "websocket.receive", "bytes": pack_request(0, 1, jpeg())}
            )
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            await peer.send_input({"type": "websocket.disconnect", "code": 1000})
            await asyncio.sleep(0.01)
            self.assertTrue(runtime.busy)
            second, error = await open_worker(worker)
            self.assertEqual(error["code"], "local_busy")
            await second.receive_output()
            await second.wait()
            gate.set()
            await peer.wait(timeout=2)
            self.assertFalse(runtime.busy)
            self.assertTrue(await peer.receive_nothing())
        finally:
            gate.set()
            await runtime.close()

    async def test_stop_waits_and_excess_frames_fail(self):
        """Functionality: Hold capacity on stop and reject a thirteenth outstanding frame.
        Inputs: native gate and separate stop/overflow sequences. Outputs: no early
        terminal/release.
        Logic: queue bound includes working frame; native completion precedes ending/error.
        Constraints: tests do not measure throughput or model correctness.
        """
        for overflow in (False, True):
            gate, started = threading.Event(), threading.Event()
            runtime, _, _ = fixture_runtime(DetectorFixture(gate, started))
            worker = InferenceWorker(runtime)
            try:
                peer, _ = await open_worker(worker)
                count = MAX_INFLIGHT + 1 if overflow else 1
                for index in range(count):
                    await peer.send_input(
                        {
                            "type": "websocket.receive",
                            "bytes": pack_request(index, index + 1, jpeg()),
                        }
                    )
                    if index == 0:
                        self.assertTrue(await asyncio.to_thread(started.wait, 1))
                if not overflow:
                    await peer.send_input({"type": "websocket.receive", "text": '{"kind":"stop"}'})
                await asyncio.sleep(0.01)
                self.assertTrue(runtime.busy)
                self.assertTrue(await peer.receive_nothing(timeout=0.02))
                gate.set()
                ending = await terminal(peer, "error" if overflow else "ended")
                if overflow:
                    self.assertEqual(ending["code"], "local_capacity")
                self.assertFalse(runtime.busy)
            finally:
                gate.set()
                await runtime.close()

    async def test_geometry_and_source_rejections(self):
        """Functionality: Reject resized same-stream input, repeated time and reordered sequence.
        Inputs: one completed frame then independent invalid packets. Outputs: explicit errors.
        Logic: actual decoder protects tracker before geometry change reaches the algorithm.
        Constraints: no automatic reset/resizing or source-clock repair.
        """
        for invalid, code in (
            (pack_request(1, 2, jpeg((100, 100))), "frame_geometry_changed"),
            (pack_request(1, 1, jpeg()), "invalid_timestamp"),
            (pack_request(2, 2, jpeg()), "invalid_sequence"),
        ):
            runtime, detector, _ = fixture_runtime()
            try:
                peer, _ = await open_worker(InferenceWorker(runtime))
                await peer.send_input(
                    {"type": "websocket.receive", "bytes": pack_request(0, 1, jpeg())}
                )
                await peer.receive_output()
                await peer.send_input({"type": "websocket.receive", "bytes": invalid})
                self.assertEqual((await terminal(peer, "error"))["code"], code)
                self.assertEqual(detector.calls, [1])
            finally:
                await runtime.close()

    async def test_failure_does_not_disclose_exception(self):
        """Functionality: Return safe inference failure without raw dependency/media text.
        Inputs: deliberately sensitive synthetic exception. Outputs: generic code/detail.
        Logic: failure crosses actual executor boundary. Constraints: no real GPU failure injection.
        """
        runtime, _, _ = fixture_runtime(DetectorFixture(failure=ValueError("secret frame payload")))
        try:
            peer, _ = await open_worker(InferenceWorker(runtime))
            await peer.send_input(
                {"type": "websocket.receive", "bytes": pack_request(0, 1, jpeg())}
            )
            error = await terminal(peer, "error")
            self.assertEqual(error["code"], "local_inference_failed")
            self.assertNotIn("secret", json.dumps(error))
        finally:
            await runtime.close()

    async def test_nonlocal_origin_rejected(self):
        """Functionality: Refuse remote peers, foreign origins and query capabilities before load.
        Inputs: separate invalid scopes. Outputs: 1008 close and zero factory calls.
        Logic: exercise real worker admission. Constraints: TLS/firewall are deployment concerns.
        """
        runtime, _, factory = fixture_runtime()
        try:
            for changed in (
                {"client": ("192.0.2.1", 1234)},
                {"query_string": b"token=x"},
                {"headers": [(b"origin", b"https://foreign.example")]},
            ):
                peer = ApplicationCommunicator(
                    InferenceWorker(runtime), {**socket_scope(), **changed}
                )
                await peer.send_input({"type": "websocket.connect"})
                self.assertEqual((await peer.receive_output())["code"], 1008)
                await peer.wait()
            factory.assert_not_called()
        finally:
            await runtime.close()

    async def test_deadline_drains_native_work(self):
        """Functionality: Preserve capacity after session expiry until native completion.
        Inputs: short explicit transport deadline and gated synthetic prediction.
        Outputs: no early release or result delivery, followed by normal counted end.
        Logic: only transport timer is patched; algorithm settings remain untouched.
        Constraints: synthetic test does not assert model latency or safe forced GPU cancellation.
        """
        gate, started = threading.Event(), threading.Event()
        runtime, _, _ = fixture_runtime(DetectorFixture(gate, started))
        try:
            with patch("integration.inference.services.SESSION_SECONDS", 0.02):
                peer, _ = await open_worker(InferenceWorker(runtime))
                await peer.send_input(
                    {"type": "websocket.receive", "bytes": pack_request(0, 1, jpeg())}
                )
                self.assertTrue(await asyncio.to_thread(started.wait, 1))
                await asyncio.sleep(0.05)
                self.assertTrue(runtime.busy)
                self.assertTrue(await peer.receive_nothing(timeout=0.01))
                gate.set()
                self.assertEqual((await terminal(peer))["frames"], 0)
                self.assertFalse(runtime.busy)
        finally:
            gate.set()
            await runtime.close()

    async def test_cancel_drains_native_work(self):
        """Functionality: Keep native capacity through repeated ASGI task cancellation.
        Inputs: active native gate and two cancellations. Outputs: busy until actual completion.
        Logic: second cancellation occurs during drain and must not bypass finally/release.
        Constraints: no thread termination, dependency exception repair or production retries.
        """
        gate, started = threading.Event(), threading.Event()
        runtime, _, _ = fixture_runtime(DetectorFixture(gate, started))
        try:
            peer, _ = await open_worker(InferenceWorker(runtime))
            await peer.send_input(
                {"type": "websocket.receive", "bytes": pack_request(0, 1, jpeg())}
            )
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            peer.future.cancel()
            await asyncio.sleep(0.01)
            peer.future.cancel()
            await asyncio.sleep(0.01)
            self.assertTrue(runtime.busy)
            self.assertFalse(peer.future.done())
            gate.set()
            await peer.wait(timeout=2)
            self.assertFalse(runtime.busy)
            self.assertTrue(peer.future.cancelled())
        finally:
            gate.set()
            await runtime.close()


class GatewayTests(SimpleTestCase):
    """Functionality: Validate the middleware source/result contract without algorithm ownership.
    Logic: explicit provider peer. Constraints: no remote calls or GPU inference evidence.
    """

    async def test_relay_identity_and_stop(self):
        """Functionality: Forward exact packets and worker identity then explicitly stop upstream.
        Inputs: real PRF2/PRS1 source and provider fixture. Outputs: unchanged bytes/counts/control.
        Logic: complete ASGI gateway lifecycle. Constraints: mock transport, not network validation.
        """
        provider = PeerFixture()
        peer, hello = await open_worker(
            LocalGateway(opener=provider.context), socket_scope("/ws/detect/local/")
        )
        self.assertEqual(hello["session_id"], provider.contract["session_id"])
        raw = pack_request(0, 5.125, jpeg())
        await peer.send_input({"type": "websocket.receive", "bytes": raw})
        metadata, image = unpack_result((await peer.receive_output())["bytes"], 0)
        self.assertEqual(metadata["capture_time_s"], 5.125)
        self.assertEqual(image, jpeg())
        await peer.send_input({"type": "websocket.receive", "text": '{"kind":"stop"}'})
        self.assertEqual((await terminal(peer))["frames"], 1)
        self.assertEqual(provider.sent, [raw, '{"kind":"stop"}'])

    async def test_provider_corruption(self):
        """Functionality: Refuse source-time tampering even when JPEG fingerprint remains valid.
        Inputs: corrupted explicit provider. Outputs: local_result_invalid before frame delivery.
        Logic: source correlation is separate from hash validation.
        Constraints: no tamper repair, substituted result or implicit retry.
        """
        provider = PeerFixture(corrupt=True)
        peer, _ = await open_worker(
            LocalGateway(opener=provider.context), socket_scope("/ws/detect/local/")
        )
        await peer.send_input({"type": "websocket.receive", "bytes": pack_request(0, 1, jpeg())})
        self.assertEqual((await terminal(peer, "error"))["code"], "local_result_invalid")

    async def test_origin_capacity_and_invalid_source(self):
        """Functionality: Enforce browser origin/Host/query and single-session source admission.
        Inputs: distinct invalid scopes, second peer and reordered source packet.
        Outputs: rejection before provider access or explicit bounded session failure.
        Logic: count actual synthetic provider sends. Constraints: no external transport calls.
        """
        provider = PeerFixture()
        gateway = LocalGateway(opener=provider.context)
        for changed in (
            {"query_string": b"secret=x"},
            {"headers": [(b"origin", b"https://foreign.example")]},
            {"headers": [(b"origin", ORIGIN.encode()), (b"host", b"localhost:8766")]},
        ):
            rejected = ApplicationCommunicator(
                gateway, {**socket_scope("/ws/detect/local/"), **changed}
            )
            await rejected.send_input({"type": "websocket.connect"})
            self.assertEqual((await rejected.receive_output())["code"], 1008)
            await rejected.wait()
        first, _ = await open_worker(gateway, socket_scope("/ws/detect/local/"))
        second, error = await open_worker(gateway, socket_scope("/ws/detect/local/"))
        self.assertEqual(error["code"], "local_busy")
        await second.receive_output()
        await second.wait()
        await first.send_input({"type": "websocket.receive", "bytes": pack_request(1, 1, jpeg())})
        self.assertEqual((await terminal(first, "error"))["code"], "invalid_sequence")
        self.assertEqual(provider.sent, ['{"kind":"stop"}'])

    async def test_stop_suppressed_result_counts(self):
        """Functionality: Separate verified provider results from browser delivery on stop.
        Inputs: queued valid result held until browser stop is acknowledged upstream.
        Outputs: worker_verified1 but frames/delivered0 and confirmed forwarded1.
        Logic: release queued PRS1 after stop; validate the worker's actual terminal counts.
        Constraints: ASGI send completion is not a physical browser display acknowledgement.
        """
        result_gate = asyncio.Event()
        provider = PeerFixture(result_gate=result_gate)
        peer, _ = await open_worker(
            LocalGateway(opener=provider.context), socket_scope("/ws/detect/local/")
        )
        await peer.send_input({"type": "websocket.receive", "bytes": pack_request(0, 1, jpeg())})
        await asyncio.wait_for(provider.result_ready.wait(), 1)
        await peer.send_input({"type": "websocket.receive", "text": '{"kind":"stop"}'})
        await asyncio.wait_for(provider.stop_seen.wait(), 1)
        result_gate.set()
        ending = await terminal(peer)
        self.assertEqual(ending["frames"], 0)
        self.assertEqual(ending["delivered"], 0)
        self.assertEqual(ending["worker_verified"], 1)
        self.assertEqual(ending["worker_received"], 1)
        self.assertEqual(ending["received"], 1)
        self.assertEqual(ending["forwarded"], 1)

    async def test_early_result_confirms_send(self):
        """Functionality: Admit valid results that arrive before the provider send await completes.
        Inputs: synthetic already-buffered send held at its completion boundary.
        Outputs: exact result and confirmed prefix count after cancellation.
        Logic: source map and attempted prefix correlate output; worker results confirm receipt.
        Constraints: no successful send counter increment based solely on attempting a transmission.
        """
        send_gate = asyncio.Event()
        provider = PeerFixture(send_gate=send_gate)
        peer, _ = await open_worker(
            LocalGateway(opener=provider.context), socket_scope("/ws/detect/local/")
        )
        await peer.send_input({"type": "websocket.receive", "bytes": pack_request(0, 1, jpeg())})
        metadata, _ = unpack_result((await peer.receive_output())["bytes"], 0)
        self.assertEqual(metadata["capture_time_s"], 1)
        self.assertFalse(send_gate.is_set())
        await peer.send_input({"type": "websocket.receive", "text": '{"kind":"stop"}'})
        ending = await terminal(peer)
        self.assertEqual(ending["frames"], 1)
        self.assertEqual(ending["forwarded"], 1)
        self.assertEqual(ending["worker_verified"], 1)

    async def test_invalid_terminal_counts(self):
        """Functionality: Reject missing, bool, negative, inconsistent and impossible worker counts.
        Inputs: independent terminal fixtures with zero submitted frames.
        Outputs: local_result_invalid instead of falsely successful endings.
        Logic: validate all three integer counters against attempted/admitted/result prefixes.
        Constraints: no coercion, count repair or replacement success response.
        """
        valid = {"kind": "ended", "frames": 0, "received": 0, "delivered": 0}
        for changed in (
            {"frames": False},
            {"received": True},
            {"delivered": False},
            {"received": -1},
            {"received": 1},
            {"delivered": 1},
            {"frames": 1},
            {"received": None},
        ):
            provider = PeerFixture(terminal_override={**valid, **changed})
            peer, _ = await open_worker(
                LocalGateway(opener=provider.context), socket_scope("/ws/detect/local/")
            )
            await peer.send_input({"type": "websocket.receive", "text": '{"kind":"stop"}'})
            self.assertEqual((await terminal(peer, "error"))["code"], "local_result_invalid")

    async def test_repeated_cancel_preserves_cleanup(self):
        """Functionality: Hold gateway admission until provider ends through repeated cancellation.
        Inputs: explicit pending worker terminal and two outer task cancellations.
        Outputs: busy second peer, then released capacity after provider completion.
        Logic: disconnect the browser while worker drains; shield the entire cleanup task.
        Constraints: fixture never claims actual GPU or TCP shutdown behavior.
        """
        terminal_gate = asyncio.Event()
        provider = PeerFixture(terminal_gate=terminal_gate)
        gateway = LocalGateway(opener=provider.context)
        peer, _ = await open_worker(gateway, socket_scope("/ws/detect/local/"))
        peer.future.cancel()
        await asyncio.wait_for(provider.stop_seen.wait(), 1)
        peer.future.cancel()
        await asyncio.sleep(0.01)
        self.assertFalse(peer.future.done())
        second, error = await open_worker(gateway, socket_scope("/ws/detect/local/"))
        self.assertEqual(error["code"], "local_busy")
        await second.receive_output()
        await second.wait()
        terminal_gate.set()
        await peer.wait(timeout=2)
        self.assertTrue(peer.future.cancelled())
        self.assertTrue(gateway.admission.acquire(blocking=False))
        gateway.admission.release()


class WrapperTests(SimpleTestCase):
    """Functionality: Verify entry-specific software isolation and honest status surfaces.
    Logic: mocked software callable and actual ASGI responses. Constraints: no original app edits.
    """

    async def test_delegation_disabled_routes_and_status(self):
        """Functionality: Delegate original scopes while preventing online-service handler calls.
        Inputs: original and external HTTP/WS paths, unloaded worker health.
        Outputs: exact software delegation, explicit feature errors and actual unloaded status.
        Logic: use async mock software to count invocation; no request can trigger original
        providers.
        Constraints: routing test does not validate actual MCP/database/online service behavior.
        """
        from unittest.mock import AsyncMock

        software = AsyncMock()
        health = Mock(
            return_value={
                "status": "ok",
                "provider": "local",
                "loaded": False,
                "ready": False,
                "busy": False,
            }
        )
        wrapper = IntegrationApplication(software, health_reader=health)
        for scope in (
            {"type": "lifespan"},
            {"type": "http", "path": "/api/health/"},
            {"type": "websocket", "path": "/ws/echo/"},
            {"type": "websocket", "path": "/ws/control/"},
            {"type": "http", "path": "/mcp"},
        ):
            await wrapper(scope, Mock(), Mock())
        self.assertEqual(software.await_count, 5)
        for path in ("/scene/describe/", "/maps/route/", "/maps/"):
            response = ApplicationCommunicator(
                wrapper, {"type": "http", "path": path, "method": "POST"}
            )
            self.assertEqual((await response.receive_output())["status"], 403)
            self.assertEqual(
                json.loads((await response.receive_output())["body"])["code"],
                "local_only_external_disabled",
            )
            await response.wait()
        disabled = ApplicationCommunicator(wrapper, socket_scope("/ws/detect/hf/"))
        await disabled.send_input({"type": "websocket.connect"})
        await disabled.receive_output()
        await terminal(disabled, "error")
        response = ApplicationCommunicator(
            wrapper,
            {
                "type": "http",
                "path": "/integration/status/",
                "method": "GET",
                "headers": [(b"host", b"127.0.0.1:8766")],
            },
        )
        self.assertEqual((await response.receive_output())["status"], 200)
        status = json.loads((await response.receive_output())["body"])
        self.assertFalse(status["ready"])
        self.assertFalse(status["loaded"])
        self.assertNotIn("http://", json.dumps(status))
        await response.wait()
        self.assertEqual(software.await_count, 5)

    async def test_page_injection_and_asset_allowlist(self):
        """Functionality: Inject independent UI entry while keeping source HTML unchanged.
        Inputs: real HTML and forbidden asset names. Outputs: local marker/module and 404s.
        Logic: bounded file reads after allowlist checks. Constraints: no DOM/browser validation
        here.
        """
        wrapper = IntegrationApplication(Mock())
        page = ApplicationCommunicator(wrapper, {"type": "http", "path": "/live/", "method": "GET"})
        self.assertEqual((await page.receive_output())["status"], 200)
        body = (await page.receive_output())["body"]
        self.assertIn(b'data-local-mvp="true"', body)
        self.assertIn(b"../integration/assets/local-mvp.js", body)
        await page.wait()
        for name in ("../.env", "../../backend/models/yolo26n.pt", "README.md"):
            response = ApplicationCommunicator(
                wrapper, {"type": "http", "method": "GET", "path": "/integration/assets/" + name}
            )
            self.assertEqual((await response.receive_output())["status"], 404)
            await response.receive_output()
            await response.wait()

    async def test_video_entry_isolated(self):
        """Functionality: Serve the independent file-video entry without camera permission.
        Inputs: real video HTML/speech asset, HEAD and invalid method requests.
        Outputs: safe CSP/blob media, denied camera/microphone and video speech strategy wiring.
        Logic: verify isolated entry and actual module serving before input/inference can run.
        Constraints: codec, motion and actual GPU behavior need separate browser validation.
        """
        wrapper = IntegrationApplication(Mock())
        for method in ("GET", "HEAD", "POST"):
            response = ApplicationCommunicator(
                wrapper, {"type": "http", "path": "/integration/video/", "method": method}
            )
            start = await response.receive_output()
            body = (await response.receive_output())["body"]
            self.assertEqual(start["status"], 405 if method == "POST" else 200)
            if method != "POST":
                headers = dict(start["headers"])
                self.assertEqual(headers[b"permissions-policy"], b"camera=(), microphone=()")
                self.assertIn(b"media-src 'self' blob:", headers[b"content-security-policy"])
                if method == "GET":
                    self.assertIn(b"video-mvp.js", body)
                    self.assertNotIn(b'assets/camera.js', body)
                else:
                    self.assertEqual(body, b"")
                    self.assertGreater(int(headers[b"content-length"]), 0)
            await response.wait()

        for name in ("video-alerts.js", "video-mvp.js"):
            response = ApplicationCommunicator(
                wrapper, {"type": "http", "path": "/integration/assets/" + name, "method": "GET"}
            )
            start = await response.receive_output()
            body = (await response.receive_output())["body"]
            self.assertEqual(start["status"], 200)
            self.assertEqual(dict(start["headers"])[b"content-type"], b"text/javascript")
            if name == "video-mvp.js":
                self.assertIn(b'from "./video-alerts.js"', body)
                self.assertIn(b"selectAlert: selectVideoAlert, alertText: videoAlertText", body)
            else:
                self.assertIn(b"export function selectVideoAlert", body)
                self.assertIn(b"export function videoAlertText", body)
            await response.wait()

    async def test_navigation_entry_isolated(self):
        """Functionality: Serve offline navigation replay without device or external dependencies.
        Inputs: real replay page/modules/source, HEAD and invalid methods. Outputs: fixed assets,
        synthetic provenance and denied camera/microphone/geolocation policy.
        Logic: route only the isolated page/assets; original maps and core remain unchanged.
        Constraints: this checks serving boundaries, not browser speech or real GPS/ORS accuracy.
        """
        wrapper = IntegrationApplication(Mock())
        for method in ("GET", "HEAD", "POST"):
            response = ApplicationCommunicator(
                wrapper, {"type": "http", "path": "/integration/navigation/", "method": method}
            )
            start = await response.receive_output()
            body = (await response.receive_output())["body"]
            self.assertEqual(start["status"], 405 if method == "POST" else 200)
            if method != "POST":
                headers = dict(start["headers"])
                self.assertEqual(
                    headers[b"permissions-policy"], b"camera=(), microphone=(), geolocation=()"
                )
                self.assertNotIn(b"https://", headers[b"content-security-policy"])
                if method == "GET":
                    self.assertIn(b"navigation-mvp.js", body)
                    self.assertIn(b'data-local-mvp="true"', body)
                    self.assertNotIn(b"video-mvp.js", body)
                    self.assertNotIn(b"maps-page.js", body)
                else:
                    self.assertEqual(body, b"")
            await response.wait()
        for name in (
            "navigation-mvp.js", "navigation-speech.js", "navigation-replay.js",
            "navigation.css", "navigation-sample.json",
        ):
            response = ApplicationCommunicator(
                wrapper, {"type": "http", "path": "/integration/assets/" + name, "method": "GET"}
            )
            start = await response.receive_output()
            body = (await response.receive_output())["body"]
            self.assertEqual(start["status"], 200)
            if name.endswith(".json"):
                self.assertEqual(dict(start["headers"])[b"content-type"], b"application/json")
                sample = json.loads(body)
                self.assertEqual(sample["source"], "synthetic")
                self.assertTrue(sample["fixes"])
            elif name == "navigation-mvp.js":
                self.assertIn(b"new WalkingNavigation", body)
                self.assertIn(b"new NavigationSpeechAdapter", body)
                self.assertIn(b'from "/assets/navigation.js"', body)
                self.assertIn(b'from "/assets/maps-client.js"', body)
                self.assertNotIn(b"../navigation.js", body)
                self.assertNotIn(b"GuidanceManager", body)
            await response.wait()

        downstream = AsyncMock()
        wrapper = IntegrationApplication(downstream)
        for path in ("/integration/navigation.js", "/integration/maps-client.js",
                     "/assets/navigation.js", "/assets/maps-client.js"):
            receive, send = AsyncMock(), AsyncMock()
            scope = {"type": "http", "path": path, "method": "GET"}
            await wrapper(scope, receive, send)
            downstream.assert_awaited_with(scope, receive, send)
            downstream.reset_mock()

    async def test_inference_health_is_lazy(self):
        """Functionality: Exercise the independent worker health ASGI path without initializing
        models.
        Inputs: unloaded synthetic runtime and real HTTP router. Outputs: loadedfalse and zero
        loads.
        Logic: patch only module-owned worker. Constraints: no inference/CUDA dependency is
        required.
        """
        from .inference import asgi

        runtime, _, factory = fixture_runtime()
        try:
            with patch.object(asgi, "worker", InferenceWorker(runtime)):
                response = ApplicationCommunicator(
                    asgi.application,
                    {
                        "type": "http",
                        "path": "/health/",
                        "method": "GET",
                        "client": ("127.0.0.1", 1234),
                        "server": ("127.0.0.1", 8767),
                    },
                )
                self.assertEqual((await response.receive_output())["status"], 200)
                self.assertFalse(json.loads((await response.receive_output())["body"])["loaded"])
                await response.wait()
            factory.assert_not_called()
        finally:
            await runtime.close()
