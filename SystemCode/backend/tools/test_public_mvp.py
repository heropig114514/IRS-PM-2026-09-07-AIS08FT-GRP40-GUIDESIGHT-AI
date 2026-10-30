"""Responsibilities: Verify the published MVP's explicit configuration and publication boundary.
Implementation: Pure configuration, real HTML rendering and preflight seams; no network or GPU.
Related Modules: tools.run_public_mvp; integration.assist.
Declaration Index: PublicMvpTests; PublicMvpTests.args; PublicMvpTests.test_frozen_profile;
PublicMvpTests.test_connector_proxy_isolation; PublicMvpTests.test_unique_camera_attribute;
PublicMvpTests.test_invalid_upstream; PublicMvpTests.test_bad_binary_fails_before_gpu;
PublicMvpTests.test_check_does_not_publish.
Variable Index: None.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase

from integration.assist import render_assist_page
from tools import run_public_mvp as public


class PublicMvpTests(SimpleTestCase):
    """Functionality: Check repeatable MVP settings and no accidental publication in check mode.
    Logic: preserve exact supported policy and reject ambiguity. Constraints: synthetic evidence.
    """

    def args(self, *extra):
        """Functionality: Build explicit CLI inputs. Inputs: overrides. Outputs: namespace.
        Logic: fixed inert paths/checksum. Constraints: no actual executable or engine load.
        """
        return public.parse_args(["--engine-manifest", "models/example/manifest.json",
                                  "--cloudflared", "connector", "--cloudflared-sha256", "0" * 64,
                                  *extra])

    def test_frozen_profile(self):
        """Functionality: Ignore a shell's unrelated detector/depth/voice and provider settings.
        Inputs: conflicting inherited values. Outputs: exact accepted profile and cleared keys.
        Logic: exercise real environment construction. Constraints: parent remains unchanged.
        """
        with patch.dict(os.environ, {"PRS_DETECTION_MODEL": "yolo26n-walk",
                                    "PRS_DEPTH_BACKEND": "torch", "PRS_RISK_TTL_MS": "1000",
                                    "PRS_CAMERA_PROFILE": "standard",
                                    "PRS_AGENT_API_KEY": "fixture",
                                    "PRS_TTS_UPSTREAM_ORIGIN": "https://old.invalid"}):
            selected = public.selected_environment(self.args())
            self.assertEqual(selected["PRS_DETECTION_MODEL"], "yolo26n")
            self.assertEqual(selected["PRS_DEPTH_BACKEND"], "tensorrt")
            self.assertEqual(selected["PRS_CAMERA_PROFILE"], "low-latency-2")
            self.assertEqual(selected["PRS_RISK_TTL_MS"], "2000")
            self.assertEqual(selected["PRS_AGENT_API_KEY"], "")
            self.assertEqual(selected["PRS_TTS_UPSTREAM_ORIGIN"], "")
            self.assertEqual(os.environ["PRS_DETECTION_MODEL"], "yolo26n-walk")

    def test_connector_proxy_isolation(self):
        """Functionality: Remove proxy/token inheritance without editing the host.
        Inputs: mixed-case proxy keys. Outputs: only ordinary runtime variables survive.
        Logic: verify a copy. Constraints: OS VPN routes are outside this policy.
        """
        original = {"https_proxy": "fixture", "HTTP_PROXY": "fixture", "All_Proxy": "fixture",
                    "TUNNEL_TOKEN": "fixture", "TUNNEL_ORIGIN_CERT": "fixture", "PATH": "runtime"}
        self.assertEqual(public.connector_environment(original), {"PATH": "runtime"})
        self.assertEqual(len(original), 6)

    def test_unique_camera_attribute(self):
        """Functionality: Prevent duplicate HTML attributes from selecting a high-queue profile.
        Inputs: explicit profiles and both templates. Outputs: one exact selected attribute.
        Logic: render actual repository templates. Constraints: no browser permission claim.
        """
        with patch.dict(os.environ, {"PRS_LATENCY_PANEL": "1", "PRS_RISK_TTL_MS": "2000"}):
            for view in ("product", "test"):
                for profile in ("low-latency-2", "bounded-2", "standard"):
                    html, _ = render_assist_page(view=view, camera_profile=profile)
                    self.assertEqual(html.count(b'data-camera-profile="'), 1)
                    self.assertIn(f'data-camera-profile="{profile}"'.encode(), html)

    def test_invalid_upstream(self):
        """Functionality: Reject an unsafe optional speech target.
        Inputs: insecure origin or URL credentials. Outputs: ValueError.
        Logic: use the existing mobile origin contract. Constraints: no external request.
        """
        for target in ("http://example.com", "https://user:password@example.com"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                public.selected_environment(self.args("--tts-upstream", target))

    def test_bad_binary_fails_before_gpu(self):
        """Functionality: Refuse an unverified connector before any executable is started.
        Inputs: mismatched file hash. Outputs: failure and no runtime probe.
        Logic: exercise real preflight order. Constraints: inert temporary file only.
        """
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "connector"
            binary.write_bytes(b"inert fixture")
            args = self.args("--cloudflared", str(binary))
            with patch.object(public, "inspect_environment") as probe:
                with self.assertRaises(public.StartupError):
                    public.preflight(args)
                probe.assert_not_called()

    def test_check_does_not_publish(self):
        """Functionality: Keep preflight separate from public traffic and process creation.
        Inputs: successful check-mode preflight. Outputs: no directory, warmup or child creation.
        Logic: run the actual check-mode branch. Constraints: environment patch restores parent.
        """
        args = self.args("--check")
        args.environment = dict(os.environ)
        with patch.dict(os.environ), patch.object(public, "preflight", return_value=(None, {})), \
                patch.object(public.subprocess, "Popen") as spawn, \
                patch.object(public, "warmup") as warmup, \
                patch.object(Path, "mkdir") as mkdir:
            public.run(args)
            spawn.assert_not_called()
            warmup.assert_not_called()
            mkdir.assert_not_called()
