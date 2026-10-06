"""Responsibilities: 分派 Django HTTP、摄像头原样回传与HF视频会话 WebSocket。
Implementation: 装配 Django 后导入回传处理器；未知 WebSocket 路径明确关闭。
Related Modules: config.settings/camera_server/production, core.echo/hf_video_relay。
Declaration Index: application — 按协议类型分派，保持原 HTTP 契约。
Variable Index: django_application — Django ASGI HTTP 应用。
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django_application = get_asgi_application()

from core.echo import echo_socket  # noqa: E402
from core.hf_video_relay import relay  # noqa: E402


async def application(scope, receive, send):
    """Functionality: 为 HTTP、原样回传和HF视频 WebSocket 提供唯一入口。
    Inputs: ASGI scope、receive、send；设置由环境选择。
    Outputs: HTTP 响应或 WebSocket 消息；无 Python 返回值。
    Logic: /ws/echo/ 委派原样回传，/ws/detect/hf/ 委派有界私有HF会话；其他WS以1008关闭。
    Constraints: Uvicorn 使用 --lifespan off；仅单 worker，连接上限为进程内共享状态。
    """
    if scope["type"] == "websocket":
        if scope["path"] == "/ws/echo/":
            await echo_socket(scope, receive, send)
        elif scope["path"] == "/ws/detect/hf/":
            await relay(scope, receive, send)
        else:
            await send({"type": "websocket.close", "code": 1008})
    else:
        await django_application(scope, receive, send)
