"""Responsibilities: Verify explicit native depth selection and complete comparison semantics.
Implementation: CPU-only startup, missing-shape, immutable-config and numerical-report contracts.
Related Modules: realtime.depth_config/depth_runtime/depth; integration.inference.services;
tools.benchmark_depth_integration.
Declaration Index: DepthBackendTests; DepthBackendTests.test_explicit_selection;
DepthBackendTests.test_service_freezes_selection; DepthBackendTests.test_no_shape_fallback;
DepthBackendTests.test_precision_policy_rejected_before_model_load;
DepthBackendTests.test_comparison_does_not_hide_decision_changes.
DepthBackendTests.test_model_and_fusion_are_frozen;
DepthBackendTests.test_host_fusion_mode_boundary.
DepthBackendTests.test_launcher_verifies_selected_weights.
DepthBackendTests.test_launcher_explicit_interpreter.
DepthBackendTests.test_phone_launcher_explicit_interpreter.
Variable Index: None. No checkpoint or engine is loaded by these contract tests.
"""

import asyncio
import hashlib
import json
import os
import tempfile
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
                            {"PRS_DEPTH_ENGINE_MANIFEST": "models/test.json"},
                            {"PRS_DEPTH_FUSION": "auto"}):
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

    def test_model_and_fusion_are_frozen(self):
        """Functionality: Preserve an optional model's advertised identity across lazy loading.
        Inputs: walk/host startup then a changed process environment. Outputs: original identity.
        Logic: check actual manifest/class count, file selection, fusion and deferred factory args.
        Constraints: no model initialization or GPU work; the default remains the original model.
        """
        from integration.inference.services import InferenceRuntime

        with patch.dict(os.environ, {"PRS_DETECTION_MODEL": "yolo26n-walk",
                                     "PRS_DEPTH_FUSION": "host"}):
            runtime = InferenceRuntime()
        try:
            with patch.dict(os.environ, {"PRS_DETECTION_MODEL": "yolo26n",
                                         "PRS_DEPTH_FUSION": "gpu"}):
                contract = runtime.contract("test")
                self.assertEqual((contract["model"], contract["classes"]), ("yolo26n-walk", 87))
                self.assertEqual(contract["depth_fusion"], "host")
                self.assertEqual(runtime.detection_weights.name, "yolo26n-walk.pt")
                self.assertEqual(runtime.status()["models"]["detection"]["model"], "yolo26n-walk")
                self.assertEqual(runtime.factory.keywords["detection_model"], "yolo26n-walk")
                self.assertEqual(runtime.factory.keywords["depth_config"].fusion, "host")
        finally:
            asyncio.run(runtime.close())

    def test_host_fusion_mode_boundary(self):
        """Functionality: Reject unsupported fusion/mode combinations before loading models.
        Inputs: host fusion with geometric or temporal distance. Outputs: explicit ValueError.
        Logic: call the actual factory with frozen configs. Constraints: no altered B/C algorithm.
        """
        from integration.inference.services import load_detector
        from realtime.distance_config import DistanceConfig

        for mode in ("geometry", "temporal_depth"):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                load_detector(DistanceConfig(distance_mode=mode), DepthBackendConfig(fusion="host"))

    def test_launcher_verifies_selected_weights(self):
        """Functionality: Check the optional checkpoint even when original-model weights are absent.
        Inputs: inert walk bytes, manifest and geometric mode. Outputs: pass then rejection.
        Logic: use the actual launcher preflight, mutate bytes and require a digest failure.
        Constraints: fixture is never deserialized; no optional model substitutes for missing files.
        """
        from realtime.distance_config import DistanceConfig
        from tools.run_local_mvp import StartupError, verify_models

        manifest = json.loads((Path(__file__).resolve().parents[1]
                               / "models/yolo26n-walk.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "models").mkdir()
            weights = root / "models/yolo26n-walk.pt"
            weights.write_bytes(b"inert walk fixture")
            manifest["source_sha256"] = hashlib.sha256(weights.read_bytes()).hexdigest()
            profile = root / "models/yolo26n-walk.json"
            profile.write_text(json.dumps(manifest), encoding="utf-8")
            with patch("tools.run_local_mvp.ROOT", root), \
                    patch("core.detection_model.model_files", return_value=(weights, profile)), \
                    patch("realtime.distance_config.read_distance_config",
                          return_value=DistanceConfig(distance_mode="geometry")):
                verify_models()
                weights.write_bytes(b"changed weights")
                with self.assertRaises(StartupError):
                    verify_models()

    def test_launcher_explicit_interpreter(self):
        """Functionality: Keep runtime verification when selecting a prepared GPU interpreter.
        Inputs: explicit interpreter then a missing path. Outputs: selected probe or early failure.
        Logic: exercise actual preflight with I/O seams; missing selection never uses a default.
        Constraints: inert file is never executed; this is not CUDA readiness evidence.
        """
        from tools.run_local_mvp import StartupError, run

        with tempfile.TemporaryDirectory() as directory:
            interpreter = Path(directory) / "python.exe"
            interpreter.write_bytes(b"inert interpreter fixture")
            args = SimpleNamespace(inference_python=interpreter, check=True,
                                   web_port=8766, inference_port=8767)
            with patch("tools.run_local_mvp.python_path", return_value=interpreter) as default, \
                    patch("tools.run_local_mvp.inspect_environment",
                          return_value={"device": "fixture", "cuda_runtime": "fixture"}) as probe, \
                    patch("tools.run_local_mvp.verify_models"), \
                    patch("tools.run_local_mvp.verify_ports"), \
                    patch("tools.run_local_mvp.read_or_create_key") as key:
                run(args)
                default.assert_called_once_with(".venv")
                self.assertEqual(probe.call_args.args[0], interpreter.resolve())
                self.assertTrue(probe.call_args.kwargs["cuda"])
                key.assert_not_called()
                args.inference_python = Path(directory) / "missing.exe"
                probe.reset_mock()
                with self.assertRaises(StartupError):
                    run(args)
                probe.assert_not_called()

    def test_phone_launcher_explicit_interpreter(self):
        """Functionality: Preflight a selected phone GPU runtime before any SSH authentication.
        Inputs: existing inert interpreter then a missing path. Outputs: probe or early failure.
        Logic: execute real check-mode orchestration with dependency and port seams.
        Constraints: no CUDA or SSH calls; no fallback to the default interpreter.
        """
        from tools.run_phone_mvp import ROOT, StartupError, run

        with tempfile.TemporaryDirectory() as directory:
            interpreter = Path(directory) / "python.exe"
            interpreter.write_bytes(b"inert interpreter fixture")
            args = SimpleNamespace(inference_python=interpreter, check=True,
                                   inference_port=8767, log_dir=ROOT / ".venv/tmp/phone-mvp")
            with patch("tools.run_phone_mvp.python_path", return_value=interpreter) as default, \
                    patch("tools.run_phone_mvp.inspect_environment") as probe, \
                    patch("tools.run_phone_mvp.verify_models"), \
                    patch("tools.run_phone_mvp.verify_inference_port"), \
                    patch("tools.run_phone_mvp.connect_server") as connect:
                run(args)
                default.assert_called_once_with(".venv")
                self.assertEqual(probe.call_args.args[0], interpreter.resolve())
                self.assertTrue(probe.call_args.kwargs["cuda"])
                connect.assert_not_called()
                args.inference_python = Path(directory) / "missing.exe"
                probe.reset_mock()
                with self.assertRaises(StartupError):
                    run(args)
                probe.assert_not_called()

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
