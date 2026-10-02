"""Responsibilities: Same-origin ZeroGPU status and annotated-JPEG HTTP adapters.
Implementation: Dedicated non-queued slot, explicit HF call, safe errors and fixed response headers.
Related Modules: core.hf_services/detector/vision; config.urls; frontend.camera/detection-client.
Declaration Index: hf_config — startup status; hf_frame — one annotated frame.
Variable Index: slot — one provider call; logger — IDs/count/timing, never media or credentials.
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
from .hf_services import hf_request
from .vision import error_response

slot = BoundedSemaphore(1)
logger = logging.getLogger(__name__)


@require_safe
def hf_config(request):
    """Functionality: Query initialized Space metadata without invoking the GPU.
    Inputs: request and fixed provider configuration; Outputs: no-store JSON or safe provider error.
    Logic: acquire one non-queued slot, call once, release in finally.
    Constraints: readiness does not prove queue availability;
    unexpected errors retain Django semantics.
    """
    request_id = uuid.uuid4().hex[:12]
    if not slot.acquire(blocking=False):
        return error_response(DetectionError("hf_busy", "ZeroGPU 忙。", 429), request_id)
    try:
        response = JsonResponse(hf_request())
        response["Cache-Control"] = "no-store"
        return response
    except DetectionError as error:
        return error_response(error, request_id)
    finally:
        slot.release()


@csrf_exempt
@require_POST
def hf_frame(request):
    """Functionality: Return one ZeroGPU-annotated frame under the camera JPEG contract.
    Inputs: same-origin JPEG request;
    Outputs: JPEG/count/timing/FP32 headers or safe diagnosed error.
    Logic: validate origin/media/JPEG, acquire one slot, call HF once, finally release the slot.
    Constraints: approved 55-second upstream/60-second client;
    no media files, retries or backend switch.
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
            raise DetectionError("hf_busy", "ZeroGPU 忙。", 429)
        decode_frame(request.body)
        jpeg, count, inference_ms = hf_request(request.body)
        response = HttpResponse(jpeg, content_type="image/jpeg")
        response["Cache-Control"] = "no-store"
        response["X-Detection-Count"] = str(count)
        response["X-Inference-Ms"] = f"{inference_ms:.1f}"
        response["X-Model"] = "yolo26n"
        response["X-Request-ID"] = request_id
        response["X-Inference-Backend"] = "PyTorch ZeroGPU"
        response["X-Inference-Precision"] = "FP32"
        logger.info(
            "ZeroGPU relay completed id=%s count=%d total_ms=%.1f",
            request_id, count, (time.monotonic() - started) * 1000,
        )
        return response
    except DetectionError as error:
        return error_response(error, request_id)
    except Exception as error:
        logger.error(
            "ZeroGPU relay failed id=%s exception_type=%s", request_id, type(error).__name__
        )
        return error_response(
            DetectionError("hf_relay_failed", "ZeroGPU 转发失败。", 500), request_id
        )
    finally:
        if acquired:
            slot.release()
