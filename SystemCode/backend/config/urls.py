"""Responsibilities: Register the camera page, shared assets, maps, scene description, health/admin.
Implementation: Root redirects to live/; shared assets accept exact allowlisted nested names;
live assets retain their single-segment route; ASGI Echo/HF paths perform frame processing.
Related Modules: core.camera/maps/scene/urls; config.asgi; config.base.
Declaration Index: None.
Variable Index: urlpatterns — explicit HTTP routes; no detector HTTP API.
"""

from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from core.camera import camera_asset, camera_page
from core.maps import maps_config, maps_page, maps_route
from core.scene import scene_config, scene_describe

urlpatterns = [
    path("", RedirectView.as_view(url="live/", permanent=False), name="camera-root"),
    path("live/", camera_page, name="camera"),
    path("live/assets/<str:name>", camera_asset, name="camera-asset"),
    path("assets/<path:name>", camera_asset, name="shared-asset"),
    path("maps/", maps_page, name="maps"),
    path("maps/config/", maps_config, name="maps-config"),
    path("maps/route/", maps_route, name="maps-route"),
    path("scene/config/", scene_config, name="scene-config"),
    path("scene/describe/", scene_describe, name="scene-describe"),
    path("admin/", admin.site.urls),
    path("api/", include("core.urls")),
]
