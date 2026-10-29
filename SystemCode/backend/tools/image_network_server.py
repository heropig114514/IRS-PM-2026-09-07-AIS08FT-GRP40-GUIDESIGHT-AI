"""Standalone LAN image echo test. No model, credentials, database or image persistence."""

import argparse
import asyncio
import ipaddress
import json
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
ASSETS = {
    "/": ("network/index.html", "text/html; charset=utf-8"),
    "/network.js": ("network/network.js", "text/javascript"),
    "/network.css": ("network/network.css", "text/css"),
    "/echo-client.js": ("echo-client.js", "text/javascript"),
}
MAX_BYTES = 1024 * 1024


class ImageNetworkApp:
    def __init__(self, host, port):
        self.authority = f"{host}:{port}"
        self.origin = f"http://{self.authority}"
        self.active = 0

    async def __call__(self, scope, receive, send):
        headers = dict(scope.get("headers", []))
        host = headers.get(b"host", b"").decode("ascii", "replace")
        origin = headers.get(b"origin", b"").decode("ascii", "replace")
        valid = host == self.authority and not scope.get("query_string")
        if scope["type"] == "http":
            status, body, mime = 404, b"Not found", "text/plain"
            if not valid or origin and origin != self.origin:
                status, body = 403, b"Origin rejected"
            elif scope["method"] not in {"GET", "HEAD"}:
                status, body = 405, b"Method not allowed"
            elif scope["path"] in ASSETS:
                name, mime = ASSETS[scope["path"]]
                body = (FRONTEND / name).read_bytes()
                status = 200
            await send(
                {
                    "type": "http.response.start",
                    "status": status,
                    "headers": [
                        (b"content-type", mime.encode()),
                        (b"content-length", str(len(body)).encode()),
                        (b"cache-control", b"no-store"),
                        (b"x-content-type-options", b"nosniff"),
                        (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
                        (b"referrer-policy", b"no-referrer"),
                        (
                            b"content-security-policy",
                            (
                                "default-src 'self'; script-src 'self'; style-src 'self'; "
                                f"connect-src 'self' ws://{self.authority}; img-src 'self' blob:; "
                                "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
                            ).encode(),
                        ),
                    ],
                }
            )
            await send(
                {"type": "http.response.body", "body": b"" if scope["method"] == "HEAD" else body}
            )
            return
        if scope["type"] != "websocket":
            return
        first = await receive()
        if (
            first["type"] != "websocket.connect"
            or not valid
            or origin != self.origin
            or scope["path"] != "/ws/echo/"
            or scope.get("subprotocols")
        ):
            await send({"type": "websocket.close", "code": 1008})
            return
        if self.active >= 4:
            await send({"type": "websocket.close", "code": 1013})
            return
        self.active += 1
        try:
            await send({"type": "websocket.accept"})
            for _ in range(400):
                message = await asyncio.wait_for(receive(), 15)
                if message["type"] == "websocket.disconnect":
                    return
                if message["type"] != "websocket.receive":
                    break
                payload = message.get("bytes")
                if payload is not None:
                    if not 0 < len(payload) <= MAX_BYTES:
                        await send({"type": "websocket.close", "code": 1009})
                        return
                    await send({"type": "websocket.send", "bytes": payload})
                else:
                    raw = message.get("text", "")
                    if len(raw.encode()) > 1024:
                        break
                    command = json.loads(raw)
                    if (
                        not isinstance(command, dict)
                        or set(command) != {"type", "id"}
                        or command["type"] != "ping"
                        or not isinstance(command["id"], str)
                        or not 1 <= len(command["id"]) <= 64
                    ):
                        break
                    await send(
                        {
                            "type": "websocket.send",
                            "text": json.dumps({"type": "pong", "id": command["id"]}),
                        }
                    )
            await send({"type": "websocket.close", "code": 1008})
        except (TimeoutError, ValueError, OSError):
            await send({"type": "websocket.close", "code": 1008})
        finally:
            self.active -= 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="Explicit private LAN IPv4 address")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    try:
        address = ipaddress.IPv4Address(args.host)
    except ipaddress.AddressValueError:
        parser.error("Use an explicit private IPv4 address")
    if (
        not address.is_private
        or address.is_unspecified
        or address.is_multicast
        or not 1024 <= args.port <= 65535
    ):
        parser.error(
            "Use a private IPv4 address and port 1024..65535; wildcard binding is disabled"
        )
    import uvicorn

    print(f"Image network test: http://{address}:{args.port}/", flush=True)
    uvicorn.run(
        ImageNetworkApp(str(address), args.port),
        host=str(address),
        port=args.port,
        lifespan="off",
        access_log=False,
        ws_max_size=MAX_BYTES,
        ws_max_queue=1,
        ws_per_message_deflate=False,
        ws_ping_interval=None,
    )


if __name__ == "__main__":
    main()
