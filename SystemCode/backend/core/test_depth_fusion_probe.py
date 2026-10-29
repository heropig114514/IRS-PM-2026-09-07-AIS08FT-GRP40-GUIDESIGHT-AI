"""Responsibilities: Validate the experimental ROI reducer's exact numerical boundaries.
Implementation: Deterministic planes on CPU and available CUDA; independent expected medians
and direct comparison with the unchanged control, including subnormal and overflow boundaries.
Related Modules: tools.depth_fusion_probe; realtime.depth.
Declaration Index: FusionProbeTests; FusionProbeTests.test_exact_medians;
FusionProbeTests.test_clipping_ownership_and_fusion; FusionProbeTests.test_invalid_inputs.
Variable Index: HAS_TORCH — optional inference runtime availability.
"""

import importlib.util
from unittest import skipUnless
from unittest.mock import patch

import numpy as np
from django.test import SimpleTestCase

from realtime.depth import fuse_object_depth, roi_bounds, roi_median
from tools.depth_fusion_probe import sorted_roi_median

HAS_TORCH = importlib.util.find_spec("torch") is not None


@skipUnless(HAS_TORCH, "Torch belongs to the optional inference runtime")
class FusionProbeTests(SimpleTestCase):
    """Functionality: Guard exact distance semantics before any performance claim.
    Logic: vary positive sample counts and invalid values without random data or approximations.
    Constraints: synthetic numerical cases cannot establish pretrained accuracy or video quality.
    """

    def test_exact_medians(self):
        """Functionality: Preserve central ranks, invalid filtering and safe even arithmetic.
        Inputs: every valid count from zero to17, finite extremes and FP32 subnormals.
        Outputs: exact known medians and control equality on CPU/available CUDA.
        Logic: reversed unique values prevent assumptions about ordering; invalids stay excluded.
        Constraints: no RNG, tolerance relaxation or altered precision in the model.
        """
        import torch

        devices = ["cpu", "cuda"] if torch.cuda.is_available() else ["cpu"]
        smallest = np.nextafter(np.float32(0), np.float32(1)).item()
        largest = torch.finfo(torch.float32).max
        cases = [(list(range(count, 0, -1)) + [float("nan"), float("inf"), -1., 0.],
                  (count + 1) / 2 if count else float("nan")) for count in range(18)]
        cases += [([smallest], smallest), ([largest, largest], largest),
                  ([smallest, smallest * 2, smallest * 3], smallest * 2)]
        for device in devices:
            for values, expected in cases:
                with self.subTest(device=device, values=values):
                    plane = torch.tensor([values], device=device, dtype=torch.float32)
                    bounds = (0, 0, len(values), 1)
                    actual = sorted_roi_median(plane, bounds)
                    torch.testing.assert_close(actual, roi_median(plane, bounds),
                                               rtol=0, atol=0, equal_nan=True)
                    torch.testing.assert_close(actual, plane.new_tensor(expected),
                                               rtol=0, atol=0, equal_nan=True)

    def test_clipping_ownership_and_fusion(self):
        """Functionality: Preserve clipped ROI fusion and earlier outputs across changed pixels.
        Inputs: strided depth view, fractional/outside boxes, then a modified source map.
        Outputs: identical canonical boxes, unchanged input and independently owned scalar.
        Logic: compare full fusion contract under an explicit temporary candidate substitution.
        Constraints: no full-map host transfer in the reducer or source mutation.
        """
        import torch

        devices = ["cpu", "cuda"] if torch.cuda.is_available() else ["cpu"]
        for device in devices:
            plane = torch.arange(1, 49, dtype=torch.float32, device=device).reshape(6, 8)[:, ::2]
            original = plane.clone()
            boxes = [{"xyxy": coords, "label": "person", "score": 0.9, "class_id": 0}
                     for coords in ([0.5, 1.1, 3.2, 4.8], [-10, -2, 10, 10], [8, 9, 10, 11])]
            expected = fuse_object_depth(plane, boxes, (4, 6))
            bounds = roi_bounds(boxes[0]["xyxy"], plane.shape)
            first = sorted_roi_median(plane, bounds)
            retained = first.clone()
            with patch("realtime.depth.roi_median", sorted_roi_median):
                self.assertEqual(fuse_object_depth(plane, boxes, (4, 6)), expected)
                self.assertEqual(fuse_object_depth(plane, [], (4, 6)), [])
            torch.testing.assert_close(plane, original, rtol=0, atol=0)
            plane.add_(100)
            second = sorted_roi_median(plane, bounds)
            torch.testing.assert_close(first, retained, rtol=0, atol=0)
            torch.testing.assert_close(second, retained + 100, rtol=0, atol=0)

    def test_invalid_inputs(self):
        """Functionality: Fail explicitly for unsupported input types and empty unclamped bounds.
        Inputs: NumPy/None/integer maps and an empty slice. Outputs: ValueError.
        Logic: ensure no hidden CPU fallback. Constraints: caller still owns map/ROI validation.
        """
        import torch

        for plane in (None, np.ones((2, 2)), torch.ones((2, 2), dtype=torch.int64)):
            with self.assertRaises(ValueError):
                sorted_roi_median(plane, None)
        with self.assertRaises(ValueError):
            sorted_roi_median(torch.ones((2, 2)), (0, 0, 0, 0))
