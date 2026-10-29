"""Loopback-only mobile entry for an explicitly configured public HTTPS tunnel."""

import os

from .assist_asgi import application as assist_application
from .tunnel import TunnelApplication

if os.environ.get("PRS_PREPARED_SPEECH") == "1":
    from .prepared_speech import PreparedSpeechApplication

    assist_application = PreparedSpeechApplication(
        assist_application, upstream_origin=os.environ.get("PRS_TTS_UPSTREAM_ORIGIN") or None,
    )
application = TunnelApplication(assist_application)
