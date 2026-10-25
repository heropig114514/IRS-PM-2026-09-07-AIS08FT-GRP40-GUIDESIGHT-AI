"""Responsibilities: Verify fixed-standard coverage diagnosis and independent geometry checks.
Implementation: Synthetic candidate graphs and actual official-Devkit temporary fixtures.
Related Modules: unified_evaluation.diagnostics; existing original match_objects is unchanged.
Declaration Index: geometry_row; DiagnosticTests; DiagnosticTests.test_independent_iou;
DiagnosticTests.test_cardinality_audit_does_not_replace_greedy;
DiagnosticTests.test_eligibility_boundaries; DiagnosticTests.test_loss_attribution;
DiagnosticTests.test_original_and_subset_denominators; DiagnosticTests.test_distributions;
DiagnosticGeometryTests; DiagnosticGeometryTests.test_official_geometry_and_cached_guard;
DiagnosticGeometryTests.test_new_output_guard.
Variable Index: POLICY — fixed descriptive subset; HAS_DEVKIT — optional official SDK availability.
"""

import importlib.util
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest import skipUnless

from django.test import SimpleTestCase

from core.test_unified_evaluation import make_nuscenes
from model_tests.unified_evaluation.dataset import load_records, prepare
from model_tests.unified_evaluation.diagnostics import (
    audit_geometry,
    diagnose,
    diagnose_matches,
    distribution,
    eligible,
    independent_iou,
    maximum_cardinality,
    summarize_rows,
)
from object_distance.metrics import match_objects

POLICY = {
    "min_visibility_token": 3,
    "min_input_width_px": 8,
    "min_input_height_px": 16,
    "min_retained_bbox_fraction": 0.5,
    "sensitivity_input_heights_px": [0, 8, 16, 32],
}
HAS_DEVKIT = importlib.util.find_spec("nuscenes") is not None


def geometry_row():
    """Functionality: Build a complete descriptive GT fixture without real-data assumptions.
    Inputs: None. Outputs: one eligible40x40px car at10m.
    Logic: positive finite geometry and explicit visibility. Constraints: no inference or GPU calls.
    """
    return {
        "frame_id": "frame",
        "annotation_id": "annotation",
        "class_name": "car",
        "bbox": [0, 0, 100, 100],
        "visibility_token": "4",
        "input_width_px": 40,
        "input_height_px": 40,
        "retained_bbox_fraction": 1,
        "policy_eligible": True,
        "policy_exclusion_reasons": [],
        "camera_z_m": 10,
        "potential_foreground_occluder": None,
    }


class DiagnosticTests(SimpleTestCase):
    """Functionality: Keep attribution factual and preserve original evaluation denominators.
    Logic: known IoU/assignment examples and disjoint loss categories.
    Constraints: synthetic tests establish diagnosis contracts,not real detection accuracy.
    """

    def test_independent_iou(self):
        """Functionality: Check independent continuous-box arithmetic.
        Inputs: identical,disjoint and half-overlap squares. Outputs:1,0,1/7.
        Logic: independent expected areas. Constraints: no inclusive pixel offset.
        """
        self.assertEqual(independent_iou([0, 0, 10, 10], [0, 0, 10, 10]), 1)
        self.assertEqual(independent_iou([0, 0, 10, 10], [20, 20, 30, 30]), 0)
        self.assertAlmostEqual(independent_iou([0, 0, 10, 10], [5, 5, 15, 15]), 1 / 7)

    def test_cardinality_audit_does_not_replace_greedy(self):
        """Functionality: Detect greedy cardinality loss without changing official assignments.
        Inputs:fixed0.5 graph with one flexible and one constrained prediction. Outputs:1 versus2.
        Logic: original greedy keeps its result; independent augmenting audit finds another path.
        Constraints: diagnostic maximum never enters official coverage or matching.
        """
        gt = [{"class_name": "car", "bbox": box} for box in ([0, 0, 10, 10], [2, 0, 12, 10])]
        predictions = [
            {"class_name": "car", "bbox": box} for box in ([1, 0, 11, 10], [0, 0, 6, 10])
        ]
        before = match_objects(gt, predictions, 0.5)
        self.assertEqual(len(before), 1)
        self.assertEqual(maximum_cardinality(gt, predictions, 0.5), 2)
        self.assertEqual(before, match_objects(gt, predictions, 0.5))

    def test_eligibility_boundaries(self):
        """Functionality: Declare inclusive subset edges and retain every exclusion reason.
        Inputs:width8,height16,visibility3,retained.5 and slightly smaller values.
        Outputs:boundary eligible,explicit overlapping reasons. Logic: fixed policy only.
        Constraints: eligibility never removes original GT observations.
        """
        row = {
            **geometry_row(),
            "input_width_px": 8,
            "input_height_px": 16,
            "visibility_token": "3",
            "retained_bbox_fraction": 0.5,
        }
        self.assertTrue(eligible(row, POLICY)[0])
        row.update(input_width_px=7.99, input_height_px=15.99, visibility_token="2")
        result, reasons = eligible(row, POLICY)
        self.assertFalse(result)
        self.assertEqual(
            set(reasons),
            {"input_width_below_policy", "input_height_below_policy", "visibility_below_policy"},
        )
        self.assertIn(
            "visibility_unknown", eligible({**geometry_row(), "visibility_token": ""}, POLICY)[1]
        )

    def test_loss_attribution(self):
        """Functionality: Separate confidence,class,localization,missing and distance-stage losses.
        Inputs:one GT and controlled original/raw candidates. Outputs:exact reason at unchanged0.5.
        Logic: no below-threshold candidate enters original one-to-one matching.
        Constraints: overlapping raw boxes are evidence,not proof of identity.
        """
        row = geometry_row()
        frame = {
            "objects": [
                {
                    "annotation_id": row["annotation_id"],
                    "class_name": "car",
                    "bbox": row["bbox"],
                    "distance_gt_m": 10,
                }
            ]
        }
        good = {
            "class_name": "car",
            "bbox": row["bbox"],
            "confidence": 0.9,
            "distance_m": 10,
            "valid_bbox": True,
        }
        cases = [
            ([good], [good], "matched_valid_distance"),
            ([{**good, "distance_m": None}], [good], "matched_distance_unavailable"),
            ([], [{**good, "confidence": 0.1}], "same_class_iou_candidate_below_confidence"),
            ([{**good, "class_name": "truck"}], [], "wrong_class_prediction_at_required_iou"),
            ([{**good, "bbox": [60, 0, 160, 100]}], [], "kept_same_class_overlap_below_iou"),
            (
                [],
                [{**good, "bbox": [60, 0, 160, 100], "confidence": 0.01}],
                "only_raw_same_class_overlap_below_iou",
            ),
            ([], [], "no_same_class_overlap_in_raw_topk"),
        ]
        for kept, raw, expected in cases:
            observations, _ = diagnose_matches(frame, [row], kept, raw, 0.5, 0.25)
            self.assertEqual(observations[0]["loss_reason"], expected)

    def test_original_and_subset_denominators(self):
        """Functionality: Report eligible coverage beside unchanged full-GT coverage.
        Inputs:one matched excluded GT and one unmatched eligible GT.
        Outputs:50% original,0% subset.
        Logic: keep original assignment and explicit denominators.
        Constraints: cannot rematch excluded predictions to make the subset look better.
        """
        first = {
            **geometry_row(),
            "detection_matched": True,
            "valid_distance": True,
            "loss_reason": "matched_valid_distance",
            "max_raw_confidence_at_required_iou": 0.9,
            "policy_eligible": False,
            "visibility_token": "1",
            "policy_exclusion_reasons": ["visibility_below_policy"],
        }
        second = {
            **geometry_row(),
            "annotation_id": "second",
            "detection_matched": False,
            "valid_distance": False,
            "loss_reason": "no_same_class_overlap_in_raw_topk",
            "max_raw_confidence_at_required_iou": None,
        }
        result = summarize_rows([first, second], POLICY)
        self.assertEqual(result["original_gt_coverage"], 0.5)
        self.assertEqual(result["policy_eligible_coverage"], 0)
        self.assertEqual((result["original_gt"], result["policy_eligible_gt"]), (2, 1))
        self.assertEqual(len(result["sensitivity"]), 4)
        self.assertIsNone(summarize_rows([], POLICY)["original_gt_coverage"])

    def test_distributions(self):
        """Functionality: Distinguish missing confidence from zero and threshold-edge scores.
        Inputs:None,NaN,zero,.25. Outputs:two missing,two valid,one per half-open interval.
        Logic: fixed bins only. Constraints: missing evidence is not zero confidence.
        """
        result = distribution([None, float("nan"), 0, 0.25], (0, 0.25, 1))
        self.assertEqual((result["count"], result["missing"]), (2, 2))
        self.assertEqual([row["count"] for row in result["histogram"]], [1, 1])


@skipUnless(HAS_DEVKIT, "Optional official nuScenes SDK required")
class DiagnosticGeometryTests(SimpleTestCase):
    """Functionality: Exercise independent official projection audits on complete local fixtures.
    Logic: Devkit sensor boxes,homogeneous transforms and official2D postprocessor must agree.
    Constraints: fixture projection tests are separate from real six-frame GPU replay evidence.
    """

    def test_official_geometry_and_cached_guard(self):
        """Functionality: Validate original projections and catch intrinsics/box corruption.
        Inputs:complete SDK fixture and altered cached copy. Outputs:zero error or explicit failure.
        Logic: independent official APIs,never mutate raw fixture files.
        Constraints: cannot diagnose the detector after a failed geometry preflight.
        """
        from nuscenes.nuscenes import NuScenes

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = make_nuscenes(root)
            prepare(raw, root / "processed")
            _, frames = load_records(root / "processed")
            nusc = NuScenes(version="v1.0-mini", dataroot=str(raw), verbose=False)
            capture = {"transform": {"scale_x": 4, "scale_y": 4}}
            rows, checks = audit_geometry(nusc, frames[0], capture, 0.1, POLICY)
            self.assertTrue(checks["cached_gt_ids_match"])
            self.assertLess(checks["official_projection_max_error_px"], 1e-8)
            self.assertTrue(rows[0]["policy_eligible"])
            corrupted = deepcopy(frames[0])
            corrupted["objects"][0]["bbox"][0] += 1
            with self.assertRaisesRegex(ValueError, "Geometry audit failed"):
                audit_geometry(nusc, corrupted, capture, 0.1, POLICY)

    def test_new_output_guard(self):
        """Functionality: Reject diagnostic writes inside the original result tree.
        Inputs:existing original directory and child output. Outputs:ValueError before GPU activity.
        Logic: containment guard. Constraints: original result files must remain byte-identical.
        """
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "outside the original"):
                diagnose(Path(directory), Path(directory) / "diagnosis", Path("unused-policy"))
