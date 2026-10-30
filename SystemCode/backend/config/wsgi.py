"""Responsibilities: Provide the Django synchronous HTTP deployment entry point.
Implementation: Use development settings by default; production configuration must be selected
explicitly through the environment.
Related Modules: config.settings/production; routine project startup uses config.asgi.
Declaration Index: None。
Variable Index: application — the Django WSGI application.
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
application = get_wsgi_application()
