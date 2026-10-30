"""Responsibilities: Opt-in, auditable temporal refinement for complete video exports.
Implementation: Conservative same-class deduplication, confidence persistence, robust depth/speed
and source-time risk hysteresis; original network predictions remain available in frame audits.
Related Modules: realtime.detector; video_export.services; VIDEO-EXPORT.md.
Declaration Index: RefinementConfig; finite_distance; Candidate; DistanceState;
VideoRefinement; VideoRefinement.__init__; VideoRefinement.reset;
VideoRefinement.filter_detections; VideoRefinement.update_distance;
VideoRefinement.refine_motion; VideoRefinement.stabilize_risk.
Variable Index: PROFILE - explicit export policy version. Histories are owned per export.
"""

import math
from collections import deque
from dataclasses import asdict, dataclass, field
from statistics import median

from core.detector import box_iou

from .risk_smoothing import RiskSmoother

PROFILE = "video-stable-v6"


@dataclass(frozen=True)
class RefinementConfig:
    """Functionality: Record the fixed, reviewable export policy in each run manifest.
    Logic: Source-time windows and absolute/relative measurement tolerances.
    Constraints: Heuristic defaults for this demo; not calibrated safety or accuracy guarantees.
    """

    duplicate_iou: float = 0.85
    candidate_iou: float = 0.4
    candidate_gap_s: float = 0.2
    confirmation_s: float = 0.1
    weak_confirmation_s: float = 0.4
    confidence_hold_s: float = 0.3
    immediate_confidence: float = 0.9
    priority_confidence: float = 0.5
    display_classes: tuple = ("person", "car", "bus", "truck", "motorcycle", "bicycle", "scooter")
    display_confidence: float = 0.35
    display_confirmation_s: float = 0.03
    threshold_confirmed_classes: tuple = ("traffic light", "pedestrian light")
    priority_classes: tuple = ("person", "car", "bus", "truck", "motorcycle", "bicycle",
                               "scooter", "stairs", "bollard", "traffic_cone")
    confidence_floor: float = 0.5
    weak_confidence_floor: float = 0.35
    near_distance_m: float = 3.0
    depth_window_s: float = 1.0
    depth_gap_s: float = 0.6
    depth_jump_m: float = 1.0
    depth_jump_fraction: float = 0.15
    jump_speed_allowance_mps: float = 10.0
    jump_confirmation_s: float = 0.07
    smooth_tau_s: float = 0.2
    speed_min_span_s: float = 0.5
    speed_pair_span_s: float = 0.2
    motion_threshold_mps: float = 0.5
    motion_release_s: float = 0.3
    risk_missing_grace_s: float = 0.3
    risk_rise_tau_s: float = 0.12
    risk_fall_tau_s: float = 0.35
    risk_margin: float = 5.0
    risk_rise_s: float = 0.2
    risk_fall_s: float = 0.45
    urgent_ttc_s: float = 1.5
    expiry_s: float = 2.0


def finite_distance(value):
    """Functionality: Reject unavailable metric inputs. Inputs: Any scalar. Outputs: bool.
    Logic: Require a finite positive number. Constraints: No coercion or invented distance.
    """
    return type(value) in (float, int) and math.isfinite(value) and value > 0


@dataclass
class Candidate:
    """Functionality: Keep one provisional visual candidate's confidence evidence.
    Logic: Greedy one-to-one same-class IoU links across adjacent frames.
    Constraints: Does not replace BoT-SORT identity or introduce a semantic class blacklist.
    """

    box: dict
    first: float
    last: float
    scores: deque = field(default_factory=lambda: deque(maxlen=16))
    confirmed_at: float | None = None
    interrupted: bool = False


@dataclass
class DistanceState:
    """Functionality: Store bounded accepted measurements for a confirmed track/class.
    Logic: Raw accepted samples drive robust slope; a separate EMA drives displayed distance.
    Constraints: A gap or confirmed discontinuity resets speed rather than inventing a trajectory.
    """

    label: str
    seen: float
    samples: deque = field(default_factory=lambda: deque(maxlen=64))
    smooth: float | None = None
    pending: tuple | None = None
    motion_quality: str = "unavailable"


class VideoRefinement:
    """Functionality: Own optional refinement state for one export stream and tracker epoch.
    Logic: Three explicit hooks around association, risk scoring and publication.
    Constraints: No model changes, downloads, offline future-frame lookahead or global defaults.
    """

    def __init__(self):
        """Functionality: Create a fixed policy and independent state. Inputs: None.
        Outputs: Refiner. Logic: Initialize versioned configuration and reset.
        Constraints: Each detector/export must own its own instance.
        """
        self.config = RefinementConfig()
        self.profile = {"name": PROFILE, **asdict(self.config)}
        self.reset()

    def reset(self):
        """Functionality: Clear state when a tracker epoch changes. Inputs: None. Outputs: None.
        Logic: Discard candidate, distance and risk histories. Constraints: No cross-session reuse.
        """
        self.candidates, self.distances = [], {}
        self.risk_smoother = RiskSmoother(self.config)
        self.audit = {}

    def filter_detections(self, boxes, timestamp, thresholds):
        """Functionality: Remove near-identical same-class boxes before BoT-SORT association.
        Inputs: Decoded boxes, source seconds and existing class confidence thresholds.
        Outputs: Deduplicated candidates with explicit confirmation status.
        Logic: Highest score wins at IoU >= .85; one-to-one temporal confidence evidence;
        admit lights at their original class threshold without extra temporal confirmation;
        admit moderate-confidence people/vehicles for display while retaining original alert gates.
        Constraints: Every raw candidate and suppression reason is retained in this frame's audit.
        Unconfirmed near objects can bypass confirmation after actual depth is available.
        """
        cfg = self.config
        self.audit = {"raw_detections": boxes, "duplicate_detections": [],
                      "unconfirmed_detections": []}
        kept = []
        for box in sorted(boxes, key=lambda item: item["score"], reverse=True):
            if any(box["label"] == other["label"] and box_iou(box["xyxy"], other["xyxy"])
                   >= cfg.duplicate_iou for other in kept):
                self.audit["duplicate_detections"].append(box)
            else:
                kept.append(box)
        previous = [c for c in self.candidates if timestamp - c.last <= cfg.candidate_gap_s]
        used, current, result = set(), [], []
        for box in kept:
            threshold = thresholds.get(box["label"], 0.25)
            if box["label"] in cfg.threshold_confirmed_classes:
                result.append({**box, "detection_confirmed": box["score"] >= threshold})
                continue
            choices = [(box_iou(c.box["xyxy"], box["xyxy"]), i, c)
                       for i, c in enumerate(previous) if i not in used
                       and c.box["label"] == box["label"]]
            match = max(choices, key=lambda item: item[0], default=None)
            if match is not None and match[0] >= cfg.candidate_iou:
                _, index, candidate = match
                used.add(index)
                candidate.box, candidate.last = box, timestamp
            else:
                candidate = Candidate(box, timestamp, timestamp)
            display_count = len(candidate.scores) + 1
            display_duration = timestamp - candidate.first
            if candidate.interrupted:
                # A missed frame resets the original alarm evidence, but not display matching.
                candidate.first = timestamp
                candidate.confirmed_at, candidate.interrupted = None, False
                candidate.scores.clear()
            candidate.scores.append(box["score"])
            strong = min(cfg.confidence_floor, threshold * 2)
            weak = min(cfg.weak_confidence_floor, threshold * 1.4)
            duration, count = timestamp - candidate.first, len(candidate.scores)
            confidence = median(candidate.scores)
            confirmed = (box["score"] >= cfg.immediate_confidence or
                         (box["label"] in cfg.priority_classes
                          and box["score"] >= cfg.priority_confidence) or
                         (duration >= cfg.confirmation_s and count >= 3 and confidence >= strong
                          and box["score"] >= weak) or
                         (duration >= cfg.weak_confirmation_s and count >= 8
                          and confidence >= weak and box["score"] >= weak))
            if confirmed:
                candidate.confirmed_at = timestamp
            confirmed = candidate.confirmed_at is not None and (
                timestamp - candidate.confirmed_at <= cfg.confidence_hold_s)
            display_confirmed = (box["label"] in cfg.display_classes
                                 and box["score"] >= threshold and (
                                     box["score"] >= cfg.display_confidence or
                                     (display_count >= 2
                                      and display_duration >= cfg.display_confirmation_s)))
            current.append(candidate)
            result.append({**box, "detection_confirmed": confirmed or display_confirmed,
                           "alert_detection_confirmed": confirmed})
        # Retain association evidence through short misses, never emit an absent object's box.
        for i, candidate in enumerate(previous):
            if i not in used and candidate.box["label"] in cfg.display_classes:
                candidate.interrupted = True
                current.append(candidate)
        self.candidates = current
        return result

    def update_distance(self, state, raw, timestamp):
        """Functionality: Reject isolated jumps, estimate robust speed and smooth display meters.
        Inputs: Track state, current metric measurement and source seconds.
        Outputs: Smoothed meters, speed, motion label and measurement quality.
        Logic: Relative jump gate; three consistent jump samples rebase history; near observations
        bypass the gate. Check a one-second Theil-Sen slope against median residual noise;
        unsupported noisy motion is unknown, while low-noise flat depth is stationary.
        Constraints: Missing/rejected samples never add slope evidence; urgent proximity is not
        hidden by a historical long distance. Slope is camera-relative, not world velocity.
        """
        cfg = self.config
        state.motion_quality = "unavailable"
        if state.samples and timestamp - state.samples[-1][0] > cfg.depth_gap_s:
            state.samples.clear()
            state.smooth, state.pending = None, None
        if not finite_distance(raw):
            return None, None, "unknown", "missing"
        quality = "measured"
        if state.samples:
            recent = median([value for _, value in list(state.samples)[-5:]])
            dt = timestamp - state.samples[-1][0]
            limit = max(cfg.depth_jump_m, recent * cfg.depth_jump_fraction)
            limit += cfg.jump_speed_allowance_mps * dt
            if abs(raw - recent) > limit:
                if raw <= cfg.near_distance_m:
                    state.samples.clear()
                    state.smooth, state.pending = None, None
                    quality = "near_reacquired"
                else:
                    pending = state.pending
                    if pending and abs(raw - pending[0]) <= max(cfg.depth_jump_m, raw * .1):
                        state.pending = (raw, pending[1], pending[2] + 1)
                    else:
                        state.pending = (raw, timestamp, 1)
                    if state.pending[2] >= 3 and timestamp - state.pending[1] >= (
                            cfg.jump_confirmation_s):
                        state.samples.clear()
                        state.smooth, state.pending = None, None
                        quality = "reacquired"
                    else:
                        return state.smooth, None, "unknown", "outlier"
            else:
                state.pending = None
        dt = timestamp - state.samples[-1][0] if state.samples else 0
        alpha = 1 - math.exp(-dt / cfg.smooth_tau_s)
        state.smooth = raw if state.smooth is None else alpha * raw + (1 - alpha) * state.smooth
        if raw <= cfg.near_distance_m:
            state.smooth = min(raw, state.smooth)
        state.samples.append((timestamp, raw))
        while state.samples and timestamp - state.samples[0][0] > cfg.depth_window_s:
            state.samples.popleft()
        if len(state.samples) < 6 or timestamp - state.samples[0][0] < cfg.speed_min_span_s:
            return state.smooth, None, "unknown", quality
        samples = list(state.samples)
        slopes = [(b[1] - a[1]) / (b[0] - a[0]) for i, a in enumerate(samples)
                  for b in samples[i + 1:] if b[0] - a[0] >= cfg.speed_pair_span_s]
        speed = median(slopes)
        intercept = median(value - speed * (time - timestamp) for time, value in samples)
        residual = median(abs(value - (intercept + speed * (time - timestamp)))
                          for time, value in samples)
        signal = abs(speed) * (timestamp - samples[0][0])
        if signal < max(.25, residual * 3):
            if residual * 3 > .25:
                state.motion_quality = "uncertain"
                return state.smooth, None, "unknown", quality
            speed = 0.0
        state.motion_quality = "measured"
        motion = ("approaching" if speed <= -cfg.motion_threshold_mps else
                  "moving_away" if speed >= cfg.motion_threshold_mps else "stationary")
        return state.smooth, speed, motion, quality

    def refine_motion(self, boxes, timestamp):
        """Functionality: Publish robust motion before the existing rules-v2 risk calculation.
        Inputs: Depth-fused/tracked boxes and source seconds. Outputs: Refined current boxes.
        Logic: Confirm uncertain detections, bypass confirmation for close obstacles; retain raw
        depth and original estimates in audit fields; invalidate missing/outlier risk inputs.
        Constraints: No guessed ID continuity, fabricated distances or reuse after class changes.
        """
        cfg = self.config
        self.distances = {key: state for key, state in self.distances.items()
                          if timestamp - state.seen <= cfg.expiry_s}
        result = []
        for box in boxes:
            raw, identity = box.get("distance_m"), box.get("track_id")
            near = finite_distance(raw) and raw <= cfg.near_distance_m
            if not box.get("detection_confirmed", True) and not near:
                self.audit["unconfirmed_detections"].append(box)
                continue
            enriched = {**box, "confirmation_bypass": "near" if near and not box.get(
                        "detection_confirmed", True) else None,
                        "display_only": not box.get("alert_detection_confirmed", True) and not near,
                        "unfiltered_motion": {key: box.get(key) for key in (
                            "smoothed_distance_m", "relative_speed_mps", "motion_state")}}
            if type(identity) is not int or identity <= 0:
                enriched.update(display_distance_m=raw, distance_quality="untracked")
                result.append(enriched)
                continue
            state = self.distances.get(identity)
            if state is None or state.label != box["label"]:
                state = self.distances[identity] = DistanceState(box["label"], timestamp)
            state.seen = timestamp
            measured = None if box.get("distance_source") == "cached" else raw
            smooth, speed, motion, quality = self.update_distance(state, measured, timestamp)
            enriched.update(smoothed_distance_m=smooth if quality != "outlier" else None,
                            display_distance_m=smooth, relative_speed_mps=speed,
                            motion_state=motion, distance_quality=quality,
                            motion_quality=state.motion_quality)
            result.append(enriched)
        return result

    def stabilize_risk(self, boxes, timestamp):
        """Functionality: Publish smooth risk with marked short holds and current alert gates.
        Inputs: Instant rules-v2 boxes and source seconds. Outputs: Smoothed, audited risk fields.
        Logic: Delegate to the per-export score/state smoother; reset with the tracker epoch.
        Constraints: Display-only weak detections cannot alert; actual near bypass is preserved.
        Raw risk remains in instant_risk; held values cannot create or renew alerts.
        """
        gated = [{**box, "alert_required": False, "alert_priority": 0,
                  "risk_reasons": [*box["risk_reasons"], "display_only_detection"]}
                 if box.get("display_only") else box for box in boxes]
        return self.risk_smoother.apply(gated, timestamp)
