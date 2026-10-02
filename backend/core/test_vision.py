"""Responsibilities: Verify JPEG/coordinate boundaries, initialization and vision HTTP contracts.
Implementation: Real Pillow/NumPy transforms; mocked model/session/HTTP, no downloaded weights.
Related Modules: core.detector, core.vision; actual model and public HTTP checked separately.
Declaration Index: jpeg; TransformTests; TransformTests.test_decode;
TransformTests.test_coordinates; TransformTests.test_reuse;
TransformTests.test_render; TransformTests.test_bad_predictions;
InitializationTests; InitializationTests.test_hash; InitializationTests.test_cached_failure;
InitializationTests.test_missing; VisionTests; VisionTests.post; VisionTests.test_success;
InitializationTests.test_allocator;
VisionTests.test_boundaries; VisionTests.test_busy; VisionTests.test_failure;
VisionTests.test_config.
Variable Index: NAMES — synthetic class order, not a trained-model accuracy fixture.
"""

import io
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import numpy as np
from django.test import SimpleTestCase, override_settings
from PIL import Image

from . import detector, vision

NAMES = [f"class-{index}" for index in range(80)]


def jpeg(size=(120, 80), format="JPEG"):
    """Functionality: Encode a synthetic image. Inputs: size/format; Outputs: bytes.
    Logic: solid RGB Pillow fixture; Constraints: contains no camera or external-service data.
    """
    output = io.BytesIO()
    Image.new("RGB", size, "black").save(output, format=format)
    return output.getvalue()


class TransformTests(SimpleTestCase):
    """Functionality: Test real preprocessing/rendering; Logic: controlled pixels and boxes.
    Constraints: does not measure a pretrained detector's accuracy.
    """

    def test_decode(self):
        """Functionality: Check decode limits. Inputs: synthetic JPEG/PNG/truncation/size.
        Outputs: RGB or safe error; Logic: exercise actual decoder; Constraints: no inference.
        """
        self.assertEqual(detector.decode_frame(jpeg((640, 320))).size, (640, 320))
        for payload in (jpeg((641, 20)), jpeg(format="PNG"), jpeg()[:100], b"bad"):
            with self.subTest(length=len(payload)), self.assertRaises(detector.DetectionError):
                detector.decode_frame(payload)
        for payload in (b"", b"x" * (detector.MAX_FRAME_BYTES + 1)):
            with self.assertRaises(detector.DetectionError) as caught:
                detector.decode_frame(payload)
            self.assertEqual(caught.exception.status, 413)

    def test_coordinates(self):
        """Functionality: Verify inverse letterbox and threshold. Inputs: portrait/landscape boxes.
        Outputs: original coordinates and normalized tensor; Logic: synthesize transformed rows.
        Constraints: zero-score rows ignored, no NMS, no ONNX session.
        """
        for size in ((320, 640), (640, 319)):
            with self.subTest(size=size):
                image = Image.new("RGB", size, "white")
                tensor, scale, left, top = detector.letterbox(image)
                self.assertEqual(tensor.shape, (1, 3, 640, 640))
                self.assertEqual(tensor.dtype, np.float32)
                rows = np.zeros((1, 300, 6), dtype=np.float32)
                rows[0, 0] = [
                    left + 10 * scale,
                    top + 20 * scale,
                    left + 50 * scale,
                    top + 60 * scale,
                    0.25,
                    0,
                ]
                rows[0, 1] = [left, top, left + 50, top + 50, 0.249, 0]
                boxes = detector.decode_boxes(rows, size, (scale, left, top), NAMES)
                self.assertEqual(len(boxes), 1)
                np.testing.assert_allclose(boxes[0]["xyxy"], [10, 20, 50, 60])
                self.assertEqual(boxes[0]["label"], "class-0")

    def test_render(self):
        """Functionality: Verify drawn JPEG and empty result. Inputs: RGB image and valid box.
        Outputs: same dimensions, changed green border; Logic: decode actual rendering.
        Constraints: verifies pixels, not semantic object recognition.
        """
        image = Image.new("RGB", (120, 80), "black")
        box = {"xyxy": [10, 30, 100, 70], "label": "person", "score": 0.9}
        output = detector.decode_frame(detector.annotate(image, [box]))
        self.assertEqual(output.size, image.size)
        self.assertGreater(output.getpixel((10, 50))[1], 100)
        self.assertEqual(detector.decode_frame(detector.annotate(image, [])).size, image.size)

    def test_reuse(self):
        """Functionality: Verify reusable tensor ownership, RGB values and overwritten padding.
        Inputs: red portrait, blue landscape and an existing tensor; Outputs: identity/pixel checks.
        Logic: two aspect ratios reuse one buffer; Constraints: no model or mirrored resize oracle.
        """
        output = np.empty((1, 3, 640, 640), dtype=np.float32)
        first, _, left, top = detector.letterbox(Image.new("RGB", (320, 640), "red"), output)
        self.assertIs(first, output)
        np.testing.assert_array_equal(first[0, :, top + 20, left + 20], [1, 0, 0])
        second, _, left, top = detector.letterbox(Image.new("RGB", (640, 320), "blue"), output)
        self.assertIs(second, output)
        np.testing.assert_array_equal(second[0, :, top + 20, left + 20], [0, 0, 1])
        np.testing.assert_array_equal(
            second[0, :, 0, 300], np.full(3, np.float32(114) / np.float32(255), dtype=np.float32)
        )
        with self.assertRaises(ValueError):
            detector.letterbox(Image.new("RGB", (10, 10)), np.empty((1, 3, 640, 640), np.float64))

    def test_bad_predictions(self):
        """Functionality: Reject invalid model outputs. Inputs: shape/NaN/class/score fixtures.
        Outputs: safe 502; Logic: mutate each independent boundary; Constraints: mock predictions.
        """
        for value, column in ((float("nan"), 0), (80, 5), (0.5, 5), (1.1, 4)):
            rows = np.zeros((1, 300, 6), dtype=np.float32)
            rows[0, 0, column] = value
            with self.assertRaises(detector.DetectionError):
                detector.decode_boxes(rows, (120, 80), (1, 0, 0), NAMES)
        with self.assertRaises(detector.DetectionError):
            detector.decode_boxes(np.zeros((1, 6)), (120, 80), (1, 0, 0), NAMES)


class InitializationTests(SimpleTestCase):
    """Functionality: Verify explicit artifact failure. Logic: isolate process-global state.
    Constraints: session construction mocked, no real weights or network.
    """

    def test_hash(self):
        """Functionality: Prevent unverified session construction. Inputs: wrong artifact bytes.
        Outputs: ValueError, no session; Logic: real SHA check; Constraints: temporary fixture only.
        """
        with TemporaryDirectory() as folder, patch("onnxruntime.InferenceSession") as session:
            path = Path(folder) / "wrong.onnx"
            path.write_bytes(b"invalid model")
            with self.assertRaises(ValueError):
                detector.Detector(str(path))
            session.assert_not_called()

    def test_cached_failure(self):
        """Functionality: Prohibit implicit load retry. Inputs: failing constructor/environment.
        Outputs: two 503s, one load; Logic: fresh globals restored afterward.
        Constraints: constructor mocked.
        """
        with (
            patch.object(detector, "detector", None),
            patch.object(detector, "initialization_error", None),
            patch.dict("os.environ", {"YOLO_MODEL_PATH": "/test/model.onnx"}),
            patch.object(detector, "Detector", side_effect=ValueError("private")) as load,
        ):
            for _ in range(2):
                with self.assertRaises(detector.DetectionError) as caught:
                    detector.get_detector()
                self.assertEqual(caught.exception.status, 503)
                self.assertNotIn("private", caught.exception.detail)
            self.assertEqual(load.call_count, 1)

    def test_missing(self):
        """Functionality: Detect missing model configuration. Inputs: empty path/fresh state.
        Outputs: model_not_configured; Logic: explicit failure; Constraints: no download attempted.
        """
        with (
            patch.object(detector, "detector", None),
            patch.object(detector, "initialization_error", None),
            patch.dict("os.environ", {"YOLO_MODEL_PATH": ""}),
        ):
            with self.assertRaises(detector.DetectionError) as caught:
                detector.get_detector()
            self.assertEqual(caught.exception.code, "model_not_configured")

    def test_allocator(self):
        """Functionality: Verify bounded allocator registers once. Inputs: fresh flag and ORT mock.
        Outputs: one registration with 128 MiB/requested-size strategy; Logic: two explicit calls.
        Constraints: registration mocked, resource/equality validation performed on real server.
        """
        with (
            patch.object(detector, "arena_registered", False),
            patch("onnxruntime.create_and_register_allocator") as register,
        ):
            detector.register_arena()
            detector.register_arena()
        self.assertEqual(register.call_count, 1)
        config = register.call_args.args[1]
        self.assertEqual(config.max_mem, 128 * 1024 * 1024)
        self.assertEqual(config.arena_extend_strategy, 1)


@override_settings(CAMERA_ALLOWED_ORIGINS=["http://testserver"])
class VisionTests(SimpleTestCase):
    """Functionality: Check actual Django endpoints. Logic: model mocked, JPEG decoding real.
    Constraints: HTTP contracts do not prove real detector accuracy or external deployment.
    """

    def post(self, payload=None, origin="http://testserver", content_type="image/jpeg"):
        """Functionality: Submit controlled request. Inputs: payload/origin/content_type.
        Outputs: Django response; Logic: real routing; Constraints: local test client only.
        """
        return self.client.post(
            "/vision/frame/",
            jpeg() if payload is None else payload,
            content_type=content_type,
            HTTP_ORIGIN=origin,
        )

    def test_success(self):
        """Functionality: Verify JPEG response metadata. Inputs: model returning real encoded image.
        Outputs: count/timing/model headers; Logic: real input decoder.
        Constraints: prediction mocked.
        """
        model = Mock()
        model.predict.return_value = (jpeg(), [{}], 12.3)
        with patch.object(vision, "get_detector", return_value=model):
            response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Detection-Count"], "1")
        self.assertEqual(response["X-Inference-Ms"], "12.3")
        self.assertEqual(response["X-Model"], "yolo26n")
        self.assertEqual(detector.decode_frame(response.content).size, (120, 80))
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_boundaries(self):
        """Functionality: Reject bad origin/method/MIME/JPEG. Inputs: invalid controlled requests.
        Outputs: 403/405/415/400; Logic: exercise guards; Constraints: model must not be invoked.
        """
        with patch.object(vision, "get_detector") as load:
            self.assertEqual(self.post(origin="http://evil.test").status_code, 403)
            self.assertEqual(self.post(origin="").status_code, 403)
            self.assertEqual(self.post(content_type="image/png").status_code, 415)
            self.assertEqual(self.post(b"bad").status_code, 400)
            self.assertEqual(self.client.get("/vision/frame/").status_code, 405)
            load.assert_not_called()

    def test_busy(self):
        """Functionality: Verify non-queued admission. Inputs: occupied global slot.
        Outputs: 429 on config/frame; Logic: real semaphore; Constraints: release in finally.
        """
        vision.frame_slot.acquire()
        try:
            self.assertEqual(self.post().status_code, 429)
            self.assertEqual(self.client.get("/vision/config/").status_code, 429)
        finally:
            vision.frame_slot.release()

    def test_failure(self):
        """Functionality: Verify safe errors and slot cleanup. Inputs: decoder-valid failed model.
        Outputs: generic 500 then accessible slot; Logic: mock failure; Constraints: no retries.
        """
        with patch.object(vision, "get_detector", side_effect=RuntimeError("secret")):
            response = self.post()
        self.assertEqual(response.status_code, 500)
        self.assertNotIn(b"secret", response.content)
        self.assertTrue(vision.frame_slot.acquire(blocking=False))
        vision.frame_slot.release()

    def test_config(self):
        """Functionality: Verify readiness contract. Inputs: initialized fake 80-class model.
        Outputs: fixed ready/model/classes/threshold; Logic: Django view; Constraints: no ONNX run.
        """
        with patch.object(vision, "get_detector", return_value=Mock(names=NAMES)):
            response = self.client.get("/vision/config/")
        self.assertEqual(
            response.json(),
            {"ready": True, "model": "yolo26n", "classes": 80, "confidence_threshold": 0.25},
        )
