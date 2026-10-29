"""Responsibilities: Name the admitted detection models and select one explicitly at startup.
Implementation: Fixed profile table (weights, manifest, class count); PRS_DETECTION_MODEL selects a
profile, default the fine-tuned yolo26n-walk (COCO yolo26n stays selectable); strict manifest
identity check without Torch.
Related Modules: zerogpu.runtime.load_model; integration.protocol/inference.services;
model_tests.run_depth; realtime.detector; core.detector.pedestrian_lights; WALK-MODEL.md.
Declaration Index: selected_model; model_files; validate_manifest; light_rule.
Variable Index: ORIGINAL — COCO model name; WALK — default fine-tuned walking-aid model name;
PROFILES — model name -> (weights path, manifest path, class count) relative to backend root;
ROOT — backend source root.
"""

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = "yolo26n"
WALK = "yolo26n-walk"
PROFILES = {
    ORIGINAL: ("models/yolo26n.pt", "models/yolo26n.json", 80),
    WALK: ("models/yolo26n-walk.pt", "models/yolo26n-walk.json", 87),
}


def selected_model(environ=None):
    """Functionality: Resolve the detection model chosen for this process.
    Inputs: optional environment mapping (default os.environ). Outputs: profile name.
    Logic: PRS_DETECTION_MODEL, blank or absent means the walk model; `yolo26n` selects COCO.
    Constraints: unknown names fail; read once at startup, never per frame; no fallback model.
    """
    environment = os.environ if environ is None else environ
    name = environment.get("PRS_DETECTION_MODEL", "").strip() or WALK
    if name not in PROFILES:
        raise ValueError(f"Unknown PRS_DETECTION_MODEL {name!r}; "
                         f"expected one of {sorted(PROFILES)}")
    return name


def model_files(name=None):
    """Functionality: Return the pinned files of a profile.
    Inputs: profile name (default: selected_model()). Outputs: (weights Path, manifest Path).
    Logic: paths are fixed relative to the backend root. Constraints: existence is not checked here.
    """
    weights, manifest, _ = PROFILES[selected_model() if name is None else name]
    return ROOT / weights, ROOT / manifest


def validate_manifest(manifest):
    """Functionality: Check a manifest's identity for any admitted profile.
    Inputs: decoded manifest. Outputs: the same manifest or ValueError.
    Logic: known model name; non-empty unique string names of the profile's class count; COCO ids
    0-79 keep the original names in order for the walk model; 64-hex source hash; build version;
    the walk model must declare its pedestrian-light rule.
    Constraints: identity only; COCO or colour quality is documented evidence, not checked here.
    """
    model = manifest.get("model")
    names = manifest.get("names")
    if (model not in PROFILES or not isinstance(names, list) or len(names) != PROFILES[model][2]
            or any(not isinstance(name, str) or not name for name in names)
            or len(set(names)) != len(names)
            or not re.fullmatch(r"[0-9a-f]{64}", str(manifest.get("source_sha256", "")))
            or not isinstance(manifest.get("build", {}).get("ultralytics"), str)):
        raise ValueError("Detection-model manifest identity is invalid")
    if model == WALK:
        import json

        original = json.loads((ROOT / PROFILES[ORIGINAL][1]).read_text(encoding="utf-8"))["names"]
        if names[:80] != original:
            raise ValueError("Walk model must keep the 80 COCO class ids and names")
        light_rule(manifest)
    return manifest


def light_rule(manifest):
    """Functionality: Read the pedestrian-light colour rule declared by a manifest.
    Inputs: manifest. Outputs: dict(classes=(red, green, off), green_threshold, class_confidence,
    class_id) or None; class_confidence is the colour classes' decode threshold (the calibration
    operating point) and class_id the red id, the stable id of every merged pedestrian-light box.
    Logic: only models that declare `pedestrian_lights` have one; all three class names must exist.
    Constraints: threshold must lie in (0, 1); the original COCO model has no colour rule.
    """
    rule = manifest.get("pedestrian_lights")
    if rule is None:
        return None
    classes = tuple(rule.get("classes", ()))
    threshold = rule.get("green_threshold")
    confidence = rule.get("class_confidence")
    if (len(classes) != 3 or any(name not in manifest["names"] for name in classes)
            or type(threshold) is not float or not 0 < threshold < 1
            or type(confidence) is not float or not 0 < confidence <= 0.25):
        raise ValueError("Pedestrian-light rule is invalid")
    return {"classes": classes, "green_threshold": threshold, "class_confidence": confidence,
            "class_id": manifest["names"].index(classes[0])}
