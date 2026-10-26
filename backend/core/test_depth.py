"""Responsibilities: Verify metric ROI fusion, model lifetime, alignment and failure boundaries.
Implementation: Controlled NumPy/Torch maps and explicit model/graph substitutes; no downloads.
Related Modules: realtime.depth/detector/protocol; zerogpu.live_worker; core.detector.
Declaration Index: DepthTests; DepthTests.test_median_and_invalid_values;
DepthTests.test_roi_clipping_and_invalid_boxes; DepthTests.test_invalid_maps;
DepthTests.test_fusion_and_shape_contract; DepthTests.test_missing_and_wrong_model;
DepthTests.test_resident_integration; DepthTests.test_rendered_labels;
DepthTests.test_full_detection_packet; DepthTests.test_depth_deployment_closure;
DepthTests.test_tensor_median; DepthTests.test_model_reuse_alignment_and_failures;
DepthTests.test_worker_depth_handoff; DepthTests.test_depth_experiment_options.
Variable Index: HAS_TORCH — optional inference-runtime availability; test doubles do not prove
pretrained accuracy or actual CUDA inference. GPU tensor checks run only with actual CUDA.
"""

import ast
import hashlib
import importlib.util
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import MagicMock, patch

import numpy as np
from django.test import SimpleTestCase
from PIL import Image

from core.detector import DetectionError, annotate
from core.test_frames import jpeg
from realtime.depth import DepthEstimator, fuse_object_depth, get_object_distance
from realtime.detector import ResidentDetector
from realtime.protocol import pack_result, unpack_result

HAS_TORCH = importlib.util.find_spec("torch") is not None


class DepthTests(SimpleTestCase):
    """Functionality: Exercise numerical, resident-inference and wire compatibility contracts.
    Logic: independent expected medians and model call counts; optional real tensor operations.
    Constraints: synthetic predictions are never reported as pretrained depth/accuracy evidence.
    """

    def test_median_and_invalid_values(self):
        """Functionality: Filter invalid samples and average the two even central values.
        Inputs: known depth plane/outlier/invalids. Outputs: exact metric median and None.
        Logic: independent2/4 central samples give3m. Constraints: no object-size heuristic.
        """
        depth = np.array([[np.nan, np.inf, 0, -1], [1, 2, 4, 100]], dtype=np.float32)
        self.assertEqual(get_object_distance(depth, [0, 0, 4, 2]), 3.0)
        self.assertIsNone(get_object_distance(depth, [0, 0, 4, 1]))
        self.assertEqual(get_object_distance(depth, [0, 1, 3, 2]), 2.0)

    def test_roi_clipping_and_invalid_boxes(self):
        """Functionality: Verify half-open fractional clipping and reject empty/reversed ROIs.
        Inputs:2x3 known plane, outside/nonfinite/malformed boxes. Outputs: median or None.
        Logic: floor/ceil covers partial boundary pixels. Constraints: never swap bad coordinates.
        """
        depth = np.array([[1, 3, 9], [2, 4, 10]], dtype=np.float32)
        self.assertEqual(get_object_distance(depth, [-10, -10, 10, 10]), 3.5)
        self.assertEqual(get_object_distance(depth, [0.5, 0.5, 1.5, 1.5]), 2.5)
        for bbox in ([3, 0, 5, 2], [-5, -3, -1, -1], [1, 1, 1, 2], [2, 0, 1, 2],
                     [0, 0, np.nan, 2], [0, 0, np.inf, 2], [0, 1], None, "bad"):
            with self.subTest(bbox=bbox):
                self.assertIsNone(get_object_distance(depth, bbox))

    def test_invalid_maps(self):
        """Functionality: Distinguish malformed maps from valid maps with no available ROI.
        Inputs: empty/batched/nonfloating/complex maps. Outputs: stable depth_map_size error.
        Logic: actual shape/type validation. Constraints: no squeezing or numerical substitution.
        """
        for depth in (np.empty((0, 2)), np.ones(2), np.ones((1, 2, 2)),
                      np.ones((2, 2), dtype=int), np.ones((2, 2), dtype=complex), None):
            with self.subTest(shape=getattr(depth, "shape", None)):
                with self.assertRaises(DetectionError) as caught:
                    get_object_distance(depth, [0, 0, 1, 1])
                self.assertEqual(caught.exception.code, "depth_map_size")

    def test_fusion_and_shape_contract(self):
        """Functionality: Preserve box fields and expose meters/null without mutating input.
        Inputs: two boxes and original-frame depth. Outputs: canonical aliases, null and mismatch.
        Logic: one finite ROI and one invalid ROI. Constraints: never scale padded maps blindly.
        """
        boxes = [{"xyxy": [0, 0, 2, 1], "label": "person", "score": 0.93, "class_id": 0},
                 {"xyxy": [0, 1, 2, 2], "label": "car", "score": 0.8, "class_id": 2}]
        depth = np.array([[2, 4], [np.nan, 0]], dtype=np.float32)
        fused = fuse_object_depth(depth, boxes, (2, 2))
        self.assertEqual(fused[0]["distance_m"], 3.0)
        self.assertEqual(fused[0]["class_name"], "person")
        self.assertEqual(fused[0]["confidence"], 0.93)
        self.assertEqual(fused[0]["bbox"], boxes[0]["xyxy"])
        self.assertIsNone(fused[1]["distance_m"])
        self.assertNotIn("distance_m", boxes[0])
        self.assertEqual(fuse_object_depth(depth, [], (2, 2)), [])
        with self.assertRaises(DetectionError) as caught:
            fuse_object_depth(depth, boxes, (3, 2))
        self.assertEqual(caught.exception.code, "depth_map_size")

    def test_missing_and_wrong_model(self):
        """Functionality: Fail before downloader access for missing weights; reject wrong tasks.
        Inputs: missing path/local placeholder and explicit fake SDK. Outputs: stable error codes.
        Logic: verify YOLO constructor count and task/version checks. Constraints: no real model.
        """
        loader = MagicMock(return_value=SimpleNamespace(task="detect"))
        sdk = SimpleNamespace(__version__="8.4.171", YOLO=loader)
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, ultralytics=sdk):
            path = Path(directory) / "yolo26n-depth.pt"
            with self.assertRaises(DetectionError) as caught:
                DepthEstimator(path, "8.4.171")
            self.assertEqual(caught.exception.code, "depth_model_missing")
            loader.assert_not_called()
            path.write_bytes(b"explicit test placeholder")
            with self.assertRaises(DetectionError) as caught:
                DepthEstimator(path, "8.4.171")
            self.assertEqual(caught.exception.code, "depth_model_load")
            loader.assert_called_once()
            with self.assertRaises(DetectionError):
                DepthEstimator(path, "wrong-version")
            loader.assert_called_once()

    def test_resident_integration(self):
        """Functionality: Fuse the actual shared predict path while keeping detection output fixed.
        Inputs: deterministic graph, actual JPEG codec and depth substitute. Outputs:2 fused frames,
        same legacy boxes/raw hash, additive depth subtimers, one graph construction and failure.
        Logic: compare enabled/disabled on identical predictions. Constraints: no CUDA model proof.
        """
        rows = np.zeros((1, 300, 6), dtype=np.float32)
        rows[0, 0] = [0, 0, 640, 640, 0.93, 0]
        runner = MagicMock(return_value=(rows, 1.0))
        runner.setup_ms, runner.forward_event_ms, runner.evidence = 2.0, 0.5, {}
        resident = object.__new__(ResidentDetector)
        resident.model, resident.runner = None, None
        resident.tensor = np.empty((1, 3, 640, 640), dtype=np.float32)
        resident.execution_config, resident.precision, resident.backend = {}, "FP32", "test CUDA"
        resident.manifest = {"names": ["person"] * 80, "source_sha256": "a" * 64}
        depth = MagicMock(model_name="yolo26n-depth")
        depth.predict.return_value = np.full((80, 120), 2.4, dtype=np.float32)
        resident.depth_estimator = depth
        with patch("realtime.detector.GraphRunner", return_value=runner) as factory:
            first, rendered = resident.predict(jpeg())
            second, _ = resident.predict(jpeg())
            self.assertAlmostEqual(first["boxes"][0]["distance_m"], 2.4, places=6)
            self.assertEqual(first["depth_units"], "m")
            self.assertAlmostEqual(first["depth_ms"], first["decode_boxes_ms"]
                                   + first["depth_predict_ms"] + first["depth_fusion_ms"])
            self.assertGreaterEqual(first["depth_fusion_ms"], 0)
            self.assertEqual(first["jpeg_sha256"], hashlib.sha256(rendered).hexdigest())
            self.assertEqual(second["graph_setup_ms"], 0)
            factory.assert_called_once()
            self.assertEqual(depth.predict.call_count, 2)
            self.assertEqual(depth.predict.call_args.args[0].size, (120, 80))
            resident.depth_estimator = None
            original, _ = resident.predict(jpeg())
            self.assertEqual(original["raw_sha256"], first["raw_sha256"])
            self.assertNotIn("depth_model", original)
            self.assertNotIn("depth_fusion_ms", original)
            self.assertEqual(original["boxes"][0], {key: first["boxes"][0][key]
                             for key in ("xyxy", "score", "class_id", "label")})
            resident.depth_estimator = depth
            depth.predict.side_effect = DetectionError(
                "depth_inference_failed", "Test failure", 502)
            with self.assertRaises(DetectionError):
                resident.predict(jpeg())

    def test_rendered_labels(self):
        """Functionality: Show metric labels and N/A without changing detection-only labels.
        Inputs: same box with present/null/absent distance. Outputs: exact text passed to renderer.
        Logic: capture drawing calls; real JPEG encoding. Constraints: no OCR/model inference.
        """
        box = {"xyxy": [10, 30, 100, 70], "label": "person", "score": 0.93}
        for extra, expected in (({}, "person 0.93"), ({"distance_m": 2.4}, "person 0.93 | 2.4 m"),
                                ({"distance_m": None}, "person 0.93 | N/A")):
            with patch("PIL.ImageDraw.Draw") as drawing:
                drawing.return_value.textbbox.return_value = (0, 0, 60, 14)
                annotate(Image.new("RGB", (120, 80)), [{**box, **extra}])
                self.assertEqual(drawing.return_value.text.call_args.args[1], expected)

    def test_full_detection_packet(self):
        """Functionality: Carry300 fused boxes through the bounded real binary envelope.
        Inputs: maximal box count/full-precision coordinates. Outputs: intact objects/JPEG.
        Logic: test bounded fused metadata/legacy aliases. Constraints: no network/GPU assertion.
        """
        box = {"xyxy": [12.34567890123456, 2.34567890123456, 99.34567890123456, 75.34567890123456],
               "label": "traffic light", "score": 0.9312345678901234, "class_id": 9}
        fused = fuse_object_depth(np.full((80, 120), 2.4, dtype=np.float32), [box] * 300, (120, 80))
        raw = jpeg()
        metadata = {"index": 0, "boxes": fused, "jpeg_sha256": hashlib.sha256(raw).hexdigest()}
        packet = pack_result(metadata, raw)
        self.assertGreater(len(packet), 64 * 1024)
        self.assertEqual(unpack_result(packet, 0), (metadata, raw))

    def test_depth_deployment_closure(self):
        """Functionality: Include the newly imported depth module in the HF source bundle.
        Inputs: actual publisher AST. Outputs: exact source/destination mapping assertion.
        Logic: read literal allowlist without publisher import. Constraints: no uploads/HF calls.
        """
        root = Path(__file__).resolve().parents[1]
        module = ast.parse((root / "tools/deploy_space.py").read_text(encoding="utf-8"))
        files = next(ast.literal_eval(node.value) for node in module.body
                     if isinstance(node, ast.Assign)
                     and any(isinstance(target, ast.Name) and target.id == "FILES"
                             for target in node.targets))
        self.assertEqual(files["realtime/depth.py"], "realtime/depth.py")

    @skipUnless(HAS_TORCH, "Torch only belongs to the inference runtime")
    def test_tensor_median(self):
        """Functionality: Verify CPU/CUDA tensor medians agree with the statistical median.
        Inputs: known even/odd/invalid maps on available devices. Outputs:3m/None and fused values.
        Logic: actual Torch operations, including empty lists. Constraints: no model accuracy.
        """
        import torch

        devices = ["cpu", "cuda"] if torch.cuda.is_available() else ["cpu"]
        box = {"xyxy": [0, 0, 4, 2], "label": "person", "score": 0.93, "class_id": 0}
        for device in devices:
            depth = torch.tensor([[np.nan, np.inf, 0, -1], [1, 2, 4, 100]], device=device)
            self.assertEqual(get_object_distance(depth, box["xyxy"]), 3.0)
            self.assertEqual(get_object_distance(depth, [0, 1, 3, 2]), 2.0)
            self.assertIsNone(get_object_distance(depth, [0, 0, 4, 1]))
            self.assertIsNone(get_object_distance(depth, [9, 9, 10, 10]))
            self.assertEqual(fuse_object_depth(depth, [box], (4, 2))[0]["distance_m"], 3.0)
            self.assertEqual(fuse_object_depth(depth, [], (4, 2)), [])

    @skipUnless(HAS_TORCH, "Torch only belongs to the inference runtime")
    def test_model_reuse_alignment_and_failures(self):
        """Functionality: Check one load/activation, same-frame GPU resizing and explicit failures.
        Inputs: local placeholder/fake YOLO with actual CUDA maps. Outputs: resident counts/errors.
        Logic: exercise public estimator against original-shape mismatch and failed predictions.
        Constraints: requires actual CUDA; synthetic model does not validate real depth weights.
        """
        import torch

        if not torch.cuda.is_available():
            self.skipTest("CUDA required for estimator device contract")
        model = MagicMock(task="depth")
        loader = MagicMock(return_value=model)
        sdk = SimpleNamespace(__version__="8.4.171", YOLO=loader)
        result = SimpleNamespace(orig_shape=(4, 6),
                                 depth=SimpleNamespace(data=torch.tensor([[1., 2., 3.],
                                                                         [4., 5., 6.]],
                                                                        device="cuda")))
        model.predict.return_value = [result]
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, ultralytics=sdk):
            path = Path(directory) / "yolo26n-depth.pt"
            path.write_bytes(b"explicit test placeholder")
            estimator = DepthEstimator(path, "8.4.171")
            image = Image.new("RGB", (6, 4))
            with self.assertRaises(DetectionError):
                estimator.predict(image)
            estimator.activate()
            estimator.activate()
            model.to.assert_called_once_with("cuda")
            for _ in range(2):
                aligned = estimator.predict(image)
                self.assertEqual(aligned.shape, (4, 6))
                torch.testing.assert_close(aligned, result.depth.data.repeat_interleave(2, 0)
                                           .repeat_interleave(2, 1))
            loader.assert_called_once()
            self.assertEqual(model.predict.call_count, 2)
            self.assertIs(model.predict.call_args.kwargs["source"], image)
            self.assertEqual(model.predict.call_args.kwargs["imgsz"], 768)
            self.assertIs(model.predict.call_args.kwargs["half"], False)
            experimental = DepthEstimator(path, "8.4.171", image_size=640, half=True)
            experimental.activate()
            aligned = experimental.predict(image)
            self.assertEqual(aligned.shape, (4, 6))
            self.assertEqual(model.predict.call_args.kwargs["imgsz"], 640)
            self.assertIs(model.predict.call_args.kwargs["half"], True)
            result.orig_shape = (6, 4)
            with self.assertRaises(DetectionError) as caught:
                estimator.predict(image)
            self.assertEqual(caught.exception.code, "depth_map_size")
            result.orig_shape, result.depth = (4, 6), None
            with self.assertRaises(DetectionError):
                estimator.predict(image)
            model.predict.side_effect = RuntimeError("private SDK failure")
            with self.assertRaises(DetectionError) as caught:
                estimator.predict(image)
            self.assertEqual(caught.exception.code, "depth_inference_failed")
            self.assertNotIn("private", str(caught.exception))
            estimator.active = False
            with patch("torch.cuda.is_available", return_value=False):
                with self.assertRaises(DetectionError) as caught:
                    estimator.activate()
                self.assertEqual(caught.exception.code, "depth_inference_failed")

    def test_depth_experiment_options(self):
        """Functionality: Reject malformed depth-only options before model loading.
        Inputs: invalid sizes and precision flags. Outputs: explicit configuration errors.
        Logic: validate integer stride multiples and boolean precision. Constraints: no downloads.
        """
        for options in ({"image_size": 0}, {"image_size": 641}, {"image_size": True},
                        {"half": "true"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                DepthEstimator("unused.pt", "unused", **options)

    def test_worker_depth_handoff(self):
        """Functionality: Pass the loaded estimator once to the actual live worker factory.
        Inputs: explicit synthetic loopback handoffs/factory. Outputs: exact keyword and one end.
        Logic: stop before any frame; no model construction in frame loop.
        Constraints: no sockets, HF quota or actual ZeroGPU allocation.
        """
        from zerogpu.live_worker import run_worker

        depth, factory = object(), MagicMock()
        with patch("zerogpu.live_worker.exchange", side_effect=[(200, b""), (410, b""),
                                                               (200, b"")]):
            result = run_worker("a" * 64, None, None, factory=factory, config={},
                                depth_estimator=depth)
        factory.assert_called_once_with(None, None, {}, depth_estimator=depth)
        self.assertEqual(result["frames"], 0)
