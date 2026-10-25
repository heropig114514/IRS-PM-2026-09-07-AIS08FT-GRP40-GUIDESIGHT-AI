"""Responsibilities: Verify exact A fast input/ROI semantics and counterbalanced timing rules.
Implementation: Known/seeded pixel planes, optional CUDA comparisons and local timer mocks.
Related Modules: realtime.depth_fast; realtime.distance; unified_evaluation.a_formal/a_performance.
Declaration Index: FastDepthTests; FastDepthTests.test_bgr_pixels;
FastDepthTests.test_exact_medians; FastDepthTests.test_empty_and_invalid;
FastDepthTests.test_counterbalance_and_audit; FastDepthTests.test_coarse_timer_barrier.
Variable Index: None. Synthetic data establishes contracts, not runtime speed.
"""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import numpy as np
import torch
from PIL import Image

from core.detector import DetectionError
from model_tests.unified_evaluation.a_formal import FormalA
from model_tests.unified_evaluation.a_performance import compare_objects, pair_order
from realtime.depth import fuse_object_depth
from realtime.depth_fast import fuse_depth_host, pack_bgr


class FastDepthTests(TestCase):
    """Functionality: Prevent precision, pixel, bounds and missing-distance regressions.
    Logic: compare real original operations and inspect timing boundaries.
    Constraints: pretrained inference equality is checked separately on actual SANPO frames.
    """

    def test_bgr_pixels(self):
        """Functionality: Keep direct input export pixel-identical to official PIL ingestion.
        Inputs: seeded RGB images including narrow/odd shapes. Outputs: exact contiguous BGR.
        Logic: compare official loader output byte-for-byte. Constraints: no resizing involved.
        """
        from ultralytics.data.loaders import LoadPilAndNumpy

        rng = np.random.default_rng(42)
        for shape in ((1, 1, 3), (31, 73, 3), (500, 901, 3)):
            pixels = rng.integers(0, 256, size=shape, dtype=np.uint8)
            image = Image.fromarray(pixels)
            actual = pack_bgr(image)
            self.assertTrue(actual.flags.c_contiguous)
            np.testing.assert_array_equal(actual, LoadPilAndNumpy._single_check(image))
            np.testing.assert_array_equal(np.asarray(image), pixels)
        with self.assertRaises(ValueError):
            pack_bgr(Image.new("L", (4, 4)))

    def test_exact_medians(self):
        """Functionality: Preserve original finite-positive filtering and even/odd arithmetic.
        Inputs: FP32 planes, invalid/overlapping/fractional boxes, tiny and extreme depths.
        Outputs: exactly equal canonical objects. Logic: original Torch versus host partition.
        Constraints: CPU always tested; CUDA tested when present, with no approximate tolerance.
        """
        rng = np.random.default_rng(42)
        plane = rng.uniform(0.01, 1000, (49, 79)).astype(np.float32)
        plane[0, :8] = [np.nan, np.inf, -np.inf, 0, -2, 3, 7, 11]
        plane[1, :2] = np.finfo(np.float32).max
        plane[2, :2] = np.finfo(np.float32).tiny
        bounds = [
            [0, 0, 8, 1],
            [0, 0, 49, 37],
            [2.1, 4.8, 18.1, 40.2],
            [-5, -10, 100, 100],
            [100, 100, 120, 120],
            [0, 0, 1, 1],
            [0, 1, 2, 2],
            [0, 2, 2, 3],
            [0, 0, 0, 0],
            [np.nan, 0, 4, 4],
        ]
        boxes = [
            {"xyxy": b, "label": "person", "score": 0.9, "class_id": 0, "track_id": i}
            for i, b in enumerate(bounds)
        ]
        devices = ["cpu", "cuda"] if torch.cuda.is_available() else ["cpu"]
        for device in devices:
            depth = torch.from_numpy(plane).to(device)
            expected = fuse_object_depth(depth, boxes, (79, 49))
            actual = fuse_depth_host(depth, boxes, (79, 49))
            self.assertEqual(actual, expected)
            self.assertNotIn("distance_m", boxes[0])
            np.testing.assert_array_equal(depth.cpu().numpy(), plane)

    def test_empty_and_invalid(self):
        """Functionality: Preserve empty and malformed-map contracts without needless downloads.
        Inputs: empty boxes, wrong dtype and unaligned shape. Outputs: empty or explicit error.
        Logic: exercise public optimized fusion. Constraints: unsupported precision is not cast.
        """
        depth = torch.ones((4, 6))
        self.assertEqual(fuse_depth_host(depth, [], (6, 4)), [])
        with self.assertRaises(DetectionError):
            fuse_depth_host(depth, [], (4, 6))
        with self.assertRaises(ValueError):
            fuse_depth_host(depth.half(), [], (6, 4))

    def test_counterbalance_and_audit(self):
        """Functionality: Prevent order bias and missing-distance mismatches being hidden.
        Inputs: two rounds of odd-count frames and differing predictions.
        Outputs: each frame sees both orderings, mismatches are explicit.
        Logic: assert per-frame positional balance and exact distance comparison.
        Constraints: ordering changes execution only, never source ordering or GT selection.
        """
        for index in range(7):
            self.assertEqual(pair_order(0, index), pair_order(1, index)[::-1])
        left = [{"bbox": [0, 0, 4, 4], "distance_m": 2.0}]
        self.assertEqual(compare_objects(left, left)["distance_mismatches"], 0)
        right = [{"bbox": [0, 0, 4, 4], "distance_m": None}]
        self.assertEqual(compare_objects(left, right)["missing_pattern_mismatches"], 1)
        self.assertEqual(compare_objects(left, [])["structure_mismatches"], 1)

    def test_coarse_timer_barrier(self):
        """Functionality: Add a barrier only at completed depth, never for individual ROIs.
        Inputs: fake operations and device synchronizer. Outputs: identical values and timings.
        Logic: use the actual local wrapper. Constraints: tests call boundaries, not CUDA speed.
        """
        formal = FormalA.__new__(FormalA)
        formal.values, formal.adapter = {}, SimpleNamespace(device="cuda:0")
        owner = SimpleNamespace(depth=Mock(return_value="map"), fuse=Mock(return_value="objects"))
        formal.wrap(owner, "depth", "depth_ms", complete=True)
        formal.wrap(owner, "fuse", "roi_ms")
        with patch("model_tests.unified_evaluation.a_formal.synchronize") as sync:
            self.assertEqual(owner.depth(), "map")
            self.assertEqual(owner.fuse(), "objects")
        sync.assert_called_once_with("cuda:0")
        self.assertGreaterEqual(formal.values["roi_ms"], 0)
