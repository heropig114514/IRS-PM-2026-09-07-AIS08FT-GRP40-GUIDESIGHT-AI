"""Responsibilities: Expose loopback inference health and timestamped sessions independently.
Implementation: Small ASGI router with lazy native runtime and graceful executor shutdown.
Related Modules: integration.inference.services; integration.protocol; tools.run_local_mvp.
Declaration Index: application; respond.
Variable Index: worker — process-local inference facade; no Django or GPU initialized on import.
"""

import json

from integration.protocol import is_loopback

from .services import InferenceWorker

worker = InferenceWorker()


async def respond(send, status, body):
    """Functionality: Return safe noncached JSON without framework or model initialization.
    Inputs: ASGI send/status/body. Outputs: HTTP response messages.
    Logic: finite compact serialization. Constraints: no credentials, media, or path disclosure.
    """
    raw = json.dumps(body, allow_nan=False).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"cache-control", b"no-store")],
        }
    )
    await send({"type": "http.response.body", "body": raw})


async def application(scope, receive, send):
    """Functionality: Route only isolated health, inference and lifespan scopes.
    Inputs: ASGI scope/receive/send. Outputs: health/frames or explicit rejection.
    Logic: restrict HTTP to loopback; native loading only occurs on a submitted real frame.
    Constraints: one worker bound to 127.0.0.1, no external services, database or hidden fallback.
    """
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await worker.runtime.close()
                await send({"type": "lifespan.shutdown.complete"})
                return
    elif scope["type"] == "websocket":
        if scope.get("path") == "/ws/infer/":
            await worker(scope, receive, send)
        else:
            await receive()
            await send({"type": "websocket.close", "code": 1008})
    elif scope["type"] == "http":
        local = is_loopback(scope.get("client", (None, 0))[0]) and is_loopback(
            scope.get("server", (None, 0))[0]
        )
        if not local:
            await respond(
                send,
                403,
                {
                    "code": "origin_rejected",
                    "detail": "The inference process accepts local access only.",
                },
            )
        elif scope.get("path") != "/health/" or scope.get("query_string"):
            await respond(send, 404, {"code": "not_found"})
        elif scope.get("method") != "GET":
            await respond(send, 405, {"code": "method_not_allowed"})
        else:
            await respond(send, 200, worker.runtime.status())
