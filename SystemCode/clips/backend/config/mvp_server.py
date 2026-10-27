"""Responsibilities: Isolate Django settings for the public MVP Web service.
Implementation: Reuse shared security/framework defaults while assigning an application-specific
SQLite state file and keeping Django's accepted Host names loopback-only behind MobileApplication.
Related Modules: config.base/settings/asgi; integration.mobile_asgi; deploy/prs-mvp-web.service.
Declaration Index: None.
Variable Index: ALLOWED_HOSTS — loopback Host boundary after relay normalization; DATABASES —
MVP-only SQLite state path selected by PRS_MVP_DB_PATH; BASE_DIR — backend source root inherited
from base.
"""

import os
from pathlib import Path

from .base import *  # noqa: F403
from .base import BASE_DIR

ALLOWED_HOSTS = ["127.0.0.1", "localhost", "[::1]"]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": Path(os.environ.get("PRS_MVP_DB_PATH", str(BASE_DIR / "db.sqlite3"))),
    }
}
