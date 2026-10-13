"""Responsibilities: Verify persistent BoT-SORT/CMC, bounded motion history and full fusion flow.
Implementation: Deterministic frames/distances; real SDK association; controlled GPU predictions.
Related Modules: realtime.tracking/detector/depth/risk/protocol; zerogpu.live_worker; core.detector.
Declaration Index: tracked_box; HistoryTests; HistoryTests.test_approaching;
HistoryTests.test_moving_away; HistoryTests.test_stationary; HistoryTests.test_unknown_and_time;
HistoryTests.test_bounded_history; HistoryTests.test_invalid_depth; HistoryTests.test_stale_cleanup;
HistoryTests.test_class_and_identity_changes; HistoryTests.test_hysteresis;
HistoryTests.test_expired_depth_and_reset; HistoryTests.test_configuration;
HistoryTests.test_noisy_approaching; HistoryTests.test_irregular_time_and_regression;
HistoryTests.test_track_isolation; HistoryTests.test_missing_depth_continuity;
HistoryTests.test_thresholds_and_stabilization;
BoTSORTTests; BoTSORTTests.test_stable_ids_and_persistence;
BoTSORTTests.test_mobile_camera_compensation; BoTSORTTests.test_temporary_occlusion;
BoTSORTTests.test_unconfirmed_and_geometry;
BoTSORTTests.test_reset_and_failure; BoTSORTTests.test_pipeline_integration;
ContractTests; ContractTests.test_labels; ContractTests.test_packet_capacity;
ContractTests.test_worker_handoff; ContractTests.test_deployment_closure.
Variable Index: HAS_TRACKER — optional installed SDK/lap availability for association checks;
no model downloads/GPU inference/physical accuracy claimed by synthetic tests.
"""

import ast
import hashlib
import importlib.util
import json
from dataclasses import asdict
from pathlib import Path
from unittest import skipUnless
from unittest.mock import MagicMock, patch

import numpy as np
from django.test import SimpleTestCase
from PIL import Image

from core.detector import annotate
from core.test_frames import jpeg
from realtime.detector import ResidentDetector
from realtime.protocol import pack_result, unpack_result
from realtime.tracking import (
    ObjectTracker,
    TrackHistory,
    TrackingConfig,
    motion_candidate,
    read_tracking_config,
)

HAS_TRACKER = all(importlib.util.find_spec(name) is not None for name in ("ultralytics", "lap"))


def tracked_box(distance, identity=7, name="person"):
    """Functionality: Produce an explicit original/fused box for independent history assertions.
    Inputs: nullable meters, identity and class name. Outputs: compatible dict.
    Logic: fixed valid coordinates/confidence. Constraints: synthetic data, not model predictions.
    """
    return {"track_id": identity, "class_name": name, "label": name, "class_id": 0,
            "confidence": .93, "score": .93, "bbox": [20., 30., 80., 130.],
            "xyxy": [20., 30., 80., 130.], "distance_m": distance}


class HistoryTests(SimpleTestCase):
    """Functionality: Verify distance filtering, smoothing, motion and lifecycle independently.
    Logic: explicit same-clock seconds and known trends. Constraints: no physical accuracy claim.
    """

    def test_approaching(self):
        """Functionality: Classify decreasing depth after two eligible regression updates.
        Inputs:5.0/4.5/4.0/3.5m at half-second intervals. Outputs: approaching, negative speed.
        Logic: independent EMA5/4.75/4.375/3.9375 and OLS-0.7125 across all four points.
        Constraints: first two points have no speed; third point awaits confirmation.
        """
        history = TrackHistory(TrackingConfig())
        output = [history.update([tracked_box(value)], index / 2)[0]
                  for index, value in enumerate((5., 4.5, 4., 3.5))]
        self.assertEqual([box["motion_state"] for box in output],
                         ["unknown", "unknown", "unknown", "approaching"])
        self.assertTrue(all(box["relative_speed_mps"] is None for box in output[:2]))
        self.assertAlmostEqual(output[-1]["smoothed_distance_m"], 3.9375)
        self.assertAlmostEqual(output[-1]["relative_speed_mps"], -.7125)
        self.assertEqual(output[-1]["distance_m"], 3.5)

    def test_moving_away(self):
        """Functionality: Preserve positive speed sign for increasing depth.
        Inputs:2.0/2.5/3.0/3.5m. Outputs: moving_away. Logic: two eligible OLS estimates.
        Constraints: same timing/smoothing/thresholds as approaching test.
        """
        history = TrackHistory(TrackingConfig())
        for index, value in enumerate((2., 2.5, 3., 3.5)):
            result = history.update([tracked_box(value)], index / 2)[0]
        self.assertEqual(result["motion_state"], "moving_away")
        self.assertGreater(result["relative_speed_mps"], 0)

    def test_stationary(self):
        """Functionality: Keep small depth noise in the stationary dead band.
        Inputs:3.0/3.05/2.98/3.02m. Outputs: stationary. Logic: smoothed small-noise regression.
        Constraints: does not mean the object is stationary in world coordinates.
        """
        history = TrackHistory(TrackingConfig())
        for index, value in enumerate((3., 3.05, 2.98, 3.02)):
            result = history.update([tracked_box(value)], index / 2)[0]
        self.assertEqual(result["motion_state"], "stationary")
        self.assertLess(abs(result["relative_speed_mps"]), .2)

    def test_unknown_and_time(self):
        """Functionality: Reject insufficient/too-close measurements and backward clocks.
        Inputs: three points within20ms, missing ID, duplicate timestamp. Outputs: unknown/no speed.
        Logic: minimum span and strict clock boundary. Constraints: never divide by zero.
        """
        insufficient = TrackHistory(TrackingConfig())
        for timestamp, distance in ((0., 4.), (.5, 3.8)):
            estimate = insufficient.update_motion_state(7, distance, timestamp)
            self.assertEqual(estimate.motion_state, "unknown")
            self.assertIsNone(estimate.relative_speed_mps)
        history = TrackHistory(TrackingConfig())
        for timestamp in (0., .01, .02):
            result = history.update([tracked_box(3.)], timestamp)[0]
            self.assertEqual(result["motion_state"], "unknown")
            self.assertIsNone(result["relative_speed_mps"])
        self.assertEqual(history.update([tracked_box(3., None)], .03)[0]["motion_state"], "unknown")
        for timestamp in (.03, .02, float("nan"), float("inf"), -1, True, None, "1"):
            with self.assertRaises(ValueError):
                history.update([], timestamp)

    def test_bounded_history(self):
        """Functionality: Bound aligned raw/time/EMA storage despite many valid observations.
        Inputs:20 known points at100ms intervals. Outputs: only last8 samples in each queue.
        Logic: compare retained input suffix. Constraints: no unbounded per-ID media buffers.
        """
        history = TrackHistory(TrackingConfig())
        values = [5 - index * .01 for index in range(20)]
        for index, value in enumerate(values):
            history.update([tracked_box(value)], index / 10)
        state = history.tracks[7]
        self.assertEqual(list(state.distance_history), values[-8:])
        self.assertEqual(len(state.timestamp_history), 8)
        self.assertEqual(len(state.smoothed_history), 8)

    def test_invalid_depth(self):
        """Functionality: Ignore interleaved missing/invalid depths without inserting fake values.
        Inputs:4.0/None/3.6/NaN/3.2 then other invalid values. Outputs: only three valid samples.
        Logic: invalid updates do not advance smoothing/regression/confirmation.
        Constraints: insufficient initial confirmation remains unknown; raw depth is not rewritten.
        """
        history = TrackHistory(TrackingConfig())
        for index, value in enumerate((4., None, 3.6, float("nan"), 3.2)):
            result = history.update([tracked_box(value)], index / 4)[0]
        self.assertAlmostEqual(result["smoothed_distance_m"], 3.5)
        self.assertEqual(result["motion_state"], "unknown")
        before = history.tracks[7].estimate()
        for index, invalid in enumerate((None, float("nan"), float("inf"), 0, -1, True, "bad")):
            history.update([tracked_box(invalid)], 1.1 + index / 10)
            self.assertEqual(history.tracks[7].estimate(), before)
            self.assertEqual(list(history.tracks[7].distance_history), [4., 3.6, 3.2])
        self.assertAlmostEqual(history.tracks[7].last_seen_timestamp, 1.7)

    def test_stale_cleanup(self):
        """Functionality: Retain temporary disappearance then expire absent tracks by time.
        Inputs: observation at0, empty frames at2/2.01 seconds. Outputs: retained then removed.
        Logic: exact timeout boundary. Constraints: cleanup also runs on frames without boxes.
        """
        history = TrackHistory(TrackingConfig())
        history.update([tracked_box(3.)], 0.)
        history.update([], 2.)
        self.assertIn(7, history.tracks)
        history.update([], 2.01)
        self.assertEqual(history.tracks, {})

    def test_class_and_identity_changes(self):
        """Functionality: Keep unrelated classes/new IDs from inheriting another object's speed.
        Inputs: approaching person7, then car7 and person8. Outputs: fresh unknown histories.
        Logic: explicitly restart only the changed identity. Constraints: no guessed ID handover.
        """
        history = TrackHistory(TrackingConfig(stale_timeout_s=20))
        for index, value in enumerate((5., 4.5, 4., 3.5)):
            history.update([tracked_box(value)], index / 2)
        output = history.update([tracked_box(8., name="car"), tracked_box(3., identity=8)], 2.)
        self.assertEqual([box["motion_state"] for box in output], ["unknown", "unknown"])
        self.assertEqual(list(history.tracks[7].distance_history), [8.])
        self.assertEqual(list(history.tracks[8].distance_history), [3.])

    def test_hysteresis(self):
        """Functionality: Require consecutive eligible candidates when changing established state.
        Inputs: decreasing then increasing3-point windows. Outputs: delayed moving_away switch.
        Logic: one stationary candidate and one away candidate cannot switch approaching.
        Constraints: explicit alpha/window overrides are isolated test fixtures.
        """
        history = TrackHistory(TrackingConfig(ema_alpha=1., history_size=3))
        outputs = [history.update([tracked_box(value)], index)[0]["motion_state"]
                   for index, value in enumerate((6., 5., 4., 3., 4., 5., 6.))]
        self.assertEqual(outputs, ["unknown", "unknown", "unknown", "approaching", "approaching",
                                   "approaching", "moving_away"])

    def test_expired_depth_and_reset(self):
        """Functionality: Expire old depth even when a visually present ID has invalid depth.
        Inputs: one valid sample then missing depth after3s; reset and reused ID.
        Outputs: empty samples/null smoothing and fresh unknown. Logic: measurement timeout/reset.
        Constraints: old model distances cannot survive a new stream.
        """
        history = TrackHistory(TrackingConfig(stale_timeout_s=10))
        history.update([tracked_box(3.)], 0.)
        history.update([tracked_box(None)], 1.5)
        result = history.update([tracked_box(None)], 3.)[0]
        self.assertIsNone(result["smoothed_distance_m"])
        self.assertEqual(len(history.tracks[7].distance_history), 0)
        history.reset()
        self.assertEqual(history.update([tracked_box(5.)], 0.)[0]["motion_state"], "unknown")

    def test_configuration(self):
        """Functionality: Reject invalid limits, alternate trackers and nonfinite thresholds.
        Inputs: explicit invalid config fields and defaults. Outputs: errors/one canonical profile.
        Logic: dataclass validation. Constraints: no parameter coercion or fallback.
        """
        for changes in ({"tracker": "bytetrack.yaml"}, {"min_valid_samples": 2},
                        {"history_size": 1000}, {"history_size": True}, {"ema_alpha": 2},
                        {"confirmation_updates": 1}, {"stale_timeout_s": 0},
                        {"motion_history_timeout_s": 0},
                        {"approaching_threshold_mps": float("nan")}, {"min_time_span_s": 3}):
            with self.assertRaises(ValueError):
                TrackingConfig(**changes)
        self.assertEqual(asdict(read_tracking_config()), asdict(TrackingConfig()))

    def test_noisy_approaching(self):
        """Functionality: Recover approaching despite one noisy upward raw-depth fluctuation.
        Inputs:5.0/4.7/4.8/4.2/4.0 at quarter-second times. Outputs: smoothed approaching.
        Logic: default EMA and whole-window OLS. Constraints: no rejection of finite positive noise.
        """
        history = TrackHistory(TrackingConfig())
        values = [5., 4.7, 4.8, 4.2, 4.]
        for index, distance in enumerate(values):
            estimate = history.update_motion_state(7, distance, index / 4)
        self.assertEqual(estimate.motion_state, "approaching")
        self.assertLess(estimate.relative_speed_mps, -.2)
        self.assertAlmostEqual(estimate.smoothed_distance_m, 4.25625)
        self.assertEqual(list(history.tracks[7].distance_history), values)

    def test_irregular_time_and_regression(self):
        """Functionality: Fit all observations at actual irregular times, including large offsets.
        Inputs: known rational OLS fixture and equal-endpoint/interior-noise fixture.
        Outputs: slope-316/355 and nonzero slope despite equal endpoints.
        Logic: independently calculated covariance/variance; alpha1 isolates time/regression.
        Constraints: explicit fixture config, not a production parameter change.
        """
        history = TrackHistory(TrackingConfig(ema_alpha=1.))
        for offset, distance in zip((0., .125, .5, 1.5), (5., 4.5, 4.5, 3.5), strict=True):
            estimate = history.update_motion_state(7, distance, 1_000_000_000. + offset)
        self.assertAlmostEqual(estimate.relative_speed_mps, -316 / 355)
        self.assertNotAlmostEqual(estimate.relative_speed_mps, (3.5 - 5.) / 1.5)
        self.assertEqual(estimate.motion_state, "approaching")
        other = TrackHistory(TrackingConfig(ema_alpha=1.))
        for timestamp, distance in zip((0., .4, .7, 1.2), (3., 2.6, 3.4, 3.), strict=True):
            estimate = other.update_motion_state(7, distance, timestamp)
        self.assertAlmostEqual(estimate.relative_speed_mps, .12 / .7675)
        self.assertGreater(estimate.relative_speed_mps, .1)

    def test_track_isolation(self):
        """Functionality: Reuse the public API for two IDs observed at each shared frame time.
        Inputs: decreasing ID7 and increasing ID9. Outputs: isolated opposite motion estimates.
        Logic: inspect independently bounded raw queues and reject replay of one track timestamp.
        Constraints: same-frame equality is valid across IDs; missing IDs cannot inherit history.
        """
        history = TrackHistory(TrackingConfig())
        for index in range(4):
            approaching = history.update_motion_state(7, 5. - index / 2, index / 2)
            away = history.update_motion_state(9, 2. + index / 2, index / 2)
        self.assertEqual(approaching.motion_state, "approaching")
        self.assertEqual(away.motion_state, "moving_away")
        self.assertEqual(list(history.tracks[7].distance_history), [5., 4.5, 4., 3.5])
        self.assertEqual(list(history.tracks[9].distance_history), [2., 2.5, 3., 3.5])
        with self.assertRaises(ValueError):
            history.update_motion_state(7, 3., 1.5)
        self.assertEqual(history.update_motion_state(None, 3., 1.5).motion_state, "unknown")

    def test_missing_depth_continuity(self):
        """Functionality: Preserve a published estimate for short gaps, then expire only motion.
        Inputs: approaching track, missing depth, resumed depth, missing beyond valid-depth timeout.
        Outputs: preserved motion and unchanged raw None, then unknown/null speed with same ID.
        Logic: visual timeout and motion timeout are independent. Constraints: no inserted samples.
        """
        history = TrackHistory(TrackingConfig(stale_timeout_s=10.))
        for index, distance in enumerate((5., 4.5, 4., 3.5)):
            valid = history.update([tracked_box(distance)], index / 4)[0]
        missing = history.update([tracked_box(None)], 1.)[0]
        self.assertIsNone(missing["distance_m"])
        for field in ("smoothed_distance_m", "relative_speed_mps", "motion_state"):
            self.assertEqual(missing[field], valid[field])
        self.assertEqual(len(history.tracks[7].distance_history), 4)
        resumed = history.update([tracked_box(3.)], 1.25)[0]
        self.assertEqual(resumed["motion_state"], "approaching")
        history.update([tracked_box(None)], 2.)
        at_boundary = history.update([tracked_box(None)], 3.25)[0]
        self.assertEqual(at_boundary["motion_state"], "approaching")
        expired = history.update([tracked_box(None)], 3.251)[0]
        self.assertEqual(expired["track_id"], 7)
        self.assertEqual(expired["motion_state"], "unknown")
        self.assertIsNone(expired["smoothed_distance_m"])
        self.assertIsNone(expired["relative_speed_mps"])
        self.assertEqual(len(history.tracks[7].distance_history), 0)
        fresh = history.update([tracked_box(2.)], 3.5)[0]
        self.assertEqual(fresh["motion_state"], "unknown")
        self.assertEqual(fresh["smoothed_distance_m"], 2.)

    def test_thresholds_and_stabilization(self):
        """Functionality: Use inclusive thresholds and suppress alternating near-threshold labels.
        Inputs: exact +/-0.2 and successive slopes around the approaching boundary.
        Outputs: one approaching confirmation, retained approach, then confirmed stationary.
        Logic: candidate changes clear pending confirmation; no two-raw-frame classification.
        Constraints: explicit alpha1/window3 fixtures isolate hysteresis from smoothing effects.
        """
        config = TrackingConfig(ema_alpha=1., history_size=3)
        self.assertEqual(motion_candidate(-.2, config), "approaching")
        self.assertEqual(motion_candidate(.2, config), "moving_away")
        self.assertEqual(motion_candidate(0., config), "stationary")
        history = TrackHistory(config)
        values = [5., 4.79, 4.58, 4.35, 4.20, 3.93, 3.82, 3.57]
        labels = [history.update_motion_state(7, distance, timestamp).motion_state
                  for timestamp, distance in enumerate(values)]
        self.assertEqual(labels, ["unknown"] * 3 + ["approaching"] * 4 + ["stationary"])


@skipUnless(HAS_TRACKER, "Ultralytics/lap belong to the inference environment")
class BoTSORTTests(SimpleTestCase):
    """Functionality: Exercise actual BoT-SORT/CMC and the inherited fusion path.
    Logic: deterministic JPEG/box/depth fixtures and installed pinned association code.
    Constraints: mock detection/depth predictions do not prove video identity or range accuracy.
    """

    def test_stable_ids_and_persistence(self):
        """Functionality: Reuse one actual tracker/GMC across consecutive frames.
        Inputs: repeated original boxes. Outputs: stable ID and original bbox/score/class.
        Logic: inspect object identity/frame count and forbid reset calls.
        Constraints: static frames are a persistence check, not an occlusion benchmark.
        """
        tracker = ObjectTracker(TrackingConfig(), "8.4.171")
        image = Image.new("RGB", (160, 160))
        original = tracked_box(3.)
        instance = tracker.tracker
        with patch.object(instance, "reset", wraps=instance.reset) as reset:
            output = [tracker.associate(image, [original])[0] for _ in range(5)]
            reset.assert_not_called()
        self.assertIs(tracker.tracker, instance)
        self.assertEqual(instance.frame_id, 5)
        self.assertEqual(len({box["track_id"] for box in output}), 1)
        self.assertIsNotNone(output[0]["track_id"])
        for box in output:
            for key in ("xyxy", "score", "label", "class_id"):
                self.assertEqual(box[key], original[key])
        self.assertEqual(instance.gmc.method, "sparseOptFlow")
        self.assertIsNone(instance.encoder)

    def test_mobile_camera_compensation(self):
        """Functionality: Keep identity under a camera translation larger than the object's width.
        Inputs: real textured sample shifted60px and a40px-wide box shifted with it.
        Outputs: same BoT-SORT ID with enabled actual optical-flow GMC.
        Logic: without warp boxes do not overlap. Constraints: planar translation only.
        """
        tracker = ObjectTracker(TrackingConfig(), "8.4.171")
        with Image.open(Path(__file__).parent / "samples/bus.jpg") as source:
            first = source.convert("RGB").resize((480, 640))
        second = Image.new("RGB", first.size)
        second.paste(first, (60, 0))
        box = tracked_box(3.)
        box["xyxy"] = [100., 200., 140., 360.]
        first_id = tracker.associate(first, [box])[0]["track_id"]
        box["xyxy"] = [160., 200., 200., 360.]
        second_id = tracker.associate(second, [box])[0]["track_id"]
        self.assertIsNotNone(first_id)
        self.assertEqual(first_id, second_id)
        self.assertTrue(tracker.available)
        self.assertIsNotNone(tracker.tracker.gmc.prevKeyPoints)

    def test_temporary_occlusion(self):
        """Functionality: Retain lost-track identity through a short empty detection frame.
        Inputs: detected/empty/detected frames. Outputs: same ID, no imaginary occluded detection.
        Logic: actual BoT-SORT lost buffer and continued CMC updates. Constraints: brief gap only.
        """
        tracker = ObjectTracker(TrackingConfig(), "8.4.171")
        image = Image.new("RGB", (160, 160))
        first = tracker.associate(image, [tracked_box(3.)])[0]["track_id"]
        self.assertEqual(tracker.associate(image, []), [])
        second = tracker.associate(image, [tracked_box(3.)])[0]["track_id"]
        self.assertEqual(first, second)

    def test_reset_and_failure(self):
        """Functionality: Report/latch failed CMC, preserve detections and explicitly reset history.
        Inputs: one tracked sample then GMC exception. Outputs: null ID/unknown without retry.
        Logic: error path must not silently switch to identity warp or ByteTrack.
        Constraints: reset invoked only outside the frame loop.
        """
        tracker = ObjectTracker(TrackingConfig(), "8.4.171")
        image = Image.new("RGB", (160, 160))
        tracker.enrich(tracker.associate(image, [tracked_box(3.)]), 0.)
        with patch.object(tracker.tracker.gmc, "apply", side_effect=RuntimeError("test GMC")):
            failed = tracker.enrich(tracker.associate(image, [tracked_box(3.)]), 1.)[0]
        self.assertIsNone(failed["track_id"])
        self.assertEqual(failed["motion_state"], "unknown")
        self.assertEqual(tracker.metadata()["tracking_status"], "unavailable")
        with patch.object(tracker.tracker, "update") as update:
            tracker.associate(image, [tracked_box(3.)])
            update.assert_not_called()
        tracker.reset()
        self.assertEqual(tracker.epoch, 1)
        self.assertEqual(tracker.history.tracks, {})
        self.assertIsNone(tracker.tracker.gmc.prevFrame)
        self.assertTrue(tracker.available)
        tracker.enrich(tracker.associate(image, [tracked_box(5.)]), 0.)
        self.assertEqual(next(iter(tracker.history.tracks.values())).motion_state, "unknown")

    def test_unconfirmed_and_geometry(self):
        """Functionality: Retain detections before ID confirmation and reject changed video size.
        Inputs: empty first frame, new detection, confirmation, changed geometry.
        Outputs: null ID then stable ID, latched unavailability on geometry change.
        Logic: real SDK pending track and session boundary.
        Constraints: never resets in association.
        """
        tracker = ObjectTracker(TrackingConfig(), "8.4.171")
        image = Image.new("RGB", (160, 160))
        self.assertEqual(tracker.associate(image, []), [])
        pending = tracker.enrich(tracker.associate(image, [tracked_box(3.)]), 0.)[0]
        self.assertIsNone(pending["track_id"])
        self.assertEqual(pending["xyxy"], tracked_box(3.)["xyxy"])
        self.assertEqual(pending["motion_state"], "unknown")
        confirmed = tracker.enrich(tracker.associate(image, [tracked_box(3.)]), 1.)[0]
        self.assertIsNotNone(confirmed["track_id"])
        with patch.object(tracker.tracker, "reset") as reset:
            changed = tracker.associate(Image.new("RGB", (320, 160)), [tracked_box(3.)])[0]
            reset.assert_not_called()
        self.assertIsNone(changed["track_id"])
        self.assertFalse(tracker.available)
        self.assertEqual(tracker.history.tracks, {})

    def test_pipeline_integration(self):
        """Functionality: Fuse actual BoT-SORT IDs, depth medians, motion/TTC/risk and wire output.
        Inputs: graph fixture,4.8/4.3/3.7/3.2m planes at0/0.5/1/1.5s, then missing depth.
        Outputs: stable ID, confirmed approaching/TTC, cached motion but unknown risk without depth.
        Logic: one graph/tracker across all frames; last zero plane has no valid ROI depth.
        Constraints: CPU fusion and controlled GPU predictions; actual models tested separately.
        """
        rows = np.zeros((1, 300, 6), dtype=np.float32)
        rows[0, 0] = [120, 140, 360, 460, .93, 0]
        runner = MagicMock(return_value=(rows, 1.))
        runner.setup_ms, runner.forward_event_ms, runner.evidence = 2., .5, {}
        resident = object.__new__(ResidentDetector)
        resident.model, resident.runner = None, None
        resident.tensor = np.empty((1, 3, 640, 640), dtype=np.float32)
        resident.execution_config, resident.precision, resident.backend = {}, "FP32", "test CUDA"
        resident.manifest = {"names": ["person"] * 80, "source_sha256": "a" * 64}
        depth = MagicMock(model_name="yolo26n-depth")
        depth.predict.side_effect = [np.full((80, 120), value, dtype=np.float32)
                                     for value in (4.8, 4.3, 3.7, 3.2, 0.)]
        resident.depth_estimator = depth
        resident.object_tracker = ObjectTracker(TrackingConfig(), "8.4.171")
        with patch("realtime.detector.GraphRunner", return_value=runner) as factory:
            outputs = [resident.predict(jpeg(), timestamp=timestamp)
                       for timestamp in (0., .5, 1., 1.5, 1.75)]
            factory.assert_called_once()
        boxes = [metadata["boxes"][0] for metadata, _ in outputs]
        self.assertEqual(len({box["track_id"] for box in boxes}), 1)
        self.assertEqual(boxes[-1]["motion_state"], "approaching")
        self.assertLess(boxes[-1]["relative_speed_mps"], 0)
        self.assertAlmostEqual(boxes[-2]["distance_m"], 3.2, places=5)
        self.assertIsNone(boxes[-1]["distance_m"])
        self.assertEqual(boxes[-1]["relative_speed_mps"], boxes[-2]["relative_speed_mps"])
        self.assertAlmostEqual(boxes[-2]["ttc_sec"], boxes[-2]["smoothed_distance_m"] /
                               -boxes[-2]["relative_speed_mps"])
        self.assertIsNone(boxes[-1]["ttc_sec"])
        self.assertEqual(boxes[-1]["risk_level"], "unknown")
        self.assertFalse(boxes[-1]["alert_required"])
        self.assertEqual(resident.object_tracker.tracker.frame_id, 5)
        self.assertEqual(len({meta["raw_sha256"] for meta, _ in outputs}), 1)
        depth.predict.assert_called()
        for metadata, rendered in outputs:
            packet = pack_result({**metadata, "index": 0}, rendered)
            self.assertEqual(unpack_result(packet, 0), ({**metadata, "index": 0}, rendered))
            self.assertTrue(metadata["tracking_ms"] >= 0)
            self.assertEqual(metadata["risk_model"], "rules-v1")
            self.assertGreaterEqual(metadata["risk_ms"], 0)


class ContractTests(SimpleTestCase):
    """Functionality: Verify labels, maximum packet and worker/publisher lifecycle contracts.
    Logic: real encoding/framing and explicit handoff doubles. Constraints: no external deployment.
    """

    def test_labels(self):
        """Functionality: Show signed speed for known motion, omit it while unknown/unavailable.
        Inputs: full box, unknown, null speed, no ID or missing distance. Outputs: exact text.
        Logic: inspect actual annotate text calls. Constraints: no phone/layout accuracy claim.
        """
        for changes, expected in (({}, "ID 7 | person 0.93 | 3.2 m | approaching | -0.74 m/s"),
                                  ({"motion_state": "moving_away", "relative_speed_mps": .74},
                                   "ID 7 | person 0.93 | 3.2 m | moving_away | +0.74 m/s"),
                                  ({"motion_state": "unknown"},
                                   "ID 7 | person 0.93 | 3.2 m | unknown"),
                                  ({"relative_speed_mps": None},
                                   "ID 7 | person 0.93 | 3.2 m | approaching"),
                                  ({"track_id": None}, "person 0.93 | 3.2 m"),
                                  ({"distance_m": None}, "person 0.93 | N/A")):
            box = {**tracked_box(3.2), "motion_state": "approaching",
                   "relative_speed_mps": -.74, **changes}
            with patch("PIL.ImageDraw.Draw") as drawing:
                drawing.return_value.textbbox.return_value = (0, 0, 100, 20)
                annotate(Image.new("RGB", (160, 160)), [box])
                self.assertEqual(drawing.return_value.text.call_args.args[1], expected)

    def test_packet_capacity(self):
        """Functionality: Carry300 full-precision tracked objects beyond the old128KiB limit.
        Inputs: long valid numerical fields and safe max IDs. Outputs: intact256KiB-bounded packet.
        Logic: actual compact serialization and JPEG fingerprint. Constraints: no field truncation.
        """
        box = tracked_box(3.234567890123456)
        box.update(xyxy=[12.34567890123456, 2.34567890123456, 99.34567890123456,
                         75.34567890123456], score=.9312345678901234,
                   confidence=.9312345678901234, class_name="baseball glove",
                   label="baseball glove",
                   smoothed_distance_m=3.234567890123456, relative_speed_mps=-.1234567890123456,
                   motion_state="approaching")
        box["bbox"] = list(box["xyxy"])
        boxes = [{**box, "track_id": 2**53 - 1 - index} for index in range(300)]
        raw = jpeg()
        metadata = {"index": 0, "boxes": boxes, "jpeg_sha256": hashlib.sha256(raw).hexdigest()}
        self.assertGreater(len(json.dumps(metadata, separators=(",", ":")).encode()), 128 * 1024)
        self.assertEqual(unpack_result(pack_result(metadata, raw), 0), (metadata, raw))

    def test_worker_handoff(self):
        """Functionality: Forward one immutable profile and depth estimator to one session factory.
        Inputs: explicit end-before-frame handoffs. Outputs: one construction with both keywords.
        Logic: actual run_worker orchestration. Constraints: no sockets/GPU allocation/retries.
        """
        from zerogpu.live_worker import run_worker

        config, depth, factory = TrackingConfig(), object(), MagicMock()
        with patch("zerogpu.live_worker.exchange", side_effect=[(200, b""), (410, b""),
                                                               (200, b"")]):
            run_worker("a" * 64, None, None, factory=factory, config={},
                       depth_estimator=depth, tracking_config=config)
        factory.assert_called_once_with(None, None, {}, depth_estimator=depth,
                                        tracking_config=config)

    def test_deployment_closure(self):
        """Functionality: Include tracking and stateless risk in both maintained source bundles.
        Inputs: publisher AST allowlists. Outputs: explicit mapping/list membership.
        Logic: parse without importing publishers/credentials. Constraints: no remote calls.
        """
        root = Path(__file__).resolve().parents[1]
        for filename, key in (("deploy_space.py", "FILES"), ("deploy_phone_live.py", "BACKEND")):
            module = ast.parse((root / "tools" / filename).read_text(encoding="utf-8"))
            files = next(ast.literal_eval(node.value) for node in module.body
                         if isinstance(node, ast.Assign)
                         and any(isinstance(target, ast.Name) and target.id == key
                                 for target in node.targets))
            self.assertIn("realtime/tracking.py", files)
            self.assertIn("realtime/risk.py", files)
