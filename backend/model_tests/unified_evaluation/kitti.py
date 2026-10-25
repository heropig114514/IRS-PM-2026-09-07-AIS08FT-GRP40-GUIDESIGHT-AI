"""Responsibilities: Compare existing A/B/C/E on the original KITTI validation split.
Implementation: Serial isolated workers, shared GT/matching, four-band weighted reports.
Related Modules: adapters/scoring/performance; object_distance.data/metrics/reporting.
Declaration Index: load_dataset; make_frame; collect_metrics; read_rgb; evaluate_pass;
worker; table_row; write_tables; run; main.
Variable Index: LIMITATIONS records target/runtime differences and independent-image C semantics.
"""

import argparse
import csv
import json
import logging
import math
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

from PIL import Image

from object_distance.config import ROOT, TARGET, digest, write_json
from object_distance.data import KittiObjects
from object_distance.metrics import DistanceMetrics
from object_distance.reporting import distance_report, write_distance_report

from .performance import latency_summary, synchronize
from .scoring import paired_scores, score_frame

LIMITATIONS = [
    "Original KITTI validation selected E's best checkpoint; this is in-domain validation, "
    "not an independent final test.",
    "All methods use KITTI reference camera-z labels. A/C predict bbox depth medians; "
    "B predicts assumed-height geometry; no test-label calibration is performed.",
    "KITTI object images have no verified temporal stream. C resets before EVERY image and "
    "runs depth each time: independent-image control, NOT low-frequency temporal validation.",
    "A/B/C retain fused FP32 CUDA Graph; E retains eager checkpoint precision. Native scalar "
    "bbox inversion and E rounded x/y inversion can yield different detections.",
    "Errors are conditional on valid predictions; coverage includes all accepted GT objects. "
    "Paired tables additionally restrict errors to identical valid objects across methods.",
    "Timing includes state reset and in-memory RGB preprocessing through canonical outputs; "
    "excludes image decode, metrics, file output, rendering and navigation. It differs from "
    "object_distance.evaluate timing, which includes image decode.",
]


def load_dataset(options):
    """Functionality: Load unchanged KITTI validation and verify checkpoint selection provenance.
    Inputs: processed directory, trained checkpoint and optional smoke limit. Outputs: dataset.
    Logic: existing target/hash validation plus original training-run validation fingerprint.
    Constraints: no repartitioning, relabeling, difficulty filters or test-set fitting.
    """
    dataset = KittiObjects(options["processed"], "val", limit=options.get("limit"))
    run_path = Path(options["e_checkpoint"]).parent / "run.json"
    training = json.loads(run_path.read_text(encoding="utf-8"))
    if (
        training["distance_target"] != TARGET
        or training["dataset"]["val_manifest_sha256"] != dataset.fingerprint
    ):
        raise ValueError("Require the original KITTI validation used by the E training run")
    if not dataset.records:
        raise ValueError("Empty validation split")
    return dataset


def make_frame(record):
    """Functionality: Adapt existing KITTI boxes/z and per-image P2 to the common adapter API.
    Inputs: immutable KITTI record. Outputs: frame with canonical GT and original pixel intrinsics.
    Logic: use P2's left 3x3 matrix; B reads fy, never object GT heights or test-derived scale.
    Constraints: P2 translation does not modify the original KITTI reference-z target.
    """
    p2 = record["p2"]
    if len(p2) != 12 or not all(math.isfinite(v) for v in p2) or min(p2[0], p2[5]) <= 0:
        raise ValueError("Invalid KITTI P2 calibration")
    return {
        "frame_id": record["image_id"],
        "camera_intrinsic": [[p2[0], p2[1], p2[2]], [p2[4], p2[5], p2[6]], p2[8:11]],
        "objects": [
            {**obj, "annotation_id": str(index), "distance_gt_m": obj["distance_m"]}
            for index, obj in enumerate(record["objects"])
        ],
    }


def collect_metrics(observations):
    """Functionality: Reuse the exact E arithmetic for overall and weighted four-band errors.
    Inputs: canonical scored GT observations. Outputs: original metrics and distance report.
    Logic: retain nullable predictions in their original denominator.
    Constraints: empty bands make weighted score unavailable, not implicitly zero.
    """
    metrics = DistanceMetrics()
    for row in observations:
        metrics.add(row["target_m"], row["prediction_m"], row["class_name"])
    return {"metrics": metrics.summary(), "distance_report": distance_report(metrics)}


def read_rgb(dataset, record):
    """Functionality: Decode validated original RGB outside inference timing.
    Inputs: dataset and record. Outputs: detached PIL image.
    Logic: check dimensions/mode before copying. Constraints: no additional JPEG or resizing.
    """
    with Image.open(dataset.root / record["image_path"]) as image:
        if list(image.size) != record["image_size"] or image.mode != "RGB":
            raise ValueError("KITTI image changed since preparation")
        return image.copy()


def evaluate_pass(adapter, dataset, output, options):
    """Functionality: Evaluate one existing method with independent-image lifecycle boundaries.
    Inputs: resident adapter, verified dataset, owned output, pass options. Outputs: result.
    Logic: separate cold/warm calls, reset every scored image, retain every GT and prediction.
    Constraints: no temporal links invented from KITTI filenames; no inference algorithm changes.
    """
    first = dataset.records[0]
    image, frame = read_rgb(dataset, first), make_frame(first)
    cold = None
    for index in range(options["warmup"] + 1):
        adapter.reset()
        _, timing, _ = adapter.predict(image, frame, bbox_mode=options["bbox_mode"], timestamp=0)
        if index == 0:
            cold = timing
    rows, observations, flags = [], [], []
    with (
        (output / "predictions.jsonl").open("w", encoding="utf-8") as predictions,
        (output / "observations.jsonl").open("w", encoding="utf-8") as observed,
    ):
        for index, record in enumerate(dataset.records):
            image, frame = read_rgb(dataset, record), make_frame(record)
            synchronize(options["device"])
            started = time.perf_counter()
            adapter.reset()
            reset_ms = (time.perf_counter() - started) * 1000
            objects, timing, depth = adapter.predict(
                image, frame, bbox_mode=options["bbox_mode"], timestamp=0
            )
            if options["method"] in {"a", "c"} and not depth:
                raise ValueError("Independent-image A/C must run depth for every scored frame")
            timing = {**timing, "state_reset_ms": reset_ms}
            timing["end_to_end_ms"] += reset_ms
            scored, _ = score_frame(
                frame,
                objects,
                bbox_mode=options["bbox_mode"],
                match_iou=options["match_iou"],
                depth_frame=depth,
            )
            observations.extend(scored)
            rows.append(timing)
            flags.append(bool(depth))
            predictions.write(
                json.dumps(
                    {
                        "image_id": record["image_id"],
                        "distance_target": TARGET,
                        "objects": objects,
                        "timings": timing,
                        "depth_frame": bool(depth),
                    },
                    allow_nan=False,
                )
                + "\n"
            )
            observed.writelines(json.dumps(row, allow_nan=False) + "\n" for row in scored)
            predictions.flush()
            observed.flush()
            if index % 100 == 0:
                print(options["method"], options["bbox_mode"], index + 1, len(dataset), flush=True)
    report = {
        "status": "complete",
        "method": options["method"],
        "bbox_mode": options["bbox_mode"],
        "split": "val",
        "distance_target": TARGET,
        "split_manifest_sha256": dataset.fingerprint,
        "evaluated_images": len(dataset),
        "image_limit": options.get("limit"),
        "identity": adapter.identity,
        **collect_metrics(observations),
        "latency": latency_summary(rows),
        "cold_first_call": cold,
        "warmup_calls": options["warmup"],
        "temporal": {
            "scope": "independent_images_reset_every_frame",
            "low_frequency_evaluation_available": False,
            "configured_depth_interval": options["depth_interval"],
            "depth_calls": sum(flags),
            "total_frames": len(rows),
            "actual_depth_ratio": sum(flags) / len(rows),
            "depth_frame_latency": latency_summary(
                [t for t, f in zip(rows, flags, strict=True) if f]
            ),
            "non_depth_frame_latency": latency_summary(
                [t for t, f in zip(rows, flags, strict=True) if not f]
            ),
        },
        "limitations": LIMITATIONS,
    }
    write_distance_report(output, options["bbox_mode"], report)
    return report


def worker(options):
    """Functionality: Run one isolated GPU pass and persist explicit failures.
    Inputs: resolved worker options. Outputs: process exit status and result.json.
    Logic: reuse unchanged model adapters; save traceback on failure without fallback or retry.
    Constraints: only one method/bbox pass occupies the GPU in this process.
    """
    from .adapters import create_adapter

    output = Path(options["pass_output"])
    report = {"status": "failed", "method": options["method"], "bbox_mode": options["bbox_mode"]}
    try:
        dataset = load_dataset(options)
        adapter = create_adapter(options)
        if options["method"] == "e" and adapter.model.config.distance_target != TARGET:
            raise ValueError("E requires the original KITTI checkpoint")
        report = evaluate_pass(adapter, dataset, output, options)
    except Exception as error:
        report["error"] = str(error)
        (output / "failure.txt").write_text(traceback.format_exc(), encoding="utf-8")
        logging.exception("KITTI pass failed")
    write_json(output / "result.json", report)
    return 0 if report["status"] == "complete" else 1


def table_row(method, mode, report, *, scope="all_gt"):
    """Functionality: Flatten complete or failed passes into comparison table rows.
    Inputs: method/mode/result/scope. Outputs: consistent scalar columns with missing blanks.
    Logic: overall errors, weighted score, coverage and measured stages.
    Constraints: failures retain visible rows; absent measurements never become zero.
    """
    overall = report.get("metrics", {}).get("overall", {})
    valid, total = overall.get("valid_predictions"), overall.get("ground_truth_objects")
    latency = report.get("latency", {})
    return {
        "method": method.upper(),
        "bbox_mode": mode,
        "scope": scope,
        "status": report.get("status", "complete"),
        "gt_objects": total,
        "matched_objects": report.get("matched_objects"),
        "valid_predictions": valid,
        "coverage": valid / total if total else None,
        "mae_m": overall.get("mae_m"),
        "abs_rel": overall.get("abs_rel"),
        "weighted_mae_m": report.get("distance_report", {}).get("weighted_mae_m"),
        "detection_ms": latency.get("detection_ms", {}).get("mean"),
        "distance_ms": latency.get("distance_ms", {}).get("mean"),
        "end_to_end_ms": latency.get("end_to_end_ms", {}).get("mean"),
        "fps": latency.get("fps"),
        "depth_calls": report.get("temporal", {}).get("depth_calls"),
        "depth_ratio": report.get("temporal", {}).get("actual_depth_ratio"),
        "error": report.get("error", ""),
    }


def write_tables(output, report):
    """Functionality: Publish machine-readable tables and a human-readable combined report.
    Inputs: new output and aggregate result. Outputs: CSV tables, comparison.md and metrics.json.
    Logic: retain all-GT and common-valid scopes separately, include four-band counts/errors.
    Constraints: partial runs remain explicitly partial; limitations accompany every report.
    """
    rows, bands, paired = [], [], []
    for mode, methods in report["results"].items():
        for method, result in methods.items():
            rows.append(table_row(method, mode, result))
            for band in result.get("distance_report", {}).get("bands", []):
                bands.append({"method": method.upper(), "bbox_mode": mode, **band})
        for method, result in report.get("paired", {}).get(mode, {}).get("reports", {}).items():
            paired.append(table_row(method, mode, result, scope="common_valid"))
    for name, records in (
        ("comparison", rows),
        ("distance_bands", bands),
        ("paired_comparison", paired),
    ):
        if records:
            with (output / f"{name}.csv").open("w", newline="", encoding="utf-8-sig") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(records[0]))
                writer.writeheader()
                writer.writerows(records)
    lines = [
        "# " + report.get("title", "KITTI A/B/C/E validation comparison"),
        "", f"Status: {report['status']}", "",
    ]
    lines.extend(f"- {text}" for text in report.get("limitations", LIMITATIONS))
    lines.extend(
        [
            "",
            "## Overall results",
            "",
            "| Method | Boxes | Status | MAE (m) | AbsRel | Weighted MAE (m) | "
            "Coverage | ms/frame | FPS |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in rows:
        values = [
            row[k]
            for k in (
                "method",
                "bbox_mode",
                "status",
                "mae_m",
                "abs_rel",
                "weighted_mae_m",
                "coverage",
                "end_to_end_ms",
                "fps",
            )
        ]
        lines.append(
            "| "
            + " | ".join(
                "N/A" if v is None else f"{v:.6f}" if isinstance(v, float) else str(v)
                for v in values
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Distance bands",
            "",
            "Weights: [0,10): 0.4; [10,20): 0.3; [20,40): 0.2; [40,inf): 0.1.",
            "",
            "| Method | Boxes | Range (m) | GT | Valid | MAE (m) | AbsRel | Coverage |",
            "|---|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for band in bands:
        upper = band["upper_m"] if band["upper_m"] is not None else "inf"
        values = [
            band["method"],
            band["bbox_mode"],
            f"[{band['lower_m']},{upper})",
            band["ground_truth_objects"],
            band["valid_predictions"],
            band["mae_m"],
            band["abs_rel"],
            band["coverage"],
        ]
        lines.append(
            "| "
            + " | ".join(
                "N/A" if v is None else f"{v:.6f}" if isinstance(v, float) else str(v)
                for v in values
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "Coverage and AbsRel are fractions (multiply by 100 for percent).",
            "See paired_comparison.csv for common-valid-object errors; missing bands yield N/A.",
        ]
    )
    (output / "comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_json(output / "metrics.json", report)


def run(options, *, launch=None):
    """Functionality: Execute selected passes serially and aggregate comparable KITTI reports.
    Inputs: CLI options and optional test launcher. Outputs: aggregate result and owned artifacts.
    Logic: fresh process per pass avoids model-memory overlap and mutable thread/runtime state.
    Constraints: preserve existing outputs, checkpoint bytes, original split and core algorithms.
    """
    dataset = load_dataset(options)
    output = Path(options["output"])
    output.mkdir(parents=True, exist_ok=False)
    methods = list("abce") if options["method"] == "all" else [options["method"]]
    modes = ["gt", "predicted"] if options["bbox_mode"] == "both" else [options["bbox_mode"]]
    fingerprints = {k: digest(options[k]) for k in ("e_checkpoint", "detection_weights")}
    if any(m in methods for m in ("a", "c")):
        fingerprints["depth_weights"] = digest(options["depth_weights"])
    report = {
        "status": "running",
        "configuration": options,
        "weight_sha256": fingerprints,
        "split_manifest_sha256": dataset.fingerprint,
        "selected_images": len(dataset),
        "selected_objects": sum(len(r["objects"]) for r in dataset.records),
        "selected_image_ids": [r["image_id"] for r in dataset.records],
        "limitations": LIMITATIONS,
        "results": {mode: {} for mode in modes},
        "paired": {},
    }
    write_json(output / "metrics.json", report)
    for mode in modes:
        observed = {}
        for method in methods:
            folder = output / "passes" / f"{method}-{mode}"
            folder.mkdir(parents=True)
            selected = {**options, "method": method, "bbox_mode": mode, "pass_output": str(folder)}
            write_json(folder / "options.json", selected)
            print(f"Starting {method.upper()} / {mode}: {len(dataset)} images", flush=True)
            if launch is None:
                with (folder / "worker.log").open("w", encoding="utf-8") as log:
                    code = subprocess.run(
                        [
                            sys.executable,
                            "-X",
                            "utf8",
                            "-m",
                            "model_tests.unified_evaluation.kitti",
                            "--worker-options",
                            str(folder / "options.json"),
                        ],
                        cwd=ROOT,
                        env={**os.environ, "YOLO_OFFLINE": "1", "YOLO_AUTOINSTALL": "0"},
                        stdout=log,
                        stderr=subprocess.STDOUT,
                    ).returncode
            else:
                code = launch(selected)
            path = folder / "result.json"
            result = (
                json.loads(path.read_text(encoding="utf-8"))
                if path.exists()
                else {
                    "status": "failed",
                    "error": "No result; inspect worker.log",
                }
            )
            if code:
                result.update(status="failed", worker_returncode=code)
            report["results"][mode][method] = result
            if result["status"] == "complete":
                observed[method] = [
                    json.loads(s)
                    for s in (folder / "observations.jsonl")
                    .read_text(encoding="utf-8")
                    .splitlines()
                ]
            print(f"Finished {method.upper()} / {mode}: {result['status']}", flush=True)
            write_tables(output, report)
        paired = paired_scores(observed, methods)
        if paired["status"] == "complete":
            common = set.intersection(
                *[
                    {r["key"] for r in values if r["prediction_m"] is not None}
                    for values in observed.values()
                ]
            )
            paired["reports"] = {
                method: collect_metrics([r for r in values if r["key"] in common])
                for method, values in observed.items()
            }
        report["paired"][mode] = paired
    report["weights_unchanged"] = all(digest(options[k]) == v for k, v in fingerprints.items())
    report["status"] = (
        "complete"
        if report["weights_unchanged"]
        and all(
            r["status"] == "complete"
            for methods in report["results"].values()
            for r in methods.values()
        )
        else "partial_failure"
    )
    write_tables(output, report)
    return report


def main(argv=None):
    """Functionality: Expose one command for four-method KITTI validation or explicit smoke runs.
    Inputs: CLI paths/methods/box modes and optional positive limit. Outputs: process exit status.
    Logic: defaults bind old KITTI E checkpoint and original 80/20 validation.
    Constraints: no training; C's configured interval cannot create missing temporal provenance.
    """
    parser = argparse.ArgumentParser(description="KITTI A/B/C/E independent-image validation")
    parser.add_argument("--worker-options", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--method", choices=["all", "a", "b", "c", "e"], default="all")
    parser.add_argument("--bbox-mode", choices=["both", "gt", "predicted"], default="both")
    parser.add_argument("--processed", default="model_tests/datasets/kitti/processed-80-20-seed42")
    parser.add_argument(
        "--e-checkpoint", default="models/experiments/object-distance-full-01/best.pt"
    )
    parser.add_argument("--detection-weights", default="models/yolo26n.pt")
    parser.add_argument("--detection-manifest", default="models/yolo26n.json")
    parser.add_argument("--depth-weights", default="models/yolo26n-depth.pt")
    parser.add_argument("--distance-config")
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--depth-interval", type=int, default=3)
    parser.add_argument("--match-iou", type=float, default=0.5)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    if args.worker_options:
        return worker(json.loads(args.worker_options.read_text(encoding="utf-8")))
    if (
        args.output is None
        or args.warmup < 0
        or args.depth_interval < 1
        or args.limit is not None
        and args.limit < 1
        or not 0 < args.match_iou <= 1
    ):
        parser.error(
            "Require new --output, positive interval/limit, nonnegative warmup and valid IoU"
        )
    options = {k: v for k, v in vars(args).items() if k != "worker_options"}
    for key in (
        "output",
        "processed",
        "e_checkpoint",
        "detection_weights",
        "detection_manifest",
        "depth_weights",
        "distance_config",
    ):
        if options[key] is not None:
            options[key] = str((ROOT / options[key]).resolve())
    return 0 if run(options)["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
