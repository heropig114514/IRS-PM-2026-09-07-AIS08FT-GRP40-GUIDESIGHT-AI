"""Responsibilities: 分派 Django、MCP HTTP、摄像头/HF/AI 控制 WebSocket。
Implementation: Django 初始化后装配处理器；ASGI lifespan 管理 MCP 的任务组。
Related Modules: config.settings/camera_server/production;
core.echo/hf_video_relay/ai_bridge/ai_mcp。
Declaration Index: application — 按协议类型和路径分派，保持既有 HTTP/媒体契约。
Variable Index: django_application — Django ASGI HTTP 应用。
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django_application = get_asgi_application()

from core.ai_bridge import hub  # noqa: E402
from core.ai_mcp import mcp_application  # noqa: E402
from core.echo import echo_socket  # noqa: E402
from core.hf_video_relay import relay  # noqa: E402


async def application(scope, receive, send):
    """Functionality: 为 HTTP、MCP 和三种 WebSocket 提供唯一入口。
    Inputs: ASGI scope、receive、send；设置由环境选择。
    Outputs: HTTP 响应或 WebSocket 消息；无 Python 返回值。
    Logic: MCP HTTP/lifespan 委派官方 SDK；控制 WS 使用共享 hub；媒体路径保持原处理器。
    Constraints: Uvicorn 必须启用 lifespan；仅单 worker，连接/AI tab 状态均为进程内共享。
    """
    if scope["type"] == "lifespan":
        await mcp_application(scope, receive, send)
    elif scope["type"] == "http" and scope["path"] in {"/mcp", "/mcp/"}:
        await mcp_application({**scope, "path": "/mcp"}, receive, send)
    elif scope["type"] == "websocket":
        if scope["path"] == "/ws/echo/":
            await echo_socket(scope, receive, send)
        elif scope["path"] == "/ws/detect/hf/":
            await relay(scope, receive, send)
        elif scope["path"] == "/ws/control/":
            await hub.socket(scope, receive, send)
        else:
            await send({"type": "websocket.close", "code": 1008})
    else:
        await django_application(scope, receive, send)
