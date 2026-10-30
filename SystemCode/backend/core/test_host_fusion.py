"""Responsibilities: Guard exact host-fusion ranks, arithmetic, ownership and failure boundaries.
Implementation: Deterministic FP32 fixtures compared directly with original CPU/CUDA reductions;
real CUDA fusion when available and explicit rejection of unsupported source contracts.
Related Modules: tools.host_fusion_probe/resident_host_fusion_probe; realtime.depth.
Declaration Index: HostFusionTests; HostFusionTests.test_exact_ranks;
HostFusionTests.test_cuda_fusion; HostFusionTests.test_rejection_and_restoration;
HostFusionTests.test_service_scope.
Variable Index: HAS_TORCH — optional inference runtime availability.
"""

import copy
import importlib.util
import math
from unittest import skipUnless
from unittest.mock import patch

import numpy as np
from django.test import SimpleTestCase

from core.detector import DetectionError
from realtime.depth import fuse_object_depth, roi_median
from tools.host_fusion_probe import host_fuse_object_depth, host_roi_median

HAS_TORCH = importlib.util.find_spec("torch") is not None


@skipUnless(HAS_TORCH, "Torch belongs to the optional inference runtime")
class HostFusionTests(SimpleTestCase):
    """Functionality: Check numerical and resource contracts independently of pretrained models.
    Logic: use exact comparisons, invalid values, extrema and real available CUDA tensors.
    Constraints: no RNG, relaxed tolerance, accuracy claim or timing measurements.
    """

    def test_exact_ranks(self):
        """Functionality: Preserve all central ranks and explicit CUDA FP32 arithmetic.
        Inputs: zero to 17 valid samples, duplicates, invalids, maximum finite and subnormals.
        Outputs: exact control equality and unchanged source. Logic: compare host partition with
        original Torch reducer on CPU/available CUDA, including even subnormal pairs.
        Constraints: NumPy production float64 behavior is intentionally not the reference.
        """
        import torch

        tiny = np.nextafter(np.float32(0), np.float32(1))
        large = np.finfo(np.float32).max
        cases = [list(range(count, 0, -1)) + [np.nan, np.inf, -np.inf, -1., 0., -0.]
                 for count in range(18)]
        cases += [[tiny], [tiny, tiny], [tiny, tiny * 2], [tiny * 3, tiny * 5],
                  [large, large], [large / 2, large], [3., 3., 3., 3.]]
        devices = ["cpu", "cuda"] if torch.cuda.is_available() else ["cpu"]
        for device in devices:
            for values in cases:
                with self.subTest(device=device, values=values):
                    plane = np.array([values], dtype=np.float32)
                    original = plane.copy()
                    bounds = (0, 0, plane.shape[1], 1)
                    expected = float(roi_median(torch.tensor(plane, device=device), bounds))
                    actual = host_roi_median(plane, bounds)
                    if math.isnan(expected):
                        self.assertTrue(math.isnan(actual))
                    else:
                        self.assertEqual(actual, expected)
                    np.testing.assert_array_equal(plane, original)
        self.assertTrue(math.isnan(host_roi_median(np.ones((1, 1), np.float32), None)))

    def test_cuda_fusion(self):
        """Functionality: Preserve every fused field with fractional/clipped/invalid boxes.
        Inputs: real noncontiguous CUDA map, then changed pixels and empty detections.
        Outputs: exact dictionaries, original source/boxes and independent previous results.
        Logic: compare canonical CUDA fusion directly with explicit host candidate.
        Constraints: no hidden CPU emulation if CUDA is absent; synthetic quality not assessed.
        """
        import torch

        if not torch.cuda.is_available():
            self.skipTest("Actual CUDA is required for full host-fusion comparison")
        plane = torch.arange(1, 49, dtype=torch.float32, device="cuda").reshape(6, 8)[:, ::2]
        plane[0, 0] = float("nan")
        plane[2, 1] = 0
        bounds = [(-.2, -.5, 4.5, 6.5), (1.2, .5, 3.7, 4.1), (5, 8, 9, 10),
                  (2, 1, 1, 3), (0, 0, 1, 1), (float("nan"), 0, 2, 3)]
        boxes = [{"xyxy": list(b), "label": "person", "score": .9,
                  "class_id": 0, "track_id": i} for i, b in enumerate(bounds)]
        original, original_boxes = plane.clone(), copy.deepcopy(boxes)
        expected = fuse_object_depth(plane, boxes, (4, 6))
        actual = host_fuse_object_depth(plane, boxes, (4, 6))
        self.assertEqual(actual, expected)
        self.assertEqual(boxes, original_boxes)
        torch.testing.assert_close(plane, original, rtol=0, atol=0, equal_nan=True)
        saved = copy.deepcopy(actual)
        plane.add_(10)
        self.assertEqual(host_fuse_object_depth(plane, boxes, (4, 6)),
                         fuse_object_depth(plane, boxes, (4, 6)))
        self.assertEqual(actual, saved)
        self.assertEqual(host_fuse_object_depth(plane, [], (4, 6)), [])

    def test_rejection_and_restoration(self):
        """Functionality: Reject unsupported maps and restore the reducer after assembly failure.
        Inputs: wrong host dtype, CPU tensor, unaligned CUDA map, injected assembly exception.
        Outputs: explicit errors and original global reducer identity.
        Logic: validate source before copy; use ExitStack-style scope on the only temporary hook.
        Constraints: no retry or fallback; exception injection establishes hook cleanup only.
        """
        import torch

        import realtime.depth as depth

        with self.assertRaises(ValueError):
            host_roi_median(np.ones((1, 1), dtype=np.float64), (0, 0, 1, 1))
        with self.assertRaises(ValueError):
            host_fuse_object_depth(torch.ones((2, 2)), [], (2, 2))
        if torch.cuda.is_available():
            plane = torch.ones((2, 2), device="cuda")
            with self.assertRaises(DetectionError):
                host_fuse_object_depth(plane, [], (2, 3))
            with self.assertRaises(ValueError):
                host_fuse_object_depth(plane.half(), [], (2, 2))
            with patch("tools.host_fusion_probe.fuse_object_depth", side_effect=RuntimeError):
                with self.assertRaises(RuntimeError):
                    host_fuse_object_depth(plane, [{"xyxy": [0, 0, 2, 2]}], (2, 2))
            self.assertIs(depth.roi_median, roi_median)

    def test_service_scope(self):
        """Functionality: Restore production fusion even when the parent service call fails.
        Inputs: isolated wrapper and explicit parent exception. Outputs: original function restored.
        Logic: bypass model construction and fail inside the scoped service hook.
        Constraints: orchestration only; actual GPU/browser reports establish operational behavior.
        """
        import realtime.distance as distance
        from tools.resident_host_fusion_probe import HostFusionServiceProbe

        probe = object.__new__(HostFusionServiceProbe)
        original = distance.fuse_object_depth
        with patch("tools.resident_overlap_probe.OverlapServiceProbe.__call__",
                   side_effect=RuntimeError("explicit test failure")):
            with self.assertRaises(RuntimeError):
                probe(b"unused fixture")
        self.assertIs(distance.fuse_object_depth, original)
