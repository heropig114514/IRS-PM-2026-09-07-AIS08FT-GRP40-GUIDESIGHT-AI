"""Responsibilities: Bound server-to-laptop inference calls through the SSH loopback tunnel.
Implementation: Configurable loopback port, bearer auth, bounded HTTP, no redirect/retry/fallback.
Related Modules: core.laptop_vision; core.laptop_worker returns the same JPEG metadata contract.
Declaration Index: laptop_request — authenticated transport and response validation.
Variable Index: TIMEOUT_SECONDS — established 8-second I/O; logger — safe diagnostics.
"""

import http.client
import json
import logging
import math
import os
import time

from .detector import CONFIDENCE_THRESHOLD, MAX_FRAME_BYTES, DetectionError, decode_frame

TIMEOUT_SECONDS = 8
logger = logging.getLogger(__name__)


def laptop_request(payload=None):
    """Functionality: Request readiness or annotated JPEG. Inputs: optional bounded JPEG bytes.
    Outputs: readiness dict or (JPEG,count,time); Logic: configured loopback HTTP across SSH.
    Constraints: token only in header; 8-second socket plus total read deadline; no raw body logs.
    """
    token = os.environ.get("LAPTOP_INFERENCE_TOKEN", "")
    if len(token) < 32:
        raise DetectionError("laptop_not_configured", "服务端尚未配置 laptop 推理连接。", 503)
    try:
        port = int(os.environ.get("LAPTOP_INFERENCE_PORT", "18767"))
        if not 1024 <= port <= 65535:
            raise ValueError("Invalid port")
    except ValueError as error:
        logger.error("Invalid LAPTOP_INFERENCE_PORT; expected 1024..65535")
        raise DetectionError(
            "laptop_not_configured", "服务端 laptop 推理端口配置无效。", 503
        ) from error
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=TIMEOUT_SECONDS)
    started = time.monotonic()
    try:
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "image/jpeg"}
        connection.request(
            "GET" if payload is None else "POST",
            "/config/" if payload is None else "/frame/",
            payload,
            headers,
        )
        # getresponse() detaches the socket on Connection: close; retain its deadline control.
        upstream_socket = connection.sock
        response = connection.getresponse()
        body = bytearray()
        limit = 4096 if payload is None or response.status != 200 else MAX_FRAME_BYTES
        while len(body) <= limit:
            remaining = TIMEOUT_SECONDS - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError("Laptop response deadline")
            if upstream_socket is not None:
                upstream_socket.settimeout(remaining)
            chunk = response.read1(min(65536, limit + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
        if len(body) > limit:
            raise ValueError("Laptop response exceeds boundary")
        if response.status != 200:
            logger.warning("Laptop upstream rejected status=%d", response.status)
            if response.status == 429:
                raise DetectionError("laptop_busy", "Laptop 正在处理图像，请稍后手动开始。", 429)
            raise DetectionError("laptop_unavailable", "Laptop 推理未就绪，请检查模型和隧道。", 503)
        if payload is None:
            data = json.loads(body)
            if data != {
                "ready": True,
                "model": "yolo26n",
                "classes": 80,
                "confidence_threshold": CONFIDENCE_THRESHOLD,
                "backend": "TensorRT",
                "precision": "FP32",
            }:
                raise ValueError("Laptop readiness contract mismatch")
            return data
        count = int(response.getheader("X-Detection-Count", ""))
        inference_ms = float(response.getheader("X-Inference-Ms", ""))
        try:
            result_size = decode_frame(bytes(body)).size
        except DetectionError as error:
            raise ValueError("Laptop returned invalid JPEG") from error
        if (
            response.getheader("Content-Type", "").split(";")[0] != "image/jpeg"
            or response.getheader("X-Model") != "yolo26n"
            or response.getheader("X-Inference-Backend") != "TensorRT"
            or response.getheader("X-Inference-Precision") != "FP32"
            or not 0 <= count <= 300
            or not math.isfinite(inference_ms)
            or inference_ms < 0
            or result_size != decode_frame(payload).size
        ):
            raise ValueError("Laptop image contract mismatch")
        return bytes(body), count, inference_ms
    except DetectionError:
        raise
    except (TimeoutError, OSError, http.client.HTTPException) as error:
        logger.warning("Laptop transport failed exception_type=%s", type(error).__name__)
        raise DetectionError(
            "laptop_disconnected", "Laptop 连接已中断，请检查电源和 SSH 隧道。", 503
        ) from error
    except (ValueError, TypeError, KeyError) as error:
        logger.error("Laptop response invalid exception_type=%s", type(error).__name__)
        raise DetectionError(
            "laptop_invalid_response", "Laptop 返回的数据格式无效。", 502
        ) from error
    finally:
        connection.close()
