"""Responsibilities: Expose the explicitly configured mobile relay around the local assist entry.
Implementation: Add exact public admission and mount headers while preserving original loopback
Web/GPU origin configuration, shared controller hub and local-only MCP access.
Related Modules: integration.mobile/assist_asgi; mobile launcher and reverse SSH/Nginx deployment.
Declaration Index: None.
Variable Index: application — mobile-only wrapper around the original combined callable.
"""

from .assist_asgi import application as assist_application
from .mobile import MobileApplication

application = MobileApplication(assist_application)
