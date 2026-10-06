"""Responsibilities: Verify JPEG, FP32 transforms, coordinate boundaries and rendering.
Implementation: Actual Pillow/NumPy helpers with controlled pixels/predictions, no model or HTTP.
Related Modules: core.detector; live WS tests reuse the explicit jpeg fixture.
Declaration Index: jpeg; TransformTests; TransformTests.test_decode;
TransformTests.test_coordinates;
TransformTests.test_render; TransformTests.test_reuse; TransformTests.test_bad_predictions.
Variable Index: NAMES — synthetic80-class order, not an accuracy dataset.
"""

import io

import numpy as np
from django.test import SimpleTestCase
from PIL import Image

from . import detector

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
        Constraints: zero-score rows ignored, no NMS, no model session.
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
