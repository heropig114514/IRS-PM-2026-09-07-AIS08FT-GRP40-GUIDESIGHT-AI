"""Responsibilities: Expose the explicit direct-LAN HTTPS assist entry.
Implementation: Wrap the existing application with fixed LAN admission from startup settings.
Related Modules: integration.lan/assist_asgi; tools.run_lan_mvp.
Declaration Index: None.
Variable Index: application is the isolated LAN wrapper; PRS_LAN_ORIGIN/PRS_LAN_SUBNET are required.
"""

import os

from .assist_asgi import application as assist_application
from .lan import LanApplication

application = LanApplication(
    assist_application, origin=os.environ["PRS_LAN_ORIGIN"], subnet=os.environ["PRS_LAN_SUBNET"]
)
