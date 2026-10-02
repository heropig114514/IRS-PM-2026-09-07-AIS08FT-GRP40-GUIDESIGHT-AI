"""Responsibilities: 注册摄像头、CPU/ZeroGPU YOLO 检测、地图、固定资源和公共 API。
Implementation: 页面委派 core.camera/maps，/admin/ 委派 Django，/api/ 委派 core。
Related Modules: core.urls/camera/maps/vision/hf_vision, config.base。
Declaration Index: None。
Variable Index: urlpatterns — Django 根路由列表。
"""

from django.contrib import admin
from django.urls import include, path

from core.camera import camera_asset, camera_page
from core.hf_vision import hf_config, hf_frame
from core.maps import maps_config, maps_page, maps_route
from core.vision import vision_config, vision_frame

urlpatterns = [
    path("", camera_page, name="camera"),
    path("assets/<str:name>", camera_asset, name="camera-asset"),
    path("vision/config/", vision_config, name="vision-config"),
    path("vision/frame/", vision_frame, name="vision-frame"),
    path("vision/hf/config/", hf_config, name="hf-config"),
    path("vision/hf/frame/", hf_frame, name="hf-frame"),
    path("maps/", maps_page, name="maps"),
    path("maps/config/", maps_config, name="maps-config"),
    path("maps/route/", maps_route, name="maps-route"),
    path("admin/", admin.site.urls),
    path("api/", include("core.urls")),
]
