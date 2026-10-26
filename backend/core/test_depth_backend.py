"""Responsibilities: Verify explicit native depth selection and complete comparison semantics.
Implementation: CPU-only startup, missing-shape, immutable-config and numerical-report contracts.
Related Modules: realtime.depth_config/depth_runtime/depth; integration.inference.services;
tools.benchmark_depth_integration.
Declaration Index: DepthBackendTests; DepthBackendTests.test_explicit_selection;
DepthBackendTests.test_service_freezes_selection; DepthBackendTests.test_no_shape_fallback;
DepthBackendTests.test_precision_policy_rejected_before_model_load;
DepthBackendTests.test_comparison_does_not_hide_decision_changes.
Variable Index: None. No checkpoint or engine is loaded by these contract tests.
"""

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from django.test import SimpleTestCase

from core.detector import DetectionError
from realtime.depth import DepthEstimator
from realtime.depth_config import DepthBackendConfig, read_depth_backend
from realtime.depth_runtime import TensorRTDepthPool
from tools.benchmark_depth_integration import compare_boxes, compare_depth


class DepthBackendTests(SimpleTestCase):
    """Functionality: Prevent silent backend/shape/precision changes during service operation.
    Logic: exercise public configuration and dispatch boundaries without actual CUDA.
    Constraints: real engine output, speed and video semantics are validated by GPU reports.
    """

    def test_explicit_selection(self):
        """Functionality: Require complete, unambiguous operator configuration.
        Inputs: empty, valid and invalid environment maps. Outputs: frozen selection or rejection.
        Logic: resolve relative manifests; reject unknown modes and stray manifest settings.
        Constraints: no implicit engine lookup or alternate backend.
        """
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(read_depth_backend(root, {}), DepthBackendConfig())
        actual = read_depth_backend(root, {"PRS_DEPTH_BACKEND": "tensorrt",
                                           "PRS_DEPTH_ENGINE_MANIFEST": "models/test.json"})
        self.assertEqual(actual.manifest, root / "models/test.json")
        for environment in ({"PRS_DEPTH_BACKEND": "auto"}, {"PRS_DEPTH_BACKEND": "tensorrt"},
                            {"PRS_DEPTH_ENGINE_MANIFEST": "models/test.json"}):
            with self.subTest(environment=environment), self.assertRaises(ValueError):
                read_depth_backend(root, environment)

    def test_service_freezes_selection(self):
        """Functionality: Keep advertised and loaded backend selection consistent across lazy load.
        Inputs: TensorRT settings then changed environment. Outputs: original identity.
        Logic: use the real unloaded runtime contract/factory; assert no GPU initialization occurs.
        Constraints: not a runtime availability or model-quality check.
        """
        from integration.inference.services import InferenceRuntime

        with patch.dict(os.environ, {"PRS_DEPTH_BACKEND": "tensorrt",
                                     "PRS_DEPTH_ENGINE_MANIFEST": "models/test.json"}):
            runtime = InferenceRuntime()
        try:
            with patch.dict(os.environ, {"PRS_DEPTH_BACKEND": "torch",
                                         "PRS_DEPTH_ENGINE_MANIFEST": ""}):
                self.assertEqual(runtime.contract("test")["depth_backend"], "tensorrt")
                self.assertEqual(runtime.status()["models"]["depth"]["backend"], "tensorrt")
                self.assertEqual(runtime.factory.keywords["depth_config"], runtime.depth_config)
                self.assertIsNone(runtime.detector)
        finally:
            asyncio.run(runtime.close())

    def test_no_shape_fallback(self):
        """Functionality: Reject a missing engine before invoking any other runner.
        Inputs: a pool with one fake shape and an incompatible tensor contract. Outputs: error.
        Logic: exercise the actual dispatch lookup. Constraints: no GPU simulation or timing claim.
        """
        pool = TensorRTDepthPool.__new__(TensorRTDepthPool)
        runner = Mock()
        pool.runners = {(1, 3, 448, 768): runner}
        with self.assertRaises(DetectionError) as caught:
            pool(SimpleNamespace(shape=(1, 3, 768, 768)))
        self.assertEqual(caught.exception.code, "depth_input_shape")
        runner.assert_not_called()

    def test_precision_policy_rejected_before_model_load(self):
        """Functionality: Reject unsupported precision or ambiguous artifacts before checkpoint I/O.
        Inputs: invalid policies. Outputs: ValueError before any model dependency import.
        Logic: use a nonexistent checkpoint so accidental model-loading paths cannot pass.
        Constraints: no FP16/INT8 conversion or silent edge-size adjustment.
        """
        for options in ({"backend": "tensorrt"},
                        {"backend": "torch", "engine_manifest": "test.json"},
                        {"backend": "tensorrt", "engine_manifest": "test.json", "half": True},
                        {"backend": "tensorrt", "engine_manifest": "test.json", "image_size": 640}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                DepthEstimator("nonexistent.pt", "8.4.171", **options)

    def test_comparison_does_not_hide_decision_changes(self):
        """Functionality: Retain small numerical deviations and discrete risk changes separately.
        Inputs: two maps and objects with a changed distance/alert. Outputs: explicit differences.
        Logic: examine every field/pixel. Constraints: no tolerance or accuracy acceptance.
        """
        before = [{"distance_m": 2.0, "alert_required": False, "track_id": 7}]
        after = [{"distance_m": 2.001, "alert_required": True, "track_id": 7}]
        result = compare_boxes(before, after)
        self.assertFalse(result["exact"])
        self.assertEqual(result["structural_or_decision_changes"], ["boxes[0].alert_required"])
        self.assertGreater(result["numeric_differences"]["distance_m"]["max_abs"], 0)
        result = compare_depth(np.ones((2, 2), dtype=np.float32),
                               np.array([[1, 1], [1.001, np.nan]], dtype=np.float32))
        self.assertEqual(result["candidate_nonfinite"], 1)
        self.assertEqual(result["different_pixels"], 2)
        self.assertFalse(result["exact"])
