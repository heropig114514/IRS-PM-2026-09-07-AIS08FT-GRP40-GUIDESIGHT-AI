"""Responsibilities: Django 同步 HTTP 部署入口。
Implementation: 默认使用开发设置，生产通过环境显式选择配置。
Related Modules: config.settings/production；本项目日常启动使用 config.asgi。
Declaration Index: None。
Variable Index: application — Django WSGI 应用。
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
application = get_wsgi_application()
