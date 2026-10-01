"""Responsibilities: 提供摄像头页面和明确白名单中的前端资源。
Implementation: FileResponse 流式读源码文件；不接收上传，也不根据用户输入拼接任意路径。
Related Modules: config.urls, frontend/index.html/camera.css/camera.js/echo-client.js。
Declaration Index: camera_page — 主页面；camera_asset — 白名单资源；serve_file — 公共响应头。
Variable Index: FRONTEND_DIR — 前端目录；ASSETS — 允许文件名与 MIME 的固定映射。
"""

from django.conf import settings
from django.http import FileResponse, Http404
from django.views.decorators.http import require_safe

FRONTEND_DIR = settings.BASE_DIR.parent / "frontend"
ASSETS = {
    "camera.css": "text/css",
    "camera.js": "text/javascript",
    "echo-client.js": "text/javascript",
}


def serve_file(name, content_type):
    """Functionality: 打开确定的前端文件并声明资源访问边界。
    Inputs: 内部白名单文件名及 MIME；读取 FRONTEND_DIR 和允许 Origin。
    Outputs: FileResponse；文件由 Django 响应关闭，缺失文件抛出原文件异常。
    Logic: 使用 same-origin 页面及外部模块脚本，不缓存敏感回传页面。
    Constraints: 此函数只供两个经过限定的视图使用；不接受任意路径。
    """
    response = FileResponse((FRONTEND_DIR / name).open("rb"), content_type=content_type)
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
    """Functionality: 显示手机摄像头原样回传页面。
    Inputs: 只读 Django request；页面不依赖用户或数据库。
    Outputs: HTML FileResponse；非 GET/HEAD 返回 405。
    Logic: 文件名固定，不执行客户端模板插值。
    Constraints: 摄像头权限由浏览器在用户点击后申请。
    """
    return serve_file("index.html", "text/html")


@require_safe
def camera_asset(request, name):
    """Functionality: 提供三个白名单 JavaScript/CSS 文件。
    Inputs: Django request 和路由的单段 name。
    Outputs: 固定 MIME FileResponse；未知文件抛 Http404。
    Logic: 先查固定映射，验证通过后才读取文件。
    Constraints: 不暴露 README、配置、密钥或任意目录。
    """
    if name not in ASSETS:
        raise Http404("Unknown frontend asset")
    return serve_file(name, ASSETS[name])
