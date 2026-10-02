"""Responsibilities: Verify ZeroGPU streaming and relay failure/identity boundaries.
Implementation: Real codec/routing with synthetic HTTPS streams and mocked provider calls.
Related Modules: core.hf_services/hf_vision; zerogpu.protocol; external verification is separate.
Declaration Index: HFTests; HFTests.test_stream; HFTests.test_quota;
HFTests.test_boundaries; HFTests.test_identity; HFTests.test_relay;
HFTests.test_busy; HFTests.test_removed_routes.
Variable Index: None; fixtures and mocks are local to each test.
"""

import json
import time
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from zerogpu.protocol import encode_result

from . import hf_services, hf_vision
from .detector import DetectionError, decode_frame
from .test_vision import jpeg


class HFTests(SimpleTestCase):
    """Functionality: Exercise parsing, schema and selected-provider guards.
    Logic: inject transport boundaries, retain real validators; Constraints: no actual HF/GPU.
    """

    def test_stream(self):
        """Functionality: Check split SSE completion and heartbeat handling.
        Inputs: three synthetic chunks; Outputs: parsed list and closed connection assertion.
        Logic: real exchange parser with mocked TLS; Constraints: not a network verification.
        """
        response = Mock(status=200)
        response.getheader.return_value = "text/event-stream; charset=utf-8"
        response.read1.side_effect = [b'event: heartbeat\ndata: null\n\nevent: com',
                                     b'plete\ndata: [{"count":', b'0}]\n\n']
        with patch.object(hf_services.http.client, "HTTPSConnection") as factory:
            connection = factory.return_value
            connection.getresponse.return_value = response
            result = hf_services.exchange("GET", "/test", None, time.monotonic() + 1, True)
        self.assertEqual(result, [{"count": 0}])
        connection.close.assert_called_once()

    def test_quota(self):
        """Functionality: Preserve provider quota failure without success or fallback.
        Inputs: SSE quota event; Outputs: safe DetectionError and one request.
        Logic: real parser, synthetic HTTP response; Constraints: no real quota consumed.
        """
        response = Mock(status=200)
        response.getheader.return_value = "text/event-stream"
        response.read1.return_value = b'event: error\ndata: {"title":"ZeroGPU quota exceeded"}\n\n'
        with patch.object(hf_services.http.client, "HTTPSConnection") as factory:
            factory.return_value.getresponse.return_value = response
            with self.assertRaises(DetectionError) as caught:
                hf_services.exchange("GET", "/test", None, time.monotonic() + 1, True)
        self.assertEqual(caught.exception.code, "hf_quota_exceeded")
        factory.return_value.request.assert_called_once()

    def test_boundaries(self):
        """Functionality: Enforce bytes and absolute deadline, even for streamed reads.
        Inputs: excess bytes or expired deadline; Outputs: ValueError/TimeoutError.
        Logic: consume actual reader; Constraints: mocked response, no waiting or network.
        """
        response = Mock()
        response.read1.return_value = b"1234"
        with self.assertRaises(ValueError):
            list(hf_services.chunks(response, None, time.monotonic() + 1, 3))
        with self.assertRaises(TimeoutError):
            list(hf_services.chunks(response, None, time.monotonic() - 1, 3))

    def test_identity(self):
        """Functionality: Reject substituted precision, dimensions and nonfinite timing.
        Inputs: real synthetic JPEG result with altered metadata; Outputs: failures.
        Logic: real result serialization/validator; Constraints: no accuracy/GPU claim.
        """
        image = decode_frame(jpeg())
        result = encode_result(image, [], 1.0)
        self.assertEqual(hf_services.validate_result(result, image.size)[1], 0)
        for key, value in (("precision", "FP16"), ("width", True), ("inference_ms", float("nan"))):
            with self.subTest(key=key), self.assertRaises(ValueError):
                hf_services.validate_result({**result, key: value}, image.size)

    @override_settings(CAMERA_ALLOWED_ORIGINS=["http://testserver"])
    def test_relay(self):
        """Functionality: Check selected backend, origin and quota propagation at public routes.
        Inputs: JPEG, rejected origin and mocked quota; Outputs: 200/403/503 and slot release.
        Logic: actual Django relay; Constraints: upstream is mocked, not an end-to-end test.
        """
        with patch.object(hf_vision, "hf_request", return_value=(jpeg(), 0, 1.0)) as upstream:
            denied = self.client.post("/vision/hf/frame/", jpeg(), content_type="image/jpeg",
                                      HTTP_ORIGIN="http://evil.invalid")
            self.assertEqual(denied.status_code, 403)
            upstream.assert_not_called()
            result = self.client.post("/vision/hf/frame/", jpeg(), content_type="image/jpeg",
                                      HTTP_ORIGIN="http://testserver")
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result["X-Inference-Backend"], "PyTorch ZeroGPU")
        with patch.object(hf_vision, "hf_request", side_effect=DetectionError(
                "hf_quota_exceeded", "quota", 503)) as upstream:
            result = self.client.get("/vision/hf/config/")
            self.assertEqual(result.status_code, 503)
            self.assertEqual(json.loads(result.content)["code"], "hf_quota_exceeded")
            upstream.assert_called_once()
        self.assertTrue(hf_vision.slot.acquire(blocking=False))
        hf_vision.slot.release()

    @override_settings(CAMERA_ALLOWED_ORIGINS=["http://testserver"])
    def test_busy(self):
        """Functionality: Preserve non-queued HF status/frame rejection after adapter consolidation.
        Inputs: occupied slot and valid synthetic JPEG; Outputs: 429 and no provider calls.
        Logic: real routing under one held semaphore; Constraints: no threads, network or GPU.
        """
        hf_vision.slot.acquire()
        try:
            with patch.object(hf_vision, "hf_request") as upstream:
                self.assertEqual(self.client.get("/vision/hf/config/").status_code, 429)
                self.assertEqual(self.client.post(
                    "/vision/hf/frame/", jpeg(), content_type="image/jpeg",
                    HTTP_ORIGIN="http://testserver",
                ).status_code, 429)
                upstream.assert_not_called()
        finally:
            hf_vision.slot.release()

    def test_removed_routes(self):
        """Functionality: Confirm retired local-GPU routes cannot call an alternate provider.
        Inputs: former status/frame paths; Outputs: 404 and no HF calls.
        Logic: actual URL resolution; Constraints: no real server or external service involved.
        """
        with patch.object(hf_vision, "hf_request") as upstream:
            self.assertEqual(self.client.get("/vision/laptop/config/").status_code, 404)
            self.assertEqual(self.client.post(
                "/vision/laptop/frame/", jpeg(), content_type="image/jpeg",
            ).status_code, 404)
            upstream.assert_not_called()
