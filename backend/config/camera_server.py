"""Responsibilities: Provide isolated SQLite deployment settings for the camera diagnostic app behind the existing Nginx proxy.
Implementation: Explicitly define the proxy IP source; require the database path and Origin, and use isolated cookies to avoid affecting other Django applications.
Related Modules: config.base, deploy/prs-camera.service, deploy/nginx.locations.conf。
Declaration Index: None。
Variable Index:
- ALLOWED_HOSTS — explicit single-server hosts; CAMERA_ALLOWED_ORIGINS — the sole HTTPS Origin.
- DATABASES — application-specific database under /var/lib; FORCE_SCRIPT_NAME — /prs reverse-proxy prefix.
- SECURE_PROXY_SSL_HEADER — trust only the protocol header overwritten by the same-host proxy; SECURE_SSL_REDIRECT — require HTTPS.
- SESSION_COOKIE_NAME/CSRF_COOKIE_NAME — isolated cookie names.
- SESSION_COOKIE_PATH/CSRF_COOKIE_PATH — isolated /prs/ cookie paths.
- SESSION_COOKIE_SECURE/CSRF_COOKIE_SECURE — send cookies over HTTPS only.
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
