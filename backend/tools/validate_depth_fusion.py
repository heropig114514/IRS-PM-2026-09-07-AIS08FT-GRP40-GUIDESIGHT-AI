"""Responsibilities: Compare experimental ROI fusion on every explicitly supplied still image.
Implementation: One native FP32 pipeline without tracking; forward/reverse image order;
exact output comparisons and isolated control/candidate/control fusion measurements.
Related Modules: tools.depth_fusion_probe/benchmark_inference/fixtures; realtime.depth/detector.
Declaration Index: measure_fusion; run; main.
Variable Index: ROOT — backend asset root; no truth data or production acceptance.
"""

import argparse
import hashlib
import json
import platform
import time
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def measure_fusion(depth_map, boxes, size, samples):
    """Functionality: Isolate completed ROI fusion from depth forward and model startup noise.
    Inputs: actual CUDA map, detector boxes, frame size and explicit sample count.
    Outputs: every duration and exact comparison for control/sort/control, plus operator counts.
    Logic: synchronize pending model work before measurement; the normal distance-vector CPU
    read completes each measured fusion. Keep first sample separate and retain all later samples.
    Constraints: same map/boxes for all phases; no model FPS claim, retries or outlier deletion.
    Operator profiling runs afterwards and its perturbed durations are never used for speedups.
    """
    import torch

    from realtime.depth import fuse_object_depth
    from tools.benchmark_inference import distribution
    from tools.depth_fusion_probe import sorted_roi_median

    torch.cuda.synchronize()
    expected = fuse_object_depth(depth_map, boxes, size)
    phases = []
    for name in ("control_before", "sort", "control_after"):
        measurements = []
        with (patch("realtime.depth.roi_median", sorted_roi_median)
              if name == "sort" else nullcontext()):
            for index in range(samples):
                started = time.perf_counter()
                actual = fuse_object_depth(depth_map, boxes, size)
                elapsed = (time.perf_counter() - started) * 1000
                measurements.append({"index": index, "fusion_ms": elapsed,
                                     "exact": actual == expected})
            with torch.profiler.profile(activities=[torch.profiler.ProfilerActivity.CPU]) as trace:
                fuse_object_depth(depth_map, boxes, size)
        phases.append({"phase": name, "samples": measurements, "first": measurements[0],
                       "steady": distribution([row["fusion_ms"] for row in measurements[1:]]),
                       "operator_counts": {event.key: event.count for event in trace.key_averages()
                                           if event.key in {"aten::nonzero", "aten::sort",
                                                            "aten::kthvalue"}}})
    return {"phases": phases, "all_exact": all(row["exact"] for phase in phases
                                             for row in phase["samples"])}


def run(inputs, output, samples):
    """Functionality: Retain all paired control/candidate results on existing image fixtures.
    Inputs: explicit image paths, isolated fusion sample count and new output directory.
    Outputs: report or recorded failure.
    Logic: same loaded detector and depth model; substitute only the ROI reducer temporarily;
    compare every object and encoded output exactly, including repeated shapes in reverse order.
    Constraints: no tracking across unrelated stills, new evaluation split, numerical tolerance,
    fallback, retries or model substitution; isolated fusion timing is not model throughput.
    """
    if (not inputs or output.exists() or samples < 2
            or any(not path.is_file() for path in inputs)):
        raise ValueError("Provide existing images, at least two samples and a new output directory")
    output.mkdir(parents=True)
    report = {"status": "running", "scope": "Exact static ROI-fusion diagnostic; no truth data",
              "requested_comparisons": len(inputs) * 2, "completed_comparisons": 0, "frames": [],
              "isolated_samples_per_phase": samples, "isolated_fusion": [],
              "input_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}}
    report["implementation_sha256"] = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in ("realtime/depth.py", "realtime/detector.py", "zerogpu/runtime.py",
                     "tools/depth_fusion_probe.py", "tools/validate_depth_fusion.py")}
    try:
        import torch

        from core.detector import decode_frame
        from tools.benchmark_inference import build_detector
        from tools.depth_fusion_probe import sorted_roi_median
        from tools.fixtures import fixture_bytes

        detector = build_detector("depth", ROOT / "models/yolo26n.pt", ROOT / "models/yolo26n.json",
                                  ROOT / "models/yolo26n-depth.pt")
        report["environment"] = {"python": platform.python_version(), "torch": torch.__version__,
                                 "cuda": torch.version.cuda, "gpu": torch.cuda.get_device_name()}
        report["assets_sha256"] = {}
        for name in ("yolo26n.pt", "yolo26n-depth.pt", "yolo26n.json"):
            with (ROOT / "models" / name).open("rb") as asset:
                report["assets_sha256"][name] = hashlib.file_digest(asset, "sha256").hexdigest()
        for index, path in enumerate([*inputs, *reversed(inputs)]):
            raw, size = fixture_bytes(path)
            control, control_jpeg = detector.predict(raw)
            with patch("realtime.depth.roi_median", sorted_roi_median):
                candidate, candidate_jpeg = detector.predict(raw)
            row = {"index": index, "input": str(path), "frame_size": size,
                   "prepared_sha256": hashlib.sha256(raw).hexdigest(),
                   "detection_exact": control["raw_sha256"] == candidate["raw_sha256"],
                   "boxes_and_distances_exact": control["boxes"] == candidate["boxes"],
                   "jpeg_exact": control_jpeg == candidate_jpeg,
                   "control": control, "candidate": candidate}
            report["frames"].append(row)
            if not all(row[key] for key in ("detection_exact", "boxes_and_distances_exact",
                                           "jpeg_exact")):
                raise AssertionError("Experimental ROI fusion differs from the control")
            report["completed_comparisons"] += 1
            if index < len(inputs):
                depth_map = detector.depth_estimator.predict(decode_frame(raw))
                isolated = measure_fusion(depth_map, control["boxes"], size, samples)
                report["isolated_fusion"].append({"input": str(path), "count": control["count"],
                                                   **isolated})
                if not isolated["all_exact"]:
                    raise AssertionError("Repeated isolated fusion differs from control")
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
    """Functionality: Run an explicit existing-image comparison without enabling the candidate.
    Inputs: --inputs, --samples and --output. Outputs: report path/count or nonzero error.
    Logic: parse paths and call the paired diagnostic. Constraints: no quality acceptance.
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
