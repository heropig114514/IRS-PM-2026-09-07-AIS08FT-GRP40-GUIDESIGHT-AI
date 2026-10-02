"""Responsibilities: Fixed-target ZeroGPU Gradio transport and annotated-frame validation.
Implementation: HTTPS POST/SSE under one 55-second deadline; bounded JSON; no retries or fallback.
Related Modules: core.hf_vision; zerogpu.protocol/app; models/yolo26n.json.
Declaration Index: chunks — bounded reader; exchange — HTTP/SSE transaction;
validate_result — model/JPEG/box contract; hf_request — readiness or one frame.
Variable Index: HOST — owned Space; TIMEOUT_SECONDS — 55 of client's approved 60 seconds;
MAX_RESPONSE_BYTES — 2 MiB JSON/SSE boundary; logger — safe phase/type diagnostics.
"""

import base64
import http.client
import json
import logging
import math
import os
import re
import time
from pathlib import Path

from zerogpu.protocol import decode_input

from .detector import CONFIDENCE_THRESHOLD, DetectionError, decode_frame

HOST = "heropig114514-prs-yolo26.hf.space"
TIMEOUT_SECONDS = 55
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
logger = logging.getLogger(__name__)


def chunks(response, socket, deadline, limit):
    """Functionality: Bound response bytes and idle/total read time.
    Inputs: response, retained socket, absolute deadline and byte limit.
    Outputs: byte iterator; Logic: refresh timeout before reads, count every byte.
    Constraints: exceeded limits raise, without retries or response-body logging.
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


def exchange(method, path, body, deadline, stream=False):
    """Functionality: Execute one fixed-host HTTPS request and parse JSON/SSE completion.
    Inputs: method/path/body, shared deadline, stream flag; private HF_INFERENCE_TOKEN env.
    Outputs: parsed JSON; Logic: retain socket, parse fragmented complete/error events.
    Constraints: verified TLS, no redirect/retry; optional token selects explicit anonymous quota.
    """
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("ZeroGPU request deadline")
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("HF_INFERENCE_TOKEN", "")
    if token:
        if not token.startswith("hf_") or len(token) > 4096 or any(c.isspace() for c in token):
            logger.error("Invalid HF_INFERENCE_TOKEN format; check private server configuration")
            raise DetectionError(
                "hf_not_configured", "服务端 HF token 格式无效，请检查私有配置。", 503
            )
        headers["Authorization"] = f"Bearer {token}"
    connection = http.client.HTTPSConnection(HOST, timeout=remaining)
    try:
        connection.request(method, path, body, headers)
        socket = connection.sock  # Connection: close may detach it in getresponse().
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("ZeroGPU headers deadline")
        if socket is not None:
            socket.settimeout(remaining)
        response = connection.getresponse()
        if response.status != 200:
            logger.warning("ZeroGPU HTTP rejected phase=%s status=%d", method, response.status)
            if response.status == 429:
                raise DetectionError("hf_busy", "ZeroGPU 请求受限，请稍后手动开始。", 429)
            raise DetectionError(
                "hf_unavailable", "ZeroGPU 接口未就绪，请检查 Space 状态或服务端 token。", 503
            )
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
                    quota = "quota" in str(json.loads(data[0])).lower()
                    code = "hf_quota_exceeded" if quota else "hf_inference_failed"
                    detail = (
                        "ZeroGPU 免费配额不足，请等待配额恢复。"
                        if quota else "ZeroGPU 推理失败，请检查 Space 日志和 GPU 分配状态。"
                    )
                    logger.warning("ZeroGPU SSE rejected code=%s", code)
                    raise DetectionError(code, detail, 503)
                if event[0] != "heartbeat":
                    raise ValueError("Unexpected ZeroGPU SSE event")
        raise ValueError("ZeroGPU stream ended without completion")
    finally:
        connection.close()


def validate_result(data, image_size):
    """Functionality: Validate returned model identity, frame and boxes before public success.
    Inputs: result dict and input (width,height); Outputs: (JPEG,count,GPU-stage milliseconds).
    Logic: check schema/precision/JPEG/dimensions and finite COCO boxes with pinned label order.
    Constraints: failures propagate; never log base64, media or raw provider errors.
    """
    expected = {
        "schema_version": 1, "model": "yolo26n", "backend": "PyTorch ZeroGPU",
        "precision": "FP32", "classes": 80, "confidence_threshold": CONFIDENCE_THRESHOLD,
        "width": image_size[0], "height": image_size[1],
    }
    if not isinstance(data, dict) or any(data.get(k) != v for k, v in expected.items()):
        raise ValueError("ZeroGPU model contract mismatch")
    count, elapsed, boxes = data["count"], data["inference_ms"], data["boxes"]
    if (
        type(count) is not int or not 0 <= count <= 300
        or type(elapsed) not in (float, int) or not math.isfinite(elapsed) or elapsed < 0
        or not isinstance(boxes, list) or len(boxes) != count
        or type(data["schema_version"]) is not int
        or any(type(data[key]) is not int for key in ("classes", "width", "height"))
    ):
        raise ValueError("ZeroGPU count or timing mismatch")
    try:
        image = decode_input({"schema_version": 1, "jpeg_base64": data["jpeg_base64"]})
    except DetectionError as error:
        raise ValueError("ZeroGPU returned an invalid JPEG") from error
    if image.size != image_size:
        raise ValueError("ZeroGPU image dimensions mismatch")
    names = json.loads(
        (Path(__file__).parents[1] / "models/yolo26n.json").read_text(encoding="utf-8")
    )["names"]
    for box in boxes:
        cls, score, coordinates = box["class_id"], box["score"], box["xyxy"]
        if (
            type(cls) is not int or not 0 <= cls < 80 or box["label"] != names[cls]
            or type(score) not in (float, int) or not math.isfinite(score)
            or not CONFIDENCE_THRESHOLD <= score <= 1
            or not isinstance(coordinates, list) or len(coordinates) != 4
            or any(type(v) not in (float, int) or not math.isfinite(v) for v in coordinates)
        ):
            raise ValueError("ZeroGPU box metadata mismatch")
        x1, y1, x2, y2 = coordinates
        if not (0 <= x1 < x2 <= image.width and 0 <= y1 < y2 <= image.height):
            raise ValueError("ZeroGPU box coordinates mismatch")
    return base64.b64decode(data["jpeg_base64"], validate=True), count, float(elapsed)


def hf_request(payload=None):
    """Functionality: Query startup status or process one JPEG using the explicit Space backend.
    Inputs: optional JPEG bytes; Outputs: readiness dict or validated JPEG/count/timing tuple.
    Logic: POST one event, GET SSE completion within a shared 55-second network deadline.
    Constraints: no fallback; status proves initialization only; anonymous/token quota is explicit.
    """
    image_size = None if payload is None else decode_frame(payload).size
    api = "status" if payload is None else "detect"
    values = [] if payload is None else [{
        "schema_version": 1, "jpeg_base64": base64.b64encode(payload).decode("ascii"),
    }]
    deadline = time.monotonic() + TIMEOUT_SECONDS
    logger.info(
        "ZeroGPU request started phase=%s authentication=%s", api,
        "token" if os.environ.get("HF_INFERENCE_TOKEN") else "anonymous",
    )
    try:
        receipt = exchange(
            "POST", f"/gradio_api/call/{api}", json.dumps({"data": values}).encode(), deadline
        )
        event = receipt["event_id"]
        if not isinstance(event, str) or re.fullmatch(r"[a-f0-9]{32}", event) is None:
            raise ValueError("Invalid ZeroGPU event ID")
        result = exchange("GET", f"/gradio_api/call/{api}/{event}", None, deadline, stream=True)
        if not isinstance(result, list) or len(result) != 1:
            raise ValueError("Invalid ZeroGPU output envelope")
        data = result[0]
        if payload is not None:
            return validate_result(data, image_size)
        manifest = json.loads(
            (Path(__file__).parents[1] / "models/yolo26n.json").read_text(encoding="utf-8")
        )
        if not isinstance(data, dict) or data != {
            "status": "initialized", "schema_version": 1, "model": "yolo26n", "classes": 80,
            "precision": "FP32", "backend": "PyTorch ZeroGPU",
            "confidence_threshold": CONFIDENCE_THRESHOLD, "input_shape": [1, 3, 640, 640],
            "source_sha256": manifest["source_sha256"],
        } or type(data["schema_version"]) is not int:
            raise ValueError("ZeroGPU startup contract mismatch")
        return {
            "ready": True, "model": "yolo26n", "classes": 80,
            "confidence_threshold": CONFIDENCE_THRESHOLD, "precision": "FP32",
            "backend": "PyTorch ZeroGPU",
            "authentication": "token" if os.environ.get("HF_INFERENCE_TOKEN") else "anonymous",
        }
    except DetectionError:
        raise
    except (TimeoutError, OSError, http.client.HTTPException) as error:
        logger.warning("ZeroGPU transport failed phase=%s type=%s", api, type(error).__name__)
        raise DetectionError(
            "hf_connection_failed", "ZeroGPU 连接失败或超过 55 秒，请检查网络与排队状态。", 503
        ) from error
    except (ValueError, TypeError, KeyError, IndexError, OverflowError) as error:
        logger.error("ZeroGPU response invalid phase=%s type=%s", api, type(error).__name__)
        raise DetectionError("hf_invalid_response", "ZeroGPU 返回的数据格式无效。", 502) from error
