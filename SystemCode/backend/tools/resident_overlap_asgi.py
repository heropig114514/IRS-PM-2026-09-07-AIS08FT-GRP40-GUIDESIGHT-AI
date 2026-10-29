"""Responsibilities: Provide the explicit loopback-only tracking/depth overlap test entry.
Implementation: Original ASGI app and serialized resident service with the experimental factory.
Related Modules: tools.resident_overlap_probe; realtime.resident_asgi/services; config.asgi.
Declaration Index: None.
Variable Index: service — owned full worker; application — explicit experimental entry only.
"""

from django.conf import settings

from config.asgi import application as original_application
from realtime.resident_asgi import ResidentApplication
from realtime.services import ResidentService
from tools.resident_overlap_probe import build_overlap_detector

service = ResidentService(settings.BASE_DIR, "full", factory=build_overlap_detector)
application = ResidentApplication(service, original_application)
