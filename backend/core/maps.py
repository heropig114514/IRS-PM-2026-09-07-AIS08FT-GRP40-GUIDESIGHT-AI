"""Responsibilities: OSM 页面、非敏感配置和同源步行路线 HTTP 适配。
Implementation: 地图资源和路线服务分离；Origin/Host 双重检查；凭据仅由 services 使用。
Related Modules: core.services, core.camera, config.urls；frontend 地图页面与模块。
Declaration Index: maps_page — 页面；maps_config — 配置状态；maps_route — 路线 POST。
Variable Index: logger — 安全诊断日志；MAX_REQUEST_BYTES — 小型坐标请求上限。
"""

import json
import logging
import os
import secrets
import uuid
from urllib.parse import urlsplit

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_safe

from .camera import FRONTEND_DIR
from .services import RouteError, validate_coordinate, walking_route

logger = logging.getLogger(__name__)
MAX_REQUEST_BYTES = 2048


@require_safe
def maps_page(request):
    """Functionality: 显示 Leaflet/OSM 步行路线测试页。
    Inputs: request、固定 HTML；Outputs: 独立 nonce CSP、定位权限、无缓存 HTML。
    Logic: 本地模块配合固定版本/SRI 的 Leaflet；OSM 瓦片直连并保留来源与正常缓存。
    Constraints: 仅地图页面允许 unpkg/OSM；不改变摄像头页面策略。
    """
    nonce = secrets.token_urlsafe(24)
    html = (FRONTEND_DIR / "maps.html").read_text(encoding="utf-8")
    response = HttpResponse(html.replace("__CSP_NONCE__", nonce), content_type="text/html")
    response["Cache-Control"] = "no-store"
    response["Permissions-Policy"] = "geolocation=(self), camera=(), microphone=()"
    response["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' https://unpkg.com; "
        f"style-src 'self' 'nonce-{nonce}' https://unpkg.com; "
        "img-src 'self' https://tile.openstreetmap.org data:; connect-src 'self'; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    )
    return response


@require_safe
def maps_config(request):
    """Functionality: 告知 ORS 是否配置，绝不提供 key。
    Inputs: request、ORS_API_KEY 环境；Outputs: 200 或缺失配置的 503 JSON，无缓存。
    Logic: 仅公开 provider/profile/configured；不以配置存在证明真实认证通过。
    Constraints: 实际认证由路线调用验证。
    """
    configured = bool(os.environ.get("ORS_API_KEY", "").strip())
    response = JsonResponse(
        {
            "provider": "openrouteservice",
            "profile": "foot-walking",
            "configured": configured,
            "code": "ready" if configured else "ors_not_configured",
        },
        status=200 if configured else 503,
    )
    response["Cache-Control"] = "no-store"
    return response


@csrf_exempt
@require_POST
def maps_route(request):
    """Functionality: 将明确提交的起终点发送 ORS 并返回规范路线。
    Inputs: request Origin/Host、JSON start/end 两个 [经度,纬度]；Outputs: 路线或安全错误 JSON。
    Logic: 代理不传 Cookie，采用严格 Origin/Host 检查代替 cookie CSRF；限尺寸并验证输入。
    Constraints: 同源不等于身份认证；坐标只处理不存储；不记录正文/密钥；无自动重试。
    """
    request_id = uuid.uuid4().hex[:12]
    try:
        origin = request.headers.get("Origin", "")
        if (
            origin not in settings.CAMERA_ALLOWED_ORIGINS
            or urlsplit(origin).netloc != request.get_host()
        ):
            raise RouteError("origin_rejected", "路线接口仅接受本页面的同源请求。", 403)
        if request.content_type != "application/json":
            raise RouteError("invalid_content_type", "请求必须使用 application/json。", 415)
        if len(request.body) > MAX_REQUEST_BYTES:
            raise RouteError("request_too_large", "路线请求超过 2 KiB 上限。", 413)
        try:
            body = json.loads(request.body)
            if not isinstance(body, dict) or set(body) != {"start", "end"}:
                raise ValueError("仅接受 start 和 end 两个坐标字段。")
            start, end = validate_coordinate(body["start"]), validate_coordinate(body["end"])
            if start == end:
                raise ValueError("起点和终点不能相同。")
        except (ValueError, UnicodeDecodeError, OverflowError) as error:
            raise RouteError(
                "invalid_route_input", "起终点必须为不同的有效 [经度,纬度] 坐标。", 400
            ) from error
        route = walking_route(start, end, request_id)
        response = JsonResponse({**route, "request_id": request_id})
    except RouteError as error:
        logger.warning(
            "Route request failed id=%s code=%s status=%s", request_id, error.code, error.status
        )
        response = JsonResponse(
            {"code": error.code, "detail": error.detail, "request_id": request_id},
            status=error.status,
        )
    response["Cache-Control"] = "no-store"
    return response
