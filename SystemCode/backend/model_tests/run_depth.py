"""Responsibilities: Run explicit local A/B/C image tests through the existing detector pipeline.
Implementation: Resident FP32 models; optional sequence tracking/TTC/risk, JSONL and annotations.
Related Modules: realtime.depth/detector/tracking/risk; tools.fixtures; model_tests.test_cli.
Declaration Index: collect_inputs; file_digest; write_manifest; build_detector; run; main.
Variable Index: TEST_ROOT — self-contained test asset root; ROOT — shared backend model root;
IMAGE_SUFFIXES — accepted source extensions;
logger — safe phase/count/error diagnostics. No sampling, training or accuracy metrics.
tracking/risk — explicit immutable profiles recorded for ordered runs, no per-frame reloading.
distance_config — explicit strategy/calibration profile; performance — last cumulative summary.
"""

import argparse
import hashlib
import json
import logging
import sys
import time
from pathlib import Path

from core.detection_model import model_files

TEST_ROOT = Path(__file__).resolve().parent
ROOT = TEST_ROOT.parent
IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".bmp", ".webp"})
logger = logging.getLogger(__name__)


def collect_inputs(source):
    """Functionality: Discover one image or every supported image in a directory.
    Inputs: explicit source Path. Outputs: source root and deterministic sorted image paths.
    Logic: recursive discovery without random selection or limits; reject missing/empty sources.
    Constraints: ordering is path-based, not an official dataset split; symlink paths cannot escape.
    """
    source = source.resolve()
    if source.is_file() and source.suffix.lower() in IMAGE_SUFFIXES:
        return source.parent, [source]
    if not source.is_dir():
        raise ValueError("Input must be an existing image or image directory")
    images = sorted((path for path in source.rglob("*")
                     if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES),
                    key=lambda path: path.relative_to(source).as_posix())
    if not images:
        raise ValueError("Input directory contains no supported images")
    if any(not path.resolve().is_relative_to(source) for path in images):
        raise ValueError("Input image symlinks must remain within the input directory")
    return source, images


def file_digest(path):
    """Functionality: Fingerprint a local source/model without retaining its whole content.
    Inputs: existing Path. Outputs: lowercase SHA256. Logic: stream1MiB chunks.
    Constraints: no downloads or path substitution; any file error aborts the explicit run.
    """
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def write_manifest(output, report):
    """Functionality: Persist run identity/status independently of streamed frame results.
    Inputs: owned output Path and finite report dict. Outputs: run.json written in UTF8.
    Logic: replace only this newly-created run's manifest; retain partial JSONL on failure.
    Constraints: no existing run may be overwritten by the CLI; no sensitive media in logs.
    """
    (output / "run.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def build_detector(weights, manifest_path, depth_weights, *, tracking_config=None,
                   risk_config=None, distance_config=None, roi=False):
    """Functionality: Initialize selected existing models once before the image loop.
    Inputs: explicit weights/manifest; optional distance/tracking/risk profiles for one sequence;
    roi=True adds the centre-top traffic-light pass.
    Outputs: ResidentDetector on actual CUDA. Logic: reuse pinned loader and explicit FP32 control.
    Constraints: detector remains original YOLO26n; no GPU fallback/download/experiment changes.
    """
    from realtime.depth import DepthEstimator
    from realtime.detector import ResidentDetector
    from realtime.distance_config import DEFAULT_DISTANCE_CONFIG

    distance_config = DEFAULT_DISTANCE_CONFIG if distance_config is None else distance_config
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if distance_config.distance_mode == "temporal_depth" and tracking_config is None:
        raise ValueError("temporal_depth requires --track for one ordered sequence")
    depth = (DepthEstimator(depth_weights, manifest["build"]["ultralytics"])
             if distance_config.distance_mode != "geometry" else None)
    options = {"risk_config": risk_config} if risk_config is not None else {}
    if roi:
        options["roi"] = True
    return ResidentDetector(weights, manifest_path, depth_estimator=depth,
                            tracking_config=tracking_config, distance_config=distance_config,
                            **options)


def run(args):
    """Functionality: Produce reviewable local detection/distance outputs for all chosen images.
    Inputs: CLI namespace with paths, distance mode, repeats and explicit --track.
    Outputs: report dict; new run.json/results.jsonl and frame annotations; exceptions propagate.
    Logic: validate paths before GPU work, fingerprint assets, construct once, preprocess each image
    once using the established640/JPEG80 fixture, process every repeat and stream its result.
    Constraints: rejects existing/nested-input output directories; never skips failures or first
    frames, retries, shuffles data, computes accuracy without labels or changes production settings.
    Input predictions/bboxes use the resized camera-frame coordinate system, not the source size.
    Tracking treats all images/repeats as one stream using inference-start time, not source FPS;
    unrelated dataset stills must leave --track off. Optional tracking/risk profiles load once.
    """
    from dataclasses import asdict

    from PIL import Image

    from core.detector import CLASS_CONF
    from realtime.distance_config import DEFAULT_DISTANCE_CONFIG, read_distance_config
    from realtime.risk import read_risk_config
    from realtime.tracking import read_tracking_config
    from tools.fixtures import fixture_bytes

    distance_config = read_distance_config(getattr(args, "distance_config", None),
                                          distance_mode=getattr(args, "distance_mode", None),
                                          depth_interval=getattr(args, "depth_interval", None))
    needs_depth = distance_config.distance_mode != "geometry"
    if distance_config.distance_mode == "temporal_depth" and not getattr(args, "track", False):
        raise ValueError("temporal_depth requires --track for one ordered sequence")

    root, images = collect_inputs(args.input)
    output = args.output.resolve()
    if output.exists():
        raise ValueError("Output already exists; choose a new run directory")
    if args.repeat < 1:
        raise ValueError("repeat must be positive")
    roi, max_edge = getattr(args, "roi", False), getattr(args, "max_edge", 640)
    if not 1 <= max_edge <= 1280:
        raise ValueError("max-edge must be between 1 and 1280")
    if args.input.is_dir() and output.is_relative_to(root):
        raise ValueError("Output must be outside the input image directory")
    tracking = (read_tracking_config(getattr(args, "tracking_config", None))
                if getattr(args, "track", False) else None)
    if getattr(args, "tracking_config", None) is not None and tracking is None:
        raise ValueError("--tracking-config requires --track")
    risk_path = getattr(args, "risk_config", None)
    if risk_path is not None and tracking is None:
        raise ValueError("--risk-config requires --track")
    risk = read_risk_config(risk_path) if tracking is not None else None
    report = {"schema_version": 1, "status": "running",
              "label": args.label or (args.depth_weights.stem if needs_depth else "geometry"),
              "scope": "Native CUDA image inference; no ground-truth accuracy or video FPS claim",
              "input_root": str(root), "repeat": args.repeat, "images": [], "frames": 0,
              "settings": {"detector_config": {}, "detection_edge": 640,
                           "confidence_threshold": 0.25,
                           "class_confidence_thresholds": dict(CLASS_CONF),
                           "jpeg_quality": 80,
                           "depth_edge": 768, "depth_precision": "FP32",
                           "object_distance": "median of positive finite bbox ROI depths"},
              "models": {"detection_sha256": file_digest(args.weights),
                         "manifest_sha256": file_digest(args.manifest),
                         "depth_sha256": file_digest(args.depth_weights) if needs_depth else None,
                         "depth_weights": (str(args.depth_weights.resolve())
                                           if needs_depth else None)}}
    report["settings"].update(distance_mode=distance_config.distance_mode,
                              distance_config={**asdict(distance_config),
                                               "object_heights_m": dict(
                                                   distance_config.object_heights_m)})
    if not needs_depth:
        report["settings"].update(depth_edge=None, depth_precision=None,
                                  object_distance="fy_px * object_height_m / bbox_height_px")
    elif distance_config.distance_mode == "temporal_depth":
        report["settings"]["object_distance"] += "; last valid distance on non-depth frames"
    for path in images:
        report["images"].append({"path": path.relative_to(root).as_posix(),
                                 "source_sha256": file_digest(path)})
    if roi or max_edge != 640:
        report["settings"].update(frame_max_edge=max_edge, roi=roi)
    if tracking is not None:
        report["settings"]["tracking"] = asdict(tracking)
        report["settings"]["risk"] = asdict(risk)
        report["scope"] += "; ordered sequence tracking using local frame-start timestamps"
    output.mkdir(parents=True)
    write_manifest(output, report)
    logger.info("Local depth test starting images=%d repeats=%d", len(images), args.repeat)
    try:
        options = {"tracking_config": tracking} if tracking is not None else {}
        if risk_path is not None:
            options["risk_config"] = risk
        if distance_config != DEFAULT_DISTANCE_CONFIG:
            options["distance_config"] = distance_config
        if roi:
            options["roi"] = True
        detector = build_detector(args.weights, args.manifest, args.depth_weights, **options)
        with (output / "results.jsonl").open("w", encoding="utf-8") as records:
            for path, identity in zip(images, report["images"], strict=True):
                frame, size = (fixture_bytes(path) if max_edge == 640
                               else fixture_bytes(path, max_edge))
                with Image.open(path) as source_image:
                    source_size = list(source_image.size)
                input_hash = hashlib.sha256(frame).hexdigest()
                for repeat in range(args.repeat):
                    started = time.perf_counter()
                    metadata, rendered = detector.predict(frame)
                    wall_ms = (time.perf_counter() - started) * 1000
                    relative = Path("annotations") / path.relative_to(root) / f"{repeat:04d}.jpg"
                    target = output / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(rendered)
                    record = {"image": identity["path"], "source_sha256": identity["source_sha256"],
                              "input_jpeg_sha256": input_hash, "frame_size": list(size),
                              "source_size": source_size, "coordinate_space": "frame",
                              "repeat_index": repeat, "annotation": relative.as_posix(),
                              "predict_wall_ms": wall_ms, "metadata": metadata}
                    records.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
                    records.flush()
                    report["frames"] += 1
                    if isinstance(metadata.get("performance"), dict):
                        report["performance"] = metadata["performance"]
                    print(json.dumps({"image": identity["path"], "repeat": repeat,
                                      "predict_wall_ms": round(wall_ms, 2),
                                      "objects": [{key: box[key] for key in (
                                          "class_name", "confidence", "bbox", "distance_m",
                                          "track_id", "smoothed_distance_m", "relative_speed_mps",
                                          "motion_state", "direction", "ttc_sec", "risk_score",
                                          "risk_level", "alert_priority", "alert_required",
                                          "risk_reasons") if key in box}
                                          for box in metadata["boxes"]]},
                                     ensure_ascii=False, allow_nan=False))
        report["status"] = "complete"
        write_manifest(output, report)
        logger.info("Local depth test completed images=%d frames=%d", len(images), report["frames"])
        return report
    except Exception as error:
        report.update(status="failed", error_code=getattr(error, "code", type(error).__name__))
        write_manifest(output, report)
        logger.error("Local depth test failed frames=%d code=%s", report["frames"],
                     report["error_code"])
        raise


def main(argv=None):
    """Functionality: Expose a terminal entry point for the reproducible local image test.
    Inputs: optional explicit argv or terminal arguments. Outputs:0 success/1 failure exit status.
    Logic: default to this test folder's public sample and shared pinned backend model assets;
    require a new output directory. Relative explicit paths remain relative to the terminal cwd.
    Constraints: errors display only stable codes/types; labels never select models or parameters.
    """
    from realtime.distance_config import DISTANCE_MODES

    parser = argparse.ArgumentParser(description="Local YOLO26 detection + A/B/C distance test")
    parser.add_argument("--input", type=Path, default=TEST_ROOT / "samples/bus.jpg",
                        help="Image or directory; default: bundled official bus image")
    parser.add_argument("--output", type=Path, required=True, help="New output directory")
    # Defaults follow PRS_DETECTION_MODEL (COCO yolo26n unless the walk model is selected).
    parser.add_argument("--weights", type=Path, default=model_files()[0])
    parser.add_argument("--manifest", type=Path, default=model_files()[1])
    parser.add_argument("--depth-weights", type=Path, default=ROOT / "models/yolo26n-depth.pt")
    parser.add_argument("--distance-config", type=Path, help="Distance JSON profile")
    parser.add_argument("--distance-mode", choices=DISTANCE_MODES,
                        help="A: depth (default), B: geometry, C: temporal_depth")
    parser.add_argument("--depth-interval", type=int,
                        help="C depth cadence; positive frames, default 3; 1 means every frame")
    parser.add_argument("--repeat", type=int, default=1,
                        help="Explicit repeats per image; default1")
    parser.add_argument("--label", help="Run label only; default: depth weight filename stem")
    parser.add_argument("--track", action="store_true",
                        help="Treat inputs/repeats as one ordered sequence, not unrelated photos")
    parser.add_argument("--tracking-config", type=Path,
                        help="Explicit JSON motion profile; needs --track")
    parser.add_argument("--risk-config", type=Path,
                        help="Explicit JSON TTC/rule profile; needs --track")
    parser.add_argument("--roi", action="store_true",
                        help="Extra centre-top native-resolution traffic-light pass")
    parser.add_argument("--max-edge", type=int, default=640,
                        help="Frame long-edge cap, 1-1280; default 640 (old behaviour)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    try:
        report = run(args)
    except Exception as error:
        code = getattr(error, "code", type(error).__name__)
        logger.error("Local test aborted code=%s", code)
        detail = (str(error) if isinstance(error, ValueError | FileNotFoundError) else
                  getattr(error, "detail", "Check CUDA/dependencies and the run.json error code."))
        print(f"Test failed [{code}]: {detail}")
        return 1
    print(f"Saved {report['frames']} frames to {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    # Direct script execution from model_tests also needs the shared backend inference modules.
    # Module execution already supplies that root; neither entry point changes the user's cwd.
    if __package__ in (None, ""):
        sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
