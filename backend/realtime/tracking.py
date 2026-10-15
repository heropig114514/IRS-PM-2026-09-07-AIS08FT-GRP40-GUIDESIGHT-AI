"""Responsibilities: Persistent Ultralytics BoT-SORT association and relative depth motion.
Implementation: Official BoT-SORT/CMC; bounded EMA histories and timestamped least-squares motion.
Related Modules: realtime.detector/depth; zerogpu.live_worker/app; model_tests.test_tracking.
Declaration Index: TrackingConfig; TrackingConfig.__post_init__; read_tracking_config;
motion_candidate; _validate_timestamp; _regression_speed; MotionEstimate;
TrackState; TrackState.__post_init__; TrackState.reset_motion; TrackState.estimate;
TrackState.add_distance; TrackHistory.update_motion_state;
TrackHistory; TrackHistory.__init__; TrackHistory.reset; TrackHistory.cleanup; TrackHistory.update;
build_tracker; build_tracker.CheckedBOTSORT; build_tracker.CheckedBOTSORT._pre_first_associate;
ObjectTracker; ObjectTracker.__init__; ObjectTracker.reset; ObjectTracker.associate;
ObjectTracker.enrich; ObjectTracker.metadata.
Variable Index: logger — safe lifecycle/error diagnostics;
DEFAULT_TRACKING_CONFIG — immutable default profile (no tracker/history singleton);
TrackState — bounded raw/time/EMA samples and hysteresis state; ObjectTracker — one session's
tracker/history/epoch, failure latch and frame geometry. No models, downloads, TTC or risk scores.
distance_source=cached — historical C output, never a new motion sample.
"""

import json
import logging
import math
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from core.detector import DetectionError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TrackingConfig:
    """Functionality: Centralize configurable tracking/history and motion thresholds.
    Logic: immutable defaults, optional explicit JSON overrides; reuse official association YAML.
    Constraints: BoT-SORT with active CMC only, no ReID, bounded histories and positive timing.
    """

    tracker: str = "botsort.yaml"
    history_size: int = 8
    min_valid_samples: int = 3
    ema_alpha: float = 0.5
    stale_timeout_s: float = 2.0
    motion_history_timeout_s: float = 2.0
    min_time_span_s: float = 0.1
    approaching_threshold_mps: float = 0.2
    moving_away_threshold_mps: float = 0.2
    confirmation_updates: int = 2

    def __post_init__(self):
        """Functionality: Reject unsupported tracker choices and unsafe history settings.
        Inputs: dataclass fields. Outputs: validated instance or ValueError.
        Logic: exact integer bounds and finite numerical checks. Constraints: no coercion/fallback.
        """
        if self.tracker != "botsort.yaml":
            raise ValueError("Only the pinned official botsort.yaml is supported")
        for value in (self.history_size, self.min_valid_samples, self.confirmation_updates):
            if type(value) is not int:
                raise ValueError("History/sample/confirmation counts must be integers")
        if not 3 <= self.min_valid_samples <= self.history_size <= 64:
            raise ValueError("Require 3 <= min_valid_samples <= history_size <= 64")
        if not 2 <= self.confirmation_updates < self.history_size:
            raise ValueError("Require 2 <= confirmation_updates < history_size")
        for value in (self.ema_alpha, self.stale_timeout_s, self.motion_history_timeout_s,
                      self.min_time_span_s,
                      self.approaching_threshold_mps, self.moving_away_threshold_mps):
            if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(
                    value) or value <= 0:
                raise ValueError("Motion configuration must contain positive finite numbers")
        if self.ema_alpha > 1 or self.min_time_span_s >= self.motion_history_timeout_s:
            raise ValueError("Invalid EMA alpha or timing window")


DEFAULT_TRACKING_CONFIG = TrackingConfig()


def read_tracking_config(path=None):
    """Functionality: Load an explicitly selected JSON profile or immutable defaults once.
    Inputs: optional local path. Outputs: TrackingConfig. Logic: reject unknown keys via dataclass.
    Constraints: no implicit downloads, environment reads, parameter changes or retry.
    """
    if path is None:
        return DEFAULT_TRACKING_CONFIG
    values = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(values, dict):
        raise ValueError("Tracking profile must be a JSON object")
    return TrackingConfig(**values)


def motion_candidate(speed, config):
    """Functionality: Classify a finite relative depth slope with centralized thresholds.
    Inputs: meters/second and TrackingConfig. Outputs: motion label.
    Logic: inclusive negative/positive thresholds, stationary strictly inside the dead band.
    Constraints: caller requires enough valid samples/time; this is not world velocity.
    """
    if speed <= -config.approaching_threshold_mps:
        return "approaching"
    if speed >= config.moving_away_threshold_mps:
        return "moving_away"
    return "stationary"


def _validate_timestamp(timestamp, previous=None, *, allow_equal=False):
    """Functionality: Validate one monotonic stream clock before touching history.
    Inputs: finite nonnegative seconds, optional prior seconds and same-frame allowance.
    Outputs: None or ValueError. Logic: reject invalid/backward/duplicate observations.
    Constraints: equal frame time is allowed only across different tracks, not for one track.
    """
    if isinstance(timestamp, bool) or not isinstance(timestamp, int | float) or not (
            math.isfinite(timestamp)) or timestamp < 0 or (previous is not None and (
                timestamp < previous or (timestamp == previous and not allow_equal))):
        raise ValueError("Tracking timestamps must be finite nonnegative increasing seconds")


def _regression_speed(timestamps, distances):
    """Functionality: Fit distance = speed*time + intercept over every smoothed sample.
    Inputs: aligned valid seconds/meters. Outputs: finite m/s or None for degenerate arithmetic.
    Logic: center time relative to the first observation and use least-squares covariance/variance.
    Constraints: caller enforces minimum samples/span; no endpoint-only slope or frame-count clock.
    """
    times = [timestamp - timestamps[0] for timestamp in timestamps]
    count = len(times)
    try:
        mean_time = math.fsum(value / count for value in times)
        mean_distance = math.fsum(value / count for value in distances)
        variance = math.fsum((value - mean_time) ** 2 for value in times)
        if variance <= 0 or not math.isfinite(variance):
            return None
        covariance = math.fsum((timestamp - mean_time) * (distance - mean_distance)
                               for timestamp, distance in zip(times, distances, strict=True))
        speed = covariance / variance
        return speed if math.isfinite(speed) else None
    except OverflowError:
        return None


@dataclass(frozen=True)
class MotionEstimate:
    """Functionality: Return an immutable snapshot without exposing mutable per-track queues.
    Logic: smoothed meters, regression m/s and stabilized label; empty history defaults to unknown.
    Constraints: retained estimates during missing depth are historical, not new measurements.
    """

    smoothed_distance_m: float | None = None
    relative_speed_mps: float | None = None
    motion_state: str = "unknown"


@dataclass
class TrackState:
    """Functionality: Store one identity's bounded distance/time/EMA history and motion state.
    Logic: scalar measurements only; valid samples advance smoothing, slope and hysteresis.
    Constraints: no history transfer across IDs/classes/resets; EMA introduces temporal lag.
    """

    track_id: int
    class_name: str | None
    last_seen_timestamp: float
    config: TrackingConfig
    distance_history: deque = field(init=False)
    timestamp_history: deque = field(init=False)
    smoothed_history: deque = field(init=False)
    smoothed_distance_m: float | None = None
    relative_speed_mps: float | None = None
    motion_state: str = "unknown"
    pending_state: str | None = None
    pending_updates: int = 0

    def __post_init__(self):
        """Functionality: Allocate bounded independent queues for one new identity.
        Inputs: validated config. Outputs: aligned empty histories.
        Logic: identical maxlen. Constraints: never shares mutable state with another track.
        """
        self.distance_history = deque(maxlen=self.config.history_size)
        self.timestamp_history = deque(maxlen=self.config.history_size)
        self.smoothed_history = deque(maxlen=self.config.history_size)

    def reset_motion(self):
        """Functionality: Clear measurements and confirmation after a stale valid-depth gap.
        Inputs: None. Outputs: empty queues/unknown estimate. Logic: clear aligned state together.
        Constraints: retains visual track ID/class/last-seen time; never resets BoT-SORT.
        """
        self.distance_history.clear()
        self.timestamp_history.clear()
        self.smoothed_history.clear()
        self.smoothed_distance_m, self.relative_speed_mps = None, None
        self.motion_state, self.pending_state, self.pending_updates = "unknown", None, 0

    def estimate(self):
        """Functionality: Snapshot the last published estimate for pipeline or reusable callers.
        Inputs: current state. Outputs: MotionEstimate. Logic: copy scalar fields only.
        Constraints: missing samples never manufacture a new distance or speed.
        """
        return MotionEstimate(self.smoothed_distance_m, self.relative_speed_mps, self.motion_state)

    def add_distance(self, distance, timestamp):
        """Functionality: Accept finite positive depth and update robust relative motion.
        Inputs: nullable metric distance, increasing seconds. Outputs: valid-update bool.
        Logic: reset after a gap since the last valid depth, EMA, full-window linear regression;
        require minimum samples/span and consecutive eligible candidates for every state change.
        Constraints: temporary invalid depth preserves the last estimate without counting toward
        confirmation; no speed from <=2 measurements, duplicate times, stale data or class changes.
        """
        if self.timestamp_history and timestamp - self.timestamp_history[-1] > (
                self.config.motion_history_timeout_s):
            self.reset_motion()
            logger.info("Motion history expired id=%d", self.track_id)
        try:
            value = float(distance) if not isinstance(distance, bool) else float("nan")
        except (TypeError, ValueError, OverflowError):
            value = float("nan")
        if not math.isfinite(value) or value <= 0:
            return False
        if self.timestamp_history and timestamp <= self.timestamp_history[-1]:
            return False
        previous = self.smoothed_distance_m
        self.smoothed_distance_m = (value if previous is None else
                                    self.config.ema_alpha * value +
                                    (1 - self.config.ema_alpha) * previous)
        self.distance_history.append(value)
        self.timestamp_history.append(timestamp)
        self.smoothed_history.append(self.smoothed_distance_m)
        span = timestamp - self.timestamp_history[0]
        if len(self.distance_history) < self.config.min_valid_samples or span < (
                self.config.min_time_span_s):
            self.relative_speed_mps, self.motion_state = None, "unknown"
            self.pending_state, self.pending_updates = None, 0
            return True
        self.relative_speed_mps = _regression_speed(self.timestamp_history, self.smoothed_history)
        if self.relative_speed_mps is None:
            self.relative_speed_mps, self.motion_state = None, "unknown"
            self.pending_state, self.pending_updates = None, 0
            return True
        candidate = motion_candidate(self.relative_speed_mps, self.config)
        if candidate == self.motion_state:
            self.pending_state, self.pending_updates = None, 0
            return True
        self.pending_updates = self.pending_updates + 1 if candidate == self.pending_state else 1
        self.pending_state = candidate
        if self.pending_updates >= self.config.confirmation_updates:
            logger.info("Motion state changed id=%d state=%s", self.track_id, candidate)
            self.motion_state = candidate
            self.pending_state, self.pending_updates = None, 0
        return True


class TrackHistory:
    """Functionality: Own distance histories for one ordered stream of track IDs.
    Logic: clean disappeared IDs by monotonic time; class changes start independent histories.
    Constraints: no visual reassociation or guessed identity continuity; missing IDs stay unknown.
    """

    def __init__(self, config):
        """Functionality: Initialize empty per-stream history once.
        Inputs: TrackingConfig. Outputs: tracks dict and clock state.
        Logic: instance ownership. Constraints: do not share across cameras/allocations.
        """
        self.config, self.tracks, self.last_timestamp = config, {}, None

    def reset(self):
        """Functionality: Clear history at an explicit sequence/tracker reset or latched failure.
        Inputs: None. Outputs: empty histories. Logic: discard all identities and clock state.
        Constraints: never reuses distances after an ID counter is restarted.
        """
        self.tracks.clear()
        self.last_timestamp = None
        logger.info("Track distance histories cleared")

    def cleanup(self, timestamp):
        """Functionality: Remove disappeared tracks after the configured timeout.
        Inputs: same-clock seconds. Outputs: expired count. Logic: compare last visual observation.
        Constraints: invalid depth alone does not expire a visually present track.
        """
        expired = [key for key, state in self.tracks.items()
                   if timestamp - state.last_seen_timestamp > self.config.stale_timeout_s]
        for key in expired:
            del self.tracks[key]
        if expired:
            logger.info("Stale track histories removed count=%d", len(expired))
        return len(expired)

    def update(self, boxes, timestamp):
        """Functionality: Add smoothed depth/speed/motion fields to current detections.
        Inputs: CPU box dictionaries, finite nonnegative increasing timestamp in seconds.
        Outputs: new dictionaries retaining every existing field. Logic: clean stale histories,
        validate IDs, restart mismatched classes and update valid depth only.
        Constraints: invalid/nonincreasing frame clocks reject updates; temporary invalid depth
        retains the last estimate until timeout. Raw distances are unchanged, including None.
        C's distance_source=cached passes a missing measurement to motion without changing output.
        """
        _validate_timestamp(timestamp, self.last_timestamp)
        self.cleanup(timestamp)
        self.last_timestamp = timestamp
        enriched = []
        for box in boxes:
            track_id = box.get("track_id")
            estimate = self.update_motion_state(
                track_id, None if box.get("distance_source") == "cached" else box.get("distance_m"),
                timestamp,
                class_name=box.get("class_name") or box.get("label"))
            enriched.append({**box, "track_id": track_id if type(track_id) is int and (
                                track_id > 0) else None,
                             "smoothed_distance_m": estimate.smoothed_distance_m,
                             "relative_speed_mps": estimate.relative_speed_mps,
                             "motion_state": estimate.motion_state})
        return enriched

    def update_motion_state(self, track_id, distance_m, timestamp, *, class_name=None):
        """Functionality: Reuse this stream's TrackState to update one object's motion estimate.
        Inputs: positive integer ID, nullable raw meters, monotonic seconds, optional class name.
        Outputs: MotionEstimate. Logic: clean stale tracks once per timestamp; isolate IDs/classes,
        accept valid depth, retain short missing-depth estimates and reset after timeout.
        Constraints: distinct IDs may share a frame time; duplicate times for one ID reject;
        missing IDs return unknown. No duplicate store, ML model, GPU transfer or raw mutation.
        """
        _validate_timestamp(timestamp, self.last_timestamp, allow_equal=True)
        if self.last_timestamp is None or timestamp > self.last_timestamp:
            self.cleanup(timestamp)
            self.last_timestamp = timestamp
        if type(track_id) is not int or track_id <= 0:
            return MotionEstimate()
        state = self.tracks.get(track_id)
        if state is not None:
            _validate_timestamp(timestamp, state.last_seen_timestamp)
        if state is None or (class_name is not None and state.class_name != class_name):
            if state is not None:
                logger.warning("Track class changed id=%d; distance history restarted", track_id)
            state = TrackState(track_id, class_name, timestamp, self.config)
            self.tracks[track_id] = state
        state.last_seen_timestamp = timestamp
        state.add_distance(distance_m, timestamp)
        return state.estimate()


def build_tracker(config, expected_version):
    """Functionality: Build actual Ultralytics BoT-SORT once from the pinned official YAML.
    Inputs: TrackingConfig and verified detector package version. Outputs: BOTSORT instance.
    Logic: preflight lap before SDK imports to prevent its automatic installer; require active
    CMC and disabled ReID. Override only the pinned GMC hook to propagate unexpected failures.
    Constraints: no model.track(), new NMS/model/precision, alternate tracker, downloads or retry.
    """
    import lap
    import ultralytics

    if not getattr(lap, "__version__", None) or ultralytics.__version__ != expected_version:
        raise ValueError("Tracking requires lap and the verified Ultralytics build")
    from ultralytics.trackers.bot_sort import BOTSORT
    from ultralytics.trackers.utils.stracks import multi_gmc
    from ultralytics.utils import YAML, IterableSimpleNamespace

    path = Path(ultralytics.__file__).resolve().parent / "cfg/trackers" / config.tracker
    values = YAML.load(path)
    if values.get("tracker_type") != "botsort" or values.get("with_reid") is not False or (
            values.get("gmc_method") not in {"sparseOptFlow", "orb", "sift", "ecc"}):
        raise ValueError("BoT-SORT requires enabled camera motion compensation and no ReID")

    class CheckedBOTSORT(BOTSORT):
        """Functionality: Preserve BoT-SORT association while surfacing unexpected GMC failures.
        Logic: same official GMC/warp hook without the SDK's exception-to-identity substitution.
        Constraints: pinned private hook; low-feature identity estimates remain SDK behavior.
        """

        def _pre_first_associate(self, strack_pool, unconfirmed, img, results_high):
            """Functionality: Apply official camera warp before BoT-SORT association.
            Inputs: tracker pools/current BGR/high detections. Outputs: warped Kalman states.
            Logic: propagate GMC exceptions to session wrapper. Constraints: no silent disabled CMC.
            """
            warp = self.gmc.apply(img, results_high.xyxy)
            multi_gmc(strack_pool, warp)
            multi_gmc(unconfirmed, warp)

    return CheckedBOTSORT(IterableSimpleNamespace(**values))


class ObjectTracker:
    """Functionality: Own one BoT-SORT instance and depth history for a video session.
    Logic: associate original CPU detections, then enrich after GPU depth fusion.
    Constraints: serial ordered frames; failure is explicit and latched until caller reset;
    tracker construction/reset never occurs during per-frame inference; no dense-depth transfer.
    """

    def __init__(self, config, expected_version):
        """Functionality: Construct association/history state once before a frame loop.
        Inputs: validated config/build. Outputs: persistent tracker/history/epoch or load error.
        Logic: local official YAML and dependency preflight. Constraints: no automatic installation.
        """
        self.config, self.history = config, TrackHistory(config)
        self.epoch, self.frame_size, self.available = 0, None, True
        try:
            self.tracker = build_tracker(config, expected_version)
        except Exception as error:
            logger.error("BoT-SORT initialization failed type=%s", type(error).__name__)
            raise DetectionError("tracking_unavailable", "Install pinned tracking dependencies.",
                                 503) from None
        logger.info("BoT-SORT initialized CMC=%s ReID=%s history=%d",
                    self.tracker.args.gmc_method, self.tracker.args.with_reid, config.history_size)

    def reset(self):
        """Functionality: Explicitly begin a new stream with the same models.
        Inputs: None. Outputs: cleared tracker/GMC/history and incremented epoch.
        Logic: caller invokes between sequences only. Constraints: never called by predict/update.
        """
        self.tracker.reset()
        self.history.reset()
        self.epoch += 1
        self.frame_size, self.available = None, True
        logger.info("BoT-SORT sequence reset epoch=%d", self.epoch)

    def associate(self, image, boxes):
        """Functionality: Attach track IDs to original post-decoding detections.
        Inputs: current PIL RGB/original-frame boxes. Outputs: copies with nullable ID.
        Logic: CPU BGR for CMC, official NumPy Boxes and detection-index association; preserve all
        detections including unconfirmed ones and original coordinates/scores/classes.
        Constraints: no GPU transfers/NMS; unexpected errors latch unavailability with prior labels,
        not a retry/alternative tracker. Changed geometry needs an explicit caller stream reset.
        """
        tracked = [{**box, "track_id": None, "class_name": box["label"],
                    "confidence": box["score"], "bbox": list(box["xyxy"])} for box in boxes]
        if not self.available:
            return tracked
        try:
            import numpy as np
            from ultralytics.engine.results import Boxes

            if self.frame_size is not None and self.frame_size != image.size:
                raise ValueError("Tracking frame geometry changed")
            self.frame_size = image.size
            data = np.asarray([[*box["xyxy"], box["score"], box["class_id"]] for box in boxes],
                              dtype=np.float32).reshape(-1, 6)
            frame = np.ascontiguousarray(np.asarray(image)[:, :, ::-1])
            matches = self.tracker.update(Boxes(data, (image.height, image.width)), frame)
            ids, indices = set(), set()
            for row in matches:
                if len(row) != 8 or not np.isfinite(row).all():
                    raise ValueError("Invalid BoT-SORT result")
                index, identity = int(row[7]), int(row[4])
                if row[7] != index or not 0 <= index < len(boxes) or row[4] != identity or (
                        identity <= 0 or identity > 2**53 - 1 or index in indices or
                        identity in ids):
                    raise ValueError("Invalid BoT-SORT detection/identity mapping")
                indices.add(index)
                ids.add(identity)
                if int(row[6]) == boxes[index]["class_id"]:
                    tracked[index]["track_id"] = identity
            return tracked
        except Exception as error:
            self.available = False
            self.history.reset()
            logger.error("BoT-SORT unavailable until explicit reset type=%s", type(error).__name__)
            return [{**box, "track_id": None} for box in tracked]

    def enrich(self, boxes, timestamp):
        """Functionality: Update scalar distance motion after depth fusion.
        Inputs: fused boxes and same-worker frame-start seconds. Outputs: extended object fields.
        Logic: missing IDs/latched failures emit unknown. Constraints: no stale speed or map copies.
        """
        return self.history.update(boxes, timestamp)

    def metadata(self):
        """Functionality: Expose tracker mode/lifecycle without mutable history or camera data.
        Inputs: current instance. Outputs: additive protocol metadata.
        Logic: explicit CMC/ReID/status/epoch. Constraints: epoch IDs are scoped to this session.
        """
        return {"tracking_model": "botsort", "tracking_status": (
                    "active" if self.available else "unavailable"),
                "tracking_epoch": self.epoch, "gmc_method": self.tracker.args.gmc_method,
                "with_reid": self.tracker.args.with_reid}
