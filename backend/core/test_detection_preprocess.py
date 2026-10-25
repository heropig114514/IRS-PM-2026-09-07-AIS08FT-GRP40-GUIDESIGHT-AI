"""Responsibilities: Protect exact detector tensors and independent optimization switches.
Implementation: Bit-level comparisons across shapes/pixels, contaminated buffer reuse and mocks.
Related Modules: core.detection_preprocess; unified_evaluation.a_formal/preprocess_profile.
Declaration Index: DetectionPreprocessTests; DetectionPreprocessTests.test_exact_pixels;
DetectionPreprocessTests.test_buffer_reuse; DetectionPreprocessTests.test_invalid_output;
DetectionPreprocessTests.test_profile_matches_original;
DetectionPreprocessTests.test_switch_isolation.
Variable Index: None. Tests verify pixels and contracts, not hardware performance.
"""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

import numpy as np
from PIL import Image

from core.detection_preprocess import letterbox_fast
from core.detector import letterbox
from model_tests.unified_evaluation.a_formal import FormalA
from model_tests.unified_evaluation.preprocess_profile import baseline_stages, jpeg_probe


class DetectionPreprocessTests(TestCase):
    """Functionality: Guard interpolation, padding, normalization and switch semantics.
    Logic: compare original code bit-for-bit on diverse deterministic RGB inputs.
    Constraints: real model equality is verified by the separate full SANPO experiment.
    """

    def test_exact_pixels(self):
        """Functionality: Preserve all uint8 intensities and non-square transforms exactly.
        Inputs: ramps/random pixels at odd, square, narrow and source-dataset dimensions.
        Outputs: bit-identical FP32 tensors and inverse coordinate tuples.
        Logic: compare original public letterbox. Constraints: use exact bits, not allclose.
        """
        rng = np.random.default_rng(42)
        for width, height in (
            (256, 256),
            (641, 639),
            (319, 89),
            (89, 319),
            (1, 1),
            (640, 640),
            (2208, 1242),
            (513, 1025),
            (1025, 513),
        ):
            data = rng.integers(0, 256, size=(height, width, 3), dtype=np.uint8)
            if width == 256:
                data[:] = np.arange(256, dtype=np.uint8)[None, :, None]
            image = Image.fromarray(data)
            baseline, *transform = letterbox(image)
            fast, *actual_transform = letterbox_fast(image)
            self.assertEqual(actual_transform, transform)
            np.testing.assert_array_equal(fast.view(np.uint32), baseline.view(np.uint32))
            np.testing.assert_array_equal(np.asarray(image), data)

    def test_buffer_reuse(self):
        """Functionality: Overwrite stale pixels when orientation/padding changes between frames.
        Inputs: deliberately contaminated output reused over portrait/landscape/square images.
        Outputs: same output identity and completely exact contents on every call.
        Logic: prefill NaN then compare to fresh baseline. Constraints: no hidden shape cache.
        """
        output = np.empty((1, 3, 640, 640), np.float32)
        for shape in ((321, 123), (123, 321), (640, 640), (99, 100), (321, 123)):
            output.fill(np.nan)
            image = Image.new("RGB", shape, (11, 123, 255))
            actual = letterbox_fast(image, output=output)[0]
            self.assertIs(actual, output)
            np.testing.assert_array_equal(
                actual.view(np.uint32), letterbox(image)[0].view(np.uint32)
            )

    def test_invalid_output(self):
        """Functionality: Reject invalid output layouts or modes without silent conversion.
        Inputs: FP16/wrong-shape/noncontiguous buffers and grayscale image.
        Outputs: ValueError. Logic: exercise API guards. Constraints: valid FP32 layout is fixed.
        """
        image = Image.new("RGB", (16, 16))
        for output in (
            np.empty((1, 3, 640, 640), np.float16),
            np.empty((3, 640, 640), np.float32),
            np.empty((1, 3, 640, 640), np.float32)[:, :, :, ::-1],
        ):
            with self.assertRaises(ValueError):
                letterbox_fast(image, output)
        with self.assertRaises(ValueError):
            letterbox_fast(Image.new("L", (16, 16)))

    def test_profile_matches_original(self):
        """Functionality: Keep diagnostic replicas and JPEG ingress interpretation honest.
        Inputs: odd RGB image and scratch output. Outputs: exact reference tensors and JPEG audit.
        Logic: compare both measured paths and original, checking finite nonnegative stage times.
        Constraints: derived JPEG is a separate ingress probe, not a scored dataset substitute.
        """
        image = Image.fromarray(
            np.random.default_rng(1).integers(0, 256, (201, 301, 3), dtype=np.uint8)
        )
        reference = letterbox(image)[0]
        for operation in (baseline_stages, letterbox_fast):
            stages = {}
            actual = operation(image, np.empty_like(reference), stages=stages)[0]
            np.testing.assert_array_equal(actual.view(np.uint32), reference.view(np.uint32))
            self.assertIn("resize_ms", stages)
            self.assertTrue(all(np.isfinite(v) and v >= 0 for v in stages.values()))
        self.assertTrue(jpeg_probe(image)["production_rgb_equal"])

    def test_switch_isolation(self):
        """Functionality: Hold a-fast constant when only detector preprocessing is compared.
        Inputs: mocked native A and two comparison axes. Outputs: independent expected flags.
        Logic: call actual FormalA prediction dispatch without GPU operations.
        Constraints: original distance experiment remains backward compatible.
        """
        for experiment in ("distance", "preprocess"):
            formal = FormalA.__new__(FormalA)
            formal.experiment, formal.predictor = experiment, None
            adapter = SimpleNamespace(
                depth=SimpleNamespace(model=SimpleNamespace(predictor=None), fast_input=False),
                strategy=SimpleNamespace(fast_roi=False),
                preprocess_fast=False,
                predict=Mock(side_effect=lambda *a, **kw: ([], {}, True)),
            )
            formal.adapter = adapter
            for fast in (False, True, False):
                formal.predict(None, {}, fast=fast, timestamp=0)
                self.assertEqual(adapter.depth.fast_input, fast or experiment == "preprocess")
                self.assertEqual(adapter.strategy.fast_roi, fast or experiment == "preprocess")
                self.assertEqual(
                    adapter.preprocess_fast, fast if experiment == "preprocess" else False
                )
