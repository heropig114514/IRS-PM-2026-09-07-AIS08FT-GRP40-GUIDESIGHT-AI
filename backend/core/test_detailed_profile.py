"""Responsibilities: Verify CUDA timing boundaries and unchanged instrumented ROI fusion.
Implementation: Ordered event mocks plus actual CUDA ROI equivalence when available.
Related Modules: model_tests.unified_evaluation.detailed_profile; realtime.depth/distance.
Declaration Index: DetailedProfileTests; DetailedProfileTests.test_event_order_and_reuse;
DetailedProfileTests.test_fusion_and_restore.
DetailedProfileTests.test_runner_ignores_coarse_duration.
Variable Index: None. Synthetic event durations verify arithmetic, not GPU speed.
"""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import numpy as np
import torch
from PIL import Image

from model_tests.unified_evaluation.adapters import NativeAdapter
from model_tests.unified_evaluation.detailed_profile import CudaTimer, DetailedProfile
from realtime import depth, distance


class DetailedProfileTests(TestCase):
    """Functionality: Guard completion-aware measurements and exact original outputs.
    Logic: inspect call order, event reuse and restoration. Constraints: serial profiler tests.
    """

    def test_event_order_and_reuse(self):
        """Functionality: Prevent asynchronous enqueue time from masquerading as completed work.
        Inputs: fake events, deterministic clock and ordered operation. Outputs: correct totals.
        Logic: assert drain/record/operation/end/wait/drain/read order and event-pair reuse.
        Constraints: no GPU required for this boundary test.
        """
        order = []
        begin, end = Mock(), Mock()
        begin.record.side_effect = lambda stream: order.append("begin")
        end.record.side_effect = lambda stream: order.append("end")
        end.synchronize.side_effect = lambda: order.append("event_wait")
        begin.elapsed_time.side_effect = lambda event: order.append("elapsed") or 3.0
        operation = Mock(side_effect=lambda: order.append("operation") or "result")
        timer = CudaTimer("cuda:0")
        with (
            patch("model_tests.unified_evaluation.detailed_profile.synchronize",
                  side_effect=lambda device: order.append("drain")),
            patch("model_tests.unified_evaluation.detailed_profile.time.perf_counter",
                  side_effect=[1, 1.005, 2, 2.006]),
            patch("torch.cuda.Event", side_effect=[begin, end]) as factory,
            patch("torch.cuda.current_stream", return_value="stream"),
        ):
            for _ in range(2):
                self.assertEqual(timer.call("forward", operation, events=True), "result")
        self.assertEqual(order, ["drain", "begin", "operation", "end", "event_wait",
                                 "drain", "elapsed"] * 2)
        self.assertEqual(factory.call_count, 2)
        self.assertAlmostEqual(timer.values["forward_wall_ms"], 11)
        self.assertEqual(timer.values["forward_cuda_ms"], 6)

    def test_fusion_and_restore(self):
        """Functionality: Preserve actual ROI medians and restore temporary module hooks.
        Inputs: CUDA depth, valid and outside-image boxes. Outputs: identical fused dictionaries.
        Logic: compare original versus instrumented fusion, retain per-box samples and cleanup.
        Constraints: requires CUDA; does not establish pretrained model accuracy.
        """
        if not torch.cuda.is_available():
            self.skipTest("CUDA required for actual ROI event timing")
        plane = torch.arange(1, 25, device="cuda", dtype=torch.float32).reshape(4, 6)
        boxes = [{"xyxy": bbox, "label": "person", "score": 0.9, "class_id": 0}
                 for bbox in ([0, 0, 6, 4], [1, 1, 4, 3], [9, 9, 10, 10])]
        expected = depth.fuse_object_depth(plane, boxes, (6, 4))
        original_roi, original_fuse = depth.roi_median, distance.fuse_object_depth
        adapter = SimpleNamespace(method="a", device="cuda:0", identity={},
                                  depth=SimpleNamespace(predict=Mock()))
        profile = DetailedProfile(adapter)
        try:
            with self.assertRaises(ValueError):
                DetailedProfile(adapter)
            actual = distance.fuse_object_depth(plane, boxes, (6, 4))
            self.assertEqual(actual, expected)
            self.assertEqual(len(profile.box_rows), 3)
            self.assertEqual(profile.box_rows[-1]["pixels"], 0)
            self.assertTrue(all(r["wall_ms"] >= 0 and r["cuda_ms"] >= 0
                                for r in profile.box_rows))
        finally:
            profile.close()
            profile.close()
        self.assertIs(depth.roi_median, original_roi)
        self.assertIs(distance.fuse_object_depth, original_fuse)

    def test_runner_ignores_coarse_duration(self):
        """Functionality: Avoid Windows GetTickCount64 rounding in detector substage attribution.
        Inputs: fake runner reporting an exaggerated legacy duration and a precise synthetic clock.
        Outputs: runner wall timing comes from perf_counter, not its returned coarse duration.
        Logic: execute the actual adapter branch. Constraints: no GPU or detector weights required.
        """
        adapter = NativeAdapter.__new__(NativeAdapter)
        adapter.device, adapter.method, adapter.names = "cpu", "a", ["person"]
        adapter.tensor = np.empty((1, 3, 640, 640), dtype=np.float32)
        adapter.runner = Mock(return_value=(np.zeros((1, 300, 6)), 99999.0))
        adapter.runner.forward_event_ms = 1.0
        adapter.tracker, adapter.depth = None, object()
        adapter.strategy = SimpleNamespace(apply=Mock(return_value=([], True)))
        with patch("model_tests.unified_evaluation.adapters.time.perf_counter",
                   side_effect=range(9)):
            _, timings, _ = adapter.predict(Image.new("RGB", (6, 4)), {},
                                             bbox_mode="predicted", timestamp=0)
        self.assertEqual(timings["detection_runner_ms"], 1000)
        self.assertEqual(timings["detection_decode_ms"], 1000)
        self.assertEqual(timings["detection_forward_event_ms"], 1)
        self.assertLess(timings["detection_runner_ms"], timings["detection_ms"])
