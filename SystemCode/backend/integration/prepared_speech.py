"""Responsibilities: Serve verified startup PCM and an explicitly selected speech upstream.
Implementation: Fixed clip hashes, canonical same-origin TTS contract and bounded PCM forwarding.
Related Modules: core.agent.tts; integration.mobile/tunnel_asgi; frontend speech-startup assets.
Declaration Index: load_clips; PreparedSpeechApplication; PreparedSpeechApplication.__init__;
PreparedSpeechApplication.__call__; PreparedSpeechApplication.__call__.replay;
PreparedSpeechApplication.__call__.accepted_send; PreparedSpeechApplication.relay;
PreparedSpeechApplication.relay.stream.
Variable Index: TEXTS fixes supported startup wording; ROOT is the public clip directory.
"""
import asyncio
import hashlib
import io
import json
import wave
from pathlib import Path
from urllib.parse import urlsplit

from django.conf import settings
from websockets.asyncio.client import connect

from core.agent.provider import AgentError
from core.agent.tts import (
    LANGUAGES,
    MAX_AUDIO_BYTES,
    send_json,
    validate_request,
    wait_for_disconnect,
)

from .mobile import mobile_origin

TEXTS = {"zh-CN": "语音提醒已开启。", "en-US": "Voice alerts are enabled."}
ROOT = Path(__file__).resolve().parents[2] / "frontend/assets/speech-startup"


def load_clips(root=ROOT):
    """Functionality: Validate startup clips before serving them.
    Inputs: fixed resource directory. Outputs: language/rate/text to PCM mapping.
    Logic: check manifest identity, WAV format and two hashes. Constraints: no generated audio.
    """
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != 1 or len(manifest.get("clips", [])) != 2:
        raise ValueError("Invalid startup audio manifest")
    clips = {}
    for item in manifest["clips"]:
        language = item["language"]
        if (language not in TEXTS or item["text"] != TEXTS[language]
                or item["rate"] != 2 or item["voice"] != "Cherry"
                or item["path"] != language + ".wav"):
            raise ValueError("Invalid startup audio identity")
        raw = (root / item["path"]).read_bytes()
        if len(raw) > 512 * 1024 or hashlib.sha256(raw).hexdigest() != item["sha256"]:
            raise ValueError("Invalid startup audio checksum")
        with wave.open(io.BytesIO(raw), "rb") as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 24000):
                raise ValueError("Invalid startup audio format")
            pcm = audio.readframes(audio.getnframes())
        if (len(pcm) != item["pcm_bytes"] or not any(pcm)
                or hashlib.sha256(pcm).hexdigest() != item["pcm_sha256"]):
            raise ValueError("Invalid startup PCM checksum")
        clips[(LANGUAGES[language], 2, item["text"])] = pcm
    if len(clips) != 2:
        raise ValueError("Missing startup language")
    return clips


class PreparedSpeechApplication:
    """Functionality: Keep startup voice independent of synthesis availability.
    Logic: intercept only canonical TTS requests. Constraints: preserve Origin and request checks.
    """
    def __init__(self, application, *, clips=None, upstream_origin=None, opener=connect):
        """Functionality: Freeze owned resources and optional upstream.
        Inputs: downstream application, clips, HTTPS origin and socket opener. Outputs: wrapper.
        Logic: validate origin immediately. Constraints: no implicit speech-service discovery.
        """
        self.application = application
        self.clips = load_clips() if clips is None else clips
        self.upstream_origin = mobile_origin(upstream_origin) if upstream_origin else None
        self.opener = opener

    async def __call__(self, scope, receive, send):
        """Functionality: Serve fixed startup speech or preserve explicit TTS handling.
        Inputs: ASGI request/channel. Outputs: bounded PCM messages or original application.
        Logic: validate same-origin before accepting. Constraints: no raw text or PCM logging.
        """
        if scope["type"] != "websocket" or scope.get("path") != "/ws/tts/":
            return await self.application(scope, receive, send)
        headers = scope.get("headers", [])
        origins = [value for name, value in headers if name == b"origin"]
        hosts = [value for name, value in headers if name == b"host"]
        allowed = [origin.encode() for origin in settings.CAMERA_ALLOWED_ORIGINS]
        if (len(origins) != 1 or origins[0] not in allowed or len(hosts) != 1
                or urlsplit(origins[0].decode()).netloc.encode() != hosts[0]):
            await receive()
            return await send({"type": "websocket.close", "code": 1008})
        first = await receive()
        if first.get("type") != "websocket.connect":
            return
        # ASGI permits receiving application data only after accepting the connection.
        await send({"type": "websocket.accept"})
        try:
            request_event = await asyncio.wait_for(receive(), 5)
            if request_event.get("type") == "websocket.disconnect":
                return
            request = validate_request(request_event.get("text"))
        except (AgentError, TimeoutError, TypeError):
            await send_json(send, {"type": "error", "request_id": None,
                                  "code": "invalid_speech_request", "detail": "语音请求无效。"})
            return await send({"type": "websocket.close", "code": 1008})
        pcm = self.clips.get((request["language"], request["rate"], request["text"]))
        if pcm is None:
            if self.upstream_origin:
                return await self.relay(request_event["text"], request["request_id"], receive, send)
            buffered = [first, request_event]

            async def replay():
                """Functionality: Replay consumed request events to the original handler.
                Inputs: retained events/channel. Outputs: next event. Logic: consume once.
                Constraints: no request substitution or network retry.
                """
                return buffered.pop(0) if buffered else await receive()

            async def accepted_send(message):
                """Functionality: Preserve the already accepted socket state.
                Inputs: downstream message. Outputs: forwarded message except duplicate accept.
                Logic: filter one message type. Constraints: preserve all content/errors.
                """
                if message["type"] != "websocket.accept":
                    await send(message)

            return await self.application(scope, replay, accepted_send)
        request_id = request["request_id"]
        await send_json(send, {"type": "audio_config", "request_id": request_id,
                              "sample_rate": 24000, "format": "pcm_s16le_mono"})
        for offset in range(0, len(pcm), 8192):
            await send({"type": "websocket.send", "bytes": pcm[offset:offset + 8192]})
        await send_json(send, {"type": "complete", "request_id": request_id,
                              "audio_bytes": len(pcm)})
        await send({"type": "websocket.close", "code": 1000})

    async def relay(self, raw, request_id, receive, send):
        """Functionality: Forward uncovered wording to an explicitly configured speech service.
        Inputs: validated request text/identity and ASGI channels. Outputs: PCM or explicit error.
        Logic: race bounded streaming with disconnect. Constraints: cancel pending work; no retry.
        """
        async def stream():
            """Functionality: Relay one request while validating every response.
            Inputs: selected upstream and request. Outputs: audio messages.
            Logic: validate identity, format and length. Constraints: 55-second/byte bounds.
            """
            url = self.upstream_origin.replace("https://", "wss://", 1) + "/mvp/ws/tts/"
            async with self.opener(url, origin=self.upstream_origin, proxy=None,
                                   open_timeout=15, close_timeout=3, max_size=1024 * 1024) as peer:
                await peer.send(raw)
                configured, total = False, 0
                async with asyncio.timeout(55):
                    while True:
                        event = await peer.recv()
                        if isinstance(event, bytes):
                            total += len(event)
                            if (not configured or not event or len(event) % 2
                                    or total > MAX_AUDIO_BYTES):
                                raise ValueError("Invalid project speech PCM")
                            await send({"type": "websocket.send", "bytes": event})
                            continue
                        message = json.loads(event)
                        if message.get("request_id") != request_id:
                            raise ValueError("Project speech identity mismatch")
                        if message.get("type") == "audio_config" and not configured:
                            if (message.get("sample_rate") != 24000
                                    or message.get("format") != "pcm_s16le_mono"):
                                raise ValueError("Invalid project speech format")
                            configured = True
                            await send_json(send, message)
                        elif message.get("type") == "complete" and total > 0:
                            if message.get("audio_bytes") != total:
                                raise ValueError("Invalid project speech length")
                            await send_json(send, message)
                            return
                        else:
                            raise ValueError("Project speech failed")

        synthesis = asyncio.create_task(stream())
        disconnected = asyncio.create_task(wait_for_disconnect(receive))
        try:
            done, _ = await asyncio.wait(
                {synthesis, disconnected}, return_when=asyncio.FIRST_COMPLETED,
            )
            if disconnected in done:
                return
            await synthesis
            await send({"type": "websocket.close", "code": 1000})
        except Exception:
            await send_json(send, {
                "type": "error", "request_id": request_id,
                "code": "tts_upstream_unavailable", "detail": "项目语音服务暂不可用。",
            })
            await send({"type": "websocket.close", "code": 1011})
        finally:
            for task in (synthesis, disconnected):
                if not task.done():
                    task.cancel()
            await asyncio.gather(synthesis, disconnected, return_exceptions=True)
