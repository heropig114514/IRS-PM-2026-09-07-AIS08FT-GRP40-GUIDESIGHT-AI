"""Responsibilities: 注册管理后台和公共 API，业务路由后续通过独立应用接入。
Implementation: /admin/ 委派 Django，/api/ 委派 core；不实现业务逻辑。
Related Modules: core.urls, config.base。
Declaration Index: None。
Variable Index: urlpatterns — Django 根路由列表。
"""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("core.urls")),
]
