"""Responsibilities: Verify deterministic TTC/risk math, boundaries and pipeline contracts.
Implementation: Scalar rule tests, real motion history, JPEG/wire and CLI lifecycle fixtures.
Related Modules: realtime.risk/tracking/protocol; core.detector; model_tests.run_depth/test_cli;
model_tests.test_tracking.
Declaration Index: RiskTests; RiskTests.test_ttc_examples; RiskTests.test_invalid_ttc;
RiskTests.test_closing_speed_and_horizon; RiskTests.test_requested_scenarios;
RiskTests.test_distance_boundaries; RiskTests.test_ttc_boundaries;
RiskTests.test_score_boundaries_and_clamp; RiskTests.test_speed_and_medium_alert_policy;
RiskTests.test_config_loading; RiskTests.test_invalid_config; RiskTests.test_directions;
RiskTests.test_invalid_risk_inputs; RiskTests.test_fusion_preserves_fields;
RiskTests.test_motion_integration; RiskTests.test_packet_capacity; RiskTests.test_labels;
RiskTests.test_worker_profile_handoff; RiskTests.test_cli_profile.
Variable Index: None. Rules and synthetic distances do not establish real-world safety accuracy.
"""

import hashlib
import io
import json
import math
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from PIL import Image

from core.detector import annotate
from model_tests import run_depth as cli
from model_tests import test_cli
from model_tests.test_tracking import tracked_box
from realtime.protocol import MAX_METADATA_BYTES, pack_result, unpack_result
from realtime.risk import (
    DEFAULT_RISK_CONFIG,
    RiskConfig,
    assess_object_risk,
    enrich_object_risk,
    estimate_direction,
    estimate_ttc,
    read_risk_config,
)
from realtime.tracking import TrackHistory, TrackingConfig


class RiskTests(SimpleTestCase):
    """Functionality: Verify deterministic scoring and unchanged upstream data/history.
    Logic: cover requested combinations, exact boundaries, invalid inputs and handoff/export.
    Constraints: no external services, neural risk model or claim of measured collision accuracy.
    """

    def test_ttc_examples(self):
        """Functionality: Calculate approaching TTC. Inputs: 3/-1 and 2/-.5 meters/mps.
        Outputs: 3 and 4 seconds. Logic: actual API division. Constraints: smoothed input assumed.
        """
        self.assertAlmostEqual(estimate_ttc(3., -1., "approaching"), 3.)
        self.assertAlmostEqual(estimate_ttc(2., -.5, "approaching"), 4.)

    def test_invalid_ttc(self):
        """Functionality: Reject invalid/noisy/nonapproaching TTC inputs.
        Inputs: null/nonfinite/nonpositive distances, invalid speeds/states.
        Outputs: None. Logic: exercise independent gates. Constraints: never infinity or NaN.
        """
        for distance in (None, math.nan, math.inf, -math.inf, 0, -1, True, "3", 10**400):
            self.assertIsNone(estimate_ttc(distance, -1., "approaching"))
        for speed in (None, math.nan, math.inf, -math.inf, 0, .5, -.02, True, "-1"):
            self.assertIsNone(estimate_ttc(3., speed, "approaching"))
        for motion in ("stationary", "moving_away", "unknown", None):
            self.assertIsNone(estimate_ttc(3., -1., motion))

    def test_closing_speed_and_horizon(self):
        """Functionality: Include exact minimum speed and retain TTC beyond scoring horizon.
        Inputs: boundary-adjacent speeds and 10/15 seconds. Outputs: exact eligibility/zero score.
        Logic: independent API and component assertions. Constraints: no capping exported TTC.
        """
        minimum = DEFAULT_RISK_CONFIG.min_closing_speed_mps
        self.assertIsNone(estimate_ttc(2., -math.nextafter(minimum, 0), "approaching"))
        self.assertEqual(estimate_ttc(2., -minimum, "approaching"), 10.)
        self.assertEqual(estimate_ttc(15., -1., "approaching"), 15.)
        for distance in (10., 15.):
            risk = assess_object_risk("car", distance, "front", "approaching", -1., distance)
            self.assertEqual(dict(risk.components)["ttc"], 0)
            self.assertNotEqual(risk.risk_level, "critical")
        risk = assess_object_risk("car", 15., "front", "approaching", -1., 15.)
        self.assertIn("ttc_beyond_horizon", risk.risk_reasons)

    def test_requested_scenarios(self):
        """Functionality: Cover user scenarios A–G including close stationary obstacles.
        Inputs: class/distance/motion/speed/TTC combinations. Outputs: exact defaults and alerts.
        Logic: five-component sum. Constraints: no TTC necessary for high proximity risk.
        """
        cases = (("person", 20., "stationary", 0., None, 15, "low", 0),
                 ("car", 5., "moving_away", 1., None, 30, "medium", 0),
                 ("bicycle", 2., "approaching", -1., 2., 97, "critical", 4),
                 ("person", .8, "stationary", 0., None, 55, "high", 3),
                 ("car", 15., "approaching", -1., 15., 45, "medium", 0),
                 ("bicycle", 1., "approaching", -2., .5, 100, "critical", 4),
                 ("person", None, "unknown", None, None, None, "unknown", 0))
        for name, distance, motion, speed, ttc, score, level, priority in cases:
            with self.subTest(name=name, distance=distance):
                risk = assess_object_risk(name, distance, "front", motion, speed, ttc)
                self.assertEqual((risk.risk_score, risk.risk_level, risk.alert_priority),
                                 (score, level, priority))
                self.assertEqual(risk.alert_required, priority > 0)
                self.assertTrue(risk.risk_reasons)

    def test_distance_boundaries(self):
        """Functionality: Include exact distance thresholds without overlapping band sums.
        Inputs: each threshold and next float. Outputs: expected distance component.
        Logic: inspect component, independent of other weights. Constraints: all cutoffs configured.
        """
        bands = DEFAULT_RISK_CONFIG.distance_bands
        for index, (distance, weight) in enumerate(bands):
            risk = assess_object_risk("person", distance, "front", "stationary", 0., None)
            self.assertEqual(dict(risk.components)["distance"], weight)
            next_weight = bands[index + 1][1] if index + 1 < len(bands) else 0
            risk = assess_object_risk("person", math.nextafter(distance, math.inf), "front",
                                      "stationary", 0., None)
            self.assertEqual(dict(risk.components)["distance"], next_weight)

    def test_ttc_boundaries(self):
        """Functionality: Include exact TTC score cutoffs, ignore inconsistent supplied TTC.
        Inputs: each threshold and next float using -1m/s. Outputs: configured component weights.
        Logic: keep TTC consistent with smoothed distance. Constraints: no fabricated urgency.
        """
        bands = DEFAULT_RISK_CONFIG.ttc_bands
        for index, (ttc, weight) in enumerate(bands):
            risk = assess_object_risk("person", ttc, "front", "approaching", -1., ttc)
            self.assertEqual(dict(risk.components)["ttc"], weight)
            ttc = math.nextafter(ttc, math.inf)
            risk = assess_object_risk("person", ttc, "front", "approaching", -1., ttc)
            self.assertEqual(dict(risk.components)["ttc"],
                             bands[index + 1][1] if index + 1 < len(bands) else 0)
        risk = assess_object_risk("car", 20., "front", "approaching", -1., .5)
        self.assertEqual(dict(risk.components)["ttc"], 0)
        self.assertIn("ttc_inconsistent", risk.risk_reasons)

    def test_score_boundaries_and_clamp(self):
        """Functionality: Map inclusive score cutoffs and clamp negative/large sums.
        Inputs: isolated scores24/25/49/50/74/75 and extreme weights.
        Outputs: levels within0–100. Logic: explicit profile, not changed defaults.
        Constraints: priorities map only when alert policy permits.
        """
        for score, level in ((24, "low"), (25, "medium"), (49, "medium"), (50, "high"),
                             (74, "high"), (75, "critical")):
            config = replace(DEFAULT_RISK_CONFIG, distance_bands=((100, score),),
                             default_class_weight=0,
                             direction_weights=tuple((k, 0) for k, _ in
                                                     DEFAULT_RISK_CONFIG.direction_weights))
            risk = assess_object_risk("unlisted", 20., "front", "unknown", None, None,
                                      config=config)
            self.assertEqual((risk.risk_score, risk.risk_level), (score, level))
        low = assess_object_risk("sign", 20., "behind", "moving_away", 1., None)
        high = assess_object_risk("truck", .1, "front", "approaching", -2., .05)
        self.assertEqual(low.risk_score, 0)
        self.assertEqual(high.risk_score, 100)

    def test_speed_and_medium_alert_policy(self):
        """Functionality: Increase approaching score with closing speed; select medium alerts.
        Inputs: approaching speed bands and close frontal-side pole.
        Outputs: increasing motion components and configurable medium alert.
        Logic: use true speed sign even during delayed motion-state switch.
        Constraints: moving-away label with negative speed must not reduce risk.
        """
        weights = [dict(assess_object_risk("person", 20., "front", "approaching", speed,
                                           estimate_ttc(20., speed, "approaching")).components)
                   ["motion"] for speed in (-.2, -1., -2.)]
        self.assertEqual(weights, [15, 20, 25])
        risk = assess_object_risk("pole", 3., "front_left", "approaching", -.2, 15.)
        self.assertEqual((risk.risk_score, risk.risk_level, risk.alert_priority), (48, "medium", 2))
        disabled = replace(DEFAULT_RISK_CONFIG, medium_alert_enabled=False)
        self.assertFalse(assess_object_risk("pole", 3., "front_left", "approaching", -.2, 15.,
                                           config=disabled).alert_required)
        mismatch = assess_object_risk("car", 5., "front", "moving_away", -1., None)
        self.assertEqual(dict(mismatch.components)["motion"], 0)

    def test_config_loading(self):
        """Functionality: Apply explicit JSON overrides once and retain unmodified named weights.
        Inputs: partial maps, replacement bands/levels. Outputs: validated immutable tunable rules.
        Logic: real UTF8 file reader. Constraints: profile does not mutate global defaults.
        """
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "risk.json"
            path.write_text(json.dumps({"min_closing_speed_mps": .3,
                                        "class_weights": {"bicycle": 20, "unknown-new": 7},
                                        "distance_bands": [[2, 50], [10, 5]],
                                        "risk_level_thresholds": [30, 60, 90]}), encoding="utf-8")
            config = read_risk_config(path)
        self.assertEqual(dict(config.class_weights)["bicycle"], 20)
        self.assertEqual(dict(config.class_weights)["car"], 15)
        self.assertEqual(config.distance_bands, ((2, 50), (10, 5)))
        self.assertIsNone(estimate_ttc(2., -.2, "approaching", config=config))
        self.assertEqual(dict(DEFAULT_RISK_CONFIG.class_weights)["bicycle"], 12)
        self.assertIs(read_risk_config(), DEFAULT_RISK_CONFIG)

    def test_invalid_config(self):
        """Functionality: Fail malformed numeric, table, direction and alert profiles early.
        Inputs: invalid dataclass/JSON values. Outputs: ValueError.
        Logic: no silent profile repair. Constraints: no model loading or downloads.
        """
        for values in ({"min_closing_speed_mps": 0}, {"max_ttc_sec": math.inf},
                       {"ttc_bands": ((11, 30),)}, {"distance_bands": ((3, 5), (2, 30))},
                       {"motion_weights": (("approaching", 5),)},
                       {"risk_level_thresholds": (75, 50, 25)},
                       {"front_left_fraction": .8}, {"medium_alert_enabled": 1},
                       {"default_class_weight": -1},
                       {"alert_priorities": tuple((k, 0) for k, _ in
                                                  DEFAULT_RISK_CONFIG.alert_priorities)}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                replace(DEFAULT_RISK_CONFIG, **values)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "risk.json"
            for value in ({"typo": 1}, {"direction_weights": {"typo": 1}},
                          {"class_weights": []}, {"distance_bands": [2]}, []):
                path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(ValueError):
                    read_risk_config(path)

    def test_directions(self):
        """Functionality: Derive camera-relative regions, preserve supplied valid direction.
        Inputs: clamped boxes, exact thirds and bad geometry. Outputs: bounded direction labels.
        Logic: center location only. Constraints: never infer distance from box size.
        """
        for bbox, direction in (([-10, 0, 20, 50], "front_left"), ([90, 0, 110, 50], "front"),
                                ([290, 0, 330, 50], "front_right"), ([20, 0, 10, 50], "unknown"),
                                ([0, 0, math.nan, 50], "unknown")):
            self.assertEqual(estimate_direction(bbox, (300, 100)), direction)
        self.assertEqual(estimate_direction([190, 0, 210, 50], (300, 100)), "front")
        self.assertEqual(estimate_direction([0, 0, 20, 50], (0, 100)), "unknown")
        box = {**tracked_box(3.), "direction": "right", "smoothed_distance_m": 3.}
        self.assertEqual(enrich_object_risk([box], (160, 160))[0]["direction"], "right")

    def test_invalid_risk_inputs(self):
        """Functionality: Represent unavailable depth and unknown class/direction explicitly.
        Inputs: invalid distances/metadata. Outputs: unknown/null/no alerts or low risk.
        Logic: inspect public assessment. Constraints: absent TTC never means automatic safety.
        """
        for distance in (None, math.nan, math.inf, 0, -1, True):
            risk = assess_object_risk("car", distance, "front", "approaching", -1., 1.)
            self.assertEqual((risk.risk_score, risk.risk_level, risk.alert_priority),
                             (None, "unknown", 0))
            self.assertFalse(risk.alert_required)
        risk = assess_object_risk(None, 20., None, None, None, None)
        self.assertEqual(risk.risk_score, DEFAULT_RISK_CONFIG.default_class_weight)
        self.assertIn("class_default", risk.risk_reasons)

    def test_fusion_preserves_fields(self):
        """Functionality: Add TTC/risk using smoothed depth while preserving every upstream field.
        Inputs: different raw/smoothed meters, missing depth and missing ID.
        Outputs: smoothed TTC, unknown on unavailable inputs, original dictionary untouched.
        Logic: compare additive copies. Constraints: no independent history or cached-depth TTC.
        """
        original = {**tracked_box(2.1), "smoothed_distance_m": 2.2,
                    "motion_state": "approaching", "relative_speed_mps": -1.2}
        result = enrich_object_risk([original], (160, 160))[0]
        self.assertAlmostEqual(result["ttc_sec"], 2.2 / 1.2)
        self.assertEqual({key: result[key] for key in original}, original)
        self.assertNotIn("risk_score", original)
        for changes in ({"distance_m": None}, {"distance_m": math.nan},
                        {"track_id": None}, {"smoothed_distance_m": None}):
            result = enrich_object_risk([{**original, **changes}], (160, 160))[0]
            self.assertEqual((result["ttc_sec"], result["risk_level"], result["alert_required"]),
                             (None, "unknown", False))
        self.assertEqual(enrich_object_risk([], (160, 160)), [])

    def test_motion_integration(self):
        """Functionality: Reuse actual bounded motion history before stateless TTC/risk assessment.
        Inputs: ID7 approaching samples, independent ID9, then missing ID7 depth.
        Outputs: correct smoothed TTC, unchanged histories and cached motion with unknown risk.
        Logic: real TrackHistory.update. Constraints: no second state system.
        """
        history = TrackHistory(TrackingConfig())
        for index, distance in enumerate((4.8, 4.3, 3.7, 3.2)):
            boxes = history.update([tracked_box(distance), tracked_box(9., identity=9)], index * .5)
            results = enrich_object_risk(boxes, (160, 160))
        approaching = results[0]
        self.assertEqual(approaching["motion_state"], "approaching")
        self.assertAlmostEqual(approaching["ttc_sec"], approaching["smoothed_distance_m"] /
                               -approaching["relative_speed_mps"])
        self.assertIsNone(results[1]["ttc_sec"])
        state = history.tracks[7]
        before = tuple(state.distance_history)
        missing = enrich_object_risk(history.update([tracked_box(None)], 1.75), (160, 160))[0]
        self.assertIs(history.tracks[7], state)
        self.assertEqual(tuple(state.distance_history), before)
        self.assertEqual(missing["motion_state"], "approaching")
        self.assertIsNone(missing["ttc_sec"])
        self.assertEqual(missing["risk_level"], "unknown")

    def test_packet_capacity(self):
        """Functionality: Carry300 full precision fused/tracked/risk objects without field loss.
        Inputs: maximum safe IDs, long floats and configured-class reasons.
        Outputs: complete bounded PRS1 roundtrip. Logic: actual JSON/JPEG encoding and framing.
        Constraints: no numeric truncation or dropped objects to satisfy the packet bound.
        """
        box = {**tracked_box(1.234567890123456), "class_name": "fire hydrant",
               "label": "fire hydrant", "score": .9312345678901234,
               "confidence": .9312345678901234,
               "xyxy": [12.34567890123456, 2.34567890123456, 99.34567890123456, 75.34567890123456],
               "smoothed_distance_m": 1.234567890123456,
               "relative_speed_mps": -2.1234567890123456, "motion_state": "approaching"}
        box["bbox"] = list(box["xyxy"])
        boxes = enrich_object_risk([{**box, "track_id": 2**53 - 1 - i} for i in range(300)],
                                   (120, 80))
        image = io.BytesIO()
        Image.new("RGB", (120, 80)).save(image, format="JPEG")
        jpeg = image.getvalue()
        metadata = {"index": 0, "boxes": boxes, "risk_model": "rules-v1", "risk_ms": 1.,
                    "jpeg_sha256": hashlib.sha256(jpeg).hexdigest()}
        size = len(json.dumps(metadata, separators=(",", ":")).encode())
        self.assertLess(size, MAX_METADATA_BYTES)
        self.assertEqual(unpack_result(pack_result(metadata, jpeg), 0), (metadata, jpeg))

    def test_labels(self):
        """Functionality: Show concise risk labels and TTC N/A for close stationary objects.
        Inputs: risk objects and low/medium/legacy counterparts. Outputs: exact annotation text.
        Logic: inspect actual Pillow draw calls. Constraints: no layout or mobile usability claim.
        """
        box = {**tracked_box(2.1), "motion_state": "approaching", "relative_speed_mps": -1.2}
        for changes, expected in (({"risk_level": "critical", "ttc_sec": 1.8},
                                   "ID 7 | person | 2.1m | approaching | TTC 1.8s | CRITICAL"),
                                  ({"risk_level": "high", "ttc_sec": None, "distance_m": .8,
                                    "motion_state": "stationary", "relative_speed_mps": 0.},
                                   "ID 7 | person | 0.8m | stationary | TTC N/A | HIGH"),
                                  ({"risk_level": "low"},
                                   "ID 7 | person 0.93 | 2.1 m | approaching | -1.20 m/s"),
                                  ({"risk_level": "medium"},
                                   "ID 7 | person 0.93 | 2.1 m | approaching | -1.20 m/s")):
            with patch("PIL.ImageDraw.Draw") as draw:
                draw.return_value.textbbox.return_value = (0, 0, 100, 20)
                annotate(Image.new("RGB", (160, 160)), [{**box, **changes}])
                self.assertEqual(draw.return_value.text.call_args.args[1], expected)

    def test_worker_profile_handoff(self):
        """Functionality: Forward one immutable risk profile to one worker detector construction.
        Inputs: explicit profile and immediate session end. Outputs: exact keyword and one factory.
        Logic: real orchestration with HTTP double. Constraints: no allocation/network/retry.
        """
        from zerogpu.live_worker import run_worker

        config, factory = RiskConfig(min_closing_speed_mps=.3), MagicMock()
        with patch("zerogpu.live_worker.exchange", side_effect=[(200, b""), (410, b""),
                                                               (200, b"")]):
            run_worker("a" * 64, None, None, factory=factory, risk_config=config)
        factory.assert_called_once_with(None, None, risk_config=config)

    def test_cli_profile(self):
        """Functionality: Persist and forward explicitly selected risk parameters once.
        Inputs: real profile file and CLI namespace. Outputs: exact profile and report settings.
        Logic: filesystem run with detector double; reject --risk-config without --track.
        Constraints: synthetic predictions verify configuration, not GPU inference or risk accuracy.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "a.jpg"
            Image.new("RGB", (20, 20)).save(source)
            args = test_cli.DepthCliTests().arguments(root, source)
            args.track, args.risk_config = True, root / "risk.json"
            args.risk_config.write_text('{"min_closing_speed_mps": 0.3}', encoding="utf-8")
            detector = MagicMock()
            detector.predict.return_value = ({"boxes": []}, b"explicit test JPEG")
            with patch.object(cli, "build_detector", return_value=detector) as factory, \
                    patch("builtins.print"):
                report = cli.run(args)
            factory.assert_called_once_with(args.weights, args.manifest, args.depth_weights,
                                            tracking_config=TrackingConfig(),
                                            risk_config=RiskConfig(min_closing_speed_mps=.3))
            self.assertEqual(report["settings"]["risk"]["min_closing_speed_mps"], .3)
            args.output, args.track = root / "unused", False
            with patch.object(cli, "build_detector") as factory, self.assertRaises(ValueError):
                cli.run(args)
            factory.assert_not_called()
