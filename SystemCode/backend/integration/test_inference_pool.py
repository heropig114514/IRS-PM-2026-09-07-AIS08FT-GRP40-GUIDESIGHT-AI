"""Responsibilities: Verify bounded multi-phone routing and independent native-worker leases.
Implementation: Real pool with controlled ASGI queues, health snapshots and upstream sockets;
no model, GPU, camera, actual sockets or remote services are used.
Related Modules: integration.inference.pool/pool_asgi; tools.run_phone_mvp.
Declaration Index: healthy; scope; Peer; Peer.__init__; Peer.send; Peer.recv; Peer.__aenter__;
Peer.__aexit__; Fixture; Fixture.__init__; Fixture.read; Fixture.open; Fixture.connection;
Fixture.connection.receive; Fixture.connection.send; Fixture.wait; PoolConfigurationTests;
PoolConfigurationTests.test_ports; PoolConfigurationTests.test_health_reads;
PoolTests; PoolTests.setUp; PoolTests.test_three_streams_and_capacity;
PoolTests.test_drain_holds_lease; PoolTests.test_cancellation_waits_for_native_idle;
PoolTests.test_cleanup_timeout_quarantines_only_one_slot; PoolTests.test_model_mismatch;
PoolTests.test_unavailable_slot_and_loaded_observations; PoolTests.test_boundaries;
PoolTests.test_http_and_lifespan; PoolTests.test_http_and_lifespan.send;
PoolTests.test_invalid_payload_and_upstream_failure.
Variable Index: fixture queues retain synthetic strings/bytes only; no algorithm state is replaced.
"""

import asyncio
import copy
import importlib
import json
import os
from unittest import TestCase
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from integration.inference.pool import InferencePool, backend_health, health_identity, pool_ports

PORTS = (8768, 8769, 8770)
ORIGIN = "http://127.0.0.1:18766"


def healthy():
    """Functionality: Provide a complete synthetic native-health identity. Inputs: none.
    Outputs: independent mapping. Logic: explicit stable fingerprints. Constraints: no model result.
    """
    return {
        "status": "ok",
        "provider": "local",
        "loaded": True,
        "ready": True,
        "busy": False,
        "accept_available": True,
        "error_code": None,
        "models": {
            "detection": {
                "model": "fixture",
                "classes": 87,
                "expected_sha256": "a" * 64,
                "verified_sha256": "a" * 64,
            },
            "depth": {"sha256": "b" * 64, "backend": "torch", "fusion": "host"},
        },
        "distance_mode": "depth",
        "depth_interval": 1,
        "session_seconds": None,
        "capabilities": {"detection": True, "tracking": True, "risk": True, "depth": True},
    }


def scope(kind="websocket", **changes):
    """Functionality: Define canonical local transport admission. Inputs: scope overrides.
    Outputs: independent ASGI scope. Logic: fixed allowed authority/origin. Constraints: no network.
    """
    value = {
        "type": kind,
        "path": "/ws/infer/" if kind == "websocket" else "/health/",
        "client": ("127.0.0.1", 10000),
        "server": ("127.0.0.1", 8767),
        "query_string": b"",
        "method": "GET",
        "headers": [(b"host", b"127.0.0.1:8767"), (b"origin", ORIGIN.encode())],
    }
    return {**value, **changes}


class Peer:
    """Functionality: Supply an independent upstream connection with observable affinity.
    Logic: echo each original input through a port-tagged output queue. Constraints: no inference.
    """

    def __init__(self, port):
        """Functionality: Create one upstream socket fixture. Inputs: fixed port.
        Outputs: queues/history. Logic: unique hello. Constraints: synthetic protocol data only.
        """
        self.port, self.sent = port, []
        self.outgoing = asyncio.Queue()
        self.outgoing.put_nowait(json.dumps({"kind": "session", "session_id": str(port)}))
        self.closed = asyncio.Event()

    async def send(self, payload):
        """Functionality: Observe unchanged client data. Inputs: text/bytes.
        Outputs: tagged synthetic reply. Logic: one queue per peer. Constraints: no target state.
        """
        self.sent.append(payload)
        await self.outgoing.put(
            (str(self.port) + ":").encode() + payload
            if isinstance(payload, bytes)
            else str(self.port) + ":" + payload
        )

    async def recv(self):
        """Functionality: Wait for one upstream message. Inputs: own queue.
        Outputs: original queued fixture. Logic: ordered wait. Constraints: no timeout simulation.
        """
        return await self.outgoing.get()

    async def __aenter__(self):
        """Functionality: Enter one socket context. Inputs: none. Outputs: this peer.
        Logic: no connection work. Constraints: no implicit second peer.
        """
        return self

    async def __aexit__(self, *_):
        """Functionality: Observe socket closure. Inputs: context exit details.
        Outputs: closed event. Logic: record exactly this peer. Constraints: no native idle claim.
        """
        self.closed.set()


class Fixture:
    """Functionality: Bind production pool logic to isolated fake transports.
    Logic: explicit per-port health and peer queues. Constraints: no real runtime allocation.
    """

    def __init__(self, **options):
        """Functionality: Construct health/transport dependencies. Inputs: cleanup bounds.
        Outputs: production pool and fixtures. Logic: keep backend values independent.
        Constraints: no setup I/O or process start.
        """
        self.health = {port: healthy() for port in PORTS}
        self.peers, self.calls = {}, []
        self.pool = InferencePool(
            PORTS,
            reader=self.read,
            opener=self.open,
            drain_seconds=options.get("drain_seconds", 1),
            poll_seconds=0.001,
        )

    def read(self, port):
        """Functionality: Read a controlled health snapshot. Inputs: port.
        Outputs: copied state or explicit error. Logic: no hidden state changes.
        Constraints: worker completion is controlled by each test.
        """
        if isinstance(self.health[port], Exception):
            raise self.health[port]
        return copy.deepcopy(self.health[port])

    def open(self, url, **options):
        """Functionality: Observe the one selected backend connection. Inputs: fixed URL/options.
        Outputs: independent peer. Logic: parse only the selected fixture port.
        Constraints: no redirects, retries or real socket.
        """
        port = int(url.split(":")[2].split("/")[0])
        self.calls.append((url, options))
        peer = Peer(port)
        self.peers[port] = peer
        return peer

    def connection(self, **changes):
        """Functionality: Start one synthetic client. Inputs: optional scope changes.
        Outputs: task, incoming queue and sent events. Logic: invoke the real ASGI dispatcher.
        Constraints: source data is never shared between clients.
        """
        incoming, outgoing = asyncio.Queue(), []
        incoming.put_nowait({"type": "websocket.connect"})

        async def receive():
            """Functionality: Deliver ordered ASGI input. Inputs: fixture queue.
            Outputs: next event. Logic: await exactly one. Constraints: no automatic disconnect.
            """
            return await incoming.get()

        async def send(event):
            """Functionality: Record one client output. Inputs: ASGI event.
            Outputs: isolated history. Logic: append once. Constraints: no other client visibility.
            """
            outgoing.append(event)

        return asyncio.create_task(self.pool(scope(**changes), receive, send)), incoming, outgoing

    async def wait(self, condition):
        """Functionality: Bound async fixture scheduling. Inputs: state predicate.
        Outputs: None when true. Logic: yield until one-second deadline.
        Constraints: no network retry or measured-performance claim.
        """
        async with asyncio.timeout(2):
            while not condition():
                await asyncio.sleep(0.001)


class PoolConfigurationTests(TestCase):
    """Functionality: Check explicit process selection and bounded health reads.
    Logic: standard-library mocks only. Constraints: no runtime side effects.
    """

    def test_ports(self):
        """Functionality: Accept only two/three unique numeric ports. Inputs: valid/invalid CSV.
        Outputs: tuple or failure. Logic: boundary matrix. Constraints: no port probing.
        """
        self.assertEqual(pool_ports("8768,8769"), (8768, 8769))
        self.assertEqual(pool_ports("8768,8769,8770"), PORTS)
        for value in (
            None,
            "",
            "8768",
            "8768,8768",
            "8768, 8769",
            "08768,8769",
            "http://127.0.0.1:8768,8769",
            "1023,8768",
            "8768,65536",
            "8768,8769,8770,8771",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                pool_ports(value)
        with patch.dict(os.environ, {"PRS_INFERENCE_POOL_PORTS": "8768,8769,8770"}):
            module = importlib.import_module("integration.inference.pool_asgi")
            self.assertEqual(len(module.application.slots), 3)

    def test_health_reads(self):
        """Functionality: Bound original native health reads and validate model fingerprints.
        Inputs: fixed mock HTTP response and malformed identity values. Outputs: explicit failures.
        Logic: one GET with closed connection. Constraints: no redirects, retries or model import.
        """
        connection = Mock()
        response = connection.getresponse.return_value
        response.status, response.read.return_value = 200, json.dumps(healthy()).encode()
        with patch(
            "integration.inference.pool.http.client.HTTPConnection", return_value=connection
        ):
            self.assertEqual(backend_health(8768), healthy())
        connection.request.assert_called_once_with("GET", "/health/")
        connection.close.assert_called_once()
        for raw, status in ((b"x" * 65537, 200), (b"{}", 302)):
            response.read.return_value, response.status = raw, status
            with (
                patch(
                    "integration.inference.pool.http.client.HTTPConnection", return_value=connection
                ),
                self.assertRaises(ValueError),
            ):
                backend_health(8768)
        for value in ({}, {**healthy(), "busy": 0}, {**healthy(), "models": {}}, None):
            with self.assertRaises(ValueError):
                health_identity(value)
        invalid = healthy()
        invalid["models"]["detection"]["verified_sha256"] = "c" * 64
        with self.assertRaises(ValueError):
            health_identity(invalid)


class PoolTests(SimpleTestCase):
    """Functionality: Verify three-stream affinity, lifecycle isolation and admission boundaries.
    Logic: real async dispatcher with per-client queues. Constraints: no CUDA or media accuracy
    claim.
    """

    def setUp(self):
        """Functionality: Pin allowed local software Origin. Inputs: test environment.
        Outputs: restored configuration after test. Logic: explicit patch.
        Constraints: no secrets, external endpoint or persistent settings.
        """
        patcher = patch.dict(os.environ, {"LOCAL_WEB_ORIGIN": ORIGIN})
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_three_streams_and_capacity(self):
        """Functionality: Keep three independent streams while rejecting a fourth immediately.
        Inputs: interleaved raw bytes/text and disconnects. Outputs: exact per-backend data.
        Logic: reserve whole sockets, then reuse only a fully idle slot.
        Constraints: no per-frame routing, timestamp rewrite or implicit reconnect.
        """
        f = Fixture()
        clients = [f.connection() for _ in range(3)]
        await f.wait(lambda: all(len(events) >= 2 for _, _, events in clients))
        leased = [int(json.loads(events[1]["text"])["session_id"]) for _, _, events in clients]
        self.assertEqual(set(leased), set(PORTS))
        fourth, _, messages = f.connection()
        await asyncio.wait_for(fourth, 2)
        self.assertEqual(json.loads(messages[1]["text"])["code"], "local_busy")
        for index, (_, queue, _) in enumerate(clients):
            queue.put_nowait(
                {"type": "websocket.receive", "bytes": b"PRF2-source-" + bytes([index])}
            )
        await f.wait(lambda: all(peer.sent for peer in f.peers.values()))
        for index, port in enumerate(leased):
            self.assertEqual(f.peers[port].sent, [b"PRF2-source-" + bytes([index])])
        clients[0][1].put_nowait({"type": "websocket.disconnect"})
        await clients[0][0]
        self.assertEqual((await f.pool.inspect())["active_sessions"], 2)
        clients[1][1].put_nowait({"type": "websocket.receive", "text": "stop-control"})
        await f.wait(lambda: "stop-control" in f.peers[leased[1]].sent)
        self.assertFalse(clients[1][0].done())
        for task, queue, _ in clients[1:]:
            queue.put_nowait({"type": "websocket.disconnect"})
            await task
        report = await f.pool.inspect()
        self.assertEqual(
            (report["capacity"], report["active_sessions"], report["available_sessions"]), (3, 0, 3)
        )
        self.assertTrue(report["ready"])
        for _, options in f.calls:
            self.assertIsNone(options["proxy"])
            self.assertIsNone(options["compression"])
            self.assertEqual(options["origin"], ORIGIN)

    async def test_drain_holds_lease(self):
        """Functionality: Keep a disconnected worker reserved until its native GPU becomes idle.
        Inputs: busy health after peer close. Outputs: retained then released lease.
        Logic: separate socket close from native completion. Constraints: no hidden slot reuse.
        """
        f = Fixture()
        task, queue, _ = f.connection()
        await f.wait(lambda: bool(f.peers))
        f.health[8768]["busy"] = True
        queue.put_nowait({"type": "websocket.disconnect"})
        await f.peers[8768].closed.wait()
        self.assertTrue(f.pool.slots[0].reserved)
        self.assertFalse(task.done())
        f.health[8768]["busy"] = False
        await task
        self.assertFalse(f.pool.slots[0].reserved)

    async def test_cancellation_waits_for_native_idle(self):
        """Functionality: Preserve native ownership through repeated outer cancellations.
        Inputs: active lease, busy backend, two task cancellations. Outputs: drained cancelled task.
        Logic: cleanup is shielded. Constraints: cancellation never means GPU completion.
        """
        f = Fixture()
        task, _, _ = f.connection()
        await f.wait(lambda: bool(f.peers))
        f.health[8768]["busy"] = True
        task.cancel()
        await f.peers[8768].closed.wait()
        task.cancel()
        await asyncio.sleep(0.01)
        self.assertTrue(f.pool.slots[0].reserved)
        f.health[8768]["busy"] = False
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(f.pool.slots[0].reserved)
        self.assertFalse(f.pool.slots[0].quarantined)

    async def test_cleanup_timeout_quarantines_only_one_slot(self):
        """Functionality: Seal a stuck backend without stopping unrelated streams.
        Inputs: short explicit cleanup deadline and permanently busy native fixture.
        Outputs: quarantined slot, degraded health and available other workers.
        Logic: operator recovery remains explicit. Constraints: no restart or automatic
        unquarantine.
        """
        f = Fixture(drain_seconds=0.03)
        task, queue, _ = f.connection()
        await f.wait(lambda: bool(f.peers))
        f.health[8768]["busy"] = True
        queue.put_nowait({"type": "websocket.disconnect"})
        await task
        report = await f.pool.inspect()
        self.assertEqual(report["status"], "degraded")
        self.assertEqual(report["available_sessions"], 2)
        self.assertTrue(f.pool.slots[0].quarantined)
        f.health[8768]["busy"] = False
        self.assertEqual((await f.pool.inspect())["available_sessions"], 2)
        next_task, next_queue, _ = f.connection()
        await f.wait(lambda: 8769 in f.peers)
        next_queue.put_nowait({"type": "websocket.disconnect"})
        await next_task

    async def test_model_mismatch(self):
        """Functionality: Reject a pool with inconsistent configured or observed model identity.
        Inputs: differing detector and depth fingerprints. Outputs: explicit mismatch error.
        Logic: compare all healthy backend identities before admission. Constraints: no
        substitution.
        """
        for model in ("detection", "depth"):
            f = Fixture()
            if model == "detection":
                f.health[8769]["models"][model]["model"] = "different"
            else:
                f.health[8769]["models"][model]["sha256"] = "c" * 64
            report = await f.pool.inspect()
            self.assertFalse(report["ready"])
            self.assertEqual(report["available_sessions"], 0)
            task, _, events = f.connection()
            await task
            self.assertEqual(json.loads(events[1]["text"])["code"], "pool_model_mismatch")
            self.assertEqual(f.calls, [])

    async def test_unavailable_slot_and_loaded_observations(self):
        """Functionality: Distinguish lazy loading from missing or invalid backends.
        Inputs: one unloaded worker, then one failed health reader. Outputs: honest readiness.
        Logic: configured identity ignores absent observed hashes only. Constraints: no stale
        health.
        """
        f = Fixture()
        f.health[8769]["loaded"] = f.health[8769]["ready"] = False
        f.health[8769]["models"]["detection"]["verified_sha256"] = None
        f.health[8769]["models"]["depth"]["sha256"] = None
        report = await f.pool.inspect()
        self.assertEqual(report["status"], "ok")
        self.assertFalse(report["ready"])
        self.assertEqual(report["available_sessions"], 3)
        f.health[8769] = OSError("private path must not be exposed")
        report = await f.pool.inspect()
        self.assertEqual(report["available_sessions"], 2)
        self.assertNotIn("private path", json.dumps(report))

    async def test_boundaries(self):
        """Functionality: Reject external/ambiguous authorities and paths before upstream access.
        Inputs: Origin/Host/peer/path/query matrices. Outputs: fixed policy close.
        Logic: exact original admission. Constraints: no public bind or Host forwarding.
        """
        f = Fixture()
        cases = (
            {"path": "/ws/unknown/"},
            {"query_string": b"x=1"},
            {"client": ("10.0.0.1", 1)},
            {"server": ("0.0.0.0", 8767)},
            {"server": ("127.0.0.1", 8768)},
            {"headers": []},
            {"headers": [(b"host", b"evil.test"), (b"origin", ORIGIN.encode())]},
            {"headers": [(b"host", b"127.0.0.1:8767")]},
            {"headers": [(b"host", b"127.0.0.1:8767"), (b"origin", b"http://evil.test")]},
            {"headers": [(b"host", b"127.0.0.1:8767")] * 2 + [(b"origin", ORIGIN.encode())]},
            {
                "headers": [(b"host", b"127.0.0.1:8767"), (b"origin", ORIGIN.encode())]
                + [(b"origin", ORIGIN.encode())]
            },
        )
        for values in cases:
            task, _, events = f.connection(**values)
            await task
            self.assertEqual(events, [{"type": "websocket.close", "code": 1008}])
        self.assertEqual(f.calls, [])

    async def test_http_and_lifespan(self):
        """Functionality: Preserve remote tunnel health authority and ordinary lifecycle.
        Inputs: loopback GET/invalid method and startup/shutdown events. Outputs: bounded health.
        Logic: exact routes with no native start. Constraints: launcher retains process ownership.
        """
        f, events = Fixture(), []

        async def send(event):
            """Functionality: Record ASGI output. Inputs: event. Outputs: history.
            Logic: append once. Constraints: no response mutation.
            """
            events.append(event)

        await f.pool(scope("http", headers=[(b"host", b"127.0.0.1:18767")]), Mock(), send)
        self.assertEqual(events[0]["status"], 200)
        self.assertEqual(json.loads(events[1]["body"])["capacity"], 3)
        events.clear()
        await f.pool(scope("http", method="POST"), Mock(), send)
        self.assertEqual(events[0]["status"], 405)
        events.clear()
        await f.pool(scope("http", query_string=b"x"), Mock(), send)
        self.assertEqual(events[0]["status"], 403)
        queue = asyncio.Queue()
        queue.put_nowait({"type": "lifespan.startup"})
        queue.put_nowait({"type": "lifespan.shutdown"})
        events.clear()
        await f.pool({"type": "lifespan"}, queue.get, send)
        self.assertEqual(
            events, [{"type": "lifespan.startup.complete"}, {"type": "lifespan.shutdown.complete"}]
        )

    async def test_invalid_payload_and_upstream_failure(self):
        """Functionality: Reject invalid input and surface upstream failures without retry.
        Inputs: empty packet and failing socket opener. Outputs: safe error and released lease.
        Logic: close transport before idle-health observation. Constraints: no leaked exceptions.
        """
        f = Fixture()
        task, queue, events = f.connection()
        await f.wait(lambda: bool(f.peers))
        queue.put_nowait({"type": "websocket.receive", "bytes": b""})
        await task
        self.assertEqual(json.loads(events[-2]["text"])["code"], "local_packet_invalid")
        self.assertFalse(f.pool.slots[0].reserved)
        f.pool.opener = Mock(side_effect=OSError("private diagnostic"))
        task, _, events = f.connection()
        await task
        self.assertEqual(json.loads(events[-2]["text"])["code"], "pool_backend_unavailable")
        self.assertNotIn("private diagnostic", json.dumps(events))
        f.pool.opener.assert_called_once()
