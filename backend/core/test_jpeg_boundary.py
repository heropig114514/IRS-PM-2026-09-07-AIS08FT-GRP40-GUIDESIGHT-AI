"""Responsibilities: Keep JPEG decoding independent of SDK generic image/plugin patches.
Implementation: Real Pillow codec/reference pixels and explicit invalid format/size fixtures.
Related Modules: core.detector; Ultralytics patches PIL.Image.open after model loading.
Declaration Index: JpegBoundaryTests; JpegBoundaryTests.test_reference_pixels;
JpegBoundaryTests.test_invalid_input_without_plugin_probe.
Variable Index: None. Pixel equality here is decoder evidence, not model accuracy.
"""

import io
from unittest.mock import patch

from django.test import SimpleTestCase
from PIL import Image

from core.detector import DetectionError, decode_frame


class JpegBoundaryTests(SimpleTestCase):
    """Functionality: Preserve JPEG RGB output and reject unsupported input before GPU work.
    Logic: compare real decoder pixels while the generic opener is forbidden.
    Constraints: no HEIF dependencies, downloads, patched model or performance assertions.
    """

    def test_reference_pixels(self):
        """Functionality: Match original Pillow decoding for normal JPEG modes/encodings.
        Inputs: RGB/L/CMYK images with baseline/progressive JPEG. Outputs: exact RGB bytes and size.
        Logic: generate known gradients, decode with original opener, then forbid that opener.
        Constraints: resize, interpolation, JPEG quality and inference parameters stay unchanged.
        """
        for mode in ("RGB", "L", "CMYK"):
            for progressive in (False, True):
                image = Image.linear_gradient("L").resize((64, 32)).convert(mode)
                output = io.BytesIO()
                image.save(output, "JPEG", quality=80, progressive=progressive)
                payload = output.getvalue()
                with Image.open(io.BytesIO(payload)) as original:
                    expected = original.convert("RGB").tobytes()
                with patch("PIL.Image.open", side_effect=AssertionError("plugin probe forbidden")):
                    actual = decode_frame(payload)
                self.assertEqual(actual.mode, "RGB")
                self.assertEqual(actual.size, (64, 32))
                self.assertEqual(actual.tobytes(), expected)

    def test_invalid_input_without_plugin_probe(self):
        """Functionality: Reject junk, truncated JPEG, PNG and oversize geometry without discovery.
        Inputs: malformed/public synthetic codec inputs. Outputs: original safe error codes.
        Logic: forbid generic Image.open across every case. Constraints: no retries or plugin loads.
        """
        jpeg, png, large = io.BytesIO(), io.BytesIO(), io.BytesIO()
        Image.new("RGB", (32, 32), "red").save(jpeg, "JPEG")
        Image.new("RGB", (32, 32), "red").save(png, "PNG")
        Image.new("RGB", (1281, 32), "red").save(large, "JPEG")
        cases = [(b"", "frame_size"), (b"invalid JPEG fixture", "invalid_frame"),
                 (jpeg.getvalue()[:-20], "invalid_frame"), (png.getvalue(), "invalid_frame"),
                 (large.getvalue(), "invalid_frame")]
        with patch("PIL.Image.open", side_effect=AssertionError("plugin probe forbidden")):
            for payload, code in cases:
                with self.subTest(code=code, size=len(payload)):
                    with self.assertRaises(DetectionError) as caught:
                        decode_frame(payload)
                    self.assertEqual(caught.exception.code, code)
