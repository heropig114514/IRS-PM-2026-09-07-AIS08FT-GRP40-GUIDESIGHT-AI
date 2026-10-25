"""Responsibilities: Define explicit PostgreSQL and HTTPS production settings.
Implementation: Inherit shared settings directly; missing variables or connection failures do not fall back to the development database.
Related Modules: config.base, requirements-production.txt; HTTPS/static assets are served by a reverse proxy.
Declaration Index: None。
Variable Index:
- ALLOWED_HOSTS — required host allowlist; DATABASES — required PostgreSQL connection settings.
- SECURE_SSL_REDIRECT — require HTTPS; SESSION_COOKIE_SECURE/CSRF_COOKIE_SECURE — secure cookies.
Constraints: No proxy headers are trusted; configure forwarded-protocol handling only after defining the trusted boundary for an HTTPS proxy deployment.
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
