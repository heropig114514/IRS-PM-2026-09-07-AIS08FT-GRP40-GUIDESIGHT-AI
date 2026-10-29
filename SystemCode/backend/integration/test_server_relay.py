"""Responsibilities: Verify prepared voice through the actual server relay entry composition.
Implementation: Load the ASGI entry with an inert downstream, then send canonical public scopes.
Related Modules: integration.mobile_asgi/mobile/prepared_speech; deploy/phone-mvp.env.example.
Declaration Index: ServerRelayTests; ServerRelayTests.load_entry; ServerRelayTests.request;
ServerRelayTests.test_prepared_voice_through_public_gate; ServerRelayTests.test_wrong_origin;
ServerRelayTests.test_disabled_profile_preserves_downstream; ServerRelayTests.test_server_preset.
Variable Index: ORIGIN is the explicit deployment target; no SSH, GPU or physical speaker is used.
"""

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, patch

from asgiref.testing import ApplicationCommunicator
from django.test import SimpleTestCase, override_settings

from .prepared_speech import PreparedSpeechApplication

ORIGIN = "https://47.239.50.129"


@override_settings(CAMERA_ALLOWED_ORIGINS=("http://127.0.0.1:18766",))
class ServerRelayTests(SimpleTestCase):
    """Functionality: Protect same-origin public startup audio in the server topology.
    Logic: exercise the actual entry and fixed PCM. Constraints: no network/provider synthesis.
    """

    def load_entry(self, prepared=True):
        """Functionality: Load the real entry with controlled role configuration.
        Inputs: explicit prepared mode. Outputs: public wrapper and inert downstream.
        Logic: replace only expensive software bootstrap. Constraints: retain real admission/audio.
        """
        original = ModuleType("integration.assist_asgi")
        original.application = AsyncMock()
        spec = importlib.util.spec_from_file_location(
            "integration._server_entry_test", Path(__file__).with_name("mobile_asgi.py"))
        entry = importlib.util.module_from_spec(spec)
        with patch.dict("sys.modules", {"integration.assist_asgi": original}), patch.dict(
            "os.environ", {"MOBILE_PUBLIC_ORIGIN": ORIGIN,
                           "LOCAL_WEB_ORIGIN": "http://127.0.0.1:18766",
                           "PRS_PREPARED_SPEECH": "1" if prepared else "0",
                           "PRS_TTS_UPSTREAM_ORIGIN": ""},
        ):
            spec.loader.exec_module(entry)
        return entry.application, original.application

    async def request(self, application, origin=ORIGIN):
        """Functionality: Submit a public TTS socket behind the approved Nginx relay.
        Inputs: wrapper and optional hostile Origin. Outputs: communicator and first message.
        Logic: canonical relay marker and loopback peer. Constraints: no forwarded-header bypass.
        """
        peer = ApplicationCommunicator(application, {
            "type": "websocket", "path": "/ws/tts/", "query_string": b"",
            "client": ("127.0.0.1", 42000), "headers": [
                (b"host", b"47.239.50.129"), (b"origin", origin.encode()),
                (b"x-prs-relay", b"1"),
            ],
        })
        await peer.send_input({"type": "websocket.connect"})
        return peer, await peer.receive_output()

    async def test_prepared_voice_through_public_gate(self):
        """Functionality: Play verified startup PCM after the server's Origin normalization.
        Inputs: real public wrapper and valid request. Outputs: PCM and successful completion.
        Logic: assert request identity and nonempty bytes. Constraints: no provider invocation.
        """
        app, downstream = self.load_entry()
        self.assertIsInstance(app.application, PreparedSpeechApplication)
        peer, first = await self.request(app)
        self.assertEqual(first["type"], "websocket.accept")
        await peer.send_input({"type": "websocket.receive", "text": json.dumps({
            "type": "synthesize", "request_id": "a38166cc-33d4-430f-885d-b189a8530e00",
            "language": "zh-CN", "rate": 2, "text": "语音提醒已开启。",
        })})
        config = json.loads((await peer.receive_output())["text"])
        self.assertEqual(config["sample_rate"], 24000)
        total = 0
        while True:
            message = await peer.receive_output()
            if "bytes" in message:
                total += len(message["bytes"])
            else:
                complete = json.loads(message["text"])
                break
        self.assertGreater(total, 0)
        self.assertEqual(complete["audio_bytes"], total)
        self.assertEqual((await peer.receive_output())["code"], 1000)
        await peer.wait()
        downstream.assert_not_called()

    async def test_wrong_origin(self):
        """Functionality: Reject cross-origin speech before audio/provider access.
        Inputs: foreign Origin. Outputs: policy close. Logic: public admission runs first.
        Constraints: no permissive localhost bypass for proxied public scopes.
        """
        app, downstream = self.load_entry()
        peer, message = await self.request(app, "https://example.invalid")
        self.assertEqual(message, {"type": "websocket.close", "code": 1008})
        await peer.wait()
        downstream.assert_not_called()

    def test_disabled_profile_preserves_downstream(self):
        """Functionality: Keep prepared playback opt-in for existing deployments.
        Inputs: disabled profile. Outputs: original downstream identity.
        Logic: load the actual alternate entry branch. Constraints: no automatic mode switch.
        """
        app, downstream = self.load_entry(False)
        self.assertIs(app.application, downstream)

    def test_server_preset(self):
        """Functionality: Keep comparison controls explicit and free of provider credentials.
        Inputs: repository preset. Outputs: exact transport/voice selections.
        Logic: parse only assignment lines. Constraints: operators merge into private app.env.
        """
        path = Path(__file__).resolve().parents[1] / "deploy/phone-mvp.env.example"
        values = dict(line.split("=", 1) for line in path.read_text().splitlines()
                      if line and not line.startswith("#"))
        self.assertEqual(values["LOCAL_INFERENCE_URL"], "ws://127.0.0.1:18767/ws/infer/")
        self.assertEqual(values["PRS_CAMERA_PROFILE"], "low-latency-2")
        self.assertEqual(values["PRS_RISK_TTL_MS"], "2000")
        self.assertEqual(values["PRS_PREPARED_SPEECH"], "1")
        self.assertFalse(any("KEY" in key or "PASSWORD" in key for key in values))
