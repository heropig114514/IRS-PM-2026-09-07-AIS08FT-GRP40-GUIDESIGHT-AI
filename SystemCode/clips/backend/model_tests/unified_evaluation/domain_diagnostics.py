"""Responsibilities: Audit KITTI/nuScenes camera scales, target distributions and signed errors.
Implementation: Read-only manifest/raw checks, identical-checkpoint comparisons and descriptive
focal-ratio sensitivity without fitting labels or replacing original predictions/metrics.
Related Modules: object_distance.prepare/evaluate/model; unified_evaluation.dataset/diagnostics.
Declaration Index: read_lines; numeric_summary; distance_bin; camera_parameters; target_record;
kitti_records; nuscenes_records; join_kitti; join_nuscenes; error_summary; aggregate;
standardize_composition; box_effects; export_csv; render; analyze; main.
Variable Index: EDGES/LABELS — fixed descriptive half-open meter bins; logger — audit progress.
"""

import argparse
import csv
import json
import logging
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

from object_distance.config import ROOT, digest, write_json
from object_distance.metrics import match_objects
from object_distance.prepare import parse_label, read_calibration

from .diagnostics import snapshot
from .runner import source_snapshot

EDGES = (0, 5, 10, 20, 30, 40, 60, 80, 100, float("inf"))
LABELS = ("0-5", "5-10", "10-20", "20-30", "30-40", "40-60", "60-80", "80-100", "100+")
logger = logging.getLogger(__name__)


def read_lines(path):
    """Functionality: Read complete nonempty JSONL records.
    Inputs: existing path. Outputs: list of dictionaries. Logic: strict JSON decoding.
    Constraints: no recovery from truncated or malformed experiment artifacts.
    """
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]


def numeric_summary(values):
    """Functionality: Describe finite numeric observations with explicit missing counts.
    Inputs: nullable scalar iterable. Outputs: count/missing/mean/quantiles/extrema.
    Logic: NumPy percentiles on valid values. Constraints: empty summaries contain nulls, not zeros.
    """
    values = list(values)
    valid = [float(v) for v in values if v is not None and math.isfinite(v)]
    result = {"count": len(valid), "missing": len(values) - len(valid)}
    result.update(
        {
            k: float(np.percentile(valid, q)) if valid else None
            for k, q in [
                ("min", 0),
                ("p10", 10),
                ("median", 50),
                ("p90", 90),
                ("p99", 99),
                ("max", 100),
            ]
        }
    )
    result["mean"] = float(np.mean(valid)) if valid else None
    return result


def distance_bin(value):
    """Functionality: Assign one fixed diagnostic GT-z interval.
    Inputs: finite nonnegative camera z. Outputs: descriptive label.
    Logic: half-open boundaries. Constraints: these bins do not change any evaluator's metrics.
    """
    if not math.isfinite(value) or value < 0:
        raise ValueError("Expected finite nonnegative target")
    return next(
        label for a, b, label in zip(EDGES[:-1], EDGES[1:], LABELS, strict=True) if a <= value < b
    )


def camera_parameters(frame, dataset, split, edge=640):
    """Functionality: Express original camera intrinsics in the exact rounded letterbox canvas.
    Inputs: frame, dataset/split labels and edge. Outputs: original/input K, dimensions and FOV.
    Logic: K_input=A@K with distinct rounded x/y scales and integer padding.
    Constraints: P2 fourth column stays a separate reference-camera translation, not a focal term.
    """
    width, height = frame["image_size"]
    resized = [round(v * edge / max(width, height)) for v in (width, height)]
    sx, sy = resized[0] / width, resized[1] / height
    left, top = (edge - resized[0]) // 2, (edge - resized[1]) // 2
    if dataset == "kitti":
        p = np.asarray(frame["p2"], dtype=float).reshape(3, 4)
        k, offset = p[:, :3], np.linalg.solve(p[:, :3], p[:, 3])
    else:
        k, offset = np.asarray(frame["camera_intrinsic"], dtype=float), np.zeros(3)
    if k.shape != (3, 3) or not np.isfinite(k).all() or min(k[0, 0], k[1, 1]) <= 0:
        raise ValueError("Invalid camera intrinsic")
    affine = np.array([[sx, 0, left], [0, sy, top], [0, 0, 1]])
    transformed = affine @ k
    return {
        "dataset": dataset,
        "split": split,
        "frame_id": frame.get("image_id", frame.get("frame_id")),
        "scene_id": frame.get("scene_id", "unavailable"),
        "camera_id": json.dumps(k.tolist(), separators=(",", ":")),
        "width": width,
        "height": height,
        "resized_width": resized[0],
        "resized_height": resized[1],
        "scale_x": sx,
        "scale_y": sy,
        "left": left,
        "top": top,
        "fx": float(k[0, 0]),
        "fy": float(k[1, 1]),
        "cx": float(k[0, 2]),
        "cy": float(k[1, 2]),
        "input_fx": float(transformed[0, 0]),
        "input_fy": float(transformed[1, 1]),
        "input_cx": float(transformed[0, 2]),
        "input_cy": float(transformed[1, 2]),
        "horizontal_fov_deg": math.degrees(
            math.atan(k[0, 2] / k[0, 0]) + math.atan((width - k[0, 2]) / k[0, 0])
        ),
        "vertical_fov_deg": math.degrees(
            math.atan(k[1, 2] / k[1, 1]) + math.atan((height - k[1, 2]) / k[1, 1])
        ),
        "reference_to_optical_z_offset_m": float(offset[2]),
    }


def target_record(camera, obj, identity, *, height, center, visibility=None):
    """Functionality: Build comparable object-level z/size descriptors without dropping hard GTs.
    Inputs: camera row, original GT object, identity, physical height and camera-frame center.
    Outputs: descriptive row. Logic: retain original target and project bbox scales only.
    Constraints: Euclidean distance is audit metadata and never substitutes for z labels.
    """
    bbox = obj["bbox"]
    z = obj.get("distance_m", obj.get("distance_gt_m"))
    if z <= 0 or not math.isfinite(z) or abs(z - center[2]) > 1e-9:
        raise ValueError("Target is not the recorded positive camera z")
    return {
        **camera,
        "object_id": identity,
        "class_name": obj["class_name"],
        "target_m": z,
        "distance_bin": distance_bin(z),
        "prediction_m": None,
        "matched": False,
        "method": "GT",
        "bbox_mode": "gt",
        "variant": "original",
        "input_height_px": (bbox[3] - bbox[1]) * camera["scale_y"],
        "input_width_px": (bbox[2] - bbox[0]) * camera["scale_x"],
        "real_height_m": height,
        "visibility": visibility,
        "euclidean_minus_z_m": float(np.linalg.norm(center) - z),
    }


def kitti_records(processed):
    """Functionality: Audit both actual training splits against raw KITTI labels/calibrations.
    Inputs: active processed directory. Outputs: all GT rows, cameras, split frames and checks.
    Logic: verify split/raw SHA, parse all accepted labels, compare P2 and actual image dimensions.
    Constraints: public testing images have no labels and never enter the reported distributions.
    """
    processed, rows, cameras, frames = Path(processed), [], [], {}
    manifest = json.loads((processed / "manifest.json").read_text())
    identifiers = []
    for split in ("train", "val"):
        path = processed / f"{split}.jsonl"
        if digest(path) != manifest["splits"][split]["manifest_sha256"]:
            raise ValueError("KITTI manifest fingerprint mismatch")
        frames[split] = read_lines(path)
        identifiers.append({f["image_id"] for f in frames[split]})
        for f in frames[split]:
            raw = processed.parent
            label = raw / "raw/training/label_2" / f"{f['image_id']}.txt"
            calib = raw / f["calibration_path"]
            if digest(label) != f["label_sha256"] or digest(calib) != f["calibration_sha256"]:
                raise ValueError("KITTI raw label/calibration changed")
            if read_calibration(calib) != f["p2"]:
                raise ValueError("KITTI cached P2 differs from raw")
            with Image.open(raw / f["image_path"]) as image:
                if list(image.size) != f["image_size"] or image.mode != "RGB":
                    raise ValueError("KITTI image metadata mismatch")
            accepted, heights = [], []
            for line in label.read_text().splitlines():
                obj, _ = parse_label(line, f["image_size"])
                if obj is not None:
                    accepted.append(obj)
                    heights.append(float(line.split()[8]))
            if accepted != f["objects"]:
                raise ValueError("KITTI cached labels differ from raw parse")
            camera = camera_parameters(f, "kitti", split)
            cameras.append(camera)
            rows.extend(
                target_record(
                    camera,
                    obj,
                    f"{f['image_id']}:{i}",
                    height=h,
                    center=obj["location_xyz_m"],
                    visibility=obj["occlusion"],
                )
                for i, (obj, h) in enumerate(zip(accepted, heights, strict=True))
            )
        logger.info("Audited KITTI split=%s frames=%d", split, len(frames[split]))
    if identifiers[0] & identifiers[1]:
        raise ValueError("KITTI train/val leakage by image ID")
    return (
        rows,
        cameras,
        frames,
        {
            "raw_label_and_calibration_matches": True,
            "train_val_image_disjoint": True,
            "scene_independence_established": False,
        },
    )


def nuscenes_records(processed, run):
    """Functionality: Cross-check every selected camera/GT against official SDK sensor boxes.
    Inputs: processed directory and complete comparison run. Outputs: rows,cameras,frames,checks.
    Logic: exact selected frame IDs, per-exposure K and camera coordinates from get_sample_data.
    Constraints: no visibility/size filtering; cuboid center z remains the original reference.
    """
    from nuscenes.nuscenes import NuScenes
    from nuscenes.utils.geometry_utils import BoxVisibility

    run, processed = Path(run), Path(processed)
    manifest = json.loads((run / "run_manifest.json").read_text())
    summary = json.loads((processed / "dataset_summary.json").read_text())
    if (
        manifest["status"] != "complete"
        or digest(processed / "frames.jsonl") != manifest["dataset_frames_sha256"]
    ):
        raise ValueError("Incomplete run or changed nuScenes manifest")
    indexed = {f["frame_id"]: f for f in read_lines(processed / "frames.jsonl")}
    frames = [indexed[k] for k in manifest["selected_frame_ids"]]
    nusc = NuScenes(version=summary["version"], dataroot=summary["raw_directory"], verbose=False)
    rows, cameras, errors, bottom_delta = [], [], [], []
    for f in frames:
        sd = nusc.get("sample_data", f["frame_id"])
        sensor = nusc.get("calibrated_sensor", sd["calibrated_sensor_token"])
        _, boxes, k = nusc.get_sample_data(
            f["frame_id"],
            box_vis_level=BoxVisibility.NONE,
            selected_anntokens=[obj["annotation_id"] for obj in f["objects"]],
        )
        if not np.array_equal(k, f["camera_intrinsic"]) or f["image_size"] != [
            sd["width"],
            sd["height"],
        ]:
            raise ValueError("nuScenes camera metadata differs from official SDK")
        box_map = {b.token: b for b in boxes}
        camera = camera_parameters(f, "nuscenes", "mini")
        camera.update(
            sensor_translation_ego_m=json.dumps(sensor["translation"]),
            sensor_rotation_ego_wxyz=json.dumps(sensor["rotation"]),
        )
        cameras.append(camera)
        for obj in f["objects"]:
            box = box_map[obj["annotation_id"]]
            error = float(np.max(np.abs(box.center - obj["center_camera_m"])))
            errors.append(error)
            if error > 1e-8:
                raise ValueError("nuScenes cached camera center differs from official SDK")
            row = target_record(
                camera,
                obj,
                f"{f['frame_id']}:{obj['annotation_id']}",
                height=float(box.wlh[2]),
                center=box.center,
                visibility=obj["visibility_token"],
            )
            delta = float(-box.wlh[2] / 2 * box.orientation.rotation_matrix[2, 2])
            row["physical_center_to_bottom_z_delta_m"] = delta
            rows.append(row)
            bottom_delta.append(abs(delta))
    logger.info("Audited nuScenes frames=%d GT=%d", len(frames), len(rows))
    return (
        rows,
        cameras,
        frames,
        {
            "official_center_max_abs_error_m": max(errors, default=0),
            "camera_intrinsics_and_sizes_match": True,
            "physical_center_to_bottom_abs_z_delta_m": numeric_summary(bottom_delta),
            "kitti_convention_center_to_bottom_z_delta_m": 0.0,
        },
    )


def join_kitti(rows, frames, evaluation, checkpoint_sha):
    """Functionality: Join current-checkpoint validation outputs using the unchanged matcher.
    Inputs: GT rows/frames,evaluation directory and expected E checkpoint SHA. Outputs: scored rows.
    Logic: enforce checkpoint/split/count identity; aligned GT slots or same-class IoU assignment.
    Constraints: old smoke weights or old splits fail explicitly, never become comparison evidence.
    """
    evaluation, result = Path(evaluation), []
    targets = {r["object_id"]: r for r in rows if r["split"] == "val"}
    for mode in ("gt", "predicted"):
        metrics = json.loads((evaluation / f"{mode}_metrics.json").read_text())
        if metrics["checkpoint_sha256"] != checkpoint_sha or metrics["evaluated_images"] != len(
            frames
        ):
            raise ValueError("KITTI comparison uses a different checkpoint or frame set")
        saved = {r["image_id"]: r for r in read_lines(evaluation / f"{mode}_predictions.jsonl")}
        if set(saved) != {f["image_id"] for f in frames}:
            raise ValueError("KITTI evaluation frame identity mismatch")
        selected = []
        for frame in frames:
            predictions = saved[frame["image_id"]]["objects"]
            gt = frame["objects"]
            if mode == "gt" and len(predictions) != len(gt):
                raise ValueError("KITTI GT alignment mismatch")
            matches = (
                dict(enumerate(range(len(gt))))
                if mode == "gt"
                else match_objects(gt, predictions, metrics["match_iou"])
            )
            for i in range(len(gt)):
                prediction = predictions[matches[i]] if i in matches else None
                row = {
                    **targets[f"{frame['image_id']}:{i}"],
                    "method": "e",
                    "bbox_mode": mode,
                    "matched": prediction is not None,
                    "prediction_m": prediction["distance_m"] if prediction is not None else None,
                }
                selected.append(row)
        if abs(error_summary(selected)["mae_m"] - metrics["metrics"]["overall"]["mae_m"]) > 1e-8:
            raise ValueError("KITTI reconstructed errors differ from original metrics")
        result.extend(selected)
    return result


def join_nuscenes(rows, run):
    """Functionality: Attach preserved per-target observations for all original methods/modes.
    Inputs: nuScenes GT rows and comparison run. Outputs: scored descriptive rows.
    Logic: require exact GT identity/class/distance and original aggregate metric reproduction.
    Constraints: original IoU assignments and missing distances are never recomputed or imputed.
    """
    run, result = Path(run), []
    indexed = {r["object_id"]: r for r in rows}
    metrics = json.loads((run / "metrics.json").read_text())
    for mode in ("gt", "predicted"):
        for method in "abce":
            observations = read_lines(run / "passes" / f"{method}-{mode}" / "observations.jsonl")
            if {r["key"] for r in observations} != indexed.keys():
                raise ValueError("nuScenes observation identity mismatch")
            selected = []
            for obj in observations:
                target = indexed[obj["key"]]
                if (
                    abs(target["target_m"] - obj["target_m"]) > 1e-9
                    or target["class_name"] != obj["class_name"]
                ):
                    raise ValueError("nuScenes observation target mismatch")
                selected.append(
                    {
                        **target,
                        "method": method,
                        "bbox_mode": mode,
                        "prediction_m": obj["prediction_m"],
                        "matched": obj["matched"],
                        "depth_frame": obj["depth_frame"],
                    }
                )
            original = metrics["results"][mode][method]["metrics"]["overall"]
            if abs(error_summary(selected)["mae_m"] - original["mae_m"]) > 1e-8:
                raise ValueError("nuScenes reconstructed errors differ from original metrics")
            result.extend(selected)
    return result


def error_summary(rows):
    """Functionality: Separate signed bias from absolute error and missing-target coverage.
    Inputs: descriptive prediction rows. Outputs: numeric error/coverage/prediction summaries.
    Logic: error=prediction-GT; negative means underestimation; nulls excluded only from errors.
    Constraints: every GT stays in count/coverage; no sentinel-zero errors or implicit imputation.
    """
    valid = [r for r in rows if r["prediction_m"] is not None]
    errors = np.asarray([r["prediction_m"] - r["target_m"] for r in valid])
    targets = np.asarray([r["target_m"] for r in valid])
    return {
        "gt_count": len(rows),
        "matched_count": sum(r["matched"] for r in rows),
        "valid_count": len(valid),
        "coverage": len(valid) / len(rows) if rows else None,
        "bias_m": float(errors.mean()) if valid else None,
        "median_bias_m": float(np.median(errors)) if valid else None,
        "mae_m": float(np.abs(errors).mean()) if valid else None,
        "rmse_m": float(np.sqrt(np.square(errors).mean())) if valid else None,
        "abs_rel": float(np.mean(np.abs(errors) / targets)) if valid else None,
        "underestimate_fraction": float(np.mean(errors < 0)) if valid else None,
        "median_prediction_target_ratio": float(np.median(1 + errors / targets)) if valid else None,
        "prediction_p99_m": float(np.percentile([r["prediction_m"] for r in valid], 99))
        if valid
        else None,
        "prediction_max_m": max((r["prediction_m"] for r in valid), default=None),
    }


def aggregate(rows, fields):
    """Functionality: Produce deterministic grouped signed-error tables.
    Inputs: rows and explicit group fields. Outputs: flat dictionaries with group/metric columns.
    Logic: preserve all rows in each group. Constraints: no fitting,weighting or target filtering.
    """
    groups = defaultdict(list)
    for row in rows:
        groups[tuple(row[k] for k in fields)].append(row)
    return [
        {**dict(zip(fields, key, strict=True)), **error_summary(values)}
        for key, values in sorted(groups.items())
    ]


def standardize_composition(kitti, nuscenes):
    """Functionality: Audit sample-mix effects using fixed class/distance strata.
    Inputs: current E GT-box validation rows for both domains. Outputs: counterfactual weighted MAE.
    Logic: apply nuScenes stratum counts to KITTI validation stratum errors,without fitting a model.
    Constraints: empty source strata remain unsupported; coarse matching does not prove causation.
    """
    source, target = {}, defaultdict(list)
    for row in aggregate(kitti, ["class_name", "distance_bin"]):
        source[(row["class_name"], row["distance_bin"])] = row
    for row in nuscenes:
        target[(row["class_name"], row["distance_bin"])].append(row)
    cells, numerator, count = [], 0.0, 0
    for key, values in sorted(target.items()):
        reference = source.get(key)
        cells.append(
            {
                "class_name": key[0],
                "distance_bin": key[1],
                "kitti_val_count": reference["valid_count"] if reference else 0,
                "nuscenes_gt_count": len(values),
                "kitti_val_mae_m": reference["mae_m"] if reference else None,
                "nuscenes_mae_m": error_summary(values)["mae_m"],
            }
        )
        if reference and reference["mae_m"] is not None:
            numerator += reference["mae_m"] * len(values)
            count += len(values)
    return {
        "weighted_kitti_mae_m": numerator / count if count else None,
        "represented_nuscenes_count": count,
        "total_nuscenes_count": len(nuscenes),
        "unsupported_nuscenes_count": len(nuscenes) - count,
        "limitation": (
            "Coarse class/z reweighting; no scene/visibility/camera matching; "
            "sparse far bins remain sparse"
        ),
        "cells": cells,
    }


def box_effects(rows):
    """Functionality: Compare GT/predicted boxes on identical successfully detected E targets.
    Inputs: original scored rows. Outputs: paired per-domain regression and output-change summaries.
    Logic: pair by dataset/split/object identity before error aggregation.
    Constraints: all-GT versus detected-only MAE is not attributed solely to box quality.
    """
    gt = {
        (r["dataset"], r["split"], r["object_id"]): r
        for r in rows
        if r["method"] == "e" and r["bbox_mode"] == "gt" and r["variant"] == "original"
    }
    groups = defaultdict(list)
    for row in rows:
        if (
            row["method"] == "e"
            and row["bbox_mode"] == "predicted"
            and row["variant"] == "original"
            and row["prediction_m"] is not None
        ):
            groups[row["dataset"]].append(
                (gt[(row["dataset"], row["split"], row["object_id"])], row)
            )
    return [
        {
            "dataset": key,
            "common_valid_count": len(pairs),
            "gt_box_mae_m": error_summary([a for a, b in pairs])["mae_m"],
            "predicted_box_mae_m": error_summary([b for a, b in pairs])["mae_m"],
            "mean_abs_distance_change_m": float(
                np.mean([abs(a["prediction_m"] - b["prediction_m"]) for a, b in pairs])
            ),
        }
        for key, pairs in sorted(groups.items())
    ]


def export_csv(path, rows):
    """Functionality: Export nullable descriptive tables readable by Excel.
    Inputs: new path and flat dictionary rows. Outputs: UTF-8 BOM CSV.
    Logic: union columns,retain nulls as empty cells. Constraints: never writes original run files.
    """
    keys = list(dict.fromkeys(k for row in rows for k in row))
    with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def render(output, report, targets, scored):
    """Functionality: Publish static scientific charts and a readable evidence report.
    Inputs: new output,summary,GT/scored rows. Outputs: PNG/SVG/Markdown/CSV artifacts.
    Logic: explicit normalized distribution and original/sensitivity plots with unchanged counts.
    Constraints: sensitivity is labeled diagnostic only,not a newly evaluated model or benchmark.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure, axes = plt.subplots(2, 2, figsize=(15, 10), constrained_layout=True)
    for dataset, split, label in [
        ("kitti", "train", "KITTI train"),
        ("kitti", "val", "KITTI val"),
        ("nuscenes", "mini", "nuScenes CAM_FRONT"),
    ]:
        values = [r["target_m"] for r in targets if r["dataset"] == dataset and r["split"] == split]
        counts = np.histogram(values, EDGES)[0] / len(values) * 100
        axes[0, 0].plot(LABELS, counts, marker="o", label=label)
    axes[0, 0].set(
        title="GT camera-z distribution (all accepted targets)",
        ylabel="Share of targets (%)",
        xlabel="GT z (m)",
    )
    axes[0, 0].legend()
    for dataset, label in [("kitti", "KITTI val E"), ("nuscenes", "nuScenes E")]:
        rows = [
            r
            for r in scored
            if r["dataset"] == dataset
            and r["bbox_mode"] == "gt"
            and r["method"] == "e"
            and r["variant"] == "original"
        ]
        values = {r["distance_bin"]: r for r in aggregate(rows, ["distance_bin"])}
        axes[0, 1].plot(
            LABELS,
            [values.get(k, {}).get("bias_m", np.nan) for k in LABELS],
            marker="o",
            label=label,
        )
    axes[0, 1].axhline(0, color="black", linewidth=0.8)
    axes[0, 1].set(
        title="E GT-box signed bias (prediction - GT)",
        ylabel="Mean signed error (m)",
        xlabel="GT z (m)",
    )
    axes[0, 1].legend()
    for variant, label in [
        ("original", "Original E"),
        ("focal_sensitivity", "E x input-focal ratio (diagnostic only)"),
    ]:
        rows = [
            r
            for r in scored
            if r["dataset"] == "nuscenes"
            and r["bbox_mode"] == "predicted"
            and r["method"] == "e"
            and r["variant"] == variant
        ]
        values = {r["distance_bin"]: r for r in aggregate(rows, ["distance_bin"])}
        axes[1, 0].plot(
            LABELS,
            [values.get(k, {}).get("mae_m", np.nan) for k in LABELS],
            marker="o",
            label=label,
        )
    axes[1, 0].set(
        title="nuScenes E predicted-box conditional MAE", ylabel="MAE (m)", xlabel="GT z (m)"
    )
    axes[1, 0].legend(fontsize=8)
    camera_groups = [("kitti", "train"), ("kitti", "val"), ("nuscenes", "mini")]
    summary = report["camera_summary"]
    medians = [summary[f"{d}/{s}"]["input_fy"]["median"] for d, s in camera_groups]
    axes[1, 1].bar(
        ["KITTI train", "KITTI val", "nuScenes"], medians, color=["#4477aa", "#66aadd", "#ee7733"]
    )
    for i, value in enumerate(medians):
        axes[1, 1].text(i, value + 5, f"{value:.2f} px", ha="center")
    axes[1, 1].set(
        title="Median effective fy at the unchanged 640 input",
        ylabel="Effective focal length (px)",
        ylim=(0, max(medians) * 1.15),
    )
    figure.suptitle(
        "Fixed checkpoint: KITTI validation vs nuScenes Mini | No model or metric changes",
        fontsize=14,
    )
    figure.savefig(output / "domain-analysis.png", dpi=160)
    figure.savefig(output / "domain-analysis.svg")
    plt.close(figure)
    lines = [
        "# KITTI / nuScenes distance-domain diagnosis",
        "",
        "All errors use prediction minus GT for signed bias. "
        "Original files,weights,models and evaluator thresholds are preserved.",
        "",
        "## Original-checkpoint results",
        "",
        "| Dataset | Boxes | Variant | GT | Valid | MAE m | Bias m | Underestimated |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in report["overall"]:
        if row["method"] != "e":
            continue
        lines.append(
            f"| {row['dataset']} | {row['bbox_mode']} | {row['variant']} | "
            f"{row['gt_count']} | {row['valid_count']} | {row['mae_m']:.4f} | "
            f"{row['bias_m']:.4f} | {row['underestimate_fraction']:.2%} |"
        )
    lines.extend(
        [
            "",
            "## Target and camera distributions",
            "",
            "| Dataset/split | Targets | Median z m | P90 z m | Max z m | Median input fy px |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for key in ("kitti/train", "kitti/val", "nuscenes/mini"):
        values = report["target_summary"][key]["target_m"]
        focal = report["camera_summary"][key]["input_fy"]["median"]
        lines.append(
            f"| {key} | {values['count']} | {values['median']:.3f} | "
            f"{values['p90']:.3f} | {values['max']:.3f} | {focal:.3f} |"
        )
    lines.extend(
        [
            "",
            "## E GT-box range errors (all original GTs)",
            "",
            "| GT range m | KITTI val n | KITTI MAE m | KITTI bias m | "
            "nuScenes n | nuScenes MAE m | nuScenes bias m |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    bins = {
        (r["dataset"], r["distance_bin"]): r
        for r in aggregate(
            [
                r
                for r in scored
                if r["method"] == "e" and r["bbox_mode"] == "gt" and r["variant"] == "original"
            ],
            ["dataset", "distance_bin"],
        )
    }
    for label in LABELS:
        cells = []
        for dataset in ("kitti", "nuscenes"):
            row = bins.get((dataset, label))
            cells.append(
                f"{row['valid_count']} | {row['mae_m']:.3f} | {row['bias_m']:.3f}"
                if row
                else "0 | unavailable | unavailable"
            )
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    shift = report["class_distance_standardization"]
    checks = report["checks"]["nuscenes"]
    lines.extend(
        [
            "",
            "## Evidence-based attribution",
            "",
            "Camera scale is the strongest supported contributor to E's low estimates: "
            "effective input focal length differs substantially,E does not consume intrinsics. "
            "The no-fit focal sensitivity substantially reduces error on identical targets. "
            "This is an attribution sensitivity,not a validated production correction.",
            "",
            f"Class/range weighted KITTI MAE is {shift['weighted_kitti_mae_m']:.3f}m "
            f"on strata representing {shift['represented_nuscenes_count']}/"
            f"{shift['total_nuscenes_count']} nuScenes targets. This is far below the original "
            "nuScenes GT-box error,so distance/class mixture alone does not explain the gap. "
            "Sparse/unsupported far strata prevent treating this as an exact decomposition.",
            "",
            "All selected nuScenes camera centers match official SDK values "
            f"(maximum error {checks['official_center_max_abs_error_m']:.3g}m). "
            "KITTI bottom-to-center convention changes z by0m; the largest nuScenes physical "
            "center-to-bottom z shift is "
            f"{checks['physical_center_to_bottom_abs_z_delta_m']['max']:.4f}m. "
            "These reference-point effects cannot explain the observed meter-scale low bias.",
        ]
    )
    lines.extend(
        [
            "",
            "## Interpretation and limits",
            "",
            "- Current E uses ROI features,normalized canvas box geometry and class indicators; "
            "no camera K/focal input. No hard80m output cap exists (Softplus is unbounded).",
            "- Focal sensitivity multiplies each unchanged E output by current input fy / "
            "KITTI TRAIN frame-median input fy. It uses no nuScenes GT fit and changes neither "
            "coverage nor original metrics. Improvement supports a camera-scale mismatch, "
            "not a proof that all domain effects are caused by focal length.",
            "- KITTI bottom-to-geometric-center conversion changes camera y by half height and "
            "leaves z unchanged. P2 optical-origin z offsets and nuScenes physical vertical-axis "
            "z shifts are separately quantified in analysis.json. A meter-scale z bias is not "
            "explained merely by calling one label bottom-center and the other geometric-center.",
            "- KITTI image-ID80/20 splitting does not establish scene independence. nuScenes "
            "repeated keyframe observations are not independent objects; no confidence intervals "
            "are claimed.",
            "- nuScenes GTs are projected3D cuboids; KITTI uses provided2D boxes. GT-box passes "
            "remove detector misses,not box-convention/domain differences. Paired box-effects.csv "
            "isolates the same detected targets.",
            "- Raw GT distributions retain every currently accepted car/person label. No "
            "visibility,size,distance or confidence filtering was added. nuScenes raw "
            "all-around/out-of-image annotations are outside the existing camera-evaluation "
            "universe.",
            "- Class/z standardization is descriptive,with unsupported/sparse cells explicit. "
            "Counts beyond the training maximum and per-class training range are in support.json.",
            "",
            "## Reproducibility",
            "",
            "See analysis.json,provenance.json,preservation.json and the accompanying CSV tables. "
            "The current checkpoint SHA and split fingerprints must match before comparisons "
            "are accepted.",
            "",
            "Reference: [official nuScenes KITTI conversion]"
            "(https://github.com/nutonomy/nuscenes-devkit/blob/master/"
            "python-sdk/nuscenes/utils/kitti.py) "
            "and [nuScenes schema]"
            "(https://github.com/nutonomy/nuscenes-devkit/blob/master/docs/schema_nuscenes.md).",
            "",
            "![Domain analysis](domain-analysis.png)",
        ]
    )
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def analyze(kitti_processed, kitti_evaluation, nuscenes_processed, nuscenes_run, output):
    """Functionality: Execute a reproducible read-only cross-domain distance/camera diagnosis.
    Inputs: explicit dataset/evaluation directories and a fresh external output directory.
    Outputs: JSON,CSV,PNG,SVG and report path. Logic: audit sources,join fixed outputs,quantify bias
    and training support,then independently label a no-fit focal sensitivity calculation.
    Constraints: no training,inference,threshold edits,GT fitting or production-path modifications.
    """
    paths = [
        Path(p).resolve()
        for p in (kitti_processed, kitti_evaluation, nuscenes_processed, nuscenes_run)
    ]
    output = Path(output).resolve()
    if output.exists() or any(output.is_relative_to(p) or p.is_relative_to(output) for p in paths):
        raise ValueError("Choose a new output outside all original inputs")
    kitti_processed, kitti_evaluation, nuscenes_processed, nuscenes_run = paths
    nusc_summary = json.loads((nuscenes_processed / "dataset_summary.json").read_text())
    raw_directories = [kitti_processed.parent / "raw", Path(nusc_summary["raw_directory"])]
    if any(output.is_relative_to(p) or p.is_relative_to(output) for p in raw_directories):
        raise ValueError("Choose a new output outside raw datasets")
    before = {str(p): snapshot(p) for p in paths}
    sources = source_snapshot()
    analysis_source = digest(__file__)
    manifest = json.loads((nuscenes_run / "run_manifest.json").read_text())
    if sources != manifest["source_sha256"]:
        raise ValueError("Original inference sources differ from the nuScenes run")
    checkpoint_sha = manifest["weight_sha256"]["e_checkpoint"]
    checkpoint = Path(manifest["configuration"]["e_checkpoint"])
    if digest(checkpoint) != checkpoint_sha:
        raise ValueError("Current checkpoint differs from original nuScenes evaluation")
    kitti, kc, kf, kchecks = kitti_records(kitti_processed)
    raw_fingerprints = {}
    for frames in kf.values():
        for frame in frames:
            raw_fingerprints[
                str(raw_directories[0] / "training/label_2" / f"{frame['image_id']}.txt")
            ] = frame["label_sha256"]
            raw_fingerprints[str(kitti_processed.parent / frame["calibration_path"])] = frame[
                "calibration_sha256"
            ]
    for table, sha in nusc_summary["raw_audit"]["table_sha256"].items():
        raw_fingerprints[str(raw_directories[1] / nusc_summary["version"] / f"{table}.json")] = sha
    for key, sha in manifest["weight_sha256"].items():
        raw_fingerprints[manifest["configuration"][key]] = sha
    if any(digest(path) != sha for path, sha in raw_fingerprints.items()):
        raise ValueError("Raw metadata or weights differ from original input fingerprints")
    nusc, nc, _, nchecks = nuscenes_records(nuscenes_processed, nuscenes_run)
    for mode in ("gt", "predicted"):
        km = json.loads((kitti_evaluation / f"{mode}_metrics.json").read_text())
        if km["split_manifest_sha256"] != digest(kitti_processed / "val.jsonl"):
            raise ValueError("KITTI reference uses a different validation split")
        identity = manifest["conditions"][mode]["identities"]["e"]
        if (
            km["confidence"] != identity["confidence"]
            or km["match_iou"] != manifest["configuration"]["match_iou"]
            or km["config"]["image_size"] != identity["image_size"]
            or identity["image_size"] != 640
        ):
            raise ValueError("Reference thresholds or640 input differ from the nuScenes run")
    scored = join_kitti(kitti, kf["val"], kitti_evaluation, checkpoint_sha) + join_nuscenes(
        nusc, nuscenes_run
    )
    reference_fy = float(np.median([c["input_fy"] for c in kc if c["split"] == "train"]))
    sensitivity = [
        {
            **r,
            "variant": "focal_sensitivity",
            "prediction_m": r["prediction_m"] * r["input_fy"] / reference_fy
            if r["prediction_m"] is not None
            else None,
        }
        for r in scored
        if r["dataset"] == "nuscenes" and r["method"] == "e"
    ]
    scored.extend(sensitivity)
    targets, cameras = kitti + nusc, kc + nc
    fields = ["dataset", "split", "method", "bbox_mode", "variant"]
    overall = aggregate(scored, fields)
    distributions, details, cam_summary = [], {}, {}
    for dataset, split in (("kitti", "train"), ("kitti", "val"), ("nuscenes", "mini")):
        selected = [r for r in targets if r["dataset"] == dataset and r["split"] == split]
        details[f"{dataset}/{split}"] = {
            name: numeric_summary([r[name] for r in selected])
            for name in (
                "target_m",
                "input_height_px",
                "input_width_px",
                "real_height_m",
                "euclidean_minus_z_m",
            )
        }
        for category in ("car", "person"):
            details[f"{dataset}/{split}/{category}"] = {
                name: numeric_summary([r[name] for r in selected if r["class_name"] == category])
                for name in ("target_m", "input_height_px", "real_height_m")
            }
        selected_cameras = [r for r in cameras if r["dataset"] == dataset and r["split"] == split]
        cam_summary[f"{dataset}/{split}"] = {
            name: numeric_summary([r[name] for r in selected_cameras])
            for name in (
                "fx",
                "fy",
                "cx",
                "cy",
                "input_fx",
                "input_fy",
                "input_cx",
                "input_cy",
                "top",
                "resized_height",
                "horizontal_fov_deg",
                "vertical_fov_deg",
                "reference_to_optical_z_offset_m",
            )
        }
        cam_summary[f"{dataset}/{split}"].update(
            frames=len(selected_cameras),
            distinct_intrinsics=len({r["camera_id"] for r in selected_cameras}),
        )
        for category in ("all", "car", "person"):
            subset = [r for r in selected if category == "all" or r["class_name"] == category]
            for label in LABELS:
                count = sum(r["distance_bin"] == label for r in subset)
                distributions.append(
                    {
                        "dataset": dataset,
                        "split": split,
                        "class_name": category,
                        "distance_bin": label,
                        "count": count,
                        "fraction": count / len(subset) if subset else None,
                    }
                )
    support = {}
    for category in ("all", "car", "person"):
        train = [
            r
            for r in kitti
            if r["split"] == "train" and (category == "all" or r["class_name"] == category)
        ]
        external = [r for r in nusc if category == "all" or r["class_name"] == category]
        maximum = max(r["target_m"] for r in train)
        support[category] = {
            "training_count": len(train),
            "training_max_m": maximum,
            "training_ge80_count": sum(r["target_m"] >= 80 for r in train),
            "nuscenes_count": len(external),
            "nuscenes_ge80_count": sum(r["target_m"] >= 80 for r in external),
            "nuscenes_above_training_max_count": sum(r["target_m"] > maximum for r in external),
        }
    kt = [r for r in scored if r["dataset"] == "kitti" and r["bbox_mode"] == "gt"]
    nt = [
        r
        for r in scored
        if r["dataset"] == "nuscenes"
        and r["bbox_mode"] == "gt"
        and r["method"] == "e"
        and r["variant"] == "original"
    ]
    composition = standardize_composition(kt, nt)
    report = {
        "schema_version": 1,
        "distance_unit": "meters",
        "signed_error": "prediction_minus_GT",
        "checkpoint_sha256": checkpoint_sha,
        "reference_train_input_fy_px": reference_fy,
        "checks": {"kitti": kchecks, "nuscenes": nchecks},
        "overall": overall,
        "target_summary": details,
        "camera_summary": cam_summary,
        "training_support": support,
        "class_distance_standardization": composition,
        "paired_box_effects": box_effects(scored),
        "scope": "Descriptive domain audit; focal sensitivity is not an official model score",
    }
    output.mkdir(parents=True)
    export_csv(output / "distance-distribution.csv", distributions)
    export_csv(output / "camera-frames.csv", cameras)
    export_csv(output / "targets.csv", targets)
    export_csv(output / "prediction-audit.csv", scored)
    export_csv(output / "overall.csv", overall)
    export_csv(output / "bias-by-distance.csv", aggregate(scored, fields + ["distance_bin"]))
    export_csv(
        output / "bias-by-class-distance.csv",
        aggregate(scored, fields + ["class_name", "distance_bin"]),
    )
    export_csv(output / "bias-by-camera.csv", aggregate(scored, fields + ["camera_id"]))
    export_csv(output / "bias-by-scene.csv", aggregate(scored, fields + ["scene_id"]))
    export_csv(output / "bias-by-visibility.csv", aggregate(scored, fields + ["visibility"]))
    export_csv(output / "box-effects.csv", report["paired_box_effects"])
    export_csv(output / "composition-cells.csv", composition["cells"])
    write_json(output / "support.json", support)
    write_json(output / "analysis.json", report)
    render(output, report, targets, scored)
    preservation = {
        "original_input_trees_unchanged": {str(p): before[str(p)] == snapshot(p) for p in paths},
        "original_sources_unchanged": sources == source_snapshot(),
        "checkpoint_unchanged": digest(checkpoint) == checkpoint_sha,
        "analysis_source_unchanged": digest(__file__) == analysis_source,
        "raw_labels_calibrations_metadata_and_weights_unchanged": all(
            digest(path) == sha for path, sha in raw_fingerprints.items()
        ),
    }
    write_json(output / "preservation.json", preservation)
    write_json(
        output / "provenance.json",
        {
            "inputs_before": before,
            "original_source_sha256": sources,
            "analysis_source_sha256": analysis_source,
            "checkpoint_sha256": checkpoint_sha,
            "raw_labels_calibrations_metadata_and_weights_sha256": raw_fingerprints,
        },
    )
    if (
        not all(preservation["original_input_trees_unchanged"].values())
        or not preservation["original_sources_unchanged"]
        or not preservation["checkpoint_unchanged"]
        or not preservation["analysis_source_unchanged"]
        or not preservation["raw_labels_calibrations_metadata_and_weights_unchanged"]
    ):
        raise ValueError("Original inputs changed during diagnosis")
    return report


def main(argv=None):
    """Functionality: Expose explicit inputs and a new output for cross-domain diagnosis.
    Inputs: CLI paths. Outputs: saved report and short JSON status.
    Logic: no implicit inference run. Constraints: reference evaluation must already be complete.
    """
    parser = argparse.ArgumentParser(
        description="Audit fixed KITTI/nuScenes distance-domain evidence"
    )
    parser.add_argument(
        "--kitti-processed",
        type=Path,
        default=ROOT / "model_tests/datasets/kitti/processed-80-20-seed42",
    )
    parser.add_argument("--kitti-evaluation", type=Path, required=True)
    parser.add_argument(
        "--nuscenes-processed", type=Path, default=ROOT / "model_tests/datasets/nuscenes/processed"
    )
    parser.add_argument("--nuscenes-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    report = analyze(
        args.kitti_processed,
        args.kitti_evaluation,
        args.nuscenes_processed,
        args.nuscenes_run,
        args.output,
    )
    print(
        json.dumps({"output": str(args.output), "checkpoint_sha256": report["checkpoint_sha256"]})
    )


if __name__ == "__main__":
    main()
