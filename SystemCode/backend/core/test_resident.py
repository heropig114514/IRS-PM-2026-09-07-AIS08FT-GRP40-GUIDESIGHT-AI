"""Responsibilities: Verify resident model ownership, stream boundaries and shutdown semantics.
Implementation: Real executor threads with gated synthetic inference and in-memory ASGI messages.
Related Modules: realtime.services/resident_asgi/protocol; core.test_frames.
Declaration Index: Detector; Detector.__init__; Detector.predict; fallback; Harness;
Harness.__init__; Harness.message; Harness.finish; ResidentTests;
ResidentTests.test_reuse_and_reset;
ResidentTests.test_stop_drains_and_order; ResidentTests.test_disconnect_waits_for_worker;
ResidentTests.test_bad_packets_and_credits; ResidentTests.test_origin_and_busy;
ResidentTests.test_failures_and_lifespan; ResidentTests.test_repeated_cancel_joins.
Variable Index: ORIGIN — local test-only origin; no real GPU, external service or camera claims.
"""

import asyncio
import hashlib
import json
import threading
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from core.detector import DetectionError
from core.test_frames import jpeg
from realtime.protocol import pack_request, unpack_result
from realtime.resident_asgi import ResidentApplication
from realtime.services import ResidentService

ORIGIN = "http://127.0.0.1:8766"


class Detector:
    """Functionality: Expose worker ownership and controllable synthetic inference.
    Logic: events gate prediction; hash real bytes. Constraints: no model or timing claim.
    """

    def __init__(self, root, mode):
        """Functionality: Record initialization and create prediction gates.
        Inputs: unused asset root/mode. Outputs: explicit synthetic state.
        Logic: permit immediate completion by default. Constraints: no CUDA/model construction.
        """
        self.threads = [threading.get_ident()]
        self.entered, self.proceed = threading.Event(), threading.Event()
        self.proceed.set()
        self.error = None
        self.precision, self.backend = "FP32", "synthetic test"
        self.manifest = {"source_sha256": "0" * 64, "names": [str(i) for i in range(80)],
                         "build": {"ultralytics": "test"}}

    def predict(self, raw):
        """Functionality: Block only when explicitly gated, then echo fingerprinted bytes.
        Inputs: JPEG. Outputs: synthetic metadata/JPEG or injected exception.
        Logic: retain thread IDs for ownership assertions. Constraints: finite test gate timeout.
        """
        self.threads.append(threading.get_ident())
        self.entered.set()
        if not self.proceed.wait(5):
            raise TimeoutError("Test gate expired")
        if self.error:
            raise self.error
        return {"jpeg_sha256": hashlib.sha256(raw).hexdigest()}, raw


async def fallback(scope, receive, send):
    """Functionality: Provide explicit minimal underlying ASGI lifespan for wrapper tests.
    Inputs: ASGI triple. Outputs: startup/shutdown completion. Logic: acknowledge exact events.
    Constraints: asserts only lifecycle delegation, not Django or MCP behavior.
    """
    if scope["type"] == "lifespan":
        assert (await receive())["type"] == "lifespan.startup"
        await send({"type": "lifespan.startup.complete"})
        assert (await receive())["type"] == "lifespan.shutdown"
        await send({"type": "lifespan.shutdown.complete"})


class Harness:
    """Functionality: Drive adapter message ordering without network scheduling variability.
    Logic: explicit ASGI receive/send queues. Constraints: no transport performance claims.
    """

    def __init__(self, app, **scope_overrides):
        """Functionality: Start a connection with configurable scope.
        Inputs: adapter and overrides. Outputs: task/input/output queues.
        Logic: queue connect before running application. Constraints: caller must finish task.
        """
        self.incoming, self.outgoing = asyncio.Queue(), asyncio.Queue()
        self.incoming.put_nowait({"type": "websocket.connect"})
        scope = {"type": "websocket", "path": "/ws/detect/resident/", "query_string": b"",
                 "headers": [(b"origin", ORIGIN.encode()), (b"host", b"127.0.0.1:8766")]}
        self.task = asyncio.create_task(app({**scope, **scope_overrides},
                                           self.incoming.get, self.outgoing.put))

    async def message(self):
        """Functionality: Read a bounded next server response.
        Inputs: output queue. Outputs: ASGI message. Logic: fail stalled tests after three seconds.
        Constraints: timeout is a test guard, not a production latency assertion.
        """
        return await asyncio.wait_for(self.outgoing.get(), 3)

    async def finish(self):
        """Functionality: Disconnect and join the adapter.
        Inputs: running task. Outputs: completed cleanup. Logic: enqueue disconnect then await.
        Constraints: worker gate must be released by caller first.
        """
        self.incoming.put_nowait({"type": "websocket.disconnect"})
        await asyncio.wait_for(self.task, 3)


@override_settings(CAMERA_ALLOWED_ORIGINS=[ORIGIN])
class ResidentTests(SimpleTestCase):
    """Functionality: Exercise concurrency, protocol and lifecycle failure boundaries.
    Logic: real worker execution with deterministic gates. Constraints: synthetic detector only.
    """

    async def test_reuse_and_reset(self):
        """Functionality: Keep models/thread while resetting tracking, distance and counters.
        Inputs: two fake trackers/leases and first-session statistics. Outputs: isolated state.
        Logic: assert factory count, ownership and zero second-session counters with unchanged
        strategy configuration. Constraints: no real tracking-quality claim.
        """
        with patch("realtime.tracking.ObjectTracker", side_effect=[object(), object()]) as tracker:
            service = ResidentService(Path(), "full", factory=Detector)
            await service.start()
            try:
                detector = service.detector
                first = service.claim()
                await service.begin(first)
                old = detector.object_tracker
                old_strategy, old_performance = detector.distance_strategy, detector.performance
                old_performance.record(detection_ms=1, distance_ms=2, end_to_end_ms=3,
                                       depth_frame=True)
                old_strategy.frame_index = 9
                await service.predict(first, jpeg())
                service.release(first)
                second = service.claim()
                await service.begin(second)
                self.assertIsNot(old, detector.object_tracker)
                self.assertIsNot(old_strategy, detector.distance_strategy)
                self.assertEqual(detector.distance_strategy.config, old_strategy.config)
                self.assertEqual(detector.distance_strategy.frame_index, 0)
                self.assertIsNot(old_performance, detector.performance)
                self.assertEqual(detector.performance.total_frames, 0)
                self.assertEqual(detector.performance.depth_model_calls, 0)
                self.assertIs(detector, service.detector)
                self.assertEqual(tracker.call_count, 2)
                await service.predict(second, jpeg())
                self.assertEqual(len(set(detector.threads)), 1)
                self.assertNotEqual(detector.threads[0], threading.get_ident())
                service.release(second)
            finally:
                await service.close()

    async def test_stop_drains_and_order(self):
        """Functionality: Deliver every accepted frame before normal stop acknowledgement.
        Inputs: twelve queued frames then stop. Outputs: twelve ordered/hash-valid results.
        Logic: count received/delivered and confirm lease release. Constraints: no dropped tail.
        """
        service = ResidentService(Path(), "detection", factory=Detector)
        await service.start()
        try:
            harness = Harness(ResidentApplication(service, fallback))
            await harness.message()
            self.assertEqual(json.loads((await harness.message())["text"])["protocol"],
                             "resident-v1")
            raw = jpeg()
            for index in range(12):
                harness.incoming.put_nowait({"type": "websocket.receive",
                                            "bytes": pack_request(index, raw)})
            harness.incoming.put_nowait({"type": "websocket.receive", "text": '{"kind":"stop"}'})
            for index in range(12):
                self.assertEqual(unpack_result((await harness.message())["bytes"], index)[1], raw)
            ended = json.loads((await harness.message())["text"])
            self.assertEqual((ended["kind"], ended["received"], ended["delivered"]),
                             ("ended", 12, 12))
            self.assertEqual((await harness.message())["code"], 1000)
            await harness.finish()
            self.assertIsNone(service.active)
        finally:
            await service.close()

    async def test_disconnect_waits_for_worker(self):
        """Functionality: Prevent reconnect from reusing GPU buffers before cancelled work exits.
        Inputs: gated prediction then peer disconnect. Outputs: admission stays busy until finish.
        Logic: release real worker gate after asserting pending cleanup. Constraints: no sleeps as
        evidence of GPU latency; thread events establish execution ownership.
        """
        service = ResidentService(Path(), "detection", factory=Detector)
        await service.start()
        service.detector.proceed.clear()
        harness = Harness(ResidentApplication(service, fallback))
        try:
            await harness.message()
            await harness.message()
            harness.incoming.put_nowait({"type": "websocket.receive",
                                        "bytes": pack_request(0, jpeg())})
            self.assertTrue(await asyncio.to_thread(service.detector.entered.wait, 2))
            harness.incoming.put_nowait({"type": "websocket.disconnect"})
            await asyncio.sleep(0.02)
            self.assertFalse(harness.task.done())
            self.assertIsNone(service.claim())
            service.detector.proceed.set()
            await harness.finish()
            self.assertIsNone(service.active)
            self.assertTrue(service.ready)
        finally:
            service.detector.proceed.set()
            await service.close()

    async def test_bad_packets_and_credits(self):
        """Functionality: Reject wrong sequence, invalid framing and excess outstanding frames.
        Inputs: invalid packets plus thirteen uninterrupted frames. Outputs: explicit failure/close.
        Logic: input is queued before writer can return credits. Constraints: no recovery retries.
        """
        service = ResidentService(Path(), "detection", factory=Detector)
        await service.start()
        try:
            raw = jpeg()
            for packets in ([pack_request(1, raw)], [b"bad"],
                            [pack_request(i, raw) for i in range(13)]):
                harness = Harness(ResidentApplication(service, fallback))
                await harness.message()
                await harness.message()
                for packet in packets:
                    harness.incoming.put_nowait({"type": "websocket.receive", "bytes": packet})
                messages = []
                while not messages or messages[-1]["type"] != "websocket.close":
                    messages.append(await harness.message())
                self.assertEqual(messages[-1]["code"], 1011)
                self.assertEqual(json.loads(messages[-2]["text"])["kind"], "error")
                await harness.finish()
                self.assertIsNone(service.active)
        finally:
            await service.close()

    async def test_origin_and_busy(self):
        """Functionality: Enforce origin/host/query boundaries and nonqueued single-user admission.
        Inputs: invalid scopes and concurrent valid connections. Outputs:1008 or busy1013.
        Logic: keep first lease open while second connects. Constraints: no authentication claim.
        """
        service = ResidentService(Path(), "detection", factory=Detector)
        await service.start()
        try:
            app = ResidentApplication(service, fallback)
            for overrides in ({"query_string": b"token=x"}, {"headers": []},
                              {"headers": [(b"origin", ORIGIN.encode()), (b"host", b"evil")]}):
                harness = Harness(app, **overrides)
                self.assertEqual((await harness.message())["code"], 1008)
                await harness.finish()
            first, second = Harness(app), None
            await first.message()
            await first.message()
            second = Harness(app)
            await second.message()
            self.assertEqual(json.loads((await second.message())["text"])["code"], "resident_busy")
            self.assertEqual((await second.message())["code"], 1013)
            await second.finish()
            await first.finish()
        finally:
            await service.close()

    async def test_failures_and_lifespan(self):
        """Functionality: Fail startup explicitly and distinguish bad input from pipeline failure.
        Inputs: failing factory, then input-error and runtime-error predictions. Outputs: correct
        readiness latch and sanitized failure. Logic: inspect public messages/state, no retry.
        Constraints: error bodies never expose exception text or local paths.
        """
        with patch(__name__ + ".Detector", side_effect=RuntimeError("private detail")) as factory:
            service = ResidentService(Path(), factory=factory)
            messages = asyncio.Queue()
            incoming = asyncio.Queue()
            incoming.put_nowait({"type": "lifespan.startup"})
            await ResidentApplication(service, fallback)(
                {"type": "lifespan"}, incoming.get, messages.put)
            response = await messages.get()
            self.assertEqual(response["type"], "lifespan.startup.failed")
            self.assertNotIn("private detail", response["message"])
        service = ResidentService(Path(), "detection", factory=Detector)
        incoming, outgoing = asyncio.Queue(), asyncio.Queue()
        incoming.put_nowait({"type": "lifespan.startup"})
        task = asyncio.create_task(ResidentApplication(service, fallback)(
            {"type": "lifespan"}, incoming.get, outgoing.put))
        self.assertEqual((await outgoing.get())["type"], "lifespan.startup.complete")
        lease = service.claim()
        await service.begin(lease)
        for error, ready in ((DetectionError("invalid_frame", "bad input", 400), True),
                             (RuntimeError("worker failed"), False)):
            service.detector.error = error
            with self.assertRaises(type(error)):
                await service.predict(lease, jpeg())
            self.assertEqual(service.ready, ready)
        service.release(lease)
        self.assertIsNone(service.claim())
        incoming.put_nowait({"type": "lifespan.shutdown"})
        self.assertEqual((await outgoing.get())["type"], "lifespan.shutdown.complete")
        self.assertFalse(service.ready)
        self.assertTrue(service.executor._shutdown)
        await task

    async def test_repeated_cancel_joins(self):
        """Functionality: Drain real thread work even if a caller receives repeated cancellation.
        Inputs: blocked worker and two task.cancel calls. Outputs: cancellation only after finish.
        Logic: assert future remains pending until worker event released. Constraints: no CUDA.
        """
        service = ResidentService(Path(), "detection", factory=Detector)
        await service.start()
        service.detector.proceed.clear()
        lease = service.claim()
        task = asyncio.create_task(service.predict(lease, jpeg()))
        try:
            self.assertTrue(await asyncio.to_thread(service.detector.entered.wait, 2))
            for _ in range(2):
                task.cancel()
                await asyncio.sleep(0.01)
            self.assertFalse(task.done())
            service.detector.proceed.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        finally:
            service.detector.proceed.set()
            await service.close()
