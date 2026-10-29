"""Check that a local connector cannot expose the localhost/MCP branch to public clients."""

from unittest.mock import patch

from django.test import SimpleTestCase

from .assist import render_assist_page
from .test_mobile import LOCAL, RecordingApplication, http_call, websocket_call
from .tunnel import TunnelApplication

PUBLIC = "https://phone-demo.trycloudflare.com"
HOST = b"phone-demo.trycloudflare.com"


class TunnelTests(SimpleTestCase):
    def setUp(self):
        env = patch.dict("os.environ", {"LOCAL_WEB_ORIGIN": LOCAL})
        env.start()
        self.addCleanup(env.stop)
        self.target = RecordingApplication()
        self.app = TunnelApplication(self.target, public_origin=PUBLIC)

    def test_latency_panel_is_explicit_and_keeps_optimized_profile(self):
        with patch.dict("os.environ", {"PRS_LATENCY_PANEL": ""}):
            html, _ = render_assist_page()
            self.assertNotIn(b'id="live-latency-values"', html)
        with patch.dict("os.environ", {"PRS_LATENCY_PANEL": "1"}):
            html, _ = render_assist_page()
            self.assertIn(b'id="live-latency-values"', html)
            self.assertIn(b'data-camera-profile="low-latency-2"', html)
            self.assertIn(b'data-camera-result-transport="metadata-v1"', html)
            self.assertIn(b'id="live-motion-enabled"', html)

    async def test_page_mount_and_csp(self):
        start, _ = await http_call(self.app, "/mvp/maps/", headers=[(b"host", HOST)])
        self.assertEqual(start["status"], 302)
        headers = dict(start["headers"])
        self.assertEqual(headers[b"location"], b"/mvp/maps/")
        self.assertIn(b"wss://phone-demo.trycloudflare.com", headers[b"content-security-policy"])
        self.assertEqual(self.target.scopes[0]["path"], "/maps/")
        self.assertEqual(self.target.scopes[0]["root_path"], "/mvp")

    async def test_localhost_host_cannot_reach_privileged_branch(self):
        for host in (b"localhost:8766", b"127.0.0.1:8766", b"evil.example", HOST + b":443"):
            start, _ = await http_call(self.app, "/mvp/mcp/", headers=[(b"host", host)])
            self.assertEqual(start["status"], 403)
        self.assertFalse(self.target.scopes)

    async def test_forbidden_routes_and_query(self):
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
