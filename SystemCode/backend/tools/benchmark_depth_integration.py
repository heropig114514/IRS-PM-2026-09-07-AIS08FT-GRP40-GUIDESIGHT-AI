"""Responsibilities: Quantify the actual native TensorRT depth integration on complete videos.
Implementation: Original frames/timestamps, explicit 80/87-class model and GPU/host fusion;
full pipeline, complete depth comparisons and same-model controls.
Related Modules: integration.inference.services; realtime.depth_config/depth_runtime;
tools.benchmark_video; tools.benchmark_inference; DEPTH-EXPORT.md.
Declaration Index: DepthCapture; DepthCapture.__init__; DepthCapture.__call__; DepthCapture.consume;
compare_boxes; compare_depth; run; main.
Variable Index: NUMERIC_FIELDS — explicitly reported continuous distance/motion values, not ignored;
SOURCE_FILES — computation implementations bound to each comparison;
TIMING_FIELDS — unchanged whole-frame and nested phase host durations, not kernel measurements.
"""

import argparse
import hashlib
import json
import logging
import time
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import numpy as np

from realtime.depth_config import DepthBackendConfig
from realtime.depth_runtime import file_hash
from tools.benchmark_inference import distribution
from tools.benchmark_video import VideoFrames

NUMERIC_FIELDS = {"distance_m", "smoothed_distance_m", "relative_speed_mps", "ttc_sec"}
SOURCE_FILES = ("core/detector.py", "core/detection_model.py", "zerogpu/runtime.py",
                "realtime/depth.py", "realtime/depth_runtime.py", "realtime/depth_config.py",
                "realtime/depth_fast.py",
                "realtime/depth_predictor.py", "realtime/detector.py", "realtime/distance.py",
                "realtime/tracking.py", "realtime/risk.py", "integration/inference/services.py",
                "tools/benchmark_depth_integration.py")
TIMING_FIELDS = ("server_ms", "depth_predict_ms", "depth_fusion_ms", "tracking_ms", "render_ms")


class DepthCapture:
    """Functionality: Observe the actual aligned map without changing timed pipeline execution.
    Logic: retain one tensor, copy after prediction completes, then release the reference.
    Constraints: diagnostic only; host copies and disk I/O are outside prediction timing.
    """

    def __init__(self, predict):
        """Functionality: Bind the original depth call. Inputs: callable. Outputs: empty observer.
        Logic: retain original ownership. Constraints: no model initialization or warmup here.
        """
        self.predict, self.pending = predict, None

    def __call__(self, image):
        """Functionality: Record exactly the map used by object fusion.
        Inputs: unchanged decoded image. Outputs: original tensor.
        Logic: call once and retain. Constraints: unread previous output is an explicit error.
        """
        if self.pending is not None:
            raise RuntimeError("Previous depth evidence was not consumed")
        self.pending = self.predict(image)
        return self.pending

    def consume(self):
        """Functionality: Transfer every aligned pixel outside the timed region.
        Inputs: pending CUDA tensor. Outputs: independently owned FP32 NumPy array.
        Logic: copy and release. Constraints: no thresholding, spatial sampling or approximation.
        """
        if self.pending is None:
            raise RuntimeError("Depth evidence is missing")
        value = self.pending.detach().cpu().numpy().copy()
        self.pending = None
        return value


def compare_boxes(expected, actual):
    """Functionality: Keep structural/decision changes separate from continuous numerical errors.
    Inputs: original and candidate complete object lists. Outputs: changed fields and error maxima.
    Logic: compare every field; numeric distance/speed/TTC differences are explicitly quantified.
    Constraints: no tolerance or equivalence acceptance; missing values remain structural changes.
    """
    structural, numerical = [], {}
    if len(expected) != len(actual):
        structural.append("object_count")
    for index, (left, right) in enumerate(zip(expected, actual, strict=False)):
        for key in sorted(left.keys() | right.keys()):
            before, after = left.get(key), right.get(key)
            if key in left and key in right and before == after:
                continue
            if (key in NUMERIC_FIELDS and type(before) in (int, float)
                    and type(after) in (int, float)):
                previous = numerical.setdefault(key, {"different_objects": 0, "max_abs": 0.0})
                previous["different_objects"] += 1
                previous["max_abs"] = max(previous["max_abs"], abs(after - before))
            else:
                structural.append(f"boxes[{index}].{key}")
    return {"structural_or_decision_changes": structural, "numeric_differences": numerical,
            "exact": expected == actual}


def compare_depth(expected, actual):
    """Functionality: Quantify all aligned depth pixels against the original pipeline.
    Inputs: original and candidate NumPy maps. Outputs: finite counts, exactness and error measures.
    Logic: validate contracts then calculate float64 errors over mutually finite values.
    Constraints: no accuracy threshold; invalid values are counted rather than substituted.
    """
    if expected.shape != actual.shape or expected.dtype != actual.dtype:
        raise ValueError("Aligned depth shape or dtype changed")
    finite = np.isfinite(expected) & np.isfinite(actual)
    error = np.abs(expected[finite].astype(np.float64) - actual[finite].astype(np.float64))
    return {"exact": bool(np.array_equal(expected, actual)),
            "different_pixels": int(np.count_nonzero(expected != actual)),
            "reference_nonfinite": int((~np.isfinite(expected)).sum()),
            "candidate_nonfinite": int((~np.isfinite(actual)).sum()),
            "max_abs_m": float(error.max()) if error.size else None,
            "mean_abs_m": float(error.mean()) if error.size else None}


def run(args):
    """Functionality: Measure one complete video through the repository's actual inference factory.
    Inputs: CLI video/backend/manifest/output, optional reference and private depth cache.
    Outputs: complete frame records and latency/comparison summary, including failures.
    Logic: process every original frame; time predict only; compare cached baseline outside timing.
    Constraints: one process per pipeline; no pacing, dropped frames, retries or changed thresholds.
    """
    import torch

    from integration.inference.services import load_detector
    from realtime.distance_config import DEFAULT_DISTANCE_CONFIG
    from realtime.risk import DEFAULT_RISK_CONFIG
    from realtime.tracking import DEFAULT_TRACKING_CONFIG

    args.output.mkdir(parents=True, exist_ok=False)
    if not args.reference:
        if args.depth_cache is None:
            raise ValueError("The reference requires an explicit private depth cache")
        args.depth_cache.mkdir(parents=True, exist_ok=False)
    config = DepthBackendConfig(args.backend, args.manifest, args.fusion)
    report = {"status": "running", "backend": args.backend, "fusion": args.fusion,
              "completed_frames": 0,
              "scope": "Full-video native inference; no truth-based accuracy acceptance",
              "quality_accepted": False,
              "reference": str(args.reference) if args.reference else None,
              "video_sha256": file_hash(args.video),
              "source_sha256": {name: file_hash(name) for name in SOURCE_FILES},
              "settings": {"batch": 1, "precision": "FP32", "depth_edge": 768,
                           "detection_model": args.detection_model,
                           "detection_manifest_sha256": file_hash(
                               Path("models") / (args.detection_model + ".json")),
                           "fixture_max_edge": 640, "jpeg_quality": 80,
                           "distance": asdict(DEFAULT_DISTANCE_CONFIG),
                           "tracking": asdict(DEFAULT_TRACKING_CONFIG),
                           "risk": asdict(DEFAULT_RISK_CONFIG)}}
    source, reference_file, frames = None, None, []
    try:
        source = VideoFrames(args.video)
        report.update(declared_frames=source.count, source_fps=source.fps,
                      source_size=[source.width, source.height])
        if args.reference:
            original = json.loads((args.reference / "report.json").read_text(encoding="utf-8"))
            if original["status"] != "completed":
                raise ValueError("Reference is not complete")
            for field in ("video_sha256", "source_sha256", "settings", "declared_frames"):
                if json.loads(json.dumps(report[field])) != original[field]:
                    raise ValueError(f"Reference {field} changed")
            depth_cache = Path(original["depth_cache"])
            reference_file = (args.reference / "frames.jsonl").open(encoding="utf-8")
        else:
            depth_cache = args.depth_cache
        report["depth_cache"] = str(depth_cache)
        started = time.perf_counter()
        detector, hashes = load_detector(DEFAULT_DISTANCE_CONFIG, config, args.detection_model)
        report["load_ms"] = (time.perf_counter() - started) * 1000
        report["weights"] = hashes
        if args.reference and hashes != original["weights"]:
            raise ValueError("Reference checkpoint identity changed")
        report["engine_manifest_sha256"] = (
            detector.depth_estimator.forward.manifest_sha256
            if args.backend == "tensorrt" else None)
        capture = DepthCapture(detector.depth_estimator.predict)
        with (patch.object(detector.depth_estimator, "predict", capture),
              (args.output / "frames.jsonl").open("w", encoding="utf-8") as output):
            for identity, jpeg in source:
                started = time.perf_counter()
                metadata, _ = detector.predict(jpeg, timestamp=identity["timestamp_s"])
                wall_ms = (time.perf_counter() - started) * 1000
                depth = capture.consume()
                if metadata["depth_backend"] != args.backend:
                    raise ValueError("Actual depth backend differs from requested backend")
                if (metadata["model"] != args.detection_model
                        or metadata["depth_fusion"] != args.fusion):
                    raise ValueError("Actual detection model or fusion differs from selection")
                row = {**identity, "metadata": metadata, "wall_ms": wall_ms}
                row["depth_sha256"] = hashlib.sha256(depth.tobytes()).hexdigest()
                map_path = depth_cache / f"{identity['index']:06d}.npy"
                if reference_file:
                    expected = json.loads(reference_file.readline())
                    if any(expected[key] != identity[key] for key in identity):
                        raise ValueError("Reference input ordering or bytes changed")
                    baseline_map = np.load(map_path, allow_pickle=False)
                    if (hashlib.sha256(baseline_map.tobytes()).hexdigest()
                            != expected["depth_sha256"]):
                        raise ValueError("Reference depth cache identity changed")
                    row["comparison"] = {
                        "depth": compare_depth(baseline_map, depth),
                        "objects": compare_boxes(expected["metadata"]["boxes"], metadata["boxes"]),
                        "raw_detection_exact": (expected["metadata"]["raw_sha256"]
                                                == metadata["raw_sha256"]),
                        "jpeg_exact": (expected["metadata"]["jpeg_sha256"]
                                       == metadata["jpeg_sha256"]),
                    }
                else:
                    np.save(map_path, depth, allow_pickle=False)
                output.write(json.dumps(row, allow_nan=False) + "\n")
                output.flush()
                frames.append({"wall_ms": wall_ms,
                               **{name: metadata[name] for name in TIMING_FIELDS},
                               "comparison": row.get("comparison")})
                report["completed_frames"] += 1
                if identity["index"] % 100 == 0:
                    print(json.dumps({"backend": args.backend, "frame": identity["index"],
                                      "total": source.count}), flush=True)
            if reference_file and reference_file.readline():
                raise ValueError("Reference contains unexpected trailing frames")
        report["status"] = "completed"
        report["torch_peak_reserved_bytes"] = torch.cuda.max_memory_reserved()
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__, error=str(error))
        raise
    finally:
        if source:
            source.close()
        if reference_file:
            reference_file.close()
        report["first_frame"] = frames[0] if frames else None
        report["steady"] = {name: distribution([row[name] for row in frames[1:]])
                            for name in ("wall_ms", *TIMING_FIELDS)}
        report["comparisons"] = [row["comparison"] for row in frames if row["comparison"]]
        (args.output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False)
                                                + "\n", encoding="utf-8")
    return report


def main():
    """Functionality: Run one explicit full-video baseline or candidate process.
    Inputs: video, detection model, depth backend/fusion, manifest, output and cache/reference.
    Outputs: structured report and process failure on runtime errors.
    Logic: parse and run once. Constraints: no automatic service control, downloads or retries.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--backend", choices=("torch", "tensorrt"), required=True)
    parser.add_argument("--detection-model", choices=("yolo26n", "yolo26n-walk"), default="yolo26n")
    parser.add_argument("--fusion", choices=("gpu", "host"), default="gpu")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--depth-cache", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    report = run(args)
    print(json.dumps({"status": report["status"], "frames": report["completed_frames"]}))


if __name__ == "__main__":
    main()
