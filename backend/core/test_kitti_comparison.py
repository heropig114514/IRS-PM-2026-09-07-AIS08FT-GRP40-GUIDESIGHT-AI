"""Responsibilities: Guard KITTI four-method reporting, calibration and independent-image state.
Implementation: Synthetic canonical frames, fake adapters/workers and temporary artifacts.
Related Modules: model_tests.unified_evaluation.kitti; object_distance.reporting.
Declaration Index: KittiComparisonTests; KittiComparisonTests.fixture;
KittiComparisonTests.test_calibration_and_targets; KittiComparisonTests.test_reset_every_image;
KittiComparisonTests.test_reset_every_image.predict; KittiComparisonTests.test_partial_failure;
KittiComparisonTests.test_partial_failure.launch; KittiComparisonTests.test_original_validation.
Variable Index: HAS_TORCH identifies the optional evaluation dataset loader dependency.
"""

import json
import tempfile
from importlib.util import find_spec
from pathlib import Path
from unittest import skipUnless
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from PIL import Image

from object_distance.config import TARGET, write_json

HAS_TORCH = find_spec("torch") is not None
if HAS_TORCH:
    from model_tests.unified_evaluation.kitti import (
        collect_metrics,
        evaluate_pass,
        load_dataset,
        make_frame,
        run,
    )


@skipUnless(HAS_TORCH, "Optional PyTorch evaluation dataset loader required")
class KittiComparisonTests(SimpleTestCase):
    """Functionality: Verify lifecycle and evaluation contracts without external models.
    Logic: independent expected camera values, boundary metrics and failed-method aggregation.
    Constraints: fake inference establishes orchestration only; real GPU smoke is separate.
    """

    def fixture(self):
        """Functionality: Create two unrelated KITTI frames with one target each.
        Inputs: None. Outputs: dataset mock with stable length and fingerprint.
        Logic: distinct image IDs and nonzero P2 translation. Constraints: no real training data.
        """
        dataset = MagicMock()
        dataset.records = [
            {
                "image_id": str(i),
                "image_path": f"{i}.png",
                "image_size": [100, 80],
                "p2": [700, 0, 50, 40, 0, 710, 40, 1, 0, 0, 1, 0.003],
                "objects": [{"class_name": "car", "bbox": [10, 10, 20, 30], "distance_m": 5}],
            }
            for i in range(2)
        ]
        dataset.__len__.return_value = 2
        dataset.fingerprint = "original-val"
        return dataset

    def test_calibration_and_targets(self):
        """Functionality: Preserve reference-z while extracting actual image fy.
        Inputs: P2 with translations and five-meter GT. Outputs: fy710 and unchanged GT.
        Logic: use left 3x3, not flattened wrong-column access. Constraints: reject invalid fy.
        """
        record = self.fixture().records[0]
        frame = make_frame(record)
        self.assertEqual(frame["camera_intrinsic"], [[700, 0, 50], [0, 710, 40], [0, 0, 1]])
        self.assertEqual(frame["objects"][0]["distance_gt_m"], 5)
        self.assertNotIn("annotation_id", record["objects"][0])
        record["p2"][5] = 0
        with self.assertRaises(ValueError):
            make_frame(record)

    def test_reset_every_image(self):
        """Functionality: Prevent temporal distance carryover across unrelated KITTI images.
        Inputs: two images, fake C, two warmups. Outputs: five resets and two scored depth calls.
        Logic: assert reset occurs immediately before every prediction; verify missing metrics.
        Constraints: cold/warm calls never enter scored frame counts or depth ratio.
        """
        adapter = MagicMock()
        adapter.identity = {"method": "c"}

        def predict(image, frame, *, bbox_mode, timestamp):
            """Functionality: Assert reset lifecycle and return one missing distance.
            Inputs: adapter frame. Outputs: valid slot without depth estimate and timing.
            Logic: call count equals reset count. Constraints: fake depth frame, no real inference.
            """
            self.assertEqual(adapter.reset.call_count, adapter.predict.call_count)
            self.assertEqual(timestamp, 0)
            return (
                [{**obj, "distance_m": None} for obj in frame["objects"]],
                {
                    "detection_ms": 1,
                    "distance_ms": 2,
                    "end_to_end_ms": 4,
                },
                True,
            )

        adapter.predict.side_effect = predict
        options = {
            "method": "c",
            "bbox_mode": "gt",
            "device": "cpu",
            "warmup": 2,
            "depth_interval": 3,
            "match_iou": 0.5,
            "limit": None,
        }
        with tempfile.TemporaryDirectory() as temporary:
            with patch(
                "model_tests.unified_evaluation.kitti.read_rgb",
                return_value=Image.new("RGB", (100, 80)),
            ):
                report = evaluate_pass(adapter, self.fixture(), Path(temporary), options)
        self.assertEqual(adapter.reset.call_count, 5)
        self.assertEqual(report["temporal"]["depth_calls"], 2)
        self.assertEqual(report["temporal"]["actual_depth_ratio"], 1)
        self.assertEqual(report["temporal"]["non_depth_frame_latency"]["frames"], 0)
        self.assertEqual(report["metrics"]["overall"]["missing_predictions"], 2)
        self.assertIsNone(report["distance_report"]["weighted_mae_m"])

    def test_partial_failure(self):
        """Functionality: Preserve failed method rows and refuse incomplete paired comparisons.
        Inputs: one failed C worker and three successful fake workers. Outputs: partial tables.
        Logic: missing C cannot silently reduce a four-method comparison to three.
        Constraints: output directory cannot be reused or overwritten.
        """
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            weights = root / "weights.pt"
            weights.write_bytes(b"fixture")
            options = {
                "method": "all",
                "bbox_mode": "predicted",
                "output": str(root / "out"),
                "e_checkpoint": str(weights),
                "detection_weights": str(weights),
                "depth_weights": str(weights),
            }

            def launch(selected):
                """Functionality: Write complete scalar fixture results except for C.
                Inputs: worker options. Outputs: worker exit code. Logic: mimic persisted artifacts.
                Constraints: no subprocess or checkpoint loading in arithmetic tests.
                """
                folder = Path(selected["pass_output"])
                if selected["method"] == "c":
                    write_json(folder / "result.json", {"status": "failed", "error": "fixture"})
                    return 1
                observations = [
                    {
                        "key": str(i),
                        "target_m": d,
                        "prediction_m": d + 1,
                        "class_name": "car",
                        "matched": True,
                        "depth_frame": True,
                    }
                    for i, d in enumerate((5, 10, 20, 40))
                ]
                write_json(
                    folder / "result.json", {"status": "complete", **collect_metrics(observations)}
                )
                (folder / "observations.jsonl").write_text(
                    "".join(json.dumps(o) + "\n" for o in observations)
                )
                return 0

            with patch(
                "model_tests.unified_evaluation.kitti.load_dataset", return_value=self.fixture()
            ):
                report = run(options, launch=launch)
                self.assertEqual(report["status"], "partial_failure")
                self.assertEqual(report["paired"]["predicted"]["status"], "unavailable")
                self.assertTrue(report["weights_unchanged"])
                self.assertIn(
                    "failed", (root / "out/comparison.csv").read_text(encoding="utf-8-sig")
                )
                self.assertTrue((root / "out/distance_bands.csv").is_file())
                with self.assertRaises(FileExistsError):
                    run(options, launch=launch)

    def test_original_validation(self):
        """Functionality: Refuse validation fingerprints that differ from E training provenance.
        Inputs: mocked dataset and original run metadata. Outputs: accept exact hash, reject change.
        Logic: validate before any model load. Constraints: no silently substituted SANPO target.
        """
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            options = {"processed": "fixture", "e_checkpoint": str(root / "best.pt")}
            write_json(
                root / "run.json",
                {"distance_target": TARGET, "dataset": {"val_manifest_sha256": "original-val"}},
            )
            dataset = self.fixture()
            with patch("model_tests.unified_evaluation.kitti.KittiObjects", return_value=dataset):
                self.assertIs(load_dataset(options), dataset)
                dataset.fingerprint = "changed"
                with self.assertRaises(ValueError):
                    load_dataset(options)
