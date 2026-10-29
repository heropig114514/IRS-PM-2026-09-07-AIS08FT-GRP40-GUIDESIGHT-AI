"""Responsibilities: Verify unchanged WebSocket echo bytes, capacity, origin admission and asset
boundaries.
Implementation: Run real ASGI handlers with ApplicationCommunicator; no browser or camera is
required.
Related Modules: core.echo, core.camera, SystemCode/frontend/i18n.js;
public TLS/WSS is validated separately.
Declaration Index:
- EchoTests — protocol and resource-release tests.
- EchoTests.open_socket — create an ASGI session with explicit origins.
- EchoTests.test_binary_is_unchanged — byte equality and disconnect cleanup.
- EchoTests.test_ping_id_is_preserved — application ping/pong ID equality.
- EchoTests.test_origin_and_host_rejected — reject invalid origins/hosts.
- EchoTests.test_invalid_control_rejected — reject invalid control messages.
- EchoTests.test_frame_limit — reject empty and oversized frames.
- EchoTests.test_capacity_and_release — reject the fifth connection and release admitted sessions.
- CameraPageTests — HTTP page and asset allowlist tests.
- CameraPageTests.test_page_and_script — page/modules and same-origin microphone policy.
- CameraPageTests.test_retired_routes_and_modes — four modes; reject retired HTTP routes.
- CameraPageTests.test_retired_websockets — reject retired WebSocket routes.
- CameraPageTests.test_unknown_asset_is_not_exposed — reject unlisted files.
Variable Index: None.
"""

import json
import re

from asgiref.testing import ApplicationCommunicator
from django.test import SimpleTestCase

from . import echo


class EchoTests(SimpleTestCase):
    """Functionality: Check ASGI echo contracts and process-local connection state.
    Logic: Run the actual handler in an in-memory ASGI queue and await handler cleanup after
    disconnect.
    Constraints: One worker only; no real network or media devices.
    """

    async def open_socket(self, origin="http://127.0.0.1:8766", host="127.0.0.1:8766"):
        """Functionality: Prepare a test session and receive its first connection event.
        Inputs: self, explicit origin and host; no admission-logic replacement.
        Outputs: Communicator and first response; callers must disconnect accepted sessions.
        Logic: Build a minimal WebSocket scope, then send connect.
        Constraints: The queue verifies protocol behavior, not successful real-network connectivity.
        """
        communicator = ApplicationCommunicator(
            echo.echo_socket,
            {
                "type": "websocket",
                "path": "/ws/echo/",
                "headers": [(b"origin", origin.encode()), (b"host", host.encode())],
            },
        )
        await communicator.send_input({"type": "websocket.connect"})
        return communicator, await communicator.receive_output()

    async def test_binary_is_unchanged(self):
        """Functionality: Verify exact binary length and byte preservation.
        Inputs: In-memory session and synthetic bytes; no send/receive handler mocks.
        Outputs: Data/type/connection-count assertions and a closed session.
        Logic: Echo, disconnect explicitly, then wait for final cleanup.
        Constraints: Synthetic data is not a real camera JPEG; decoding is not tested.
        """
        socket, accepted = await self.open_socket()
        self.assertEqual(accepted["type"], "websocket.accept")
        payload = bytes(range(256)) * 512
        await socket.send_input({"type": "websocket.receive", "bytes": payload})
        self.assertEqual(
            await socket.receive_output(), {"type": "websocket.send", "bytes": payload}
        )
        await socket.send_input({"type": "websocket.disconnect", "code": 1000})
        await socket.wait()
        self.assertEqual(echo.active_connections, 0)

    async def test_ping_id_is_preserved(self):
        """Functionality: Verify pong preserves the application request identifier.
        Inputs: Synthetic ping JSON and the real ASGI handler.
        Outputs: Matching pong followed by normal disconnect.
        Logic: Compare the complete response dictionary.
        Constraints: Does not measure real-network latency.
        """
        socket, _ = await self.open_socket()
        await socket.send_input(
            {"type": "websocket.receive", "text": '{"type":"ping","id":"test-1"}'}
        )
        response = await socket.receive_output()
        self.assertEqual(json.loads(response["text"]), {"type": "pong", "id": "test-1"})
        await socket.send_input({"type": "websocket.disconnect", "code": 1000})
        await socket.wait()

    async def test_origin_and_host_rejected(self):
        """Functionality: Verify both Origin and Host admission boundaries.
        Inputs: Invalid Origin/Host combinations without mocks.
        Outputs: Close code 1008 and zero admitted connections.
        Logic: Rejected sessions end directly without allocating connection capacity.
        Constraints: Same-origin validation is not user authentication.
        """
        for origin, host in [
            ("https://other.example", "127.0.0.1:8766"),
            ("http://127.0.0.1:8766", "other.example"),
        ]:
            socket, response = await self.open_socket(origin, host)
            self.assertEqual(response, {"type": "websocket.close", "code": 1008})
            await socket.wait()
        self.assertEqual(echo.active_connections, 0)

    async def test_invalid_control_rejected(self):
        """Functionality: Require explicit failure for invalid JSON/control messages.
        Inputs: Three invalid messages and the actual handler without mocks.
        Outputs: Each session closes with 1008 and is cleaned up.
        Logic: Cover JSON parsing, message type and ID type boundaries.
        Constraints: Invalid messages are not corrected.
        """
        for text in ["not-json", "[]", '{"type":"ping","id":3}']:
            socket, _ = await self.open_socket()
            await socket.send_input({"type": "websocket.receive", "text": text})
            self.assertEqual((await socket.receive_output())["code"], 1008)
            await socket.wait()

    async def test_frame_limit(self):
        """Functionality: Reject empty frames and frames exceeding 1 MiB.
        Inputs: Two synthetic byte sequences and the actual MAX_FRAME_BYTES limit.
        Outputs: Close code 1009 and released connections.
        Logic: Test the handler boundary directly without changing its threshold.
        Constraints: In-memory ASGI does not validate Uvicorn transport-size limits.
        """
        for payload in [b"", b"x" * (echo.MAX_FRAME_BYTES + 1)]:
            socket, _ = await self.open_socket()
            await socket.send_input({"type": "websocket.receive", "bytes": payload})
            self.assertEqual((await socket.receive_output())["code"], 1009)
            await socket.wait()
        self.assertEqual(echo.active_connections, 0)

    async def test_capacity_and_release(self):
        """Functionality: Verify the real four-connection limit and cleanup on rejection/disconnect.
        Inputs: Five in-memory sessions with MAX_CONNECTIONS unchanged.
        Outputs: The fifth closes with 1013; final occupancy is zero.
        Logic: Close all established sessions in finally to avoid leaking global state between
        assertions.
        Constraints: Single-worker in-memory capacity only; no network/systemd-limit coverage.
        """
        sockets = []
        try:
            for _ in range(echo.MAX_CONNECTIONS):
                socket, response = await self.open_socket()
                sockets.append(socket)
                self.assertEqual(response["type"], "websocket.accept")
            rejected, response = await self.open_socket()
            self.assertEqual(response["code"], 1013)
            await rejected.wait()
        finally:
            for socket in sockets:
                await socket.send_input({"type": "websocket.disconnect", "code": 1000})
                await socket.wait()
        self.assertEqual(echo.active_connections, 0)


class CameraPageTests(SimpleTestCase):
    """Functionality: Verify the diagnostic page and asset access scope.
    Logic: Use Django's test client and actual frontend files without mocking the filesystem.
    Constraints: Do not execute browser scripts or request camera permission.
    """

    def test_page_and_script(self):
        """Functionality: Verify the page and camera/voice/localization JavaScript responses.
        Inputs: self.client and repository frontend files.
        Outputs: Status, same-origin camera/microphone policy and MIME assertions; closed responses.
        Logic: Read response bodies; the views close file handles while reading.
        Constraints: Does not replace phone layout or permission validation.
        """
        for path, mime in [
            ("/live/", "text/html"),
            ("/assets/camera.js", "text/javascript"),
            ("/live/assets/vibration.js", "text/javascript"),
            ("/live/assets/speech-output.js", "text/javascript"),
            ("/assets/i18n.js", "text/javascript"),
            ("/assets/translations.js", "text/javascript"),
        ]:
            response = self.client.get(path)
            try:
                self.assertEqual(response.status_code, 200)
                self.assertIn(mime, response["Content-Type"])
                self.assertEqual(response["Permissions-Policy"], "camera=(self), microphone=(self)")
                self.assertTrue(response.content)
            finally:
                response.close()

    def test_retired_routes_and_modes(self):
        """Functionality: Verify redirect, existing modes, disabled local and retired routes.
        Inputs: actual URL/ASGI dispatch and frontend bytes; Outputs: redirect/options/404/1008.
        Logic: no GPU dependencies reached for an obsolete path.
        Constraints: actual reverse proxy routing is checked independently after publication.
        """
        self.assertRedirects(self.client.get("/"), "/live/", fetch_redirect_response=False)
        page = self.client.get("/live/").content.decode()
        modes = re.search(r'<select id="processing"[^>]*>(.*?)</select>', page, re.S).group(1)
        self.assertEqual(re.findall(r'value="([^"]+)"', modes), ["echo", "resident", "hf", "local"])
        self.assertRegex(modes, r'<option value="local" disabled>')
        for path in (
            "/vision/config/",
            "/vision/frame/",
            "/vision/hf/config/",
            "/vision/hf/frame/",
            "/assets/detection-client.js",
            "/assets/detection-sample.jpg",
        ):
            self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(
            self.client.get("/live/assets/camera.js").content,
            self.client.get("/assets/camera.js").content,
        )

    async def test_retired_websockets(self):
        """Functionality: Reject old processing WS paths before connection acceptance.
        Inputs: all retired paths and real ASGI application; Outputs: 1008 close per path.
        Logic: unknown dispatch must avoid GPU/HTTP dependencies.
        Constraints: in-memory ASGI, independently checked on deployed proxy later.
        """
        from config.asgi import application

        for name in ("cpu", "hf", "hf-boxes", "hf-live"):
            socket = ApplicationCommunicator(
                application,
                {
                    "type": "websocket",
                    "path": f"/ws/vision/{name}/",
                    "headers": [],
                },
            )
            await socket.send_input({"type": "websocket.connect"})
            self.assertEqual(
                await socket.receive_output(), {"type": "websocket.close", "code": 1008}
            )
            await socket.wait()

    def test_unknown_asset_is_not_exposed(self):
        """Functionality: Reject files outside the allowlist.
        Inputs: README.md and .env asset paths without mocks.
        Outputs: HTTP 404 for both; no filesystem writes.
        Logic: Exercise real view routes rather than testing only the mapping.
        Constraints: Covers only the current asset route.
        """
        for path in ["/assets/README.md", "/assets/.env"]:
            self.assertEqual(self.client.get(path).status_code, 404)
