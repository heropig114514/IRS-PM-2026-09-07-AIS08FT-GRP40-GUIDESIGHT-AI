"""Responsibilities: Bridge one bounded live WebSocket to a ZeroGPU worker across process forks.
Implementation: Parent owns eight-credit queues and same-parent-clock queue/cycle observations;
worker uses capability-protected loopback HTTP only; no cross-host timestamp subtraction.
Related Modules: live_worker uses spaces.GPU; app registers allocation; entrypoint wraps ASGI.
Declaration Index: LiveSession; LiveSession.__init__; LiveTransport; LiveTransport.__init__;
LiveTransport.__call__; LiveTransport.respond; LiveTransport.socket;
LiveTransport.read_frames; LiveTransport.write_results; LiveTransport.worker_request;
LiveTransport.take_frame;
LiveMiddleware; LiveMiddleware.__init__; LiveMiddleware.__call__.
Variable Index: PREFIX — exact live route namespace; SESSION_SECONDS —55s within default60s task;
IDLE_SECONDS —5s input inactivity release; MAX_INFLIGHT —8 ordered unacknowledged requests;
input_received_at/frame_started_at — bounded parent timestamps for queue/local-cycle attribution;
outgoing — validated metadata/JPEG/enqueue-time tuples, serialized once by the WS writer;
logger — lifecycle/count diagnostics; transport — parent-process singleton admission/queues.
No quota allocation occurs in this module; Gradio supplies the authenticated allocation context.
"""

import asyncio
import hmac
import json
import logging
import os
import secrets
import time

from realtime.protocol import MAX_PACKET_BYTES, pack_result, unpack_request, unpack_result

PREFIX = "/prs_live"
SESSION_SECONDS = 55
IDLE_SECONDS = 5
MAX_INFLIGHT = 8
logger = logging.getLogger(__name__)


class LiveSession:
    """Functionality: Keep bounded CPU state for a single interactive GPU allocation.
    Logic: capability authenticates loopback handoffs, credits bound outstanding frames.
    Constraints: frames remain in memory; no graph/model in the parent; no retry or skipping.
    """

    def __init__(self):
        """Functionality: Create a fresh session with no GPU allocation.
        Inputs: entropy/system monotonic clock; Outputs: queues, capability and lifecycle state.
        Logic: separate256-bit client/worker keys,eight-slot queues/credits; keys never enter logs.
        Worker key stays private to Gradio/GPU worker and is never included in the WS hello.
        started remains None until the decorated worker explicitly claims this session.
        Per-index input_received_at/frame_started_at hold at most MAX_INFLIGHT entries; remove
        them after result validation. All queue/cycle durations use this parent's monotonic clock.
        """
        self.capability = secrets.token_hex(32)
        self.worker_capability = secrets.token_hex(32)
        self.incoming = asyncio.Queue(MAX_INFLIGHT)
        self.outgoing = asyncio.Queue(MAX_INFLIGHT)
        self.credits = asyncio.Semaphore(MAX_INFLIGHT)
        self.stopped = asyncio.Event()
        self.started = None
        self.received = self.completed = self.sent = 0
        self.peer_disconnected = False
        self.close_code, self.close_reason = 1000, "Video session ended"
        self.input_received_at = {}
        self.frame_started_at = {}


class LiveTransport:
    """Functionality: Serve CPU live routes while forwarding the original Gradio application.
    Logic: one WS admission and loopback-only capability handoff to the allocated worker.
    Constraints:55s allocated session,5s idle release; ordinary Gradio APIs remain unchanged.
    """

    def __init__(self, app=None, origins=None):
        """Functionality: Bind an ASGI delegate and explicit browser origins.
        Inputs: app callable, optional origin set or PRS_LIVE_ORIGINS environment.
        Outputs: wrapper with one empty admission slot; Logic: own session state in this instance.
        Constraints: default origins are existing PRS and this HF Space, never a wildcard;
        entrypoint's LiveMiddleware delegates to the separate shared transport instance.
        """
        self.app = app
        self.origins = frozenset(origins if origins is not None else os.environ.get(
            "PRS_LIVE_ORIGINS", "https://47.239.50.129 "
            "https://heropig114514-prs-yolo26.hf.space").split())
        self.session = None

    async def __call__(self, scope, receive, send):
        """Functionality: Dispatch exact public routes and loopback worker handoff endpoints.
        Inputs: ASGI scope/receive/send; Outputs: bounded WS/HTTP or original delegate behavior.
        Logic: health uses no GPU; reject query/foreign paths; internal routes verify peer/token.
        Constraints: no credential/body logs, private frame persistence or hardware requests.
        """
        path = scope.get("path", "")
        if not path.startswith(PREFIX + "/"):
            return await self.app(scope, receive, send)
        if scope.get("query_string"):
            if scope["type"] == "websocket":
                return await send({"type": "websocket.close", "code": 1008})
            return await self.respond(send, 404)
        if scope["type"] == "http" and path == PREFIX + "/healthz":
            body = json.dumps({"ready": True, "gpu_allocated_by_this_route": False,
                               "active_session": self.session is not None,
                               "session_seconds": SESSION_SECONDS,
                               "max_inflight": MAX_INFLIGHT}).encode()
            return await self.respond(send, 200, body, b"application/json")
        if scope["type"] == "websocket":
            origin = dict(scope.get("headers", [])).get(b"origin", b"").decode("ascii", "ignore")
            if path != PREFIX + "/ws" or origin not in self.origins:
                return await send({"type": "websocket.close", "code": 1008})
            return await self.socket(receive, send)
        if scope["type"] == "http":
            return await self.worker_request(scope, receive, send)
        return await self.app(scope, receive, send)

    @staticmethod
    async def respond(send, status, body=b"", content_type=b"application/octet-stream"):
        """Functionality: Send one non-streamed, non-cacheable internal HTTP response.
        Inputs: ASGI send/status/body/content_type; Outputs: start and complete body messages.
        Logic: fixed content length supports worker HTTP keep-alive; Constraints: bounded by caller.
        """
        await send({"type": "http.response.start", "status": status, "headers": [
            (b"content-type", content_type), (b"content-length", str(len(body)).encode()),
            (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": body})

    async def socket(self, receive, send):
        """Functionality: Admit one live peer and release CPU queues on stop/error/disconnect.
        Inputs: WS receive/send and current admission; Outputs: hello token, ordered binary results.
        Logic: independent bounded reader/writer, cancel sibling on completion, clear singleton.
        Constraints: a second peer closes1013; worker quota/Graph exists only in decorated function;
        CPU disconnect makes worker's next HTTP call410, allowing that GPU function to return.
        """
        await receive()  # ASGI requires consuming websocket.connect before accepting.
        await send({"type": "websocket.accept"})
        if self.session is not None:
            return await send({"type": "websocket.close", "code": 1013,
                               "reason": "A live video session is already admitted"})
        session = self.session = LiveSession()
        logger.info("ZeroGPU live peer admitted; GPU not allocated")
        tasks = []
        try:
            await send({"type": "websocket.send", "text": json.dumps({
                "kind": "session", "capability": session.capability,
                "session_seconds": SESSION_SECONDS, "max_inflight": MAX_INFLIGHT})})
            tasks = [asyncio.create_task(self.read_frames(session, receive)),
                     asyncio.create_task(self.write_results(session, send))]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except ValueError:
            session.close_code, session.close_reason = 1008, "Invalid live frame protocol"
            logger.warning("ZeroGPU live protocol rejected received=%d", session.received)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            session.close_code, session.close_reason = 1011, "Live transport failed; inspect logs"
            logger.exception("ZeroGPU live transport failed type=%s", type(error).__name__)
        finally:
            session.stopped.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.session = None
            logger.info("ZeroGPU live peer released received=%d completed=%d sent=%d",
                        session.received, session.completed, session.sent)
        if not session.peer_disconnected:
            await send({"type": "websocket.close", "code": session.close_code,
                        "reason": session.close_reason})

    async def read_frames(self, session, receive):
        """Functionality: Admit exact sequence-ordered requests under eight-frame backpressure.
        Inputs: session state and WS receive; Outputs: incoming queue or completion on peer stop.
        Logic: receive lifecycle messages even when credits exhausted; validate packet/index,
        reject a peer exceeding its advertised eight-frame limit; allow explicit stop message.
        Record validated ingress before queueing, without retaining any additional media copy.
        Constraints: no dropped frames; pending capture is bounded by client credits separately;
        malformed frames raise before GPU access. Disconnect does not await a new GPU task.
        """
        while True:
            message = await receive()
            if message["type"] == "websocket.disconnect":
                session.peer_disconnected = True
                return
            if message.get("text") == '{"kind":"stop"}':
                return
            packet = message.get("bytes")
            received_at = time.perf_counter()
            unpack_request(packet, session.received)
            if session.credits.locked():
                raise ValueError("Peer exceeded advertised live in-flight limit")
            await session.credits.acquire()
            session.input_received_at[session.received] = received_at
            await session.incoming.put(packet)
            session.received += 1

    async def write_results(self, session, send):
        """Functionality: Deliver validated complete binary results, bounding downstream queues.
        Inputs: validated outgoing tuples/lifecycle/ASGI send; Outputs: WS messages and credits.
        Logic: observe output queue residence, serialize unchanged JPEG once, await sends;
        log120-frame milestones and long awaits. Queue timing excludes the awaited network send.
        Constraints: ASGI send completion is not client ACK; benchmark retains true capture clocks.
        """
        while not session.stopped.is_set():
            try:
                metadata, jpeg, queued_at = await asyncio.wait_for(session.outgoing.get(), 0.2)
            except TimeoutError:
                continue
            before = time.perf_counter()
            metadata["hf_output_queue_ms"] = (before - queued_at) * 1000
            packet = pack_result(metadata, jpeg)
            await send({"type": "websocket.send", "bytes": packet})
            wait_ms = (time.perf_counter() - before) * 1000
            session.sent += 1
            session.credits.release()
            if wait_ms > 100 or session.sent % 120 == 0:
                logger.info("ZeroGPU live delivered frames=%d send_ms=%.1f queue_ms=%.2f "
                            "local_cycle_ms=%.2f output_queue_ms=%.2f",
                            session.sent, wait_ms, metadata["hf_parent_queue_ms"],
                            metadata["hf_local_cycle_ms"], metadata["hf_output_queue_ms"])

    async def worker_request(self, scope, receive, send):
        """Functionality: Handle loopback worker claim/pull/result/end for the admitted capability.
        Inputs: bounded HTTP/session; Outputs: status/body, CPU queues/lifecycle updates.
        Logic: verify loopback peer plus constant-time256-bit capability, enforce single claim,
        then5s input wait and55s allocation bound; validate every returned sequence/JPEG hash.
        hf_parent_queue_ms spans ingress to pull; hf_local_cycle_ms spans pull response preparation
        to full POST-result validation; hf_result_ingest_ms is the POST body/validation subset.
        Cold initialization and intentional input waits remain observable, not removed as outliers.
        Constraints: token only in header; no query credentials/retries; worker errors close1011.
        Public requests cannot access worker routes even with a guessed session capability.
        """
        session = self.session
        request_started = time.perf_counter()
        client = scope.get("client") or ("", 0)
        authorization = dict(scope.get("headers", [])).get(b"authorization", b"")
        if client[0] not in ("127.0.0.1", "::1"):
            return await self.respond(send, 403)
        if session is None or session.stopped.is_set():
            return await self.respond(send, 410)
        if not hmac.compare_digest(authorization,
                                   ("Bearer " + session.worker_capability).encode()):
            return await self.respond(send, 403)
        action = scope["path"].removeprefix(PREFIX + "/")
        method = scope["method"]
        if action == "start" and method == "POST":
            if session.started is not None:
                return await self.respond(send, 409)
            session.started = time.monotonic()
            logger.info("ZeroGPU live allocated worker claimed; limit_s=%d", SESSION_SECONDS)
            return await self.respond(send, 200)
        if session.started is None:
            return await self.respond(send, 409)
        if action == "next" and method == "GET":
            remaining = SESSION_SECONDS - (time.monotonic() - session.started)
            if remaining <= 0:
                session.close_reason = "ZeroGPU video session time limit reached"
                session.stopped.set()
                return await self.respond(send, 410)
            try:
                packet = await self.take_frame(session, min(IDLE_SECONDS, remaining))
            except TimeoutError:
                session.close_reason = "ZeroGPU video session idle or time limit reached"
                session.stopped.set()
                return await self.respond(send, 410)
            if packet is None:
                return await self.respond(send, 410)
            session.frame_started_at[session.completed] = time.perf_counter()
            return await self.respond(send, 200, packet)
        if action in ("result", "end") and method == "POST":
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > MAX_PACKET_BYTES:
                    return await self.respond(send, 413)
                if not message.get("more_body", False):
                    break
            if action == "end":
                if body != b"ok":
                    session.close_code, session.close_reason = 1011, "Allocated GPU worker failed"
                session.stopped.set()
                return await self.respond(send, 200)
            try:
                metadata, jpeg = unpack_result(bytes(body), session.completed)
                if session.completed >= session.received:
                    raise ValueError("No admitted frame for result")
            except (ValueError, KeyError, json.JSONDecodeError):
                session.close_code, session.close_reason = 1011, "GPU result protocol invalid"
                session.stopped.set()
                return await self.respond(send, 422)
            validated_at = time.perf_counter()
            index = session.completed
            pulled_at = session.frame_started_at.pop(index)
            received_at = session.input_received_at.pop(index)
            metadata.update(
                hf_parent_queue_ms=(pulled_at - received_at) * 1000,
                hf_local_cycle_ms=(validated_at - pulled_at) * 1000,
                hf_result_ingest_ms=(validated_at - request_started) * 1000,
            )
            await session.outgoing.put((metadata, jpeg, validated_at))
            session.completed += 1
            return await self.respond(send, 200)
        return await self.respond(send, 404)

    @staticmethod
    async def take_frame(session, timeout):
        """Functionality: Wake the blocked worker immediately when its CPU peer stops.
        Inputs: session queue/stop event and bounded timeout; Outputs: packet, None or TimeoutError.
        Logic: race one queue get against stop, cancel/await both helpers in every branch.
        Constraints: no detached tasks or implicit retry; timeout retains the idle-release meaning.
        """
        getter = asyncio.create_task(session.incoming.get())
        stopper = asyncio.create_task(session.stopped.wait())
        try:
            done, _ = await asyncio.wait((getter, stopper), timeout=timeout,
                                         return_when=asyncio.FIRST_COMPLETED)
            if not done:
                raise TimeoutError("Live input idle")
            return None if stopper in done else getter.result()
        finally:
            getter.cancel()
            stopper.cancel()
            await asyncio.gather(getter, stopper, return_exceptions=True)


transport = LiveTransport()


class LiveMiddleware:
    """Functionality: Mount the shared parent broker around Gradio's ASGI application.
    Logic: one middleware instance attaches Gradio as the shared broker's delegate.
    Constraints: one Gradio worker per Space; GPU child accesses queues only through HTTP.
    """

    def __init__(self, app):
        """Functionality: Bind the parent ASGI delegate used by the registered live API.
        Inputs: app callable; Outputs: shared transport delegates non-live paths to app.
        Logic: assign once at server initialization; Constraints: no GPU request or network call.
        """
        transport.app = app

    async def __call__(self, scope, receive, send):
        """Functionality: Invoke the shared broker for this ASGI request.
        Inputs: scope/receive/send; Outputs: broker response or original Gradio response.
        Logic: direct awaited dispatch; Constraints: no admission state copies across middleware.
        """
        return await transport(scope, receive, send)
