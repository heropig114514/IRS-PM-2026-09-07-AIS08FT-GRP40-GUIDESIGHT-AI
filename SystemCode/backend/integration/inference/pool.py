"""Responsibilities: Route isolated phone streams to explicit loopback inference processes.
Implementation: One backend lease per WebSocket; bounded transparent forwarding and native-drain
observation. No Torch imports, per-frame routing, protocol rewriting or automatic reconnects.
Related Modules: integration.inference.pool_asgi/services; tools.run_phone_mvp;
integration.protocol.
Declaration Index: pool_ports; backend_health; health_identity; PoolSlot; InferencePool;
InferencePool.__init__; InferencePool.inspect; InferencePool.acquire; InferencePool.release;
InferencePool.allowed; InferencePool.terminal; InferencePool.upload; InferencePool.download;
InferencePool.proxy; InferencePool.websocket; InferencePool.respond; InferencePool.__call__.
Variable Index: HEALTH_BYTES/HEALTH_SECONDS bound local health reads; DRAIN_SECONDS/POLL_SECONDS
bound post-disconnect observations; slots retain only reservation/quarantine and scalar health.
"""

import asyncio
import copy
import http.client
import json
import logging
import re
from dataclasses import dataclass

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedOK

from integration.errors import IntegrationError
from integration.protocol import MAX_INFLIGHT, is_loopback, web_origins
from realtime.protocol import MAX_PACKET_BYTES

logger = logging.getLogger(__name__)
HEALTH_BYTES, HEALTH_SECONDS = 65536, 2
DRAIN_SECONDS, POLL_SECONDS = 55, 0.1


def pool_ports(value):
    """Functionality: Validate an explicitly selected local process pool.
    Inputs: Strict CSV of two or three TCP ports. Outputs: distinct integer tuple.
    Logic: accept decimal ports only. Constraints: no URL, whitespace, duplicates or discovery.
    """
    if not isinstance(value, str) or not re.fullmatch(
        r"[1-9][0-9]{3,4}(,[1-9][0-9]{3,4}){1,2}", value
    ):
        raise ValueError("PRS_INFERENCE_POOL_PORTS requires two or three distinct loopback ports")
    ports = tuple(int(part) for part in value.split(","))
    if len(set(ports)) != len(ports) or any(not 1024 <= port <= 65535 for port in ports):
        raise ValueError("Inference pool ports must be distinct values between 1024 and 65535")
    return ports


def backend_health(port):
    """Functionality: Read one fixed native backend without executing inference.
    Inputs: validated loopback port. Outputs: bounded parsed health.
    Logic: direct HTTP only, no redirects/proxies. Constraints: one request, no retry or raw logs.
    """
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=HEALTH_SECONDS)
    try:
        connection.request("GET", "/health/")
        response = connection.getresponse()
        raw = response.read(HEALTH_BYTES + 1)
        if response.status != 200 or len(raw) > HEALTH_BYTES:
            raise ValueError("Invalid pool backend health response")
        return json.loads(raw)
    finally:
        connection.close()


def health_identity(body):
    """Functionality: Validate and compare configured identities independently of lazy load state.
    Inputs: backend health dictionary. Outputs: immutable identity tuple.
    Logic: freeze expected detector, depth and capture policy; verify observed hashes when present.
    Constraints: a loaded/unloaded pair may differ in observations, never model configuration.
    """
    if (
        not isinstance(body, dict)
        or body.get("status") != "ok"
        or body.get("provider") != "local"
        or any(
            type(body.get(key)) is not bool
            for key in ("loaded", "ready", "busy", "accept_available")
        )
    ):
        raise ValueError("Invalid pool backend health contract")
    models = body.get("models")
    if not isinstance(models, dict):
        raise ValueError("Missing pool backend identity")
    detection, depth = models.get("detection"), models.get("depth")
    if not isinstance(detection, dict) or not isinstance(depth, dict):
        raise ValueError("Missing pool model identity")
    expected = detection.get("expected_sha256")
    if (
        not isinstance(expected, str)
        or not re.fullmatch(r"[0-9a-f]{64}", expected)
        or not isinstance(detection.get("model"), str)
        or type(detection.get("classes")) is not int
        or detection.get("verified_sha256") not in (None, expected)
    ):
        raise ValueError("Invalid pool model fingerprint")
    depth_hash = depth.get("sha256")
    if depth_hash is not None and (
        not isinstance(depth_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", depth_hash)
    ):
        raise ValueError("Invalid pool depth fingerprint")
    if (
        depth.get("backend") not in {None, "torch", "tensorrt"}
        or depth.get("fusion") not in {None, "host", "gpu"}
        or body.get("distance_mode") not in {"depth", "geometry"}
        or type(body.get("depth_interval")) is not int
        or body.get("session_seconds") is not None
        and type(body["session_seconds"]) is not int
    ):
        raise ValueError("Invalid pool pipeline identity")
    return (
        detection["model"],
        detection["classes"],
        expected,
        depth.get("backend"),
        depth.get("fusion"),
        body.get("distance_mode"),
        body.get("depth_interval"),
        body.get("session_seconds"),
        json.dumps(body.get("capabilities"), sort_keys=True),
    )


@dataclass
class PoolSlot:
    """Functionality: Keep one process reservation and explicit operator-recovery state.
    Logic: each slot owns a fixed port. Constraints: no images, target histories or credentials.
    """

    port: int
    reserved: bool = False
    quarantined: bool = False
    health: dict | None = None
    error_code: str | None = None


class InferencePool:
    """Functionality: Lease independent algorithm processes to concurrent phone streams.
    Logic: session affinity and fail-closed identity validation, with original frame bytes.
    Constraints: pool does not own model state or perform GPU work; capacity is explicit.
    """

    def __init__(
        self,
        ports,
        *,
        reader=backend_health,
        opener=connect,
        drain_seconds=DRAIN_SECONDS,
        poll_seconds=POLL_SECONDS,
    ):
        """Functionality: Freeze explicit backends and injectable transport adapters.
        Inputs: validated ports, health reader and socket opener. Outputs: idle process pool.
        Logic: verify ports before creating slots. Constraints: construction has no network/GPU I/O.
        """
        ports = pool_ports(",".join(str(port) for port in ports))
        if drain_seconds <= 0 or poll_seconds < 0:
            raise ValueError("Pool cleanup bounds must be positive")
        self.slots = [PoolSlot(port) for port in ports]
        self.reader, self.opener = reader, opener
        self.drain_seconds, self.poll_seconds = drain_seconds, poll_seconds
        self.lock = asyncio.Lock()

    async def inspect(self):
        """Functionality: Aggregate bounded per-process health and current reservations.
        Inputs: configured slots. Outputs: compatible health plus capacity and per-slot diagnostics.
        Logic: read each backend once concurrently; mismatched known identities stop all admission.
        Constraints: unavailable/quarantined slots remain explicit, never replaced or restarted.
        """
        results = await asyncio.gather(
            *(asyncio.to_thread(self.reader, slot.port) for slot in self.slots),
            return_exceptions=True,
        )
        identities, healthy = [], []
        async with self.lock:
            for slot, result in zip(self.slots, results, strict=True):
                try:
                    identity = health_identity(result)
                    slot.health = copy.deepcopy(result)
                    if not slot.quarantined:
                        slot.error_code = result.get("error_code")
                    identities.append(identity)
                    healthy.append(slot)
                except (TypeError, ValueError, KeyError):
                    slot.health = None
                    if not slot.quarantined:
                        slot.error_code = "pool_backend_unavailable"
            mismatch = len(set(identities)) > 1
            depth_hashes = {slot.health["models"]["depth"].get("sha256") for slot in healthy}
            depth_hashes.discard(None)
            mismatch |= len(depth_hashes) > 1
            available = (
                sum(
                    not slot.reserved
                    and not slot.quarantined
                    and slot.health is not None
                    and slot.health["accept_available"]
                    and not slot.health["busy"]
                    and slot.error_code is None
                    for slot in self.slots
                )
                if not mismatch
                else 0
            )
            active = sum(slot.reserved for slot in self.slots)
            good = (
                len(healthy) == len(self.slots)
                and not mismatch
                and all(not slot.quarantined and slot.error_code is None for slot in self.slots)
            )
            base = copy.deepcopy(healthy[0].health) if healthy else {"models": {}}
            base.update(
                status="ok" if good else "degraded",
                provider="local",
                loaded=bool(healthy)
                and all(slot.health["loaded"] for slot in healthy)
                and len(healthy) == len(self.slots),
                ready=good and all(slot.health["ready"] for slot in self.slots),
                busy=available == 0,
                accept_available=available > 0,
                capacity=len(self.slots),
                active_sessions=active,
                available_sessions=available,
                error_code="pool_model_mismatch"
                if mismatch
                else (None if good else "pool_backend_unavailable"),
                last_inference=None,
                workers=[
                    {
                        "slot": index,
                        "reserved": slot.reserved,
                        "quarantined": slot.quarantined,
                        "error_code": slot.error_code,
                        "ready": bool(slot.health and slot.health["ready"]),
                        "busy": bool(slot.health and slot.health["busy"]),
                    }
                    for index, slot in enumerate(self.slots)
                ],
            )
            return base

    async def acquire(self):
        """Functionality: Reserve one available configured worker for a whole socket.
        Inputs: current backend health. Outputs: slot or fixed capacity/health failure.
        Logic: lock reservation after fresh health reads. Constraints: no waiting queue or
        rerouting.
        """
        status = await self.inspect()
        async with self.lock:
            if status["error_code"] == "pool_model_mismatch":
                raise IntegrationError(
                    "pool_model_mismatch",
                    "Configured inference workers have different model identities.",
                )
            for slot in self.slots:
                if (
                    not slot.reserved
                    and not slot.quarantined
                    and slot.health is not None
                    and slot.health["accept_available"]
                    and not slot.health["busy"]
                    and slot.error_code is None
                ):
                    slot.reserved = True
                    return slot
            if status["error_code"]:
                raise IntegrationError(
                    "pool_backend_unavailable", "An inference worker requires operator attention."
                )
            raise IntegrationError("local_busy", "Local inference is busy.")

    async def release(self, slot):
        """Functionality: Release a lease only after native GPU work has finished.
        Inputs: owned slot after socket closure. Outputs: None or safe quarantine code.
        Logic: bounded idle-health observations; failures/timeouts seal this slot until restart.
        Constraints: observations never resend frames, reconnect a socket or free busy native state.
        """
        deadline = asyncio.get_running_loop().time() + self.drain_seconds
        error = "pool_cleanup_timeout"
        try:
            while asyncio.get_running_loop().time() < deadline:
                remaining = deadline - asyncio.get_running_loop().time()
                body = await asyncio.wait_for(asyncio.to_thread(self.reader, slot.port), remaining)
                health_identity(body)
                if not body["busy"]:
                    async with self.lock:
                        slot.health, slot.reserved = copy.deepcopy(body), False
                    return None
                await asyncio.sleep(
                    min(self.poll_seconds, max(0, deadline - asyncio.get_running_loop().time()))
                )
        except TimeoutError:
            error = "pool_cleanup_timeout"
        except Exception:
            error = "pool_cleanup_failed"
        async with self.lock:
            slot.reserved, slot.quarantined, slot.error_code = False, True, error
        logger.error("Inference pool slot quarantined slot_port=%d code=%s", slot.port, error)
        return error

    def allowed(self, scope, *, websocket=False):
        """Functionality: Admit only canonical loopback health/inference traffic.
        Inputs: ASGI scope. Outputs: boolean. Logic: exact Host/Origin and fixed paths/query policy.
        Constraints: the assigned remote SSH health authority is explicit; no wildcard public hosts.
        """
        client, server = scope.get("client", (None, 0)), scope.get("server", (None, 0))
        if (
            not is_loopback(client[0])
            or not is_loopback(server[0])
            or scope.get("query_string")
            or server[1] in {slot.port for slot in self.slots}
        ):
            return False
        headers = scope.get("headers", [])
        hosts = [value for key, value in headers if key == b"host"]
        origins = [value for key, value in headers if key == b"origin"]
        accepted_hosts = {f"127.0.0.1:{server[1]}".encode(), b"127.0.0.1:18767"}
        if len(hosts) != 1 or hosts[0] not in accepted_hosts:
            return False
        if len(origins) > 1 or websocket and len(origins) != 1:
            return False
        if origins and origins[0] not in {value.encode() for value in web_origins()}:
            return False
        return scope.get("path") == ("/ws/infer/" if websocket else "/health/")

    async def terminal(self, send, error):
        """Functionality: Return an existing safe error shape when routing fails.
        Inputs: ASGI sender and authored error. Outputs: error text and terminal close.
        Logic: no raw exception bodies. Constraints: never invent a session identifier.
        """
        await send(
            {
                "type": "websocket.send",
                "text": json.dumps(
                    {
                        "kind": "error",
                        "code": error.code,
                        "detail": error.detail,
                        "request_id": None,
                    }
                ),
            }
        )
        await send({"type": "websocket.close", "code": 1011})

    async def upload(self, receive, peer, state):
        """Functionality: Forward ordered original client messages to its leased worker.
        Inputs: ASGI receiver, upstream socket and local disconnect flag. Outputs: raw messages.
        Logic: one send at a time; bound each payload. Constraints: no packet or clock rewriting.
        """
        while True:
            event = await receive()
            if event["type"] == "websocket.disconnect":
                state["disconnected"] = True
                return
            if event["type"] != "websocket.receive":
                raise IntegrationError("local_packet_invalid", "Invalid inference message.")
            payload = event.get("bytes") if event.get("bytes") is not None else event.get("text")
            size = len(payload.encode("utf-8")) if isinstance(payload, str) else len(payload or b"")
            if not isinstance(payload, bytes | str) or not 0 < size <= MAX_PACKET_BYTES:
                raise IntegrationError("local_packet_invalid", "Invalid inference message size.")
            await peer.send(payload)

    async def download(self, peer, send):
        """Functionality: Forward worker messages unchanged to the same client.
        Inputs: leased peer and ASGI sender. Outputs: original bytes or text.
        Logic: socket-level bounds and sequential sends preserve backpressure.
        Constraints: never combine streams, synthesize results or extend capture deadlines.
        """
        while True:
            try:
                payload = await peer.recv()
            except ConnectionClosedOK:
                return
            await send(
                {
                    "type": "websocket.send",
                    "bytes" if isinstance(payload, bytes) else "text": payload,
                }
            )

    async def proxy(self, slot, receive, send, origin, state):
        """Functionality: Bridge exactly one socket to one fixed backend process.
        Inputs: lease, original channels/origin and disconnect state. Outputs: transparent stream.
        Logic: first completed direction cancels its sibling before socket close and native drain.
        Constraints: no redirect, proxy discovery, reconnection, compression or protocol parsing.
        """
        async with self.opener(
            f"ws://127.0.0.1:{slot.port}/ws/infer/",
            origin=origin,
            proxy=None,
            compression=None,
            open_timeout=10,
            close_timeout=5,
            max_size=MAX_PACKET_BYTES,
            max_queue=MAX_INFLIGHT,
            ping_interval=None,
        ) as peer:
            tasks = [
                asyncio.create_task(self.upload(receive, peer, state)),
                asyncio.create_task(self.download(peer, send)),
            ]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    async def websocket(self, scope, receive, send):
        """Functionality: Admit, relay and safely release one independent phone stream.
        Inputs: fixed inference ASGI socket. Outputs: original worker messages or explicit failures.
        Logic: shield native-drain cleanup from cancellation. Constraints: no slot reuse before
        idle.
        """
        first = await receive()
        if first.get("type") != "websocket.connect" or not self.allowed(scope, websocket=True):
            return await send({"type": "websocket.close", "code": 1008})
        await send({"type": "websocket.accept"})
        slot, error, cancelled = None, None, False
        state = {"disconnected": False}
        try:
            slot = await self.acquire()
            origin = dict(scope["headers"])[b"origin"].decode("ascii")
            await self.proxy(slot, receive, send, origin, state)
        except asyncio.CancelledError:
            state["disconnected"], cancelled = True, True
        except Exception as caught:
            error = (
                caught
                if isinstance(caught, IntegrationError)
                else IntegrationError(
                    "pool_backend_unavailable", "The selected inference worker is unavailable."
                )
            )
        finally:
            if slot is not None:
                cleanup = asyncio.create_task(self.release(slot))
                while not cleanup.done():
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        state["disconnected"], cancelled = True, True
                code = cleanup.result()
                if code:
                    error = IntegrationError(code, "Inference cleanup requires operator attention.")
        if cancelled:
            raise asyncio.CancelledError
        if not state["disconnected"]:
            if error:
                await self.terminal(send, error)
            else:
                await send({"type": "websocket.close", "code": 1000})

    async def respond(self, send, status, body):
        """Functionality: Return bounded noncached public health metadata.
        Inputs: authored status/body. Outputs: ASGI HTTP response. Logic: finite JSON only.
        Constraints: no credentials, private paths, media or backend URLs.
        """
        raw = json.dumps(body, allow_nan=False).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": raw})

    async def __call__(self, scope, receive, send):
        """Functionality: Expose only explicit loopback health, inference and lifespan routes.
        Inputs: ASGI scope/channels. Outputs: health/stream or fixed refusal.
        Logic: workers remain owned by the launcher. Constraints: no process/GPU start on import.
        """
        if scope["type"] == "lifespan":
            while True:
                event = await receive()
                if event["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif event["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        elif scope["type"] == "websocket":
            await self.websocket(scope, receive, send)
        elif scope["type"] == "http":
            if not self.allowed(scope):
                await self.respond(send, 403, {"code": "origin_rejected"})
            elif scope.get("method") != "GET":
                await self.respond(send, 405, {"code": "method_not_allowed"})
            else:
                await self.respond(send, 200, await self.inspect())
