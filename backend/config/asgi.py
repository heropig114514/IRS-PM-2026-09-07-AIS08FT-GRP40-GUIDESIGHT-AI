"""Responsibilities: Uvicorn 的 Django HTTP ASGI 入口。
Implementation: 环境未指定设置模块时选择开发设置，再构造 Django 应用。
Related Modules: config.settings/production, config.urls。
Declaration Index: None。
Variable Index: application — Django ASGI HTTP 应用。
Constraints: 尚未实现 WebSocket 协议路由，不能将 ASGI 入口视为实时业务实现。
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
application = get_asgi_application()
