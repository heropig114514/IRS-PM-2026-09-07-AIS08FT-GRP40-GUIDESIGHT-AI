"""Responsibilities: Estimate TTC and deterministic risk after existing tracked motion fusion.
Implementation: Stateless scalar scoring and vehicle alert gates; validated immutable profile.
Related Modules: realtime.detector/tracking; model_tests.run_depth/test_risk; zerogpu.live_worker.
Declaration Index: RiskConfig; RiskConfig.__post_init__; _number; _bands; _weights;
read_risk_config; estimate_ttc; estimate_direction; RiskAssessment; RiskAssessment.fields;
_band_score; _vehicle_alert_suppression; assess_object_risk; enrich_object_risk.
Variable Index: DIRECTIONS/MOTION_STATES/RISK_LEVELS — contract vocabularies;
VEHICLE_CLASSES/SIDE_DIRECTIONS — classes and camera regions subject to vehicle alert gates;
DEFAULT_RISK_CONFIG — immutable default thresholds/weights, never per-object state;
RISK_MODEL — rule version; logger — aggregate unavailable-input diagnostics without media.
"""

import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path

DIRECTIONS = ("front_left", "front", "front_right", "left", "right", "behind", "unknown")
MOTION_STATES = ("approaching", "moving_away", "stationary", "unknown")
RISK_LEVELS = ("low", "medium", "high", "critical", "unknown")
RISK_MODEL = "rules-v2"
VEHICLE_CLASSES = frozenset(("car", "truck", "bus", "motorcycle", "bicycle", "scooter"))
SIDE_DIRECTIONS = frozenset(("front_left", "front_right", "left", "right"))
logger = logging.getLogger(__name__)


def _number(value, *, positive=False):
    """Functionality: Accept only finite scalar numbers, optionally strictly positive.
    Inputs: arbitrary value, positivity flag. Outputs: numeric value or None.
    Logic: reject bool/strings/nonfinite values before arithmetic.
    Constraints: no coercion, tensor access, or CPU/GPU transfer.
    """
    if type(value) not in (int, float):
        return None
    try:
        if not math.isfinite(value):
            return None
    except OverflowError:
        return None
    return value if not positive or value > 0 else None


def _bands(values, name):
    """Functionality: Validate immutable ascending (threshold, weight) tables.
    Inputs: tuple table, diagnostic name. Outputs: None or ValueError.
    Logic: require positive finite ascending thresholds and nonnegative bounded weights.
    Constraints: configuration errors fail at startup; no repaired or silently reordered profile.
    """
    if not isinstance(values, tuple) or not values:
        raise ValueError(f"{name} must be a nonempty tuple of bands")
    previous = 0
    for item in values:
        if not isinstance(item, tuple) or len(item) != 2:
            raise ValueError(f"Invalid {name} band")
        threshold, weight = item
        if (_number(threshold, positive=True) is None or threshold <= previous
                or _number(weight) is None or not 0 <= weight <= 100):
            raise ValueError(f"Invalid {name} threshold/weight")
        previous = threshold


def _weights(values, name, *, keys=None, signed=False):
    """Functionality: Validate immutable named weight tables and fixed contract vocabularies.
    Inputs: tuple table, name, optional required keys and signed-weight flag.
    Outputs: None or ValueError. Logic: unique bounded string keys and finite bounded numbers.
    Constraints: signed weights are reserved for motion; no dependency or mutable shared map.
    """
    if not isinstance(values, tuple) or not 0 < len(values) <= 64:
        raise ValueError(f"Invalid {name} table")
    seen = set()
    for item in values:
        if not isinstance(item, tuple) or len(item) != 2:
            raise ValueError(f"Invalid {name} entry")
        key, weight = item
        if (not isinstance(key, str) or not 0 < len(key) <= 48 or key in seen
                or _number(weight) is None or not (-100 if signed else 0) <= weight <= 100):
            raise ValueError(f"Invalid {name} key/weight")
        seen.add(key)
    if keys is not None and seen != set(keys):
        raise ValueError(f"{name} must define all supported keys")


@dataclass(frozen=True)
class RiskConfig:
    """Functionality: Centralize every TTC/risk threshold and score weight.
    Logic: tuple tables are immutable; validate at construction and JSON loading.
    Constraints: these initial heuristic weights are tunable rules, not calibrated probabilities.
    """

    min_closing_speed_mps: float = 0.20
    max_ttc_sec: float = 10.0
    distance_bands: tuple = ((1.5, 40), (3.0, 25), (5.0, 15), (10.0, 5))
    ttc_bands: tuple = ((1.5, 40), (3.0, 30), (5.0, 15), (8.0, 5))
    closing_speed_bands: tuple = ((1.0, 5), (2.0, 10))
    motion_weights: tuple = (("approaching", 15), ("moving_away", -10),
                             ("stationary", 0), ("unknown", 0))
    direction_weights: tuple = (("front", 10), ("front_left", 7), ("front_right", 7),
                                ("left", 3), ("right", 3), ("behind", 0), ("unknown", 0))
    class_weights: tuple = (("car", 15), ("truck", 18), ("bus", 18), ("motorcycle", 15),
                            ("bicycle", 12), ("scooter", 12), ("person", 5), ("animal", 5),
                            ("bird", 5), ("cat", 5), ("dog", 5), ("horse", 5), ("sheep", 5),
                            ("cow", 5), ("elephant", 5), ("bear", 5), ("zebra", 5),
                            ("giraffe", 5), ("bench", 1), ("chair", 1), ("stop sign", 1),
                            ("fire hydrant", 1), ("pole", 1), ("sign", 1),
                            # Walk-model obstacles (core.detection_model WALK): static, low-weight
                            # like pole; stairs slightly higher as a fall hazard.
                            ("bollard", 1), ("traffic_cone", 1), ("stairs", 3))
    default_class_weight: float = 5
    risk_level_thresholds: tuple = (25, 50, 75)
    alert_priorities: tuple = (("low", 0), ("medium", 2), ("high", 3), ("critical", 4),
                               ("unknown", 0))
    medium_alert_enabled: bool = True
    medium_alert_max_distance_m: float = 3.0
    medium_alert_directions: tuple = ("front", "front_left", "front_right")
    front_left_fraction: float = 1 / 3
    front_right_fraction: float = 2 / 3
    vehicle_alert_filter_enabled: bool = True
    vehicle_front_distance_m: float = 10.0
    vehicle_side_distance_m: float = 3.0
    vehicle_front_ttc_sec: float = 3.0
    vehicle_side_ttc_sec: float = 2.0

    def __post_init__(self):
        """Functionality: Reject malformed rule profiles before any model work.
        Inputs: dataclass fields. Outputs: validated instance or ValueError.
        Logic: check scalar ranges, ascending bands, vocabularies, score and priority boundaries.
        Constraints: no modification of supplied thresholds or experiment settings.
        """
        for name in ("min_closing_speed_mps", "max_ttc_sec", "medium_alert_max_distance_m",
                     "vehicle_front_distance_m", "vehicle_side_distance_m",
                     "vehicle_front_ttc_sec", "vehicle_side_ttc_sec"):
            if _number(getattr(self, name), positive=True) is None:
                raise ValueError(f"{name} must be finite and positive")
        for name in ("distance_bands", "ttc_bands", "closing_speed_bands"):
            _bands(getattr(self, name), name)
        if self.ttc_bands[-1][0] > self.max_ttc_sec:
            raise ValueError("TTC scoring bands must fit within max_ttc_sec")
        _weights(self.motion_weights, "motion_weights", keys=MOTION_STATES, signed=True)
        _weights(self.direction_weights, "direction_weights", keys=DIRECTIONS)
        _weights(self.class_weights, "class_weights")
        _weights(self.alert_priorities, "alert_priorities", keys=RISK_LEVELS)
        priorities = dict(self.alert_priorities)
        if (any(type(v) is not int or not 0 <= v <= 4 for v in priorities.values())
                or any(priorities[k] != 0 for k in ("low", "unknown"))
                or any(priorities[k] == 0 for k in ("medium", "high", "critical"))):
            raise ValueError("Alert priorities must match alert/no-alert semantics")
        if _number(self.default_class_weight) is None or not 0 <= self.default_class_weight <= 100:
            raise ValueError("Invalid default_class_weight")
        levels = self.risk_level_thresholds
        if (not isinstance(levels, tuple) or len(levels) != 3
                or any(_number(x) is None for x in levels)
                or not 0 < levels[0] < levels[1] < levels[2] <= 100):
            raise ValueError("Risk level thresholds must be ascending within (0, 100]")
        if (type(self.medium_alert_enabled) is not bool
                or not isinstance(self.medium_alert_directions, tuple)
                or not self.medium_alert_directions
                or any(x not in DIRECTIONS for x in self.medium_alert_directions)):
            raise ValueError("Invalid medium alert policy")
        if (_number(self.front_left_fraction) is None
                or _number(self.front_right_fraction) is None
                or not 0 < self.front_left_fraction < self.front_right_fraction < 1):
            raise ValueError("Direction fractions must satisfy 0 < left < right < 1")
        if (type(self.vehicle_alert_filter_enabled) is not bool
                or self.vehicle_side_distance_m > self.vehicle_front_distance_m
                or not self.vehicle_side_ttc_sec <= self.vehicle_front_ttc_sec <= self.max_ttc_sec):
            raise ValueError("Invalid vehicle alert policy")


DEFAULT_RISK_CONFIG = RiskConfig()


def read_risk_config(path=None):
    """Functionality: Load an explicit JSON rule profile once, retaining documented defaults.
    Inputs: optional local path. Outputs: immutable RiskConfig or ValueError/IO error.
    Logic: scalar fields override defaults; JSON weight objects patch default named tables;
    band lists and threshold/direction lists replace the entire corresponding tuple.
    Constraints: unknown fields, unknown fixed-vocabulary keys and invalid values fail startup.
    No downloading, retries, or silent substitution on an invalid file.
    """
    if path is None:
        return DEFAULT_RISK_CONFIG
    values = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(values, dict) or set(values) - RiskConfig.__dataclass_fields__.keys():
        raise ValueError("Risk profile must contain only supported RiskConfig fields")
    for name in ("motion_weights", "direction_weights", "class_weights", "alert_priorities"):
        if name in values:
            if not isinstance(values[name], dict):
                raise ValueError(f"{name} must be a JSON object")
            values[name] = tuple({**dict(getattr(DEFAULT_RISK_CONFIG, name)),
                                  **values[name]}.items())
    for name in ("distance_bands", "ttc_bands", "closing_speed_bands"):
        if name in values:
            if (not isinstance(values[name], list)
                    or any(not isinstance(x, list) for x in values[name])):
                raise ValueError(f"{name} must be a JSON list of pairs")
            values[name] = tuple(tuple(x) for x in values[name])
    for name in ("risk_level_thresholds", "medium_alert_directions"):
        if name in values:
            if not isinstance(values[name], list):
                raise ValueError(f"{name} must be a JSON list")
            values[name] = tuple(values[name])
    profile = RiskConfig(**values)
    logger.info("Risk profile loaded model=%s min_closing_speed_mps=%s max_ttc_sec=%s "
                "vehicle_filter=%s vehicle_distances_m=%s/%s vehicle_ttc_sec=%s/%s",
                RISK_MODEL, profile.min_closing_speed_mps, profile.max_ttc_sec,
                profile.vehicle_alert_filter_enabled, profile.vehicle_front_distance_m,
                profile.vehicle_side_distance_m, profile.vehicle_front_ttc_sec,
                profile.vehicle_side_ttc_sec)
    return profile


def estimate_ttc(distance_m, relative_speed_mps, motion_state, *, config=DEFAULT_RISK_CONFIG):
    """Functionality: Extrapolate camera-relative time to zero depth for approaching objects.
    Inputs: positive smoothed meters, signed m/s, motion state, immutable rule config.
    Outputs: positive finite seconds or None. Logic: distance / abs(negative speed), with
    inclusive minimum closing speed; retain values beyond the horizon numerically.
    Constraints: constant speed assumption, no trajectory/intersection or collision guarantee.
    Caller must reject cached depth when this frame has no valid measurement.
    """
    distance, speed = _number(distance_m, positive=True), _number(relative_speed_mps)
    if (motion_state != "approaching" or distance is None or speed is None
            or speed >= 0 or -speed < config.min_closing_speed_mps):
        return None
    result = distance / -speed
    return result if math.isfinite(result) and result > 0 else None


def estimate_direction(bbox, frame_size, *, config=DEFAULT_RISK_CONFIG):
    """Functionality: Categorize a visible bbox relative to the camera's horizontal view.
    Inputs: original xyxy and (width, height), immutable split fractions.
    Outputs: front_left/front/front_right or unknown for invalid geometry.
    Logic: validate and clamp ROI, split its center into three configurable horizontal regions.
    Constraints: camera heading approximates walking direction; bbox never estimates depth/speed.
    """
    if (not isinstance(bbox, (list, tuple)) or len(bbox) != 4
            or not isinstance(frame_size, (list, tuple)) or len(frame_size) != 2
            or any(_number(v, positive=True) is None for v in frame_size)
            or any(_number(v) is None for v in bbox)):
        return "unknown"
    width, height = frame_size
    x1, y1, x2, y2 = bbox
    x1, x2 = max(0, min(x1, width)), max(0, min(x2, width))
    y1, y2 = max(0, min(y1, height)), max(0, min(y2, height))
    if x2 <= x1 or y2 <= y1:
        return "unknown"
    center = (x1 / width + x2 / width) / 2
    return ("front_left" if center < config.front_left_fraction else
            "front_right" if center > config.front_right_fraction else "front")


@dataclass(frozen=True)
class RiskAssessment:
    """Functionality: Return explainable risk without retaining mutable track state.
    Logic: published score/level/alerts/reasons plus internal named score components.
    Constraints: unknown score is None, not a zero interpreted as safety.
    """

    risk_score: float | None
    risk_level: str
    alert_priority: int
    alert_required: bool
    risk_reasons: tuple
    components: tuple = ()

    def fields(self):
        """Functionality: Export the backward-compatible per-object protocol fields.
        Inputs: immutable estimate. Outputs: fresh JSON-safe dictionary/list.
        Logic: omit internal components from wire to bound 300-box packets; reasons explain them.
        Constraints: no rounding of score or mutation of the original detection.
        """
        return {"risk_score": self.risk_score, "risk_level": self.risk_level,
                "alert_priority": self.alert_priority, "alert_required": self.alert_required,
                "risk_reasons": list(self.risk_reasons)}


def _band_score(value, bands, unit, *, lower=False):
    """Functionality: Choose one inclusive, non-cumulative configured score band.
    Inputs: positive number, threshold/weight pairs, diagnostic unit, lower-bound mode.
    Outputs: weight and optional reason. Logic: first containing upper band, or highest reached
    lower band for closing speed. Constraints: thresholds never occur outside configuration.
    """
    if lower:
        choices = [(t, w) for t, w in bands if value >= t]
        item = choices[-1] if choices else None
    else:
        item = next(((t, w) for t, w in bands if value <= t), None)
    if item is None:
        return 0, None
    threshold, weight = item
    return weight, f"{unit}_{'ge' if lower else 'le'}_{threshold:g}"


def _vehicle_alert_suppression(class_name, distance, direction, ttc, config):
    """Functionality: Explain suppression of an otherwise eligible distant vehicle alert.
    Inputs: normalized class/direction, valid smoothed meters, validated TTC or None, profile.
    Outputs: suppression reason or None. Logic: keep proximity alerts and urgent approach;
    use shorter distance/TTC windows only for known lateral regions in the camera view.
    Constraints: does not create alerts or change scores; unknown/behind use frontal limits.
    This is a camera-relative heuristic, not a lane or collision-path classifier.
    """
    if not config.vehicle_alert_filter_enabled or class_name not in VEHICLE_CLASSES:
        return None
    side = direction in SIDE_DIRECTIONS
    distance_limit = config.vehicle_side_distance_m if side else config.vehicle_front_distance_m
    ttc_limit = config.vehicle_side_ttc_sec if side else config.vehicle_front_ttc_sec
    if distance <= distance_limit or (ttc is not None and ttc <= ttc_limit):
        return None
    return "vehicle_outside_side_alert_range" if side else "vehicle_outside_front_alert_range"


def assess_object_risk(class_name, distance_m, direction, motion_state, relative_speed_mps,
                       ttc_sec, *, config=DEFAULT_RISK_CONFIG):
    """Functionality: Combine independent, inspectable distance/motion/TTC/direction/class scores.
    Inputs: current valid smoothed distance, direction, motion/speed and optional supplied TTC.
    Outputs: RiskAssessment. Logic: gate motion by speed sign; recompute TTC eligibility and
    use supplied TTC only if it agrees with the formula; suppress TTC contribution past horizon;
    clamp sum to [0,100], map levels, then apply medium and vehicle alert eligibility.
    Constraints: invalid depth is unknown; no class-only alert rule, learned model, or history.
    """
    distance = _number(distance_m, positive=True)
    if distance is None:
        return RiskAssessment(None, "unknown", 0, False, ("depth_unavailable",))
    direction = direction if direction in DIRECTIONS else "unknown"
    motion = motion_state if motion_state in MOTION_STATES else "unknown"
    speed = _number(relative_speed_mps)
    closing = (motion == "approaching" and speed is not None
               and speed <= -config.min_closing_speed_mps)
    receding = motion == "moving_away" and speed is not None and speed > 0
    effective_motion = motion if closing or receding or motion == "stationary" else "unknown"
    distance_score, distance_reason = _band_score(distance, config.distance_bands, "distance_m")
    motion_score = dict(config.motion_weights)[effective_motion]
    reasons = [distance_reason] if distance_reason else []
    reasons.append(effective_motion)
    if closing:
        bonus, reason = _band_score(-speed, config.closing_speed_bands, "closing_mps", lower=True)
        motion_score += bonus
        if reason:
            reasons.append(reason)
    expected_ttc = estimate_ttc(distance, speed, motion, config=config)
    ttc = _number(ttc_sec, positive=True)
    ttc_score = 0
    validated_ttc = None
    if (ttc is not None and expected_ttc is not None
            and math.isclose(ttc, expected_ttc, rel_tol=1e-6, abs_tol=1e-9)):
        if ttc <= config.max_ttc_sec:
            validated_ttc = expected_ttc
            ttc_score, reason = _band_score(ttc, config.ttc_bands, "ttc_s")
            if reason:
                reasons.append(reason)
        else:
            reasons.append("ttc_beyond_horizon")
    elif ttc_sec is not None:
        reasons.append("ttc_inconsistent")
    direction_score = dict(config.direction_weights)[direction]
    reasons.append(f"direction_{direction}")
    class_key = class_name if isinstance(class_name, str) else ""
    class_map = dict(config.class_weights)
    class_score = class_map.get(class_key, config.default_class_weight)
    reasons.append(f"class_{class_key}" if class_key in class_map else "class_default")
    components = (("distance", distance_score), ("motion", motion_score), ("ttc", ttc_score),
                  ("direction", direction_score), ("class", class_score))
    score = max(0, min(100, sum(weight for _, weight in components)))
    medium, high, critical = config.risk_level_thresholds
    level = ("critical" if score >= critical else "high" if score >= high else
             "medium" if score >= medium else "low")
    alert = level in ("high", "critical") or (
        level == "medium" and config.medium_alert_enabled and closing
        and distance <= config.medium_alert_max_distance_m
        and direction in config.medium_alert_directions)
    if alert:
        suppression = _vehicle_alert_suppression(class_key, distance, direction,
                                                 validated_ttc, config)
        if suppression is not None:
            alert = False
            reasons.append(suppression)
    return RiskAssessment(score, level, dict(config.alert_priorities)[level] if alert else 0,
                          alert, tuple(reasons), components)


def enrich_object_risk(boxes, frame_size, *, config=DEFAULT_RISK_CONFIG):
    """Functionality: Add stateless TTC/risk fields after persistent motion estimation.
    Inputs: tracked/depth-fused boxes, original frame geometry, immutable rule profile.
    Outputs: copied boxes preserving every existing field. Logic: reuse a valid direction or
    derive camera-relative region; require current raw depth, smoothed depth and track ID;
    compute TTC from smoothed meters and attach deterministic risk/alert fields.
    Constraints: missing current depth never reuses cached motion distance for finite TTC/risk;
    no reset of tracker/history, tensor transfer, model inference, or new per-object state.
    """
    results, unavailable = [], 0
    for box in boxes:
        direction = box.get("direction")
        if direction not in DIRECTIONS:
            direction = estimate_direction(box.get("bbox", box.get("xyxy")), frame_size,
                                           config=config)
        tracked = type(box.get("track_id")) is int and box["track_id"] > 0
        raw, smooth = box.get("distance_m"), box.get("smoothed_distance_m")
        current = tracked and _number(raw, positive=True) is not None
        distance = smooth if current else None
        ttc = estimate_ttc(distance, box.get("relative_speed_mps"), box.get("motion_state"),
                           config=config)
        risk = assess_object_risk(box.get("class_name", box.get("label")), distance, direction,
                                  box.get("motion_state"), box.get("relative_speed_mps"), ttc,
                                  config=config)
        if not tracked:
            risk = RiskAssessment(None, "unknown", 0, False, ("track_unavailable",))
        if risk.risk_level == "unknown":
            unavailable += 1
        results.append({**box, "direction": direction, "ttc_sec": ttc, **risk.fields()})
    if unavailable:
        logger.debug("Risk inputs unavailable objects=%d total=%d", unavailable, len(boxes))
    return results
