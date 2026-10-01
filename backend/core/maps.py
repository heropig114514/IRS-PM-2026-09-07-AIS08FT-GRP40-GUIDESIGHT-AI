"""Responsibilities: 独立地图页面、浏览器 key 配置及 Google 专用 CSP。
Implementation: 每次页面响应生成 nonce；配置只读取本应用环境，不接收或记录坐标。
Related Modules: config.urls, frontend/maps.html/maps-page.js/maps-client.js。
Declaration Index: maps_page — 地图页面；maps_config — 可公开的浏览器 key。
Variable Index: logger — 无敏感内容的配置状态日志。
"""

import logging
import os
import secrets

from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_safe

from .camera import FRONTEND_DIR

logger = logging.getLogger(__name__)


@require_safe
def maps_page(request):
    """Functionality: 提供允许 Google 地图和设备定位的独立页面。
    Inputs: 只读 request、固定 HTML 文件；不读用户数据或数据库。
    Outputs: 不缓存的 HTML，独立 CSP、定位权限和来源头；文件缺失异常向上传递。
    Logic: nonce 仅替换源码中的固定占位符；Google SDK 继承该 nonce 加载资源。
    Constraints: HTTPS/localhost 才能定位；摄像头页面的 CSP 不变。
    """
    nonce = secrets.token_urlsafe(24)
    html = (FRONTEND_DIR / "maps.html").read_text(encoding="utf-8")
    response = HttpResponse(html.replace("__CSP_NONCE__", nonce), content_type="text/html")
    response["Cache-Control"] = "no-store"
    response["Permissions-Policy"] = "geolocation=(self), camera=(), microphone=()"
    response["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response["Content-Security-Policy"] = (
        "default-src 'self'; "
        f"script-src 'self' 'nonce-{nonce}' 'strict-dynamic' https: 'unsafe-eval' blob:; "
        f"style-src 'self' 'nonce-{nonce}' https://fonts.googleapis.com; "
        "img-src 'self' https://*.googleapis.com https://*.gstatic.com "
        "https://*.google.com https://*.googleusercontent.com data:; "
        "connect-src 'self' https://*.googleapis.com https://*.google.com "
        "https://*.gstatic.com data: blob:; font-src https://fonts.gstatic.com; "
        "frame-src https://*.google.com; worker-src blob:; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    )
    return response


@require_safe
def maps_config(request):
    """Functionality: 返回网站限制型 Maps JavaScript key 或明确的缺失配置错误。
    Inputs: request 与 GOOGLE_MAPS_API_KEY 环境变量，已有环境优先于 .env。
    Outputs: JSON 200/503；配置状态日志；不缓存、不返回任何服务器凭据。
    Logic: 去除空白后检查 key；浏览器 SDK 必须使用可公开且受网站限制的 key。
    Constraints: 此 key 在浏览器可见；不得在此使用服务账号或服务器专用凭据。
    """
    key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    if not key:
        logger.warning("Maps configuration unavailable: GOOGLE_MAPS_API_KEY is absent")
        response = JsonResponse({"code": "maps_not_configured"}, status=503)
    else:
        logger.info("Maps browser configuration requested")
        response = JsonResponse({"apiKey": key})
    response["Cache-Control"] = "no-store"
    return response
