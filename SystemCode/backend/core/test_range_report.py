"""Responsibilities: Verify GT-based near-band errors and immutable saved-run reporting.
Implementation: Hand-calculated boundaries, missing predictions and temporary source artifacts.
Related Modules: model_tests.unified_evaluation.range_report.
Declaration Index: observation; RangeReportTests; RangeReportTests.test_boundaries_and_missing;
RangeReportTests.test_invalid_and_empty; RangeReportTests.test_export_preserves_sources.
RangeReportTests.test_thirty_meter_gt_limit; RangeReportTests.test_invalid_edges;
RangeReportTests.test_all_methods_cli.
Variable Index: None.
"""

import json
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from model_tests.unified_evaluation.range_report import export_report, main, summarize_ranges


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

    def test_thirty_meter_gt_limit(self):
        """Functionality: Apply the near-range restriction only to GT, preserving large errors.
        Inputs: near GT predicted beyond 30m, a miss, and GT at/above 30m predicted nearby.
        Outputs: MAE 40m, coverage 1/2 and two excluded GT observations.
        Logic: use [0,30) with the existing matching. Constraints: never clip predicted distance.
        """
        report = summarize_ranges([
            observation("near", 10, 50), observation("miss", 20, None, False),
            observation("edge", 30, 1), observation("far", 40, 2),
        ], (0, 30))
        band = report["bands"][0]
        self.assertEqual(band["gt_range_m"], "[0, 30)")
        self.assertEqual(band["mae_m"], 40)
        self.assertEqual(band["abs_rel"], 4)
        self.assertEqual(band["coverage"], 0.5)
        self.assertEqual(band["matched_objects"], 1)
        self.assertEqual(report["gt_objects_outside_requested_ranges"], 2)

    def test_invalid_edges(self):
        """Functionality: Reject ambiguous/invalid ranges before exporting files.
        Inputs: missing, duplicate, descending, negative and nonfinite edges. Outputs: ValueError.
        Logic: explicit boundaries only. Constraints: do not reorder or repair caller input.
        """
        for edges in ((), (0,), (0, 0), (30, 0), (-1, 30), (0, float("nan")), (0, True)):
            with self.subTest(edges=edges), self.assertRaises(ValueError):
                summarize_ranges([], edges)

    def test_all_methods_cli(self):
        """Functionality: Export one four-method table with actual observation-weighted MAEs.
        Inputs: completed fake passes with different predictions. Outputs: four CSV rows.
        Logic: invoke the CLI with [0,30) edges and preserve source bytes.
        Constraints: no inference, threshold change, or overwrite of an existing export.
        """
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshots = {}
            for index, method in enumerate("abce"):
                source = root / "passes" / f"{method}-predicted"
                source.mkdir(parents=True)
                path = source / "observations.jsonl"
                raw = json.dumps(observation("near", 10, 11 + index)) + "\n"
                path.write_text(raw, encoding="utf-8")
                snapshots[path] = path.read_bytes()
                (source / "result.json").write_text(json.dumps({
                    "status": "complete", "method": method, "distance_target": "fixture_m",
                    "split": "fixture", "metrics": {"overall": {"ground_truth_objects": 1}},
                }), encoding="utf-8")
            output = root / "ranges"
            args = ["--run", str(root), "--method", "all", "--edges", "0", "30",
                    "--output", str(output)]
            with patch("builtins.print"):
                main(args)
            for index, method in enumerate("abce"):
                report = json.loads((output / method / "metrics.json").read_text())
                self.assertEqual(report["bands"][0]["mae_m"], index + 1)
                self.assertEqual(report["range_edges_m"], [0, 30])
            self.assertEqual(len((output / "distance_ranges.csv").read_text().splitlines()), 5)
            self.assertTrue(all(path.read_bytes() == raw for path, raw in snapshots.items()))
            with self.assertRaises(FileExistsError):
                main(args)

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
