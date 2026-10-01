"""Responsibilities: 本地开发数据库与主机白名单。
Implementation: 使用共享装配及 backend/db.sqlite3；仅允许本机 Host。
Related Modules: config.base；生产必须显式选择 config.production。
Declaration Index: None。
Variable Index: ALLOWED_HOSTS — 本地主机；DATABASES — SQLite 路径。
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
