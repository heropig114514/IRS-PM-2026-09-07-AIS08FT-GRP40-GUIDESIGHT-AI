"""Responsibilities: Start the local integration wrapper around the unchanged software ASGI app.
Implementation: Explicit local Origin configuration and new integration routes only.
Related Modules: config.asgi; integration.services/protocol; tools.run_local_mvp.
Declaration Index: None.
Variable Index: application — local MVP wrapper around the imported software ASGI callable.
"""

from django.conf import settings

from config.asgi import application as software_application

from .protocol import web_origins
from .services import IntegrationApplication

# The explicit local launcher port applies to the original Echo/control handlers in this process.
settings.CAMERA_ALLOWED_ORIGINS = web_origins()
application = IntegrationApplication(software_application)
