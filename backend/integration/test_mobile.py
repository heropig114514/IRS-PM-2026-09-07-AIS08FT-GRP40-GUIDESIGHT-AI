"""Responsibilities: Verify the explicit mobile relay without listeners, SSH, TLS or CUDA.
Implementation: Real ASGI packets and assist/Django handlers exercise exact public admission,
the guest assistant socket, canonical forwarding, relative resources and localhost/MCP isolation.
Related Modules: integration.mobile/assist/asgi; core.maps/camera;
frontend/integration/assist-mvp.js.
Declaration Index: http_call; websocket_call; RecordingApplication; RecordingApplication.__init__;
RecordingApplication.__call__; MobileTests; MobileTests.setUp;
MobileTests.test_explicit_https_origin; MobileTests.test_original_local_scope_and_mcp;
MobileTests.test_public_headers_and_mount; MobileTests.test_public_origin_host_and_peer_rejections;
MobileTests.test_relay_marker_prevents_local_bypass; MobileTests.test_public_paths_and_methods;
MobileTests.test_public_websocket_admission; MobileTests.test_no_path_guessing_or_traversal;
MobileTests.test_redirect_csp_and_local_response_isolation; MobileTests.test_real_assist_template;
MobileTests.test_real_asset_allowlist; MobileTests.test_real_route_uses_original_origin_gate;
MobileTests.test_lifespan_delegates; MobileTests.test_video_endpoint_mount_resolution;
MobileTests.test_public_map_assets_are_forwarded_unchanged.
Variable Index: PUBLIC/LOCAL — explicit synthetic deployment origins; RecordingApplication.scopes
contains in-memory synthetic request evidence only, never browser media, real locations or secrets.
Constraints: Passing tests does not verify the public certificate, phone permissions, Nginx guest
include, reverse tunnel, real GPS/ORS, GPU model output, physical audio or mobile-network latency.
"""

import json
import re
from unittest.mock import AsyncMock, patch
from urllib.parse import urljoin

from asgiref.testing import ApplicationCommunicator
from django.core.asgi import get_asgi_application
from django.test import SimpleTestCase, override_settings

from .assist import AssistApplication
from .mobile import MOUNT, MobileApplication, mobile_origin
from .services import FRONTEND

PUBLIC = "https://47.239.50.129"
LOCAL = "http://127.0.0.1:8766"


async def http_call(app, path, method="GET", *, headers=None, body=b"", **changes):
    """Functionality: Exercise one HTTP request entirely in memory.
    Inputs: application/path/method and explicit scope/body boundaries. Outputs: response
    start/body.
    Logic: public relay defaults; Constraints: no socket, external provider, credentials or browser.
    """
    default_headers = [(b"host", b"47.239.50.129"), (b"x-prs-relay", b"1")]
    if method == "POST":
        default_headers += [(b"origin", PUBLIC.encode()), (b"content-type", b"application/json")]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "method": method,
        "scheme": "http",
        "server": ("127.0.0.1", 8766),
        "client": ("127.0.0.1", 51000),
        "headers": default_headers if headers is None else headers,
        "query_string": b"",
        **changes,
    }
    peer = ApplicationCommunicator(app, scope)
    await peer.send_input({"type": "http.request", "body": body, "more_body": False})
    start, response = await peer.receive_output(), await peer.receive_output()
    await peer.wait()
    return start, response["body"]


async def websocket_call(app, path, *, headers=None, **changes):
    """Functionality: Exercise a browser socket's admission without real transport.
    Inputs: explicit path/headers/scope changes. Outputs: first message and terminal message if
    accepted.
    Logic: original fixture acknowledges once; Constraints: no WebSocket server or GPU session.
    """
    scope = {
        "type": "websocket",
        "path": path,
        "root_path": "",
        "scheme": "ws",
        "client": ("127.0.0.1", 51000),
        "server": ("127.0.0.1", 8766),
        "query_string": b"",
        "headers": [
            (b"host", b"47.239.50.129"),
            (b"origin", PUBLIC.encode()),
            (b"x-prs-relay", b"1"),
        ]
        if headers is None
        else headers,
        **changes,
    }
    peer = ApplicationCommunicator(app, scope)
    await peer.send_input({"type": "websocket.connect"})
    first = await peer.receive_output()
    if first["type"] == "websocket.accept":
        await peer.receive_output()
    await peer.wait()
    return first


class RecordingApplication:
    """Functionality: Record delegated ASGI inputs with explicit closed fixture responses.
    Logic: responses contain only fixed headers; Constraints: no production provider substitution.
    """

    def __init__(self):
        """Functionality: Initialize isolated request records.
        Inputs: None. Outputs: empty fixture. Logic: no I/O. Constraints: test-only evidence.
        """
        self.scopes = []

    async def __call__(self, scope, receive, send):
        """Functionality: Observe delegation and return a closed ASGI response.
        Inputs: ASGI scope/channels. Outputs: fixed JSON or accepted/closed WS.
        Logic: retain original scope identity for mutation checks; Constraints: never runs a real
        service.
        """
        self.scopes.append(scope)
        await receive()
        if scope["type"] == "websocket":
            await send({"type": "websocket.accept"})
            return await send({"type": "websocket.close", "code": 1000})
        await send(
            {
                "type": "http.response.start",
                "status": 302,
                "headers": [
                    (b"location", b"/maps/"),
                    (b"content-type", b"application/json"),
                    (
                        b"content-security-policy",
                        b"default-src 'self'; script-src 'self' https://unpkg.com; "
                        b"style-src 'self' 'nonce-fixed' https://unpkg.com; "
                        b"media-src 'self' blob:; "
                        b"connect-src 'self' ws://127.0.0.1:8766; frame-ancestors 'none'",
                    ),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b"{}"})


@override_settings(
    CAMERA_ALLOWED_ORIGINS=(LOCAL, "http://localhost:8766"),
    ALLOWED_HOSTS=["127.0.0.1", "localhost", "[::1]"],
)
class MobileTests(SimpleTestCase):
    """Functionality: Guard public/local separation and unchanged original routing contracts.
    Logic: explicit process environment plus real ASGI/template/origin handlers.
    Constraints: no network/certificates/GPU/camera/ORS/navigation or phone-audio verification.
    """

    def setUp(self):
        """Functionality: Select explicit isolated deployment boundaries.
        Inputs: None. Outputs: automatically restored environment.
        Logic: supply only nonsecret fixture settings; Constraints: no .env or process/service
        mutation.
        """
        environment = patch.dict(
            "os.environ",
            {"LOCAL_MVP_MODE": "assist", "LOCAL_WEB_ORIGIN": LOCAL, "MOBILE_PUBLIC_ORIGIN": PUBLIC},
        )
        environment.start()
        self.addCleanup(environment.stop)

    def test_explicit_https_origin(self):
        """Functionality: Require exact explicit public HTTPS configuration.
        Inputs: environment and malformed/ambiguous origins. Outputs: canonical value or startup
        error.
        Logic: no automatic provider selection; Constraints: no DNS/TLS or public endpoint request.
        """
        self.assertEqual(mobile_origin(), PUBLIC)
        self.assertEqual(mobile_origin("https://relay.example:8443"), "https://relay.example:8443")
        for value in (
            "",
            "http://47.239.50.129",
            PUBLIC + "/",
            PUBLIC + "/mvp",
            PUBLIC + "?key=fixture",
            PUBLIC + "#fragment",
            "https://user:fixture@relay.example",
            "https://localhost",
            "https://127.0.0.1",
            "https://*.example",
            "https://Relay.example",
            "https://relay.example:443",
            "https://relay.example:0",
            "https://relay.example:",
            "https://relay.example:bad",
            "https://relay.example\n",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                mobile_origin(value)
        with patch.dict("os.environ", {"MOBILE_PUBLIC_ORIGIN": ""}), self.assertRaises(ValueError):
            MobileApplication(AsyncMock())

    async def test_original_local_scope_and_mcp(self):
        """Functionality: Preserve original raw loopback capabilities and response headers.
        Inputs: canonical local Host without relay marker. Outputs: exact original scope/response
        behavior.
        Logic: local MCP still shares the same inner application; Constraints: no real MCP lifespan
        needed.
        """
        inner = RecordingApplication()
        app = MobileApplication(inner)
        scope = {
            "type": "http",
            "path": "/mcp",
            "method": "POST",
            "client": ("127.0.0.1", 22),
            "headers": [(b"host", b"127.0.0.1:8766")],
            "root_path": "",
        }
        await app(scope, AsyncMock(return_value={"type": "http.request"}), AsyncMock())
        self.assertIs(inner.scopes[0], scope)
        self.assertEqual(scope["root_path"], "")
        start, _ = await http_call(app, "/mcp", headers=[(b"host", b"localhost:8766")])
        self.assertEqual(start["status"], 302)
        self.assertEqual(dict(start["headers"])[b"location"], b"/maps/")
        self.assertIn(b"ws://127.0.0.1:8766", dict(start["headers"])[b"content-security-policy"])

    async def test_public_headers_and_mount(self):
        """Functionality: Map only admitted public headers while preserving original scope/body
        identity.
        Inputs: trusted relay POST and spoofed forwarded headers. Outputs: canonical Host/Origin and
        fixed mount.
        Logic: downstream sees unchanged loopback contracts; Constraints: no worker/core setting
        widening.
        """
        inner = RecordingApplication()
        app = MobileApplication(inner)
        original = {
            "type": "http",
            "path": "/maps/route/",
            "method": "POST",
            "client": ("127.0.0.1", 22),
            "headers": [
                (b"host", b"47.239.50.129"),
                (b"origin", PUBLIC.encode()),
                (b"x-prs-relay", b"1"),
                (b"x-forwarded-host", b"evil.test"),
                (b"x-forwarded-proto", b"http"),
                (b"content-type", b"application/json"),
            ],
            "root_path": "",
            "scheme": "http",
        }
        await app(original, AsyncMock(return_value={"type": "http.request"}), AsyncMock())
        forwarded = inner.scopes[0]
        self.assertIsNot(forwarded, original)
        self.assertEqual(forwarded["path"], "/maps/route/")
        self.assertEqual(forwarded["root_path"], MOUNT)
        self.assertEqual(forwarded["scheme"], "https")
        self.assertEqual(
            dict(forwarded["headers"]),
            {
                b"host": b"127.0.0.1:8766",
                b"origin": LOCAL.encode(),
                b"content-type": b"application/json",
            },
        )
        self.assertEqual(original["root_path"], "")
        self.assertEqual(dict(original["headers"])[b"host"], b"47.239.50.129")

    async def test_public_origin_host_and_peer_rejections(self):
        """Functionality: Reject original hostile/missing/duplicate admission data before
        delegation.
        Inputs: explicit malformed origins, Host values and nonloopback peers. Outputs:403 and no
        inner call.
        Logic: never trust forwarded metadata to rescue a rejected peer; Constraints: no public body
        processing.
        """
        inner = RecordingApplication()
        app = MobileApplication(inner)
        for headers in (
            [],
            [(b"host", b"evil.test")],
            [(b"host", b"47.239.50.129")],
            [(b"host", b"47.239.50.129"), (b"origin", b"null")],
            [(b"host", b"47.239.50.129"), (b"origin", b"https://evil.test")],
            [(b"host", b"47.239.50.129"), (b"host", b"47.239.50.129")],
            [
                (b"host", b"47.239.50.129"),
                (b"origin", PUBLIC.encode()),
                (b"origin", PUBLIC.encode()),
            ],
        ):
            self.assertEqual(
                (await http_call(app, "/maps/route/", "POST", headers=headers))[0]["status"], 403
            )
        for client in (("192.0.2.1", 22), None):
            self.assertEqual((await http_call(app, "/maps/", client=client))[0]["status"], 403)
        self.assertEqual(inner.scopes, [])

    async def test_relay_marker_prevents_local_bypass(self):
        """Functionality: Make any relay marker incompatible with the privileged local Host branch.
        Inputs: canonical local Host plus relay marker, duplicates and malformed marker values.
        Outputs:403 before local MCP or other original handler. Logic: no proxy misconfiguration
        bypass.
        Constraints: callers must preserve explicit public Host at the external Nginx boundary.
        """
        inner = RecordingApplication()
        app = MobileApplication(inner)
        for markers in ([b"1"], [b"0"], [b""], [b"1", b"1"]):
            headers = [(b"host", b"127.0.0.1:8766")] + [
                (b"x-prs-relay", value) for value in markers
            ]
            self.assertEqual(
                (await http_call(app, "/mcp", "POST", headers=headers))[0]["status"], 403
            )
        self.assertEqual(
            (
                await http_call(
                    app,
                    "/mcp",
                    "POST",
                    headers=[(b"host", b"127.0.0.1:8766"), (b"origin", PUBLIC.encode())],
                )
            )[0]["status"],
            403,
        )
        self.assertEqual(inner.scopes, [])

    async def test_public_paths_and_methods(self):
        """Functionality: Keep public operations on the exact combined-entry capability set.
        Inputs: allowed methods plus MCP/admin/API/HF/standalone paths. Outputs: delegation/404/405.
        Logic: no prefix-based business-route exposure; Constraints: fixture response is not real
        business validation.
        """
        inner = RecordingApplication()
        app = MobileApplication(inner)
        for path, method in (
            ("/", "GET"),
            ("/maps/", "HEAD"),
            ("/maps/test", "GET"),
            ("/maps/test", "HEAD"),
            ("/maps/config/", "GET"),
            ("/maps/route/", "POST"),
            ("/integration/status/", "GET"),
            ("/assets/camera.js", "GET"),
            ("/assets/integration/assist-mvp.js", "HEAD"),
        ):
            self.assertEqual((await http_call(app, path, method))[0]["status"], 302)
        for path in (
            "/mcp",
            "/mcp/",
            "/admin/",
            "/api/health/",
            "/live/",
            "/integration/video/",
            "/scene/describe/",
            "/maps/unknown/",
            "/maps/test/",
            "/ws/detect/hf/",
        ):
            self.assertEqual((await http_call(app, path))[0]["status"], 404)
        for path, method in (
            ("/maps/", "POST"),
            ("/maps/test", "POST"),
            ("/maps/route/", "GET"),
            ("/assets/camera.js", "POST"),
            ("/integration/status/", "HEAD"),
        ):
            self.assertEqual((await http_call(app, path, method))[0]["status"], 405)

    async def test_public_websocket_admission(self):
        """Functionality: Admit exact same-origin control, assistant, and local inference sockets.
        Inputs: three explicitly allowed sockets and hostile/absent-origin/foreign-path variants.
        Outputs: preserved acceptance/normalized headers or 1008. Logic: original hub remains a
        singleton.
        Constraints: no socket listener, native frame or extra control implementation.
        """
        inner = RecordingApplication()
        app = MobileApplication(inner)
        for path in ("/ws/control/", "/ws/agent/", "/ws/detect/local/"):
            self.assertEqual((await websocket_call(app, path))["type"], "websocket.accept")
            self.assertEqual(inner.scopes[-1]["root_path"], MOUNT)
            self.assertEqual(inner.scopes[-1]["scheme"], "wss")
            self.assertEqual(dict(inner.scopes[-1]["headers"])[b"origin"], LOCAL.encode())
        for path in (
            "/ws/echo/",
            "/ws/agent",
            "/ws/detect/hf/",
            "/ws/infer/",
            "/mcp",
            "/mvp/ws/control/",
        ):
            self.assertEqual((await websocket_call(app, path))["code"], 1008)
        for headers in (
            [(b"host", b"47.239.50.129")],
            [(b"host", b"47.239.50.129"), (b"origin", b"https://evil.test")],
            [(b"host", b"localhost:8766"), (b"origin", LOCAL.encode()), (b"x-prs-relay", b"1")],
        ):
            self.assertEqual(
                (await websocket_call(app, "/ws/control/", headers=headers))["code"], 1008
            )
        self.assertEqual(
            (await websocket_call(app, "/ws/control/", query_string=b"token=fixture"))["code"], 1008
        )
        self.assertEqual(len(inner.scopes), 3)

    async def test_no_path_guessing_or_traversal(self):
        """Functionality: Enforce the one stripped-path contract without traversal or query aliases.
        Inputs: prefixed/doubled/empty-segment/backslash/parent paths. Outputs:404 and zero
        delegation.
        Logic: no automatic second stripping; Constraints: no file-system path repair or lookup.
        """
        inner = RecordingApplication()
        app = MobileApplication(inner)
        for path in (
            "/mvp/maps/",
            "/mvp/mvp/maps/",
            "//maps/",
            "/assets/",
            "/assets//camera.js",
            "/assets/../camera.js",
            "/assets/./camera.js",
            "/assets/integration/../assist.py",
            "/assets/integration\\assist.py",
        ):
            self.assertEqual((await http_call(app, path))[0]["status"], 404)
        self.assertEqual(
            (await http_call(app, "/maps/", query_string=b"x=fixture"))[0]["status"], 404
        )
        self.assertEqual(inner.scopes, [])

    async def test_redirect_csp_and_local_response_isolation(self):
        """Functionality: Preserve /mvp redirects and public WSS without losing CSP restrictions.
        Inputs: exact downstream CSP/redirect and local/public requests. Outputs: public-only safe
        header changes.
        Logic: nonce/CDN/blob/ancestors directives survive byte-for-byte; Constraints: no body
        mutation.
        """
        app = MobileApplication(RecordingApplication())
        start, body = await http_call(app, "/")
        headers = dict(start["headers"])
        self.assertEqual(headers[b"location"], b"/mvp/maps/")
        self.assertEqual(body, b"{}")
        csp = headers[b"content-security-policy"]
        self.assertIn(b"connect-src 'self' wss://47.239.50.129", csp)
        self.assertNotIn(b"ws://127.0.0.1", csp)
        self.assertEqual(csp.count(b"connect-src"), 1)
        for directive in (
            b"script-src 'self' https://unpkg.com",
            b"style-src 'self' 'nonce-fixed' https://unpkg.com",
            b"media-src 'self' blob:",
            b"frame-ancestors 'none'",
        ):
            self.assertIn(directive, csp)
        local, _ = await http_call(app, "/", headers=[(b"host", b"127.0.0.1:8766")])
        self.assertEqual(dict(local["headers"])[b"location"], b"/maps/")
        self.assertIn(b"ws://127.0.0.1", dict(local["headers"])[b"content-security-policy"])

    async def test_real_assist_template(self):
        """Functionality: Verify actual combined HTML uses one mount-relative module graph.
        Inputs: original assist renderer through the public wrapper. Outputs: nonce, product-only
        same-origin microphone policy, SRI and URL assertions.
        Logic: resolve real script/link paths as the phone does; Constraints: no CDN/browser/TLS
        execution.
        """
        app = MobileApplication(AssistApplication(get_asgi_application()))
        for path, view in (("/maps/", "product"), ("/maps/test", "test")):
            start, body = await http_call(app, path)
            self.assertEqual(start["status"], 200)
            self.assertIn(b'src="../assets/integration/assist-mvp.js"', body)
            self.assertIn(b'href="../assets/integration/assist.css"', body)
            self.assertIn(b'data-api-base="../"', body)
            self.assertIn(f'data-assist-view="{view}"'.encode(), body)
            headers = dict(start["headers"])
            self.assertRegex(headers[b"content-security-policy"], rb"'nonce-[A-Za-z0-9_-]+'")
            if view == "test":
                nonce = re.search(rb'<style nonce="([^"]+)"', body).group(1)
                self.assertIn(b"'nonce-" + nonce + b"'", headers[b"content-security-policy"])
            self.assertEqual(body.count(b'integrity="sha256-'), 2)
            microphone_policy = b"microphone=(self)" if view == "product" else b"microphone=()"
            self.assertEqual(headers[b"permissions-policy"],
                             b"geolocation=(self), camera=(self), " + microphone_policy)
            scripts = re.findall(rb'<script[^>]+src="([^"]+)"', body)
            for script in scripts:
                if not script.startswith(b"https://"):
                    self.assertTrue(
                        urljoin(PUBLIC + "/mvp" + path, script.decode()).startswith(
                            PUBLIC + "/mvp/assets/"
                        )
                    )
            self.assertEqual(urljoin(PUBLIC + "/mvp" + path, "./config/"),
                             PUBLIC + "/mvp/maps/config/")
            head, empty = await http_call(app, path, "HEAD")
            self.assertEqual(head["status"], 200)
            self.assertEqual(empty, b"")

    async def test_real_asset_allowlist(self):
        """Functionality: Retain actual assist/core asset allowlists behind the mobile route
        category.
        Inputs: real canonical modules and unknown nested/file/traversal names. Outputs:200 or404.
        Logic: outer admission never substitutes a filename; Constraints: no arbitrary filesystem
        exposure.
        """
        app = MobileApplication(AssistApplication(get_asgi_application()))
        for path in (
            "/assets/camera.js",
            "/assets/speech-output.js",
            "/assets/integration/assist-mvp.js",
            "/assets/integration/assist.css",
            "/assets/integration/assist-guidance.js",
            "/assets/integration/voice-preferences.js",
            "/assets/integration/product-ui.js",
            "/assets/integration/assist-product.css",
        ):
            self.assertEqual((await http_call(app, path))[0]["status"], 200)
        for path in (
            "/assets/unknown.js",
            "/assets/integration/unknown.js",
            "/assets/integration/raw.html",
            "/assets/integration/../../.env",
            "/assets/%2e%2e/.env",
        ):
            self.assertEqual((await http_call(app, path))[0]["status"], 404)

    async def test_real_route_uses_original_origin_gate(self):
        """Functionality: Exercise original Django Host/Origin/input validation through header
        adaptation.
        Inputs: explicit synthetic coordinates and a mocked upstream route call. Outputs: original
        response contract.
        Logic: valid public POST maps to existing local allowed headers; Constraints: no actual
        ORS/key or real location.
        """
        app = MobileApplication(AssistApplication(get_asgi_application()))
        body = json.dumps({"start": [10, 10], "end": [10.001, 10.001]}).encode()
        with patch(
            "core.maps.walking_route", return_value={"fixture": "original_route_called"}
        ) as route:
            start, result = await http_call(app, "/maps/route/", "POST", body=body)
        self.assertEqual(start["status"], 200)
        self.assertEqual(json.loads(result)["fixture"], "original_route_called")
        route.assert_called_once()

    async def test_public_map_assets_are_forwarded_unchanged(self):
        """Functionality: Forward public map assets through the original guest application.
        Inputs: actual map sources and public/local GET operations. Outputs: unchanged asset bytes.
        Logic: compare whole responses and source files for public and localhost scopes.
        Constraints: no user credential, source mutation, navigation calculation or browser.
        """
        app = MobileApplication(AssistApplication(get_asgi_application()))
        for name in ("maps-page.js", "maps-client.js"):
            original = (FRONTEND / name).read_bytes()
            start, body = await http_call(app, "/assets/" + name)
            self.assertEqual(start["status"], 200)
            self.assertEqual(body, original)
            headers = {key.lower(): value for key, value in start["headers"]}
            self.assertEqual(headers[b"content-type"], b"text/javascript")
            self.assertEqual(int(headers[b"content-length"]), len(original))
            local, local_body = await http_call(app, "/assets/" + name,
                                               headers=[(b"host", b"127.0.0.1:8766")])
            self.assertEqual(local["status"], 200)
            self.assertEqual(local_body, original)
            local_headers = {key.lower(): value for key, value in local["headers"]}
            self.assertEqual(headers[b"content-type"], local_headers[b"content-type"])
            self.assertEqual((FRONTEND / name).read_bytes(), original)
        for name in ("navigation.js", "navigation-ui.js", "speech-output.js", "camera.js"):
            start, body = await http_call(app, "/assets/" + name)
            self.assertEqual(start["status"], 200)
            self.assertEqual(body, (FRONTEND / name).read_bytes())

    async def test_lifespan_delegates(self):
        """Functionality: Preserve original MCP lifespan ownership with no HTTP gate or extra
        worker.
        Inputs: exact lifespan scope/channels. Outputs: one unchanged delegate call.
        Logic: wrapper adds no tasks/listeners; Constraints: fixture does not initialize real MCP
        state.
        """
        inner = AsyncMock()
        app = MobileApplication(inner)
        scope, receive, send = {"type": "lifespan"}, AsyncMock(), AsyncMock()
        await app(scope, receive, send)
        inner.assert_awaited_once_with(scope, receive, send)

    def test_video_endpoint_mount_resolution(self):
        """Functionality: Verify the replay's existing ../../ socket rule under explicit integration
        base.
        Inputs: root and mounted browser document URLs. Outputs: original or mounted WS path, same
        origin.
        Logic: apply standard URL resolution twice; Constraints: no frontend implementation mirror
        or media claim.
        """
        for page, expected in (
            (PUBLIC + "/maps/", PUBLIC + "/ws/detect/local/"),
            (PUBLIC + "/mvp/maps/", PUBLIC + "/mvp/ws/detect/local/"),
        ):
            replay_base = urljoin(page, "../integration/video/")
            self.assertEqual(urljoin(replay_base, "../../ws/detect/local/"), expected)
