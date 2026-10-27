"""Responsibilities: Stream bounded bilingual text-to-speech from the configured Qwen region.
Implementation: Admit one same-origin request, configure a short Qwen3-TTS realtime session,
forward PCM16 chunks as binary WebSocket messages, then close upstream work on
browser disconnect.
Related Modules: provider.configuration; config.asgi; integration.mobile;
SystemCode/frontend/speech-output.js.
Declaration Index: validate_request; upstream_url; upstream_session; wait_for_disconnect;
tts_socket; send_json.
Variable Index: MODEL/VOICE/SAMPLE_RATE fix the synthesis contract;
MAX_TEXT_LENGTH/MAX_MESSAGE_BYTES bound browser input;
MAX_AUDIO_BYTES/MAX_UPSTREAM_MESSAGE_BYTES bound decoded audio and its base64 provider event;
REGIONAL_URLS maps the existing provider region to WSS;
logger records request IDs, lifecycle stages and exception types without text, audio or credentials.
"""

import asyncio
import base64
import binascii
import json
import logging
import uuid
from urllib.parse import urlsplit

from django.conf import settings
from websockets.asyncio.client import connect

from .provider import AgentError, configuration

logger = logging.getLogger(__name__)
MODEL = "qwen3-tts-flash-realtime"
VOICE = "Cherry"
SAMPLE_RATE = 24000
MAX_TEXT_LENGTH = 1000
MAX_MESSAGE_BYTES = 8192
MAX_AUDIO_BYTES = 20_000_000
# Qwen sends audio as base64 inside one JSON event; allow that encoding plus the bounded envelope.
MAX_UPSTREAM_MESSAGE_BYTES = ((MAX_AUDIO_BYTES + 2) // 3) * 4 + MAX_MESSAGE_BYTES
REQUEST_TIMEOUT_SECONDS = 45
REGIONAL_URLS = {
    "https://dashscope.aliyuncs.com/compatible-mode/v1":
        "wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=" + MODEL,
    "https://dashscope-intl.aliyuncs.com/compatible-mode/v1":
        "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime?model=" + MODEL,
}
LANGUAGES = {"zh-CN": "Chinese", "en-US": "English", "auto": "Auto"}


def validate_request(raw):
    """Functionality: Parse and validate one browser synthesis command.
    Inputs: UTF-8 JSON text from a WebSocket; Outputs: request ID, original text, language and rate.
    Logic: Require an exact field set, canonical UUID, bounded nonblank Unicode text and explicit
    supported language/rate values before opening the provider connection.
    Constraints: Text is forwarded unmodified and never logged; no voice, URL or model is browser
    configurable; malformed data raises a locally authored AgentError.
    """
    if not isinstance(raw, str):
        raise AgentError("invalid_message", "语音请求格式或大小无效。")
    try:
        if len(raw.encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise ValueError("message_size")
        message = json.loads(raw)
        if not isinstance(message, dict) or set(message) != {
            "type", "request_id", "text", "language", "rate"
        } or message["type"] != "synthesize":
            raise ValueError("fields")
        request_id = str(uuid.UUID(message["request_id"]))
        text = message["text"]
        language = message["language"]
        rate = message["rate"]
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_LENGTH:
            raise ValueError("text")
        text.encode("utf-8")
        if (
            language not in LANGUAGES
            or isinstance(rate, bool)
            or not isinstance(rate, (int, float))
            or not 0.5 <= rate <= 2
        ):
            raise ValueError("options")
    except (ValueError, TypeError, KeyError, UnicodeEncodeError, json.JSONDecodeError) as error:
        raise AgentError("invalid_speech_request", "语音文本、语言或语速无效。") from error
    return {
        "request_id": request_id,
        "text": text,
        "language": LANGUAGES[language],
        "rate": float(rate),
    }


def upstream_url(config):
    """Functionality: Resolve the provider's already configured regional WebSocket endpoint.
    Inputs: Internal provider configuration containing URL and key. Outputs: fixed WSS URL.
    Logic: Exact mapping prevents request-controlled host selection and preserves regional key
    scope.
    Constraints: Missing or unknown region raises a fixed AgentError; credentials are not returned.
    """
    url = REGIONAL_URLS.get(config.get("url"))
    if not config.get("key") or not url:
        raise AgentError("tts_not_configured", "后端语音服务尚未配置。")
    return url


async def send_json(send, message):
    """Functionality: Send one compact JSON response to the browser WebSocket.
    Inputs: ASGI send callable and bounded response object. Outputs: One text frame.
    Logic: Encode UTF-8 without ASCII escaping for multilingual status messages.
    Constraints: Callers must not include prompt/audio/provider payloads.
    """
    await send({"type": "websocket.send", "text": json.dumps(message, ensure_ascii=False)})


async def wait_for_disconnect(receive):
    """Functionality: Monitor the browser while upstream synthesis is active.
    Inputs: ASGI receive callable. Outputs: normal return on disconnect or AgentError on a second
    request.
    Logic: Keep exactly one synthesis command per socket and surface browser cancellation
    immediately.
    Constraints: This task is canceled when synthesis finishes; it does not accept or queue another
    utterance.
    """
    while True:
        event = await receive()
        if event.get("type") == "websocket.disconnect":
            return
        if event.get("type") == "websocket.receive":
            raise AgentError("invalid_message", "每个语音连接只接受一条请求。")


async def upstream_session(request, config, send, *, opener=connect):
    """Functionality: Synthesize one request with Qwen and forward PCM as it arrives.
    Inputs: Validated request, secret-bearing provider configuration, browser send and WS connector.
    Outputs: First-audio metadata, binary PCM chunks and one completion/error message.
    Logic: Await created/updated acknowledgements, append/commit one complete text, forward each
    decoded audio delta immediately, then stop at response.done; WebSocket closure cancels upstream.
    Constraints: One model call, a 45-second deadline, a 20 MB decoded-output cap and an upstream
    frame bound sized for the base64 representation of that output; provider diagnostics, text,
    binary audio and keys do not enter application logs or browser errors.
    """
    url = upstream_url(config)
    sent = 0
    stage = "connect"
    try:
        async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
            async with opener(
                url,
                additional_headers={"Authorization": "Bearer " + config["key"]},
                origin=urlsplit(url).scheme + "://" + urlsplit(url).netloc,
                compression=None,
                proxy=None,
                open_timeout=10,
                close_timeout=3,
                max_size=MAX_UPSTREAM_MESSAGE_BYTES,
                max_queue=1,
                ping_interval=None,
            ) as peer:
                stage = "session_created"
                created = json.loads(await asyncio.wait_for(peer.recv(), 10))
                if created.get("type") != "session.created":
                    raise AgentError("tts_upstream_protocol", "语音服务会话初始化失败。")
                stage = "session_update"
                await peer.send(json.dumps({
                    "event_id": str(uuid.uuid4()), "type": "session.update",
                    "session": {"voice": VOICE, "mode": "commit",
                        "language_type": request["language"], "response_format": "pcm",
                        "sample_rate": SAMPLE_RATE, "speech_rate": request["rate"]},
                }))
                updated = json.loads(await asyncio.wait_for(peer.recv(), 10))
                if updated.get("type") != "session.updated":
                    raise AgentError("tts_upstream_protocol", "语音服务语言配置失败。")
                stage = "submit_text"
                await peer.send(json.dumps({"event_id": str(uuid.uuid4()),
                    "type": "input_text_buffer.append", "text": request["text"]}))
                await peer.send(json.dumps({"event_id": str(uuid.uuid4()),
                    "type": "input_text_buffer.commit"}))
                await send_json(send, {"type": "audio_config", "request_id": request["request_id"],
                    "sample_rate": SAMPLE_RATE, "format": "pcm_s16le_mono"})
                stage = "receive_audio"
                while True:
                    event = json.loads(await asyncio.wait_for(peer.recv(), 30))
                    kind = event.get("type")
                    if kind == "error":
                        raise AgentError("tts_upstream_error", "后端语音合成失败，请稍后重试。")
                    if kind == "response.audio.delta":
                        encoded = event.get("delta")
                        if not isinstance(encoded, str):
                            raise AgentError("tts_upstream_protocol", "语音服务返回格式无效。")
                        try:
                            chunk = base64.b64decode(encoded, validate=True)
                        except (ValueError, binascii.Error) as error:
                            raise AgentError(
                                "tts_upstream_protocol", "语音服务音频块无效。"
                            ) from error
                        if not chunk or len(chunk) % 2:
                            raise AgentError("tts_upstream_protocol", "语音服务音频块长度无效。")
                        sent += len(chunk)
                        if sent > MAX_AUDIO_BYTES:
                            raise AgentError("tts_audio_limit", "本次语音输出超过大小限制。")
                        await send({"type": "websocket.send", "bytes": chunk})
                    elif kind == "response.done":
                        await send_json(
                            send,
                            {
                                "type": "complete",
                                "request_id": request["request_id"],
                                "audio_bytes": sent,
                            },
                        )
                        logger.info(
                            "TTS completed request=%s audio_bytes=%d",
                            request["request_id"], sent,
                        )
                        return
    except AgentError:
        raise
    except TimeoutError as error:
        logger.warning("TTS timeout request=%s stage=%s", request["request_id"], stage)
        raise AgentError("tts_timeout", "后端语音合成超时，请稍后重试。") from error
    except asyncio.CancelledError:
        raise
    except Exception as error:
        logger.warning("TTS transport failed request=%s stage=%s exception_type=%s",
            request["request_id"], stage, type(error).__name__)
        raise AgentError("tts_upstream_unavailable", "无法连接后端语音服务。") from error


async def tts_socket(scope, receive, send, *, opener=connect):
    """Functionality: Admit and execute one browser-to-server TTS WebSocket request.
    Inputs: Same-origin ASGI WebSocket channels and optional injected provider connector.
    Outputs: Safe JSON lifecycle/error messages and binary PCM chunks; no HTTP response.
    Logic: Require matching configured Host/Origin, exactly one bounded JSON request, then call the
    provider once. A concurrent receive monitor cancels the provider context on browser disconnect.
    Constraints: Same-origin is transport admission only, not user identity; no text persistence,
    authentication bypass to other routes, reconnect, provider detail leakage or hidden retry.
    """
    headers = dict(scope.get("headers", []))
    origin = headers.get(b"origin", b"").decode("latin1")
    host = headers.get(b"host", b"").decode("latin1")
    if origin not in settings.CAMERA_ALLOWED_ORIGINS or urlsplit(origin).netloc != host:
        await send({"type": "websocket.close", "code": 1008})
        return
    if (await receive()).get("type") != "websocket.connect":
        return
    await send({"type": "websocket.accept"})
    request_id = None
    try:
        async with asyncio.timeout(5):
            event = await receive()
        if event.get("type") == "websocket.disconnect" or not isinstance(event.get("text"), str):
            raise AgentError("invalid_message", "缺少语音请求。")
        request = validate_request(event["text"])
        request_id = request["request_id"]
        try:
            config = configuration()
            upstream_url(config)
        except AgentError as error:
            if error.code == "agent_not_configured":
                raise AgentError("tts_not_configured", "后端语音服务尚未配置。") from error
            raise
        logger.info("TTS request accepted request=%s language=%s characters=%d",
            request_id, request["language"], len(request["text"]))
        synthesis = asyncio.create_task(upstream_session(request, config, send, opener=opener))
        disconnected = asyncio.create_task(wait_for_disconnect(receive))
        done, _ = await asyncio.wait({synthesis, disconnected}, return_when=asyncio.FIRST_COMPLETED)
        if disconnected in done:
            error = disconnected.exception()
            synthesis.cancel()
            await asyncio.gather(synthesis, return_exceptions=True)
            if error:
                raise error
            return
        disconnected.cancel()
        await asyncio.gather(disconnected, return_exceptions=True)
        await synthesis
        await send({"type": "websocket.close", "code": 1000})
    except AgentError as error:
        await send_json(send, {"type": "error", "request_id": request_id,
            "code": error.code, "detail": error.detail})
        await send(
            {
                "type": "websocket.close",
                "code": 1011 if error.code.startswith("tts_") else 1008,
            }
        )
    except TimeoutError:
        await send_json(send, {"type": "error", "request_id": request_id,
            "code": "request_timeout", "detail": "语音请求等待超时。"})
        await send({"type": "websocket.close", "code": 1008})
