"""Responsibilities: Centralize explicit A/B/C distance experiment parameters.
Implementation: Immutable validated profiles; JSON plus optional process/CLI overrides.
Related Modules: realtime.distance; integration.inference.services; zerogpu.app;
model_tests.run_depth.
Declaration Index: DistanceConfig; DistanceConfig.__post_init__; read_distance_config.
Variable Index: DISTANCE_MODES — supported strategies; DEFAULT_OBJECT_HEIGHTS_M — approximate
category heights in meters, editable experiment assumptions; DEFAULT_DISTANCE_CONFIG — A defaults.
"""

import json
import math
import os
from dataclasses import dataclass, replace
from pathlib import Path

DISTANCE_MODES = ("depth", "geometry", "temporal_depth")
DEFAULT_OBJECT_HEIGHTS_M = (("person", 1.7), ("bicycle", 1.1), ("motorcycle", 1.2),
                            ("car", 1.5), ("bus", 3.2), ("truck", 3.0))


@dataclass(frozen=True)
class DistanceConfig:
    """Functionality: Describe a distance strategy without changing detection/depth algorithms.
    Logic: focal length is vertical fy at a reference image width; resize it with frame width.
    Constraints: heights/focal length are baseline assumptions, not a calibration claim.
    """

    distance_mode: str = "depth"
    depth_interval: int = 3
    focal_length_px: float = 700.0
    reference_width_px: int = 640
    object_heights_m: tuple = DEFAULT_OBJECT_HEIGHTS_M

    def __post_init__(self):
        """Functionality: Reject invalid experiment settings before model initialization.
        Inputs: profile fields. Outputs: validated frozen profile or ValueError.
        Logic: exact counts and positive finite calibration/heights; normalize JSON height maps.
        Constraints: no silent coercion, unsupported keys, fallback or inferred calibration.
        """
        if self.distance_mode not in DISTANCE_MODES:
            raise ValueError(f"distance_mode must be one of {DISTANCE_MODES}")
        for name in ("depth_interval", "reference_width_px"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        heights = self.object_heights_m
        if isinstance(heights, dict):
            heights = tuple(heights.items())
        try:
            heights = tuple((name, height) for name, height in heights)
            if any(not isinstance(name, str) or not name for name, _ in heights) or (
                    len(dict(heights)) != len(heights)):
                raise ValueError("Invalid class names")
            values = [self.focal_length_px, *(height for _, height in heights)]
            if any(isinstance(value, bool) or not isinstance(value, int | float) or
                   not math.isfinite(value) or value <= 0 for value in values):
                raise ValueError("Focal length/heights must be positive finite numbers")
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("Invalid geometry calibration or object_heights_m") from error
        object.__setattr__(self, "object_heights_m", heights)


DEFAULT_DISTANCE_CONFIG = DistanceConfig()


def read_distance_config(path=None, *, distance_mode=None, depth_interval=None, environ=None):
    """Functionality: Resolve one explicit distance profile at startup, without model I/O.
    Inputs: optional JSON path, CLI overrides, optional environment mapping (default os.environ).
    Outputs: DistanceConfig. Logic: defaults < JSON < PRS_DISTANCE_MODE/PRS_DEPTH_INTERVAL < CLI.
    PRS_DISTANCE_CONFIG selects JSON relative to backend root; explicit paths use caller cwd.
    Constraints: unknown/invalid settings fail; never reread configuration during frame inference.
    """
    environment = os.environ if environ is None else environ
    if path is None and environment.get("PRS_DISTANCE_CONFIG", "").strip():
        path = Path(__file__).resolve().parents[1] / environment["PRS_DISTANCE_CONFIG"].strip()
    config = (DEFAULT_DISTANCE_CONFIG if path is None else
              DistanceConfig(**json.loads(Path(path).read_text(encoding="utf-8"))))
    overrides = {}
    mode = distance_mode if distance_mode is not None else environment.get("PRS_DISTANCE_MODE")
    interval = (depth_interval if depth_interval is not None else
                environment.get("PRS_DEPTH_INTERVAL"))
    if mode is not None:
        overrides["distance_mode"] = mode
    if interval is not None:
        overrides["depth_interval"] = int(interval) if isinstance(interval, str) else interval
    return replace(config, **overrides)
