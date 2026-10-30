"""Responsibilities: Smooth export risk without treating unavailable measurements as safe.
Implementation: Per-ID/class asymmetric EMA, score-band hysteresis, valid-time confirmation and
bounded marked display holds; urgent evidence bypasses delay and current alert gates always apply.
Related Modules: video_export.refinement/overlay; realtime.risk; VIDEO-EXPORT.md.
Declaration Index: RiskState; RiskSmoother; RiskSmoother.__init__; RiskSmoother.target_level;
RiskSmoother.confirm_motion_release; RiskSmoother.update_valid; RiskSmoother.apply.
Variable Index: LEVELS - ordered known levels; RISK_FIELDS - raw rule-output audit fields.
"""

import math
from dataclasses import dataclass

from realtime.risk import DEFAULT_RISK_CONFIG

LEVELS = ("low", "medium", "high", "critical")
RISK_FIELDS = ("risk_level", "risk_score", "alert_required", "alert_priority", "risk_reasons")


@dataclass
class RiskState:
    """Functionality: Keep one identity's score and display-level evidence.
    Logic: Store last valid measurement and accumulated valid confirmation time.
    Constraints: Missing frames never refresh validity or contribute score/confirmation evidence.
    """

    label: str
    score: float
    level: str
    valid_at: float
    pending: str | None = None
    pending_seconds: float = 0.
    approach_active: bool = False
    release_seconds: float = 0.
    release_at: float | None = None


class RiskSmoother:
    """Functionality: Publish stable, explicitly qualified export risk for a single stream.
    Logic: Freeze briefly on unavailable input; smooth valid scores with fast-rise/slow-fall EMA;
    cross bands with margins and confirmation. Constraints: No inference or cross-identity reuse.
    """

    def __init__(self, config, risk_config=DEFAULT_RISK_CONFIG):
        """Functionality: Own independent per-video state. Inputs: Refinement and rule profiles.
        Outputs: Empty smoother. Logic: Use actual scoring boundaries/priorities.
        Constraints: No shared state or reinterpretation of unknown as score zero.
        """
        self.config, self.risk_config = config, risk_config
        self.states, self.previous_time = {}, None

    def target_level(self, score, previous):
        """Functionality: Add a dead band around each existing risk boundary.
        Inputs: Filtered score and previous known level. Outputs: Candidate level.
        Logic: Rise at threshold+margin, fall below threshold-margin; permit multi-level changes.
        Constraints: These are display hysteresis boundaries, not changes to raw rules-v2 scores.
        """
        index = LEVELS.index(previous)
        thresholds, margin = self.risk_config.risk_level_thresholds, self.config.risk_margin
        while index < 3 and score >= thresholds[index] + margin:
            index += 1
        while index > 0 and score < thresholds[index - 1] - margin:
            index -= 1
        return LEVELS[index]

    def confirm_motion_release(self, state, box, timestamp, sample_seconds):
        """Functionality: Confirm weaker motion and briefly tolerate uncertain approach evidence.
        Inputs: Identity state, current motion, source time and frame interval. Outputs: Guard flag.
        Logic: Reliable weaker motion confirms after 0.3s; uncertain motion shares that bounded
        timeout before distance-only rules resume. Renewed approach clears the timer.
        Constraints: Missing/absent intervals never count; noisy motion never becomes stationary.
        """
        motion, quality = box.get("motion_state"), box.get("motion_quality")
        if quality == "measured" and motion == "approaching":
            state.approach_active = True
            state.release_seconds, state.release_at = 0., None
        elif state.approach_active and ((quality == "measured" and motion in (
                "stationary", "moving_away")) or quality in ("uncertain", "unavailable")):
            state.release_seconds = (state.release_seconds + sample_seconds
                                     if state.release_at == self.previous_time else 0.)
            state.release_at = timestamp
            if state.release_seconds >= self.config.motion_release_s:
                state.approach_active = False
        else:
            state.release_seconds, state.release_at = 0., None
        return state.approach_active and state.release_at is not None

    def update_valid(self, state, box, timestamp, sample_seconds):
        """Functionality: Advance only valid risk evidence, with an immediate urgent rise path.
        Inputs: State, valid raw box, source time and duration since the previous input frame.
        Outputs: Whether urgency bypassed smoothing. Logic: Missing intervals pause the EMA and
        confirmation; reliable close/short-TTC high risk rises immediately.
        Constraints: Near-distance bypass requires raw high/critical risk, not any fluctuation.
        """
        cfg = self.config
        release_pending = self.confirm_motion_release(state, box, timestamp, sample_seconds)
        raw, level = box["risk_score"], box["risk_level"]
        distance, ttc = box.get("distance_m"), box.get("ttc_sec")
        near = type(distance) in (int, float) and 0 < distance <= cfg.near_distance_m
        short_ttc = (type(ttc) in (int, float) and 0 < ttc <= cfg.urgent_ttc_s
                     and box.get("distance_quality") == "measured")
        urgent = level in ("high", "critical") and (near or short_ttc)
        bypass = urgent and LEVELS.index(level) > LEVELS.index(state.level)
        dt = sample_seconds
        if urgent:
            state.score = max(state.score, raw)
        if bypass:
            state.level, state.pending, state.pending_seconds = level, None, 0.
        else:
            tau = cfg.risk_rise_tau_s if raw > state.score else cfg.risk_fall_tau_s
            alpha = 1 - math.exp(-dt / tau)
            if not release_pending or raw >= state.score:
                state.score += alpha * (raw - state.score)
            candidate = self.target_level(state.score, state.level)
            if release_pending and LEVELS.index(candidate) < LEVELS.index(state.level):
                candidate = state.level
            if urgent and LEVELS.index(candidate) < LEVELS.index(level):
                candidate = level
            if candidate == state.level:
                state.pending, state.pending_seconds = None, 0.
            else:
                rising = LEVELS.index(candidate) > LEVELS.index(state.level)
                same_direction = state.pending is not None and (
                    (LEVELS.index(state.pending) > LEVELS.index(state.level)) == rising)
                state.pending_seconds = state.pending_seconds + dt if same_direction else 0.
                state.pending = candidate
                delay = cfg.risk_rise_s if rising else cfg.risk_fall_s
                if state.pending_seconds >= delay:
                    state.level, state.pending, state.pending_seconds = candidate, None, 0.
        state.valid_at = timestamp
        return bypass

    def apply(self, boxes, timestamp):
        """Functionality: Smooth scores/levels and qualify stale display values explicitly.
        Inputs: Current boxes with raw rules-v2 fields and monotonically increasing source time.
        Outputs: New boxes with raw audit, filtered score, status/age and stable level.
        Logic: Same-ID/class validity grace <=0.3s; unknown beyond grace, no alerts on holds;
        current raw eligibility and stable level jointly bound alerts on valid frames.
        Constraints: No ghost boxes, alert renewal from held values, history transfer on class
        changes or grace extension from repeated bad frames. Missing scores never become zero.
        """
        cfg, result = self.config, []
        if self.previous_time is not None and timestamp <= self.previous_time:
            raise ValueError("Risk timestamps must increase strictly")
        self.states = {key: state for key, state in self.states.items()
                       if timestamp - state.valid_at <= cfg.risk_missing_grace_s}
        for box in boxes:
            raw = {key: box[key] for key in RISK_FIELDS}
            identity, label, score = box.get("track_id"), box["label"], raw["risk_score"]
            tracked = type(identity) is int and identity > 0
            state = self.states.get(identity) if tracked else None
            if state is not None and state.label != label:
                del self.states[identity]
                state = None
            valid = (tracked and raw["risk_level"] in LEVELS and type(score) in (int, float)
                     and math.isfinite(score) and 0 <= score <= 100)
            invalid_reasons = [*raw["risk_reasons"]]
            if box.get("motion_quality") == "uncertain":
                invalid_reasons.append("motion_uncertain")
            if not valid:
                if state is not None:
                    state.release_seconds, state.release_at = 0., None
                    age = timestamp - state.valid_at
                    result.append({**box, "risk_level": state.level, "risk_score": state.score,
                                   "alert_required": False, "alert_priority": 0,
                                   "risk_reasons": [*invalid_reasons, "display_hold_pending"],
                                   "instant_risk": raw, "smoothed_risk_score": state.score,
                                   "risk_data_status": "held", "risk_age_s": age,
                                   "risk_urgent_bypass": False})
                else:
                    result.append({**box, "risk_level": "unknown", "risk_score": None,
                                   "alert_required": False, "alert_priority": 0,
                                   "risk_reasons": invalid_reasons,
                                   "instant_risk": raw, "smoothed_risk_score": None,
                                   "risk_data_status": "unknown", "risk_age_s": None,
                                   "risk_urgent_bypass": False})
                continue
            if state is None:
                state = RiskState(label, float(score), raw["risk_level"], timestamp)
                state.approach_active = (box.get("motion_quality") == "measured"
                                         and box.get("motion_state") == "approaching")
                self.states[identity] = state
                bypass = False
            else:
                sample_seconds = (timestamp - self.previous_time
                                  if self.previous_time is not None else 0.)
                bypass = self.update_valid(state, box, timestamp, sample_seconds)
            level = state.level
            alert = raw["alert_required"] and (level in ("high", "critical") or (
                level == "medium" and raw["risk_level"] == "medium"))
            priority = min(dict(self.risk_config.alert_priorities)[level], raw["alert_priority"])
            reasons = [*raw["risk_reasons"], "risk_score_smoothed"]
            if box.get("motion_quality") == "uncertain":
                reasons.append("motion_uncertain")
            if level != raw["risk_level"]:
                reasons.append("risk_level_hysteresis")
            if state.pending is not None:
                reasons.append("risk_transition_pending")
            if state.approach_active and state.release_at is not None:
                reasons.append("motion_release_pending")
            result.append({**box, "risk_score": state.score, "risk_level": level,
                           "alert_required": alert, "alert_priority": priority if alert else 0,
                           "risk_reasons": reasons, "instant_risk": raw,
                           "smoothed_risk_score": state.score, "risk_data_status": "valid",
                           "risk_age_s": 0., "risk_urgent_bypass": bypass})
        self.previous_time = timestamp
        return result
