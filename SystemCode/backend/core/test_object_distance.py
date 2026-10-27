"""Responsibilities: Verify E data semantics, ROI geometry, gradients, freezing and checkpoints.
Implementation: Tiny actual KITTI-style files, independent metric expectations and optional real
YOLO.
Related Modules: object_distance.prepare/config/data/model/train/evaluate/predict.
Declaration Index: make_raw; ObjectDistanceDataTests;
ObjectDistanceDataTests.test_z_field_and_filters;
ObjectDistanceDataTests.test_reproducible_splits; ObjectDistanceDataTests.test_conversion_and_audit;
ObjectDistanceDataTests.test_configuration; ObjectDistanceDataTests.test_metrics_and_matching;
ObjectDistanceDataTests.test_default_preparation_policy;
ObjectDistanceVisionTests; ObjectDistanceVisionTests.setUpClass; ObjectDistanceVisionTests.setUp;
ObjectDistanceVisionTests.test_roundtrip_and_roi_coordinates;
ObjectDistanceVisionTests.test_forward_shared_and_frozen_gradients;
ObjectDistanceVisionTests.test_partial_feature_tuning;
ObjectDistanceVisionTests.test_empty_invalid_and_unsupported;
ObjectDistanceVisionTests.test_checkpoint_and_detector_identity;
ObjectDistanceVisionTests.test_incompatible_teammate_classes;
ObjectDistanceVisionTests.test_manifest_dataset_and_collate;
ObjectDistanceVisionTests.test_predicted_evaluation_missing_coverage;
ObjectDistanceVisionTests.test_no_depth_or_production_import;
ObjectDistanceVisionTests.test_training_artifacts; CoordinateHead; CoordinateHead.forward.
ObjectDistanceVisionTests.test_offline_empty_and_null;
ObjectDistanceVisionTests.test_external_comparison_target.
Variable Index: HAS_VISION — optional installed inference dependencies, not a CUDA availability
claim.
"""

import ast
import importlib.util
import tempfile
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch

from django.test import SimpleTestCase
from PIL import Image

from object_distance.config import ROOT, TARGET, ExperimentConfig, read_config
from object_distance.metrics import DistanceMetrics, match_objects
from object_distance.prepare import convert, inventory, main, parse_label, split_ids

HAS_VISION = all(importlib.util.find_spec(name) for name in ("torch", "torchvision", "ultralytics"))


def make_raw(root):
    """Functionality: Construct a complete tiny KITTI-shaped fixture without real dataset copies.
    Inputs: owned temp directory. Outputs: raw path. Logic: RGB PNGs, P2 and two supported labels.
    Constraints: synthetic fixture values do not establish trained distance accuracy.
    """
    raw = root / "raw"
    for split, count in (("training", 4), ("testing", 2)):
        for folder in ("image_2", "calib"):
            (raw / split / folder).mkdir(parents=True)
        if split == "training":
            (raw / split / "label_2").mkdir()
        for i in range(count):
            identity = f"{i:06}"
            Image.new("RGB", (160, 96), "gray").save(raw / split / "image_2" / f"{identity}.png")
            (raw / split / "calib" / f"{identity}.txt").write_text(
                "P2: 100 0 80 0 0 100 48 0 0 0 1 0\n"
            )
            if split == "training":
                (raw / split / "label_2" / f"{identity}.txt").write_text(
                    "Car 0 0 -1 20 10 100 80 1.5 1.6 4 1 2 15 0\n"
                    "Pedestrian 0 1 0 110 10 145 85 1.7 .5 .5 2 1 8 .2\n"
                    "Van 0 0 0 10 10 100 80 2 2 5 1 1 30 0\n"
                )
    return raw


class ObjectDistanceDataTests(SimpleTestCase):
    """Functionality: Guard camera-z targets, local-data ownership and fixed split semantics.
    Logic: independent field/metric expectations and actual temporary files.
    Constraints: these checks require no Torch, GPU, model downloads or training.
    """

    def test_z_field_and_filters(self):
        """Functionality: Read KITTI z exactly and separate invalid/unsupported object reasons.
        Inputs: distinct x/y/z/yaw fields and malformed variants. Outputs: z=23, correct filters.
        Logic: ensure Euclidean range and rotation_y cannot be mistaken for forward depth.
        Constraints: classes beyond Car/Pedestrian are intentionally not remapped.
        """
        line = "Car .1 2 -.5 1 2 99 70 1.5 1.7 4 3 7 23 -1.2"
        obj, reason = parse_label(line, (100, 80))
        self.assertIsNone(reason)
        self.assertEqual(obj["location_xyz_m"], [3, 7, 23])
        self.assertEqual(obj["distance_m"], 23)
        self.assertEqual(obj["distance_target"], TARGET)
        for replacement, expected in (
            (line.replace("Car", "Cyclist"), "unsupported_class:Cyclist"),
            (line.replace(" 23 ", " 0 "), "nonpositive_z"),
            (line.replace(" 23 ", " nan "), "nonfinite"),
            (line.replace("1 2 99 70", "99 2 1 70"), "invalid_bbox"),
            ("Car 1 2", "malformed_fields"),
        ):
            self.assertEqual(parse_label(replacement, (100, 80))[1], expected)

    def test_reproducible_splits(self):
        """Functionality: Keep seeded and provided split files disjoint and reproducible.
        Inputs: ten/7481 image IDs and an overlapping supplied split. Outputs: stable IDs or error.
        Logic: check full coverage/duplicates. Constraints: image splits do not imply scene
        isolation.
        """
        identities = [f"{i:06}" for i in range(10)]
        first = split_ids(identities, seed=42)
        self.assertEqual(first, split_ids(list(reversed(identities)), seed=42))
        self.assertFalse(set(first[0]) & set(first[1]))
        self.assertEqual(set(first[0]) | set(first[1]), set(identities))
        full_ids = [f"{i:06}" for i in range(7481)]
        train, val, provenance = split_ids(full_ids)
        self.assertEqual((len(train), len(val)), (5985, 1496))
        self.assertEqual((train, val, provenance), split_ids(list(reversed(full_ids))))
        self.assertFalse(set(train) & set(val))
        self.assertEqual(set(train) | set(val), set(full_ids))
        self.assertEqual((provenance["seed"], provenance["val_fraction"]), (42, 0.2))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "train.txt").write_text("\n".join(first[0]))
            (root / "val.txt").write_text("\n".join(first[1]))
            self.assertEqual(split_ids(identities, split_dir=root)[0], first[0])
            (root / "val.txt").write_text("\n".join(identities))
            with self.assertRaises(ValueError):
                split_ids(identities, split_dir=root)

    def test_default_preparation_policy(self):
        """Functionality: Align default conversion and training on the explicit80/20 policy.
        Inputs: default CLI and optional supplied split directory. Outputs: captured config.
        Logic: inspect conversion arguments and both config entry points without touching data.
        Constraints: historical supplied splits remain available only by explicit selection.
        """
        expected = ROOT / "model_tests/datasets/kitti/processed-80-20-seed42"
        with patch("object_distance.prepare.convert", return_value={}) as conversion:
            with patch("builtins.print"):
                main([])
                self.assertEqual(conversion.call_args.args[1], expected)
                self.assertEqual(conversion.call_args.kwargs["seed"], 42)
                self.assertEqual(conversion.call_args.kwargs["val_fraction"], 0.2)
                self.assertIsNone(conversion.call_args.kwargs["split_dir"])
                main(["--split-dir", "provided-splits", "--output", "custom-output"])
                self.assertEqual(conversion.call_args.kwargs["split_dir"], Path("provided-splits"))
                self.assertEqual(conversion.call_args.args[1], Path("custom-output"))
        self.assertEqual(Path(read_config().processed), expected)
        configured = read_config(ROOT / "object_distance/config.json")
        self.assertEqual(Path(configured.processed), expected)

    def test_conversion_and_audit(self):
        """Functionality: Produce complete manifests without overwriting raw or prior output.
        Inputs: four labeled/two unlabeled PNGs. Outputs:3/1 split,8 accepted objects and filters.
        Logic: full PNG verification and P2 checks; missing counterparts fail before conversion.
        Constraints: test labels remain absent and are not fabricated for metric evaluation.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = make_raw(root)
            report = convert(
                raw, root / "processed", seed=42, val_fraction=0.25, verify_images=True
            )
            self.assertEqual(report["splits"]["train"]["images"], 3)
            self.assertEqual(report["splits"]["val"]["objects"], 2)
            self.assertEqual(report["splits"]["train"]["filter_counts"]["unsupported_class:Van"], 3)
            self.assertEqual(report["raw_audit"]["testing"]["labels"], 0)
            with self.assertRaises(ValueError):
                convert(raw, root / "processed")
            (raw / "training/calib/000000.txt").unlink()
            with self.assertRaises(ValueError):
                inventory(raw)

    def test_configuration(self):
        """Functionality: Reject unsafe sizes/counts and preserve A/B/C default mode.
        Inputs: invalid E settings and production defaults. Outputs: explicit errors/default depth.
        Logic: independent configuration objects. Constraints: E never extends production choices.
        """
        for values in (
            {"batch_size": 0},
            {"image_size": 641},
            {"freeze_backbone": 1},
            {"learning_rate": -1},
            {"supported_classes": ["car"]},
            {"seed": -1},
        ):
            with self.assertRaises(ValueError):
                ExperimentConfig(**values)
        from realtime.distance_config import DEFAULT_DISTANCE_CONFIG

        self.assertEqual(DEFAULT_DISTANCE_CONFIG.distance_mode, "depth")
        self.assertEqual(read_config(device="cpu").batch_size, 2)

    def test_metrics_and_matching(self):
        """Functionality: Count missing GT predictions separately from conditional error metrics.
        Inputs: targets10/20/30 and predictions12/16/missing. Outputs:MAE3,RMSEsqrt10,AbsRel.2.
        Logic: independent arithmetic plus one-to-one same-class matching.
        Constraints: a perfect match of wrong class is rejected, not silently used for distance.
        """
        metrics = DistanceMetrics()
        for gt, pred in ((10, 12), (20, 16), (30, None)):
            metrics.add(gt, pred, "car")
        overall = metrics.summary()["overall"]
        self.assertEqual(overall["mae_m"], 3)
        self.assertAlmostEqual(overall["rmse_m"], 10**0.5)
        self.assertAlmostEqual(overall["abs_rel"], 0.2)
        self.assertAlmostEqual(overall["missing_prediction_ratio"], 1 / 3)
        gt = [{"class_name": "car", "bbox": [0, 0, 10, 10]}] * 2
        predictions = [
            {"class_name": "person", "bbox": [0, 0, 10, 10]},
            {"class_name": "car", "bbox": [0, 0, 10, 10]},
        ]
        self.assertEqual(match_objects(gt, predictions), {0: 1})


@skipUnless(HAS_VISION and (ROOT / "models/yolo26n.pt").is_file(), "Local vision weights required")
class ObjectDistanceVisionTests(SimpleTestCase):
    """Functionality: Exercise actual local YOLO and torchvision on CPU without external services.
    Logic: small real model forwards with synthetic images; CUDA training is separately recorded.
    Constraints: these tests prove code contracts, not pretrained distance accuracy or safety.
    """

    def test_offline_empty_and_null(self):
        """Functionality: Serialize empty detections and unsupported distance categories safely.
        Inputs: controlled detection boundary with actual shared features. Outputs: empty or null.
        Logic: exercise the real offline adapter after a counted model feature forward.
        Constraints: synthetic detection boundary does not establish model accuracy.
        """
        import torch

        from object_distance.data import prepare_image
        from object_distance.predict import run_frame

        _, features = self.model.extract(self.image)
        tensor, transform = prepare_image(Image.new("RGB", (128, 128)), 128)
        rows = torch.zeros((1, 300, 6))
        with patch.object(self.model, "extract", return_value=(rows, features)):
            objects, _ = run_frame(self.model, tensor, transform)
            self.assertEqual(objects, [])
            rows[0, 0] = torch.tensor([16, 16, 80, 80, 0.9, 16])
            objects, _ = run_frame(self.model, tensor, transform)
            self.assertEqual(objects[0]["class_name"], "dog")
            self.assertIsNone(objects[0]["distance_m"])
            self.assertEqual(objects[0]["distance_source"], "object_distance_head")

    def test_external_comparison_target(self):
        """Functionality: Score compatible prediction exports and reject mismatched semantics.
        Inputs: independent dataset/prediction fixture. Outputs: numeric score or explicit error.
        Logic: require IDs and the exact reference-z target before matching.
        Constraints: schema agreement alone does not validate physical calibration of A/B/C.
        """
        import json

        from object_distance.data import KittiObjects
        from object_distance.evaluate import evaluate_external

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            convert(make_raw(root), root / "processed", val_fraction=0.25)
            dataset = KittiObjects(root / "processed", "val", image_size=128)
            path = root / "predictions.jsonl"
            record = {
                "image_id": dataset.records[0]["image_id"],
                "distance_target": TARGET,
                "objects": dataset.records[0]["objects"],
            }
            path.write_text(json.dumps(record) + "\n")
            self.assertEqual(evaluate_external(dataset, path)["metrics"]["overall"]["mae_m"], 0)
            record["distance_target"] = "nearest_surface_m"
            path.write_text(json.dumps(record) + "\n")
            with self.assertRaisesRegex(ValueError, "incompatible target"):
                evaluate_external(dataset, path)

    @classmethod
    def setUpClass(cls):
        """Functionality: Load one real local detector for inference-runtime tests.
        Inputs: shipped model and small128px experiment config. Outputs: shared CPU fixture.
        Logic: fixed seed/one CPU thread. Constraints: no automatic downloads or CUDA requirement.
        """
        super().setUpClass()
        import torch

        from object_distance.config import seed_runtime
        from object_distance.model import ObjectDistanceModel

        cls.config = read_config(device="cpu", image_size=128, hidden_dim=16, roi_size=1, amp=False)
        seed_runtime(cls.config)
        cls.model = ObjectDistanceModel(cls.config)
        cls.initial_head = deepcopy(cls.model.distance_head.state_dict())
        cls.image = torch.rand(1, 3, 128, 128)
        cls.rois = torch.tensor([[0.0, 16, 16, 80, 80]])
        cls.classes = torch.tensor([0])

    def setUp(self):
        """Functionality: Isolate trainable-head test state while keeping one immutable detector.
        Inputs: stored head state. Outputs: reset gradients/head. Logic: strict reload.
        Constraints: no cross-test optimizer or feature-cache state.
        """
        self.model.distance_head.load_state_dict(self.initial_head)
        self.model.zero_grad(set_to_none=True)
        self.model.eval()

    def test_roundtrip_and_roi_coordinates(self):
        """Functionality: Verify rounded letterbox coordinates and actual ROI feature sampling.
        Inputs:1237x375 frame and independent feature ramp. Outputs:exact inverse and555.5 feature.
        Logic: aligned ROI at16..80 in128 input corresponds center5.5 at stride8.
        Constraints: validates coordinates numerically rather than mirroring the mapping formula.
        """
        import torch

        from object_distance.data import prepare_image
        from object_distance.model import ObjectDistanceModel

        _, transform = prepare_image(Image.new("RGB", (1237, 375)), 640)
        boxes = torch.tensor([[100.0, 20.0, 1000.0, 300.0]])
        self.assertTrue(
            torch.allclose(transform.to_original(transform.to_input(boxes)), boxes, atol=1e-4)
        )
        toy = ObjectDistanceModel.__new__(ObjectDistanceModel)
        torch.nn.Module.__init__(toy)
        toy.config = self.config
        toy.distance_head = CoordinateHead()
        axis = torch.arange(16, dtype=torch.float32)
        feature = (axis[None, :] + 100 * axis[:, None])[None, None]
        value = toy.regress((feature,), self.rois, self.classes, (128, 128))
        self.assertAlmostEqual(float(value[0]), 555.5)

    def test_forward_shared_and_frozen_gradients(self):
        """Functionality: Backpropagate into the head with exactly one shared detector forward.
        Inputs: one synthetic image/ROI and target8m. Outputs:correct shapes,head updates,frozen
        YOLO.
        Logic: compare full detector tensors and counted forward; unchanged raw detection tensor.
        Constraints: eval-mode detector BN buffers must also remain identical after optimizer step.
        """
        import torch

        from object_distance.train import detector_state_digest

        self.model.train()
        before = detector_state_digest(self.model)
        optimizer = torch.optim.SGD(
            [p for p in self.model.parameters() if p.requires_grad], lr=0.01
        )
        with patch.object(
            self.model.detector, "forward", wraps=self.model.detector.forward
        ) as forward:
            detections, distance = self.model(self.image, self.rois, self.classes)
            self.assertEqual(forward.call_count, 1)
        self.assertEqual(tuple(detections.shape), (1, 300, 6))
        self.assertEqual(tuple(distance.shape), (1,))
        self.assertTrue(torch.isfinite(distance).all() and (distance > 0).all())
        torch.nn.functional.smooth_l1_loss(distance, torch.tensor([8.0])).backward()
        self.assertTrue(
            any(
                p.grad is not None and p.grad.abs().sum() > 0
                for p in self.model.distance_head.parameters()
            )
        )
        self.assertTrue(all(p.grad is None for p in self.model.detector.parameters()))
        optimizer.step()
        self.assertEqual(before, detector_state_digest(self.model))
        with torch.no_grad():
            self.assertTrue(torch.equal(detections, self.model.detector(self.image)))

    def test_partial_feature_tuning(self):
        """Functionality: Keep explicit last-feature tuning differentiable with frozen Detect head.
        Inputs: config unfreezing one final feature module. Outputs:feature gradients, no Detect
        grad.
        Logic: actual YOLO forward/ROI loss. Constraints: this is distance-supervised tuning only.
        """

        from object_distance.model import ObjectDistanceModel

        model = ObjectDistanceModel(replace(self.config, unfreeze_last_layers=1)).train()
        _, distances = model(self.image, self.rois, self.classes)
        distances.sum().backward()
        self.assertTrue(
            any(
                p.grad is not None and p.grad.abs().sum() > 0
                for p in model.detector.model[-2].parameters()
            )
        )
        self.assertTrue(all(not p.requires_grad for p in model.detector.model[-1].parameters()))
        self.assertFalse(model.detector.training)

    def test_empty_invalid_and_unsupported(self):
        """Functionality: Preserve slots for missing/invalid/unsupported ROIs without crashing.
        Inputs: zero ROIs, reversed box, unknown class, NaN coordinate. Outputs:empty or NaN
        sentinel.
        Logic: actual captured features. Constraints: boundary serialization converts sentinels to
        null.
        """
        import torch

        _, features = self.model.extract(self.image)
        empty = self.model.regress(
            features, torch.empty((0, 5)), torch.empty(0, dtype=torch.long), (128, 128)
        )
        self.assertEqual(empty.numel(), 0)
        invalid = torch.tensor(
            [[0.0, 80, 16, 16, 80], [0, 16, 16, 80, 80], [0, float("nan"), 16, 80, 80]]
        )
        result = self.model.regress(features, invalid, torch.tensor([0, -1, 0]), (128, 128))
        self.assertTrue(torch.isnan(result).all())

    def test_checkpoint_and_detector_identity(self):
        """Functionality: Restore identical distance predictions and reject changed detector bytes.
        Inputs: actual model checkpoint and a mismatched detector file. Outputs:equal result/error.
        Logic: save/load complete strict tensor state, target/feature/base-weight fingerprints.
        Constraints: an old head is not silently transferred to a teammate's new weights.
        """
        import torch

        from object_distance.model import load_checkpoint, save_checkpoint

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "head.pt"
            save_checkpoint(path, self.model)
            restored, _ = load_checkpoint(path, device="cpu")
            with torch.no_grad():
                self.assertTrue(
                    torch.equal(
                        self.model(self.image, self.rois, self.classes)[1],
                        restored(self.image, self.rois, self.classes)[1],
                    )
                )
            changed = Path(directory) / "different.pt"
            changed.write_bytes(b"different base weights")
            with self.assertRaisesRegex(ValueError, "Detector weights changed"):
                load_checkpoint(path, detection_weights=changed, device="cpu")

    def test_incompatible_teammate_classes(self):
        """Functionality: Fail explicitly when a teammate changes the required class mapping.
        Inputs: cloned actual YOLO with corrupted class0 label. Outputs:compatibility ValueError.
        Logic: patch deserialization only. Constraints: this fixture is not a new trained
        checkpoint.
        """
        from object_distance.model import ObjectDistanceModel

        detector = deepcopy(self.model.detector)
        detector.names[0] = "different_class"
        with (
            patch("ultralytics.YOLO", return_value=SimpleNamespace(model=detector)),
            self.assertRaisesRegex(ValueError, "Incompatible detector"),
        ):
            ObjectDistanceModel(self.config)

    def test_manifest_dataset_and_collate(self):
        """Functionality: Decode/pack real manifest fixtures and reject changed split content.
        Inputs: converted tiny KITTI data. Outputs:batch/image/ROI shapes and integrity rejection.
        Logic: real PIL/Torch loader. Constraints: no RGB labels or depth labels interchanged.
        """
        from object_distance.data import KittiObjects, collate_objects

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            convert(make_raw(root), root / "processed", val_fraction=0.25)
            dataset = KittiObjects(root / "processed", "train", image_size=128)
            batch = collate_objects([dataset[0], dataset[1]])
            self.assertEqual(tuple(batch["images"].shape), (2, 3, 128, 128))
            self.assertEqual(tuple(batch["rois"].shape), (4, 5))
            self.assertEqual(batch["distances"].tolist(), [15.0, 8.0, 15.0, 8.0])
            with (root / "processed/train.jsonl").open("a") as stream:
                stream.write("\n")
            with self.assertRaises(ValueError):
                KittiObjects(root / "processed", "train")

    def test_predicted_evaluation_missing_coverage(self):
        """Functionality: Count undetected GTs and keep GT-slot metrics separate from predicted
        mode.
        Inputs: two GTs and one correct predicted box. Outputs:50% missing, one matched error.
        Logic: use actual evaluation aggregator. Constraints: no claimed full-system precision from
        GT.
        """
        from object_distance.evaluate import score_frame

        gt = [
            {"class_name": "car", "bbox": [0, 0, 10, 10], "distance_m": 10},
            {"class_name": "person", "bbox": [20, 20, 30, 30], "distance_m": 20},
        ]
        prediction = [{"class_name": "car", "bbox": [0, 0, 10, 10], "distance_m": 12}]
        metrics = DistanceMetrics()
        self.assertEqual(score_frame(metrics, gt, prediction, "predicted", 0.5), (1, 0))
        self.assertEqual(metrics.summary()["overall"]["missing_prediction_ratio"], 0.5)
        self.assertEqual(metrics.summary()["overall"]["mae_m"], 2)

    def test_no_depth_or_production_import(self):
        """Functionality: Keep E source independent from depth and original CUDA Graph runtime.
        Inputs: actual Python ASTs. Outputs:no DepthEstimator/GraphRunner/production distance
        import.
        Logic: inspect maintained module imports. Constraints: actual CUDA runs are recorded
        separately.
        """
        for path in (ROOT / "object_distance").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotIn(
                        node.module, {"realtime.depth", "realtime.detector", "zerogpu.runtime"}
                    )

    def test_training_artifacts(self):
        """Functionality: Execute one real CPU optimizer step and persist reviewable
        loss/checkpoints.
        Inputs: tiny converted dataset and explicit1-step/128px smoke config. Outputs:complete run.
        Logic: production training/validation code. Constraints: not a KITTI accuracy experiment.
        """
        from object_distance.train import train

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            convert(make_raw(root), root / "processed", val_fraction=0.25)
            report = train(
                replace(self.config, processed=str(root / "processed"), epochs=1),
                root / "run",
                max_steps=1,
            )
            self.assertEqual(report["status"], "complete")
            self.assertEqual(report["steps"], 1)
            self.assertEqual(report["optimizer_updates"], 1)
            self.assertEqual(report["amp_overflow_skipped_updates"], 0)
            self.assertTrue(report["detector_state_unchanged"])
            for name in ("loss.csv", "history.jsonl", "run.json", "best.pt", "last.pt"):
                self.assertTrue((root / "run" / name).is_file())


if HAS_VISION:
    import torch

    class CoordinateHead(torch.nn.Module):
        """Functionality: Observe a pooled coordinate ramp without another model forward.
        Logic: return first ROI feature. Constraints: numerical test helper, not a trained
        predictor.
        """

        def forward(self, values):
            """Functionality: Expose the independent ROI expected value.
            Inputs: concatenated features. Outputs:first scalar per ROI.
            Logic: slicing only. Constraints: used only for coordinate mapping verification.
            """
            return values[:, 0]
