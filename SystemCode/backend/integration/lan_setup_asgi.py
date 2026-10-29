"""Responsibilities: Bind the explicit LAN certificate-enrollment ASGI configuration.
Implementation: Construct only the public setup application from launcher-owned environment.
Related Modules: integration.lan_setup; tools.run_lan_mvp.
Declaration Index: None.
Variable Index: application is the public-certificate-only HTTP endpoint.
"""

import os

from .lan_setup import LanSetupApplication

application = LanSetupApplication(
    address=os.environ["PRS_LAN_ADDRESS"],
    subnet=os.environ["PRS_LAN_SUBNET"],
    port=int(os.environ["PRS_LAN_SETUP_PORT"]),
    origin=os.environ["PRS_LAN_ORIGIN"],
    tls_directory=os.environ["PRS_LAN_TLS_DIR"],
)
