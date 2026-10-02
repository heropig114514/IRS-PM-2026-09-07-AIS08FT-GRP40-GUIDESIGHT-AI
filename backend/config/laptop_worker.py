"""Responsibilities: Local-only Django GPU worker configuration, separate from web deployment.
Implementation: Load ignored .env.laptop, explicit loopback host and private worker URL table.
Related Modules: config.base, config.worker_urls, core.laptop_worker, requirements-laptop.txt.
Declaration Index: None.
Variable Index: ALLOWED_HOSTS/ROOT_URLCONF — private HTTP boundary;
DATABASES — unused worker SQLite; DATA_UPLOAD_MAX_MEMORY_SIZE — existing 1 MiB frame limit.
"""

from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env.laptop", override=False)

from .base import *  # noqa: E402,F403

ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
ROOT_URLCONF = "config.worker_urls"
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
DATA_UPLOAD_MAX_MEMORY_SIZE = 1048576
