"""Responsibilities: Private loopback laptop readiness and single-frame GPU endpoints.
Implementation: Constant-time bearer check before image/model access; one non-queued GPU slot.
Related Modules: config.laptop_worker/worker_urls; core.gpu_detector; core.laptop_services.
Declaration Index: authenticated; worker_config; worker_frame.
Variable Index: slot — serial reusable GPU buffers; logger — request/count/time, never images/token.
"""

import logging
import os
import secrets
import uuid
from threading import BoundedSemaphore

from django.core.exceptions import RequestDataTooBig
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_safe

from .detector import CONFIDENCE_THRESHOLD, DetectionError, decode_frame
from .gpu_detector import get_gpu_detector
from .vision import error_response

slot = BoundedSemaphore(1)
logger = logging.getLogger(__name__)


def authenticated(request):
    """Functionality: Reject unauthenticated local/tunnel calls. Inputs: request and worker token.
    Outputs: bool; Logic: constant-time full bearer comparison, token >=32 chars.
    Constraints: loopback alone is insufficient authorization; never log supplied credentials.
    """
    token = os.environ.get("LAPTOP_INFERENCE_TOKEN", "")
    return len(token) >= 32 and secrets.compare_digest(
        request.headers.get("Authorization", "").encode(), ("Bearer " + token).encode()
    )


@require_safe
def worker_config(request):
    """Functionality: Report verified GPU readiness. Inputs: authenticated request and model state.
    Outputs: fixed JSON or safe 401/429/503; Logic: load using same serial slot as frames.
    Constraints: does not run a synthetic frame or expose model paths, token or hardware details.
    """
    request_id = uuid.uuid4().hex[:12]
    if not authenticated(request):
        return error_response(DetectionError("unauthorized", "未授权。", 401), request_id)
    if not slot.acquire(blocking=False):
        return error_response(DetectionError("laptop_busy", "Laptop 忙。", 429), request_id)
    try:
        get_gpu_detector()
        response = JsonResponse(
            {
                "ready": True,
                "model": "yolo26n",
                "classes": 80,
                "confidence_threshold": CONFIDENCE_THRESHOLD,
                "backend": "TensorRT",
                "precision": "FP32",
            }
        )
        response["Cache-Control"] = "no-store"
        return response
    except DetectionError as error:
        return error_response(error, request_id)
    finally:
        slot.release()


@csrf_exempt
@require_POST
def worker_frame(request):
    """Functionality: Annotate server-supplied frame on GPU. Inputs: bearer and image/jpeg body.
    Outputs: JPEG and metadata or safe error; Logic: authenticate, validate, serialize inference.
    Constraints: no storage/retry/CPU fallback; missing/failed laptop stops explicit laptop mode.
    """
    request_id = uuid.uuid4().hex[:12]
    acquired = False
    try:
        if not authenticated(request):
            raise DetectionError("unauthorized", "未授权。", 401)
        if request.content_type != "image/jpeg":
            raise DetectionError("invalid_content_type", "必须使用 JPEG。", 415)
        acquired = slot.acquire(blocking=False)
        if not acquired:
            raise DetectionError("laptop_busy", "Laptop 忙。", 429)
        image = decode_frame(request.body)
        jpeg, boxes, inference_ms = get_gpu_detector().predict(image)
        response = HttpResponse(jpeg, content_type="image/jpeg")
        response["Cache-Control"] = "no-store"
        response["X-Model"] = "yolo26n"
        response["X-Detection-Count"] = str(len(boxes))
        response["X-Inference-Ms"] = f"{inference_ms:.1f}"
        response["X-Inference-Backend"] = "TensorRT"
        response["X-Inference-Precision"] = "FP32"
        logger.info(
            "Laptop frame completed id=%s count=%d inference_ms=%.1f",
            request_id,
            len(boxes),
            inference_ms,
        )
        return response
    except DetectionError as error:
        return error_response(error, request_id)
    except RequestDataTooBig:
        return error_response(DetectionError("frame_size", "图像超过 1 MiB。", 413), request_id)
    except Exception as error:
        logger.error(
            "Laptop inference failed id=%s exception_type=%s", request_id, type(error).__name__
        )
        return error_response(DetectionError("laptop_failed", "Laptop 推理失败。", 500), request_id)
    finally:
        if acquired:
            slot.release()
