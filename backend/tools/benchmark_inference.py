"""Responsibilities: Record reproducible native-CUDA baselines for three inference workloads.
Implementation: One FP32 resident detector per process; explicit sample count; retain every frame,
separate the first call and summarize subsequent calls without trimming or retries.
An explicit depth-graph probe replaces only SDK inference after the first frame; its preparation
cost is recorded separately. Mutually exclusive probes replace ROI reduction or reuse the
initialized depth predictor without rebuilding its constant arguments.
An independent label-cache probe replaces only text rasterization, including first-call misses.
Optional cProfile or explicit stage tracing excludes first frame, probe setup and evidence writes;
profiling durations are diagnostic, with unchanged before/after controls measured separately.
The default and production inference algorithms remain unchanged.
Related Modules: realtime.detector/depth/tracking/risk;
tools.fixtures/depth_graph_probe/depth_fusion_probe/depth_predictor_probe/inference_profile;
tools.label_cache_probe (explicit independent text-raster experiment);
core.test_inference_benchmark.
Declaration Index: distribution; summarize_frames; build_detector; run; main.
Variable Index: ROOT — backend asset root; TIMINGS — nested duration fields in milliseconds.
"""

import argparse
import hashlib
import json
import logging
import math
import platform
import statistics
import time
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TIMINGS = ("predict_wall_ms", "forward_event_ms", "inference_ms", "prepare_ms", "gpu_stage_ms",
           "depth_ms", "depth_predict_ms", "depth_fusion_ms", "decode_boxes_ms",
           "depth_sdk_preprocess_ms", "depth_sdk_inference_ms",
           "depth_sdk_postprocess_ms", "tracking_ms", "risk_ms", "render_ms", "server_ms",
           "graph_setup_ms")


def distribution(values):
    """Functionality: Describe all finite duration samples using inclusive linear percentiles.
    Inputs: nonnegative millisecond values. Outputs: count/mean/min/max/P50/P95/P99 or None.
    Logic: retain outliers; interpolate only within observed endpoints; handle one sample exactly.
    Constraints: descriptive statistics only; a short static probe is not a tail-latency guarantee.
    """
    if not values:
        return None
    if any(not math.isfinite(value) or value < 0 for value in values):
        raise ValueError("Timing samples must be finite and nonnegative")
    percentiles = (statistics.quantiles(values, n=100, method="inclusive")
                   if len(values) > 1 else [values[0]] * 99)
    return {"count": len(values), "mean_ms": statistics.mean(values), "min_ms": min(values),
            "max_ms": max(values), "p50_ms": statistics.median(values),
            "p95_ms": percentiles[94], "p99_ms": percentiles[98]}


def summarize_frames(frames):
    """Functionality: Separate cold first-call measurements from all subsequent completions.
    Inputs: ordered per-frame records. Outputs: first frame and per-metric subsequent distributions.
    Logic: remove only index zero from steady summaries; preserve every other slow frame.
    Constraints: optional stage metrics are reported only when present; durations are nested,
    not additive; frame processing throughput is not browser or network FPS.
    """
    return {"first_frame": frames[0] if frames else None,
            "steady": {name: distribution([frame[name] for frame in frames[1:] if name in frame])
                       for name in TIMINGS}}


def build_detector(mode, weights, manifest_path, depth_weights):
    """Functionality: Construct one unchanged local FP32 pipeline for the explicit workload.
    Inputs: detection/depth/full mode and local asset paths. Outputs: resident detector.
    Logic: depth/full add the depth model; full adds default tracking/motion/risk.
    Constraints: no production-plan substitution, downloads, precision change or CPU fallback;
    use one process per mode because Torch inter-op configuration is process-local.
    """
    from realtime.depth import DepthEstimator
    from realtime.detector import ResidentDetector
    from realtime.tracking import DEFAULT_TRACKING_CONFIG

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    depth = (DepthEstimator(depth_weights, manifest["build"]["ultralytics"])
             if mode != "detection" else None)
    return ResidentDetector(weights, manifest_path, config={}, depth_estimator=depth,
                            tracking_config=DEFAULT_TRACKING_CONFIG if mode == "full" else None)


def run(args):
    """Functionality: Save identities, all predictions and timing summaries for one local probe.
    Inputs: explicit mode/frame count, optional graph/fusion/predictor/label probe, profiler/tracer,
    assets and directory.
    Outputs: report.json and flushed frames.jsonl; exceptions propagate after failure recording.
    Logic: encode once, create one model, time ordered predict calls, retain hashes/boxes;
    copy the depth SDK's existing synchronized timings after each measured call without new sync;
    write memory high-water marks separately from durations and keep first-call setup costs.
    Optional graph preparation occurs after the first frame and before steady samples, with its
    own duration/equality evidence; the first-frame field alone is not candidate readiness time.
    Optional fusion-sort substitution applies to every frame and is restored even on failure.
    Cached predictor substitution occurs after the unchanged first frame; setup is recorded.
    Label-cache substitution starts with an empty cache on frame zero; counters are saved per frame.
    Optional cProfile collects only subsequent predict calls, including failed attempts, and
    saves every function/caller after timing. Its durations cannot establish a speed improvement.
    A separate explicit stage trace wraps original calls and existing SDK clocks, without adding
    synchronization. Trace installation/source hashing is separate from frame timing.
    Constraints: no ground-truth accuracy, video, HF, FP16-plan or moving-scene validation claim;
    output writing is outside predict timing; failures stop without retry or skipped frames.
    """
    if args.frames < 2 or args.mode not in {"detection", "depth", "full"}:
        raise ValueError("Specify a known workload and at least two frames")
    depth_graph = getattr(args, "depth_graph", False)
    fusion_sort = getattr(args, "depth_fusion_sort", False)
    cached_predictor = getattr(args, "depth_cached_predictor", False)
    label_cache = getattr(args, "label_cache", False)
    profile_steady = getattr(args, "profile_steady", False)
    trace_steady = getattr(args, "trace_steady", False)
    if trace_steady and (args.mode != "full" or profile_steady):
        raise ValueError("Stage tracing requires full mode and a separate run from cProfile")
    if any((depth_graph, fusion_sort, cached_predictor)) and args.mode == "detection":
        raise ValueError("Depth probes require depth or full workload")
    if sum((depth_graph, fusion_sort, cached_predictor, label_cache)) > 1:
        raise ValueError("Measure one independent optimization at a time")
    output = args.output.resolve()
    if output.exists():
        raise ValueError("Output already exists; choose a new directory")
    assets = {"input": args.input, "detection": args.weights, "manifest": args.manifest}
    if args.mode != "detection":
        assets["depth"] = args.depth_weights
    identities = {}
    for name, path in assets.items():
        with path.open("rb") as source:
            identities[name] = hashlib.file_digest(source, "sha256").hexdigest()
    implementation = {}
    for relative in ("core/detector.py", "realtime/detector.py", "realtime/depth.py",
                     "realtime/tracking.py", "realtime/risk.py", "realtime/distance.py",
                     "realtime/distance_config.py", "realtime/performance.py", "zerogpu/runtime.py",
                     "tools/fixtures.py", "tools/benchmark_inference.py",
                     "tools/depth_graph_probe.py", "tools/depth_fusion_probe.py",
                     "tools/depth_predictor_probe.py", "tools/inference_profile.py",
                     "tools/label_cache_probe.py"):
        implementation[relative] = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
    output.mkdir(parents=True)
    report = {"schema_version": 1, "status": "running", "mode": args.mode,
              "scope": "Native FP32 static-image probe; not production FP16 or video validation",
              "requested_frames": args.frames, "completed_frames": 0,
              "identities": identities, "implementation_sha256": implementation,
              "settings": {"detection_config": {}, "depth_graph_probe": depth_graph,
              "depth_fusion_sort_probe": fusion_sort,
              "depth_cached_predictor_probe": cached_predictor,
              "label_cache_probe": label_cache,
              "profile_steady": profile_steady, "trace_steady": trace_steady, "batch": 1,
              "detection_edge": 640, "confidence_threshold": 0.25, "jpeg_quality": 80,
              "depth_edge": 768 if args.mode != "detection" else None,
              "depth_precision": "FP32" if args.mode != "detection" else None},
              "timing_notes": ["Only first frame excluded from steady summaries",
                               "inference_ms includes transfers; forward_event_ms is CUDA replay",
                               "depth_ms includes box decoding and depth inference/fusion",
                               "depth substages use host clocks, with no added CUDA sync",
                               "depth_sdk timings come from the existing synchronized SDK profiler",
                               "Stage durations overlap with server_ms and predict_wall_ms",
                               "Tracking uses actual worker timestamps; static image repeated"]}
    frames = []
    label_probe = None
    profiler = None
    if profile_steady:
        from tools.inference_profile import SteadyProfile

        profiler = SteadyProfile()
    tracer = None
    if trace_steady:
        from tools.inference_profile import StageTrace

        tracer = StageTrace()
    try:
        import torch
        import ultralytics

        from tools.fixtures import fixture_bytes

        if not torch.cuda.is_available():
            raise RuntimeError("Native CUDA required")
        report["environment"] = {"python": platform.python_version(),
                                 "platform": platform.platform(), "torch": torch.__version__,
                                 "cuda": torch.version.cuda, "ultralytics": ultralytics.__version__,
                                 "gpu": torch.cuda.get_device_name()}
        frame_bytes, size = fixture_bytes(args.input)
        report["prepared_input"] = {"sha256": hashlib.sha256(frame_bytes).hexdigest(),
                                    "size": list(size), "bytes": len(frame_bytes)}
        torch.cuda.reset_peak_memory_stats()
        started = time.perf_counter()
        detector = build_detector(args.mode, args.weights, args.manifest, args.depth_weights)
        report["model_initialization_ms"] = (time.perf_counter() - started) * 1000
        if args.mode == "full":
            from realtime.risk import DEFAULT_RISK_CONFIG
            from realtime.tracking import DEFAULT_TRACKING_CONFIG

            report["settings"].update(tracking=asdict(DEFAULT_TRACKING_CONFIG),
                                       risk=asdict(DEFAULT_RISK_CONFIG))
        with (ExitStack() as experiment,
              (output / "frames.jsonl").open("w", encoding="utf-8") as records):
            if label_cache:
                from unittest.mock import patch

                from tools.label_cache_probe import LabelRasterCache

                label_probe = LabelRasterCache()
                experiment.enter_context(patch("realtime.detector.annotate", label_probe.annotate))
            if fusion_sort:
                from unittest.mock import patch

                from tools.depth_fusion_probe import sorted_roi_median

                experiment.enter_context(patch("realtime.depth.roi_median", sorted_roi_median))
            for index in range(args.frames):
                started = time.perf_counter()
                if profiler is not None:
                    metadata, _ = profiler.call(index, detector.predict, frame_bytes)
                elif tracer is not None:
                    metadata, _ = tracer.call(index, detector.predict, frame_bytes)
                else:
                    metadata, _ = detector.predict(frame_bytes)
                wall_ms = (time.perf_counter() - started) * 1000
                frame = {name: metadata[name] for name in TIMINGS if name in metadata}
                frame.update(index=index, predict_wall_ms=wall_ms)
                if label_probe is not None:
                    frame["label_cache"] = label_probe.snapshot()
                if args.mode != "detection":
                    speed = detector.depth_estimator.model.predictor.speed
                    for stage in ("preprocess", "inference", "postprocess"):
                        frame[f"depth_sdk_{stage}_ms"] = speed[stage]
                records.write(json.dumps({**frame, "metadata": metadata}, allow_nan=False) + "\n")
                records.flush()
                frames.append(frame)
                report["completed_frames"] += 1
                if index == 0 and cached_predictor:
                    from unittest.mock import patch

                    from tools.depth_predictor_probe import CachedDepthPredictor

                    owner = detector.depth_estimator.model
                    probe = CachedDepthPredictor(owner)
                    experiment.enter_context(patch.object(owner, "predict", probe))
                    report["depth_cached_predictor"] = probe.evidence
                if index == 0 and depth_graph:
                    import numpy as np

                    from core.detector import decode_frame
                    from tools.depth_graph_probe import DepthGraphProbe

                    predictor = detector.depth_estimator.model.predictor
                    image = np.ascontiguousarray(np.asarray(decode_frame(frame_bytes))[..., ::-1])
                    example = predictor.preprocess([image])
                    probe = DepthGraphProbe(predictor.inference, example)
                    predictor.inference = probe
                    report["depth_graph"] = probe.evidence
                if index == 0 and tracer is not None:
                    trace_started = time.perf_counter()
                    tracer.install(detector, experiment)
                    report["stage_trace_setup_ms"] = (time.perf_counter() - trace_started) * 1000
        report.update(status="complete", gpu_peak_allocated_bytes=torch.cuda.max_memory_allocated(),
                      gpu_peak_reserved_bytes=torch.cuda.max_memory_reserved())
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__)
        raise
    finally:
        report.update(summarize_frames(frames))
        if profiler is not None:
            report["steady_profile"] = profiler.write(output)
        if tracer is not None:
            report["steady_stage_trace"] = tracer.write(output)
        if label_probe is not None:
            report["label_cache"] = label_probe.snapshot()
        (output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n",
                                            encoding="utf-8")
    return report


def main(argv=None):
    """Functionality: Run one explicitly sized workload from the backend module entry point.
    Inputs: argv or command line. Outputs: report path on success, nonzero status on error.
    Logic: parse asset locations, initialize diagnostic logging and invoke the single-process run.
    Constraints: no implicit repeat count, network, retries or existing-output edits.
    """
    parser = argparse.ArgumentParser(description="Native inference baseline, one process per mode")
    parser.add_argument("--mode", required=True, choices=("detection", "depth", "full"))
    parser.add_argument("--frames", type=int, required=True)
    probes = parser.add_mutually_exclusive_group()
    probes.add_argument("--depth-graph", action="store_true",
                        help="Experimental native FP32 graph after first frame; no deployment")
    probes.add_argument("--depth-fusion-sort", action="store_true",
                        help="Experimental device-count exact ROI median; no deployment")
    probes.add_argument("--depth-cached-predictor", action="store_true",
                        help="Experimental initialized SDK predictor reuse; no deployment")
    probes.add_argument("--label-cache", action="store_true",
                        help="Independent bounded full-text raster experiment; no deployment")
    diagnostics = parser.add_mutually_exclusive_group()
    diagnostics.add_argument("--profile-steady", action="store_true",
                             help="Diagnostic cProfile after first frame; perturbs timings")
    diagnostics.add_argument("--trace-steady", action="store_true",
                             help="Full mode explicit stage wrappers; nested host timings")
    parser.add_argument("--input", type=Path, default=ROOT / "model_tests/samples/bus.jpg")
    parser.add_argument("--weights", type=Path, default=ROOT / "models/yolo26n.pt")
    parser.add_argument("--manifest", type=Path, default=ROOT / "models/yolo26n.json")
    parser.add_argument("--depth-weights", type=Path, default=ROOT / "models/yolo26n-depth.pt")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    report = run(args)
    print(json.dumps({"status": report["status"], "frames": report["completed_frames"],
                      "report": str(args.output / "report.json")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
