"""Responsibilities: Public same-origin adapter for explicitly selected laptop GPU mode.
Implementation: Preserve origin/JPEG/frame limits, proxy fixed private upstream, one active call.
Related Modules: core.laptop_services; config.urls; frontend.camera; CPU core.vision unchanged.
Declaration Index: laptop_config; laptop_frame.
Variable Index: slot — one relay call; logger — safe request IDs/count/timing.
"""

import logging
import time
import uuid
from threading import BoundedSemaphore
from urllib.parse import urlsplit

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_safe

from .detector import DetectionError, decode_frame
from .laptop_services import laptop_request
from .vision import error_response

slot = BoundedSemaphore(1)
logger = logging.getLogger(__name__)


@require_safe
def laptop_config(request):
    """Functionality: Check actual laptop readiness. Inputs: request and fixed tunnel settings.
    Outputs: ready TensorRT JSON or explicit failure; Logic: acquire relay slot, authenticated GET.
    Constraints: no host/URL accepted from browser and no CPU substitution when disconnected.
    """
    request_id = uuid.uuid4().hex[:12]
    if not slot.acquire(blocking=False):
        return error_response(DetectionError("laptop_busy", "Laptop 忙。", 429), request_id)
    try:
        response = JsonResponse(laptop_request())
        response["Cache-Control"] = "no-store"
        return response
    except DetectionError as error:
        return error_response(error, request_id)
    finally:
        slot.release()


@csrf_exempt
@require_POST
def laptop_frame(request):
    """Functionality: Return laptop-annotated JPEG. Inputs: same-origin request and JPEG body.
    Outputs: JPEG and count/GPU time/backend headers; Logic: same boundaries, serialized relay.
    Constraints: fixed 8-second upstream budget below client's 10 seconds, no retries or saving.
    """
    request_id, started = uuid.uuid4().hex[:12], time.monotonic()
    acquired = False
    try:
        origin = request.headers.get("Origin", "")
        if (
            origin not in settings.CAMERA_ALLOWED_ORIGINS
            or urlsplit(origin).netloc != request.get_host()
        ):
            raise DetectionError("origin_rejected", "检测接口仅接受本页面的同源请求。", 403)
        if request.content_type != "image/jpeg":
            raise DetectionError("invalid_content_type", "必须使用 JPEG。", 415)
        acquired = slot.acquire(blocking=False)
        if not acquired:
            raise DetectionError("laptop_busy", "Laptop 忙。", 429)
        decode_frame(request.body)
        jpeg, count, inference_ms = laptop_request(request.body)
        response = HttpResponse(jpeg, content_type="image/jpeg")
        response["Cache-Control"] = "no-store"
        response["X-Detection-Count"] = str(count)
        response["X-Inference-Ms"] = f"{inference_ms:.1f}"
        response["X-Model"] = "yolo26n"
        response["X-Request-ID"] = request_id
        response["X-Inference-Backend"] = "TensorRT"
        response["X-Inference-Precision"] = "FP32"
        logger.info(
            "Laptop relay completed id=%s count=%d total_ms=%.1f",
            request_id,
            count,
            (time.monotonic() - started) * 1000,
        )
        return response
    except DetectionError as error:
        return error_response(error, request_id)
    except Exception as error:
        logger.error(
            "Laptop relay failed id=%s exception_type=%s", request_id, type(error).__name__
        )
        return error_response(
            DetectionError("laptop_relay_failed", "Laptop 转发失败。", 500), request_id
        )
    finally:
        if acquired:
            slot.release()
