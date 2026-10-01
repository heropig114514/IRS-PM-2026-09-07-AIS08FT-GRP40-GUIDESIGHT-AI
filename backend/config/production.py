"""Responsibilities: 显式 PostgreSQL 与 HTTPS 生产配置。
Implementation: 直接继承共享设置；缺失变量或连接失败时不切换开发数据库。
Related Modules: config.base, requirements-production.txt；HTTPS/静态资源由反向代理提供。
Declaration Index: None。
Variable Index:
- ALLOWED_HOSTS — 必填主机白名单；DATABASES — 必填 PostgreSQL 连接配置。
- SECURE_SSL_REDIRECT — 强制 HTTPS；SESSION_COOKIE_SECURE/CSRF_COOKIE_SECURE — 安全 cookie。
Constraints: 未信任任何代理头；HTTPS 代理部署须明确可信边界后配置协议头处理。
"""

import os

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403

ALLOWED_HOSTS = [host.strip() for host in os.environ["DJANGO_ALLOWED_HOSTS"].split(",")]
if any(
    not host or any(char in host for char in "*/ \t\r\n") or "://" in host
    for host in ALLOWED_HOSTS
):
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS requires explicit hostnames or IP addresses")
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ["POSTGRES_DB"],
        "USER": os.environ["POSTGRES_USER"],
        "PASSWORD": os.environ["POSTGRES_PASSWORD"],
        "HOST": os.environ["POSTGRES_HOST"],
        "PORT": os.environ["POSTGRES_PORT"],
        "CONN_MAX_AGE": 0,
    }
}
if any(
    not str(value).strip()
    for key, value in DATABASES["default"].items()
    if key != "CONN_MAX_AGE"
):
    raise ImproperlyConfigured("PostgreSQL configuration values must not be empty")
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
