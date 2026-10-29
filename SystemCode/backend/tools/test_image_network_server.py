"""Exercise LAN test boundaries and binary echo without sockets, cameras or model imports."""

import unittest

from asgiref.testing import ApplicationCommunicator

from tools.image_network_server import MAX_BYTES, ImageNetworkApp


class ImageNetworkTests(unittest.IsolatedAsyncioTestCase):
    async def http(self, path, method="GET", host=b"127.0.0.1:8080", origin=None):
        headers = [(b"host", host)] + ([(b"origin", origin)] if origin else [])
        app = ImageNetworkApp("127.0.0.1", 8080)
        peer = ApplicationCommunicator(
            app,
            {
                "type": "http",
                "path": path,
                "method": method,
                "headers": headers,
                "query_string": b"",
            },
        )
        await peer.send_input({"type": "http.request"})
        start, body = await peer.receive_output(), await peer.receive_output()
        await peer.wait()
        return start, body

    async def test_fixed_assets_and_host_boundary(self):
        for path in ("/", "/network.js", "/network.css", "/echo-client.js"):
            start, body = await self.http(path)
            self.assertEqual(start["status"], 200)
            self.assertTrue(body["body"])
            self.assertEqual(dict(start["headers"])[b"cache-control"], b"no-store")
        for path in ("/.env", "/../tools/image_network_server.py", "/admin/", "/ws/infer/"):
            self.assertEqual((await self.http(path))[0]["status"], 404)
        self.assertEqual((await self.http("/", host=b"other:8080"))[0]["status"], 403)
        self.assertEqual((await self.http("/", origin=b"http://other"))[0]["status"], 403)
        self.assertEqual((await self.http("/", method="POST"))[0]["status"], 405)
        self.assertEqual((await self.http("/", method="HEAD"))[1]["body"], b"")

    async def socket(self, app, origin=b"http://127.0.0.1:8080"):
        peer = ApplicationCommunicator(
            app,
            {
                "type": "websocket",
                "path": "/ws/echo/",
                "headers": [(b"host", b"127.0.0.1:8080"), (b"origin", origin)],
                "query_string": b"",
                "subprotocols": [],
            },
        )
        await peer.send_input({"type": "websocket.connect"})
        return peer

    async def test_byte_exact_echo_ping_and_cleanup(self):
        app = ImageNetworkApp("127.0.0.1", 8080)
        peer = await self.socket(app)
        self.assertEqual((await peer.receive_output())["type"], "websocket.accept")
        payload = bytes(range(256)) * 100
        await peer.send_input({"type": "websocket.receive", "bytes": payload})
        self.assertEqual((await peer.receive_output())["bytes"], payload)
        await peer.send_input({"type": "websocket.receive", "text": '{"type":"ping","id":"1"}'})
        self.assertIn('"pong"', (await peer.receive_output())["text"])
        await peer.send_input({"type": "websocket.disconnect"})
        await peer.wait()
        self.assertEqual(app.active, 0)

    async def test_cross_origin_and_size_limit(self):
        app = ImageNetworkApp("127.0.0.1", 8080)
        bad = await self.socket(app, b"https://other.example")
        self.assertEqual((await bad.receive_output())["code"], 1008)
        await bad.wait()
        peer = await self.socket(app)
        await peer.receive_output()
        await peer.send_input({"type": "websocket.receive", "bytes": b"x" * (MAX_BYTES + 1)})
        self.assertEqual((await peer.receive_output())["code"], 1009)
        await peer.wait()
        self.assertEqual(app.active, 0)

    async def test_invalid_control_releases_capacity(self):
        for text in ("not json", "[]", '{"type":"ping","id":1}'):
            app = ImageNetworkApp("127.0.0.1", 8080)
            peer = await self.socket(app)
            await peer.receive_output()
            await peer.send_input({"type": "websocket.receive", "text": text})
            self.assertEqual((await peer.receive_output())["code"], 1008)
            await peer.wait()
            self.assertEqual(app.active, 0)
