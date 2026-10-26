"""Responsibilities: Preserve historical evaluation thresholds after production policy changes.
Implementation: Boundary fixtures and the real native-adapter decode branch with a fake runner.
Related Modules: core.detector; unified_evaluation.adapters/diagnostic_capture.
Declaration Index: ExperimentPolicyTests; ExperimentPolicyTests.rows;
ExperimentPolicyTests.test_explicit_uniform_threshold; ExperimentPolicyTests.test_native_protocol.
Variable Index: None. Tests do not change the global production traffic-light policy.
"""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

import numpy as np
from PIL import Image

from core.detector import CLASS_CONF, decode_boxes
from model_tests.unified_evaluation.adapters import NativeAdapter


class ExperimentPolicyTests(TestCase):
    """Functionality: Separate production recall changes from fixed scientific comparisons.
    Logic: low-confidence lights remain in production and stay excluded in historical evaluation.
    Constraints: no global policy mutation, threshold fitting or new model execution.
    """

    @staticmethod
    def rows():
        """Functionality: Build valid e2e rows around the original confidence boundary.
        Inputs: None. Outputs: fixed shape FP32 proposals. Logic: lights and people at .20/.25.
        Constraints: remaining zero-area proposals are intentionally below both thresholds.
        """
        values = np.zeros((1, 300, 6), dtype=np.float32)
        values[0, :4] = [[1, 1, 4, 4, .20, 1], [1, 1, 4, 4, .25, 1],
                         [1, 1, 4, 4, .20, 0], [1, 1, 4, 4, .25, 0]]
        return values

    def test_explicit_uniform_threshold(self):
        """Functionality: Preserve upstream defaults and explicit legacy uniform threshold.
        Inputs: identical rows and class names. Outputs: three production versus two legacy boxes.
        Logic: compare public API modes and check the global mapping after both calls.
        Constraints: the experimental override cannot reduce production traffic-light recall.
        """
        policy = dict(CLASS_CONF)
        names = ["person", "traffic light"]
        production = decode_boxes(self.rows(), (10, 10), (1, 0, 0), names)
        legacy = decode_boxes(self.rows(), (10, 10), (1, 0, 0), names, class_conf={})
        self.assertEqual(len(production), 3)
        self.assertEqual([b["label"] for b in legacy], ["traffic light", "person"])
        self.assertTrue(all(b["score"] == .25 for b in legacy))
        self.assertEqual(CLASS_CONF, policy)
        self.assertEqual(CLASS_CONF["traffic light"], .12)

    def test_native_protocol(self):
        """Functionality: Prove the actual offline adapter opts out of production overrides.
        Inputs: native adapter with fake forward rows and unchanged real decoding.
        Outputs: only original-threshold boxes. Logic: run the full native predict branch on CPU.
        Constraints: no detector weights are needed; CUDA behavior is tested separately.
        """
        adapter = NativeAdapter.__new__(NativeAdapter)
        adapter.device, adapter.method, adapter.names = "cpu", "a", ["person", "traffic light"]
        adapter.tensor = np.empty((1, 3, 640, 640), dtype=np.float32)
        adapter.runner = Mock(return_value=(self.rows(), 0))
        adapter.runner.forward_event_ms = 0
        adapter.tracker, adapter.depth = None, None
        adapter.strategy = SimpleNamespace(
            apply=lambda image, boxes, depth, **kwargs: (boxes, False))
        boxes, _, _ = adapter.predict(Image.new("RGB", (640, 640)), {},
                                     bbox_mode="predicted", timestamp=0)
        self.assertEqual(len(boxes), 2)
        self.assertTrue(all(b["score"] == .25 for b in boxes))
        self.assertEqual(adapter.runner.call_count, 1)
