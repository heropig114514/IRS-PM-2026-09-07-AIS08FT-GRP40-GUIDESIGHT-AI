"""Responsibilities: Verify authenticated worker, relay boundaries and explicit failure semantics.
Implementation: Real Django/Pillow with mocked GPU/HTTP; no actual GPU/SSH/external network.
Related Modules: core.laptop_worker/laptop_vision/laptop_services; real end-to-end separately.
Declaration Index: RelayTests/RelayTests.test_origin/RelayTests.test_success/
RelayTests.test_disconnected/RelayTests.test_busy;
WorkerTests/WorkerTests.test_auth/WorkerTests.test_success/WorkerTests.test_busy/
WorkerTests.test_size;
TransportTests/TransportTests.test_no_token/TransportTests.test_disconnect/
TransportTests.test_invalid_metadata/TransportTests.test_invalid_jpeg/TransportTests.test_valid.
Variable Index: TOKEN — synthetic test-only credential; HEADERS — private reply fixture.
"""

from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from . import laptop_services, laptop_vision, laptop_worker
from .detector import DetectionError
from .test_vision import jpeg

TOKEN = "test-only-token-with-at-least-32-characters"
HEADERS = {
    "Content-Type": "image/jpeg",
    "X-Model": "yolo26n",
    "X-Inference-Backend": "TensorRT",
    "X-Inference-Precision": "FP32",
    "X-Detection-Count": "1",
    "X-Inference-Ms": "12.3",
}


@override_settings(CAMERA_ALLOWED_ORIGINS=["http://testserver"])
class RelayTests(SimpleTestCase):
    """Functionality: Exercise public laptop relay. Logic: real guards, mocked upstream result.
    Constraints: no actual server-to-laptop transmission or TensorRT accuracy verification.
    """

    def test_origin(self):
        """Functionality: Check origin/JPEG rejection. Inputs: invalid origin and malformed JPEG.
        Outputs: 403/400; Logic: request guards precede relay; Constraints: no upstream call.
        """
        with patch.object(laptop_vision, "laptop_request") as upstream:
            for origin, body, status in (
                ("http://evil.test", jpeg(), 403),
                ("http://testserver", b"bad", 400),
            ):
                response = self.client.post(
                    "/vision/laptop/frame/", body, content_type="image/jpeg", HTTP_ORIGIN=origin
                )
                self.assertEqual(response.status_code, status)
            upstream.assert_not_called()

    def test_success(self):
        """Functionality: Check relay preserves contract. Inputs: annotated JPEG/count/timing.
        Outputs: correct JPEG/metadata; Logic: real routing, mocked upstream.
        Constraints: JPEG fixture is synthetic; no real GPU invoked.
        """
        with patch.object(laptop_vision, "laptop_request", return_value=(jpeg(), 1, 12.3)):
            response = self.client.post(
                "/vision/laptop/frame/",
                jpeg(),
                content_type="image/jpeg",
                HTTP_ORIGIN="http://testserver",
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, jpeg())
        self.assertEqual(response["X-Inference-Backend"], "TensorRT")
        self.assertEqual(response["X-Inference-Precision"], "FP32")

    def test_disconnected(self):
        """Functionality: Check disconnect is explicit. Inputs: upstream DetectionError.
        Outputs: 503 and released slot; Logic: no alternative backend call.
        Constraints: mocked network disconnect, not actual tunnel interruption.
        """
        with patch.object(
            laptop_vision,
            "laptop_request",
            side_effect=DetectionError("laptop_disconnected", "Disconnected", 503),
        ) as call:
            response = self.client.get("/vision/laptop/config/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(call.call_count, 1)
        self.assertTrue(laptop_vision.slot.acquire(blocking=False))
        laptop_vision.slot.release()

    def test_busy(self):
        """Functionality: Check non-queued relay. Inputs: occupied relay semaphore.
        Outputs: 429; Logic: release in test finally; Constraints: no threads or actual transport.
        """
        laptop_vision.slot.acquire()
        try:
            self.assertEqual(self.client.get("/vision/laptop/config/").status_code, 429)
        finally:
            laptop_vision.slot.release()


@override_settings(ROOT_URLCONF="config.worker_urls")
class WorkerTests(SimpleTestCase):
    """Functionality: Exercise private GPU worker. Logic: real auth and slot, mocked GPU only.
    Constraints: this does not validate actual CUDA memory operations/engine compatibility.
    """

    def test_auth(self):
        """Functionality: Check auth precedes GPU loading. Inputs: missing/wrong bearer credentials.
        Outputs: 401; Logic: real authentication; Constraints: never invokes actual model.
        """
        with (
            patch.dict("os.environ", LAPTOP_INFERENCE_TOKEN=TOKEN),
            patch.object(laptop_worker, "get_gpu_detector") as load,
        ):
            self.assertEqual(self.client.get("/config/").status_code, 401)
            self.assertEqual(
                self.client.get("/config/", HTTP_AUTHORIZATION="Bearer bad").status_code, 401
            )
            load.assert_not_called()

    def test_success(self):
        """Functionality: Check private contract. Inputs: synthetic bearer/image/model.
        Outputs: JPEG and backend metadata; Logic: mock only prediction.
        Constraints: does not prove remote caller authentication or real GPU speed.
        """
        model = Mock()
        model.predict.return_value = (jpeg(), [{}], 12.3)
        with (
            patch.dict("os.environ", LAPTOP_INFERENCE_TOKEN=TOKEN),
            patch.object(laptop_worker, "get_gpu_detector", return_value=model),
        ):
            config = self.client.get("/config/", HTTP_AUTHORIZATION="Bearer " + TOKEN)
            response = self.client.post(
                "/frame/", jpeg(), content_type="image/jpeg", HTTP_AUTHORIZATION="Bearer " + TOKEN
            )
        self.assertEqual(config.json()["backend"], "TensorRT")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Detection-Count"], "1")

    def test_busy(self):
        """Functionality: Reject simultaneous buffer reuse. Inputs: occupied GPU slot and bearer.
        Outputs: 429; Logic: real semaphore; Constraints: fixture releases slot unconditionally.
        """
        laptop_worker.slot.acquire()
        try:
            with patch.dict("os.environ", LAPTOP_INFERENCE_TOKEN=TOKEN):
                response = self.client.get("/config/", HTTP_AUTHORIZATION="Bearer " + TOKEN)
            self.assertEqual(response.status_code, 429)
        finally:
            laptop_worker.slot.release()

    @override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=1048576)
    def test_size(self):
        """Functionality: Check Django body limit remains a safe 413. Inputs: oversized bearer body.
        Outputs: no GPU invocation; Logic: actual RequestDataTooBig path.
        Constraints: local synthetic bytes, no network or media storage.
        """
        with (
            patch.dict("os.environ", LAPTOP_INFERENCE_TOKEN=TOKEN),
            patch.object(laptop_worker, "get_gpu_detector") as load,
        ):
            response = self.client.post(
                "/frame/",
                b"x" * 1048577,
                content_type="image/jpeg",
                HTTP_AUTHORIZATION="Bearer " + TOKEN,
            )
        self.assertEqual(response.status_code, 413)
        load.assert_not_called()


class TransportTests(SimpleTestCase):
    """Functionality: Verify private HTTP transport validation. Logic: injected HTTPConnection.
    Constraints: mocks cannot prove actual SSH encryption, connection lifetime or upstream speed.
    """

    def test_no_token(self):
        """Functionality: Reject unconfigured transport. Inputs: empty explicit token.
        Outputs: 503; Logic: no socket creation; Constraints: no implicit local CPU use.
        """
        with patch.dict("os.environ", LAPTOP_INFERENCE_TOKEN=""):
            with self.assertRaises(DetectionError) as error:
                laptop_services.laptop_request()
        self.assertEqual(error.exception.code, "laptop_not_configured")

    def test_disconnect(self):
        """Functionality: Check socket failure cleanup. Inputs: mock connection raising OSError.
        Outputs: 503, one request, closed connection; Logic: no retry.
        Constraints: synthetic failure, no real networking.
        """
        connection = Mock()
        connection.request.side_effect = OSError("private diagnostic")
        with (
            patch.dict("os.environ", LAPTOP_INFERENCE_TOKEN=TOKEN),
            patch.object(laptop_services.http.client, "HTTPConnection", return_value=connection),
        ):
            with self.assertRaises(DetectionError) as error:
                laptop_services.laptop_request(jpeg())
        self.assertEqual(error.exception.code, "laptop_disconnected")
        self.assertEqual(connection.request.call_count, 1)
        connection.close.assert_called_once()

    def test_invalid_metadata(self):
        """Functionality: Reject mismatched upstream model/precision. Inputs: controlled headers.
        Outputs: 502; Logic: actual bounded JPEG validation; Constraints: fake HTTP response.
        """
        for key, value in (
            ("X-Model", "other"),
            ("X-Inference-Precision", "FP16"),
            ("X-Inference-Ms", "nan"),
            ("X-Detection-Count", "301"),
        ):
            with self.subTest(key=key):
                headers = {**HEADERS, key: value}
                response = Mock(status=200)
                response.read1.side_effect = [jpeg(), b""]
                response.getheader.side_effect = headers.get
                connection = Mock()
                connection.getresponse.return_value = response
                with (
                    patch.dict("os.environ", LAPTOP_INFERENCE_TOKEN=TOKEN),
                    patch.object(
                        laptop_services.http.client, "HTTPConnection", return_value=connection
                    ),
                ):
                    with self.assertRaises(DetectionError) as error:
                        laptop_services.laptop_request(jpeg())
                self.assertEqual(error.exception.status, 502)

    def test_invalid_jpeg(self):
        """Functionality: Treat invalid upstream media as 502. Inputs: fake success with bad JPEG.
        Outputs: safe upstream error, not client-side 400; Logic: actual JPEG validation.
        Constraints: mocked transport; no real GPU output used.
        """
        response = Mock(status=200)
        response.read1.side_effect = [b"bad", b""]
        response.getheader.side_effect = HEADERS.get
        connection = Mock()
        connection.getresponse.return_value = response
        with (
            patch.dict("os.environ", LAPTOP_INFERENCE_TOKEN=TOKEN),
            patch.object(laptop_services.http.client, "HTTPConnection", return_value=connection),
        ):
            with self.assertRaises(DetectionError) as error:
                laptop_services.laptop_request(jpeg())
        self.assertEqual(error.exception.code, "laptop_invalid_response")

    def test_valid(self):
        """Functionality: Check fixed authenticated upstream request. Inputs: valid JPEG/headers.
        Outputs: decoded tuple; Logic: inspect destination/method/auth and boundary code.
        Constraints: no external service or precision comparison.
        """
        response = Mock(status=200)
        response.read1.side_effect = [jpeg(), b""]
        response.getheader.side_effect = HEADERS.get
        connection = Mock()
        connection.getresponse.return_value = response
        with (
            patch.dict("os.environ", LAPTOP_INFERENCE_TOKEN=TOKEN),
            patch.object(
                laptop_services.http.client, "HTTPConnection", return_value=connection
            ) as factory,
        ):
            self.assertEqual(laptop_services.laptop_request(jpeg()), (jpeg(), 1, 12.3))
        factory.assert_called_once_with("127.0.0.1", 18767, timeout=8)
        self.assertEqual(connection.request.call_args.args[:2], ("POST", "/frame/"))
        self.assertEqual(connection.request.call_args.args[3]["Authorization"], "Bearer " + TOKEN)
