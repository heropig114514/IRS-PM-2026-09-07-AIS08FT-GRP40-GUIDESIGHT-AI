"""Responsibilities: Verify LAN admission, certificate enrollment and capture-profile boundaries.
Implementation: In-memory ASGI requests, temporary dedicated certificates and real HTML rendering.
Related Modules: integration.lan/lan_setup/assist; tools.lan_certificate/run_lan_mvp.
Declaration Index: LanTests; LanTests.setUp; LanTests.tearDown; LanTests.call;
LanTests.test_https_route_and_origin; LanTests.test_network_and_header_rejections;
LanTests.test_no_unlisted_paths_or_insecure_access; LanTests.test_exact_private_configuration;
LanTests.test_capture_profile_template_and_rejection; CertificateTests;
CertificateTests.test_certificate_and_public_enrollment;
CertificateTests.test_runtime_path_boundary.
Variable Index: ORIGIN/SUBNET are synthetic network identities. No real listener/GPU/phone is used.
"""

import json
import plistlib
import tempfile
from pathlib import Path
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import padding
from django.test import SimpleTestCase

from tools.lan_certificate import create_certificate
from tools.run_lan_mvp import ROOT, StartupError, owned_directory

from .assist import render_assist_page
from .lan import LanApplication, lan_address
from .lan_setup import LanSetupApplication
from .test_mobile import RecordingApplication, http_call, websocket_call

ORIGIN = "https://192.168.10.8:8443"
SUBNET = "192.168.10.0/24"


class LanTests(SimpleTestCase):
    """Functionality: Verify the LAN gate before canonical mobile forwarding.
    Logic: exercise accepted and hostile scopes. Constraints: no actual sockets or device trust.
    """

    def setUp(self):
        """Functionality: Isolate the canonical worker origin.
        Inputs: none. Outputs: scoped environment and recording app.
        Logic: freeze an explicit deployment. Constraints: synthetic request evidence only.
        """
        self.environment = patch.dict("os.environ", {"LOCAL_WEB_ORIGIN": "http://127.0.0.1:18766"})
        self.environment.start()
        self.target = RecordingApplication()
        self.app = LanApplication(self.target, origin=ORIGIN, subnet=SUBNET)

    def tearDown(self):
        """Functionality: Restore environment. Inputs: fixture. Outputs: original environment.
        Logic: stop the exact patch. Constraints: no service or OS configuration changes.
        """
        self.environment.stop()

    async def call(self, path="/mvp/maps/", **changes):
        """Functionality: Send one direct LAN request.
        Inputs: path and explicit overrides. Outputs: ASGI response.
        Logic: exact listener/peer/Host defaults. Constraints: no real network traffic.
        """
        defaults = {
            "scheme": "https",
            "server": ("192.168.10.8", 8443),
            "client": ("192.168.10.25", 51000),
            "headers": [(b"host", b"192.168.10.8:8443")],
        }
        return await http_call(self.app, path, **(defaults | changes))

    async def test_https_route_and_origin(self):
        """Functionality: Preserve the canonical GPU origin and public mount after LAN admission.
        Inputs: valid HTTPS and WSS scopes. Outputs: accepted canonical scope assertions.
        Logic: reuse mobile routing. Constraints: no image inference or physical permission claim.
        """
        start, _ = await self.call()
        self.assertEqual(start["status"], 302)
        self.assertEqual(self.target.scopes[-1]["path"], "/maps/")
        self.assertEqual(self.target.scopes[-1]["root_path"], "/mvp")
        self.assertIn((b"host", b"127.0.0.1:18766"), self.target.scopes[-1]["headers"])
        messages = await websocket_call(
            self.app,
            "/mvp/ws/detect/local/",
            scheme="wss",
            client=("192.168.10.25", 51000),
            server=("192.168.10.8", 8443),
            headers=[(b"host", b"192.168.10.8:8443"), (b"origin", ORIGIN.encode())],
        )
        self.assertEqual(messages["type"], "websocket.accept")

    async def test_network_and_header_rejections(self):
        """Functionality: Reject outside peers, wrong listeners and proxy/header bypasses.
        Inputs: forged scope variants. Outputs: 403 with no downstream invocation.
        Logic: validate original peer before canonicalization. Constraints: no inferred trust.
        """
        variants = [
            {"client": ("8.8.8.8", 1234)},
            {"client": ("192.168.11.2", 1234)},
            {"server": ("0.0.0.0", 8443)},
            {"scheme": "http"},
            {"headers": [(b"host", b"127.0.0.1:18766")]},
            {"headers": [(b"host", b"192.168.10.8:8443"), (b"x-prs-relay", b"1")]},
            {
                "headers": [
                    (b"host", b"192.168.10.8:8443"),
                    (b"origin", b"https://unrelated.example"),
                ]
            },
            {"headers": [(b"host", b"192.168.10.8:8443")] * 2},
        ]
        for changes in variants:
            with self.subTest(changes=changes):
                start, _ = await self.call(**changes)
                self.assertEqual(start["status"], 403)
        self.assertEqual(self.target.scopes, [])

    async def test_no_unlisted_paths_or_insecure_access(self):
        """Functionality: Preserve the original public path allowlist and no general file serving.
        Inputs: admin/MCP/private and double-mounted paths. Outputs: 404.
        Logic: outer mount plus inner exact allowlist. Constraints: no route aliases or traversal.
        """
        for path in (
            "/maps/",
            "/mvp/admin/",
            "/mvp/mcp/",
            "/mvp/server.key",
            "/mvp/mvp/maps/",
            "/mvp/assets/../server.key",
        ):
            start, _ = await self.call(path)
            self.assertEqual(start["status"], 404)

    def test_exact_private_configuration(self):
        """Functionality: Reject wildcard/public/overbroad or mismatched LAN settings.
        Inputs: explicit invalid address/CIDR pairs. Outputs: configuration errors.
        Logic: RFC1918 subnet restriction. Constraints: no interface or DNS discovery.
        """
        for address, network in (
            ("0.0.0.0", SUBNET),
            ("8.8.8.8", "8.8.8.0/24"),
            ("192.168.10.8", "192.168.0.0/16"),
            ("192.168.11.8", SUBNET),
            ("192.168.10.0", SUBNET),
        ):
            with self.assertRaises(ValueError):
                lan_address(address, network)

    def test_capture_profile_template_and_rejection(self):
        """Functionality: Bind explicit capture policy to both phone pages without HTML injection.
        Inputs: supported profiles and invalid strings. Outputs: exact profile or error.
        Logic: original default remains available. Constraints: no browser or model claim.
        """
        for view in ("product", "test"):
            for profile in (
                "standard",
                "bounded-2",
                "bounded-3",
                "responsive-2",
                "responsive-3",
                "conservative-2",
                "conservative-3",
            ):
                page, _ = render_assist_page(view=view, camera_profile=profile)
                self.assertIn(f'data-camera-profile="{profile}"'.encode(), page)
        with self.assertRaises(ValueError):
            render_assist_page(camera_profile='invalid" onload="alert(1)')


class CertificateTests(SimpleTestCase):
    """Functionality: Verify short-lived IP trust and public-only enrollment.
    Logic: real signatures and fixed ASGI resources. Constraints: does not install trust anywhere.
    """

    async def test_certificate_and_public_enrollment(self):
        """Functionality: Validate leaf identity, signatures, outputs and enrollment boundaries.
        Inputs: temporary runtime and synthetic LAN IP. Outputs: certificate/profile assertions.
        Logic: CA key never persists; only the public CA enters the iOS profile.
        Constraints: no actual iPhone installation, system trust, public server key or network.
        """
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "tls"
            report = create_certificate(directory, "192.168.10.8")
            ca = x509.load_pem_x509_certificate((directory / "demo-ca.crt").read_bytes())
            leaf = x509.load_pem_x509_certificate((directory / "server.crt").read_bytes())
            ca.public_key().verify(
                leaf.signature,
                leaf.tbs_certificate_bytes,
                padding.PKCS1v15(),
                leaf.signature_hash_algorithm,
            )
            self.assertEqual(len(list(directory.glob("*.key"))), 1)
            self.assertFalse(report["ca_private_key_saved"])
            self.assertLessEqual((leaf.not_valid_after_utc - leaf.not_valid_before_utc).days, 7)
            self.assertEqual(
                str(
                    leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName)
                    .value[0]
                    .value
                ),
                "192.168.10.8",
            )
            with self.assertRaises(FileExistsError):
                create_certificate(directory, "192.168.10.8")
            app = LanSetupApplication(
                address="192.168.10.8",
                subnet=SUBNET,
                port=8080,
                origin=ORIGIN,
                tls_directory=directory,
            )
            defaults = {
                "headers": [(b"host", b"192.168.10.8:8080")],
                "server": ("192.168.10.8", 8080),
                "client": ("192.168.10.20", 50000),
            }
            start, body = await http_call(app, "/demo.mobileconfig", **defaults)
            self.assertEqual(start["status"], 200)
            profile = plistlib.loads(body)
            self.assertEqual(profile["PayloadContent"][0]["PayloadType"], "com.apple.security.root")
            self.assertNotIn(b"PRIVATE KEY", body)
            for path in ("/server.key", "/tls/server.key", "/../server.key", "/mvp/maps/"):
                start, _ = await http_call(app, path, **defaults)
                self.assertEqual(start["status"], 404)
            start, body = await http_call(app, "/", **defaults)
            self.assertIn(ORIGIN.encode(), body)
            self.assertIn(report["ca_sha256"].encode(), body)
            self.assertEqual(json.loads((directory / "certificate.json").read_text()), report)

    def test_runtime_path_boundary(self):
        """Functionality: Bound runtime writes to the owned ignored directory.
        Inputs: parent escape and a valid child. Outputs: rejection/absolute path.
        Logic: resolve before containment. Constraints: no files or directories are created.
        """
        with self.assertRaises(StartupError):
            owned_directory(ROOT / ".venv/tmp/../outside")
        self.assertEqual(
            owned_directory(ROOT / ".venv/tmp/example"), (ROOT / ".venv/tmp/example").resolve()
        )
