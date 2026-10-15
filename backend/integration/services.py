"""Responsibilities: Bridge local UI to the independent inference process without algorithm work.
Implementation: Same-origin bounded PRF2/PRS1 WS relay; explicit status/assets/local-only routing.
Related Modules: integration.asgi/protocol/errors; integration.inference; config.asgi;
frontend/integration. Existing software/algorithm implementation files remain untouched here.
Declaration Index: NoRedirect; NoRedirect.redirect_request; read_health; http_response;
GatewaySession; GatewaySession.__init__;
LocalGateway; LocalGateway.__init__; LocalGateway.__call__; LocalGateway.read_browser;
LocalGateway.bridge; LocalGateway.forward; LocalGateway.return_results; LocalGateway.stop_provider;
LocalGateway.cleanup; LocalGateway.finish; IntegrationApplication; IntegrationApplication.__init__;
IntegrationApplication.__call__; IntegrationApplication.asset; IntegrationApplication.status.
Variable Index: logger — lifecycle diagnostics without URLs/media; ASSETS — fixed frontend
allowlist;
FRONTEND — fixed repository frontend root; input_times/incoming — twelve outstanding frames;
submitted — attempted provider sends; forwarded — send-completed or worker-confirmed prefix;
worker_verified — complete validated provider results; delivered — completed browser ASGI sends;
LOCAL_DISABLED — explicit external-feature error, not provider fallback.
"""

import asyncio
import json
import logging
import urllib.request
from threading import BoundedSemaphore
from urllib.parse import urlsplit

from realtime.protocol import MAX_PACKET_BYTES, unpack_result

from .errors import IntegrationError
from .protocol import (
    COLD_SECONDS,
    MAX_INFLIGHT,
    ROOT,
    STEADY_SECONDS,
    inference_url,
    is_stop,
    unpack_request,
    validate_contract,
    validate_result,
    web_origins,
)

logger = logging.getLogger(__name__)
FRONTEND = ROOT.parent / "frontend"
ASSETS = {
    "video-mvp.js": "text/javascript",
    "video-replay.js": "text/javascript",
    "video.css": "text/css",
    "devices.js": "text/javascript",
    "events.js": "text/javascript",
    "catalog.js": "text/javascript",
    "guidance.js": "text/javascript",
    "local-mvp.js": "text/javascript",
    "local-mvp.css": "text/css",
}
LOCAL_DISABLED = {
    "code": "local_only_external_disabled",
    "feature": "disabled",
    "detail": "纯本地 MVP 已禁用外部推理、场景描述和在线导航服务。",
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Functionality: Reject attempts to change the explicitly configured health destination.
    Logic: decline redirect requests. Constraints: no arbitrary redirected network contact.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Functionality: Stop redirect processing before a second request is issued.
        Inputs: standard urllib redirect context. Outputs: None, causing explicit HTTP failure.
        Logic: no destination substitution. Constraints: no retry or final-URL-only verification.
        """
        return None


def read_health(url):
    """Functionality: Fetch one bounded worker health response without triggering inference.
    Inputs: validated server-owned health URL. Outputs: safe parsed dictionary.
    Logic: explicit verified transport, reject redirects; 2-second I/O/64KiB body boundary.
    Constraints: no auth/body/URL logging, proxy discovery, retries or alternate health targets.
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(url, method="GET")
    with opener.open(request, timeout=2) as response:
        if response.geturl() != url:
            raise IntegrationError("local_status_invalid", "本地推理健康检查发生重定向。")
        raw = response.read(65537)
    if len(raw) > 65536:
        raise IntegrationError("local_status_invalid", "本地推理健康响应过大。")
    body = json.loads(raw)
    if (
        not isinstance(body, dict)
        or body.get("provider") != "local"
        or any(type(body.get(key)) is not bool for key in ("loaded", "busy", "ready"))
    ):
        raise IntegrationError("local_status_invalid", "本地推理健康响应无效。")
    return body


async def http_response(
    send, status, body, content_type="application/json", *, head=False, camera_access=True
):
    """Functionality: Return a bounded no-store integration response with same-origin assets policy.
    Inputs: send/status/body/MIME/HEAD flag and camera permission policy. Outputs: HTTP start/body.
    Logic: finite JSON or literal bytes; fixed CSP permits current browser media/control needs.
    Constraints: no user-controlled file path, external connection permission or embedded scripts.
    """
    raw = body if isinstance(body, bytes) else json.dumps(body, allow_nan=False).encode()
    socket_origins = " ".join(
        item.replace("https://", "wss://").replace("http://", "ws://") for item in web_origins()
    )
    policy = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' blob:; media-src 'self' blob:; "
        f"connect-src 'self' {socket_origins}; object-src 'none'; base-uri 'none'; "
        "frame-ancestors 'none'"
    )
    headers = [
        (b"content-type", content_type.encode()),
        (b"cache-control", b"no-store"),
        (b"content-length", str(len(raw)).encode()),
        (
            b"permissions-policy",
            b"camera=(self), microphone=()" if camera_access else b"camera=(), microphone=()",
        ),
        (b"content-security-policy", policy.encode()),
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": b"" if head else raw})


class GatewaySession:
    """Functionality: Keep bounded browser-source correlation without owning algorithm state.
    Logic: queue/counters/clock map plus stop/disconnect flags. Constraints: media stays in memory.
    """

    def __init__(self):
        """Functionality: Initialize an unconnected relay.
        Inputs: asyncio state. Outputs: bounded session. Logic: no provider work.
        Constraints: source timestamp is forwarded exactly rather than inferred from server timing.
        """
        self.incoming = asyncio.Queue(MAX_INFLIGHT)
        self.input_times = {}
        self.received = self.submitted = self.forwarded = self.worker_verified = self.delivered = 0
        self.worker_received = None
        self.previous_timestamp = self.contract = None
        self.disconnected = False
        self.stopped = asyncio.Event()


class LocalGateway:
    """Functionality: Connect one same-origin local browser stream to an explicit worker.
    Logic: source validation, separate forwarding/result/stop loops and exact result identity.
    Constraints: no CUDA/model state, external service fallback, implicit retry or partial success.
    """

    def __init__(self, opener=None, admission=None):
        """Functionality: Bind explicit connector and shared single-session capacity.
        Inputs: optional synthetic test dependencies. Outputs: lightweight callable.
        Logic: WebSocket import is lazy. Constraints: no connection or GPU work during construction.
        """
        self.opener = opener
        self.admission = admission if admission is not None else BoundedSemaphore(1)

    async def __call__(self, scope, receive, send):
        """Functionality: Admit and close one browser relay with strict origin and host checks.
        Inputs: ASGI scope/receive/send. Outputs: admitted contract/results or safe error/end.
        Logic: after browser stop, await worker drain/end within the cold transport budget.
        Native capacity remains owned by the worker even if this connection times out.
        Constraints: single Uvicorn worker; cancellation never fabricates completed GPU work.
        """
        first = await receive()
        headers = dict(scope.get("headers", []))
        origin = headers.get(b"origin", b"").decode("ascii", "replace")
        host = headers.get(b"host", b"").decode("ascii", "replace")
        if (
            first["type"] != "websocket.connect"
            or scope.get("query_string")
            or origin not in web_origins()
            or host != urlsplit(origin).netloc
        ):
            return await send({"type": "websocket.close", "code": 1008})
        await send({"type": "websocket.accept"})
        if not self.admission.acquire(blocking=False):
            return await self.finish(send, None, IntegrationError("local_busy", "本地推理忙。"))
        session, reader, provider, error = GatewaySession(), None, None, None
        try:
            reader = asyncio.create_task(self.read_browser(session, receive))
            provider = asyncio.create_task(self.bridge(session, send, origin))
            done, _ = await asyncio.wait((reader, provider), return_when=asyncio.FIRST_COMPLETED)
            if reader in done:
                try:
                    reader.result()
                except Exception as caught:
                    error = caught
                session.stopped.set()
                await asyncio.shield(provider)
            else:
                provider.result()
        except asyncio.CancelledError:
            session.disconnected = True
            session.stopped.set()
            raise
        except Exception as caught:
            error = caught if error is None else error
        finally:
            cleanup = asyncio.create_task(self.cleanup(session, reader, provider, send, error))
            interrupted = False
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    session.disconnected = True
                    session.stopped.set()
                    interrupted = True
            cleanup.result()
            if interrupted:
                raise asyncio.CancelledError

    async def cleanup(self, session, reader, provider, send, error):
        """Functionality: Await bounded provider drain and release relay admission after cleanup.
        Inputs: session/tasks/send/prior error. Outputs: safe terminal and released relay slot.
        Logic: shield this complete task; stop future inputs, await worker drain acknowledgement,
        then close the browser. Constraints: transport waits have a 55-second budget;
        native admission stays held until the worker's in-flight operation actually completes.
        """
        try:
            if reader is not None:
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)
            if provider is not None and not provider.done():
                session.stopped.set()
                try:
                    await asyncio.wait_for(asyncio.shield(provider), COLD_SECONDS)
                except Exception as caught:
                    error = caught if error is None else error
                    provider.cancel()
                    await asyncio.gather(provider, return_exceptions=True)
            if provider is not None and provider.done() and not provider.cancelled():
                caught = provider.exception()
                error = caught if error is None else error
            if not session.disconnected:
                await self.finish(send, session, error)
        except OSError:
            logger.info("Local browser unavailable during close")
        finally:
            self.admission.release()
            logger.info(
                "Local gateway released received=%d submitted=%d forwarded=%d "
                "worker_verified=%d delivered=%d error=%s",
                session.received,
                session.submitted,
                session.forwarded,
                session.worker_verified,
                session.delivered,
                type(error).__name__ if error else None,
            )

    async def read_browser(self, session, receive):
        """Functionality: Validate source order/time and watch stop/disconnect independently.
        Inputs: session/receive. Outputs: exact queued source packets and bounded correlation map.
        Logic: reject excess total outstanding frames before admitting another packet.
        Constraints: no JPEG re-encoding, inferred timestamps, persistence or algorithm work.
        """
        try:
            while True:
                message = await receive()
                if message["type"] == "websocket.disconnect":
                    session.disconnected = True
                    return
                if is_stop(message.get("text")):
                    return
                packet = message.get("bytes")
                timestamp, _ = unpack_request(packet, session.received, session.previous_timestamp)
                if session.received - session.delivered >= MAX_INFLIGHT:
                    raise IntegrationError("local_capacity", "超过允许的在途帧数量。", 400)
                session.input_times[session.received] = timestamp
                session.incoming.put_nowait(packet)
                session.received += 1
                session.previous_timestamp = timestamp
        finally:
            session.stopped.set()

    async def bridge(self, session, send, origin):
        """Functionality: Execute one explicit worker connection and preserve its stream identity.
        Inputs: session/browser send/canonical browser origin. Outputs: hello and complete results.
        Logic: independent upload/download/stop control; stop awaits terminal after worker GPU
        drain.
        Constraints: destination never comes from browser; no reconnection, redirects or
        compression.
        """
        if self.opener is None:
            from websockets.asyncio.client import connect

            opener = connect
        else:
            opener = self.opener
        url, _ = inference_url()
        try:
            async with opener(
                url,
                origin=origin,
                compression=None,
                proxy=None,
                open_timeout=10,
                close_timeout=5,
                max_size=MAX_PACKET_BYTES,
                max_queue=MAX_INFLIGHT,
                ping_interval=None,
            ) as peer:
                hello = json.loads(await asyncio.wait_for(peer.recv(), 10))
                if hello.get("kind") == "error":
                    raise IntegrationError(
                        hello.get("code", "local_unavailable"), "本地推理进程拒绝会话，请检查状态。"
                    )
                session.contract = validate_contract(hello)
                if not session.disconnected:
                    await send({"type": "websocket.send", "text": json.dumps(session.contract)})
                upload = asyncio.create_task(self.forward(session, peer))
                download = asyncio.create_task(self.return_results(session, peer, send))
                stopping = asyncio.create_task(self.stop_provider(session, peer, upload))
                try:
                    done, _ = await asyncio.wait(
                        (upload, download, stopping), return_when=asyncio.FIRST_COMPLETED
                    )
                    for task in done:
                        if not task.cancelled():
                            task.result()
                    if download not in done:
                        await download
                finally:
                    for task in (upload, download, stopping):
                        task.cancel()
                    await asyncio.gather(upload, download, stopping, return_exceptions=True)
        except IntegrationError:
            raise
        except Exception as caught:
            logger.error("Local gateway worker transport failed type=%s", type(caught).__name__)
            raise IntegrationError(
                "local_worker_unavailable", "无法连接本地推理进程或推理连接已中断。"
            ) from None

    async def forward(self, session, peer):
        """Functionality: Forward original ordered timestamped packets under network backpressure.
        Inputs: admitted queue/upstream peer. Outputs: exact bytes and forward count.
        Logic: mark attempted sequence before send, confirm completion only after await returns.
        Constraints: worker acknowledgements can confirm an already-buffered cancelled send;
        no extra queue, retries, image changes or assumed successful transmission.
        """
        while True:
            packet = await session.incoming.get()
            session.submitted += 1
            attempted = session.submitted
            await peer.send(packet)
            session.forwarded = max(session.forwarded, attempted)

    async def return_results(self, session, peer, send):
        """Functionality: Validate complete results and worker terminal counts before delivery.
        Inputs: peer/session/send. Outputs: unchanged PRS1 frames or terminal completion.
        Logic: hash/order/session/source-time/algorithm identity; source map bounds unsolicited
        output.
        Constraints: stop/disconnect suppresses display while continuing worker drain
        acknowledgement.
        """
        while True:
            packet = await asyncio.wait_for(
                peer.recv(),
                COLD_SECONDS
                if (session.worker_verified == 0 or session.stopped.is_set())
                else STEADY_SECONDS,
            )
            if isinstance(packet, str):
                terminal = json.loads(packet)
                if terminal.get("kind") == "ended":
                    counts = [terminal.get(key) for key in ("frames", "received", "delivered")]
                    if (
                        all(type(value) is int and value >= 0 for value in counts)
                        and counts[0] == counts[2] == session.worker_verified
                        and counts[0] <= counts[1] <= session.submitted <= session.received
                    ):
                        session.worker_received = counts[1]
                        session.forwarded = max(session.forwarded, counts[1])
                        return
                    raise IntegrationError("local_result_invalid", "本地推理结束计数无效。")
                if terminal.get("kind") == "error":
                    code, detail = terminal.get("code"), terminal.get("detail")
                    if (
                        isinstance(code, str)
                        and len(code) <= 64
                        and isinstance(detail, str)
                        and (len(detail) <= 500)
                    ):
                        raise IntegrationError(code, detail)
                raise IntegrationError("local_result_invalid", "本地推理结束消息无效。")
            index = session.worker_verified
            metadata, _ = unpack_result(packet, index)
            timestamp = session.input_times.get(index)
            if timestamp is None or index >= session.submitted:
                raise IntegrationError("local_result_invalid", "本地推理返回未发送帧的结果。")
            validate_result(metadata, session.contract, index, timestamp)
            session.input_times.pop(index)
            session.worker_verified += 1
            session.forwarded = max(session.forwarded, session.worker_verified)
            if not session.disconnected and not session.stopped.is_set():
                await send({"type": "websocket.send", "bytes": packet})
                session.delivered += 1

    async def stop_provider(self, session, peer, upload):
        """Functionality: Stop future forwarding and explicitly end the upstream stream.
        Inputs: stop event/peer/upload task. Outputs: one stop message.
        Logic: cancel only upload then send control; download waits for real worker termination.
        Constraints: does not kill native work, reconnect or release the inference slot itself.
        """
        await session.stopped.wait()
        upload.cancel()
        await asyncio.gather(upload, return_exceptions=True)
        await peer.send('{"kind":"stop"}')

    async def finish(self, send, session, error):
        """Functionality: Emit safe error or counted normal ending at the software boundary.
        Inputs: send/session/error. Outputs: public terminal and WS close.
        Logic: preserve only IntegrationError fields; generic failures omit exception text.
        Constraints: no worker URL, sensitive body or successful fallback result.
        """
        if error is not None:
            safe = (
                error
                if isinstance(error, IntegrationError)
                else IntegrationError("local_gateway_failed", "本地中台会话失败，请检查服务日志。")
            )
            message = {
                "kind": "error",
                "code": safe.code,
                "detail": safe.detail,
                "request_id": session.contract["session_id"]
                if session and (session.contract is not None)
                else None,
            }
        else:
            message = {
                "kind": "ended",
                "frames": session.delivered,
                "received": session.received,
                "forwarded": session.forwarded,
                "worker_received": session.worker_received,
                "worker_verified": session.worker_verified,
                "delivered": session.delivered,
            }
        await send({"type": "websocket.send", "text": json.dumps(message)})
        await send({"type": "websocket.close", "code": 1011 if error else 1000})


class IntegrationApplication:
    """Functionality: Wrap software ASGI with a separate local integration surface.
    Logic: new local routes/assets/page injection; existing HTTP/Echo/control/MCP delegate.
    Constraints: original software files stay intact; this entry explicitly blocks external
    features.
    """

    def __init__(self, software, gateway=None, health_reader=read_health):
        """Functionality: Bind unchanged software application and explicit transport test seams.
        Inputs: software callable/gateway/health reader. Outputs: ASGI wrapper.
        Logic: no provider call or model loading. Constraints: local-only behavior is
        entry-specific.
        """
        self.software = software
        self.gateway = gateway if gateway is not None else LocalGateway()
        self.health_reader = health_reader

    async def __call__(self, scope, receive, send):
        """Functionality: Route integration operations while delegating original software paths.
        Inputs: ASGI scope/receive/send. Outputs: software or isolated local responses.
        Logic: block HF/scene/maps before original handler invocation; serve fixed integration
        assets.
        Constraints: no original implementation edits, public worker URL or automatic external
        calls.
        """
        path, kind = scope.get("path", ""), scope["type"]
        if kind == "websocket" and path == "/ws/detect/local/":
            return await self.gateway(scope, receive, send)
        if kind == "websocket" and path == "/ws/detect/hf/":
            await receive()
            await send({"type": "websocket.accept"})
            await send(
                {"type": "websocket.send", "text": json.dumps({"kind": "error", **LOCAL_DISABLED})}
            )
            return await send({"type": "websocket.close", "code": 1008})
        if kind == "http":
            if path.startswith(("/scene/", "/maps/")):
                return await http_response(send, 403, LOCAL_DISABLED)
            if path == "/integration/status/":
                return await self.status(scope, send)
            if path in {"/live/", "/integration/video/"} or path.startswith("/integration/assets/"):
                return await self.asset(scope, send)
        await self.software(scope, receive, send)

    async def asset(self, scope, send):
        """Functionality: Serve fixed frontend modules or inject the independent local MVP entry.
        Inputs: fixed route/method. Outputs: no-store resource/404/405.
        Logic: whitelist names before any read; fixed video page uses file media and denies camera;
        only live page receives the independent camera module/data attribute.
        Constraints: no arbitrary traversal, writes to index.html or exposure of project/private
        files.
        """
        if scope.get("method") not in {"GET", "HEAD"}:
            return await http_response(send, 405, {"code": "method_not_allowed"})
        path = scope["path"]
        try:
            if path == "/live/":
                text = (FRONTEND / "index.html").read_text(encoding="utf-8")
                text = text.replace("<html ", '<html data-local-mvp="true" ', 1)
                text = text.replace(
                    "</head>",
                    '<link rel="stylesheet" href="../integration/assets/local-mvp.css">'
                    '<script type="module" src="../integration/assets/local-mvp.js"></script>'
                    "</head>",
                    1,
                )
                body, mime = text.encode(), "text/html; charset=utf-8"
            elif path == "/integration/video/":
                body = (FRONTEND / "integration/video.html").read_bytes()
                mime = "text/html; charset=utf-8"
            else:
                name = path.removeprefix("/integration/assets/")
                if name not in ASSETS:
                    return await http_response(send, 404, {"code": "not_found"})
                body, mime = (FRONTEND / "integration" / name).read_bytes(), ASSETS[name]
            await http_response(
                send, 200, body, mime, head=scope.get("method") == "HEAD",
                camera_access=path != "/integration/video/",
            )
        except OSError:
            await http_response(send, 404, {"code": "integration_asset_missing"})

    async def status(self, scope, send):
        """Functionality: Report worker availability separately from software/database health.
        Inputs: read-only same-origin request. Outputs: actual unloaded/busy/inference state or 503.
        Logic: bounded explicit health request; no GPU startup, retries or fake ready state.
        Constraints: safe status schema, no internal URL or credentials returned to the browser.
        """
        if scope.get("method") != "GET":
            return await http_response(send, 405, {"code": "method_not_allowed"})
        headers = dict(scope.get("headers", []))
        host = headers.get(b"host", b"").decode("ascii", "replace")
        origin = headers.get(b"origin", b"").decode("ascii", "replace")
        if (
            scope.get("query_string")
            or host not in {urlsplit(item).netloc for item in web_origins()}
            or (origin and (origin not in web_origins() or urlsplit(origin).netloc != host))
        ):
            return await http_response(send, 403, {"code": "origin_rejected"})
        try:
            _, health_url = inference_url()
            body = await asyncio.to_thread(self.health_reader, health_url)
            await http_response(
                send, 200, {"mode": "local", "external_services": "disabled", **body}
            )
        except Exception as caught:
            code = (
                caught.code if isinstance(caught, IntegrationError) else "local_worker_unavailable"
            )
            logger.warning(
                "Local worker status failed code=%s type=%s", code, type(caught).__name__
            )
            await http_response(
                send,
                503,
                {
                    "mode": "local",
                    "provider": "local",
                    "ready": False,
                    "loaded": False,
                    "code": code,
                    "detail": "本地推理进程不可用，请检查独立推理服务。",
                },
            )
