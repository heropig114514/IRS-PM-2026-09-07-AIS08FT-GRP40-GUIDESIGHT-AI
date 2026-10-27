"""Responsibilities: Resource-bounded in-memory WebSocket echo; images are not parsed, stored, or
forwarded.
Implementation: Validate origin and connection limits; echo binary payloads unchanged and answer
JSON pings with a matching pong ID.
Related Modules: config.asgi; SystemCode/frontend/echo-client.js enforces one in-flight
request and byte equality.
Declaration Index:
- echo_socket — Admit, echo, time out, and release one connection.
Variable Index:
- MAX_FRAME_BYTES — 1 MiB per-frame limit; MAX_TEXT_BYTES — 1,024-byte control-message limit.
- MAX_CONNECTIONS — At most four echo connections per worker; IDLE_SECONDS — Close after 15 seconds
without input.
- active_connections — Current admitted connection count; logger — Protocol lifecycle and error
logs.
"""

import asyncio
import json
import logging
import time
import uuid
from urllib.parse import urlsplit

from django.conf import settings

MAX_FRAME_BYTES = 1024 * 1024
MAX_TEXT_BYTES = 1024
MAX_CONNECTIONS = 4
IDLE_SECONDS = 15
active_connections = 0
logger = logging.getLogger(__name__)


async def echo_socket(scope, receive, send):
    """Functionality: Echo binary frames, answer application-level pings, and bound per-process
    resource use.
    Inputs: ASGI scope/receive/send; reads CAMERA_ALLOWED_ORIGINS and process connection count.
    Outputs: Binary echo, JSON pong, or explicit close code; logs contain only connection IDs and
    counts.
    Logic: Check and increment the connection count without an await after the first await; finally
    always releases the slot.
    Constraints: Single worker only; this is an anonymous diagnostic endpoint, and same-origin
    validation is not user authentication; never log media bodies.
    """
    global active_connections
    first = await receive()
    if first["type"] != "websocket.connect":
        return
    headers = dict(scope.get("headers", []))
    origin = headers.get(b"origin", b"").decode("ascii", errors="replace")
    host = headers.get(b"host", b"").decode("ascii", errors="replace")
    if origin not in settings.CAMERA_ALLOWED_ORIGINS or host != urlsplit(origin).netloc:
        logger.warning("echo rejected reason=origin_or_host")
        await send({"type": "websocket.close", "code": 1008})
        return
    if active_connections >= MAX_CONNECTIONS:
        logger.warning("echo rejected reason=capacity active=%s", active_connections)
        await send({"type": "websocket.close", "code": 1013})
        return
    active_connections += 1
    connection_id = uuid.uuid4().hex[:12]
    started = time.monotonic()
    frames = total_bytes = 0
    logger.info("echo admitted connection=%s active=%s", connection_id, active_connections)
    try:
        await send({"type": "websocket.accept"})
        while True:
            try:
                message = await asyncio.wait_for(receive(), timeout=IDLE_SECONDS)
            except TimeoutError:
                logger.warning("echo idle timeout connection=%s", connection_id)
                await send({"type": "websocket.close", "code": 1008})
                return
            if message["type"] == "websocket.disconnect":
                return
            if message["type"] != "websocket.receive":
                raise ValueError("Unexpected ASGI message type")
            payload = message.get("bytes")
            if payload is not None:
                if not 0 < len(payload) <= MAX_FRAME_BYTES:
                    logger.warning(
                        "echo invalid frame connection=%s size=%s", connection_id, len(payload)
                    )
                    await send({"type": "websocket.close", "code": 1009})
                    return
                await send({"type": "websocket.send", "bytes": payload})
                frames += 1
                total_bytes += len(payload)
                continue
            text = message.get("text", "")
            if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
                logger.warning("echo oversized control connection=%s", connection_id)
                await send({"type": "websocket.close", "code": 1009})
                return
            try:
                command = json.loads(text)
            except (json.JSONDecodeError, TypeError):
                command = None
            if (
                not isinstance(command, dict)
                or set(command) != {"type", "id"}
                or command["type"] != "ping"
                or not isinstance(command["id"], str)
                or not 1 <= len(command["id"]) <= 64
            ):
                logger.warning("echo invalid control connection=%s", connection_id)
                await send({"type": "websocket.close", "code": 1008})
                return
            await send(
                {
                    "type": "websocket.send",
                    "text": json.dumps({"type": "pong", "id": command["id"]}),
                }
            )
    except OSError:
        logger.info("echo peer disconnected during send connection=%s", connection_id)
    except Exception:
        logger.exception("echo processing failed connection=%s", connection_id)
        try:
            await send({"type": "websocket.close", "code": 1011})
        except OSError:
            logger.warning(
                "echo error close failed connection=%s peer_unavailable=true", connection_id
            )
    finally:
        active_connections -= 1
        logger.info(
            "echo released connection=%s frames=%s bytes=%s seconds=%.2f active=%s",
            connection_id,
            frames,
            total_bytes,
            time.monotonic() - started,
            active_connections,
        )
