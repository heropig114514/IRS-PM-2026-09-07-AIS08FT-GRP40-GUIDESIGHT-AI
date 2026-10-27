"""Responsibilities: Adapt same-origin scene-description POST requests and expose configuration
status.
Implementation: Check Origin and Host (as in core.maps), enforce the body limit before reading it,
and delegate business logic to core.vlm.
Related Modules: core.vlm, core.maps, config.urls, and the frontend scene-description client.
Declaration Index: scene_config — configuration status; scene_describe — scene-description POST
endpoint.
Variable Index: logger — safe diagnostic logging; MAX_REQUEST_BYTES — 1.5 MiB request-body limit.
"""

import asyncio
import json
import logging
import os
import uuid
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_safe

from .vlm import (
    DEFAULT_MODEL,
    SceneError,
    describe_scene,
    validate_scene_request,
    vlm_settings,
)

logger = logging.getLogger(__name__)
MAX_REQUEST_BYTES = 1572864


@require_safe
def scene_config(request):
    """Functionality: Report whether the VLM is configured without exposing credentials.
    Inputs: request and DASHSCOPE_* environment; Outputs: uncached 200 or unconfigured 503 JSON.
    Logic: Expose only provider/model/configured/code; keep host private. Configuration does not
    prove authentication.
    Constraints: Authentication is verified by a description request; when unconfigured, model is
    the environment value or default.
    """
    try:
        _, _, model = vlm_settings()
        configured = True
    except SceneError:
        model = os.environ.get("VLM_MODEL", "").strip() or DEFAULT_MODEL
        configured = False
    response = JsonResponse(
        {
            "provider": "dashscope",
            "model": model,
            "configured": configured,
            "code": "ready" if configured else "vlm_not_configured",
        },
        status=200 if configured else 503,
    )
    response["Cache-Control"] = "no-store"
    return response


@csrf_exempt
@require_POST
async def scene_describe(request):
    """Functionality: Validate a same-origin request and return a conversational description of the
    scene ahead.
    Inputs: request Origin/Host、JSON image_jpeg_base64/language/question/objects；
    Outputs: Contract-compliant success JSON or {code, detail, request_id}.
    Logic: Strict Origin/Host checks replace cookie-based CSRF; check Content-Length before reading
    the body, then pass bounded input to core.vlm.
    Constraints: Same-origin is not authentication; images/questions are neither stored nor logged;
    no automatic retry or fallback. The async view runs blocking
    http.client work in a thread so it does not occupy Django's sync thread pool and stall other
    pages (up to about 20 seconds per request).
    """
    request_id = uuid.uuid4().hex[:12]
    try:
        origin = request.headers.get("Origin", "")
        if (
            origin not in settings.CAMERA_ALLOWED_ORIGINS
            or urlsplit(origin).netloc != request.get_host()
        ):
            raise SceneError(
                "origin_rejected",
                "The scene endpoint accepts same-origin requests from this page only.",
                403,
            )
        if request.content_type != "application/json":
            raise SceneError("invalid_content_type", "The request must use application/json.", 415)
        too_large = SceneError(
            "request_too_large", "The scene request exceeds the 1.5 MiB limit.", 413
        )
        declared = request.META.get("CONTENT_LENGTH", "")
        if declared.isdigit() and int(declared) > MAX_REQUEST_BYTES:
            raise too_large
        try:
            raw = request.body
        except RequestDataTooBig as error:
            raise too_large from error
        if len(raw) > MAX_REQUEST_BYTES:
            raise too_large
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeDecodeError, RecursionError) as error:
            raise SceneError(
                "invalid_scene_input", "The request body is not valid JSON.", 400
            ) from error
        scene = validate_scene_request(body)
        result = await asyncio.to_thread(describe_scene, scene, request_id)
        response = JsonResponse({**result, "request_id": request_id})
    except SceneError as error:
        logger.warning(
            "Scene request failed id=%s code=%s status=%s", request_id, error.code, error.status
        )
        response = JsonResponse(
            {"code": error.code, "detail": error.detail, "request_id": request_id},
            status=error.status,
        )
    response["Cache-Control"] = "no-store"
    return response
