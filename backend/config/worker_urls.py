"""Responsibilities: Expose only authenticated loopback GPU readiness and frame endpoints.
Implementation: Fixed URL dispatch, no admin/maps/media/download endpoint.
Related Modules: config.laptop_worker; core.laptop_worker.
Declaration Index: None.
Variable Index: urlpatterns — private worker route table.
"""

from django.urls import path

from core.laptop_worker import worker_config, worker_frame

urlpatterns = [path("config/", worker_config), path("frame/", worker_frame)]
