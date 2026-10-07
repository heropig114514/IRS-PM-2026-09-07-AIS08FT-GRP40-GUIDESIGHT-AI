"""Responsibilities: Verify real live WS/HTTP handoffs with bounded state and synthetic GPU work.
Implementation: Ephemeral Uvicorn, actual sockets and original packet parsers; no HF/CUDA calls.
Startup-control tests verify exact acknowledgement, bounded timeout and unchanged image counts.
Related Modules: live_transport/live_worker; test_protocol.FakeDetector; native bridge benchmark.
Declaration Index: empty_app; serve_transport; request; LiveTransportTests;
LiveTransportTests.test_real_worker_and_bounded_admission;
LiveTransportTests.test_origin_capability_and_worker_claim;
LiveTransportTests.test_invalid_sequence_releases_session;
LiveTransportTests.test_worker_failure_reaches_peer;
LiveTransportTests.test_worker_failure_reaches_peer.broken;
LiveTransportTests.test_allocated_deadline; LiveTransportTests.test_allocated_deadline.pull.
LiveTransportTests.test_excess_inflight_rejected; LiveTransportTests.test_idle_releases_worker.
LiveTransportTests.test_explicit_configuration;
LiveTransportTests.test_explicit_configuration.factory.
LiveTransportTests.test_warmup_acknowledgement_and_failures.
Variable Index: ORIGIN — explicit synthetic browser origin. Ephemeral server state is test-local.
"""

import asyncio
import http.client
import json
import socket
import time
from contextlib import asynccontextmanager
from unittest.mock import patch

import uvicorn
from django.test import SimpleTestCase
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK, InvalidStatus

from realtime.protocol import pack_request, unpack_result
from zerogpu.live_transport import (
    MAX_INFLIGHT,
    PREFIX,
    SESSION_SECONDS,
    WARMUP_BYTES,
    WARMUP_PREFIX,
    LiveTransport,
    receive_live_result,
)
from zerogpu.live_worker import run_worker

from .test_frames import jpeg
from .test_protocol import FakeDetector

ORIGIN = "https://prs.example"


async def empty_app(scope, receive, send):
    """Functionality: Reject non-live test routes without adding another application dependency.
    Inputs: HTTP ASGI scope/receive/send; Outputs:404; Logic: one bounded response.
    Constraints: test server disables lifespan; this fixture cannot run Gradio or allocate GPU.
    """
    await LiveTransport.respond(send, 404)


@asynccontextmanager
async def serve_transport():
    """Functionality: Bind an isolated actual ASGI transport without invoking Gradio or GPU.
    Inputs: loopback ephemeral socket/explicit test origin; Outputs: (broker, port) context.
    Logic: start Uvicorn with bounded WS queue/bytes and no compression; await startup/shutdown.
    Constraints: no external network/credentials; GPU decorator and worker are not exercised here.
    """
    broker = LiveTransport(empty_app, origins=[ORIGIN])
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            broker,
            log_level="error",
            lifespan="off",
            ws_max_queue=MAX_INFLIGHT,
            ws_max_size=2 * 1024 * 1024,
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
        yield broker, port
    finally:
        server.should_exit = True
        await asyncio.wait_for(running, 5)
        listener.close()


def request(port, path, capability=None, body=None):
    """Functionality: Send one real loopback control/health request outside the async event loop.
    Inputs: port/path/optional capability/body; Outputs: HTTP status and complete bounded bytes.
    Logic: GET health or POST explicit control; Constraints: synthetic capability never logged.
    """
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        headers = {"Authorization": "Bearer " + capability} if capability else {}
        connection.request("POST" if capability else "GET", path, body=body, headers=headers)
        response = connection.getresponse()
        return response.status, response.read(4096)
    finally:
        connection.close()


class LiveTransportTests(SimpleTestCase):
    """Functionality: Verify live-session process-boundary contracts over the actual socket stack.
    Logic: synthetic detector on blocking HTTP worker; Constraints: no real GPU/quota/performance.
    """

    async def test_explicit_configuration(self):
        """Functionality: Carry explicit selected config into the actual worker factory boundary.
        Inputs: real WS/HTTP, synthetic model/manifest/config and FakeDetector.
        Outputs: exact factory argument/one-frame completion assertions.
        Logic: exercise the three-argument factory path without initializing any GPU.
        Constraints: synthetic config is a protocol fixture, not a qualified deployment plan,
        real model precision, COCO quality, HF allocation or performance evidence.
        """
        supplied, model, manifest = [], object(), {"synthetic": True}
        config = {"dtype": "float16", "aoti": True}

        # Functionality: Record explicit construction inputs and supply synthetic prediction.
        # Inputs: worker model/manifest/config. Outputs: FakeDetector and recorded argument tuple.
        # Logic: no translation. Constraints: no model/plan loading or GPU operation.
        def factory(actual_model, actual_manifest, actual_config):
            supplied.append((actual_model, actual_manifest, actual_config))
            return FakeDetector(actual_model, actual_manifest)

        async with serve_transport() as (broker, port):
            async with connect(
                f"ws://127.0.0.1:{port}{PREFIX}/ws", origin=ORIGIN, proxy=None
            ) as peer:
                await peer.recv()
                worker = asyncio.create_task(asyncio.to_thread(
                    run_worker, broker.session.worker_capability, model, manifest, port,
                    factory, config))
                await peer.send(pack_request(0, jpeg()))
                packet = await receive_live_result(peer, 0, 5)
                self.assertEqual(unpack_result(packet, 0)[1], jpeg())
                await peer.send('{"kind":"stop"}')
                result = await asyncio.wait_for(worker, 5)
                self.assertEqual(result["frames"], 1)
                self.assertEqual(supplied, [(model, manifest, config)])
                self.assertIsNone(broker.session)

    async def test_real_worker_and_bounded_admission(self):
        """Functionality: Bound unprocessed frames, preserve every sequence and release on stop.
        Inputs:13 real JPEGs, WS peer, FakeDetector loopback worker; Outputs:13 hashes.
        Logic: queued first frame retains the deliberate50ms wait; timing fields are nonnegative;
        no-GPU backlog holds12, second peer1013; after one result, thirteenth input is admitted.
        Constraints: checksum/order verifies transport only; no skipped frames or model assertion.
        """
        async with serve_transport() as (broker, port):
            async with connect(
                f"ws://127.0.0.1:{port}{PREFIX}/ws", origin=ORIGIN, proxy=None, compression=None
            ) as peer:
                await peer.recv()
                for index in range(MAX_INFLIGHT):
                    await peer.send(pack_request(index, jpeg()))
                await asyncio.sleep(0.05)
                self.assertEqual(broker.session.received, MAX_INFLIGHT)
                self.assertEqual(broker.session.incoming.qsize(), MAX_INFLIGHT)
                async with connect(
                    f"ws://127.0.0.1:{port}{PREFIX}/ws", origin=ORIGIN, proxy=None
                ) as second:
                    with self.assertRaises(ConnectionClosedError) as caught:
                        await second.recv()
                    self.assertEqual(caught.exception.rcvd.code, 1013)
                worker = asyncio.create_task(
                    asyncio.to_thread(
                        run_worker, broker.session.worker_capability, None, None, port, FakeDetector
                    )
                )
                for index in range(MAX_INFLIGHT + 1):
                    result = await receive_live_result(peer, index, 5)
                    metadata, rendered = unpack_result(result, index)
                    self.assertEqual(rendered, jpeg())
                    for field in ("hf_parent_queue_ms", "hf_local_cycle_ms", "hf_result_ingest_ms",
                                  "hf_output_queue_ms", "worker_pull_ms", "worker_predict_ms"):
                        self.assertGreaterEqual(metadata[field], 0)
                    self.assertGreaterEqual(metadata["hf_local_cycle_ms"],
                                            metadata["worker_predict_ms"])
                    if index == 0:
                        self.assertGreaterEqual(metadata["hf_parent_queue_ms"], 40)
                        self.assertEqual(metadata["hf_transport_warmup_bytes"], 2 * WARMUP_BYTES)
                        self.assertGreaterEqual(metadata["hf_transport_warmup_ms"], 0)
                    if index == 0:
                        await peer.send(pack_request(MAX_INFLIGHT, jpeg()))
                await peer.send('{"kind":"stop"}')
                summary = await asyncio.wait_for(worker, 2)
                self.assertEqual(summary["frames"], MAX_INFLIGHT + 1)
                self.assertIsNone(broker.session)

    async def test_origin_capability_and_worker_claim(self):
        """Functionality: Reject untrusted origins/capabilities and duplicate worker allocation.
        Inputs: real HTTP/WS routes and synthetic capabilities; Outputs:403/409/health semantics.
        Logic: readiness spends no GPU; WS admission then one start claim; duplicate start409.
        Constraints: testing loopback authorization cannot verify HF edge-proxy behavior.
        """
        async with serve_transport() as (broker, port):
            status, body = await asyncio.to_thread(request, port, PREFIX + "/healthz")
            self.assertEqual(status, 200)
            self.assertFalse(json.loads(body)["gpu_allocated_by_this_route"])
            for origin in (None, "https://foreign.example"):
                with self.assertRaises(InvalidStatus) as caught:
                    async with connect(
                        f"ws://127.0.0.1:{port}{PREFIX}/ws", origin=origin, proxy=None
                    ):
                        pass
                self.assertEqual(caught.exception.response.status_code, 403)
            async with connect(
                f"ws://127.0.0.1:{port}{PREFIX}/ws", origin=ORIGIN, proxy=None
            ) as peer:
                capability = json.loads(await peer.recv())["capability"]
                self.assertEqual(
                    (await asyncio.to_thread(request, port, PREFIX + "/start", capability))[0], 403
                )
                worker_key = broker.session.worker_capability
                self.assertEqual(
                    (await asyncio.to_thread(request, port, PREFIX + "/start", worker_key))[0], 200
                )
                self.assertEqual(
                    (await asyncio.to_thread(request, port, PREFIX + "/start", worker_key))[0], 409
                )

    async def test_invalid_sequence_releases_session(self):
        """Functionality: Reject a foreign first sequence before any GPU allocation.
        Inputs: real WS index1 first packet; Outputs:1008 and cleared admission.
        Logic: actual binary parser enforces index0; Constraints: no worker/GPU executes.
        """
        async with serve_transport() as (broker, port):
            async with connect(
                f"ws://127.0.0.1:{port}{PREFIX}/ws", origin=ORIGIN, proxy=None
            ) as peer:
                await peer.recv()
                await peer.send(pack_request(1, jpeg()))
                with self.assertRaises(ConnectionClosedError) as caught:
                    await peer.recv()
                self.assertEqual(caught.exception.rcvd.code, 1008)
                self.assertIsNone(broker.session)

    async def test_worker_failure_reaches_peer(self):
        """Functionality: Surface predictor failure as1011 and original worker exception.
        Inputs: fake failing predictor with actual loopback bridge; Outputs: close and no result.
        Logic: reject prediction, end marker stops peer, no retry; Constraints: mocked GPU failure.
        """

        # Functionality: Fail test worker construction. Inputs: unused model/manifest.
        # Outputs: RuntimeError; Logic: immediate failure; Constraints: no real model/GPU operation.
        def broken(model, manifest):
            raise RuntimeError("Synthetic allocated detector failure")

        async with serve_transport() as (broker, port):
            async with connect(
                f"ws://127.0.0.1:{port}{PREFIX}/ws", origin=ORIGIN, proxy=None
            ) as peer:
                await peer.recv()
                worker = asyncio.create_task(
                    asyncio.to_thread(
                        run_worker, broker.session.worker_capability, None, None, port, broken
                    )
                )
                with self.assertRaises(ConnectionClosedError) as caught:
                    await asyncio.wait_for(peer.recv(), 3)
                self.assertEqual(caught.exception.rcvd.code, 1011)
                with self.assertRaises(RuntimeError):
                    await worker

    async def test_allocated_deadline(self):
        """Functionality: Stop frame pulling at the allocation-bound session deadline.
        Inputs: synthetic elapsed start beyond55s with actual HTTP/WS; Outputs:410 and WS1000.
        Logic: patch only parent's start clock; Constraints: no delayed real test or GPU assertion.
        """
        async with serve_transport() as (broker, port):
            async with connect(
                f"ws://127.0.0.1:{port}{PREFIX}/ws", origin=ORIGIN, proxy=None
            ) as peer:
                await peer.recv()
                capability = broker.session.worker_capability
                broker.session.started = time.monotonic() - SESSION_SECONDS - 1
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                try:
                    # Functionality: Pull expired parent session off the event loop.
                    # Inputs: enclosing connection/token; Outputs: HTTP410; Logic: one GET.
                    # Constraints: synthetic start time, not a GPU allocation timeout test.
                    def pull():
                        connection.request(
                            "GET",
                            PREFIX + "/next",
                            headers={"Authorization": "Bearer " + capability},
                        )
                        response = connection.getresponse()
                        response.read()
                        return response.status

                    self.assertEqual(await asyncio.to_thread(pull), 410)
                    with self.assertRaises(ConnectionClosedOK):
                        await peer.recv()
                finally:
                    connection.close()

    async def test_excess_inflight_rejected(self):
        """Functionality: Reject oversending instead of silently dropping or growing input queues.
        Inputs: thirteen outstanding WS frames with no worker; Outputs:1008/cleared session.
        Logic: exceed advertised twelve-frame credit limit; Constraints: no GPU/model processing.
        """
        async with serve_transport() as (broker, port):
            async with connect(
                f"ws://127.0.0.1:{port}{PREFIX}/ws", origin=ORIGIN, proxy=None
            ) as peer:
                await peer.recv()
                for index in range(MAX_INFLIGHT + 1):
                    await peer.send(pack_request(index, jpeg()))
                with self.assertRaises(ConnectionClosedError) as caught:
                    await peer.recv()
                self.assertEqual(caught.exception.rcvd.code, 1008)
                self.assertIsNone(broker.session)

    async def test_warmup_acknowledgement_and_failures(self):
        """Functionality: Require one exact echo and fail on corrupt/late/duplicate ACK.
        Inputs: Real loopback WS/HTTP, FakeDetector and explicit0.2s control deadline substitute.
        Outputs: One image count, unchanged JPEG, released control bytes and failure closes.
        Logic: Withhold image0 until ACK; mutate one control byte or repeat the ACK;
        await worker release. No retry or extra prediction follows a control failure.
        Constraints: Synthetic timeout is only a test seam, not WAN warmup or GPU FPS evidence.
        """
        for mode in ("valid", "changed", "duplicate", "timeout"):
            with self.subTest(mode=mode), patch("zerogpu.live_transport.WARMUP_SECONDS", 0.2):
                async with serve_transport() as (broker, port):
                    async with connect(
                        f"ws://127.0.0.1:{port}{PREFIX}/ws", origin=ORIGIN,
                        proxy=None, compression=None,
                    ) as peer:
                        hello = json.loads(await peer.recv())
                        self.assertEqual(hello["warmup_bytes"], WARMUP_BYTES)
                        worker = asyncio.create_task(asyncio.to_thread(
                            run_worker, broker.session.worker_capability,
                            None, None, port, FakeDetector))
                        await peer.send(pack_request(0, jpeg()))
                        control = await asyncio.wait_for(peer.recv(), 2)
                        self.assertEqual(len(control), WARMUP_BYTES)
                        self.assertTrue(control.startswith(WARMUP_PREFIX))
                        session = broker.session
                        self.assertEqual((session.received, session.completed, session.sent),
                                         (1, 1, 0))
                        if mode == "changed":
                            await peer.send(control[:-1] + bytes([control[-1] ^ 1]))
                        elif mode != "timeout":
                            await peer.send(control)
                            result = await asyncio.wait_for(peer.recv(), 2)
                            self.assertEqual(unpack_result(result, 0)[1], jpeg())
                            self.assertEqual((session.received, session.sent), (1, 1))
                            self.assertIsNone(session.warmup_packet)
                            if mode == "duplicate":
                                await peer.send(control)
                            else:
                                await peer.send('{"kind":"stop"}')
                        if mode != "valid":
                            with self.assertRaises(ConnectionClosedError) as caught:
                                await asyncio.wait_for(peer.recv(), 2)
                            self.assertEqual(caught.exception.rcvd.code,
                                             1011 if mode == "timeout" else 1008)
                        summary = await asyncio.wait_for(worker, 2)
                        self.assertEqual(summary["frames"], 1)
                        self.assertIsNone(broker.session)

    async def test_idle_releases_worker(self):
        """Functionality: Release an allocated fake worker when live input never arrives.
        Inputs: real WS/HTTP plus synthetic0.05s idle limit and FakeDetector; Outputs: clean close.
        Logic: exercise idle branch and completed worker; Constraints: only timeout is mocked,
        no real HF/GPU or production5s latency/quota claim; prediction is never invoked.
        """
        async with serve_transport() as (broker, port):
            async with connect(
                f"ws://127.0.0.1:{port}{PREFIX}/ws", origin=ORIGIN, proxy=None
            ) as peer:
                await peer.recv()
                with patch("zerogpu.live_transport.IDLE_SECONDS", 0.05):
                    worker = asyncio.create_task(
                        asyncio.to_thread(
                            run_worker,
                            broker.session.worker_capability,
                            None,
                            None,
                            port,
                            FakeDetector,
                        )
                    )
                    with self.assertRaises(ConnectionClosedOK):
                        await asyncio.wait_for(peer.recv(), 2)
                    self.assertEqual((await asyncio.wait_for(worker, 2))["frames"], 0)
                self.assertIsNone(broker.session)
