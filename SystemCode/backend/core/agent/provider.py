"""Responsibilities: Bounded Qwen text/tool inference and short WAV transcription.
Implementation: One asynchronous HTTPS request per call; explicit configuration, no retries;
model identity logged for reproducible evaluations without prompts or credentials.
Related Modules: runtime, transport; SystemCode/backend/.env.example.
Declaration Index: AgentError; AgentError.__init__; configuration; completion; transcribe.
Variable Index: logger — metadata-only diagnostics; MAX_AUDIO_BYTES/MAX_RESPONSE_BYTES — limits;
BASE_URLS — supported regional endpoints; MAX_AUDIO_SECONDS — recording contract.
"""

import base64
import io
import json
import logging
import os
import time
import wave

import httpx2

logger = logging.getLogger(__name__)
BASE_URLS = {
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
}
MAX_AUDIO_SECONDS = 20
MAX_AUDIO_BYTES = 640044
MAX_RESPONSE_BYTES = 262144


class AgentError(Exception):
    """Functionality: Carry safe public diagnostics without upstream bodies or secrets.
    Logic: Stable code, locally authored diagnostic text and optional HTTP status cross
    trusted internal boundaries.
    Constraints: Never construct detail from a provider exception string.
    """

    def __init__(self, code, detail, *, http_status=None):
        """Functionality: Initialize a safe service diagnostic.
        Inputs: stable code, locally authored detail and optional HTTP status; Outputs: exception.
        Logic: Retain bounded-classification inputs for the MCP adapter. Constraints: detail must
        not contain provider response bodies, credentials or raw exception text; no side effects.
        """
        super().__init__(detail)
        self.code, self.detail = code, detail
        self.http_status = http_status


def configuration():
    """Functionality: Validate the independent agent configuration.
    Inputs: PRS_AGENT_API_KEY/BASE_URL/MODEL and PRS_ASR_MODEL environment.
    Outputs: Secret-bearing internal dict; missing/invalid settings raise AgentError.
    Logic: Require an explicit key and regional URL; model defaults are documented.
    Constraints: Never log or return this dict to a browser; no regional/model fallback.
    """
    config = {
        "key": os.environ.get("PRS_AGENT_API_KEY", "").strip(),
        "url": os.environ.get("PRS_AGENT_BASE_URL", "").strip().rstrip("/"),
        "model": os.environ.get("PRS_AGENT_MODEL", "qwen3.6-plus").strip(),
        "asr_model": os.environ.get("PRS_ASR_MODEL", "qwen3-asr-flash").strip(),
    }
    if (
        not config["key"]
        or config["url"] not in BASE_URLS
        or not all(config[name] and len(config[name]) <= 100 for name in ("model", "asr_model"))
    ):
        raise AgentError("agent_not_configured", "请配置服务端 PRS_AGENT_* 和 PRS_ASR_MODEL。")
    return config


async def completion(messages, tools=None, *, audio=False, required_tool=None):
    """Functionality: Make one bounded Qwen request and validate its assistant message.
    Inputs: messages, discovered tools, audio flag, optional required_tool name; environment.
    Outputs: Assistant content/tool_calls; errors have fixed safe codes.
    Logic: Stream only transport bytes to enforce a response cap; enforce a 40s total caller
    deadline in runtime as well as HTTP phase timeouts; disable redirects and retries.
    Constraints: Text/audio is sent to the configured Qwen region, never logged or persisted.
    """
    config = configuration()
    payload = {
        "model": config["asr_model" if audio else "model"],
        "messages": messages,
        "stream": False,
    }
    if audio:
        payload["asr_options"] = {"enable_itn": False}
    else:
        payload.update(temperature=0, max_tokens=1200, enable_thinking=False)
        if tools:
            payload.update(
                tools=tools,
                parallel_tool_calls=False,
                tool_choice=(
                    {"type": "function", "function": {"name": required_tool}}
                    if required_tool
                    else "auto"
                ),
            )
    started = time.monotonic()
    try:
        async with httpx2.AsyncClient(
            timeout=35, follow_redirects=False, trust_env=False
        ) as client:
            async with client.stream(
                "POST",
                config["url"] + "/chat/completions",
                json=payload,
                headers={"Authorization": "Bearer " + config["key"]},
            ) as response:
                logger.info(
                    "Qwen response kind=%s model=%s status=%s elapsed_ms=%d",
                    "asr" if audio else "agent",
                    payload["model"],
                    response.status_code,
                    (time.monotonic() - started) * 1000,
                )
                if response.status_code != 200:
                    raise AgentError(
                        "qwen_http_error",
                        f"Qwen 请求失败（HTTP {response.status_code}），"
                        "请检查模型权限、额度和区域配置；未自动重试。",
                    )
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise AgentError("qwen_response_limit", "Qwen 返回超过大小限制。")
        message = json.loads(raw)["choices"][0]["message"]
        if not isinstance(message, dict) or message.get("role") != "assistant":
            raise ValueError("Invalid role")
        content, calls = message.get("content"), message.get("tool_calls")
        if content is not None and not isinstance(content, str):
            raise ValueError("Invalid content")
        if calls is not None and (not isinstance(calls, list) or len(calls) > 8):
            raise ValueError("Invalid calls")
        if not calls and not (content and content.strip()):
            raise AgentError("qwen_empty_response", "未识别到有效内容，请重新输入。")
        return {
            "role": "assistant",
            "content": content or "",
            **({"tool_calls": calls} if calls else {}),
        }
    except httpx2.HTTPError as error:
        logger.warning(
            "Qwen network failed kind=%s exception_type=%s",
            "asr" if audio else "agent",
            type(error).__name__,
        )
        raise AgentError(
            "qwen_network_error", "无法完成 Qwen 请求，请检查网络；未自动重试。"
        ) from error
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise AgentError("qwen_invalid_response", "Qwen 返回不符合消息契约。") from error


async def transcribe(encoded):
    """Functionality: Validate and transcribe one browser-produced 16 kHz mono PCM16 WAV.
    Inputs: Base64 WAV string; Outputs: nonempty transcript of at most 2000 characters.
    Logic: Validate size, WAV metadata, duration and actual frames before any external call.
    Constraints: Maximum 20 seconds; no file writes, format conversion, retries or transcription
    rewriting. Recognition is subsequently displayed as the user's submitted command.
    """
    try:
        if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_AUDIO_BYTES + 2) // 3):
            raise ValueError("Audio size")
        data = base64.b64decode(encoded, validate=True)
        if len(data) > MAX_AUDIO_BYTES:
            raise ValueError("Audio size")
        with wave.open(io.BytesIO(data), "rb") as wav:
            frames = wav.getnframes()
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 16000):
                raise ValueError("Audio format")
            if not 1600 <= frames <= MAX_AUDIO_SECONDS * 16000:
                raise ValueError("Audio duration")
            if len(wav.readframes(frames)) != frames * 2:
                raise ValueError("Truncated audio")
    except (ValueError, TypeError, wave.Error, EOFError) as error:
        raise AgentError("invalid_audio", "录音须为 0.1～20 秒、16kHz 单声道 WAV。") from error
    message = await completion(
        [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_audio",
                        "input_audio": {"data": "data:audio/wav;base64," + encoded},
                    }
                ],
            }
        ],
        audio=True,
    )
    text = message["content"].strip()
    if not text or len(text) > 2000 or message.get("tool_calls"):
        raise AgentError("invalid_transcript", "未获得有效的短语音指令，请重新输入。")
    return text
