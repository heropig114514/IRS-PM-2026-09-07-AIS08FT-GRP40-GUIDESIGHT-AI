"""Responsibilities: Run isolated serial evaluation passes with recoverable method failures.
Implementation: One child per method/bbox pass; persist predictions, observations and manifests.
Related Modules: unified_evaluation.dataset/adapters/performance/scoring/reporting.
Declaration Index: source_snapshot; read_image; evaluate_pass; worker; comparison_conditions;
run; main.
Variable Index: PRESERVED — production/E source paths fingerprinted before and after evaluation.
logger — bounded progress and explicit worker failure diagnostics.
"""

import argparse
import importlib.metadata
import json
import logging
import os
import platform
import subprocess
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

from object_distance.config import ROOT, digest, write_json

from .dataset import TARGET, load_records, prepare, safe_path
from .performance import MemorySampler, SequenceClock, latency_summary, synchronize
from .scoring import paired_scores, score_frame, summarize

PRESERVED = (
    "core/detector.py",
    "zerogpu/runtime.py",
    "realtime/detector.py",
    "realtime/depth.py",
    "realtime/distance.py",
    "realtime/distance_config.py",
    "realtime/tracking.py",
    "object_distance/model.py",
    "object_distance/predict.py",
    "object_distance/train.py",
    "object_distance/evaluate.py",
    "object_distance/data.py",
    "object_distance/metrics.py",
)
logger = logging.getLogger(__name__)


def source_snapshot(paths=PRESERVED):
    """Functionality: Record reproducible source content, including uncommitted E code.
    Inputs: backend-relative paths. Outputs: path/SHA mapping. Logic: hash existing source files.
    Constraints: Git HEAD alone cannot identify local uncommitted implementations.
    """
    return {str(path): digest(ROOT / path) for path in paths}


def read_image(summary, frame):
    """Functionality: Decode one verified RGB outside inference timing.
    Inputs: dataset summary and frame. Outputs: independent PIL RGB image.
    Logic: preserve original dimensions/pixels. Constraints: no EXIF transform or extra JPEG.
    """
    with Image.open(safe_path(summary["raw_directory"], frame["image_path"])) as image:
        if list(image.size) != frame["image_size"] or image.mode != "RGB":
            raise ValueError("Image shape/mode changed")
        return image.copy()


def evaluate_pass(adapter, summary, frames, output, options):
    """Functionality: Score cold/warm/steady calls separately while preserving temporal order.
    Inputs: loaded adapter,dataset,owned output and options. Outputs: metrics/performance report.
    Logic: discard cold/warm state, reset scene/camera state on actual source boundaries, flush
    per-frame predictions and annotation observations. Constraints: no sweep GT invention;
    cold probe/warmups are excluded from scored frame and depth-call counts.
    """
    import torch

    if not frames:
        raise ValueError("No evaluation frames")
    image = read_image(summary, frames[0])
    adapter.reset()
    _, cold, cold_depth = adapter.predict(
        image, frames[0], bbox_mode=options["bbox_mode"], timestamp=frames[0]["timestamp"] / 1e6
    )
    warm_rows, warm_depth_calls, clock = [], 0, SequenceClock()
    for index in range(options["warmup"]):
        if index % len(frames) == 0:
            clock = SequenceClock()
        frame = frames[index % len(frames)]
        timestamp, _ = clock.advance(frame, adapter)
        _, timings, depth = adapter.predict(
            read_image(summary, frame), frame, bbox_mode=options["bbox_mode"], timestamp=timestamp
        )
        warm_rows.append(timings)
        warm_depth_calls += depth
    synchronize(options["device"])
    if options["device"].startswith("cuda"):
        initialization_peak = torch.cuda.max_memory_allocated(options["device"])
        torch.cuda.reset_peak_memory_stats(options["device"])
    else:
        initialization_peak = None
    clock, rows, observations, depth_flags = SequenceClock(), [], [], []
    depth_calls, unused = 0, 0
    subsets = {True: [], False: []}
    with (output / "predictions.jsonl").open("w", encoding="utf-8") as predictions_file:
        with (output / "observations.jsonl").open("w", encoding="utf-8") as observed_file:
            for index, frame in enumerate(frames):
                image = read_image(summary, frame)
                timestamp, delta = clock.advance(frame, adapter)
                objects, timings, depth = adapter.predict(
                    image, frame, bbox_mode=options["bbox_mode"], timestamp=timestamp
                )
                observed, unmatched = score_frame(
                    frame,
                    objects,
                    bbox_mode=options["bbox_mode"],
                    match_iou=options["match_iou"],
                    depth_frame=depth,
                )
                observations.extend(observed)
                subsets[depth].extend(observed)
                rows.append(timings)
                depth_flags.append(depth)
                depth_calls, unused = depth_calls + int(depth), unused + unmatched
                record = {
                    "frame_id": frame["frame_id"],
                    "scene_id": frame["scene_id"],
                    "camera": frame["camera"],
                    "timestamp": frame["timestamp"],
                    "source_delta_seconds": delta,
                    "distance_target": TARGET,
                    "depth_frame": depth,
                    "timings": timings,
                    "objects": objects,
                }
                predictions_file.write(json.dumps(record, allow_nan=False) + "\n")
                predictions_file.flush()
                for row in observed:
                    observed_file.write(json.dumps(row, allow_nan=False) + "\n")
                observed_file.flush()
                if index % 25 == 0:
                    logger.info(
                        "Evaluation method=%s bbox=%s frames=%d/%d",
                        options["method"],
                        options["bbox_mode"],
                        index + 1,
                        len(frames),
                    )
    gpu = torch.device(options["device"]).type == "cuda"
    return {
        "status": "complete",
        "method": options["method"],
        "bbox_mode": options["bbox_mode"],
        "frames": len(frames),
        "identity": adapter.identity,
        "metrics": summarize(observations),
        "unmatched_predictions": unused,
        "temporal": {
            "depth_interval": options["depth_interval"],
            "depth_calls": depth_calls,
            "total_frames": len(frames),
            "actual_depth_ratio": depth_calls / len(frames),
            "depth_frames": sum(depth_flags),
            "non_depth_frames": len(frames) - sum(depth_flags),
            "depth_frame_metrics": summarize(subsets[True]) if options["method"] == "c" else None,
            "non_depth_frame_metrics": summarize(subsets[False])
            if options["method"] == "c"
            else None,
            "depth_frame_latency": latency_summary(
                [row for row, depth in zip(rows, depth_flags, strict=True) if depth]
            )
            if options["method"] == "c"
            else None,
            "non_depth_frame_latency": latency_summary(
                [row for row, depth in zip(rows, depth_flags, strict=True) if not depth]
            )
            if options["method"] == "c"
            else None,
            "scope": "Annotated keyframes, approximately2Hz; not30FPS validation",
        },
        "performance": {
            "cold_first_call": cold,
            "cold_depth_calls": int(cold_depth),
            "warmup_calls": len(warm_rows),
            "warmup_depth_calls": warm_depth_calls,
            "warmup": latency_summary(warm_rows),
            "steady": latency_summary(rows),
            "load_cold_warm_peak_gpu_allocated_bytes": initialization_peak,
            "peak_gpu_allocated_bytes": torch.cuda.max_memory_allocated(options["device"])
            if gpu
            else None,
            "peak_gpu_reserved_bytes": torch.cuda.max_memory_reserved(options["device"])
            if gpu
            else None,
        },
    }


def worker(options_path):
    """Functionality: Run one isolated model/pass and preserve failures with partial artifacts.
    Inputs: orchestrator-written JSON options. Outputs: report and success boolean.
    Logic: memory sampler spans loading/inference; save report in finally and never retry.
    Constraints: no model downloads/autoinstall; no training, checkpoint changes or navigation.
    """
    from .adapters import create_adapter

    options = json.loads(Path(options_path).read_text(encoding="utf-8"))
    output = Path(options["pass_output"])
    memory = MemorySampler()
    memory.start()
    started = time.perf_counter()
    report = {"status": "failed", "method": options["method"], "bbox_mode": options["bbox_mode"]}
    try:
        summary, frames = load_records(options["processed"], options["limit"])
        loaded = time.perf_counter()
        adapter = create_adapter(options)
        synchronize(options["device"])
        load_ms = (time.perf_counter() - loaded) * 1000
        report = evaluate_pass(adapter, summary, frames, output, options)
        report["performance"]["model_load_ms"] = load_ms
        import torch

        report["environment"] = {
            "torch": str(torch.__version__),
            "cuda": torch.version.cuda,
            "device": options["device"],
            "gpu": torch.cuda.get_device_name(options["device"])
            if options["device"].startswith("cuda")
            else None,
            "packages": {
                name: importlib.metadata.version(name)
                for name in (
                    "ultralytics",
                    "torchvision",
                    "nuscenes-devkit",
                    "psutil",
                    "numpy",
                    "Pillow",
                    "pyquaternion",
                    "shapely",
                )
            },
            "cpu_threads": torch.get_num_threads(),
            "cpu_interop_threads": torch.get_num_interop_threads(),
            "tf32_matmul": torch.backends.cuda.matmul.allow_tf32,
            "tf32_cudnn": torch.backends.cudnn.allow_tf32,
            "cudnn_benchmark": torch.backends.cudnn.benchmark,
        }
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__, error=str(error))
        (output / "failure.txt").write_text(traceback.format_exc(), encoding="utf-8")
        logger.exception(
            "Evaluation pass failed method=%s bbox=%s", options["method"], options["bbox_mode"]
        )
    finally:
        report["memory"] = memory.stop()
        report["elapsed_s"] = time.perf_counter() - started
        write_json(output / "result.json", report)
    return report["status"] == "complete"


def comparison_conditions(results):
    """Functionality: Expose detector/runtime/target differences before interpreting a ranking.
    Inputs: successful pass results. Outputs: explicit non-strict comparison conditions.
    Logic: compare recorded detector hashes/classes/thresholds/sizes and retain every identity.
    Constraints: matching base SHA does not prove equal tuned E detector state or executor output.
    """
    identities = {
        name: result["identity"]
        for name, result in results.items()
        if result["status"] == "complete"
    }
    checks = {}
    for key in ("detector_sha256", "classes", "confidence", "image_size", "preprocessing"):
        checks[f"same_{key}"] = (
            len({json.dumps(row[key], sort_keys=True) for row in identities.values()}) == 1
            if identities
            else None
        )
    return {
        "strict_controlled_comparison": False,
        "checks": checks,
        "identities": identities,
        "reference_target": TARGET,
        "limitations": [
            "A/C ROI median can represent visible surface/background rather than3D box center",
            "E learned KITTI reference z; nuScenes uses cuboid center z without test calibration",
            "A/B/C use fused FP32 CUDA Graph; E retains checkpoint eager/AMP configuration",
            "Native scalar inverse and E exact rounded x/y inverse can yield different boxes",
            "Original RGB replaces production bounded JPEG80 ingress for all methods",
            "GT pass includes detector compute but replaces boxes; C uses BoT-SORT on GT boxes",
            "Stage timing includes existing executor-specific transfers/postprocessing",
            "End-to-end starts at in-memory RGB preprocessing, ends at canonical output",
            "Disk decode/output, metrics, render, network, TTC and risk are excluded",
            "GTs are projected cuboid hulls, not camera-specific occlusion masks",
            "Mini is an external diagnostic set; pretrained data overlap is not established",
        ],
    }


def run(options, *, launch=None):
    """Functionality: Evaluate requested methods serially and publish honest partial comparisons.
    Inputs: resolved configuration and optional injected worker launcher for tests. Outputs: report.
    Logic: one subprocess per method/bbox; aggregate only complete passes and paired valid IDs.
    Constraints: new output only, no default full run; --limit or explicit --full is mandatory.
    """
    from .reporting import write_reports

    output = Path(options["output"])
    if output.exists():
        raise ValueError("Evaluation output must be a new directory")
    if options["limit"] is None and not options["full"]:
        raise ValueError("Choose --limit N for smoke or explicitly --full")
    summary, frames = load_records(options["processed"], options["limit"])
    methods = ["a", "b", "c", "e"] if options["method"] == "all" else [options["method"]]
    modes = ["gt", "predicted"] if options["bbox_mode"] == "both" else [options["bbox_mode"]]
    output.mkdir(parents=True)
    (output / "predictions").mkdir()
    (output / "passes").mkdir()
    before = source_snapshot()
    weights_before = {
        key: digest(value) if value and Path(value).is_file() else None
        for key, value in options.items()
        if key in {"detection_weights", "depth_weights", "e_checkpoint"}
    }
    code = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    status = subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True)
    manifest = {
        "schema_version": 1,
        "status": "running",
        "started_utc": datetime.now(UTC).isoformat(),
        "configuration": options,
        "git_head": code.stdout.strip(),
        "git_dirty_status": status.stdout.splitlines(),
        "source_sha256": before,
        "weight_sha256": weights_before,
        "evaluation_source_sha256": source_snapshot(
            [str(p.relative_to(ROOT)) for p in Path(__file__).parent.glob("*.py")]
        ),
        "python": sys.version,
        "platform": platform.platform(),
        "selected_frames": len(frames),
        "selected_objects": sum(len(row["objects"]) for row in frames),
        "selected_frame_ids": [row["frame_id"] for row in frames],
        "dataset_version": summary["version"],
        "class_mapping": summary["class_mapping"],
        "dataset_frames_sha256": summary["frames_sha256"],
        "passes": {},
    }
    write_json(output / "dataset_summary.json", summary)
    write_json(output / "run_manifest.json", manifest)
    results, paired = {mode: {} for mode in modes}, {}
    for method in methods:
        for mode in modes:
            name = f"{method}-{mode}"
            folder = output / "passes" / name
            folder.mkdir()
            selected = {**options, "method": method, "bbox_mode": mode, "pass_output": str(folder)}
            write_json(folder / "options.json", selected)
            logger.info("Starting isolated pass=%s frames=%d", name, len(frames))
            environment = {**os.environ, "YOLO_OFFLINE": "1", "YOLO_AUTOINSTALL": "0"}
            with (folder / "worker.log").open("w", encoding="utf-8") as log:
                if launch is None:
                    process = subprocess.run(
                        [
                            sys.executable,
                            "-X",
                            "utf8",
                            "-m",
                            "model_tests.unified_evaluation",
                            "worker",
                            "--options",
                            str(folder / "options.json"),
                        ],
                        cwd=ROOT,
                        env=environment,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                    )
                    returncode = process.returncode
                else:
                    returncode = launch(selected)
            path = folder / "result.json"
            result = (
                json.loads(path.read_text(encoding="utf-8"))
                if path.is_file()
                else {
                    "status": "failed",
                    "error": "Worker exited without result; inspect worker.log",
                }
            )
            if returncode != 0:
                result.update(status="failed", worker_returncode=returncode)
            results[mode][method] = result
            manifest["passes"][name] = {"status": result["status"], "result": str(path)}
            prediction_path = folder / "predictions.jsonl"
            if prediction_path.is_file():
                suffix = "" if mode == "predicted" else "-gt"
                (output / "predictions" / f"{method}{suffix}.jsonl").write_bytes(
                    prediction_path.read_bytes()
                )
            write_json(output / "run_manifest.json", manifest)
    for mode in modes:
        observations = {}
        for method, result in results[mode].items():
            if result["status"] == "complete":
                path = output / "passes" / f"{method}-{mode}" / "observations.jsonl"
                observations[method] = [
                    json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                ]
        paired[mode] = paired_scores(observations, methods)
    after = source_snapshot()
    evaluation_after = source_snapshot(manifest["evaluation_source_sha256"])
    weights_after = {
        key: digest(options[key]) if value is not None else None
        for key, value in weights_before.items()
    }
    manifest.update(
        finished_utc=datetime.now(UTC).isoformat(),
        preserved_sources_unchanged=before == after,
        preserved_weights_unchanged=weights_before == weights_after,
        evaluation_sources_unchanged=manifest["evaluation_source_sha256"] == evaluation_after,
        conditions={mode: comparison_conditions(rows) for mode, rows in results.items()},
    )
    success = (
        before == after
        and weights_before == weights_after
        and manifest["evaluation_sources_unchanged"]
        and all(row["status"] == "complete" for rows in results.values() for row in rows.values())
    )
    manifest["status"] = "complete" if success else "partial_failure"
    metrics = {"status": manifest["status"], "results": results, "paired": paired}
    write_json(output / "metrics.json", metrics)
    write_json(output / "run_manifest.json", manifest)
    write_reports(output, metrics, manifest)
    return metrics


def main(argv=None):
    """Functionality: Expose explicit prepare/run and internal isolated-worker CLI operations.
    Inputs: terminal arguments. Outputs: JSON summary and nonzero failure exit.
    Logic: validate requested scope and resolve backend-relative paths before subprocess launch.
    Constraints: no downloads, full-run defaults, random E checkpoint or implicit CPU fallback.
    """
    parser = argparse.ArgumentParser(description="External nuScenes A/B/C/E center-z evaluation")
    commands = parser.add_subparsers(dest="command", required=True)
    base = ROOT / "model_tests/datasets/nuscenes"
    conversion = commands.add_parser("prepare")
    conversion.add_argument("--raw", type=Path, default=base / "raw")
    conversion.add_argument("--output", type=Path, default=base / "processed")
    conversion.add_argument("--version", default="v1.0-mini")
    conversion.add_argument("--cameras", nargs="+", default=["CAM_FRONT"])
    conversion.add_argument("--near-plane", type=float, default=0.1)
    execution = commands.add_parser("run")
    execution.add_argument("--processed", type=Path, default=base / "processed")
    execution.add_argument("--output", type=Path, required=True)
    execution.add_argument("--method", choices=("a", "b", "c", "e", "all"), default="all")
    execution.add_argument("--bbox-mode", choices=("gt", "predicted", "both"), default="both")
    execution.add_argument("--depth-interval", type=int, choices=(1, 2, 3, 5), default=3)
    execution.add_argument("--warmup", type=int, default=3)
    scope = execution.add_mutually_exclusive_group(required=True)
    scope.add_argument("--limit", type=int)
    scope.add_argument("--full", action="store_true")
    execution.add_argument("--device", default="cuda:0")
    execution.add_argument("--match-iou", type=float, default=0.5)
    execution.add_argument("--detection-weights", type=Path, default=ROOT / "models/yolo26n.pt")
    execution.add_argument("--detection-manifest", type=Path, default=ROOT / "models/yolo26n.json")
    execution.add_argument("--depth-weights", type=Path, default=ROOT / "models/yolo26n-depth.pt")
    execution.add_argument("--e-checkpoint", type=Path)
    execution.add_argument("--distance-config", type=Path)
    child = commands.add_parser("worker")
    child.add_argument("--options", type=Path, required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    if args.command == "prepare":
        report = prepare(
            args.raw,
            args.output,
            version=args.version,
            cameras=tuple(args.cameras),
            near=args.near_plane,
        )
        print(json.dumps(report, allow_nan=False))
        return 0
    if args.command == "worker":
        return 0 if worker(args.options) else 1
    if args.warmup < 0 or not 0 < args.match_iou <= 1:
        parser.error("Require warmup>=0 and0<match-iou<=1")
    options = {
        key: str(value.resolve()) if isinstance(value, Path) else value
        for key, value in vars(args).items()
        if key != "command"
    }
    report = run(options)
    print(json.dumps({"status": report["status"], "output": options["output"]}))
    return 0 if report["status"] == "complete" else 1
