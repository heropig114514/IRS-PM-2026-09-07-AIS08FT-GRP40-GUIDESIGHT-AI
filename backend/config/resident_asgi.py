"""Responsibilities: Explicit native resident GPU deployment entry alongside the original app.
Implementation: Wrap existing Django/MCP lifecycle; select workload before one-time CUDA startup.
Related Modules: config.asgi; realtime.services/resident_asgi.
Declaration Index: None.
Variable Index: service — one process-owned pipeline; application — Uvicorn ASGI entry.
"""

import os

from django.conf import settings

from config.asgi import application as original_application
from realtime.resident_asgi import ResidentApplication
from realtime.services import ResidentService

service = ResidentService(settings.BASE_DIR, os.environ.get("RESIDENT_MODE", "full"))
application = ResidentApplication(service, original_application)
