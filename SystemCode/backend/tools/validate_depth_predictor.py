"""Responsibilities: Check cached depth predictor equivalence and isolate SDK dispatch overhead.
Implementation: Existing images in forward/reverse order, full-map and rendered-result equality;
separate original/cached/original dispatch measurements retaining every timed invocation.
Related Modules: tools.depth_predictor_probe/benchmark_inference/fixtures; realtime.depth.
Declaration Index: measure_dispatch; measure_dispatch.timed_call; run; main.
Variable Index: ROOT — backend assets; no ground-truth or production acceptance.
"""

import argparse
import hashlib
import json
import platform
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def measure_dispatch(owner, image, samples):
    """Functionality: Separate SDK wrapper overhead from actual predictor processing.
    Inputs: warmed original owner, one existing image and explicit sample count.
    Outputs: every inner/outer duration for original/cached/original, setup cost and summaries.
    Logic: time the unchanged predictor class call inside the normal wrapper or guarded probe.
    Constraints: profiling wrapper overhead is shared; nested durations cannot be added; all
    outliers retained, no extra synchronization, synthetic no-op model or inference bypass.
    """
    from tools.benchmark_inference import distribution
    from tools.depth_predictor_probe import CALL_OPTIONS, CachedDepthPredictor

    probe = CachedDepthPredictor(owner)
    original = type(owner.predictor).__call__
    inner = []

    def timed_call(predictor, *args, **kwargs):
        """Functionality: Time a complete original predictor call without changing its work.
        Inputs: actual SDK invocation. Outputs: unchanged results and one host elapsed sample.
        Logic: call original exactly once. Constraints: exceptions propagate without retry.
        """
        started = time.perf_counter()
        result = original(predictor, *args, **kwargs)
        inner.append((time.perf_counter() - started) * 1000)
        return result

    phases = []
    with patch.object(type(owner.predictor), "__call__", timed_call):
        for name in ("original_before", "cached", "original_after"):
            rows = []
            caller = probe if name == "cached" else owner.predict
            for index in range(samples):
                previous = len(inner)
                started = time.perf_counter()
                results = caller(source=image, **CALL_OPTIONS)
                wall = (time.perf_counter() - started) * 1000
                if len(inner) != previous + 1 or len(results) != 1:
                    raise AssertionError("Expected exactly one predictor call and result")
                rows.append({"index": index, "outer_ms": wall, "inner_ms": inner[-1],
                             "dispatch_ms": wall - inner[-1]})
            phases.append({"phase": name, "samples": rows, "first": rows[0],
                           "steady": {key: distribution([row[key] for row in rows[1:]])
                                      for key in ("outer_ms", "inner_ms", "dispatch_ms")}})
    return {"setup": probe.evidence, "phases": phases}


def run(inputs, output, samples):
    """Functionality: Preserve paired exact evidence and dispatch profiling on existing fixtures.
    Inputs: explicit paths, sample count and new output directory. Outputs: report or failure.
    Logic: initialize once normally; compare full depth maps, original detection hashes, object
    distances and JPEGs in both input orders; profile dispatch separately on the first image.
    Constraints: no tracking across unrelated stills, model changes, tolerance or new data split;
    setup is separate, all inputs retained, and exceptions are recorded without retries.
    """
    if (not inputs or samples < 2 or output.exists()
            or any(not path.is_file() for path in inputs)):
        raise ValueError("Provide existing images, at least two samples and a new directory")
    output.mkdir(parents=True)
    report = {"status": "running", "scope": "Static exact diagnostic, no video/accuracy claim",
              "requested_comparisons": len(inputs) * 2, "completed_comparisons": 0, "frames": [],
              "dispatch_samples_per_phase": samples,
              "input_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}}
    report["implementation_sha256"] = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in ("realtime/depth.py", "realtime/detector.py", "zerogpu/runtime.py",
                     "tools/depth_predictor_probe.py", "tools/validate_depth_predictor.py")}
    try:
        import torch
        import ultralytics

        from core.detector import decode_frame
        from tools.benchmark_inference import build_detector
        from tools.depth_predictor_probe import CachedDepthPredictor
        from tools.fixtures import fixture_bytes

        detector = build_detector("depth", ROOT / "models/yolo26n.pt", ROOT / "models/yolo26n.json",
                                  ROOT / "models/yolo26n-depth.pt")
        report["environment"] = {"python": platform.python_version(), "torch": torch.__version__,
                                 "ultralytics": ultralytics.__version__, "cuda": torch.version.cuda,
                                 "gpu": torch.cuda.get_device_name()}
        report["assets_sha256"] = {}
        for name in ("yolo26n.pt", "yolo26n-depth.pt", "yolo26n.json"):
            with (ROOT / "models" / name).open("rb") as asset:
                report["assets_sha256"][name] = hashlib.file_digest(asset, "sha256").hexdigest()
        sdk_root = Path(ultralytics.__file__).resolve().parent
        report["sdk_source_sha256"] = {
            name: hashlib.sha256((sdk_root / name).read_bytes()).hexdigest()
            for name in ("engine/model.py", "engine/predictor.py", "cfg/__init__.py")}
        estimator, probe = detector.depth_estimator, None
        for index, path in enumerate([*inputs, *reversed(inputs)]):
            raw, size = fixture_bytes(path)
            image = decode_frame(raw)
            before = estimator.predict(image).clone()
            original, original_jpeg = detector.predict(raw)
            if probe is None:
                probe = CachedDepthPredictor(estimator.model)
                report["setup"] = probe.evidence
            with patch.object(estimator.model, "predict", probe):
                after = estimator.predict(image)
                candidate, candidate_jpeg = detector.predict(raw)
            row = {"index": index, "input": str(path), "frame_size": size,
                   "prepared_sha256": hashlib.sha256(raw).hexdigest(),
                   "depth_exact": torch.equal(before, after),
                   "max_depth_difference_m": (before - after).abs().max().item(),
                   "depth_sha256": hashlib.sha256(before.cpu().numpy().tobytes()).hexdigest(),
                   "candidate_depth_sha256": hashlib.sha256(
                       after.cpu().numpy().tobytes()).hexdigest(),
                   "detection_exact": original["raw_sha256"] == candidate["raw_sha256"],
                   "boxes_and_distances_exact": original["boxes"] == candidate["boxes"],
                   "jpeg_exact": original_jpeg == candidate_jpeg,
                   "control": original, "candidate": candidate}
            report["frames"].append(row)
            if not all(row[key] for key in ("depth_exact", "detection_exact",
                                           "boxes_and_distances_exact", "jpeg_exact")):
                raise AssertionError("Cached predictor differs from original output")
            report["completed_comparisons"] += 1
        image = decode_frame(fixture_bytes(inputs[0])[0])
        report["dispatch"] = measure_dispatch(estimator.model, image, samples)
        report.update(status="completed",
                      peak_allocated_mib=torch.cuda.max_memory_allocated() / 2**20)
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__)
        raise
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n",
                                            encoding="utf-8")
    return report


def main():
    """Functionality: Run explicit existing-image validation and dispatch measurements.
    Inputs: --inputs, --samples, --output. Outputs: completion count or nonzero failure.
    Logic: parse required parameters and call run. Constraints: no production acceptance.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", nargs="+", type=Path, required=True)
    parser.add_argument("--samples", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.inputs, args.output, args.samples)
    print(json.dumps({"status": result["status"], "comparisons": result["completed_comparisons"]}))


if __name__ == "__main__":
    main()
