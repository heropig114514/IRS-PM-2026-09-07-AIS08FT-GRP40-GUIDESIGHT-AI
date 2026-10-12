"""Responsibilities: 场景描述配置状态与同源 POST 的 HTTP 适配。
Implementation: Origin/Host 双重检查（同 core.maps）；先查长度再读正文；业务全部委托 core.vlm；
scene_describe 为 async 视图，阻塞上游调用经 asyncio.to_thread 执行。
Related Modules: core.vlm、core.maps、config.urls；frontend 场景描述客户端。
Declaration Index: scene_config — 配置状态；scene_describe — 场景描述 POST。
Variable Index: logger — 安全诊断日志；MAX_REQUEST_BYTES — 1.5 MiB 请求体上限。
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
    """Functionality: 告知 VLM 是否配置，绝不提供 key。
    Inputs: request、DASHSCOPE_* 环境；Outputs: 200 或未配置的 503 JSON，无缓存。
    Logic: 仅公开 provider/model/configured/code；host 不公开；配置存在不证明认证通过。
    Constraints: 实际认证由描述调用验证；未配置时 model 为环境值或默认值。
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
    """Functionality: 校验同源请求并返回前方场景的口语化描述。
    Inputs: request Origin/Host、JSON image_jpeg_base64/language/question/objects；
    Outputs: 契约成功 JSON 或 {code, detail, request_id}。
    Logic: 严格 Origin/Host 代替 cookie CSRF；Content-Length 先于读取正文；限尺寸后交给 core.vlm。
    Constraints: 同源不等于身份认证；图像/问题不存储不记录；无自动重试或降级。异步视图把阻塞的
    http.client 调用放入线程，避免占用 Django 的 sync 线程而拖住其它同步页面（最长约 20 秒/次）。
    """
    request_id = uuid.uuid4().hex[:12]
    try:
        origin = request.headers.get("Origin", "")
        if (
            origin not in settings.CAMERA_ALLOWED_ORIGINS
            or urlsplit(origin).netloc != request.get_host()
        ):
            raise SceneError("origin_rejected", "场景接口仅接受本页面的同源请求。", 403)
        if request.content_type != "application/json":
            raise SceneError("invalid_content_type", "请求必须使用 application/json。", 415)
        too_large = SceneError("request_too_large", "场景请求超过 1.5 MiB 上限。", 413)
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
            raise SceneError("invalid_scene_input", "请求正文不是有效的 JSON。", 400) from error
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
