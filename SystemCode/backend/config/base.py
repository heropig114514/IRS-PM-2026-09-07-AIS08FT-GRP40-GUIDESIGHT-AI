"""Responsibilities: Shared HTTP, ORM, session authentication, admin, and logging configuration.
Implementation: Read SystemCode/backend/.env and stop startup when required secrets are
missing; database settings are selected by environment modules.
Related Modules: config.settings/production/camera_server select databases; core provides public
APIs and echo transport.
Declaration Index: None。
Variable Index:
- BASE_DIR — Absolute backend path; SECRET_KEY — required Django secret.
- DEBUG — Disables debug response bodies; INSTALLED_APPS — Django/DRF and shared applications.
- MIDDLEWARE — Ordered security, session, authentication, CSRF, and clickjacking protections.
- ROOT_URLCONF — Root routes; ASGI_APPLICATION/WSGI_APPLICATION — service entry points.
- TEMPLATES — Template engine and context required by the admin site.
- AUTH_PASSWORD_VALIDATORS — Django's four standard password validators.
- DEFAULT_AUTO_FIELD — Default primary-key type; USE_TZ/TIME_ZONE — UTC timezone semantics.
- STATIC_URL/STATIC_ROOT — Admin static-resource URL and collection directory.
- REST_FRAMEWORK — JSON APIs, session authentication, default login permission, and error handling.
- LOGGING — Console logs for core and Django request errors.
- CAMERA_ALLOWED_ORIGINS — Explicit same-origin allowlist for the local camera WebSocket.
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env", override=False)
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY.strip():
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be explicitly configured")
DEBUG = False
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "core",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
ASGI_APPLICATION = "config.asgi.application"
WSGI_APPLICATION = "config.wsgi.application"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"
CAMERA_ALLOWED_ORIGINS = ("http://127.0.0.1:8766", "http://localhost:8766")
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "EXCEPTION_HANDLER": "core.errors.api_exception_handler",
}
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"context": {"format": "{asctime} {levelname} {name} {message}", "style": "{"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "context"}},
    "loggers": {
        "core": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}
