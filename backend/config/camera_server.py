"""Responsibilities: 为现有 Nginx 后的摄像头诊断应用提供独立 SQLite 部署配置。
Implementation: 仅明确代理 IP 来源；必填路径和 Origin，独立 cookie 避免影响其他 Django 应用。
Related Modules: config.base, deploy/prs-camera.service, deploy/nginx.locations.conf。
Declaration Index: None。
Variable Index:
- ALLOWED_HOSTS — 显式单机 Host；CAMERA_ALLOWED_ORIGINS — 单一 HTTPS Origin。
- DATABASES — /var/lib 下本应用专用数据库；FORCE_SCRIPT_NAME — /prs 反向代理前缀。
- SECURE_PROXY_SSL_HEADER — 仅信任同机代理覆写的协议头；SECURE_SSL_REDIRECT — 强制 HTTPS。
- SESSION_COOKIE_NAME/CSRF_COOKIE_NAME — 独立 cookie 名称。
- SESSION_COOKIE_PATH/CSRF_COOKIE_PATH — 独立 /prs/ cookie 路径。
- SESSION_COOKIE_SECURE/CSRF_COOKIE_SECURE — 只在 HTTPS 发送 cookie。
"""

import os
from pathlib import Path
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403

CAMERA_ALLOWED_ORIGINS = (os.environ["CAMERA_PUBLIC_ORIGIN"],)
if CAMERA_ALLOWED_ORIGINS[0] != "https://47.239.50.129":
    raise ImproperlyConfigured("CAMERA_PUBLIC_ORIGIN must match the inspected HTTPS server")
ALLOWED_HOSTS = [urlsplit(CAMERA_ALLOWED_ORIGINS[0]).hostname]
if not Path(os.environ["PRS_DB_PATH"]).is_absolute():
    raise ImproperlyConfigured("PRS_DB_PATH must be an absolute application-specific path")
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": os.environ["PRS_DB_PATH"]}}
FORCE_SCRIPT_NAME = "/prs"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_NAME = "prs_sessionid"
CSRF_COOKIE_NAME = "prs_csrftoken"
SESSION_COOKIE_PATH = CSRF_COOKIE_PATH = "/prs/"
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = True
