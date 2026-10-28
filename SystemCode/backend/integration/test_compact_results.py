"""Verify explicit metadata transport preserves source, model and lifecycle contracts."""

import hashlib
import json
import struct

from asgiref.testing import ApplicationCommunicator
from django.test import SimpleTestCase

from core.test_frames import jpeg
from integration.protocol import pack_request
from integration.services import LocalGateway
from integration.test_local import PeerFixture, open_worker, socket_scope, terminal


class CompactResultTests(SimpleTestCase):
    async def test_metadata_preserves_source_and_worker_contract(self):
        provider = PeerFixture()
        scope = {**socket_scope("/ws/detect/local/"), "subprotocols": ["prs.metadata.v1"]}
        peer = ApplicationCommunicator(LocalGateway(opener=provider.context), scope)
        await peer.send_input({"type": "websocket.connect"})
        self.assertEqual(
            await peer.receive_output(),
            {"type": "websocket.accept", "subprotocol": "prs.metadata.v1"},
        )
        hello = json.loads((await peer.receive_output())["text"])
        self.assertEqual(hello["result_transport"], "metadata-v1")
        self.assertEqual(
            {k: v for k, v in hello.items() if k != "result_transport"}, provider.contract
        )
        original = pack_request(0, 3.25, jpeg())
        await peer.send_input({"type": "websocket.receive", "bytes": original})
        result = (await peer.receive_output())["bytes"]
        magic, length = struct.unpack("!4sI", result[:8])
        self.assertEqual(magic, b"PRM1")
        self.assertEqual(len(result), length + 8)
        metadata = json.loads(result[8:])
        self.assertEqual(metadata["source_jpeg_sha256"], hashlib.sha256(jpeg()).hexdigest())
        self.assertEqual(metadata["capture_time_s"], 3.25)
        self.assertEqual(metadata["session_id"], provider.contract["session_id"])
        self.assertEqual(metadata["result_transport"], "metadata-v1")
        self.assertEqual(provider.sent, [original])
        await peer.send_input({"type": "websocket.receive", "text": '{"kind":"stop"}'})
        self.assertEqual((await terminal(peer))["delivered"], 1)

    async def test_compact_still_rejects_worker_corruption(self):
        provider = PeerFixture(corrupt=True)
        peer, _ = await open_worker(
            LocalGateway(opener=provider.context),
            {**socket_scope("/ws/detect/local/"), "subprotocols": ["prs.metadata.v1"]},
        )
        await peer.send_input({"type": "websocket.receive", "bytes": pack_request(0, 1, jpeg())})
        self.assertEqual((await terminal(peer, "error"))["code"], "local_result_invalid")

    async def test_unknown_protocol_fails_without_provider_contact(self):
        provider = PeerFixture()
        peer = ApplicationCommunicator(
            LocalGateway(opener=provider.context),
            {**socket_scope("/ws/detect/local/"), "subprotocols": ["unknown"]},
        )
        await peer.send_input({"type": "websocket.connect"})
        self.assertEqual(await peer.receive_output(), {"type": "websocket.close", "code": 1008})
        await peer.wait()
        self.assertEqual(provider.sent, [])
