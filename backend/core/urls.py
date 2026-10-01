"""Responsibilities: 公共接口路由。
Implementation: 将健康检查注册为 /api/health/；项目业务由其他应用承载。
Related Modules: config.urls, core.views。
Declaration Index: None。
Variable Index: app_name — 路由命名空间；urlpatterns — 公共路由列表。
"""

from django.urls import path

from .views import health

app_name = "core"
urlpatterns = [path("health/", health, name="health")]
