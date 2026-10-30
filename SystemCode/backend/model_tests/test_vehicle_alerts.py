"""Responsibilities: Verify distant/lateral vehicle filtering and retained urgent alerts.
Implementation: Paired legacy-policy comparisons, boundary cases and real risk/PRS1 functions.
Related Modules: realtime.risk/protocol; integration.protocol; model_tests.test_tracking.
Declaration Index: VehicleAlertTests; VehicleAlertTests.test_scenario_comparison;
VehicleAlertTests.test_distance_boundaries; VehicleAlertTests.test_urgent_ttc_boundaries;
VehicleAlertTests.test_invalid_ttc_cannot_bypass_filter;
VehicleAlertTests.test_other_classes_and_scores_unchanged;
VehicleAlertTests.test_unknown_direction_uses_frontal_limits;
VehicleAlertTests.test_config_loading_and_validation;
VehicleAlertTests.test_pipeline_and_packet_capacity; VehicleAlertTests.test_contract_version.
Variable Index: LEGACY_POLICY — explicit filter-off baseline for the original alert rules;
SCENARIOS — synthetic scalar inputs/expected alerts, not measured road-scene accuracy.
"""

import hashlib
import json
import math
import tempfile
from dataclasses import replace
from pathlib import Path

from django.test import SimpleTestCase

from integration.protocol import model_contract, read_manifest, validate_contract
from model_tests.test_tracking import tracked_box
from realtime.protocol import pack_result, unpack_result
from realtime.risk import (
    DEFAULT_RISK_CONFIG,
    RISK_MODEL,
    SIDE_DIRECTIONS,
    VEHICLE_CLASSES,
    assess_object_risk,
    enrich_object_risk,
    estimate_ttc,
    read_risk_config,
)

LEGACY_POLICY = replace(DEFAULT_RISK_CONFIG, vehicle_alert_filter_enabled=False)
SCENARIOS = (
    # Class, meters, direction, motion, signed m/s, old alert, filtered alert.
    ("car", 20., "front", "approaching", -2., True, False),
    ("car", 20., "front_left", "approaching", -5., True, False),
    ("car", 8., "front_right", "approaching", -2., True, False),
    ("car", 4., "front_left", "approaching", -.5, True, False),
    ("truck", 30., "front_right", "approaching", -2., True, False),
    ("car", 20., "front", "approaching", -10., True, True),
    ("car", 8., "front_right", "approaching", -4., True, True),
    ("car", 3., "front_left", "approaching", -.5, True, True),
    ("car", 1., "front_right", "stationary", 0., True, True),
    ("car", 5., "front", "moving_away", 1., False, False),
    ("person", .8, "front", "stationary", 0., True, True),
    ("bicycle", 2., "front_right", "approaching", -1., True, True),
)


class VehicleAlertTests(SimpleTestCase):
    """Functionality: Verify alert suppression without changing detection/scoring inputs.
    Logic: Compare original eligibility to explicit filter-on policy and malformed inputs.
    Constraints: No inference, live road accuracy, device output or lane recognition claim.
    """

    def test_scenario_comparison(self):
        """Functionality: Suppress reported nuisance patterns and retain urgent/near alerts.
        Inputs: SCENARIOS with distances, directions and consistent motion/TTC.
        Outputs: Independently specified before/after alert flags and unchanged scores.
        Logic: Run the same scalar inputs with filtering off/on.
        Constraints: Comparison demonstrates rule behavior, not measured false-positive rates.
        """
        for name, distance, direction, motion, speed, before, after in SCENARIOS:
            with self.subTest(name=name, distance=distance, direction=direction, speed=speed):
                args = (name, distance, direction, motion, speed,
                        estimate_ttc(distance, speed, motion))
                original = assess_object_risk(*args, config=LEGACY_POLICY)
                filtered = assess_object_risk(*args)
                self.assertEqual(original.alert_required, before)
                self.assertEqual(filtered.alert_required, after)
                self.assertEqual(filtered.alert_priority,
                                 original.alert_priority if after else 0)
                self.assertEqual((filtered.risk_score, filtered.risk_level, filtered.components),
                                 (original.risk_score, original.risk_level, original.components))
                self.assertEqual(filtered.risk_reasons[:len(original.risk_reasons)],
                                 original.risk_reasons)
                if before and not after:
                    self.assertTrue(filtered.risk_reasons[-1].startswith("vehicle_outside_"))

    def test_distance_boundaries(self):
        """Functionality: Include exact near limits, suppress immediately beyond them.
        Inputs: Every vehicle class, central/lateral directions and boundary-adjacent meters.
        Outputs: Existing alert at the limit; explicit suppression above the limit.
        Logic: Use approaching speed whose TTC cannot bypass either distance boundary.
        Constraints: All directions refer to the camera, never inferred road lanes.
        """
        for name in VEHICLE_CLASSES:
            for direction, limit in (("front", 10.), *((d, 3.) for d in SIDE_DIRECTIONS)):
                for distance, expected in ((limit, True),
                                           (math.nextafter(limit, math.inf), False)):
                    with self.subTest(name=name, direction=direction, distance=distance):
                        speed = -2. if direction == "front" else -.5
                        args = (name, distance, direction, "approaching", speed,
                                estimate_ttc(distance, speed, "approaching"))
                        self.assertTrue(assess_object_risk(*args,
                                                          config=LEGACY_POLICY).alert_required)
                        self.assertEqual(assess_object_risk(*args).alert_required, expected)

    def test_urgent_ttc_boundaries(self):
        """Functionality: Preserve fast approach beyond the distance limits on both sides.
        Inputs: Frontal three-second and lateral two-second boundaries at twenty meters.
        Outputs: Urgent alerts retained, slower adjacent inputs suppressed.
        Logic: Derive TTC from fixed speed and vary distance at/above the exact boundary.
        Constraints: TTC is depth extrapolation, not proof of an intersecting path.
        """
        for direction, limit in (("front", 3.), *((d, 2.) for d in SIDE_DIRECTIONS)):
            for name in VEHICLE_CLASSES:
                for distance, expected in ((limit * 10., True), (limit * 10. + .001, False)):
                    with self.subTest(name=name, direction=direction, distance=distance):
                        args = (name, distance, direction, "approaching", -10., distance / 10.)
                        self.assertTrue(assess_object_risk(*args,
                                                          config=LEGACY_POLICY).alert_required)
                        self.assertEqual(assess_object_risk(*args).alert_required, expected)

    def test_invalid_ttc_cannot_bypass_filter(self):
        """Functionality: Reject forged or unavailable urgent TTC as a distance bypass.
        Inputs: A distant vehicle with independently high class/approach score and bad TTC.
        Outputs: Suppression even when supplied TTC claims imminent arrival.
        Logic: Exercise absent, nonfinite, negative, inconsistent and wrong-motion values.
        Constraints: Near vehicles still follow original rules without requiring valid motion.
        """
        for ttc in (None, math.nan, math.inf, -1, 0, True, "1", 1.):
            with self.subTest(ttc=ttc):
                risk = assess_object_risk("car", 20., "front", "approaching", -2., ttc)
                self.assertEqual(risk.risk_level, "high")
                self.assertFalse(risk.alert_required)
        for motion in ("stationary", "moving_away", "unknown"):
            risk = assess_object_risk("car", 20., "front_left", motion, -20., 1.)
            self.assertFalse(risk.alert_required)

    def test_other_classes_and_scores_unchanged(self):
        """Functionality: Keep nonvehicle behavior and all five scalar score components.
        Inputs: Vehicles/people/obstacles/lights over near/far, motion and direction grids.
        Outputs: Identical assessments outside vehicle eligibility and monotonic suppression.
        Logic: Compare filter-on/off for the same inputs, including missing depth.
        Constraints: Suppression may only remove an existing alert, never create one.
        """
        for name in (*VEHICLE_CLASSES, "person", "chair", "pole", "traffic light", None):
            for distance in (None, .8, 3., 5., 10., 20.):
                for direction in ("front", "front_left", "front_right", "unknown"):
                    for motion, speed in (("approaching", -2.), ("stationary", 0.),
                                          ("moving_away", 1.), ("unknown", None)):
                        args = (name, distance, direction, motion, speed,
                                estimate_ttc(distance, speed, motion))
                        original = assess_object_risk(*args, config=LEGACY_POLICY)
                        filtered = assess_object_risk(*args)
                        if name not in VEHICLE_CLASSES:
                            self.assertEqual(filtered, original)
                        self.assertEqual(filtered.components, original.components)
                        self.assertEqual(filtered.risk_score, original.risk_score)
                        self.assertFalse(filtered.alert_required and not original.alert_required)

    def test_unknown_direction_uses_frontal_limits(self):
        """Functionality: Avoid treating absent lateral evidence as a known side region.
        Inputs: Unknown/behind/bad directions and a high-risk vehicle at eight meters.
        Outputs: Original alert retained within the broader frontal window.
        Logic: Compare lateral suppression against nonlateral defaults.
        Constraints: Behind is only supplied metadata; the camera never infers it.
        """
        for direction in ("unknown", "behind", None, "invalid"):
            risk = assess_object_risk("truck", 8., direction, "approaching", -2., 4.)
            self.assertTrue(risk.alert_required)
        self.assertFalse(assess_object_risk("truck", 8., "front_left", "approaching",
                                           -2., 4.).alert_required)

    def test_config_loading_and_validation(self):
        """Functionality: Load explicit policy overrides and reject invalid gate profiles.
        Inputs: JSON custom thresholds, filter-off baseline and invalid numeric/order values.
        Outputs: Effective immutable configuration or ValueError before inference.
        Logic: Real JSON reader and dataclass validation; defaults are never mutated.
        Constraints: Disabled filtering does not allow malformed hidden thresholds.
        """
        overrides = {"vehicle_alert_filter_enabled": False, "vehicle_front_distance_m": 12.,
                     "vehicle_side_distance_m": 4., "vehicle_front_ttc_sec": 4.,
                     "vehicle_side_ttc_sec": 2.5}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "risk.json"
            path.write_text(json.dumps(overrides), encoding="utf-8")
            self.assertEqual(read_risk_config(path), replace(DEFAULT_RISK_CONFIG, **overrides))
        invalid = [{"vehicle_alert_filter_enabled": 1}, {"vehicle_side_distance_m": 11.},
                   {"vehicle_side_ttc_sec": 4.}, {"vehicle_front_ttc_sec": 11.}]
        for key in ("vehicle_front_distance_m", "vehicle_side_distance_m",
                    "vehicle_front_ttc_sec", "vehicle_side_ttc_sec"):
            invalid.extend({key: value} for value in (0, -1, True, None, "3", math.inf, math.nan))
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValueError):
                replace(DEFAULT_RISK_CONFIG, **values)
        tuned = replace(DEFAULT_RISK_CONFIG, vehicle_side_distance_m=5.)
        self.assertTrue(assess_object_risk("car", 4., "front_left", "approaching", -.5,
                                          8., config=tuned).alert_required)

    def test_pipeline_and_packet_capacity(self):
        """Functionality: Carry explicit suppression through real enrichment and PRS1 framing.
        Inputs: Three hundred tracked lateral cars with raw and smoothed twenty-meter depth.
        Outputs: Preserved upstream fields, no alert, bounded complete packet roundtrip.
        Logic: Compute direction from actual boxes, then use production serializers.
        Constraints: Synthetic boxes/JPEG bytes do not exercise neural inference or playback.
        """
        original = {**tracked_box(20., name="car"), "class_id": 2,
                    "smoothed_distance_m": 20., "relative_speed_mps": -5.,
                    "motion_state": "approaching"}
        results = enrich_object_risk([{**original, "track_id": i + 1} for i in range(300)],
                                     (300, 160))
        first = results[0]
        self.assertEqual(first["direction"], "front_left")
        self.assertFalse(first["alert_required"])
        self.assertEqual(first["risk_reasons"][-1], "vehicle_outside_side_alert_range")
        self.assertEqual(first["ttc_sec"], 4.)
        for key, value in original.items():
            if key != "track_id":
                self.assertEqual(first[key], value)
        self.assertNotIn("alert_required", original)
        jpeg = b"\xff\xd8synthetic test image\xff\xd9"
        metadata = {"index": 0, "boxes": results, "risk_model": RISK_MODEL, "risk_ms": 1.,
                    "jpeg_sha256": hashlib.sha256(jpeg).hexdigest()}
        self.assertEqual(unpack_result(pack_result(metadata, jpeg), 0), (metadata, jpeg))

    def test_contract_version(self):
        """Functionality: Advertise the filter-aware rule version in the actual local handshake.
        Inputs: Real detector manifest and generated session identity.
        Outputs: Current rule identity accepted by production contract validation.
        Logic: Shared constant prevents handshake/result drift.
        Constraints: Historical v1 result artifacts are never rewritten.
        """
        contract = model_contract("a" * 32, read_manifest())
        self.assertEqual(contract["risk_model"], "rules-v2")
        self.assertEqual(validate_contract(contract), contract)
