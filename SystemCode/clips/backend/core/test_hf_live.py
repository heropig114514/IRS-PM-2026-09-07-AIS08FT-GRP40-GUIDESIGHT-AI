"""Responsibilities: Verify phone relay ownership, free-quota gating and binary delivery.
Implementation: Two real ASGI/socket servers, mandatory startup echo and a synthetic GPU worker.
Related Modules: hf_video_relay; test_zerogpu_live; test_protocol.FakeDetector; realtime.protocol.
Declaration Index: serve_relay; dependencies; dependencies.opener; dependencies.allocator;
HFLiveTests; HFLiveTests.test_gate_and_no_start_before_frame;
HFLiveTests.test_order_busy_and_actual_worker_cleanup;
HFLiveTests.test_invalid_origin_query_sequence_and_credit;
HFLiveTests.test_invalid_origin_query_sequence_and_credit.receive;
HFLiveTests.test_worker_failure_is_not_success;
HFLiveTests.test_worker_failure_is_not_success.broken;
HFLiveTests.test_summary_does_not_cancel_final_delivery;
HFLiveTests.test_summary_does_not_cancel_final_delivery.Peer;
HFLiveTests.test_summary_does_not_cancel_final_delivery.Peer.__init__;
HFLiveTests.test_summary_does_not_cancel_final_delivery.Peer.send;
HFLiveTests.test_summary_does_not_cancel_final_delivery.Peer.recv;
HFLiveTests.test_summary_does_not_cancel_final_delivery.opener;
HFLiveTests.test_summary_does_not_cancel_final_delivery.allocator;
HFLiveTests.test_summary_does_not_cancel_final_delivery.send.
Variable Index: None. Sockets, semaphores, fixture credentials and counters are test-local.
These tests spend no HF quota and exercise no CUDA or physical camera.
"""

import asyncio
import hashlib
import json
import socket
import threading
import time
from contextlib import asynccontextmanager

import uvicorn
from django.test import SimpleTestCase, override_settings
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedOK, InvalidStatus
from websockets.frames import Close

from realtime.protocol import MAX_PACKET_BYTES, pack_request, pack_result, unpack_result
from zerogpu.live_transport import MAX_INFLIGHT, WARMUP_BYTES, WARMUP_PREFIX
from zerogpu.live_worker import run_worker

from .hf_video_relay import HFLiveRelay, RelaySession
from .test_frames import jpeg
from .test_protocol import FakeDetector
from .test_zerogpu_live import ORIGIN, serve_transport


@asynccontextmanager
async def serve_relay(relay):
    """Functionality: Run the actual phone relay over an isolated real WS server.
    Inputs: explicit relay dependency; Outputs: WS URL and matching browser origin.
    Logic: ephemeral loopback binding, bounded queue/bytes, scoped Django origin allowlist.
    Constraints: no production server, external identity, TLS or GPU claims.
    """
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(
        uvicorn.Config(
            relay,
            log_level="error",
            lifespan="off",
            ws_max_queue=MAX_INFLIGHT,
            ws_max_size=MAX_PACKET_BYTES,
            ws_per_message_deflate=False,
        )
    )
    running = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if running.done():
                    running.result()
                await asyncio.sleep(0.01)
        with override_settings(CAMERA_ALLOWED_ORIGINS=[origin]):
            yield f"ws://127.0.0.1:{port}/ws/detect/hf/", origin
    finally:
        server.should_exit = True
        await asyncio.wait_for(running, 5)
        listener.close()


def dependencies(broker, port, *, allowed=True, factory=FakeDetector):
    """Functionality: Bind explicit local upstream, quota and synchronous worker fixtures.
    Inputs: synthetic broker/port, allowed gate, detector factory; Outputs: relay and call list.
    Logic: no production credential/external host; inject minimal transport-admission metadata
    independently of production quality-plan files; preserve actual binary/worker lifecycle.
    Constraints: factory is explicit test substitution, never an automatic provider fallback.
    """
    calls = []

    def opener(url, **kwargs):
        """Functionality: Connect to the explicit local HF transport fixture.
        Inputs: ignored production URL/options; Outputs: actual local connect context.
        Logic: replace host/origin and remove synthetic private auth; retain queue/time limits.
        Constraints: tests do not verify TLS/HF edge or real authentication.
        """
        kwargs["origin"] = ORIGIN
        kwargs.pop("additional_headers")
        return connect(f"ws://127.0.0.1:{port}/prs_live/ws", **kwargs)

    def allocator(capability, token):
        """Functionality: Execute one synchronous worker using explicit FakeDetector.
        Inputs: fixture capability/token; Outputs: actual loopback worker summary.
        Logic: record dispatch/acknowledgement; use private worker key from local broker only.
        Constraints: no Gradio/ZeroGPU dispatch, thread completion is actually awaited.
        """
        calls.append({"started": True})
        summary = run_worker(broker.session.worker_capability, None, None, port, factory)
        calls[-1]["summary"] = summary
        return summary

    relay = HFLiveRelay(
        opener=opener,
        allocator=allocator,
        token_reader=lambda: "hf_fixture",
        quota_reader=lambda token: {"allowed": allowed},
        contract_reader=lambda: {"kind": "session", "max_inflight": MAX_INFLIGHT},
        admission=threading.BoundedSemaphore(1),
    )
    return relay, calls


class HFLiveTests(SimpleTestCase):
    """Functionality: Validate admission, bounded credits, error and stop over real sockets.
    Logic: isolated fixtures exercise actual relay state; Constraints: no performance/GPU proof.
    """

    async def test_summary_does_not_cancel_final_delivery(self):
        """Functionality: Drain final delivery when the SSE summary completes before ASGI send.
        Inputs: explicit peer/send/allocator gates; Outputs: delivered1 after completion summary.
        Logic: block send, finish allocator, then release send; previously cancellation lost JPEG.
        Constraints: deliberate socket/SSE scheduling substitution, no real GPU/network evidence.
        """
        gate, started = asyncio.Event(), threading.Event()
        messages = []
        raw = jpeg()

        class Peer:
            """Functionality: Supply hello, startup control, final JPEG then normal close.
            Logic: finite explicit events; Constraints: not a network stack or GPU provider.
            """

            def __init__(self):
                """Functionality: Initialize next event counter.
                Inputs: None; Outputs: state0; Logic: deterministic order; Constraints: no I/O.
                """
                self.count = 0

            async def send(self, packet):
                """Functionality: Accept the first request and check exact control acknowledgement.
                Inputs: packet; Outputs: None or assertion; Logic: exact control verification.
                Constraints: no network, model or timing claim.
                """
                if self.count == 2:
                    assert packet == WARMUP_PREFIX + bytes(WARMUP_BYTES - 4)

            async def recv(self):
                """Functionality: Return ordered explicit provider events.
                Inputs: count/raw; Outputs: hello/control/result or normal close.
                Logic: result hashes real JPEG; Constraints: no model/SSE/transport measurement.
                """
                self.count += 1
                if self.count == 1:
                    return json.dumps(
                        {
                            "kind": "session",
                            "capability": "a" * 64,
                            "max_inflight": MAX_INFLIGHT,
                            "session_seconds": 55,
                            "warmup_bytes": WARMUP_BYTES,
                        }
                    )
                if self.count == 2:
                    return WARMUP_PREFIX + bytes(WARMUP_BYTES - 4)
                if self.count == 3:
                    return pack_result(
                        {"index": 0, "jpeg_sha256": hashlib.sha256(raw).hexdigest()}, raw
                    )
                raise ConnectionClosedOK(Close(1000, ""), Close(1000, ""), True)

        @asynccontextmanager
        async def opener(url, **kwargs):
            """Functionality: Supply explicit finite provider fixture.
            Inputs: ignored URL/options; Outputs: Peer context.
            Logic: no real endpoint; Constraints: tests only, never a production fallback.
            """
            yield Peer()

        def allocator(capability, token):
            """Functionality: Finish control only when final data send has started.
            Inputs: test started event; Outputs: valid frames1 summary.
            Logic: block thread until send, then return; Constraints: no actual allocation.
            """
            if not started.wait(2):
                raise TimeoutError("Fixture send did not start")
            return {"frames": 1, "gpu_session_ms": 1}

        async def send(message):
            """Functionality: Hold binary delivery until after control completion.
            Inputs: ASGI message; Outputs: recorded completion.
            Logic: set allocator gate before awaiting release; Constraints: no browser ACK claim.
            """
            if "bytes" in message:
                started.set()
                await gate.wait()
            messages.append(message)

        relay = HFLiveRelay(
            opener=opener, allocator=allocator, quota_reader=lambda token: {"allowed": True},
            contract_reader=lambda: {"kind": "session", "max_inflight": MAX_INFLIGHT},
        )
        session = RelaySession()
        session.received = 1
        await session.credits.acquire()
        session.input_received_at[0] = time.perf_counter()
        session.incoming.put_nowait(pack_request(0, raw))
        running = asyncio.create_task(relay.run_provider(session, send, "fixture"))
        try:
            async with asyncio.timeout(2):
                while session.allocation is None:
                    await asyncio.sleep(0)
                await session.allocation
            await asyncio.sleep(0.02)
            self.assertFalse(running.done())
            gate.set()
            await asyncio.wait_for(running, 2)
            self.assertEqual(session.delivered, 1)
            metadata, delivered = unpack_result(messages[-1]["bytes"], 0)
            self.assertEqual(delivered, raw)
            self.assertGreaterEqual(metadata["prs_upstream_ms"], 0)
            self.assertGreaterEqual(metadata["prs_admission_queue_ms"], 0)
            self.assertGreaterEqual(metadata["prs_result_validation_ms"], 0)
            self.assertEqual(session.input_received_at, {})
            self.assertEqual(session.upstream_started, {})
        finally:
            gate.set()
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)

    async def test_gate_and_no_start_before_frame(self):
        """Functionality: Refuse insufficient free quota and avoid allocation on hello/stop.
        Inputs: explicit denied/allowed gates with no frames; Outputs: no allocator calls.
        Logic: preflight precedes upstream allocation; Constraints: quota API itself is substituted.
        """
        async with serve_transport() as (broker, port):
            for allowed in (False, True):
                relay, calls = dependencies(broker, port, allowed=allowed)
                async with serve_relay(relay) as (url, origin):
                    async with connect(url, origin=origin, proxy=None) as peer:
                        message = json.loads(await peer.recv())
                        self.assertEqual(message["kind"], "session" if allowed else "error")
                        self.assertNotIn("capability", message)
                        self.assertNotIn("token", message)
                        if allowed:
                            self.assertEqual(message["max_inflight"], MAX_INFLIGHT)
                            await peer.send('{"kind":"stop"}')
                            self.assertEqual(json.loads(await peer.recv())["kind"], "ended")
                        else:
                            self.assertEqual(message["code"], "hf_quota_exceeded")
                self.assertEqual(calls, [])
                self.assertTrue(relay.admission.acquire(blocking=False))
                relay.admission.release()

    async def test_order_busy_and_actual_worker_cleanup(self):
        """Functionality: Deliver every sequence and hold shared admission until worker ends.
        Inputs: six JPEGs/second peer/explicit stop; Outputs: six hashes and worker acknowledgement.
        Logic: genuine socket/HTTP handoffs; no further input after stop.
        Constraints: synthetic detector; no quality/latency or real HF allocation claim.
        """
        async with serve_transport() as (broker, port):
            relay, calls = dependencies(broker, port)
            async with serve_relay(relay) as (url, origin):
                async with connect(url, origin=origin, proxy=None) as peer:
                    await peer.recv()
                    async with connect(url, origin=origin, proxy=None) as other:
                        self.assertEqual(json.loads(await other.recv())["code"], "hf_busy")
                    for index in range(5):
                        await peer.send(pack_request(index, jpeg()))
                    for index in range(6):
                        result = await asyncio.wait_for(peer.recv(), 5)
                        self.assertEqual(unpack_result(result, index)[1], jpeg())
                        if index == 0:
                            await peer.send(pack_request(5, jpeg()))
                    await peer.send('{"kind":"stop"}')
                    ended = json.loads(await asyncio.wait_for(peer.recv(), 5))
                    self.assertEqual(ended, {"kind": "ended", "received": 6, "delivered": 6})
                    self.assertEqual(calls[0]["summary"]["frames"], 6)
            self.assertIsNone(broker.session)
            self.assertEqual(len(calls), 1)
            self.assertTrue(relay.admission.acquire(blocking=False))
            relay.admission.release()

    async def test_invalid_origin_query_sequence_and_credit(self):
        """Functionality: Reject foreign origin/query/index and excessive outstanding frames.
        Inputs: explicit malformed WS requests and slow synthetic worker; Outputs: safe errors.
        Logic: origin/query fails before accept, bad frame fails whole session.
        Constraints: cannot represent browser Origin forgery protection or remote network timing.
        """
        async with serve_transport() as (broker, port):
            relay, calls = dependencies(broker, port)
            async with serve_relay(relay) as (url, origin):
                for target, source in ((url, "https://foreign.example"), (url + "?x=1", origin)):
                    with self.assertRaises(InvalidStatus):
                        async with connect(target, origin=source, proxy=None):
                            pass
                async with connect(url, origin=origin, proxy=None) as peer:
                    await peer.recv()
                    await peer.send(pack_request(1, jpeg()))
                    self.assertEqual(json.loads(await peer.recv())["kind"], "error")
            self.assertEqual(calls, [])
            session = RelaySession()
            inputs = asyncio.Queue()
            for index in range(MAX_INFLIGHT + 1):
                inputs.put_nowait(
                    {"type": "websocket.receive", "bytes": pack_request(index, jpeg())}
                )

            async def receive():
                """Functionality: Supply thirteen inputs without allowing any output credit.
                Inputs: explicit local queue; Outputs: ASGI input.
                Logic: deterministic reader boundary; Constraints: no socket/GPU timing assertion.
                """
                return await inputs.get()

            with self.assertRaisesRegex(ValueError, f"{MAX_INFLIGHT} outstanding"):
                await relay.read_browser(session, receive)
            self.assertEqual(session.received, MAX_INFLIGHT)
            self.assertEqual(session.incoming.qsize(), MAX_INFLIGHT)

    async def test_worker_failure_is_not_success(self):
        """Functionality: Propagate a real worker-thread failure without retry/fake completion.
        Inputs: explicitly failing factory; Outputs: error control, one dispatch, released slot.
        Logic: close upstream then await actual allocator failure.
        Constraints: synthetic detector failure, not a GPU/platform diagnosis.
        """

        def broken(model, manifest):
            """Functionality: Fail explicit worker construction.
            Inputs: unused model/manifest; Outputs: RuntimeError.
            Logic: deterministic failure; Constraints: no CUDA operation.
            """
            raise RuntimeError("Synthetic allocated failure")

        async with serve_transport() as (broker, port):
            relay, calls = dependencies(broker, port, factory=broken)
            async with serve_relay(relay) as (url, origin):
                async with connect(url, origin=origin, proxy=None) as peer:
                    await peer.recv()
                    await peer.send(pack_request(0, jpeg()))
                    self.assertEqual(
                        json.loads(await asyncio.wait_for(peer.recv(), 5))["kind"], "error"
                    )
            self.assertEqual(len(calls), 1)
            self.assertTrue(relay.admission.acquire(blocking=False))
            relay.admission.release()
