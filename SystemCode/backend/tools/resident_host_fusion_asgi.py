"""Responsibilities: Expose explicit overlap/host-fusion local browser experiments.
Implementation: Original ASGI application and serialized resident worker with probe factory.
Related Modules: tools.resident_host_fusion_probe; realtime.resident_asgi/services; config.asgi.
Declaration Index: None.
Variable Index: service — owned full worker; application — explicitly selected experiment.
"""

from django.conf import settings

from config.asgi import application as original_application
from realtime.resident_asgi import ResidentApplication
from realtime.services import ResidentService
from tools.resident_host_fusion_probe import build_host_fusion_detector

service = ResidentService(settings.BASE_DIR, "full", factory=build_host_fusion_detector)
application = ResidentApplication(service, original_application)
