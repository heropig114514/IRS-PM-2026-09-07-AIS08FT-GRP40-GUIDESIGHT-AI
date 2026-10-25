"""Responsibilities: Evaluate unchanged A/B/C and old KITTI E on continuous SANPO RGB.
Implementation: Isolated serial adapters, manual-only scoring, four-band and temporal reports.
Related Modules: sanpo.canonical; kitti.collect_metrics/write_tables; scoring/performance.
Declaration Index: load_sequence; make_frame; read_rgb; evaluate_pass; worker; write_reports;
run; main.
Variable Index: LIMITATIONS records supervision, taxonomy, sampling and runtime differences.
"""

import argparse
import csv
import json
import logging
import math
import os
import subprocess
import sys
import traceback
from collections import Counter
from pathlib import Path

from PIL import Image

from object_distance.config import ROOT, digest, write_json
from object_distance.data import KittiObjects
from object_distance.sanpo_instances import TARGET

from .kitti import collect_metrics, table_row, write_tables
from .performance import latency_summary
from .sanpo import canonical
from .scoring import paired_scores, score_frame

LIMITATIONS = [
    "SANPO original official Test sessions with downloaded continuous RGB; HUMAN_ANNOTATED "
    "frames alone supply evaluation targets. No machine masks are scored.",
    "All errors use SANPO mask-surface depth medians. Old KITTI E learned reference-point z; "
    "this is an uncalibrated cross-dataset diagnostic, not same-domain E validation.",
    "Original KITTI E supports car/person. Predictions map person to pedestrian and COCO "
    "vehicle types to vehicle; unsupported distances stay missing. "
    "All object GT remain in coverage.",
    "C receives every continuous frame, resets only at session boundaries, and uses nominal "
    "source timestamps. Interval 5 avoids scoring only depth frames with 6-frame manual spacing.",
    "A/C use bbox medians, B uses assumed category heights with per-frame camera fy. "
    "No scale fitting, retraining, class guessing or threshold adjustment on Test.",
    "A/B/C retain fused FP32 CUDA Graph; E retains eager checkpoint precision. Timing starts "
    "with in-memory RGB preprocessing and excludes decode, reports and boundary reset.",
    "Main errors use each method's valid objects; paired tables use common valid objects. "
    "AbsRel and coverage are fractions; absent bands produce null Weighted MAE.",
]


def load_sequence(options):
    """Functionality: Validate continuous RGB, declared split scope and manual target manifests.
    Inputs: root, processed, source manifest and optional limit. Outputs: sources, targets, audit.
    Logic: verify target fingerprint, session membership, camera, source order and all RGB paths.
    Constraints: reject temporal gaps; limits retain chronological prefixes.
    """
    root = Path(options["root"])
    manifest = Path(options["frames"])
    sources = [json.loads(s) for s in manifest.read_text(encoding="utf-8").splitlines()]
    dataset = KittiObjects(options["processed"], "test", distance_target=TARGET)
    if dataset.root != root.resolve():
        raise ValueError("Processed targets and RGB must share the same SANPO root")
    official = set((root / "splits/test-sessions.txt").read_text().split())
    known_splits = {
        sid: split
        for split in ("train", "validation", "test")
        if (root / f"splits/{split}-sessions.txt").is_file()
        for sid in (root / f"splits/{split}-sessions.txt").read_text().split()
    }
    targets = {r["image_id"]: r for r in dataset.records}
    if len(targets) != len(dataset.records) or not sources:
        raise ValueError("Duplicate target IDs or empty source sequence")
    ids, closed, previous = set(), set(), None
    for row in sources:
        sid, index, timestamp = row["session_id"], row["frame_index"], row["timestamp_nominal_s"]
        key = f"{sid}/{index:06d}"
        if (
            (sid not in official and not options.get("allow_diagnostic_sessions"))
            or row["camera"] != "camera_head/left"
            or key in ids
            or not math.isfinite(timestamp)
            or timestamp < 0
        ):
            raise ValueError("Invalid Test session/camera/frame identity or timestamp")
        if options.get("allow_diagnostic_sessions") and (
            sid not in known_splits or row.get("source_split") != known_splits[sid]
        ):
            raise ValueError("Diagnostic sessions must retain their original split identity")
        if previous is not None:
            if sid == previous["session_id"]:
                if (
                    index != previous["frame_index"] + 1
                    or timestamp <= previous["timestamp_nominal_s"]
                ):
                    raise ValueError("Continuous ordered RGB required for temporal evaluation")
            else:
                closed.add(previous["session_id"])
                if sid in closed:
                    raise ValueError("Session order is interleaved")
        path = (root / row["rgb"]).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError("Missing or unsafe RGB path")
        if row["annotation_type"] == "HUMAN_ANNOTATED":
            if key not in targets or targets[key]["image_path"] != row["rgb"]:
                raise ValueError("Manual targets must correspond exactly to source RGB")
        elif key in targets:
            raise ValueError("Machine annotations cannot enter manual-only evaluation")
        ids.add(key)
        previous = row
    if set(targets) - ids:
        raise ValueError("Targets without source RGB")
    if options.get("limit") is not None:
        sources = sources[: options["limit"]]
    selected = {f"{r['session_id']}/{r['frame_index']:06d}" for r in sources}
    targets = {key: value for key, value in targets.items() if key in selected}
    if not targets:
        raise ValueError("Selected prefix contains no manually annotated frames")
    annotated_phases = Counter()
    position = 0
    last_session = None
    for row in sources:
        if row["session_id"] != last_session:
            position, last_session = 0, row["session_id"]
        if row["annotation_type"] == "HUMAN_ANNOTATED":
            annotated_phases[position % options["depth_interval"]] += 1
        position += 1
    audit = {
        "frames_manifest_sha256": digest(manifest),
        "test_manifest_sha256": dataset.fingerprint,
        "continuous_rgb_frames": len(sources),
        "manual_scored_frames": len(targets),
        "ground_truth_objects": sum(len(r["objects"]) for r in targets.values()),
        "sessions": sorted({r["session_id"] for r in sources}),
        "session_source_splits": {r["session_id"]: known_splits[r["session_id"]] for r in sources},
        "classes": dict(Counter(o["class_name"] for r in targets.values() for o in r["objects"])),
        "manual_frame_schedule_phases": dict(annotated_phases),
        "manual_frames_only_on_depth_phase": set(annotated_phases) == {0},
        "timestamp_source": "manifest timestamp_nominal_s; nominal camera rate, not wall clock",
        "image_limit": options.get("limit"),
    }
    return sources, targets, audit


def make_frame(source, targets):
    """Functionality: Construct surface-distance evaluation metadata for a source frame.
    Inputs: source calibration and manual target mapping. Outputs: common adapter frame.
    Logic: use original RGB fx/fy/cx/cy and preserve every manual GT object.
    Constraints: unlabeled frames have no evaluation GT but still feed detection/tracking/depth.
    """
    key = f"{source['session_id']}/{source['frame_index']:06d}"
    camera = source["calibration"]
    return {
        "frame_id": key,
        "camera_intrinsic": [
            [camera["fx"], 0, camera["cx"]],
            [0, camera["fy"], camera["cy"]],
            [0, 0, 1],
        ],
        "objects": [
            {**obj, "annotation_id": str(i), "distance_gt_m": obj["distance_m"]}
            for i, obj in enumerate(targets.get(key, {}).get("objects", []))
        ],
    }


def read_rgb(root, source):
    """Functionality: Read unchanged source RGB outside synchronized inference timing.
    Inputs: dataset root and source frame. Outputs: detached RGB image.
    Logic: verify manifest dimensions/mode. Constraints: no extra encoding or resizing.
    """
    with Image.open(Path(root) / source["rgb"]) as image:
        if image.mode != "RGB" or list(image.size) != source["shapes"]["rgb_shape"][1::-1]:
            raise ValueError("RGB changed since SANPO validation")
        return image.copy()


def evaluate_pass(adapter, sources, targets, audit, output, options):
    """Functionality: Run full temporal inference and score only manual Test targets.
    Inputs: unchanged adapter, source sequence, targets/audit, owned output and options.
    Outputs: comparable accuracy/timing plus depth/non-depth frame accuracy and call counts.
    Logic: discard warmup state, reset at session boundaries, never reset at annotation boundaries.
    Constraints: predicted boxes only; sparse GT boxes must not replace detections in tracking.
    """
    first = sources[0]
    image, frame = read_rgb(options["root"], first), make_frame(first, targets)
    adapter.reset()
    cold = None
    for i in range(options["warmup"] + 1):
        _, timing, _ = adapter.predict(image, frame, bbox_mode="predicted", timestamp=i / 15)
        if i == 0:
            cold = timing
    observations, timings, flags, session_ids = [], [], [], []
    scored_counts = {True: 0, False: 0}
    current_session = None
    with (
        (output / "predictions.jsonl").open("w", encoding="utf-8") as predictions,
        (output / "observations.jsonl").open("w", encoding="utf-8") as observed,
    ):
        for index, source in enumerate(sources):
            frame = make_frame(source, targets)
            image = read_rgb(options["root"], source)
            if source["session_id"] != current_session:
                adapter.reset()
                current_session = source["session_id"]
            objects, timing, depth = adapter.predict(
                image,
                frame,
                bbox_mode="predicted",
                timestamp=source["timestamp_nominal_s"],
            )
            depth = bool(depth)
            timings.append(timing)
            flags.append(depth)
            session_ids.append(source["session_id"])
            if frame["frame_id"] in targets:
                scored, _ = score_frame(
                    frame,
                    canonical(objects),
                    bbox_mode="predicted",
                    match_iou=options["match_iou"],
                    depth_frame=depth,
                )
                observations.extend(scored)
                scored_counts[depth] += 1
                observed.writelines(json.dumps(r, allow_nan=False) + "\n" for r in scored)
                observed.flush()
            predictions.write(
                json.dumps(
                    {
                        "image_id": frame["frame_id"],
                        "objects": objects,
                        "depth_frame": depth,
                        "timings": timing,
                        "scored": frame["frame_id"] in targets,
                        **({"roi_timings": adapter.box_rows}
                           if options.get("profile_detailed") else {}),
                    },
                    allow_nan=False,
                )
                + "\n"
            )
            predictions.flush()
            if index % 50 == 0:
                print(options["method"], index + 1, len(sources), flush=True)
    session_reports = {}
    for sid in sorted(set(session_ids)):
        selected = [r for r in observations if r["key"].startswith(sid + "/")]
        times = [t for t, s in zip(timings, session_ids, strict=True) if s == sid]
        calls = sum(f for f, s in zip(flags, session_ids, strict=True) if s == sid)
        session_reports[sid] = {
            "status": "complete",
            "source_split": audit.get("session_source_splits", {}).get(sid),
            **collect_metrics(selected),
            "matched_objects": sum(r["matched"] for r in selected),
            "latency": latency_summary(times),
            "temporal": {
                "depth_calls": calls,
                "total_frames": len(times),
                "actual_depth_ratio": calls / len(times),
            },
        }
    return {
        "status": "complete",
        "method": options["method"],
        "bbox_mode": "predicted",
        "distance_target": TARGET,
        "split": "mixed_split_diagnostic"
        if options.get("allow_diagnostic_sessions")
        else "official_test",
        "dataset": audit,
        "identity": adapter.identity,
        **collect_metrics(observations),
        "matched_objects": sum(r["matched"] for r in observations),
        "sessions": session_reports,
        "latency": latency_summary(timings),
        "cold_first_call": cold,
        "warmup_calls": options["warmup"],
        "temporal": {
            "scope": "continuous_diagnostic_sessions"
            if options.get("allow_diagnostic_sessions")
            else "continuous_original_test_session",
            "depth_interval": options["depth_interval"],
            "depth_calls": sum(flags),
            "total_frames": len(sources),
            "actual_depth_ratio": sum(flags) / len(sources),
            "scored_depth_frames": scored_counts[True],
            "scored_non_depth_frames": scored_counts[False],
            "depth_frame_latency": latency_summary(
                [t for t, f in zip(timings, flags, strict=True) if f]
            ),
            "non_depth_frame_latency": latency_summary(
                [t for t, f in zip(timings, flags, strict=True) if not f]
            ),
            "depth_frame_scores": collect_metrics([r for r in observations if r["depth_frame"]]),
            "non_depth_frame_scores": collect_metrics(
                [r for r in observations if not r["depth_frame"]]
            ),
        },
        "pedestrian_vehicle_subset": collect_metrics(
            [r for r in observations if r["class_name"] in {"pedestrian", "vehicle"}]
        ),
        "limitations": [
            "Mixed original SANPO splits; this diagnostic set is NOT an official Test benchmark. "
            "Only HUMAN_ANNOTATED targets are scored; original split identities are retained."
            if options.get("allow_diagnostic_sessions")
            else LIMITATIONS[0],
            *LIMITATIONS[1:],
        ],
    }


def worker(options):
    """Functionality: Isolate one unchanged adapter and persist complete or failed results.
    Inputs: resolved worker options. Outputs: exit code and result.json.
    Logic: verify inputs, instantiate adapter, evaluate once. Constraints: no training or fallback.
    """
    from .adapters import create_adapter

    output = Path(options["pass_output"])
    report = {"status": "failed"}
    adapter = None
    try:
        sources, targets, audit = load_sequence(options)
        adapter = create_adapter(options)
        if options.get("profile_detailed"):
            from .detailed_profile import DetailedProfile

            adapter = DetailedProfile(adapter)
        elif options.get("profile_stages"):
            from .stage_profile import StageProfile

            adapter = StageProfile(adapter)
        report = evaluate_pass(adapter, sources, targets, audit, output, options)
        if options.get("profile_detailed"):
            from .detailed_profile import export_box_timings

            export_box_timings(output)
        native = getattr(adapter, "adapter", adapter)
        if getattr(native, "depth", None) is not None:
            actual_half = bool(native.depth.model.predictor.model.fp16)
            report["identity"]["depth_runtime_precision"] = "fp16" if actual_half else "fp32"
            if actual_half != (options.get("depth_precision", "fp32") == "fp16"):
                raise ValueError("Depth backend precision differs from requested experiment")
    except Exception as error:
        report["status"] = "failed"
        report["error"] = str(error)
        (output / "failure.txt").write_text(traceback.format_exc(), encoding="utf-8")
        logging.exception("SANPO comparison failed")
    finally:
        if options.get("profile_detailed") and hasattr(adapter, "close"):
            adapter.close()
    write_json(output / "result.json", report)
    return 0 if report["status"] == "complete" else 1


def write_reports(output, report):
    """Functionality: Add depth/non-depth timing and scored-frame errors to shared tables.
    Inputs: output and complete/partial aggregate. Outputs: standard tables plus temporal.csv.
    Logic: distinguish all inference frames from manually scored frames and object errors.
    Constraints: unlabeled frames contribute timing only, never fabricated accuracy targets.
    """
    write_tables(output, report)
    rows = []
    for method, result in report["results"]["predicted"].items():
        if result["status"] != "complete":
            continue
        temporal = result["temporal"]
        for name in ("depth", "non_depth"):
            timing = temporal[f"{name}_frame_latency"]
            scores = temporal[f"{name}_frame_scores"]
            rows.append(
                {
                    "method": method.upper(),
                    "frame_type": name,
                    "inference_frames": timing["frames"],
                    "manual_scored_frames": temporal[f"scored_{name}_frames"],
                    "end_to_end_ms": timing.get("end_to_end_ms", {}).get("mean"),
                    "fps": timing["fps"],
                    **scores["metrics"]["overall"],
                    "weighted_mae_m": scores["distance_report"]["weighted_mae_m"],
                }
            )
    if rows:
        with (output / "temporal.csv").open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    sessions, stages, breakdown = [], [], []
    for method, result in report["results"]["predicted"].items():
        if result["status"] != "complete":
            continue
        for sid, subset in result.get("sessions", {}).items():
            sessions.append(
                {
                    "session_id": sid,
                    "source_split": subset["source_split"],
                    "frames": subset["temporal"]["total_frames"],
                    **table_row(method, "predicted", subset),
                }
            )
        for stage, values in result["latency"].items():
            if stage.endswith("_ms"):
                stages.append({"method": method.upper(), "stage": stage, **values})
        if result.get("identity", {}).get("detailed_profile"):
            from .detailed_profile import EXCLUSIVE_STAGES

            for label, field in EXCLUSIVE_STAGES:
                values = result["latency"][field]
                breakdown.append({"method": method.upper(), "stage": label, "field": field,
                                  **values, "share_of_e2e": values["mean"]
                                  / result["latency"]["end_to_end_ms"]["mean"]})
    for name, records in (("sessions", sessions), ("stage_latency", stages),
                          ("pipeline_breakdown", breakdown)):
        if records:
            with (output / f"{name}.csv").open("w", newline="", encoding="utf-8-sig") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(records[0]))
                writer.writeheader()
                writer.writerows(records)


def run(options):
    """Functionality: Run four serial methods and export the same summary/band/paired tables.
    Inputs: CLI options. Outputs: complete or partial report in a fresh directory.
    Logic: preserve input fingerprints, use separate processes, pair identical valid GT objects.
    Constraints: method failures stay visible; old KITTI/SANPO artifacts remain untouched.
    """
    _, _, audit = load_sequence(options)
    output = Path(options["output"])
    output.mkdir(parents=True, exist_ok=False)
    methods = list("abce") if options["method"] == "all" else [options["method"]]
    hashes = {k: digest(options[k]) for k in ("e_checkpoint", "detection_weights")}
    if any(m in {"a", "c"} for m in methods):
        hashes["depth_weights"] = digest(options["depth_weights"])
    limitations = list(LIMITATIONS)
    if options.get("allow_diagnostic_sessions"):
        limitations[0] = (
            "Mixed original Train/Validation/Test SANPO sessions selected by existing GT counts "
            "and scene diversity. This is a diagnostic set, NOT an official Test benchmark. "
            "Old KITTI E has not been trained on these SANPO sessions."
        )
    if options.get("profile_stages"):
        limitations.append(
            "Stage profiling adds timers and depth CUDA barriers. Tracking update "
            "includes GMC/association/state; do not sum nested stages twice."
        )
    if options.get("profile_detailed"):
        limitations.append(
            "Detailed profiling adds pre/post GPU synchronization and reusable current-stream "
            "events. Wall and CUDA intervals overlap and must not be added. ROI barriers perturb "
            "latency; use these runs for attribution, not uninstrumented production FPS. "
            "Cold initialization is excluded; predictor hooks attach before steady measurements."
        )
    if audit["manual_frames_only_on_depth_phase"]:
        warning = (
            "All manual frames fall on C depth frames; choose interval 5 to test cached distances."
        )
        limitations.append(warning)
        print("WARNING:", warning, flush=True)
    report = {
        "title": "SANPO continuous diagnostic A/B/C/E comparison"
        if options.get("allow_diagnostic_sessions")
        else "SANPO continuous Test A/B/C/E comparison",
        "status": "running",
        "configuration": options,
        "dataset": audit,
        "weight_sha256": hashes,
        "limitations": limitations,
        "results": {"predicted": {}},
        "paired": {},
    }
    write_json(output / "metrics.json", report)
    observations = {}
    for method in methods:
        folder = output / "passes" / f"{method}-predicted"
        folder.mkdir(parents=True)
        selected = {**options, "method": method, "pass_output": str(folder)}
        write_json(folder / "options.json", selected)
        print(
            f"Starting {method.upper()}: {audit['continuous_rgb_frames']} continuous frames",
            flush=True,
        )
        with (folder / "worker.log").open("w", encoding="utf-8") as log:
            code = subprocess.run(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    "-m",
                    "model_tests.unified_evaluation.sanpo_comparison",
                    "--worker-options",
                    str(folder / "options.json"),
                ],
                cwd=ROOT,
                env={**os.environ, "YOLO_OFFLINE": "1", "YOLO_AUTOINSTALL": "0"},
                stdout=log,
                stderr=subprocess.STDOUT,
            ).returncode
        path = folder / "result.json"
        result = (
            json.loads(path.read_text(encoding="utf-8"))
            if path.exists()
            else {"status": "failed", "error": "No result; inspect worker.log"}
        )
        if code:
            result.update(status="failed", worker_returncode=code)
        report["results"]["predicted"][method] = result
        if result["status"] == "complete":
            observations[method] = [
                json.loads(s)
                for s in (folder / "observations.jsonl").read_text(encoding="utf-8").splitlines()
            ]
        print(f"Finished {method.upper()}: {result['status']}", flush=True)
        write_reports(output, report)
    paired = paired_scores(observations, methods)
    if paired["status"] == "complete":
        common = set.intersection(
            *[
                {r["key"] for r in rows if r["prediction_m"] is not None}
                for rows in observations.values()
            ]
        )
        paired["reports"] = {
            m: collect_metrics([r for r in rows if r["key"] in common])
            for m, rows in observations.items()
        }
    report["paired"]["predicted"] = paired
    report["weights_unchanged"] = all(digest(options[k]) == v for k, v in hashes.items())
    report["status"] = (
        "complete"
        if report["weights_unchanged"]
        and all(r["status"] == "complete" for r in report["results"]["predicted"].values())
        else "partial_failure"
    )
    write_reports(output, report)
    return report


def main(argv=None):
    """Functionality: Expose continuous SANPO comparison using downloaded data and old E weights.
    Inputs: command arguments. Outputs: process status and new reports.
    Logic: default interval5 avoids manual-label sampling aliasing; allow explicit interval sweeps.
    Constraints: only real predicted detections feed temporal tracking; no synthetic GT sequence.
    """
    parser = argparse.ArgumentParser(description="SANPO continuous Test A/B/C/E comparison")
    parser.add_argument("--worker-options", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--method", choices=["all", "a", "b", "c", "e"], default="all")
    parser.add_argument("--root", default="model_tests/datasets/sanpo")
    parser.add_argument(
        "--processed", default="model_tests/datasets/sanpo/processed-agnostic-manual"
    )
    parser.add_argument(
        "--frames", default="model_tests/datasets/sanpo/manifests/test-frames.jsonl"
    )
    parser.add_argument(
        "--e-checkpoint", default="models/experiments/object-distance-full-01/best.pt"
    )
    parser.add_argument("--detection-weights", default="models/yolo26n.pt")
    parser.add_argument("--detection-manifest", default="models/yolo26n.json")
    parser.add_argument("--depth-weights", default="models/yolo26n-depth.pt")
    parser.add_argument("--depth-image-size", type=int, default=768)
    parser.add_argument("--depth-precision", choices=("fp32", "fp16"), default="fp32")
    parser.add_argument("--distance-config")
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--depth-interval", type=int, default=5)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--match-iou", type=float, default=0.5)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--allow-diagnostic-sessions", action="store_true")
    parser.add_argument("--profile-stages", action="store_true")
    parser.add_argument("--profile-detailed", action="store_true")
    parser.add_argument("--a-fast", action="store_true",
                        help="Exact host ROI partition and direct BGR packing; A 768/FP32 only")
    parser.add_argument("--a-preprocess-fast", action="store_true",
                        help="Pixel-exact detector letterbox without PIL canvas; A 768/FP32 only")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    if args.worker_options:
        return worker(json.loads(args.worker_options.read_text(encoding="utf-8")))
    if args.profile_detailed:
        if args.a_fast:
            parser.error("Use the formal a_performance benchmark for fast A timing")
        if args.method != "a":
            parser.error("Detailed profiling currently requires --method a")
        args.profile_stages = True
    if args.depth_image_size < 32 or args.depth_image_size % 32:
        parser.error("Depth image size must be a positive multiple of 32")
    if args.a_fast and (args.method != "a" or args.depth_image_size != 768
                        or args.depth_precision != "fp32"):
        parser.error("--a-fast requires --method a and depth768/FP32")
    if args.a_preprocess_fast and (args.method != "a" or args.depth_image_size != 768
                                   or args.depth_precision != "fp32"):
        parser.error("--a-preprocess-fast requires --method a and depth768/FP32")
    if args.method != "a" and (args.depth_image_size != 768 or args.depth_precision != "fp32"):
        parser.error("Experimental depth size/precision requires --method a")
    if (
        not args.output
        or args.depth_interval < 1
        or args.warmup < 0
        or args.limit is not None
        and args.limit < 1
        or not 0 < args.match_iou <= 1
    ):
        parser.error(
            "Require new output, positive interval/limit, nonnegative warmup and valid IoU"
        )
    options = {k: v for k, v in vars(args).items() if k != "worker_options"}
    for key in (
        "root",
        "processed",
        "frames",
        "e_checkpoint",
        "detection_weights",
        "detection_manifest",
        "depth_weights",
        "distance_config",
        "output",
    ):
        if options[key] is not None:
            options[key] = str((ROOT / options[key]).resolve())
    return 0 if run(options)["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
