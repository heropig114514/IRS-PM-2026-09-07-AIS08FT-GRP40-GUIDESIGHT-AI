"""Responsibilities: Compose the loopback-only public phone entry.
Implementation: Optional prepared startup speech inside the exact-origin tunnel wrapper.
Related Modules: integration.tunnel/prepared_speech/assist_asgi; tools.run_public_mvp.
Declaration Index: None.
Variable Index: application is the mounted public entry; assist_application is its canonical target.
"""

import os

from .assist_asgi import application as assist_application
from .tunnel import TunnelApplication

if os.environ.get("PRS_PREPARED_SPEECH") == "1":
    from .prepared_speech import PreparedSpeechApplication

    assist_application = PreparedSpeechApplication(
        assist_application, upstream_origin=os.environ.get("PRS_TTS_UPSTREAM_ORIGIN") or None,
    )
application = TunnelApplication(assist_application)
