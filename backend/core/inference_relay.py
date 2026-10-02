"""Responsibilities: Shared same-origin status/JPEG guards for explicit remote GPU backends.
Implementation: One supplied relay slot, validated input and fixed backend response headers.
Related Modules: core.laptop_vision/hf_vision; core.vision supplies safe error responses.
Declaration Index: relay_config — status adapter; relay_frame — image adapter.
Variable Index: logger — request IDs, selected backend, counts and timing; no media or credentials.
"""

import logging
import time
import uuid
from urllib.parse import urlsplit

from django.conf import settings
from django.http import HttpResponse, JsonResponse

from .detector import DetectionError, decode_frame
from .vision import error_response

logger = logging.getLogger(__name__)


def relay_config(request, upstream, slot, prefix, name):
    """Functionality: Read selected provider status with the original non-queued guard.
    Inputs: request, provider callable, semaphore, error prefix and display name.
    Outputs: no-store JSON or safe error; Logic: slot acquire, one call, finally release.
    Constraints: unexpected exceptions retain Django semantics; no substitution or retries.
    """
    request_id = uuid.uuid4().hex[:12]
    if not slot.acquire(blocking=False):
        return error_response(DetectionError(f"{prefix}_busy", f"{name} 忙。", 429), request_id)
    try:
        response = JsonResponse(upstream())
        response["Cache-Control"] = "no-store"
        return response
    except DetectionError as error:
        return error_response(error, request_id)
    finally:
        slot.release()


def relay_frame(request, upstream, slot, prefix, name, backend):
    """Functionality: Return one remote-annotated JPEG under the common camera contract.
    Inputs: request/JPEG, fixed provider callable/semaphore/error prefix/name/backend.
    Outputs: JPEG with count/timing/model/FP32 headers, or safe diagnosed failure.
    Logic: same-origin/content-type/JPEG guards, non-queued slot, provider call, finally release.
    Constraints: provider defines deadline; no disk media, retry, alternate mode or raw-body log.
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
            raise DetectionError(f"{prefix}_busy", f"{name} 忙。", 429)
        decode_frame(request.body)
        jpeg, count, inference_ms = upstream(request.body)
        response = HttpResponse(jpeg, content_type="image/jpeg")
        response["Cache-Control"] = "no-store"
        response["X-Detection-Count"] = str(count)
        response["X-Inference-Ms"] = f"{inference_ms:.1f}"
        response["X-Model"] = "yolo26n"
        response["X-Request-ID"] = request_id
        response["X-Inference-Backend"] = backend
        response["X-Inference-Precision"] = "FP32"
        logger.info(
            "GPU relay completed id=%s backend=%s count=%d total_ms=%.1f",
            request_id, backend, count, (time.monotonic() - started) * 1000,
        )
        return response
    except DetectionError as error:
        return error_response(error, request_id)
    except Exception as error:
        logger.error(
            "GPU relay failed id=%s backend=%s exception_type=%s",
            request_id, backend, type(error).__name__,
        )
        return error_response(
            DetectionError(f"{prefix}_relay_failed", f"{name} 转发失败。", 500), request_id
        )
    finally:
        if acquired:
            slot.release()
