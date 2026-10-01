"""Responsibilities: 注册摄像头页面、固定资源、管理后台和公共 API。
Implementation: 页面委派 core.camera，/admin/ 委派 Django，/api/ 委派 core。
Related Modules: core.urls, core.camera, config.base。
Declaration Index: None。
Variable Index: urlpatterns — Django 根路由列表。
"""

from django.contrib import admin
from django.urls import include, path

from core.camera import camera_asset, camera_page

urlpatterns = [
    path("", camera_page, name="camera"),
    path("assets/<str:name>", camera_asset, name="camera-asset"),
    path("admin/", admin.site.urls),
    path("api/", include("core.urls")),
]
