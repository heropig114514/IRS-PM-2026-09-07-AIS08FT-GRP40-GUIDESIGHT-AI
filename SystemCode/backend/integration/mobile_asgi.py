"""Responsibilities: Expose the explicitly configured mobile relay around the local assist entry.
Implementation: Add exact public admission and optional prepared startup speech while preserving
the server's canonical Web/GPU origin, controller hub and local-only MCP access.
Related Modules: integration.mobile/assist_asgi/prepared_speech; reverse SSH/Nginx deployment.
Declaration Index: None.
Variable Index: application — mobile-only wrapper; PRS_PREPARED_SPEECH explicitly enables the
same local startup PCM used by the accepted direct-laptop MVP; risk clips remain HTTP assets.
"""

import os

from .assist_asgi import application as assist_application
from .mobile import MobileApplication

if os.environ.get("PRS_PREPARED_SPEECH") == "1":
    from .prepared_speech import PreparedSpeechApplication

    assist_application = PreparedSpeechApplication(
        assist_application, upstream_origin=os.environ.get("PRS_TTS_UPSTREAM_ORIGIN") or None,
    )
application = MobileApplication(assist_application)
