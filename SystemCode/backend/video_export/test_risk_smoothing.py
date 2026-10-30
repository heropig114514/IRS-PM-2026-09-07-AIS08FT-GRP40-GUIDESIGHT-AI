"""Responsibilities: Verify smoothing, missing-data bounds, urgent upgrades and alert eligibility.
Implementation: Deterministic source-time risk sequences independent of model inference.
Related Modules: video_export.risk_smoothing/refinement/overlay.
Declaration Index: observation; RiskSmoothingTests; RiskSmoothingTests.setUp;
RiskSmoothingTests.step; RiskSmoothingTests.test_short_unknown_freezes_score_and_recovers;
RiskSmoothingTests.test_repeated_missing_expires_from_last_valid;
RiskSmoothingTests.test_missing_time_does_not_confirm_a_rise;
RiskSmoothingTests.test_unknown_without_history_never_becomes_low;
RiskSmoothingTests.test_new_id_class_and_long_gap_do_not_reuse_history;
RiskSmoothingTests.test_no_ghost_boxes_or_alerts_from_absence;
RiskSmoothingTests.test_score_boundaries_have_hysteresis;
RiskSmoothingTests.test_rise_and_release_latency;
RiskSmoothingTests.test_urgent_near_and_short_ttc_are_immediate;
RiskSmoothingTests.test_current_alert_filter_overrides_stable_level;
RiskSmoothingTests.test_source_time_not_frame_count; RiskSmoothingTests.test_invalid_clock.
Declaration Index (continued): RiskSmoothingTests.test_intermittent_recovery_eventually_releases;
RiskSmoothingTests.test_lower_target_changes_do_not_restart_release.
Declaration Index (continued): RiskSmoothingTests.test_urgent_floor_cancels_pending_downgrade.
Declaration Index (continued): RiskSmoothingTests.test_motion_release_requires_point_three_seconds;
RiskSmoothingTests.test_uncertain_motion_timeout_and_near_urgency;
RiskSmoothingTests.test_missing_motion_restarts_release_confirmation.
Variable Index: None. Timing bounds are synthetic acceptance checks, not safety calibration.
"""

import math
from unittest import TestCase

from video_export.overlay import label_text
from video_export.refinement import RefinementConfig
from video_export.risk_smoothing import RiskSmoother


def observation(score=15, **extra):
    """Functionality: Create raw rules-v2-shaped inputs. Inputs: Score and overrides. Outputs: Box.
    Logic: Existing 25/50/75 bands. Constraints: No synthetic score is claimed as model evidence.
    """
    level = ("unknown" if score is None else "critical" if score >= 75 else
             "high" if score >= 50 else "medium" if score >= 25 else "low")
    alert = level in ("high", "critical")
    return {"label": "person", "track_id": 1, "risk_score": score, "risk_level": level,
            "alert_required": alert, "alert_priority": 4 if level == "critical" else 3 if alert
            else 0, "risk_reasons": ["fixture"], "distance_m": 10., "ttc_sec": None,
            "distance_quality": "measured", **extra}


class RiskSmoothingTests(TestCase):
    """Functionality: Bound latency and uncertainty while verifying state isolation.
    Logic: Explicit time/score scenarios. Constraints: No GPU, audio or network services.
    """

    def setUp(self):
        """Functionality: Isolate state. Inputs: None. Outputs: Fresh fixed-profile smoother.
        Logic: Construct per test. Constraints: No cross-test caches.
        """
        self.smoother = RiskSmoother(RefinementConfig())

    def step(self, t, score=15, **extra):
        """Functionality: Advance one tracked observation. Inputs: Time/score/options. Outputs: Box.
        Logic: Use production state machine. Constraints: Source seconds must strictly increase.
        """
        return self.smoother.apply([observation(score, **extra)], t)[0]

    def test_short_unknown_freezes_score_and_recovers(self):
        """Functionality: A bad frame does not clear risk or enter the average as zero.
        Inputs: High, missing, high. Outputs: Frozen qualified hold and continuous recovery.
        Logic: Assert exact score/age and no renewed alert on hold. Constraints: Same track only.
        """
        self.step(0, 65)
        held = self.step(.05, None)
        self.assertEqual((held["risk_level"], held["risk_score"], held["risk_data_status"]),
                         ("high", 65, "held"))
        self.assertEqual(held["risk_age_s"], .05)
        self.assertIsNone(held["instant_risk"]["risk_score"])
        self.assertFalse(held["alert_required"])
        self.assertIn("[HOLD]", label_text(held))
        recovered = self.step(.1, 60)
        self.assertEqual(recovered["risk_level"], "high")
        expected = 65 + (1 - math.exp(-.05 / .35)) * (60 - 65)
        self.assertAlmostEqual(recovered["smoothed_risk_score"], expected)
        self.assertEqual(recovered["risk_data_status"], "valid")

    def test_repeated_missing_expires_from_last_valid(self):
        """Functionality: Repeated missing samples cannot extend a stale risk indefinitely.
        Inputs: Valid at zero, invalid frames through .31s. Outputs: Unknown after grace.
        Logic: Age last valid measurement, not last observed box. Constraints: Alerts stay off.
        """
        self.step(0, 65)
        for t in (.05, .1, .2, .29):
            self.assertEqual(self.step(t, None)["risk_data_status"], "held")
        expired = self.step(.31, None)
        self.assertEqual(expired["risk_level"], "unknown")
        self.assertIsNone(expired["smoothed_risk_score"])
        self.assertFalse(expired["alert_required"])

    def test_missing_time_does_not_confirm_a_rise(self):
        """Functionality: Exclude invalid intervals from confirmation and EMA weighting.
        Inputs: Pending high followed by .2s dropout. Outputs: No instant resumed upgrade.
        Logic: Restore prior score and paused confirmation. Constraints: Nonurgent far object.
        """
        self.step(0, 15)
        for i in range(1, 6):
            before = self.step(i * .04, 65)
        self.assertNotEqual(before["risk_level"], "high")
        self.step(.24, None)
        self.step(.4, None)
        recovered = self.step(.44, 65)
        self.assertEqual(recovered["risk_level"], before["risk_level"])
        expected = before["risk_score"] + (1 - math.exp(-.04 / .12)) * (65 - before["risk_score"])
        self.assertAlmostEqual(recovered["risk_score"], expected)

    def test_unknown_without_history_never_becomes_low(self):
        """Functionality: Keep first/malformed/untracked inputs unknown.
        Inputs: Missing scores and absent track. Outputs: No filtered score or alert.
        Logic: Require valid current identity/evidence. Constraints: Never initialize at zero.
        """
        for t, kwargs in ((0, {}), (.1, {"track_id": None}), (.2, {"risk_score": float("nan")})):
            box = observation(None, **kwargs)
            result = self.smoother.apply([box], t)[0]
            self.assertEqual(result["risk_level"], "unknown")
            self.assertIsNone(result["smoothed_risk_score"])

    def test_new_id_class_and_long_gap_do_not_reuse_history(self):
        """Functionality: Isolate risk across identity, class and temporal discontinuities.
        Inputs: High person, changed ID/class and long disappearance. Outputs: Independent risks.
        Logic: Check all reset boundaries. Constraints: No cross-track smoothing.
        """
        self.step(0, 90)
        self.assertEqual(self.step(.04, 15, track_id=2)["risk_level"], "low")
        self.assertEqual(self.step(.08, 15, label="car")["risk_level"], "low")
        self.assertEqual(self.step(.12, None, label="person")["risk_level"], "unknown")
        self.step(.16, 90)
        self.assertEqual(self.step(1., 15)["risk_level"], "low")

    def test_no_ghost_boxes_or_alerts_from_absence(self):
        """Functionality: History never invents an absent object's rendered box.
        Inputs: High followed by empty frames. Outputs: Empty lists, expired state.
        Logic: Process only current detections. Constraints: No stale spatial extrapolation.
        """
        self.step(0, 90)
        self.assertEqual(self.smoother.apply([], .1), [])
        self.assertEqual(self.smoother.apply([], .5), [])
        self.assertFalse(self.smoother.states)

    def test_score_boundaries_have_hysteresis(self):
        """Functionality: Stop threshold chatter without imposing an arbitrary final level.
        Inputs: Alternating 49/51 from medium, then 49/51 from high. Outputs: Existing level held.
        Logic: Both sides must cross the configured 45/55 band. Constraints: Valid, nonurgent data.
        """
        for initial, expected in ((49, "medium"), (51, "high")):
            self.smoother = RiskSmoother(RefinementConfig())
            self.step(0, initial)
            for i in range(1, 61):
                self.assertEqual(self.step(i / 30, 49 if i % 2 else 51)["risk_level"], expected)

    def test_rise_and_release_latency(self):
        """Functionality: Bound nonurgent rise and release under sustained strong evidence.
        Inputs: Low->65->15 at 28fps. Outputs: High within .5s, low within 1.5s of release.
        Logic: Measure actual first transitions including EMA and confirmation, not only timers.
        Constraints: This is a declared synthetic latency bound, not an external safety standard.
        """
        self.step(0, 15)
        first_high = None
        for i in range(1, 29):
            if self.step(i / 28, 65)["risk_level"] == "high" and first_high is None:
                first_high = i / 28
        self.assertIsNotNone(first_high)
        self.assertLessEqual(first_high, .5)
        first_low = None
        for i in range(29, 85):
            if self.step(i / 28, 15)["risk_level"] == "low" and first_low is None:
                first_low = i / 28 - 1
        self.assertIsNotNone(first_low)
        self.assertLessEqual(first_low, 1.5)

    def test_urgent_near_and_short_ttc_are_immediate(self):
        """Functionality: Bypass smoothing on the first reliable urgent high/critical observation.
        Inputs: Low followed by close hazard or reliable short TTC. Outputs: Same-frame critical.
        Logic: Exercise both bypass paths. Constraints: No required intermediate risk levels.
        """
        for options in ({"distance_m": 1.4}, {"ttc_sec": 1.0}):
            self.smoother = RiskSmoother(RefinementConfig())
            self.step(0, 15)
            urgent = self.step(.04, 90, **options)
            self.assertEqual(urgent["risk_level"], "critical")
            self.assertTrue(urgent["risk_urgent_bypass"])
            self.assertTrue(urgent["alert_required"])
            self.assertEqual(urgent["risk_score"], 90)

    def test_current_alert_filter_overrides_stable_level(self):
        """Functionality: Stable high cannot override vehicle suppression or generate hold alerts.
        Inputs: High followed by currently suppressed high and unknown.
        Outputs: Display level retained, alerts/priorities off. Logic: Current eligibility gates.
        Constraints: No pending-state refresh can renew an alert from stale evidence.
        """
        self.step(0, 65)
        suppressed = self.step(.04, 65, alert_required=False, alert_priority=0)
        self.assertEqual(suppressed["risk_level"], "high")
        self.assertFalse(suppressed["alert_required"])
        held = self.step(.08, None)
        self.assertFalse(held["alert_required"])
        self.assertEqual(held["alert_priority"], 0)

    def test_urgent_floor_cancels_pending_downgrade(self):
        """Functionality: Same-level urgency must prevent an already pending downgrade.
        Inputs: Held high with a low smoothed score, then a current close high observation.
        Outputs: High remains high with eligible alert. Logic: Urgent raw level is a lower bound.
        Constraints: Protection applies even when urgency is not an upward level transition.
        """
        self.step(0, 65)
        state = self.smoother.states[1]
        state.score, state.pending, state.pending_seconds = 40., "medium", .44
        result = self.step(.04, 67, distance_m=2.75)
        self.assertEqual(result["risk_level"], "high")
        self.assertGreaterEqual(result["risk_score"], 67)
        self.assertTrue(result["alert_required"])

    def test_source_time_not_frame_count(self):
        """Functionality: Make score filtering independent of decode/processing cadence.
        Inputs: Same step for .5s at 20fps and 40fps. Outputs: Equal filtered scores.
        Logic: Exponential time weighting composes consistently. Constraints: No dropouts.
        """
        scores = []
        for fps in (20, 40):
            self.smoother = RiskSmoother(RefinementConfig())
            self.step(0, 15)
            for i in range(1, fps // 2 + 1):
                result = self.step(i / fps, 65)
            scores.append(result["risk_score"])
        self.assertAlmostEqual(*scores)

    def test_invalid_clock(self):
        """Functionality: Reject repeated/backwards time before changing state.
        Inputs: Duplicate source timestamp. Outputs: Explicit error.
        Logic: Strict monotonicity guard. Constraints: No implicit clock repair.
        """
        self.step(1., 15)
        with self.assertRaisesRegex(ValueError, "increase strictly"):
            self.step(1., 65)

    def test_intermittent_recovery_eventually_releases(self):
        """Functionality: Prevent sticky critical risk when every other frame is unavailable.
        Inputs: Critical followed by alternating low/missing at 28fps.
        Outputs: Low within 3 source seconds, no alerts on missing frames.
        Logic: Count only each valid frame's interval toward EMA/confirmation, not the whole gap.
        Constraints: A continuing invalid stream still expires independently after .3 seconds.
        """
        self.step(0, 90)
        first_low = None
        for i in range(1, 85):
            result = self.step(i / 28, 15 if i % 2 else None)
            if i % 2 == 0:
                self.assertFalse(result["alert_required"])
            if result["risk_level"] == "low" and first_low is None:
                first_low = i / 28
        self.assertIsNotNone(first_low)
        self.assertLessEqual(first_low, 3.)

    def test_lower_target_changes_do_not_restart_release(self):
        """Functionality: Accumulate evidence of decreasing risk across lower candidate levels.
        Inputs: Filtered low/medium alternation under previously critical risk.
        Outputs: A downgrade after enough valid evidence. Logic: Confirm direction, not exact label.
        Constraints: Opposite direction or return to the current level still clears confirmation.
        """
        self.step(0, 90)
        state = self.smoother.states[1]
        for i in range(1, 16):
            state.score = 10. if i % 2 else 35.
            result = self.step(i / 28, state.score)
        self.assertNotEqual(result["risk_level"], "critical")

    def test_motion_release_requires_point_three_seconds(self):
        """Functionality: Confirm weaker motion for 0.3s before permitting score decay.
        Inputs: Reliable approach then stationary observations. Outputs: Bounded hold then decay.
        Logic: Check both sides of the source-time boundary and subsequent recovery.
        Constraints: Existing score/level smoothing still applies after motion confirmation.
        """
        self.step(0, 40, motion_state="approaching", motion_quality="measured")
        for t in (.05, .15, .25, .34):
            result = self.step(t, 15, motion_state="stationary", motion_quality="measured")
            self.assertEqual(result["risk_score"], 40)
            self.assertIn("motion_release_pending", result["risk_reasons"])
        result = self.step(.36, 15, motion_state="stationary", motion_quality="measured")
        self.assertLess(result["risk_score"], 40)
        self.assertNotIn("motion_release_pending", result["risk_reasons"])
        for i in range(4, 21):
            result = self.step(i / 10, 15, motion_state="stationary", motion_quality="measured")
        self.assertEqual(result["risk_level"], "low")

    def test_uncertain_motion_timeout_and_near_urgency(self):
        """Functionality: Noisy velocity pauses decay briefly without a permanent risk latch.
        Inputs: Approach, alternating noisy/stationary distance-only low, then a close hazard.
        Outputs: Frozen score for .3s, then distance-only decay, immediate close critical.
        Logic: Check score, raw audit and alert gates. Constraints: Current depth remains available.
        """
        self.step(0, 40, motion_state="approaching", motion_quality="measured")
        for t in (.05, .15, .25, .34):
            measured = t == .15
            result = self.step(t, 15, motion_state="stationary" if measured else "unknown",
                               motion_quality="measured" if measured else "uncertain")
            self.assertEqual(result["risk_level"], "medium")
            self.assertEqual(result["risk_score"], 40)
            if not measured:
                self.assertIn("motion_uncertain", result["risk_reasons"])
            self.assertEqual(result["instant_risk"]["risk_level"], "low")
            self.assertFalse(result["alert_required"])
        result = self.step(.36, 15, motion_state="unknown", motion_quality="uncertain")
        self.assertLess(result["risk_score"], 40)
        for i in range(4, 21):
            result = self.step(i / 10, 15, motion_state="unknown", motion_quality="uncertain")
        self.assertEqual(result["risk_level"], "low")
        self.smoother = RiskSmoother(RefinementConfig())
        self.step(0, 40, motion_state="approaching", motion_quality="measured")
        self.step(.05, 15, motion_state="unknown", motion_quality="uncertain")
        result = self.step(.1, 90, distance_m=1.4, motion_quality="uncertain")
        self.assertEqual(result["risk_level"], "critical")
        self.assertTrue(result["alert_required"])

    def test_missing_motion_restarts_release_confirmation(self):
        """Functionality: A missing observation breaks continuous motion-release evidence.
        Inputs: Partial stationary confirmation, missing frame and renewed stationary evidence.
        Outputs: No accumulated release across the gap. Logic: Inspect score before new .3s.
        Constraints: Risk validity grace still limits the lifetime of the previous observation.
        """
        self.step(0, 40, motion_state="approaching", motion_quality="measured")
        for t in (.05, .15, .25):
            self.step(t, 15, motion_state="stationary", motion_quality="measured")
        self.step(.29, None)
        for t in (.33, .43, .53, .62):
            result = self.step(t, 15, motion_state="stationary", motion_quality="measured")
            self.assertEqual(result["risk_score"], 40)
        result = self.step(.64, 15, motion_state="stationary", motion_quality="measured")
        self.assertLess(result["risk_score"], 40)
