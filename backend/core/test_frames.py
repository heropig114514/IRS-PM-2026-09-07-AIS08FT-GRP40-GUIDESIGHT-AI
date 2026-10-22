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
        self.assertEqual(detector.decode_frame(jpeg((1280, 720))).size, (1280, 720))
        for payload in (jpeg((1281, 20)), jpeg(format="PNG"), jpeg()[:100], b"bad"):
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

    def test_class_threshold(self):
        """Functionality: Per-class confidence. Inputs: traffic light 0.15 and other 0.15 rows.
        Outputs: only the traffic light survives; Logic: CLASS_CONF vs 0.25 default.
        Constraints: synthetic rows, no model.
        """
        names = list(NAMES)
        names[9] = "traffic light"
        rows = np.zeros((1, 300, 6), dtype=np.float32)
        rows[0, 0] = [10, 10, 20, 40, 0.15, 9]
        rows[0, 1] = [30, 10, 60, 40, 0.15, 0]
        boxes = detector.decode_boxes(rows, (640, 640), (1, 0, 0), names)
        self.assertEqual([box["label"] for box in boxes], ["traffic light"])

    def test_roi_and_front_light(self):
        """Functionality: ROI geometry and front-light choice. Inputs: 1000x600 image, boxes.
        Outputs: crop 400x330 at (300,0); the vertical light nearest centre is flagged.
        Constraints: wide boxes are never chosen; nothing is removed.
        """
        self.assertIsNone(detector.roi_crop(Image.new("RGB", (2, 2))))
        crop, x_offset, y_offset = detector.roi_crop(Image.new("RGB", (1000, 600)))
        self.assertEqual((crop.size, x_offset, y_offset), ((400, 330), 300, 0))
        light = lambda x1, x2, h: {"label": "traffic light", "xyxy": [x1, 0, x2, h]}  # noqa: E731
        boxes = [light(100, 110, 30), light(480, 490, 30), light(495, 600, 30),
                 {"label": "car", "xyxy": [0, 0, 5, 5]}]
        marked = detector.mark_front_light(boxes, (1000, 600))
        self.assertEqual([box.get("front_light") for box in marked], [False, True, False, None])
        self.assertEqual(detector.mark_front_light([], (10, 10)), [])

    def test_merge_boxes(self):
        """Functionality: Class-aware NMS merge. Inputs: duplicate light, ROI car, ROI light.
        Outputs: higher-score duplicate kept, ROI non-lights ignored; Constraints: needs torch.
        """
        try:
            import torchvision  # noqa: F401
        except ImportError:
            self.skipTest("torchvision is only installed on inference hosts")
        light = lambda box, score: {"xyxy": box, "score": score, "class_id": 9,  # noqa: E731
                                    "label": "traffic light"}
        car = {"xyxy": [0, 0, 50, 50], "score": 0.9, "class_id": 2, "label": "car"}
        merged = detector.merge_boxes([car, light([600, 100, 620, 160], 0.2)],
                                      [light([601, 101, 621, 161], 0.5), car])
        self.assertEqual([(box["label"], box["score"]) for box in merged],
                         [("car", 0.9), ("traffic light", 0.5)])

    def test_merge_keeps_overlapping_persons(self):
        """Functionality: NMS is traffic-light only. Inputs: two IoU>0.5 persons plus ROI light.
        Outputs: both persons kept first in original order; Constraints: needs torchvision.
        """
        try:
            import torchvision  # noqa: F401
        except ImportError:
            self.skipTest("torchvision is only installed on inference hosts")
        person = lambda score: {"xyxy": [10, 10, 60, 120], "score": score,  # noqa: E731
                                "class_id": 0, "label": "person"}
        light = {"xyxy": [300, 0, 310, 30], "score": 0.3, "class_id": 9, "label": "traffic light"}
        merged = detector.merge_boxes([person(0.5), person(0.9)], [light])
        self.assertEqual([(box["label"], box["score"]) for box in merged],
                         [("person", 0.5), ("person", 0.9), ("traffic light", 0.3)])

    def test_front_light_tie_and_empty(self):
        """Functionality: Tie and empty handling. Inputs: two lights equidistant from centre.
        Outputs: exactly one flagged (first); empty list stays empty. Constraints: no deletion.
        """
        light = lambda x1, x2: {"label": "traffic light", "xyxy": [x1, 0, x2, 30]}  # noqa: E731
        marked = detector.mark_front_light([light(490, 500), light(500, 510)], (1000, 600))
        self.assertEqual([box["front_light"] for box in marked], [True, False])
        self.assertEqual(detector.mark_front_light([], (1000, 600)), [])
