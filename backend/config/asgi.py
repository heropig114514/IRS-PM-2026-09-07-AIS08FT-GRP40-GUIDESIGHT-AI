"""Responsibilities: Dispatch Django, MCP HTTP and camera/HF/AI control/voice agent WebSockets.
Implementation: Initialize Django before assembling handlers; ASGI lifespan owns MCP task groups.
Related Modules: config.settings/camera_server/production;
core.echo/hf_video_relay/ai_bridge/ai_mcp/agent.transport.
Declaration Index: application — dispatch by protocol and path, preserving HTTP/media contracts.
Variable Index: django_application — Django ASGI HTTP application.
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django_application = get_asgi_application()

from core.agent.transport import agent_socket  # noqa: E402
from core.ai_bridge import hub  # noqa: E402
from core.ai_mcp import mcp_application  # noqa: E402
from core.echo import echo_socket  # noqa: E402
from core.hf_video_relay import relay  # noqa: E402


async def application(scope, receive, send):
    """Functionality: Provide one entry point for HTTP, MCP and four WebSocket paths.
    Inputs: ASGI scope, receive and send; settings are selected through the environment.
    Outputs: HTTP responses or WebSocket messages; no Python return value.
    Logic: Delegate MCP HTTP/lifespan to the SDK, control sockets to hub, agent sockets to bound
    conversations and media paths to their existing handlers.
    Constraints: Uvicorn requires lifespan and one worker; connection/tab state is process-local.
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
        elif scope["path"] == "/ws/agent/":
            await agent_socket(scope, receive, send)
        else:
            await send({"type": "websocket.close", "code": 1008})
    else:
        await django_application(scope, receive, send)
