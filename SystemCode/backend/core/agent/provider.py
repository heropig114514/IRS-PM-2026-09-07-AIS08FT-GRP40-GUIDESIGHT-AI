"""Responsibilities: Bounded Qwen text/tool inference and short WAV transcription.
Implementation: Explicit configuration, shared serialized-context accounting, bounded provider
retries for transient transport/status failures, and streamed response-size enforcement. Diagnostics
record model, status, attempt and payload sizes without prompts, audio, credentials or response
text.
Related Modules: runtime, transport; SystemCode/backend/.env.example.
Declaration Index: AgentError; AgentError.__init__; configuration; configured_context_limit;
provider_http_error; enforce_context_limit; completion; transcribe.
Variable Index: logger — metadata-only diagnostics; DEFAULT/MIN/MAX_CONTEXT_CHARS — context
budget defaults and accepted configuration range; MAX_CONTEXT_CHARS — serialized text/tool request
budget; PROVIDER_TIMEOUT_SECONDS/MAX_PROVIDER_ATTEMPTS/PROVIDER_RETRY_DELAY_SECONDS — bounded
provider retry timing; RETRYABLE_HTTP_STATUSES/RETRYABLE_NETWORK_ERRORS — transient failures;
MAX_AUDIO_BYTES/MAX_RESPONSE_BYTES — media and response limits; BASE_URLS — supported endpoints;
MAX_AUDIO_SECONDS — recording contract.
"""

import asyncio
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
DEFAULT_MAX_CONTEXT_CHARS = 150000
MIN_MAX_CONTEXT_CHARS = 60000
MAX_MAX_CONTEXT_CHARS = 180000
PROVIDER_TIMEOUT_SECONDS = 18
MAX_PROVIDER_ATTEMPTS = 2
PROVIDER_RETRY_DELAY_SECONDS = 0.5
RETRYABLE_HTTP_STATUSES = frozenset({408, 500, 502, 503, 504})
RETRYABLE_NETWORK_ERRORS = (
    httpx2.ConnectError,
    httpx2.ConnectTimeout,
    httpx2.ReadError,
    httpx2.ReadTimeout,
    httpx2.RemoteProtocolError,
    httpx2.WriteError,
    httpx2.WriteTimeout,
)


def configured_context_limit(raw=None):
    """Functionality: Resolve the bounded serialized-request character budget.
    Inputs: Optional PRS_AGENT_MAX_CONTEXT_CHARS text; omitted/blank uses the default.
    Outputs: Integer in the declared minimum/maximum range.
    Logic: Parse one explicit integer and reject malformed or out-of-range settings at startup.
    Constraints: The value counts compact JSON characters, not provider tokens; no silent clamp.
    """
    if raw is None:
        raw = os.environ.get("PRS_AGENT_MAX_CONTEXT_CHARS", "").strip()
    if raw == "":
        return DEFAULT_MAX_CONTEXT_CHARS
    try:
        value = int(raw)
    except (TypeError, ValueError) as error:
        raise ValueError(
            "PRS_AGENT_MAX_CONTEXT_CHARS must be an integer from 60000 to 180000"
        ) from error
    if not MIN_MAX_CONTEXT_CHARS <= value <= MAX_MAX_CONTEXT_CHARS:
        raise ValueError("PRS_AGENT_MAX_CONTEXT_CHARS must be an integer from 60000 to 180000")
    return value


MAX_CONTEXT_CHARS = configured_context_limit()


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
    Inputs: PRS_AGENT_API_KEY/BASE_URL/MODEL, PRS_ASR_MODEL and startup context-budget environment.
    Outputs: Secret-bearing internal dict; missing/invalid settings raise AgentError.
    Logic: Require an explicit key and regional URL; context budget is validated at module startup.
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


def provider_http_error(status):
    """Functionality: Classify an exhausted non-success Qwen HTTP status for safe user guidance.
    Inputs: Final provider status code. Outputs: AgentError with a stable code and status metadata.
    Logic: Distinguish oversized input, rate limiting, credentials and provider outages.
    Constraints: Provider response bodies are not read or exposed; no retry policy is selected here.
    """
    if status == 413:
        code, detail = "qwen_request_too_large", "Qwen rejected the request size."
    elif status == 429:
        code, detail = "qwen_rate_limit", "Qwen request quota or rate limit was reached."
    elif status in {401, 403}:
        code, detail = (
            "qwen_auth_error", "Qwen rejected the configured credentials or model access."
        )
    elif status == 408 or status >= 500:
        code, detail = "qwen_provider_unavailable", "Qwen did not complete the request."
    else:
        code, detail = "qwen_http_error", "Qwen rejected the request."
    return AgentError(code, f"{detail} (HTTP {status})", http_status=status)


def enforce_context_limit(payload, *, kind="agent"):
    """Functionality: Enforce one serialized context budget and report safe size diagnostics.
    Inputs: complete JSON-compatible text/tool request payload and a fixed request kind.
    Outputs: None within budget; raises AgentError when the serialized request is oversized.
    Logic: Count compact Unicode JSON for the full payload and its message/schema components.
    Constraints: Never log payload contents, credentials, endpoint URLs or user text; audio payloads
    use their separate byte/duration boundary and must not call this helper.
    """
    request_chars = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    message_chars = len(json.dumps(
        payload.get("messages", []), ensure_ascii=False, separators=(",", ":"),
    ))
    tool_schema_chars = len(json.dumps(
        payload.get("tools", []), ensure_ascii=False, separators=(",", ":"),
    ))
    if request_chars > MAX_CONTEXT_CHARS:
        logger.warning(
            "Qwen request context rejected kind=%s request_chars=%d message_chars=%d "
            "tool_schema_chars=%d limit_chars=%d",
            kind, request_chars, message_chars, tool_schema_chars, MAX_CONTEXT_CHARS,
        )
        raise AgentError(
            "context_limit",
            "The assistant request exceeds its context limit. Clear the conversation and retry.",
        )


async def completion(messages, tools=None, *, audio=False, required_tool=None):
    """Functionality: Make a bounded Qwen request and validate its assistant message.
    Inputs: messages, discovered tools, audio flag, optional required_tool name; environment.
    Outputs: Assistant content/tool_calls; errors have fixed safe codes.
    Logic: Validate the complete text/tool payload against the shared context budget; permit at most
    two provider attempts for listed transient transport/HTTP failures; stream response bytes under
    a fixed cap. HTTP transport phase timeouts are 18 seconds, with a 0.5-second pause between
    attempts; the runtime enforces a 40-second deadline for each completion call. Redirects are
    disabled.
    Constraints: Only Qwen generation/transcription calls are retried; returned MCP/browser/device
    actions are not replayed. Text/audio is sent to the configured region, never logged or
    persisted.
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
        enforce_context_limit(payload)
    try:
        async with httpx2.AsyncClient(
            timeout=PROVIDER_TIMEOUT_SECONDS, follow_redirects=False, trust_env=False
        ) as client:
            for attempt in range(1, MAX_PROVIDER_ATTEMPTS + 1):
                retry_status = None
                started = time.monotonic()
                try:
                    async with client.stream(
                        "POST",
                        config["url"] + "/chat/completions",
                        json=payload,
                        headers={"Authorization": "Bearer " + config["key"]},
                    ) as response:
                        logger.info(
                            "Qwen response kind=%s model=%s status=%s attempt=%d elapsed_ms=%d",
                            "asr" if audio else "agent",
                            payload["model"],
                            response.status_code,
                            attempt,
                            (time.monotonic() - started) * 1000,
                        )
                        if response.status_code != 200:
                            if (response.status_code in RETRYABLE_HTTP_STATUSES
                                    and attempt < MAX_PROVIDER_ATTEMPTS):
                                retry_status = response.status_code
                            else:
                                error = provider_http_error(response.status_code)
                                logger.warning(
                                    "Qwen request failed kind=%s status=%d attempt=%d code=%s",
                                    "asr" if audio else "agent", response.status_code,
                                    attempt, error.code,
                                )
                                raise error
                        else:
                            raw = bytearray()
                            async for chunk in response.aiter_bytes():
                                raw.extend(chunk)
                                if len(raw) > MAX_RESPONSE_BYTES:
                                    raise AgentError(
                                        "qwen_response_limit", "Qwen 返回超过大小限制。"
                                    )
                            break
                except RETRYABLE_NETWORK_ERRORS as error:
                    if attempt < MAX_PROVIDER_ATTEMPTS:
                        logger.warning(
                            "Qwen transient transport failure; retrying kind=%s attempt=%d "
                            "exception_type=%s",
                            "asr" if audio else "agent", attempt, type(error).__name__,
                        )
                        await asyncio.sleep(PROVIDER_RETRY_DELAY_SECONDS)
                        continue
                    logger.warning(
                        "Qwen network failed kind=%s attempts=%d exception_type=%s",
                        "asr" if audio else "agent", attempt, type(error).__name__,
                    )
                    raise AgentError(
                        "qwen_network_error", "无法完成 Qwen 请求，请检查网络。"
                    ) from error
                except httpx2.HTTPError as error:
                    logger.warning(
                        "Qwen network failed kind=%s attempts=%d exception_type=%s",
                        "asr" if audio else "agent", attempt, type(error).__name__,
                    )
                    raise AgentError(
                        "qwen_network_error", "无法完成 Qwen 请求，请检查网络。"
                    ) from error
                if retry_status is not None:
                    logger.warning(
                        "Qwen transient HTTP status; retrying kind=%s status=%d "
                        "attempt=%d next_attempt=%d",
                        "asr" if audio else "agent", retry_status, attempt, attempt + 1,
                    )
                    await asyncio.sleep(PROVIDER_RETRY_DELAY_SECONDS)
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
            "qwen_network_error", "无法完成 Qwen 请求，请检查网络。"
        ) from error
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise AgentError("qwen_invalid_response", "Qwen 返回不符合消息契约。") from error


async def transcribe(encoded):
    """Functionality: Validate and transcribe one browser-produced 16 kHz mono PCM16 WAV.
    Inputs: Base64 WAV string; Outputs: nonempty transcript of at most 2000 characters.
    Logic: Validate size, WAV metadata, duration and actual frames before any external call.
    Constraints: Maximum 20 seconds; no file writes, format conversion or transcription rewriting.
    The provider boundary may retry one transient request; recognized text is subsequently
    displayed as the user's submitted command.
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
