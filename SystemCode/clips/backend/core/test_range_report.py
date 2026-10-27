"""Responsibilities: Verify GT-based near-band errors and immutable saved-run reporting.
Implementation: Hand-calculated boundaries, missing predictions and temporary source artifacts.
Related Modules: model_tests.unified_evaluation.range_report.
Declaration Index: observation; RangeReportTests; RangeReportTests.test_boundaries_and_missing;
RangeReportTests.test_invalid_and_empty; RangeReportTests.test_export_preserves_sources.
Variable Index: None.
"""

import json
import tempfile
from pathlib import Path
from unittest import TestCase

from model_tests.unified_evaluation.range_report import export_report, summarize_ranges


def observation(key, target, prediction, matched=True):
    """Functionality: Construct synthetic saved observations. Inputs: identity, meters, match flag.
    Outputs: one canonical row. Logic: preserve supplied values. Constraints: no real model claims.
    """
    return dict(
        key=key, target_m=target, prediction_m=prediction, matched=matched, class_name="car"
    )


class RangeReportTests(TestCase):
    """Functionality: Guard independent arithmetic and source preservation.
    Logic: known errors cross prediction boundaries but stay grouped by GT distance.
    Constraints: tests cover reporting, not depth model accuracy.
    """

    def test_boundaries_and_missing(self):
        """Functionality: Assign exact edges once and retain missed objects in denominators.
        Inputs: 2/5/9/10/19/20 meter GT. Outputs: MAEs 2/3/3 and explicit coverage.
        Logic: predictions may fall outside their GT band. Constraints: 20m is outside this report.
        """
        rows = [
            observation("a", 2, 4), observation("b", 5, 8),
            observation("c", 9, None, False), observation("d", 10, 8),
            observation("e", 19, 23), observation("f", 20, 20),
        ]
        report = summarize_ranges(rows)
        self.assertEqual([r["mae_m"] for r in report["bands"]], [2, 3, 3])
        self.assertEqual([r["gt_objects"] for r in report["bands"]], [1, 2, 2])
        self.assertEqual([r["coverage"] for r in report["bands"]], [1, 0.5, 1])
        self.assertEqual(report["gt_objects_outside_requested_ranges"], 1)

    def test_invalid_and_empty(self):
        """Functionality: Reject inconsistent input and keep unavailable errors null.
        Inputs: zero GT, duplicate key, invalid predictions and no observations. Outputs: guards.
        Logic: no empty-error zero substitution. Constraints: invalid distance stays missing.
        """
        for rows in (
            [observation("a", 0, 2)],
            [observation("a", 2, 2), observation("a", 3, 3)],
            [observation("a", 2, 2, False)],
        ):
            with self.assertRaises(ValueError):
                summarize_ranges(rows)
        report = summarize_ranges([observation("a", 3, float("nan"))])
        self.assertIsNone(report["bands"][0]["mae_m"])
        self.assertEqual(report["bands"][0]["matched_objects"], 1)
        self.assertEqual(report["bands"][0]["coverage"], 0)
        self.assertTrue(all(r["mae_m"] is None for r in summarize_ranges([])["bands"]))

    def test_export_preserves_sources(self):
        """Functionality: Read completed artifacts without overwriting inputs or prior exports.
        Inputs: one temporary completed pass. Outputs: CSV/JSON, preserved bytes, repeat refusal.
        Logic: assert provenance and actual numerical output. Constraints: no model initialization.
        """
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "passes/a-predicted"
            source.mkdir(parents=True)
            raw = json.dumps(observation("a", 2, 5)) + "\n"
            path = source / "observations.jsonl"
            path.write_text(raw, encoding="utf-8")
            (source / "result.json").write_text(json.dumps({
                "status": "complete", "method": "a", "distance_target": "fixture_m",
                "split": "fixture", "metrics": {"overall": {"ground_truth_objects": 1}},
            }), encoding="utf-8")
            output = root / "ranges"
            report = export_report(root, "a", output)
            self.assertEqual(report["bands"][0]["mae_m"], 3)
            self.assertEqual(path.read_text(encoding="utf-8"), raw)
            self.assertIn("null", (output / "distance_ranges.csv").read_text())
            self.assertEqual(len(report["source_observations_sha256"]), 64)
            with self.assertRaises(FileExistsError):
                export_report(root, "a", output)
