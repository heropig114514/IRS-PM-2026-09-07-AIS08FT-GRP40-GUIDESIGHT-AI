"""Responsibilities: 有资源边界的内存 WebSocket 回传，不解析、存储或转发图像。
Implementation: 验证同源及连接上限；二进制原样回传，JSON ping 返回匹配 id 的 pong。
Related Modules: config.asgi；frontend/echo-client.js 提供单个在途请求与字节校验。
Declaration Index:
- echo_socket — 管理一条连接的准入、回传、超时和释放。
Variable Index:
- MAX_FRAME_BYTES — 1 MiB 单帧上限；MAX_TEXT_BYTES — 1024 字节控制消息上限。
- MAX_CONNECTIONS — 单 worker 最多 4 条回传连接；IDLE_SECONDS — 15 秒无输入关闭。
- active_connections — 当前已准入连接数；logger — 协议生命周期及错误日志。
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
    """Functionality: 原样回传二进制帧，响应应用级 ping，并限制单实例资源占用。
    Inputs: ASGI scope/receive/send；读取 CAMERA_ALLOWED_ORIGINS 和进程连接计数。
    Outputs: 二进制回传、JSON pong 或明确关闭码；日志只含连接标识和统计。
    Logic: 在首次 await 后无 await 地检查并增加连接数；finally 保证释放。
    Constraints: 仅单 worker；匿名诊断接口，同源校验不等于用户认证；不记录媒体正文。
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
