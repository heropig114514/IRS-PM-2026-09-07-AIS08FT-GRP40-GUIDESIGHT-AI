"""Responsibilities: Verify fixed bilingual signal speech assets and HTTP isolation.
Implementation: Real generated pack and ASGI responses; corrupt only temporary fixture copies.
Related Modules: integration.signal_audio/assist; frontend/assets/signal-audio.
Declaration Index: SignalAudioTests; SignalAudioTests.setUp;
SignalAudioTests.test_verified_bilingual_assets;
SignalAudioTests.test_routes_and_methods_are_fixed;
SignalAudioTests.test_corruption_and_missing_files_are_explicit.
Variable Index: None. No synthesis, GPU, phone access or audible playback occurs.
"""

import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase

from .assist import AssistApplication
from .services import FRONTEND
from .signal_audio import FILES, read_signal_resource
from .test_assist import http_call


class SignalAudioTests(SimpleTestCase):
    """Functionality: Keep prepared signal audio exact and independently deployable.
    Logic: inspect actual pack integrity and the fixed public resource route.
    Constraints: passing tests does not establish physical phone sound or signal recognition.
    """

    def setUp(self):
        """Functionality: Select the explicit assist entry for each isolated route test.
        Inputs: none. Outputs: temporary mode/origin configuration.
        Logic: restore all environment values on cleanup. Constraints: no service startup.
        """
        environment = patch.dict("os.environ", {"LOCAL_MVP_MODE": "assist",
                                                "LOCAL_WEB_ORIGIN": "http://127.0.0.1:8766"})
        environment.start()
        self.addCleanup(environment.stop)

    async def test_verified_bilingual_assets(self):
        """Functionality: Serve all four verified clips plus the matching manifest.
        Inputs: exact GET/HEAD paths. Outputs: original bytes, MIME and content length.
        Logic: actual checksum/PCM validation, preserving the existing mount-relative paths.
        Constraints: no provider request, generated test audio or alternate clip path.
        """
        app = AssistApplication(AsyncMock())
        root = FRONTEND / "assets/signal-audio"
        for name in ("manifest.json", *sorted(FILES)):
            expected, mime = read_signal_resource(root, name)
            path = "/assets/signal-audio/" + name
            start, raw = await http_call(app, path)
            self.assertEqual(start["status"], 200)
            self.assertEqual(raw, expected)
            self.assertEqual(dict(start["headers"])[b"content-type"], mime.encode())
            head, empty = await http_call(app, path, "HEAD")
            self.assertEqual(head["status"], 200)
            self.assertEqual(empty, b"")
            self.assertEqual(int(dict(head["headers"])[b"content-length"]), len(expected))

    async def test_routes_and_methods_are_fixed(self):
        """Functionality: Reject aliases, private files and synthesis-like resource requests.
        Inputs: unknown paths/query and unsupported method. Outputs: explicit 404/405.
        Logic: fail before disk/provider access. Constraints: no directory listing or traversal.
        """
        software = AsyncMock()
        app = AssistApplication(software)
        for name in ("", "README.md", "red.wav", "zh-CN/unknown.wav", "../.env",
                     "%2e%2e/.env", "zh-CN/../manifest.json", "zh-CN/red.wav/extra"):
            start, _ = await http_call(app, "/assets/signal-audio/" + name)
            self.assertEqual(start["status"], 404, name)
        path = "/assets/signal-audio/zh-CN/red.wav"
        self.assertEqual((await http_call(app, path, query_string=b"generate=1"))[0]["status"], 404)
        self.assertEqual((await http_call(app, path, "POST"))[0]["status"], 405)
        software.assert_not_awaited()

    async def test_corruption_and_missing_files_are_explicit(self):
        """Functionality: Never serve damaged or absent prepared speech as a successful clip.
        Inputs: temporary pack with altered WAV/manifest and missing files. Outputs: 503.
        Logic: mutate only owned fixture copies, checking both fingerprint and utterance identity.
        Constraints: original resources remain unchanged; no silent synthesis fallback is invoked.
        """
        with tempfile.TemporaryDirectory() as directory:
            frontend = Path(directory)
            root = frontend / "assets/signal-audio"
            shutil.copytree(FRONTEND / "assets/signal-audio", root)
            app = AssistApplication(AsyncMock())
            with patch("integration.assist.FRONTEND", frontend):
                wav = root / "zh-CN/red.wav"
                original = wav.read_bytes()
                wav.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
                start, raw = await http_call(app, "/assets/signal-audio/zh-CN/red.wav")
                self.assertEqual(start["status"], 503)
                self.assertEqual(json.loads(raw)["code"], "risk_audio_unavailable")
                wav.write_bytes(original)
                manifest = root / "manifest.json"
                value = json.loads(manifest.read_text(encoding="utf-8"))
                value["clips"][0]["text"] = "Unapproved signal wording"
                manifest.write_text(json.dumps(value), encoding="utf-8")
                self.assertEqual((await http_call(app, "/assets/signal-audio/manifest.json"))
                                 [0]["status"], 503)
                manifest.unlink()
                self.assertEqual((await http_call(app, "/assets/signal-audio/manifest.json"))
                                 [0]["status"], 503)
