"""Responsibilities: Configure the local development database and host allowlist.
Implementation: Use shared settings and SystemCode/backend/db.sqlite3; permit only local
hosts.
Related Modules: config.base; production must explicitly select config.production.
Declaration Index: None.
Variable Index: ALLOWED_HOSTS — local hosts; DATABASES — SQLite path.
"""

from .base import *  # noqa: F403
from .base import BASE_DIR

ALLOWED_HOSTS = ["127.0.0.1", "localhost", "[::1]"]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}
