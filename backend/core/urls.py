"""Responsibilities: Define shared API routes.
Implementation: Register the health check at /api/health/; project business logic belongs to other applications.
Related Modules: config.urls, core.views。
Declaration Index: None。
Variable Index: app_name — route namespace; urlpatterns — shared route list.
"""

from django.urls import path

from .views import health

app_name = "core"
urlpatterns = [path("health/", health, name="health")]
