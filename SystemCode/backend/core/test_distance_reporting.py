"""Responsibilities: Verify final four-band reporting without altering inference or training.
Implementation: Independent scalar expectations, boundary targets and explicit missing coverage.
Related Modules: object_distance.reporting/metrics/evaluate.
Declaration Index: DistanceReportingTests; DistanceReportingTests.test_boundaries_and_weights;
DistanceReportingTests.test_unavailable_band; DistanceReportingTests.test_report_export.
Variable Index: None.
"""

import json
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from object_distance.metrics import DistanceMetrics
from object_distance.reporting import distance_report, write_distance_report


class DistanceReportingTests(SimpleTestCase):
    """Functionality: Guard fixed weights and unchanged missing-prediction semantics.
    Logic: hand-calculated observations include 10/20/40/80-meter boundaries.
    Constraints: synthetic tests establish arithmetic, not actual model accuracy.
    """

    def test_boundaries_and_weights(self):
        """Functionality: Verify grouping, AbsRel and weighting independently of sample counts.
        Inputs: unequal band counts including a missed near object. Outputs: weighted MAE 2.1m.
        Logic: band MAEs are 1/2/3/5; final band merges 40m and 80m observations.
        Constraints: weight applies to each band MAE, not its object count or relative error.
        """
        metrics = DistanceMetrics()
        for target, prediction in ((5, 6), (6, None), (10, 12), (20, 23), (40, 44), (80, 86)):
            metrics.add(target, prediction, "car")
        original = metrics.summary()
        report = distance_report(metrics)
        self.assertEqual([b["mae_m"] for b in report["bands"]], [1, 2, 3, 5])
        self.assertAlmostEqual(report["weighted_mae_m"], 2.1)
        self.assertAlmostEqual(report["bands"][3]["abs_rel"], (4 / 40 + 6 / 80) / 2)
        self.assertEqual(report["bands"][0]["coverage"], 0.5)
        self.assertEqual(metrics.summary(), original)
        json.dumps(report, allow_nan=False)

    def test_unavailable_band(self):
        """Functionality: Reject fabricated weighted results for empty or wholly missed bands.
        Inputs: absent/all-missing far band and an entirely empty collector. Outputs: null score.
        Logic: no fixed-weight renormalization. Constraints: zero error remains valid if observed.
        """
        metrics = DistanceMetrics()
        for target in (5, 10, 20):
            metrics.add(target, target, "person")
        self.assertIsNone(distance_report(metrics)["weighted_mae_m"])
        metrics.add(40, None, "person")
        self.assertEqual(distance_report(metrics)["unavailable_bands"], [4])
        metrics.add(80, 80, "person")
        self.assertEqual(distance_report(metrics)["weighted_mae_m"], 0)
        self.assertIsNone(distance_report(DistanceMetrics())["weighted_mae_m"])

    def test_report_export(self):
        """Functionality: Export readable coverage and validation provenance with unavailable data.
        Inputs: one missed GT and a temporary directory. Outputs: Markdown table and caveat.
        Logic: render actual metrics. Constraints: no writes to existing experiment artifacts.
        """
        metrics = DistanceMetrics()
        metrics.add(5, None, "car")
        report = {
            "distance_report": distance_report(metrics),
            "metrics": metrics.summary(),
            "split": "val",
            "distance_target": "kitti_camera_reference_z_m",
            "evaluated_images": 1,
            "split_manifest_sha256": "fixture",
        }
        with tempfile.TemporaryDirectory() as temporary:
            write_distance_report(temporary, "predicted", report)
            text = (Path(temporary) / "predicted_report.md").read_text()
        self.assertIn("[40, inf)", text)
        self.assertIn("0.00%", text)
        self.assertIn("N/A", text)
        self.assertIn("Split: val", text)
        self.assertIn("not an independent final test set", text)
