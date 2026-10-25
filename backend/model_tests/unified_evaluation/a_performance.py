"""Responsibilities: Compare baseline and exact fast A on identical continuous SANPO frames.
Implementation: Counterbalanced per-frame AB/BA order, shared models, distance/preprocess axes.
Related Modules: a_formal; sanpo_comparison; kitti.collect_metrics; realtime.depth_fast.
Declaration Index: pair_order; compare_objects; save_tables; run; main.
Variable Index: VARIANTS names baseline/optimized; experiment fixes the comparison axis.
No E/B/C execution or training.
"""

import argparse
import csv
import json
import logging
import platform
import sys
from pathlib import Path

from object_distance.config import digest, write_json

from .a_formal import FormalA
from .adapters import NativeAdapter
from .kitti import collect_metrics
from .performance import latency_summary
from .sanpo import canonical
from .sanpo_comparison import load_sequence, make_frame, read_rgb
from .scoring import score_frame

VARIANTS = ("baseline", "optimized")


def pair_order(round_index, frame_index):
    """Functionality: Balance which variant runs first for every frame across two rounds.
    Inputs: zero-based round/frame. Outputs: ordered variant pair.
    Logic: alternate AB/BA by parity; second round reverses every frame's first round order.
    Constraints: same in-memory RGB and resident model for each pair; no output reuse.
    """
    return VARIANTS if (round_index + frame_index) % 2 == 0 else VARIANTS[::-1]


def compare_objects(reference, candidate):
    """Functionality: Audit every detection and nullable distance without loose tolerances.
    Inputs: ordered canonical detection lists. Outputs: mismatch counts and maximum meter delta.
    Logic: compare all non-distance fields, missing patterns and exact finite distances.
    Constraints: object count/order differences are structural failures, not silently rematched.
    """
    structure = int(len(reference) != len(candidate))
    changed, missing, maximum = 0, 0, 0.0
    for left, right in zip(reference, candidate, strict=False):
        structure += int(
            {k: v for k, v in left.items() if k != "distance_m"}
            != {k: v for k, v in right.items() if k != "distance_m"}
        )
        a, b = left.get("distance_m"), right.get("distance_m")
        changed += int(a != b)
        missing += int((a is None) != (b is None))
        if a is not None and b is not None:
            maximum = max(maximum, abs(a - b))
    return {
        "structure_mismatches": structure,
        "distance_mismatches": changed,
        "missing_pattern_mismatches": missing,
        "max_absolute_distance_delta_m": maximum,
    }


def save_tables(output, report):
    """Functionality: Export mean/P95/FPS, accuracy and counterbalanced stratum summaries.
    Inputs: owned folder and complete report. Outputs: JSON and CSV artifacts.
    Logic: aggregate measured frames; unique GT accuracy comes only from round zero.
    Constraints: CUDA intervals are not added; repeated rounds do not multiply GT denominators.
    """
    write_json(output / "metrics.json", report)
    rows = []
    for variant, result in report["results"].items():
        latency = result["latency"]
        overall = result["scores"]["metrics"]["overall"]
        row = {
            "variant": variant,
            "unique_frames": report["dataset"]["continuous_rgb_frames"],
            "timed_frames": latency["frames"],
            "fps": latency["fps"],
            "gt_objects": overall["ground_truth_objects"],
            "valid_predictions": overall["valid_predictions"],
            "coverage": 1 - overall["missing_prediction_ratio"],
            "mae_m": overall["mae_m"],
            "abs_rel": overall["abs_rel"],
            "depth_calls": result["depth_calls"],
            "depth_ratio": 1.0,
        }
        for field in ("prepare_ms", "roi_fusion_ms", "depth_input_ms", "end_to_end_ms"):
            row[field] = latency[field]["mean"]
            row[field.replace("_ms", "_p95_ms")] = latency[field]["p95"]
        rows.append(row)
    for name, records in (("comparison", rows), ("strata", report["strata"])):
        with (output / f"{name}.csv").open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(records[0]))
            writer.writeheader()
            writer.writerows(records)


def run(options):
    """Functionality: Execute interleaved A variants and audit outputs against prior baseline.
    Inputs: explicit immutable dataset/weight configuration and fresh output directory.
    Outputs: formal comparison and per-frame predictions with timings/order.
    Logic: cold/warm each variant, run balanced repeated full sequences, check all predictions.
    Constraints: no detailed profiler, concurrent GPU jobs, retraining, thresholds or GT changes;
    formal stages use one depth-completion barrier and naturally completed ROI host results.
    """
    import torch
    import ultralytics

    output = Path(options["output"])
    output.mkdir(parents=True, exist_ok=False)
    sources, targets, audit = load_sequence(options)
    hashes = {
        k: digest(options[k]) for k in ("detection_weights", "depth_weights", "detection_manifest")
    }
    reference = {}
    if options.get("reference"):
        reference = {
            r["image_id"]: r["objects"]
            for r in map(
                json.loads, Path(options["reference"]).read_text(encoding="utf-8").splitlines()
            )
        }
        if any(make_frame(source, targets)["frame_id"] not in reference for source in sources):
            raise ValueError("Reference predictions must cover every selected frame")
    write_json(output / "options.json", options)
    adapter = FormalA(NativeAdapter(options), experiment=options.get("experiment", "distance"))
    first = read_rgb(options["root"], sources[0])
    first_frame = make_frame(sources[0], targets)
    for step in range(options["warmup"] + 1):
        for variant in pair_order(step, 0):
            adapter.predict(first, first_frame, fast=variant == "optimized", timestamp=0)
    timings = {v: [] for v in VARIANTS}
    observations = {v: [] for v in VARIANTS}
    checks, strata, first_outputs = [], [], {}
    depth_calls = {v: 0 for v in VARIANTS}
    with (output / "predictions.jsonl").open("w", encoding="utf-8") as stream:
        for round_index in range(options["rounds"]):
            current_session = None
            subsets = {(v, position): [] for v in VARIANTS for position in (0, 1)}
            for index, source in enumerate(sources):
                image, frame = read_rgb(options["root"], source), make_frame(source, targets)
                if source["session_id"] != current_session:
                    adapter.reset()
                    current_session = source["session_id"]
                paired = {}
                for position, variant in enumerate(pair_order(round_index, index)):
                    objects, timing, called = adapter.predict(
                        image,
                        frame,
                        fast=variant == "optimized",
                        timestamp=source["timestamp_nominal_s"],
                    )
                    depth_calls[variant] += int(called)
                    timings[variant].append(timing)
                    subsets[variant, position].append(timing)
                    paired[variant] = objects
                    stream.write(
                        json.dumps(
                            {
                                "round": round_index,
                                "position": position,
                                "variant": variant,
                                "image_id": frame["frame_id"],
                                "objects": objects,
                                "timings": timing,
                            },
                            allow_nan=False,
                        )
                        + "\n"
                    )
                    if round_index == 0 and frame["frame_id"] in targets:
                        scored, _ = score_frame(
                            frame,
                            canonical(objects),
                            bbox_mode="predicted",
                            match_iou=options["match_iou"],
                            depth_frame=True,
                        )
                        observations[variant].extend(scored)
                    if round_index == 0:
                        first_outputs[variant, frame["frame_id"]] = objects
                    else:
                        checks.append(
                            {
                                "kind": "repeat",
                                "round": round_index,
                                "variant": variant,
                                "image_id": frame["frame_id"],
                                **compare_objects(
                                    first_outputs[variant, frame["frame_id"]], objects
                                ),
                            }
                        )
                checks.append(
                    {
                        "kind": "paired",
                        "round": round_index,
                        "image_id": frame["frame_id"],
                        **compare_objects(paired["baseline"], paired["optimized"]),
                    }
                )
                if reference:
                    checks.append(
                        {
                            "kind": "previous_baseline",
                            "round": round_index,
                            "image_id": frame["frame_id"],
                            **compare_objects(reference[frame["frame_id"]], paired["baseline"]),
                        }
                    )
                if index % 50 == 0 or index == len(sources) - 1:
                    stream.flush()
                    print(
                        f"round={round_index + 1}/{options['rounds']} "
                        f"frames={index + 1}/{len(sources)}",
                        flush=True,
                    )
            for (variant, position), rows in subsets.items():
                if not rows:
                    continue
                summary = latency_summary(rows)
                strata.append(
                    {
                        "round": round_index,
                        "variant": variant,
                        "position": position,
                        "frames": len(rows),
                        "fps": summary["fps"],
                        **{
                            key: summary[key]["mean"]
                            for key in (
                                "prepare_ms",
                                "roi_fusion_ms",
                                "depth_input_ms",
                                "end_to_end_ms",
                            )
                        },
                        "end_to_end_p95_ms": summary["end_to_end_ms"]["p95"],
                    }
                )
    write_json(output / "prediction_checks.json", checks)
    for variant, rows in observations.items():
        with (output / f"{variant}-observations.jsonl").open("w", encoding="utf-8") as stream:
            stream.writelines(json.dumps(row, allow_nan=False) + "\n" for row in rows)
    consistent = all(
        not row[key]
        for row in checks
        for key in ("structure_mismatches", "distance_mismatches", "missing_pattern_mismatches")
    )
    unchanged = all(digest(options[k]) == value for k, value in hashes.items())
    report = {
        "status": "complete" if consistent and unchanged else "completed_with_mismatches",
        "configuration": options,
        "variants": {"baseline": "A + a-fast", "optimized": "A + a-fast + a-preprocess-fast"}
        if options.get("experiment") == "preprocess"
        else {"baseline": "original A", "optimized": "A + a-fast"},
        "dataset": audit,
        "weight_sha256": hashes,
        "weights_unchanged": unchanged,
        "all_predictions_exactly_equal": consistent,
        "max_distance_delta_m": max(row["max_absolute_distance_delta_m"] for row in checks),
        "runtime": {
            "python": sys.version,
            "platform": platform.platform(),
            "torch": torch.__version__,
            "ultralytics": ultralytics.__version__,
            "cuda": torch.version.cuda,
            "gpu": torch.cuda.get_device_name(0),
        },
        "identity": adapter.adapter.identity,
        "strata": strata,
        "results": {
            v: {
                "latency": latency_summary(timings[v]),
                "scores": collect_metrics(observations[v]),
                "depth_calls": depth_calls[v],
            }
            for v in VARIANTS
        },
        "limitations": [
            "Mixed original SANPO splits: diagnostic runtime/consistency comparison, "
            "not held-out model selection.",
            "One shared resident YOLO/Depth pair; "
            "both variants perform complete inference per frame.",
            "AB/BA alternates by frame and reverses per round; "
            "read/decode and JSON/metric I/O excluded.",
            "Detailed profiling disabled: no per-ROI events/barriers or forward hooks. "
            "Coarse CPU timers "
            "and one depth-completion barrier attribute stages identically for both variants.",
            "Input time includes direct packing plus official setup_source for both variants; "
            "ROI time includes depth download, valid filtering, median and output packaging.",
            "Repeated frames measure performance variability; "
            "accuracy denominators count unique first-round GT only.",
        ],
    }
    save_tables(output, report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "equal": consistent,
                "fps": {v: report["results"][v]["latency"]["fps"] for v in VARIANTS},
            }
        ),
        flush=True,
    )
    return report


def main(argv=None):
    """Functionality: Run reproducible full-sequence A optimization comparisons.
    Inputs: optional dataset/config/round overrides and required new output path.
    Outputs: command status and independent artifacts. Logic: validate then reuse SANPO scorer.
    Constraints: fixed A/FP32/768, no detailed profile option; never overwrite prior outputs.
    """
    parser = argparse.ArgumentParser(description="Counterbalanced exact A performance comparison")
    parser.add_argument(
        "--experiment",
        choices=("distance", "preprocess"),
        default="distance",
        help="Preprocess holds a-fast on in both variants; default preserves round 1",
    )
    parser.add_argument("--root", default="model_tests/datasets/sanpo")
    parser.add_argument(
        "--processed", default="model_tests/datasets/sanpo/processed-continuous-diagnostic-01"
    )
    parser.add_argument(
        "--frames",
        default="model_tests/datasets/sanpo/reports/continuous-diagnostic-01/frames.jsonl",
    )
    parser.add_argument("--detection-weights", default="models/yolo26n.pt")
    parser.add_argument("--detection-manifest", default="models/yolo26n.json")
    parser.add_argument("--depth-weights", default="models/yolo26n-depth.pt")
    parser.add_argument(
        "--reference", help="Optional previous A predictions.jsonl for exact regression audit"
    )
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--output", required=True)
    options = vars(parser.parse_args(argv))
    if (
        options["rounds"] < 1
        or options["warmup"] < 1
        or (options["limit"] is not None and options["limit"] < 1)
    ):
        parser.error("Require positive rounds/warmup/limit")
    options.update(
        method="a",
        device="cuda:0",
        depth_image_size=768,
        depth_precision="fp32",
        a_fast=options["experiment"] == "preprocess",
        a_preprocess_fast=False,
        depth_interval=5,
        match_iou=0.5,
        allow_diagnostic_sessions=True,
        profile_stages=False,
        profile_detailed=False,
        distance_config=None,
    )
    logging.basicConfig(level=logging.INFO)
    return 0 if run(options)["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
