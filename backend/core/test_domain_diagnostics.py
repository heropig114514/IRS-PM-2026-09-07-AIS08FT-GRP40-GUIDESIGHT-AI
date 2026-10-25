"""Responsibilities: Verify signed-error, camera-scale and fixed-checkpoint domain audits.
Implementation: Numerical fixtures with missing targets, sparse strata and explicit guard failures.
Related Modules: unified_evaluation.domain_diagnostics; object_distance.data/prepare.
Declaration Index: observation; DomainDiagnosticsTests;
DomainDiagnosticsTests.test_signed_bias_keeps_missing_denominator;
DomainDiagnosticsTests.test_bins_are_half_open_and_reject_invalid_targets;
DomainDiagnosticsTests.test_intrinsics_follow_actual_rounded_letterbox;
DomainDiagnosticsTests.test_kitti_center_conversion_keeps_z;
DomainDiagnosticsTests.test_composition_reports_unsupported_mass;
DomainDiagnosticsTests.test_box_effects_pair_identical_detected_targets;
DomainDiagnosticsTests.test_other_checkpoint_is_rejected;
DomainDiagnosticsTests.test_existing_and_nested_outputs_are_rejected.
Variable Index: None. Synthetic assertions do not establish cross-domain model accuracy.
"""

import json
import math
import tempfile
from pathlib import Path

from django.test import SimpleTestCase
from PIL import Image

from model_tests.unified_evaluation.domain_diagnostics import (
    analyze,
    box_effects,
    camera_parameters,
    distance_bin,
    error_summary,
    join_kitti,
    numeric_summary,
    standardize_composition,
    target_record,
)
from object_distance.data import prepare_image
from object_distance.prepare import parse_label


def observation(target=10.0, prediction=8.0, **overrides):
    """Functionality: Build a minimal valid signed-error audit observation.
    Inputs: target/prediction and named overrides. Outputs: owned row.
    Logic: explicit default identity/class/domain. Constraints: no dataset or inference dependency.
    """
    return {
        "target_m": target,
        "prediction_m": prediction,
        "matched": prediction is not None,
        "dataset": "kitti",
        "split": "val",
        "object_id": "one",
        "method": "e",
        "bbox_mode": "gt",
        "variant": "original",
        "class_name": "car",
        "distance_bin": distance_bin(target),
        **overrides,
    }


class DomainDiagnosticsTests(SimpleTestCase):
    """Functionality: Protect diagnostic arithmetic, comparison identity and original inputs.
    Logic: independently known numerical outcomes and failure cases.
    Constraints: does not change or exercise any production inference algorithm.
    """

    def test_signed_bias_keeps_missing_denominator(self):
        """Functionality: Distinguish cancellation from accuracy without hiding unavailable rows.
        Inputs: +/-2m errors and one missing value. Outputs: zero bias,2m MAE,2/3 coverage.
        Logic: compare exact independent arithmetic. Constraints: missing is not zero error.
        """
        result = error_summary(
            [observation(), observation(prediction=12), observation(prediction=None)]
        )
        self.assertEqual(result["bias_m"], 0)
        self.assertEqual(result["mae_m"], 2)
        self.assertEqual(result["rmse_m"], 2)
        self.assertEqual(result["coverage"], 2 / 3)
        self.assertEqual(result["underestimate_fraction"], 0.5)
        self.assertIsNone(error_summary([])["mae_m"])
        self.assertEqual(numeric_summary([None, float("nan")])["count"], 0)

    def test_bins_are_half_open_and_reject_invalid_targets(self):
        """Functionality: Keep diagnostic range boundaries explicit and exhaustive.
        Inputs: exact edges and invalid targets. Outputs: next-bin assignments or ValueError.
        Logic: assert half-open membership. Constraints: no evaluator bins are overwritten.
        """
        self.assertEqual(distance_bin(4.999), "0-5")
        self.assertEqual(distance_bin(5), "5-10")
        self.assertEqual(distance_bin(80), "80-100")
        self.assertEqual(distance_bin(154), "100+")
        for value in (-1, math.inf, math.nan):
            with self.assertRaises(ValueError):
                distance_bin(value)

    def test_intrinsics_follow_actual_rounded_letterbox(self):
        """Functionality: Avoid comparing raw focal lengths after unequal input resize scales.
        Inputs: KITTI-like1242x375 image and P2. Outputs: exact existing preprocessing transform.
        Logic: compare camera scaling with prepare_image and known transformed principal point.
        Constraints: y rounding cannot be replaced by ideal shared scalar width ratio.
        """
        frame = {
            "image_id": "one",
            "image_size": [1242, 375],
            "p2": [721.5, 0, 609.5, 44.8, 0, 721.5, 172.8, 0.2, 0, 0, 1, 0.003],
        }
        result = camera_parameters(frame, "kitti", "train")
        _, transform = prepare_image(Image.new("RGB", (1242, 375)))
        self.assertEqual(result["scale_y"], transform.scale_y)
        self.assertNotEqual(result["scale_x"], result["scale_y"])
        self.assertAlmostEqual(result["input_fy"], 721.5 * 193 / 375)
        self.assertAlmostEqual(result["input_cy"], 172.8 * 193 / 375 + 223)
        self.assertAlmostEqual(result["reference_to_optical_z_offset_m"], 0.003)

    def test_kitti_center_conversion_keeps_z(self):
        """Functionality: Reject a false bottom-versus-center explanation of camera-z error.
        Inputs: raw KITTI label and a geometric center shifted vertically by half height.
        Outputs: identical parsed z,with invalid altered z rejected.
        Logic: KITTI yaw is around camera y. Constraints: Euclidean range remains metadata only.
        """
        obj, _ = parse_label("Car 0 0 0 10 20 40 60 1.6 1.7 4 2 1.5 30 0", (100, 100))
        camera = camera_parameters(
            {"image_size": [100, 100], "p2": [50, 0, 50, 0, 0, 50, 50, 0, 0, 0, 1, 0]},
            "kitti",
            "val",
        )
        row = target_record(camera, obj, "one", height=1.6, center=[2, 0.7, 30])
        self.assertEqual(row["target_m"], 30)
        with self.assertRaisesRegex(ValueError, "camera z"):
            target_record(camera, obj, "one", height=1.6, center=[2, 0.7, 29])

    def test_composition_reports_unsupported_mass(self):
        """Functionality: Do not fabricate KITTI errors for unsupported nuScenes strata.
        Inputs: source bins with1m/3m errors,target counts1/3 plus one unseen class.
        Outputs: weighted2.5m reference on4/5 targets and one explicitly unsupported target.
        Logic: hand-calculated reweighting. Constraints: never fits prediction corrections.
        """
        source = [observation(8, 7), observation(25, 22)]
        target = [observation(8, 6)] + [observation(25, 20) for _ in range(3)]
        target.append(observation(8, 4, class_name="person"))
        result = standardize_composition(source, target)
        self.assertEqual(result["weighted_kitti_mae_m"], 2.5)
        self.assertEqual(result["represented_nuscenes_count"], 4)
        self.assertEqual(result["unsupported_nuscenes_count"], 1)

    def test_box_effects_pair_identical_detected_targets(self):
        """Functionality: Keep detection-selection bias out of same-target box comparisons.
        Inputs: a detected target and a difficult missed GT. Outputs: only the common target pair.
        Logic: use identity rather than independent group averages. Constraints: misses stay misses.
        """
        rows = [
            observation(),
            observation(100, 1, object_id="missed"),
            observation(prediction=9, bbox_mode="predicted"),
            observation(100, None, object_id="missed", bbox_mode="predicted"),
        ]
        result = box_effects(rows)[0]
        self.assertEqual(result["common_valid_count"], 1)
        self.assertEqual(result["gt_box_mae_m"], 2)
        self.assertEqual(result["predicted_box_mae_m"], 1)

    def test_other_checkpoint_is_rejected(self):
        """Functionality: Prevent historical smoke evaluations entering the current comparison.
        Inputs: mismatched evaluation checkpoint metadata. Outputs: explicit ValueError.
        Logic: check identity before reading predictions. Constraints: no fallback to old artifacts.
        """
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "gt_metrics.json").write_text(
                json.dumps({"checkpoint_sha256": "old", "evaluated_images": 1})
            )
            with self.assertRaisesRegex(ValueError, "different checkpoint"):
                join_kitti([], [{}], path, "current")

    def test_existing_and_nested_outputs_are_rejected(self):
        """Functionality: Protect original result trees from generated diagnostic artifacts.
        Inputs: existing or descendant output directory. Outputs: ValueError before dataset reads.
        Logic: resolved containment checks. Constraints: original file bytes are left untouched.
        """
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            for output in (path, path / "nested"):
                with self.assertRaisesRegex(ValueError, "outside"):
                    analyze(path, path, path, path, output)
