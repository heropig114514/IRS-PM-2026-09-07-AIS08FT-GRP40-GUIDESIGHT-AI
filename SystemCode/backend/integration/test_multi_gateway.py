"""Responsibilities: Verify bounded multi-phone relay admission and source isolation.
Implementation: Real LocalGateway and protocol parsers with independent synthetic upstream streams.
Related Modules: integration.services/protocol; integration.test_local transport fixtures.
Declaration Index: MultiGatewayTests; MultiGatewayTests.test_capacity_configuration;
MultiGatewayTests.test_three_streams_keep_source_and_release_only_their_own_slot;
MultiGatewayTests.test_three_streams_keep_source_and_release_only_their_own_slot.open_provider;
MultiGatewayTests.test_three_streams_keep_source_and_release_only_their_own_slot.result.
Variable Index: providers retain independent fake transport counters;
no camera, media storage or GPU.
"""

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from unittest.mock import patch

from django.test import SimpleTestCase

from core.test_frames import jpeg
from realtime.protocol import unpack_result

from .errors import IntegrationError
from .protocol import pack_request
from .services import LocalGateway, gateway_capacity
from .test_local import PeerFixture, open_worker, socket_scope, terminal


class MultiGatewayTests(SimpleTestCase):
    """Functionality: Exercise gateway concurrency independently of GPU implementation.
    Logic: distinct provider identities and capture clocks. Constraints: no throughput claims.
    """

    def test_capacity_configuration(self):
        """Functionality: Preserve single-session defaults and reject unsafe admission values.
        Inputs: absent, canonical and malformed environment values. Outputs: one to three or error.
        Logic: explicit values only. Constraints: does not configure native worker state.
        """
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(gateway_capacity(), 1)
        for value in ("1", "2", "3"):
            with patch.dict("os.environ", {"PRS_INFERENCE_SESSIONS": value}):
                self.assertEqual(LocalGateway().capacity, int(value))
        for value in ("", "0", "4", "03", " 3", "3.0", "unbounded"):
            with patch.dict("os.environ", {"PRS_INFERENCE_SESSIONS": value}):
                with self.assertRaises(IntegrationError):
                    LocalGateway()

    async def test_three_streams_keep_source_and_release_only_their_own_slot(self):
        """Functionality: Admit three phones while preserving identity, sequence and capture times.
        Inputs: independent browser clocks and a fourth connection. Outputs: isolated results,
        explicit full capacity, and one released slot without stopping the two remaining streams.
        Logic: real packet parsing and stop accounting with three separate provider fixtures.
        Constraints: synthetic detections do not establish GPU latency or risk accuracy.
        """
        providers = []

        @asynccontextmanager
        async def open_provider(url, **kwargs):
            """Functionality: Supply one separately identified synthetic upstream per connection.
            Inputs: fixed gateway URL/options. Outputs: fresh provider. Logic: independent queues.
            Constraints: no network substitute is installed outside this explicit test seam.
            """
            provider = PeerFixture()
            provider.contract["session_id"] = uuid.uuid4().hex
            provider.outgoing.get_nowait()
            provider.outgoing.put_nowait(json.dumps(provider.contract))
            providers.append(provider)
            yield provider

        with patch.dict("os.environ", {"PRS_INFERENCE_SESSIONS": "3"}):
            gateway = LocalGateway(opener=open_provider)
        scope = socket_scope("/ws/detect/local/")
        opened = await asyncio.gather(*(open_worker(gateway, scope) for _ in range(3)))
        peers = [peer for peer, _ in opened]
        identities = [hello["session_id"] for _, hello in opened]
        self.assertEqual(len(set(identities)), 3)
        rejected, error = await open_worker(gateway, scope)
        self.assertEqual(error["code"], "local_busy")
        await rejected.receive_output()
        await rejected.wait()
        self.assertEqual(len(providers), 3)

        async def result(peer, index, timestamp, identity):
            """Functionality: Verify one source round trip without accepting a different phone.
            Inputs: peer/index/capture time/identity. Outputs: assertion success.
            Logic: decode the real protocol. Constraints: no replacement of capture clocks.
            """
            await peer.send_input({"type": "websocket.receive",
                                   "bytes": pack_request(index, timestamp, jpeg())})
            metadata, _ = unpack_result((await peer.receive_output())["bytes"], index)
            self.assertEqual(metadata["capture_time_s"], timestamp)
            self.assertEqual(metadata["session_id"], identity)

        await asyncio.gather(*(result(peer, 0, 100 + index * 1000, identities[index])
                               for index, peer in enumerate(peers)))
        await peers[0].send_input({"type": "websocket.receive", "text": '{"kind":"stop"}'})
        self.assertEqual((await terminal(peers[0]))["delivered"], 1)
        replacement, hello = await open_worker(gateway, scope)
        self.assertNotIn(hello["session_id"], identities)
        await asyncio.gather(result(peers[1], 1, 1101, identities[1]),
                             result(peers[2], 1, 2101, identities[2]),
                             result(replacement, 0, 3100, hello["session_id"]))
        remaining = [peers[1], peers[2], replacement]
        for peer in remaining:
            await peer.send_input({"type": "websocket.receive", "text": '{"kind":"stop"}'})
        await asyncio.gather(*(terminal(peer) for peer in remaining))
