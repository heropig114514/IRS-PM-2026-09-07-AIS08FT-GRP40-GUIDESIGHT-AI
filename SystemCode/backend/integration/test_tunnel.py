"""Responsibilities: Verify public tunnel boundaries and separate product controls from diagnostics.
Implementation: In-memory HTTP/WebSocket requests and real templates with explicit feature settings.
Related Modules: integration.tunnel; integration.assist; integration.test_mobile.
Declaration Index: TunnelTests; TunnelTests.setUp;
TunnelTests.test_latency_panel_is_explicit_and_keeps_optimized_profile;
TunnelTests.test_page_mount_and_csp; TunnelTests.test_localhost_host_cannot_reach_privileged_branch;
TunnelTests.test_forbidden_routes_and_query;
TunnelTests.test_untrusted_peer_duplicate_host_and_origin;
TunnelTests.test_socket_requires_same_origin_and_allowed_route.
Variable Index: PUBLIC/HOST describe the synthetic public connector authority.
"""

from unittest.mock import patch

from django.test import SimpleTestCase

from .assist import render_assist_page
from .test_mobile import LOCAL, RecordingApplication, http_call, websocket_call
from .tunnel import TunnelApplication

PUBLIC = "https://phone-demo.trycloudflare.com"
HOST = b"phone-demo.trycloudflare.com"


class TunnelTests(SimpleTestCase):
    """Functionality: Guard tunnel isolation and the shared live-preview rendering contract.
    Logic: use synthetic authority requests and actual owned templates.
    Constraints: no public connector, browser, camera, GPU or speech service is started.
    """

    def setUp(self):
        """Functionality: Bind a recording application behind the tunnel boundary.
        Inputs: fixed local/public authorities. Outputs: isolated app and restored environment.
        Logic: register automatic patch cleanup. Constraints: no runtime configuration files.
        """
        env = patch.dict("os.environ", {"LOCAL_WEB_ORIGIN": LOCAL})
        env.start()
        self.addCleanup(env.stop)
        self.target = RecordingApplication()
        self.app = TunnelApplication(self.target, public_origin=PUBLIC)

    def test_latency_panel_is_explicit_and_keeps_optimized_profile(self):
        """Functionality: Keep live perception and speech recovery separate from test-only controls.
        Inputs: actual product/test templates with live display explicitly disabled/enabled.
        Outputs: shared preview controls, test-only telemetry and unchanged camera/voice settings.
        Logic: inspect both views and count controller IDs and scripts once.
        Constraints: markup checks do not prove stream playback, tracking or audible speech.
        """
        with patch.dict("os.environ", {"PRS_LATENCY_PANEL": ""}):
            for view in ("product", "test"):
                html, _ = render_assist_page(view=view)
                self.assertNotIn(b'id="live-latency-values"', html)
                self.assertNotIn(b'id="live-camera-preview"', html)
        with patch.dict("os.environ", {"PRS_LATENCY_PANEL": "1", "PRS_RISK_TTL_MS": "2000",
                                       "PRS_PREPARED_SPEECH": "1",
                                       "PRS_CAMERA_PROFILE": "low-latency-2"}):
            for view in ("product", "test"):
                with self.subTest(view=view):
                    html, _ = render_assist_page(view=view)
                    for name in ("live-camera-figure", "live-camera-preview", "live-camera-overlay",
                                 "live-speech-test", "live-speech-feedback", "assist-speech-start",
                                 "assist-results", "echo"):
                        self.assertEqual(html.count(f'id="{name}"'.encode()), 1, name)
                    for name in ("live-latency-panel", "live-latency-values", "live-latency-export",
                                 "live-motion-enabled", "live-motion-status", "live-box-counts"):
                        self.assertEqual(html.count(f'id="{name}"'.encode()), int(view == "test"),
                                         name)
                    self.assertEqual(html.count(b'src="../assets/integration/live-latency.js"'), 1)
                    self.assertEqual(html.count(b'href="../assets/integration/live-camera.css"'), 1)
                    self.assertIn(b'data-camera-profile="low-latency-2"', html)
                    self.assertIn(b'data-camera-display="live-overlay"', html)
                    age = "2000" if view == "product" else "1000"
                    prepared = "true" if view == "product" else "false"
                    self.assertIn(f'data-risk-max-age-ms="{age}"'.encode(), html)
                    self.assertIn(f'data-prepared-speech="{prepared}"'.encode(), html)
                    if view == "product":
                        self.assertIn(b'data-camera-result-transport="metadata-v1"', html)
                        self.assertNotIn("实时画面延迟测试".encode(), html)

    async def test_page_mount_and_csp(self):
        """Functionality: Preserve the public mount and connector-specific socket policy.
        Inputs: allowed public page request. Outputs: original path/root and restricted CSP.
        Logic: inspect delegated scope and response. Constraints: no network listener or redirects.
        """
        start, _ = await http_call(self.app, "/mvp/maps/", headers=[(b"host", HOST)])
        self.assertEqual(start["status"], 302)
        headers = dict(start["headers"])
        self.assertEqual(headers[b"location"], b"/mvp/maps/")
        self.assertIn(b"wss://phone-demo.trycloudflare.com", headers[b"content-security-policy"])
        self.assertEqual(self.target.scopes[0]["path"], "/maps/")
        self.assertEqual(self.target.scopes[0]["root_path"], "/mvp")

    async def test_localhost_host_cannot_reach_privileged_branch(self):
        """Functionality: Reject localhost and foreign authorities at the public boundary.
        Inputs: disallowed Host variants. Outputs: 403 without application delegation.
        Logic: probe the privileged MCP path. Constraints: synthetic requests only.
        """
        for host in (b"localhost:8766", b"127.0.0.1:8766", b"evil.example", HOST + b":443"):
            start, _ = await http_call(self.app, "/mvp/mcp/", headers=[(b"host", host)])
            self.assertEqual(start["status"], 403)
        self.assertFalse(self.target.scopes)

    async def test_forbidden_routes_and_query(self):
        """Functionality: Keep private and ambiguous paths outside the public route allowlist.
        Inputs: forbidden paths and a query-bearing page. Outputs: 404 without delegation.
        Logic: exercise exact-path checks. Constraints: no private file access or API execution.
        """
        for path in (
            "/maps/", "/mvp/admin/", "/mvp/mcp/", "/mvp/health/",
            "/mvp/models/a.pt", "/mvp/mvp/maps/",
        ):
            start, _ = await http_call(self.app, path, headers=[(b"host", HOST)])
            self.assertEqual(start["status"], 404)
        start, _ = await http_call(
            self.app, "/mvp/maps/", headers=[(b"host", HOST)], query_string=b"x=1",
        )
        self.assertEqual(start["status"], 404)
        self.assertFalse(self.target.scopes)

    async def test_untrusted_peer_duplicate_host_and_origin(self):
        """Functionality: Refuse untrusted peers and ambiguous or cross-origin requests.
        Inputs: foreign peer, repeated Host, foreign Origin and unsupported method.
        Outputs: 403 without delegation. Logic: vary one boundary at a time.
        Constraints: tests do not modify host networking or credentials.
        """
        cases = (
            {"client": ("192.168.10.9", 1234)},
            {"headers": [(b"host", HOST), (b"host", HOST)]},
            {"headers": [(b"host", HOST), (b"origin", b"https://evil.example")]},
            {"method": "POST"},
        )
        for case in cases:
            options = {"headers": [(b"host", HOST)], **case}
            start, _ = await http_call(self.app, "/mvp/maps/route/", **options)
            self.assertEqual(start["status"], 403)
        self.assertFalse(self.target.scopes)

    async def test_socket_requires_same_origin_and_allowed_route(self):
        """Functionality: Admit only a public camera socket with an exact same-origin authority.
        Inputs: allowed socket and invalid Origin/private-path variants.
        Outputs: one acceptance and policy closes. Logic: inspect the delegated scope count.
        Constraints: no actual inference, MCP execution or network connection.
        """
        headers = [(b"host", HOST), (b"origin", PUBLIC.encode())]
        first = await websocket_call(self.app, "/mvp/ws/detect/local/", headers=headers)
        self.assertEqual(first["type"], "websocket.accept")
        for path, request_headers in (
            ("/mvp/ws/detect/local/", [(b"host", HOST)]),
            ("/mvp/ws/detect/local/", [(b"host", HOST), (b"origin", b"https://evil.example")]),
            ("/mvp/ws/infer/", headers),
            ("/mvp/mcp/", headers),
        ):
            first = await websocket_call(self.app, path, headers=request_headers)
            self.assertEqual(first, {"type": "websocket.close", "code": 1008})
        self.assertEqual(len(self.target.scopes), 1)
