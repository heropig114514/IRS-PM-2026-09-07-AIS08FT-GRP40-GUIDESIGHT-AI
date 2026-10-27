"""Responsibilities: Explicit synthetic inference and real binary-envelope boundary tests.
Implementation: FakeDetector hashes actual fixture JPEGs without any model; protocol tests
mutate bytes.
Related Modules: test_hf_live/test_zerogpu_live; realtime.protocol; test_frames.jpeg.
Declaration Index: FakeDetector; FakeDetector.__init__; FakeDetector.predict; ProtocolTests;
ProtocolTests.test_integrity_and_sequence.
Variable Index: None. These fixtures do not verify GPU computation or external services.
"""

import hashlib
import threading

from django.test import SimpleTestCase

from realtime.protocol import HEADER, pack_request, pack_result, unpack_request, unpack_result

from .test_frames import jpeg


class FakeDetector:
    """Functionality: Supply synthetic detector output and retain worker thread ownership.
    Logic: checksum the real JPEG without inference; Constraints: never interpreted as detection.
    """

    def __init__(self, weights_path, manifest_path):
        """Functionality: Record initialization thread. Inputs: unused test-only paths.
        Outputs: thread list; Logic: no model load; Constraints: no actual CUDA/resources.
        """
        self.threads = [threading.get_ident()]

    def predict(self, raw):
        """Functionality: Provide a bounded response with deliberately non-authoritative sequence.
        Inputs: real JPEG; Outputs: test metadata and same JPEG.
        Logic: record worker thread/hash; Constraints: bypasses all real model computation.
        """
        self.threads.append(threading.get_ident())
        return {"index": 999, "jpeg_sha256": hashlib.sha256(raw).hexdigest()}, raw


class ProtocolTests(SimpleTestCase):
    """Functionality: Verify framing/hash/order boundaries.
    Logic: real packets with explicit corruption; Constraints: no detector/network inference.
    """

    def test_integrity_and_sequence(self):
        """Functionality: Reject reordered requests and corrupted/oversized response framing.
        Inputs: real fixture bytes with mutated index/body/header; Outputs: ValueError boundaries.
        Logic: use actual codec envelopes/hash checks; Constraints: no network or GPU involvement.
        """
        raw = jpeg()
        self.assertEqual(unpack_request(pack_request(0, raw), 0), raw)
        with self.assertRaises(ValueError):
            unpack_request(pack_request(1, raw), 0)
        with self.assertRaises(ValueError):
            unpack_request("text instead of binary", 0)
        response = pack_result({"index": 0, "jpeg_sha256": hashlib.sha256(raw).hexdigest()}, raw)
        self.assertEqual(unpack_result(response, 0)[1], raw)
        with self.assertRaises(ValueError):
            unpack_result(response[:-1] + b"\x00", 0)
        with self.assertRaises(ValueError):
            unpack_result(HEADER.pack(b"PRS1", 0xFFFFFFFF) + raw, 0)
