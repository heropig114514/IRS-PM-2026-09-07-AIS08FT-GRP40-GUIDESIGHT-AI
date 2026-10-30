"""Responsibilities: Adapt same-origin ordered WebSockets to resident GPU services.
Implementation: Twelve total outstanding frames, drain-on-stop and cancellation-safe worker join.
Related Modules: realtime.services/protocol; config.resident_asgi; core.test_resident.
Declaration Index: ResidentApplication; ResidentApplication.__init__; ResidentApplication.__call__;
ResidentApplication.lifespan; ResidentApplication.lifespan.replay;
ResidentApplication.lifespan.acknowledge; ResidentApplication.socket;
ResidentApplication.read; ResidentApplication.write; Session; Session.__init__.
Variable Index: MAX_INFLIGHT — twelve accepted but not delivered frames; IO_SECONDS — idle/send
timeout; logger — safe session IDs/counters/error types; Session.queue — bounded ordered input.
"""

import asyncio
import json
import logging
import time
from urllib.parse import urlsplit

from django.conf import settings

from realtime.protocol import pack_result, unpack_request

MAX_INFLIGHT = 12
IO_SECONDS = 15
logger = logging.getLogger("core.resident.asgi")


class Session:
    """Functionality: Hold one bounded stream's ownership and delivery counters.
    Logic: one input queue and separate stop/disconnect flags. Constraints: never persist media.
    """

    def __init__(self, lease):
        """Functionality: Start empty video state.
        Inputs: exclusive lease. Outputs: queue/counters/events. Logic: allocate twelve queue slots.
        Constraints: the adapter also counts the executing frame against the twelve-frame limit.
        """
        self.lease = lease
        self.queue = asyncio.Queue(MAX_INFLIGHT)
        self.received = self.delivered = 0
        self.disconnected = False
        self.stopping = asyncio.Event()


class ResidentApplication:
    """Functionality: Add an explicitly selected resident endpoint to the original ASGI app.
    Logic: load before delegating lifespan startup; preserve existing Django/MCP/WS dispatch.
    Constraints: single process, exact origin/host, one active stream; no auth via query strings.
    """

    def __init__(self, service, fallback):
        """Functionality: Bind a resident service and existing ASGI application.
        Inputs: service/fallback. Outputs: callable adapter. Logic: store dependencies only.
        Constraints: no GPU or network work at import time.
        """
        self.service, self.fallback = service, fallback

    async def __call__(self, scope, receive, send):
        """Functionality: Dispatch resident video or preserve existing routes.
        Inputs: ASGI triple. Outputs: lifecycle or socket/HTTP responses.
        Logic: intercept lifespan and exact resident path; delegate every other route unchanged.
        Constraints: original config.asgi entry remains HF-only until this entry is selected.
        """
        if scope["type"] == "lifespan":
            await self.lifespan(scope, receive, send)
        elif scope["type"] == "websocket" and scope["path"] == "/ws/detect/resident/":
            await self.socket(scope, receive, send)
        else:
            await self.fallback(scope, receive, send)

    async def lifespan(self, scope, receive, send):
        """Functionality: Load GPU once before the underlying app announces startup.
        Inputs: ASGI lifecycle. Outputs: startup failure or delegated lifecycle acknowledgements.
        Logic: replay startup to MCP; join executor before forwarding shutdown acknowledgement.
        Constraints: missing CUDA/assets fail startup; no automatic provider selection or retries.
        """
        first = await receive()
        if first["type"] != "lifespan.startup":
            raise RuntimeError("Expected ASGI startup")
        try:
            await self.service.start()
        except Exception as error:
            logger.error("Resident startup failed type=%s", type(error).__name__)
            await self.service.close()
            await send({"type": "lifespan.startup.failed",
                        "message": "Resident GPU startup failed; inspect local assets/CUDA."})
            return
        forwarded = closed = False

        async def replay():
            """Functionality: Replay the consumed startup exactly once to the original app.
            Inputs: outer startup and ASGI receive. Outputs: next lifespan event.
            Logic: first read returns startup; later reads delegate. Constraints: single consumer.
            """
            nonlocal forwarded
            if not forwarded:
                forwarded = True
                return first
            return await receive()

        async def acknowledge(message):
            """Functionality: Finish GPU teardown before the server considers shutdown complete.
            Inputs: underlying lifecycle response. Outputs: forwarded response after worker join.
            Logic: intercept final acknowledgement; otherwise the server may cancel cleanup.
            Constraints: retain original MCP startup/shutdown semantics and failure responses.
            """
            nonlocal closed
            if message["type"] in {"lifespan.shutdown.complete", "lifespan.shutdown.failed"}:
                await self.service.close()
                closed = True
            await send(message)

        try:
            await self.fallback(scope, replay, acknowledge)
        finally:
            if not closed:
                await self.service.close()

    async def socket(self, scope, receive, send):
        """Functionality: Validate admission and own stream cleanup through actual GPU completion.
        Inputs: same-origin ASGI WS. Outputs: hello, ordered results, ended/error and close.
        Logic: reader watches disconnect; stop drains accepted frames; cancellation joins writer.
        Constraints: busy users get 1013; no waiting-user queue, frame drops or reconnect.
        """
        if (await receive())["type"] != "websocket.connect":
            return
        headers = dict(scope.get("headers", []))
        origin = headers.get(b"origin", b"").decode("ascii", "replace")
        host = headers.get(b"host", b"").decode("ascii", "replace")
        if (scope.get("query_string") or origin not in settings.CAMERA_ALLOWED_ORIGINS
                or host != urlsplit(origin).netloc):
            logger.warning("Resident rejected origin_or_host_or_query")
            await send({"type": "websocket.close", "code": 1008})
            return
        await send({"type": "websocket.accept"})
        lease = self.service.claim()
        if lease is None:
            await send({"type": "websocket.send", "text": json.dumps({"kind": "error",
                        "code": "resident_busy" if self.service.ready
                        else "resident_unavailable"})})
            await send({"type": "websocket.close", "code": 1013})
            return
        session, tasks, error = Session(lease), [], None
        logger.info("Resident admitted id=%s mode=%s", lease, self.service.mode)
        try:
            await self.service.begin(lease)
            await send({"type": "websocket.send", "text": json.dumps({
                **self.service.contract(), "max_inflight": MAX_INFLIGHT,
                "idle_seconds": IO_SECONDS})})
            reader = asyncio.create_task(self.read(session, receive))
            writer = asyncio.create_task(self.write(session, send))
            tasks = [reader, writer]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            if reader in done and session.stopping.is_set() and not session.disconnected:
                await writer
        except asyncio.CancelledError:
            session.disconnected = True
            raise
        except Exception as caught:
            error = caught
        finally:
            for task in tasks:
                task.cancel()
            # execute() shields and joins any already-running CUDA call before returning here.
            await asyncio.gather(*tasks, return_exceptions=True)
            self.service.release(lease)
            logger.log(logging.ERROR if error else logging.INFO,
                       "Resident released id=%s received=%d delivered=%d failure=%s",
                       lease, session.received, session.delivered,
                       type(error).__name__ if error else "none")
        if not session.disconnected:
            try:
                async with asyncio.timeout(IO_SECONDS):
                    await send({"type": "websocket.send", "text": json.dumps({
                        "kind": "error" if error else "ended", "request_id": lease,
                        "code": "resident_stream_failed" if error else "completed",
                        "received": session.received, "delivered": session.delivered})})
                    await send({"type": "websocket.close", "code": 1011 if error else 1000})
            except (OSError, TimeoutError):
                logger.info("Resident peer unavailable id=%s", lease)

    async def read(self, session, receive):
        """Functionality: Validate order/size/credits while observing stop and disconnect.
        Inputs: socket messages. Outputs: queued JPEGs and bounded ingress timestamps.
        Logic: reject a thirteenth outstanding frame; enqueue stop after all accepted frames.
        Constraints: idle timeout is independent of GPU residency; malformed input is never retried.
        """
        while True:
            async with asyncio.timeout(IO_SECONDS):
                message = await receive()
            if message["type"] == "websocket.disconnect":
                session.disconnected = True
                return
            if message.get("text") == '{"kind":"stop"}':
                await session.queue.put(None)
                session.stopping.set()
                return
            jpeg = unpack_request(message.get("bytes"), session.received)
            if not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9"):
                raise ValueError("Invalid JPEG markers")
            if session.received - session.delivered >= MAX_INFLIGHT:
                raise ValueError("Resident outstanding frame limit exceeded")
            session.queue.put_nowait((session.received, jpeg, time.perf_counter()))
            session.received += 1

    async def write(self, session, send):
        """Functionality: Infer and deliver each accepted frame once in order.
        Inputs: frames and stop sentinel. Outputs: binary responses with local queue duration.
        Logic: read in FIFO order; the stop sentinel follows every accepted frame.
        Constraints: only this coroutine calls predict; send has a finite timeout; no HTTP handoff.
        """
        while True:
            item = await session.queue.get()
            if item is None:
                return
            index, jpeg, received_at = item
            queue_ms = (time.perf_counter() - received_at) * 1000
            metadata, result = await self.service.predict(session.lease, jpeg)
            packet = pack_result({**metadata, "index": index,
                                  "resident_queue_ms": queue_ms}, result)
            async with asyncio.timeout(IO_SECONDS):
                await send({"type": "websocket.send", "bytes": packet})
            session.delivered += 1
