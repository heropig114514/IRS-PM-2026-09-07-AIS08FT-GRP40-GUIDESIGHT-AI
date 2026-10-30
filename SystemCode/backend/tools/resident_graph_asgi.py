"""Responsibilities: Provide an explicitly selected local-only experimental ASGI entry point.
Implementation: Original Django/MCP app and resident service with an injected Graph test factory.
Related Modules: tools.resident_graph_probe; realtime.services/resident_asgi; config.asgi.
Declaration Index: None.
Variable Index: service — full experimental worker; application — opt-in test entry, never default.
Launch only through the loopback browser diagnostic. This is not a production deployment option.
"""

from django.conf import settings

from config.asgi import application as original_application
from realtime.resident_asgi import ResidentApplication
from realtime.services import ResidentService
from tools.resident_graph_probe import build_graph_detector

service = ResidentService(settings.BASE_DIR, "full", factory=build_graph_detector)
application = ResidentApplication(service, original_application)
