"""Responsibilities: Allocate the sole fixed-host ZeroGPU live API through private HTTPS.
Implementation: One bounded POST/SSE transaction per call, sharing the relay's deadline.
Related Modules: core.hf_video_relay; zerogpu.app.detect_live.
Declaration Index: chunks — bounded reads; provider_error — safe reason/numeric diagnostics;
exchange — private JSON/SSE request.
Variable Index: HOST — owned Space; MAX_RESPONSE_BYTES — 2 MiB completion boundary;
logger — phase/status/type/reason/seconds diagnostics without provider bodies or credentials.
"""

import http.client
import json
import logging
import re
import time

from .detector import DetectionError

HOST = "heropig114514-prs-yolo26.hf.space"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
logger = logging.getLogger(__name__)


def chunks(response, socket, deadline, limit):
    """Functionality: Bound response bytes and total read time.
    Inputs: HTTP response, retained socket, monotonic deadline and byte limit.
    Outputs: byte iterator; timeout/oversize propagate to the allocation task.
    Logic: refresh remaining socket timeout before each read; allow one overflow byte.
    Constraints: no retries, body logging or unbounded accumulation.
    """
    consumed = 0
    while consumed <= limit:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("ZeroGPU response deadline")
        if socket is not None:
            socket.settimeout(remaining)
        chunk = response.read1(min(65536, limit + 1 - consumed))
        if not chunk:
            return
        consumed += len(chunk)
        if consumed > limit:
            raise ValueError("ZeroGPU response exceeds byte limit")
        yield chunk


def provider_error(payload):
    """Functionality: Convert known HF allocation failures into explicit safe public errors.
    Inputs: bounded parsed SSE error JSON; Outputs: DetectionError with no provider body.
    Logic: recognize the pinned scheduler's complete reason phrases/title; extract only bounded
    numeric quota fields. Unknown errors remain inference failures even if they mention quota.
    Constraints: never log/return arbitrary text, token, capability, URL or camera data; no retry.
    HF rounds its displayed remaining seconds; preflight retains the exact fractional balance.
    """
    message = payload.get("error", "") if isinstance(payload, dict) else ""
    title = payload.get("title") if isinstance(payload, dict) else None
    message = message if isinstance(message, str) else ""
    seconds = re.search(r"\((\d{1,6})s requested vs\. (\d{1,6})s left\)", message)
    reason, detail = "inference_failed", "ZeroGPU 推理失败，请检查 Space 日志。"
    if "You have exceeded your ZeroGPU runs limit." in message:
        reason, detail = "runs_exceeded", "ZeroGPU 今日免费调用次数已用尽，请等待额度恢复。"
    elif "too many ZeroGPU credits allocated to running tasks" in message:
        reason, detail = "pending_credits", "ZeroGPU 额度被运行中的任务占用，请等待任务结束。"
    elif title == "ZeroGPU illegal duration":
        reason, detail = "illegal_duration", "ZeroGPU 申请时长超过当前账户允许的上限。"
    elif "Space app has reached its GPU limit." in message:
        reason, detail = "space_quota_exceeded", "Space GPU 额度受限，请检查请求身份和 HF 日志。"
    elif seconds or title == "ZeroGPU quota exceeded":
        reason, detail = "quota_exceeded", "ZeroGPU 免费额度不足，请等待额度恢复。"
        if seconds:
            detail = (f"ZeroGPU 免费额度不足：HF 要求{seconds[1]}秒额度，"
                      f"返回剩余{seconds[2]}秒（取整值）。请等待额度恢复。")
    elif "No GPU was available" in message:
        reason, detail = "busy", "ZeroGPU 暂无可用 GPU，请稍后手动开始。"
    elif "GPU task aborted" in message:
        reason, detail = "worker_failed", "ZeroGPU 工作进程已中止，请检查 Space 日志。"
    logger.warning("ZeroGPU service rejected reason=%s requested_seconds=%s remaining_seconds=%s",
                   reason, int(seconds[1]) if seconds else None,
                   int(seconds[2]) if seconds else None)
    return DetectionError("hf_" + reason, detail, 503)


def exchange(method, path, body, deadline, stream=False, *, token):
    """Functionality: Execute one private fixed-host HTTPS call and parse JSON/SSE completion.
    Inputs: method/path/body, shared monotonic deadline, SSE flag and explicit private HF token.
    Outputs: parsed JSON; failure propagates, never an alternative result.
    Logic: verified TLS, bounded reads, strict event framing, heartbeat/complete/error handling;
    retain the socket before getresponse may detach it, always close this request's connection.
    Constraints: no redirect, anonymous request, token cache, callback, connection reuse or retry.
    Failures log location/status/type and classified reason/numeric quota fields, never raw text.
    """
    if (
        not isinstance(token, str)
        or not token.startswith("hf_")
        or len(token) > 4096
        or any(c.isspace() for c in token)
    ):
        raise DetectionError("hf_not_configured", "服务端 HF token 格式无效。", 503)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("ZeroGPU request deadline")
    connection = http.client.HTTPSConnection(HOST, timeout=remaining)
    started, phase = time.monotonic(), "request"
    try:
        connection.request(
            method,
            path,
            body,
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        socket = connection.sock
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("ZeroGPU headers deadline")
        if socket is not None:
            socket.settimeout(remaining)
        phase = "headers"
        response = connection.getresponse()
        if response.status != 200:
            logger.warning("ZeroGPU HTTP rejected method=%s status=%d", method, response.status)
            if response.status == 429:
                raise DetectionError("hf_busy", "ZeroGPU 请求受限，请稍后手动开始。", 429)
            raise DetectionError(
                "hf_unavailable", "ZeroGPU 接口未就绪，请检查 Space 或私有配置。", 503
            )
        phase = "body"
        content_type = response.getheader("Content-Type", "").split(";")[0]
        if content_type != ("text/event-stream" if stream else "application/json"):
            raise ValueError("Unexpected ZeroGPU response media type")
        if not stream:
            return json.loads(b"".join(chunks(response, socket, deadline, 4096)))
        pending = b""
        for chunk in chunks(response, socket, deadline, MAX_RESPONSE_BYTES):
            pending += chunk
            while b"\n\n" in pending:
                block, pending = pending.split(b"\n\n", 1)
                lines = block.decode("utf-8").splitlines()
                event = [line[7:] for line in lines if line.startswith("event: ")]
                data = [line[6:] for line in lines if line.startswith("data: ")]
                if len(event) != 1 or len(data) != 1:
                    raise ValueError("Malformed ZeroGPU SSE event")
                if event[0] == "complete":
                    return json.loads(data[0])
                if event[0] == "error":
                    raise provider_error(json.loads(data[0]))
                if event[0] != "heartbeat":
                    raise ValueError("Unexpected ZeroGPU SSE event")
        raise ValueError("ZeroGPU stream ended without completion")
    except Exception as error:
        logger.warning(
            "ZeroGPU allocation transport failed method=%s phase=%s type=%s",
            method,
            phase,
            type(error).__name__,
        )
        raise
    finally:
        connection.close()
        logger.info(
            "ZeroGPU allocation transport ended method=%s phase=%s elapsed_ms=%.1f",
            method,
            phase,
            (time.monotonic() - started) * 1000,
        )
