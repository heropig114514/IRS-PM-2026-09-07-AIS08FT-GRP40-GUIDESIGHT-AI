"""Responsibilities: Publish comparable CSV tables and a human-readable protocol report.
Implementation: Explicit status/null cells; separate GT/predicted and per-bin/per-class results.
Related Modules: unified_evaluation.runner/scoring; generated reports stay Git-ignored.
Declaration Index: cell; write_csv; write_reports.
Variable Index: None.
"""

import csv


def cell(value):
    """Functionality: Render missing numeric results honestly in CSV and Markdown.
    Inputs: scalar or None. Outputs: printable value. Logic: None becomes literal null.
    Constraints: empty samples and failed methods are never formatted as zero error.
    """
    return "null" if value is None else value


def write_csv(path, fields, rows):
    """Functionality: Save an independently usable UTF8 experiment table.
    Inputs: output path,ordered fields,dict rows. Outputs: CSV file.
    Logic: standard quoting and explicit nulls. Constraints: no per-object dataset content here.
    """
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: cell(row.get(key)) for key in fields})


def write_reports(output, metrics, manifest):
    """Functionality: Export overall,GT-bin,class,latency,resource and paired comparison tables.
    Inputs: owned output,aggregated results/run manifest. Outputs: CSVs and Markdown report.
    Logic: retain failed rows and report diagnostic comparison limits beside conditional MAE.
    Constraints: no best-method ranking or claim that low coverage improves distance quality.
    """
    comparison, bins, classes, latencies, resources = [], [], [], [], []
    lines = [
        "# External nuScenes distance comparison",
        "",
        f"Run status: **{metrics['status']}**. Strict controlled comparison: **False**.",
        "",
        f"Selected frames: {manifest['selected_frames']}; "
        f"GT objects: {manifest['selected_objects']}.",
        "",
        "Reference: nuScenes3D box center camera-forward z in meters. "
        "All errors below are conditional on valid estimates; inspect coverage together.",
        "",
        "| BBox mode | Method | Status | MAE m | RMSE m | AbsRel | Coverage | "
        "P50 ms | P95 ms | FPS |",
        "",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for mode, results in metrics["results"].items():
        for method, result in results.items():
            row = {"bbox_mode": mode, "method": method.upper(), "status": result["status"]}
            if result["status"] == "complete":
                overall = result["metrics"]["overall"]
                steady = result["performance"]["steady"]
                row.update(
                    {
                        key: overall[key]
                        for key in (
                            "mae_m",
                            "rmse_m",
                            "abs_rel",
                            "coverage",
                            "ground_truth_objects",
                            "valid_predictions",
                            "matched_objects",
                            "detection_match_rate",
                            "distance_validity_rate",
                        )
                    }
                )
                row.update(
                    p50_latency_ms=steady["end_to_end_ms"]["p50"],
                    p95_latency_ms=steady["end_to_end_ms"]["p95"],
                    fps=steady["fps"],
                )
                for group, scores in result["metrics"].items():
                    target = bins if group.startswith("range:") else classes
                    if group != "overall":
                        target.append(
                            {"bbox_mode": mode, "method": method.upper(), "group": group, **scores}
                        )
                for stage, values in steady.items():
                    if stage.endswith("_ms"):
                        latencies.append(
                            {
                                "bbox_mode": mode,
                                "method": method.upper(),
                                "phase": "steady",
                                "stage": stage,
                                **values,
                            }
                        )
                for phase in ("depth_frame_latency", "non_depth_frame_latency"):
                    temporal = result["temporal"].get(phase)
                    if temporal:
                        for stage, values in temporal.items():
                            if stage.endswith("_ms"):
                                latencies.append(
                                    {
                                        "bbox_mode": mode,
                                        "method": method.upper(),
                                        "phase": phase,
                                        "stage": stage,
                                        **values,
                                    }
                                )
                resources.append(
                    {
                        "bbox_mode": mode,
                        "method": method.upper(),
                        **result["memory"],
                        "peak_gpu_allocated_bytes": result["performance"][
                            "peak_gpu_allocated_bytes"
                        ],
                        "peak_gpu_reserved_bytes": result["performance"]["peak_gpu_reserved_bytes"],
                        "load_cold_warm_peak_gpu_allocated_bytes": result["performance"].get(
                            "load_cold_warm_peak_gpu_allocated_bytes"
                        ),
                        "depth_calls": result["temporal"]["depth_calls"],
                        "total_frames": result["frames"],
                        "actual_depth_ratio": result["temporal"]["actual_depth_ratio"],
                    }
                )
            comparison.append(row)
            lines.append(
                "| "
                + " | ".join(
                    str(cell(row.get(key)))
                    for key in (
                        "bbox_mode",
                        "method",
                        "status",
                        "mae_m",
                        "rmse_m",
                        "abs_rel",
                        "coverage",
                        "p50_latency_ms",
                        "p95_latency_ms",
                        "fps",
                    )
                )
                + " |"
            )
            if result["status"] != "complete":
                lines.extend(
                    [
                        "",
                        f"Failure {method.upper()}/{mode}: {result.get('error')}. "
                        f"See passes/{method}-{mode}/worker.log and failure.txt.",
                        "",
                    ]
                )
    fields = [
        "bbox_mode",
        "method",
        "status",
        "mae_m",
        "rmse_m",
        "abs_rel",
        "coverage",
        "p50_latency_ms",
        "p95_latency_ms",
        "fps",
        "ground_truth_objects",
        "valid_predictions",
        "matched_objects",
        "detection_match_rate",
        "distance_validity_rate",
    ]
    write_csv(output / "comparison.csv", fields, comparison)
    group_fields = [
        "bbox_mode",
        "method",
        "group",
        "mae_m",
        "rmse_m",
        "abs_rel",
        "ground_truth_objects",
        "valid_predictions",
        "missing_predictions",
        "missing_prediction_ratio",
        "matched_objects",
        "detection_match_rate",
        "distance_validity_rate",
        "coverage",
    ]
    write_csv(output / "distance_bins.csv", group_fields, bins)
    write_csv(output / "classes.csv", group_fields, classes)
    write_csv(
        output / "latency.csv",
        ["bbox_mode", "method", "phase", "stage", "count", "mean", "p50", "p95"],
        latencies,
    )
    write_csv(
        output / "resources.csv",
        [
            "bbox_mode",
            "method",
            "peak_gpu_allocated_bytes",
            "peak_gpu_reserved_bytes",
            "load_cold_warm_peak_gpu_allocated_bytes",
            "process_rss_baseline_bytes",
            "peak_process_rss_bytes",
            "os_process_lifetime_peak_rss_bytes",
            "rss_sampling_interval_ms",
            "depth_calls",
            "total_frames",
            "actual_depth_ratio",
        ],
        resources,
    )
    paired_rows = []
    lines.extend(["", "## Paired valid targets", ""])
    for mode, paired in metrics["paired"].items():
        lines.append(
            f"{mode}: status={paired['status']}, "
            f"common valid targets={paired['common_valid_objects']}."
        )
        for method, values in (paired.get("scores") or {}).items():
            paired_rows.append({"bbox_mode": mode, "method": method.upper(), **values["overall"]})
    write_csv(
        output / "paired_comparison.csv",
        ["bbox_mode", "method", "mae_m", "rmse_m", "abs_rel", "ground_truth_objects"],
        paired_rows,
    )
    lines.extend(
        [
            "",
            "## Protocol and limits",
            "",
            "Bins: [0,1),[1,3),[3,5),[5,10),[10,20),[20,infinity) meters, based on GT. "
            "Empty error groups are null. Coverage=valid/GT; detection match rate=matched/GT; "
            "distance validity=valid/matched. GT mode match rate is1 by construction.",
            "",
            "C uses actual labeled keyframe timestamps (approximately2Hz), not runtime FPS. "
            "Non-depth MAE refers to skipped labeled keyframes, never unlabelled sweeps.",
            "",
            "Depth call counters exclude model load, cold probe and warmups. "
            "Every scene/camera starts with depth, so short-run ratio can exceed1/interval.",
            "",
            "Cold/warmup details are in metrics.json. Steady GPU allocation peak includes "
            "resident model tensors; reserved memory is allocator storage, not GPU-wide usage. "
            "Process RSS is sampled at10ms; OS lifetime peak is separate.",
            "",
        ]
    )
    conditions = next(iter(manifest["conditions"].values()))
    lines.extend(f"- {limitation}" for limitation in conditions["limitations"])
    lines.extend(
        [
            "",
            "Source and weight preservation checks: "
            f"sources={manifest['preserved_sources_unchanged']}, "
            f"weights={manifest['preserved_weights_unchanged']}.",
            "",
        ]
    )
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")
