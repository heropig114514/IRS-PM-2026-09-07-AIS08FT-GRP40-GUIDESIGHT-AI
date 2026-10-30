"""Responsibilities: Same-origin authenticated browser WebSocket for conversational control.
Implementation: Prove tab ownership via existing control channel; replay bounded session events.
Related Modules: config.asgi, runtime, core.ai_bridge;
SystemCode/frontend/ai-control.js and agent-client.js.
Declaration Index: agent_socket; pump; handle_message.
Variable Index: logger — safe connection/protocol diagnostics; MAX_MESSAGE_BYTES — audio cap.
"""

import asyncio
import hmac
import json
import logging
import uuid
from urllib.parse import urlsplit

from django.conf import settings

from core.ai_bridge import hub

from .provider import AgentError, configuration
from .runtime import MAX_CONVERSATIONS, Conversation, conversations, prune

logger = logging.getLogger(__name__)
MAX_MESSAGE_BYTES = 860000


async def pump(queue, send):
    """Functionality: Stream queued evidence. Inputs: queue/ASGI send; Outputs: WS frames.
    Logic: Sequential JSON writes; Constraints: cancelled and awaited on disconnect.
    """
    while True:
        message = await queue.get()
        await send({"type": "websocket.send", "text": json.dumps(message, ensure_ascii=False)})


def handle_message(conversation, message):
    """Functionality: Validate a user command before admitting work.
    Inputs: bound conversation and JSON object; Outputs: task/state mutation or AgentError.
    Logic: Separate text/audio/cancel/clear; ping returns a pong for proxy keepalive;
    require UUID and exactly one input.
    Constraints: Unknown fields rejected, text max2000; audio validated before provider calls.
    """
    if not isinstance(message, dict):
        raise AgentError("invalid_message", "消息必须为对象。")
    kind = message.get("type")
    if kind in {"cancel", "clear", "ping"} and set(message) == {"type"}:
        if kind == "cancel":
            conversation.cancel()
        elif kind == "clear":
            conversation.clear()
        elif kind == "ping":
            return {"type": "pong"}
        return
    if kind != "submit" or set(message) not in (
        {"type", "request_id", "text"},
        {"type", "request_id", "audio"},
    ):
        raise AgentError("invalid_message", "不支持的消息格式。")
    try:
        request_id = str(uuid.UUID(message["request_id"]))
    except (ValueError, AttributeError, TypeError) as error:
        raise AgentError("invalid_request_id", "请求编号无效。") from error
    text, audio = message.get("text"), message.get("audio")
    if "text" in message and (not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000):
        raise AgentError("invalid_text", "请输入 1～2000 字的指令。")
    if "audio" in message and (not isinstance(audio, str) or not audio):
        raise AgentError("invalid_audio", "录音为空。")
    configuration()
    conversation.submit(request_id, text.strip() if text is not None else None, audio)


async def agent_socket(scope, receive, send):
    """Functionality: Bind a same-origin socket to a proven browser tab and its conversation.
    Inputs: ASGI scope/receive/send; Outputs: WebSocket events and admitted agent tasks.
    Logic: 10s hello deadline, compare secret obtained from the target tab, reconnect/replay
    on a page handoff; origin and Host must match. Session IDs alone grant no agent access.
    Constraints: No autonomous reconnect, no process sharing; disconnect does not cancel a
    handoff's task (180s total bound). Capacity rejection logs counts only; token/audio/dialogue
    never appear in application logs.
    """
    conversation = queue = sender = None
    headers = dict(scope.get("headers", []))
    origin = headers.get(b"origin", b"").decode("latin1")
    if origin not in settings.CAMERA_ALLOWED_ORIGINS or urlsplit(origin).netloc != headers.get(
        b"host", b""
    ).decode("latin1"):
        await send({"type": "websocket.close", "code": 1008})
        return
    try:
        if (await receive())["type"] != "websocket.connect":
            return
        await send({"type": "websocket.accept"})
        async with asyncio.timeout(10):
            event = await receive()
            raw = event.get("text", "")
            if len(raw) > 1024:
                raise ValueError("hello too large")
            hello = json.loads(raw)
            session_id, token = hello["session_id"], hello["token"]
            if (
                hello.get("type") != "hello"
                or not isinstance(token, str)
                or len(token) != 36
                or not isinstance(session_id, str)
                or len(session_id) > 64
            ):
                raise ValueError("Invalid hello")
            identity = await hub.command(session_id, "agent_identity")
            if not hmac.compare_digest(identity.get("token", ""), token):
                raise ValueError("Tab ownership failed")
        prune()
        conversation = conversations.get(session_id)
        if conversation and not hmac.compare_digest(conversation.token, token):
            raise ValueError("Conversation ownership failed")
        if conversation is None:
            if len(conversations) >= MAX_CONVERSATIONS:
                logger.warning(
                    "Agent capacity reached retained=%d limit=%d",
                    len(conversations),
                    MAX_CONVERSATIONS,
                )
                raise AgentError(
                    "agent_capacity",
                    "The assistant session capacity is currently reached. Refresh the page later.",
                )
            conversation = Conversation(session_id, token)
            conversations[session_id] = conversation
        queue = conversation.subscribe()
        sender = asyncio.create_task(pump(queue, send))
        logger.info("Agent connected session=%s", session_id)
        while True:
            event = await receive()
            if event["type"] == "websocket.disconnect":
                break
            raw = event.get("text", "")
            if not raw or len(raw.encode("utf-8")) > MAX_MESSAGE_BYTES:
                raise ValueError("Message size or binary input")
            try:
                reply = handle_message(conversation, json.loads(raw))
                if reply:
                    queue.put_nowait(reply)
            except AgentError as error:
                queue.put_nowait({"type": "rejected", "code": error.code, "detail": error.detail})
    except AgentError as error:
        await send(
            {
                "type": "websocket.send",
                "text": json.dumps(
                    {"type": "rejected", "code": error.code, "detail": error.detail}
                ),
            }
        )
        await send({"type": "websocket.close", "code": 1008})
    except (ValueError, KeyError, TypeError, TimeoutError, ConnectionError) as error:
        logger.warning("Agent socket rejected exception_type=%s", type(error).__name__)
        await send({"type": "websocket.close", "code": 1008})
    finally:
        if sender:
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
        if conversation and queue:
            conversation.subscribers.discard(queue)
            logger.info("Agent disconnected session=%s", conversation.session_id)
