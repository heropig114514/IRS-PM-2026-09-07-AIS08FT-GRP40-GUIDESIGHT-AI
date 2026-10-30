"""Responsibilities: Verify video refinement does not hide near hazards or corrupt uncertainty.
Implementation: Deterministic source-time sequences, real risk rules and actual label placement.
Related Modules: video_export.refinement/overlay; realtime.detector/risk.
Declaration Index: box; RefineTests; RefineTests.setUp; RefineTests.frame;
RefineTests.test_dedup_preserves_distinct_objects; RefineTests.test_uncertain_class_confirmation;
RefineTests.test_near_observation_bypasses_confirmation; RefineTests.test_jump_and_reacquisition;
RefineTests.test_steady_approach_and_noise; RefineTests.test_missing_gap_and_class_reset;
RefineTests.test_risk_hysteresis_and_urgent_bypass; RefineTests.test_alert_suppression_and_unknown;
RefineTests.test_label_layout; RefineTests.test_display_distance_and_ids;
RefineTests.test_detector_hook_order.
Declaration Index (continued): RefineTests.test_confident_safety_class_is_immediate.
Declaration Index (continued): RefineTests.test_labels_avoid_locally_without_distant_relocation;
RefineTests.test_label_anchor_at_image_edges.
Declaration Index (continued): RefineTests.test_light_thresholds_survive_gaps_and_missing_tracks;
RefineTests.test_light_colors_and_dedup_survive_confirmation.
Declaration Index (continued): RefineTests.test_people_vehicle_display_confirmation_and_gaps;
RefineTests.test_display_only_does_not_expand_alerts.
Variable Index: None. Synthetic evidence is not real-video detection accuracy.
"""

import io
import math
from unittest import TestCase
from unittest.mock import MagicMock, patch

import numpy as np
from PIL import Image

from realtime.detector import ResidentDetector
from realtime.risk import enrich_object_risk
from video_export.overlay import label_text, layout_labels, overlap
from video_export.refinement import DistanceState, VideoRefinement


def box(**extra):
    """Functionality: Construct a complete synthetic object. Inputs: Overrides. Outputs: dict.
    Logic: Known front person, valid track/depth. Constraints: Does not simulate model accuracy.
    """
    return {"xyxy": [130, 200, 180, 350], "bbox": [130, 200, 180, 350], "label": "person",
            "class_name": "person", "class_id": 0, "score": .8, "distance_m": 10.,
            "track_id": 1, "smoothed_distance_m": 10., "relative_speed_mps": None,
            "motion_state": "unknown", **extra}


class RefineTests(TestCase):
    """Functionality: Exercise continuity, uncertainty, confirmation and urgent exceptions.
    Logic: Use independent source-time scenarios. Constraints: No GPU or external services.
    """

    def setUp(self):
        """Functionality: Isolate histories. Inputs: None. Outputs: Fresh policy.
        Logic: One refiner per test. Constraints: No state shared across sequences.
        """
        self.policy = VideoRefinement()

    def frame(self, objects, timestamp):
        """Functionality: Apply the three hooks around real risk scoring.
        Inputs: Boxes and timestamp. Outputs: Final boxes. Logic: Filter, motion, rules, hysteresis.
        Constraints: Input IDs/depth are fixture values rather than actual BoT-SORT/YOLO outputs.
        """
        objects = self.policy.filter_detections(objects, timestamp, {})
        objects = self.policy.refine_motion(objects, timestamp)
        return self.policy.stabilize_risk(enrich_object_risk(objects, (360, 640)), timestamp)

    def test_dedup_preserves_distinct_objects(self):
        """Functionality: Drop only nearly identical same-class candidates.
        Inputs: Duplicate, nearby distinct and other-class boxes. Outputs: Highest score retained.
        Logic: Check candidates and raw audit. Constraints: No class-agnostic suppression.
        """
        objects = [box(), box(score=.6, xyxy=[131, 201, 181, 351]),
                   box(xyxy=[160, 200, 210, 350]), box(label="backpack", class_id=24)]
        result = self.policy.filter_detections(objects, 0, {})
        self.assertEqual(len(result), 3)
        self.assertEqual(len(self.policy.audit["duplicate_detections"]), 1)
        self.assertEqual(len(self.policy.audit["raw_detections"]), 4)
        self.assertTrue(any(b["label"] == "backpack" for b in result))

    def test_uncertain_class_confirmation(self):
        """Functionality: Reject a transient false class without blacklisting that class.
        Inputs: Weak cow observations, then a consistent confident cow.
        Outputs: Weak candidate audit and accepted confirmed cow. Logic: Time/confidence gates.
        Constraints: A real weak object can be delayed; close objects have a separate bypass.
        """
        for i in range(12):
            self.assertEqual(self.frame([box(label="cow", score=.3)], i / 28), [])
        self.assertEqual(len(self.policy.audit["unconfirmed_detections"]), 1)
        self.policy.reset()
        accepted = [self.frame([box(label="cow", score=.7)], i / 28) for i in range(6)]
        self.assertFalse(accepted[0])
        self.assertTrue(accepted[-1])
        self.assertEqual(accepted[-1][0]["label"], "cow")
        self.assertFalse(self.frame([box(label="cow", score=.7)], 1.0))

    def test_near_observation_bypasses_confirmation(self):
        """Functionality: Keep a newly appearing low-confidence close person immediately.
        Inputs: Score .26 and 1.4m. Outputs: Current box and unsmoothed close distance.
        Logic: Actual measured proximity bypasses confirmation. Constraints: No future evidence.
        """
        result = self.frame([box(score=.26, distance_m=1.4)], 0)[0]
        self.assertEqual(result["confirmation_bypass"], "near")
        self.assertEqual(result["display_distance_m"], 1.4)
        self.assertNotEqual(result["risk_level"], "unknown")

    def test_confident_safety_class_is_immediate(self):
        """Functionality: Avoid flickering away clear pedestrians while confirming weaker clutter.
        Inputs: New person/car score .6 at 12m and cow score .6.
        Outputs: Immediate person/car, pending cow. Logic: Recorded priority-class threshold.
        Constraints: Animal classes are still admitted by sustained evidence or near bypass.
        """
        for label in ("person", "car"):
            self.policy.reset()
            self.assertTrue(self.frame([box(label=label, score=.6, distance_m=12.)], 0))
        self.policy.reset()
        self.assertFalse(self.frame([box(label="cow", score=.6, distance_m=12.)], 0))

    def test_light_thresholds_survive_gaps_and_missing_tracks(self):
        """Functionality: Keep current light detections at their calibrated operating thresholds.
        Inputs: Intermittent untracked traffic/pedestrian lights, boundary scores and overrides.
        Outputs: Immediate boxes at/above threshold; no boxes below it or on missing frames.
        Logic: Exercise confirmation through motion filtering. Constraints: No stale light reuse.
        """
        for label, threshold in (("traffic light", .12), ("pedestrian light", .05),
                                 ("pedestrian light", .18)):
            self.policy.reset()
            for timestamp, score in ((0., threshold), (1., threshold + .01),
                                     (2., threshold - .001)):
                candidate = box(label=label, score=score, track_id=None, distance_m=20.)
                objects = self.policy.filter_detections([candidate], timestamp, {label: threshold})
                result = self.policy.refine_motion(objects, timestamp)
                self.assertEqual(len(result), int(score >= threshold))
                if result:
                    self.assertIsNone(result[0]["track_id"])
                    self.assertEqual(result[0]["score"], score)
                absent = self.policy.filter_detections([], timestamp + .1, {label: threshold})
                self.assertEqual(self.policy.refine_motion(absent, timestamp + .1), [])

    def test_light_colors_and_dedup_survive_confirmation(self):
        """Functionality: Preserve current lamp color while still removing duplicate boxes.
        Inputs: Red/green/off observations, a duplicate and a spatially distinct light.
        Outputs: Current state and scores unchanged on two retained lamps.
        Logic: Pass merged color fields through both hooks. Constraints: No temporal color hold.
        """
        for i, state in enumerate(("red", "green", "off")):
            first = box(label="pedestrian light", score=.06, track_id=None,
                        light_state=state, light_scores={state: .06})
            duplicate = {**first, "score": .055}
            second = {**first, "xyxy": [250, 100, 270, 150]}
            objects = self.policy.filter_detections([first, duplicate, second], i / 28,
                                                    {"pedestrian light": .05})
            result = self.policy.refine_motion(objects, i / 28)
            self.assertEqual(len(result), 2)
            self.assertEqual(len(self.policy.audit["duplicate_detections"]), 1)
            for item in result:
                self.assertEqual(item["light_state"], state)
                self.assertEqual(item["light_scores"], {state: .06})

    def test_people_vehicle_display_confirmation_and_gaps(self):
        """Functionality: Recover intermittent weak people/vehicles without inventing missing boxes.
        Inputs: Weak bus observations separated by a miss, expiry, moderate person and weak cow.
        Outputs: Two-observation display, immediate moderate display, unchanged other-class gate.
        Logic: Exercise actual filtering/output hooks. Constraints: Alert confirmation stays strict.
        """
        weak = box(label="bus", score=.28, distance_m=25.)
        first = self.policy.filter_detections([weak], 0., {})
        self.assertEqual(self.policy.refine_motion(first, 0.), [])
        absent = self.policy.filter_detections([], .04, {})
        self.assertEqual(self.policy.refine_motion(absent, .04), [])
        confirmed = self.policy.filter_detections([weak], .14, {})
        result = self.policy.refine_motion(confirmed, .14)
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["display_only"])
        self.assertFalse(result[0]["alert_detection_confirmed"])
        expired = self.policy.filter_detections([weak], .4, {})
        self.assertEqual(self.policy.refine_motion(expired, .4), [])
        for label in self.policy.config.display_classes:
            self.policy.reset()
            item = self.policy.filter_detections([box(label=label, score=.35)], 0., {})[0]
            self.assertTrue(item["detection_confirmed"])
            self.assertFalse(item["alert_detection_confirmed"])
        self.policy.reset()
        self.assertFalse(self.policy.filter_detections([box(label="cow", score=.35)], 0., {})[0]
                         ["detection_confirmed"])
        self.policy.reset()
        for i in range(10):
            item = self.policy.filter_detections([box(label="bus", score=.4)], i * .14, {})[0]
            self.assertTrue(item["detection_confirmed"])
            self.assertFalse(item["alert_detection_confirmed"])
            self.policy.filter_detections([], i * .14 + .04, {})

    def test_display_only_does_not_expand_alerts(self):
        """Functionality: Display eligibility must not relax alarm eligibility or near exceptions.
        Inputs: Display-only high risk, confirmed high risk and a newly observed close weak person.
        Outputs: No alert on display-only evidence, original alerts on confirmed/close evidence.
        Logic: Verify gates through risk smoothing and the actual near bypass.
        Constraints: Display color may remain high but cannot create an alarm by itself.
        """
        high = box(risk_level="high", risk_score=60, alert_required=True, alert_priority=3,
                   risk_reasons=["approaching"], display_only=True)
        result = self.policy.stabilize_risk([high], 0.)[0]
        self.assertFalse(result["alert_required"])
        self.assertFalse(result["instant_risk"]["alert_required"])
        self.assertIn("display_only_detection", result["risk_reasons"])
        result = self.policy.stabilize_risk([{**high, "display_only": False}], .1)[0]
        self.assertTrue(result["alert_required"])
        self.policy.reset()
        near = self.frame([box(score=.26, distance_m=1.2)], 0.)[0]
        self.assertFalse(near["display_only"])
        self.assertTrue(near["alert_required"])

    def test_jump_and_reacquisition(self):
        """Functionality: Prevent a one-frame depth spike from creating false closing velocity.
        Inputs: Stable 24m, isolated 41m, normal recovery and persistent new 40m baseline.
        Outputs: Rejected spike, preserved display and rebased speed. Logic: Verify raw inputs
        remain unchanged. Constraints: A persistent discontinuity must eventually be accepted.
        """
        state = DistanceState("person", 0)
        for i in range(28):
            self.policy.update_distance(state, 24., i / 28)
        smooth, speed, motion, quality = self.policy.update_distance(state, 41., 1.)
        self.assertEqual((smooth, speed, motion, quality), (24., None, "unknown", "outlier"))
        self.assertEqual(self.policy.update_distance(state, 24., 29 / 28)[3], "measured")
        for i in range(30, 33):
            result = self.policy.update_distance(state, 40., i / 28)
        self.assertEqual(result, (40., None, "unknown", "reacquired"))
        result = self.policy.update_distance(state, 1.2, 33 / 28)
        self.assertEqual(result, (1.2, None, "unknown", "near_reacquired"))

    def test_steady_approach_and_noise(self):
        """Functionality: Preserve real approaching slopes and reject alternating measurement noise.
        Inputs: Linear 2m/s closing, three-mode accepted noise and constant depth at 28fps.
        Outputs: Correct slope/direction vs explicitly uncertain noisy motion. Logic: Robust fit.
        Constraints: This evaluates controlled temporal signals, not real-world speed accuracy.
        """
        state = DistanceState("person", 0)
        for i in range(56):
            result = self.policy.update_distance(state, 10 - 2 * i / 28, i / 28)
        self.assertAlmostEqual(result[1], -2.)
        self.assertEqual(result[2], "approaching")
        state = DistanceState("person", 0)
        for i in range(56):
            result = self.policy.update_distance(state, 10 + .4 * (i % 3 - 1), i / 28)
        self.assertEqual(result[1:3], (None, "unknown"))
        self.assertEqual(state.motion_quality, "uncertain")
        state = DistanceState("person", 0)
        for i in range(56):
            result = self.policy.update_distance(state, 10., i / 28)
        self.assertEqual(result[1:3], (0., "stationary"))
        self.assertEqual(state.motion_quality, "measured")

    def test_missing_gap_and_class_reset(self):
        """Functionality: Invalidate missing depth and never transfer motion across gaps/classes.
        Inputs: Good history, missing depth, long gap and new class for same ID.
        Outputs: Marked short hold and independent histories. Logic: Inspect quality/speed.
        Constraints: Same integer ID alone is insufficient continuity evidence.
        """
        for i in range(20):
            self.frame([box(score=.95, distance_m=10 - i / 28)], i / 28)
        missing = self.frame([box(score=.95, distance_m=None)], 20 / 28)[0]
        self.assertIsNone(missing["display_distance_m"])
        self.assertEqual(missing["instant_risk"]["risk_level"], "unknown")
        self.assertEqual(missing["risk_data_status"], "held")
        self.assertFalse(missing["alert_required"])
        changed = self.frame([box(score=.95, label="bus", class_name="bus")], 21 / 28)[0]
        self.assertIsNone(changed["relative_speed_mps"])
        after_gap = self.frame([box(score=.95, label="bus", class_name="bus")], 2.)[0]
        self.assertIsNone(after_gap["relative_speed_mps"])

    def test_risk_hysteresis_and_urgent_bypass(self):
        """Functionality: Remove flicker while keeping immediate close and reliable-TTC escalation.
        Inputs: Alternating low/high, sustained high, then sudden close critical.
        Outputs: No high flicker, confirmed high, immediate critical. Logic: Source-time gates.
        Constraints: Direct near hazards must not wait for a display stabilization timer.
        """
        low = box(risk_level="low", risk_score=15, alert_required=False, alert_priority=0,
                  risk_reasons=["test"], distance_quality="measured")
        high = {**low, "risk_level": "high", "risk_score": 60, "alert_required": True,
                "alert_priority": 3}
        for i in range(20):
            result = self.policy.stabilize_risk([low if i % 2 == 0 else high], i / 28)[0]
            self.assertIn(result["risk_level"], ("low", "medium"))
        for i in range(20, 30):
            result = self.policy.stabilize_risk([high], i / 28)[0]
        self.assertEqual(result["risk_level"], "high")
        critical = {**high, "risk_level": "critical", "risk_score": 90,
                    "alert_priority": 4, "distance_m": 1.4}
        result = self.policy.stabilize_risk([critical], 30 / 28)[0]
        self.assertEqual(result["risk_level"], "critical")
        self.assertTrue(result["alert_required"])

    def test_alert_suppression_and_unknown(self):
        """Functionality: Prevent held risk from overriding current vehicle filters or lost depth.
        Inputs: Eligible high then suppressed low then unknown.
        Outputs: Held level without alert, qualified short hold. Logic: Preserve instant audit.
        Constraints: Historical eligibility never overrides current alert suppression.
        """
        high = box(risk_level="high", risk_score=60, alert_required=True, alert_priority=3,
                   risk_reasons=["test"])
        self.policy.stabilize_risk([high], 0)
        low = {**high, "risk_level": "low", "risk_score": 10, "alert_required": False,
               "alert_priority": 0}
        result = self.policy.stabilize_risk([low], .04)[0]
        self.assertEqual(result["risk_level"], "high")
        self.assertFalse(result["alert_required"])
        self.assertEqual(result["instant_risk"]["risk_level"], "low")
        result = self.policy.stabilize_risk([{**low, "risk_level": "unknown"}], .08)[0]
        self.assertEqual(result["risk_data_status"], "held")
        self.assertEqual(result["instant_risk"]["risk_level"], "unknown")
        self.assertFalse(result["alert_required"])

    def test_label_layout(self):
        """Functionality: Accept overlap when nearby positions cannot accommodate all labels.
        Inputs: Fourteen nearby boxes at 360x640. Outputs: Bounded labels near their own boxes.
        Logic: Assert bounded movement, tether length and remaining overlap. Constraints: No OCR.
        """
        image = Image.new("RGB", (360, 640))
        objects = [box(track_id=i, xyxy=[100 + i, 200, 160 + i, 350], risk_level="high")
                   for i in range(14)]
        layout = layout_labels(image, objects)
        self.assertEqual(len(layout), len(objects))
        for item, _, rect, _ in layout:
            self.assertTrue(0 <= rect[0] < rect[2] <= 360 and 0 <= rect[1] < rect[3] <= 640)
            anchor = layout_labels(image, [item])[0][2]
            self.assertLessEqual(math.dist(rect[:2], anchor[:2]), 28)
            x, y = item["xyxy"][:2]
            self.assertLessEqual(math.hypot(max(rect[0] - x, x - rect[2], 0),
                                           max(rect[1] - y, y - rect[3], 0)), 30)
        self.assertTrue(any(overlap(a[2], b[2]) > 0 for i, a in enumerate(layout)
                            for b in layout[i + 1:]))

    def test_labels_avoid_locally_without_distant_relocation(self):
        """Functionality: Separate the 4.79s labels without jumping to the opposite image edge.
        Inputs: Two actual observed box positions and distances, reordered/changed neighbors.
        Outputs: Nonoverlapping labels near their boxes; ordering/risk do not swap local offsets.
        Logic: Compare anchors and crowded placement. Constraints: Only bounded local avoidance.
        """
        image = Image.new("RGB", (360, 640))
        near = box(track_id=1, xyxy=[206, 175, 225, 230], distance_m=15.2,
                   display_distance_m=15.2, risk_level="low")
        far = box(track_id=35, xyxy=[251, 183, 265, 216], distance_m=20.1,
                  display_distance_m=20.1, risk_level="low")
        alone = layout_labels(image, [far])[0][2]
        self.assertGreater(alone[0], 200)
        expected = {item["track_id"]: rect for item, _, rect, _
                    in layout_labels(image, [near, far])}
        for objects in ([near, far], [far, near], [{**near, "risk_level": "critical"}, far]):
            layout = layout_labels(image, objects)
            self.assertEqual(overlap(layout[0][2], layout[1][2]), 0)
            if objects[0]["risk_level"] == "low":
                self.assertEqual({item["track_id"]: rect for item, _, rect, _ in layout}, expected)
            for item, _, rect, _ in layout:
                anchor = layout_labels(image, [item])[0][2]
                self.assertGreater(rect[0], 180)
                self.assertLessEqual(math.dist(rect[:2], anchor[:2]), 28)
                self.assertEqual(rect[1], expected[item["track_id"]][1])

    def test_label_anchor_at_image_edges(self):
        """Functionality: Clamp only to image bounds while retaining attachment to edge objects.
        Inputs: Top-left, top-right and bottom-right boxes, including a long HOLD label.
        Outputs: In-bounds labels within two pixels of their boxes.
        Logic: Measure rectangle separation. Constraints: A lone label needs no avoidance.
        """
        image = Image.new("RGB", (360, 640))
        for xyxy in ([0, 0, 20, 50], [345, 0, 360, 50], [340, 600, 360, 640]):
            item = box(xyxy=xyxy, risk_level="critical", risk_data_status="held")
            _, _, rect, _ = layout_labels(image, [item])[0]
            self.assertTrue(0 <= rect[0] < rect[2] <= 360 and 0 <= rect[1] < rect[3] <= 640)
            self.assertLessEqual(max(xyxy[0] - rect[2], rect[0] - xyxy[2], 0), 2)
            self.assertLessEqual(max(xyxy[1] - rect[3], rect[1] - xyxy[3], 0), 2)

    def test_display_distance_and_ids(self):
        """Functionality: Prefer filtered meters and opt-in IDs without pretending missing is valid.
        Inputs: Raw/filter disagreement, missing and outlier flags. Outputs: Exact label checks.
        Logic: Format current display contract. Constraints: Raw data remains separate.
        """
        item = box(risk_level="low", display_distance_m=8.2)
        self.assertEqual(label_text(item), "person | 8.2m | LOW")
        self.assertTrue(label_text(item, True).startswith("#1 "))
        self.assertIn("N/A", label_text({**item, "display_distance_m": None}))
        self.assertIn("8.2m?", label_text({**item, "distance_quality": "outlier"}))

    def test_detector_hook_order(self):
        """Functionality: Exercise refinement through the actual ResidentDetector.predict path.
        Inputs: Deterministic duplicate network rows, fake depth/tracker and real image encoding.
        Outputs: One deduplicated near object and raw audit. Logic: Assert association sees one
        candidate and risk receives refined distance. Constraints: No GPU model accuracy claim.
        """
        prediction = np.zeros((1, 300, 6), dtype=np.float32)
        prediction[0, :2] = [[200, 180, 400, 450, .95, 0], [201, 181, 401, 451, .8, 0]]
        runner = MagicMock(return_value=(prediction, 1.))
        runner.setup_ms, runner.forward_event_ms, runner.evidence = 0., 0., {}
        detector = object.__new__(ResidentDetector)
        detector.model, detector.runner = None, runner
        detector.tensor = np.empty((1, 3, 640, 640), dtype=np.float32)
        detector.execution_config, detector.precision, detector.backend = {}, "FP32", "test"
        detector.manifest = {"names": ["person"] * 80, "source_sha256": "a" * 64}
        detector.depth_estimator = MagicMock(model_name="fixture", backend="torch")
        detector.depth_estimator.predict.return_value = np.full((640, 360), 1.4, np.float32)
        detector.video_refinement = self.policy
        tracker = MagicMock(epoch=0)
        tracker.associate.side_effect = lambda image, boxes: [{**b, "track_id": 1} for b in boxes]
        tracker.enrich.side_effect = lambda boxes, timestamp: boxes
        tracker.metadata.return_value = {"tracking_model": "botsort"}
        detector.object_tracker = tracker
        buffer = io.BytesIO()
        Image.new("RGB", (360, 640)).save(buffer, "JPEG")
        with patch("realtime.detector.GraphRunner", return_value=runner):
            metadata, _ = detector.predict(buffer.getvalue(), timestamp=0.)
        self.assertEqual(len(tracker.associate.call_args.args[1]), 1)
        self.assertEqual(metadata["count"], 1)
        self.assertAlmostEqual(metadata["boxes"][0]["display_distance_m"], 1.4, places=5)
        self.assertEqual(len(metadata["refinement_audit"]["raw_detections"]), 2)
        self.assertEqual(metadata["refinement_profile"], "video-stable-v6")
