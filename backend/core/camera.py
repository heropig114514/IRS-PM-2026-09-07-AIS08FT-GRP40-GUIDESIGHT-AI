"""Responsibilities: 提供摄像头页面和摄像头/地图的白名单前端资源。
Implementation: 普通响应读取小型静态源码；不接收上传，也不根据用户输入拼接任意路径。
Related Modules: config.urls, core.maps, frontend/index.html；
i18n/translations 及摄像头/检测/有界视频流水线/地图/ai-control/震动/语音输出/
场景描述客户端模块；场景接口见 core.scene。
Declaration Index: camera_page — 主页面；camera_asset — 白名单资源；serve_file — 公共响应头。
Variable Index: FRONTEND_DIR — 前端目录；ASSETS — 允许文件名与 MIME 的固定映射。
"""

from django.conf import settings
from django.http import Http404, HttpResponse
from django.views.decorators.http import require_safe

FRONTEND_DIR = settings.BASE_DIR.parent / "frontend"
ASSETS = {
    "i18n.js": "text/javascript",
    "translations.js": "text/javascript",
    "camera.css": "text/css",
    "camera.js": "text/javascript",
    "echo-client.js": "text/javascript",
    "live-detection-client.js": "text/javascript",
    "frame-pipeline.js": "text/javascript",
    "maps-client.js": "text/javascript",
    "maps-page.js": "text/javascript",
    "ai-control.js": "text/javascript",
    "vibration.js": "text/javascript",
    "speech-output.js": "text/javascript",
    "scene-client.js": "text/javascript",
}


def serve_file(name, content_type):
    """Functionality: 打开确定的前端文件并声明资源访问边界。
    Inputs: 内部白名单文件名及 MIME；读取 FRONTEND_DIR 和允许 Origin。
    Outputs: HttpResponse；read_bytes 自动关闭文件，缺失文件抛出原文件异常。
    Logic: 小型固定源码直接返回，避免同步文件迭代器在 ASGI 下隐式适配；不缓存页面。
    Constraints: 此函数只供摄像头页面和经过限定的资源视图使用；不接受任意路径。
    """
    response = HttpResponse((FRONTEND_DIR / name).read_bytes(), content_type=content_type)
    response["Cache-Control"] = "no-store"
    response["Permissions-Policy"] = "camera=(self), microphone=()"
    websocket_origins = " ".join(
        origin.replace("https://", "wss://").replace("http://", "ws://")
        for origin in settings.CAMERA_ALLOWED_ORIGINS
    )
    response["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' blob:; media-src 'self' blob:; "
        f"connect-src 'self' {websocket_origins}; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    )
    return response


@require_safe
def camera_page(request):
    """Functionality: 显示手机摄像头原样/YOLO 标注回传页面。
    Inputs: 只读 Django request；页面不依赖用户或数据库。
    Outputs: HTML HttpResponse；非 GET/HEAD 返回 405。
    Logic: 文件名固定，不执行客户端模板插值。
    Constraints: 摄像头权限由浏览器在用户点击后申请。
    """
    return serve_file("index.html", "text/html")


@require_safe
def camera_asset(request, name):
    """Functionality: 提供固定白名单中的 JavaScript/CSS。
    Inputs: Django request 和路由的单段 name。
    Outputs: 固定 MIME HttpResponse；未知文件抛 Http404。
    Logic: 先查固定映射，验证通过后才读取文件。
    Constraints: 不暴露 README、配置、密钥或任意目录。
    """
    if name not in ASSETS:
        raise Http404("Unknown frontend asset")
    return serve_file(name, ASSETS[name])
