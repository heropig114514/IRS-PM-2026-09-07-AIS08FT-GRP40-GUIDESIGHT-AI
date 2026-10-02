"""Responsibilities: Same-origin YOLO configuration and annotated camera-frame HTTP endpoints.
Implementation: Single non-queued inference slot; JPEG request/result in memory; fixed safe errors.
Related Modules: core.detector, config.urls, frontend/detection-client.js; separate echo baseline.
Declaration Index: error_response — safe JSON; vision_config — ready check;
vision_frame — frame POST.
Variable Index: frame_slot — one active model operation; logger — IDs, counts and timing.
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

from .detector import CONFIDENCE_THRESHOLD, DetectionError, decode_frame, get_detector

frame_slot = BoundedSemaphore(1)
logger = logging.getLogger(__name__)


def error_response(error, request_id):
    """Functionality: Return safe diagnostics; Inputs: DetectionError and non-sensitive request_id.
    Outputs: no-store JSON and warning; Logic: fixed detail/code; Constraints: no media logging.
    """
    logger.warning(
        "Vision request failed id=%s code=%s status=%s", request_id, error.code, error.status
    )
    response = JsonResponse(
        {"code": error.code, "detail": error.detail, "request_id": request_id}, status=error.status
    )
    response["Cache-Control"] = "no-store"
    return response


@require_safe
def vision_config(request):
    """Functionality: Verify the pinned model can load before camera frames are submitted.
    Inputs: request, model environment; Outputs: ready/model/classes/threshold JSON or error.
    Logic: share the slot with frames, initialize once;
    Constraints: no image or model binary exposed.
    """
    request_id = uuid.uuid4().hex[:12]
    if not frame_slot.acquire(blocking=False):
        return error_response(
            DetectionError("vision_busy", "检测服务正在处理图像，请稍后手动开始。", 429), request_id
        )
    try:
        model = get_detector()
        response = JsonResponse(
            {
                "ready": True,
                "model": "yolo26n",
                "classes": len(model.names),
                "confidence_threshold": CONFIDENCE_THRESHOLD,
            }
        )
        response["Cache-Control"] = "no-store"
        return response
    except DetectionError as error:
        return error_response(error, request_id)
    finally:
        frame_slot.release()


@csrf_exempt
@require_POST
def vision_frame(request):
    """Functionality: Return camera JPEG with YOLO boxes/labels burnt into its pixels.
    Inputs: same-origin Origin/Host and image/jpeg body;
    Outputs: JPEG with count/timing/model/ID headers.
    Logic: explicit Origin replaces cookie CSRF; serialize decode/inference/render.
    Constraints: no queue/retry/storage; stop discards late frames, active CPU work finishes.
    """
    request_id = uuid.uuid4().hex[:12]
    started = time.monotonic()
    acquired = False
    try:
        origin = request.headers.get("Origin", "")
        if (
            origin not in settings.CAMERA_ALLOWED_ORIGINS
            or urlsplit(origin).netloc != request.get_host()
        ):
            raise DetectionError("origin_rejected", "检测接口仅接受本页面的同源请求。", 403)
        if request.content_type != "image/jpeg":
            raise DetectionError("invalid_content_type", "检测请求必须使用 image/jpeg。", 415)
        acquired = frame_slot.acquire(blocking=False)
        if not acquired:
            raise DetectionError("vision_busy", "检测服务正在处理图像，请稍后手动开始。", 429)
        image = decode_frame(request.body)
        model = get_detector()
        jpeg, boxes, inference_ms = model.predict(image)
        response = HttpResponse(jpeg, content_type="image/jpeg")
        response["Cache-Control"] = "no-store"
        response["X-Detection-Count"] = str(len(boxes))
        response["X-Inference-Ms"] = f"{inference_ms:.1f}"
        response["X-Model"] = "yolo26n"
        response["X-Request-ID"] = request_id
        logger.info(
            "Vision frame completed id=%s count=%d inference_ms=%.1f total_ms=%.1f bytes=%d",
            request_id,
            len(boxes),
            inference_ms,
            (time.monotonic() - started) * 1000,
            len(jpeg),
        )
        return response
    except DetectionError as error:
        return error_response(error, request_id)
    except Exception as error:
        logger.error(
            "Vision processing failed id=%s exception_type=%s; check decoder/session/render",
            request_id,
            type(error).__name__,
        )
        return error_response(
            DetectionError("vision_failed", "图像检测失败，请检查服务端诊断日志。", 500), request_id
        )
    finally:
        if acquired:
            frame_slot.release()
