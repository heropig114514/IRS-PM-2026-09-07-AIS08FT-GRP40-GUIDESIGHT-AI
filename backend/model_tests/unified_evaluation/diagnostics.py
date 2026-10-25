"""Responsibilities: Explain fixed-run coverage losses without changing evaluation standards.
Implementation: Official geometry cross-checks,original assignment audits and raw detector replay.
Related Modules: unified_evaluation.dataset/diagnostic_capture/diagnostic_render; original results.
Declaration Index: snapshot; distribution; independent_iou; maximum_cardinality;
maximum_cardinality.augment; eligible;
audit_geometry; diagnose_matches; summarize_rows; diagnose; main.
Variable Index: DISTANCE_EDGES; HEIGHT_EDGES; CONFIDENCE_EDGES — descriptive distributions only.
logger — explicit original-output replay progress.
"""

import argparse
import json
import logging
import math
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import numpy as np

from object_distance.config import ROOT, digest, write_json
from object_distance.metrics import bbox_iou, match_objects

from .dataset import camera_box, load_records, map_category, project_box
from .runner import source_snapshot
from .scoring import valid_distance

DISTANCE_EDGES = (0, 1, 3, 5, 10, 20, 40, 60, 80, float("inf"))
HEIGHT_EDGES = (0, 8, 16, 32, 64, 128, 256, float("inf"))
CONFIDENCE_EDGES = (0, 0.01, 0.05, 0.1, 0.25, 0.5, 0.75, 1.000001)
logger = logging.getLogger(__name__)


def snapshot(root):
    """Functionality: Fingerprint every original result file,including per-frame evidence.
    Inputs: original run directory. Outputs: relative file/SHA256 mapping.
    Logic: recursive read only. Constraints: generated diagnostics must reside outside this tree.
    """
    root = Path(root)
    return {str(path.relative_to(root)): digest(path) for path in root.rglob("*") if path.is_file()}


def distribution(values, edges):
    """Functionality: Describe observations without converting missing evidence into zero.
    Inputs: nullable values and fixed edges. Outputs: count/missing/quantiles/histogram.
    Logic: finite numeric observations only; final upper edge can be infinity.
    Constraints: descriptive bins never affect matching,confidence or reported original coverage.
    """
    valid = [float(v) for v in values if v is not None and math.isfinite(v)]
    histogram = [
        {
            "lower": a,
            "upper": b if math.isfinite(b) else None,
            "count": sum(a <= value < b for value in valid),
        }
        for a, b in zip(edges, edges[1:], strict=False)
    ]
    return {
        "count": len(valid),
        "missing": len(values) - len(valid),
        "min": min(valid) if valid else None,
        "max": max(valid) if valid else None,
        "median": float(np.median(valid)) if valid else None,
        "p10": float(np.percentile(valid, 10)) if valid else None,
        "p90": float(np.percentile(valid, 90)) if valid else None,
        "histogram": histogram,
    }


def independent_iou(first, second):
    """Functionality: Independently audit continuous xyxy intersection arithmetic.
    Inputs: two boxes. Outputs: scalar IoU. Logic: NumPy min/max intersection/union.
    Constraints: no pixel-inclusive offsets,threshold changes or new production matcher.
    """
    first, second = np.asarray(first, dtype=float), np.asarray(second, dtype=float)
    intersection = np.maximum(
        0, np.minimum(first[2:], second[2:]) - np.maximum(first[:2], second[:2])
    ).prod()
    union = (
        np.maximum(0, first[2:] - first[:2]).prod()
        + np.maximum(0, second[2:] - second[:2]).prod()
        - intersection
    )
    return float(intersection / union) if union > 0 else 0.0


def maximum_cardinality(ground_truth, predictions, threshold):
    """Functionality: Check whether greedy assignment loses possible fixed-threshold pairs.
    Inputs: existing GT/predictions and unchanged threshold. Outputs: diagnostic maximum count.
    Logic: bipartite augmenting paths on the same class/IoU eligibility graph.
    Constraints: this audit never replaces or recomputes official evaluation assignments.
    """
    graph = [
        [
            pi
            for pi, prediction in enumerate(predictions)
            if gt["class_name"] == prediction["class_name"]
            and independent_iou(gt["bbox"], prediction["bbox"]) >= threshold
        ]
        for gt in ground_truth
    ]
    assigned = {}

    def augment(index, seen):
        """Functionality: Find one augmenting path in the fixed candidate graph.
        Inputs: GT index and visited prediction set. Outputs: path-found bool.
        Logic: recursively reassign existing partners. Constraints: diagnostic count only.
        """
        for prediction in graph[index]:
            if prediction in seen:
                continue
            seen.add(prediction)
            if prediction not in assigned or augment(assigned[prediction], seen):
                assigned[prediction] = index
                return True
        return False

    for index in range(len(graph)):
        augment(index, set())
    return len(assigned)


def eligible(row, policy, *, height=None):
    """Functionality: Label an explicit diagnostic visibility/size subset alongside all original GT.
    Inputs: projected row,fixed policy,optional reported sensitivity height. Outputs:bool,reasons.
    Logic: require known visibility>=3,retained bbox fraction,width8,height16 at model input.
    Constraints: six-camera visibility is not CAM_FRONT occlusion truth; no rematching after subset.
    """
    reasons = []
    if row.get("bbox") is None:
        return False, ["not_projected_into_image"]
    if row.get("visibility_token") not in {"1", "2", "3", "4"}:
        reasons.append("visibility_unknown")
    elif int(row["visibility_token"]) < policy["min_visibility_token"]:
        reasons.append("visibility_below_policy")
    if row["retained_bbox_fraction"] < policy["min_retained_bbox_fraction"]:
        reasons.append("image_boundary_truncation")
    if row["input_width_px"] < policy["min_input_width_px"]:
        reasons.append("input_width_below_policy")
    minimum = policy["min_input_height_px"] if height is None else height
    if row["input_height_px"] < minimum:
        reasons.append("input_height_below_policy")
    return not reasons, reasons


def audit_geometry(nusc, frame, capture, near, policy):
    """Functionality: Reconcile raw annotations,official camera transforms and cached projections.
    Inputs: SDK,original frame,unchanged replay transform,near plane,diagnostic policy.
    Outputs:raw GT audit rows and numeric correctness checks.
    Logic: independent homogeneous transform,Devkit get_sample_data and official2D postprocessor.
    Constraints: original category/visibility/near filtering is audited,not rewritten; foreground
    bbox overlap is only a potential occlusion proxy and never excludes an observation.
    """
    from nuscenes.scripts.export_2d_annotations_as_json import post_process_coords
    from nuscenes.utils.geometry_utils import BoxVisibility, box_in_image, transform_matrix
    from pyquaternion import Quaternion

    sd = nusc.get("sample_data", frame["frame_id"])
    sample = nusc.get("sample", sd["sample_token"])
    calib = nusc.get("calibrated_sensor", sd["calibrated_sensor_token"])
    pose = nusc.get("ego_pose", sd["ego_pose_token"])
    _, sdk_boxes, intrinsic = nusc.get_sample_data(
        frame["frame_id"], box_vis_level=BoxVisibility.NONE
    )
    sdk_boxes = {box.token: box for box in sdk_boxes}
    camera_from_global = transform_matrix(
        calib["translation"], Quaternion(calib["rotation"]), inverse=True
    ) @ transform_matrix(pose["translation"], Quaternion(pose["rotation"]), inverse=True)
    cached = {obj["annotation_id"]: obj for obj in frame["objects"]}
    visible_tokens = {row["token"] for row in nusc.visibility}
    checks = {
        "camera_center_max_error_m": 0.0,
        "corner_max_error_m": 0.0,
        "cached_bbox_max_error_px": 0.0,
        "cached_distance_max_error_m": 0.0,
        "official_projection_max_error_px": 0.0,
        "iou_arithmetic_max_error": 0.0,
        "bbox_roundtrip_max_error_px": 0.0,
        "image_size": frame["image_size"],
        "input_size": capture.get("identity", {}).get("image_size"),
        "resize_scale_xy": [capture["transform"]["scale_x"], capture["transform"]["scale_y"]],
        "letterbox_padding_xy": [
            capture["transform"].get("left", 0),
            capture["transform"].get("top", 0),
        ],
        "intrinsic_original_pixels": intrinsic.tolist(),
        "intrinsic_matches": bool(np.array_equal(intrinsic, np.asarray(frame["camera_intrinsic"]))),
        "image_size_matches": frame["image_size"] == [sd["width"], sd["height"]],
        "fully_front_projection_reference_checks": 0,
    }
    rows, projected_boxes = [], []
    size = tuple(frame["image_size"])
    for token in sample["anns"]:
        annotation = nusc.get("sample_annotation", token)
        sdk = sdk_boxes[token]
        transformed = camera_box(nusc.get_box(token), pose, calib)
        independent = camera_from_global @ np.asarray([*annotation["translation"], 1.0])
        checks["camera_center_max_error_m"] = max(
            checks["camera_center_max_error_m"],
            float(np.max(np.abs(sdk.center - independent[:3]))),
            float(np.max(np.abs(transformed.center - sdk.center))),
        )
        checks["corner_max_error_m"] = max(
            checks["corner_max_error_m"],
            float(np.max(np.abs(transformed.corners() - sdk.corners()))),
        )
        bbox, reason = project_box(sdk, intrinsic, size, near)
        row = {
            "frame_id": frame["frame_id"],
            "annotation_id": token,
            "instance_id": annotation["instance_token"],
            "category": annotation["category_name"],
            "class_name": map_category(annotation["category_name"]),
            "visibility_token": annotation["visibility_token"],
            "camera_z_m": float(sdk.center[2]),
            "global_z_m": annotation["translation"][2],
            "camera_center_m": sdk.center.tolist(),
            "positive_camera_z": bool(sdk.center[2] > 0),
            "beyond_near_plane": bool(sdk.center[2] > near),
            "bbox": bbox,
            "projection_filter": reason,
            "original_evaluation_gt": token in cached,
            "camera_near_z_m": float(sdk.corners()[2].min()),
            "camera_far_z_m": float(sdk.corners()[2].max()),
        }
        if bbox is not None:
            scale_xy = np.array(
                [capture["transform"]["scale_x"], capture["transform"]["scale_y"]] * 2
            )
            padding_xy = np.array(
                [capture["transform"].get("left", 0), capture["transform"].get("top", 0)] * 2
            )
            mapped = np.asarray(bbox) * scale_xy + padding_xy
            restored = (mapped - padding_xy) / scale_xy
            checks["bbox_roundtrip_max_error_px"] = max(
                checks["bbox_roundtrip_max_error_px"], float(np.max(np.abs(restored - bbox)))
            )
            corners = sdk.corners()
            front = corners[:, corners[2] > near]
            pixels = intrinsic @ front
            pixels = (pixels[:2] / pixels[2]).T
            envelope = [*pixels.min(axis=0), *pixels.max(axis=0)]
            full_area = (envelope[2] - envelope[0]) * (envelope[3] - envelope[1])
            retained = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) / full_area
            row.update(
                input_width_px=(bbox[2] - bbox[0]) * capture["transform"]["scale_x"],
                input_height_px=(bbox[3] - bbox[1]) * capture["transform"]["scale_y"],
                original_width_px=bbox[2] - bbox[0],
                original_height_px=bbox[3] - bbox[1],
                retained_bbox_fraction=float(min(1.0, retained)),
                sdk_box_visibility_any=bool(box_in_image(sdk, intrinsic, size, BoxVisibility.ANY)),
            )
            if bool((corners[2] > near).all()):
                reference = post_process_coords(pixels.tolist(), imsize=size)
                checks["official_projection_max_error_px"] = max(
                    checks["official_projection_max_error_px"],
                    float(np.max(np.abs(np.asarray(reference) - bbox))),
                )
                checks["fully_front_projection_reference_checks"] += 1
            row["policy_eligible"], row["policy_exclusion_reasons"] = eligible(row, policy)
            projected_boxes.append(row)
        else:
            row.update(policy_eligible=False, policy_exclusion_reasons=[reason])
        if token in cached:
            if bbox is None or row["class_name"] != cached[token]["class_name"]:
                raise ValueError("Cached original GT is inconsistent with current raw annotations")
            checks["cached_bbox_max_error_px"] = max(
                checks["cached_bbox_max_error_px"],
                float(np.max(np.abs(np.asarray(bbox) - cached[token]["bbox"]))),
            )
            checks["cached_distance_max_error_m"] = max(
                checks["cached_distance_max_error_m"],
                abs(float(sdk.center[2]) - cached[token]["distance_gt_m"]),
            )
        rows.append(row)
    expected = {
        row["annotation_id"]
        for row in rows
        if row["bbox"] is not None
        and row["class_name"] is not None
        and row["visibility_token"] in visible_tokens
    }
    checks["cached_gt_ids_match"] = expected == set(cached)
    for row in projected_boxes:
        candidates = []
        area = row["original_width_px"] * row["original_height_px"]
        for other in projected_boxes:
            if other["camera_far_z_m"] >= row["camera_near_z_m"]:
                continue
            first, second = row["bbox"], other["bbox"]
            intersection = max(0, min(first[2], second[2]) - max(first[0], second[0])) * max(
                0, min(first[3], second[3]) - max(first[1], second[1])
            )
            if intersection > 0:
                candidates.append(
                    {
                        "annotation_id": other["annotation_id"],
                        "category": other["category"],
                        "bbox_cover_fraction": intersection / area,
                    }
                )
        row["potential_foreground_occluder"] = max(
            candidates, key=lambda r: r["bbox_cover_fraction"], default=None
        )
    if (
        not all(
            checks[key]
            for key in ["intrinsic_matches", "image_size_matches", "cached_gt_ids_match"]
        )
        or checks["camera_center_max_error_m"] > 1e-8
        or checks["corner_max_error_m"] > 1e-8
        or checks["cached_bbox_max_error_px"] > 1e-6
        or checks["cached_distance_max_error_m"] > 1e-8
        or checks["official_projection_max_error_px"] > 1e-6
        or checks["bbox_roundtrip_max_error_px"] > 1e-6
    ):
        raise ValueError("Geometry audit failed; do not infer detector failure")
    return rows, checks


def diagnose_matches(frame, rows, predictions, raw, threshold, confidence):
    """Functionality: Explain each original GT observation's unchanged class/IoU/distance outcome.
    Inputs: original frame/geometry,stored predictions,raw proposals,frozen thresholds.
    Outputs: per-GT evidence and matching funnel. Logic: original greedy assignment plus disjoint
    first-failure categories; raw confidence evidence never enters official assignment.
    Constraints: evidence of overlap does not prove a proposal represents that physical object.
    """
    geometry = {row["annotation_id"]: row for row in rows}
    matches = match_objects(frame["objects"], predictions, threshold)
    maximum = maximum_cardinality(frame["objects"], predictions, threshold)
    observations, class_pairs = [], 0
    max_error = 0.0
    for index, obj in enumerate(frame["objects"]):
        same = [
            (pi, pred, bbox_iou(obj["bbox"], pred["bbox"]))
            for pi, pred in enumerate(predictions)
            if pred["class_name"] == obj["class_name"]
        ]
        other = [
            (pi, pred, bbox_iou(obj["bbox"], pred["bbox"]))
            for pi, pred in enumerate(predictions)
            if pred["class_name"] != obj["class_name"]
        ]
        proposals = [
            (pred, bbox_iou(obj["bbox"], pred["bbox"]))
            for pred in raw
            if pred["valid_bbox"] and pred["class_name"] == obj["class_name"]
        ]
        best = max(same, key=lambda value: value[2], default=None)
        wrong = max(other, key=lambda value: value[2], default=None)
        raw_best = max(proposals, key=lambda value: value[1], default=None)
        raw_aligned = [p["confidence"] for p, overlap in proposals if overlap >= threshold]
        above_pairs = sum(value[2] >= threshold for value in same)
        class_pairs += above_pairs
        for _, prediction, iou in same + other:
            max_error = max(max_error, abs(iou - independent_iou(obj["bbox"], prediction["bbox"])))
        prediction = predictions[matches[index]] if index in matches else None
        if prediction is not None:
            reason = (
                "matched_valid_distance"
                if valid_distance(prediction.get("distance_m")) is not None
                else "matched_distance_unavailable"
            )
        elif above_pairs:
            reason = "one_to_one_assignment_competition"
        elif raw_aligned and max(raw_aligned) < confidence:
            reason = "same_class_iou_candidate_below_confidence"
        elif wrong is not None and wrong[2] >= threshold:
            reason = "wrong_class_prediction_at_required_iou"
        elif best is not None and best[2] > 0:
            reason = "kept_same_class_overlap_below_iou"
        elif raw_best is not None and raw_best[1] > 0:
            reason = "only_raw_same_class_overlap_below_iou"
        else:
            reason = "no_same_class_overlap_in_raw_topk"
        row = {
            **geometry[obj["annotation_id"]],
            "detection_matched": prediction is not None,
            "prediction_index": matches.get(index),
            "valid_distance": prediction is not None
            and valid_distance(prediction.get("distance_m")) is not None,
            "loss_reason": reason,
            "best_kept_same_class_iou": best[2] if best else 0.0,
            "confidence_at_best_kept_iou": best[1]["confidence"] if best else None,
            "best_wrong_class_iou": wrong[2] if wrong else 0.0,
            "best_wrong_class_label": wrong[1]["class_name"] if wrong else None,
            "best_raw_same_class_iou": raw_best[1] if raw_best else 0.0,
            "confidence_at_best_raw_iou": raw_best[0]["confidence"] if raw_best else None,
            "max_raw_confidence_at_required_iou": max(raw_aligned) if raw_aligned else None,
            "same_class_iou_candidate_count": above_pairs,
        }
        observations.append(row)
    kept = [pred for pred in raw if pred["confidence"] >= confidence and pred["valid_bbox"]]
    funnel = {
        "raw_topk_rows": len(raw),
        "raw_valid_bbox_rows": sum(p["valid_bbox"] for p in raw),
        "raw_class_counts": dict(Counter(p["class_name"] for p in raw)),
        "confidence_passing_rows": sum(p["confidence"] >= confidence for p in raw),
        "confidence_passing_valid_boxes": len(kept),
        "stored_prediction_count": len(predictions),
        "stored_class_counts": dict(Counter(p["class_name"] for p in predictions)),
        "supported_class_predictions": sum(
            p["class_name"] in {"car", "person"} for p in predictions
        ),
        "same_class_iou_candidate_pairs": class_pairs,
        "gt_with_same_class_iou_candidate": sum(
            r["same_class_iou_candidate_count"] > 0 for r in observations
        ),
        "one_to_one_matches": len(matches),
        "maximum_possible_matches": maximum,
        "valid_distances": sum(r["valid_distance"] for r in observations),
        "iou_arithmetic_max_error": max_error,
    }
    return observations, funnel


def summarize_rows(rows, policy):
    """Functionality: Preserve original denominators while describing exclusions and evidence.
    Inputs: per-GT rows,fixed policy. Outputs: coverage,reason counts and distribution summaries.
    Logic: use original assignments for every subset; never rematch after filtering.
    Constraints: visible/size subsets are diagnostics,not improved replacements for original scores.
    """
    matched = sum(row["detection_matched"] for row in rows)
    valid = sum(row["valid_distance"] for row in rows)
    selected = [row for row in rows if row["policy_eligible"]]
    valid_selected = sum(row["valid_distance"] for row in selected)
    unmatched = [row for row in rows if not row["detection_matched"]]
    sensitivity = []
    for height in policy["sensitivity_input_heights_px"]:
        subset = [row for row in rows if eligible(row, policy, height=height)[0]]
        count = sum(row["valid_distance"] for row in subset)
        sensitivity.append(
            {
                "min_input_height_px": height,
                "gt": len(subset),
                "valid": count,
                "coverage": count / len(subset) if subset else None,
            }
        )
    return {
        "original_gt": len(rows),
        "matched": matched,
        "valid_distance": valid,
        "original_gt_coverage": valid / len(rows) if rows else None,
        "policy_eligible_gt": len(selected),
        "policy_eligible_valid_distance": valid_selected,
        "policy_eligible_coverage": valid_selected / len(selected) if selected else None,
        "policy_exclusion_counts_nonexclusive": dict(
            Counter(reason for row in rows for reason in row["policy_exclusion_reasons"])
        ),
        "loss_reasons": dict(Counter(row["loss_reason"] for row in rows)),
        "unmatched_reasons": dict(Counter(row["loss_reason"] for row in unmatched)),
        "unmatched_eligible_reasons": dict(
            Counter(row["loss_reason"] for row in unmatched if row["policy_eligible"])
        ),
        "visibility_token_counts": dict(Counter(row["visibility_token"] for row in rows)),
        "distance_m": distribution([row["camera_z_m"] for row in rows], DISTANCE_EDGES),
        "input_height_px": distribution([row["input_height_px"] for row in rows], HEIGHT_EDGES),
        "input_width_px": distribution(
            [row["input_width_px"] for row in rows], (0, 4, 8, 16, 32, 64, 128, float("inf"))
        ),
        "input_area_px2": distribution(
            [row["input_width_px"] * row["input_height_px"] for row in rows],
            (0, 64, 256, 1024, 4096, 16384, float("inf")),
        ),
        "unmatched_distance_m": distribution(
            [row["camera_z_m"] for row in unmatched], DISTANCE_EDGES
        ),
        "unmatched_input_height_px": distribution(
            [row["input_height_px"] for row in unmatched], HEIGHT_EDGES
        ),
        "best_raw_aligned_confidence": distribution(
            [row["max_raw_confidence_at_required_iou"] for row in unmatched], CONFIDENCE_EDGES
        ),
        "fully_foreground_covered_bbox_proxy": sum(
            row.get("potential_foreground_occluder") is not None
            and row["potential_foreground_occluder"]["bbox_cover_fraction"] >= 0.9
            for row in rows
        ),
        "sensitivity": sensitivity,
    }


def diagnose(run_directory, output, policy_path):
    """Functionality: Produce a read-only six-frame coverage investigation with real raw replay.
    Inputs: completed original run,new sibling output,explicit diagnostic policy.
    Outputs: JSON/report.
    Logic: preserve original files/weights,serially capture native/E,verify exact saved predictions,
    cross-check geometry and audit all methods' original assignments.
    Constraints: no changes to model,confidence,IoU,raw annotations,CSV files or original scores.
    """
    from nuscenes.nuscenes import NuScenes

    from .diagnostic_render import write_diagnostics

    source, output = Path(run_directory).resolve(), Path(output).resolve()
    if output.exists() or output.is_relative_to(source):
        raise ValueError("Use a new diagnostic output outside the original results directory")
    before, protected = snapshot(source), source_snapshot()
    manifest = json.loads((source / "run_manifest.json").read_text(encoding="utf-8"))
    metrics = json.loads((source / "metrics.json").read_text(encoding="utf-8"))
    policy = json.loads(Path(policy_path).read_text(encoding="utf-8"))
    if manifest["status"] != "complete" or len(manifest["selected_frame_ids"]) != 6:
        raise ValueError("This diagnostic requires the completed selected six-frame run")
    config = manifest["configuration"]
    summary, full = load_records(config["processed"])
    indexed = {frame["frame_id"]: frame for frame in full}
    frames = [indexed[identity] for identity in manifest["selected_frame_ids"]]
    native_confidence = manifest["conditions"]["predicted"]["identities"]["a"]["confidence"]
    weights = {
        key: digest(config[key]) for key in ("detection_weights", "depth_weights", "e_checkpoint")
    }
    if weights != manifest["weight_sha256"]:
        raise ValueError("Model weight bytes no longer match the original run")
    output.mkdir(parents=True)
    options = {"configuration": config, "frames": frames, "raw_directory": summary["raw_directory"]}
    write_json(output / "capture-options.json", options)
    captures = {}
    for engine in ("native", "e"):
        logger.info("Observing original top-k outputs engine=%s frames=%d", engine, len(frames))
        path = output / "raw" / f"{engine}.jsonl"
        with (output / f"capture-{engine}.log").open("w", encoding="utf-8") as log:
            process = subprocess.run(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    "-m",
                    "model_tests.unified_evaluation.diagnostic_capture",
                    "--options",
                    str(output / "capture-options.json"),
                    "--engine",
                    engine,
                    "--output",
                    str(path),
                ],
                cwd=ROOT,
                env={**os.environ, "YOLO_OFFLINE": "1", "YOLO_AUTOINSTALL": "0"},
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        if process.returncode:
            raise RuntimeError(f"Raw capture failed: inspect capture-{engine}.log; no fallback")
        captures[engine] = {
            row["frame_id"]: row
            for row in (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
        }
    nusc = NuScenes(version=summary["version"], dataroot=summary["raw_directory"], verbose=False)
    original = {
        method: {
            row["frame_id"]: row
            for row in (
                json.loads(line)
                for line in (source / "predictions" / f"{method}.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            )
        }
        for method in ("a", "b", "c", "e")
    }
    all_geometry, geometry_checks, method_rows, method_funnels = [], [], {}, {}
    replay_checks = []
    for frame in frames:
        rows, checks = audit_geometry(
            nusc, frame, captures["native"][frame["frame_id"]], summary["near_plane_m"], policy
        )
        all_geometry.extend(rows)
        geometry_checks.append({"frame_id": frame["frame_id"], **checks})
        for method in ("a", "b", "c", "e"):
            capture = captures["e" if method == "e" else "native"][frame["frame_id"]]
            predictions = original[method][frame["frame_id"]]["objects"]
            replay = capture["postfilter_predictions"]
            if len(replay) != len(predictions) or any(
                a["class_name"] != b["class_name"] for a, b in zip(replay, predictions, strict=True)
            ):
                raise ValueError(
                    "Replay differs from saved detections; cannot attribute original losses"
                )
            bbox_error = max(
                (
                    float(np.max(np.abs(np.asarray(a["bbox"]) - b["bbox"])))
                    for a, b in zip(replay, predictions, strict=True)
                ),
                default=0,
            )
            score_error = max(
                (
                    abs(a["confidence"] - b["confidence"])
                    for a, b in zip(replay, predictions, strict=True)
                ),
                default=0,
            )
            if bbox_error > 1e-3 or score_error > 1e-5:
                raise ValueError(
                    "Replay numeric identity failed; original results remain untouched"
                )
            replay_checks.append(
                {
                    "frame_id": frame["frame_id"],
                    "method": method,
                    "bbox_max_error_px": bbox_error,
                    "confidence_max_error": score_error,
                }
            )
            observations, funnel = diagnose_matches(
                frame,
                rows,
                predictions,
                capture["raw_rows"],
                config["match_iou"],
                capture["confidence_threshold"],
            )
            geometry_checks[-1]["iou_arithmetic_max_error"] = max(
                geometry_checks[-1]["iou_arithmetic_max_error"], funnel["iou_arithmetic_max_error"]
            )
            if funnel["iou_arithmetic_max_error"] > 1e-12:
                raise ValueError("Independent IoU arithmetic audit failed")
            method_rows.setdefault(method, []).extend(observations)
            method_funnels.setdefault(method, []).append({"frame_id": frame["frame_id"], **funnel})
    results = {}
    for method, rows in method_rows.items():
        results[method] = {
            "overall": summarize_rows(rows, policy),
            "classes": {
                name: summarize_rows([r for r in rows if r["class_name"] == name], policy)
                for name in ("car", "person")
            },
            "funnel_per_frame": method_funnels[method],
        }
        overall = metrics["results"]["predicted"][method]["metrics"]["overall"]
        if (
            results[method]["overall"]["original_gt"] != overall["ground_truth_objects"]
            or results[method]["overall"]["matched"] != overall["matched_objects"]
            or results[method]["overall"]["valid_distance"] != overall["valid_predictions"]
        ):
            raise ValueError("Diagnostic assignment counts differ from original metrics")
    supported = [row for row in all_geometry if row["class_name"] is not None]
    report = {
        "status": "complete",
        "source_run": str(source),
        "selected_frames": 6,
        "confidence_threshold": native_confidence,
        "iou_threshold": config["match_iou"],
        "diagnostic_policy": policy,
        "raw_gt_counts": {
            "all_categories": len(all_geometry),
            "supported_categories": len(supported),
            "positive_camera_z_supported": sum(r["positive_camera_z"] for r in supported),
            "beyond_near_plane_supported": sum(r["beyond_near_plane"] for r in supported),
            "projected_into_image_supported": sum(r["bbox"] is not None for r in supported),
            "original_evaluation_gt": sum(r["original_evaluation_gt"] for r in supported),
            "policy_eligible_original_gt": sum(
                r.get("policy_eligible", False) for r in supported if r["original_evaluation_gt"]
            ),
        },
        "geometry_checks": geometry_checks,
        "replay_checks": replay_checks,
        "methods": results,
        "source_weights_sha256": weights,
        "diagnostic_modules_sha256": {
            name: digest(Path(__file__).with_name(name))
            for name in ("diagnostics.py", "diagnostic_capture.py", "diagnostic_render.py")
        },
        "policy_sha256": digest(policy_path),
        "original_manifest_sha256": digest(source / "run_manifest.json"),
        "limitations": [
            "Raw proposals mean e2e top300 outputs,not pre-head dense outputs",
            "Six-camera visibility tokens do not establish CAM_FRONT pixel visibility",
            "A/B/C share native replay; E eager/AMP has its own independent raw replay",
            "Eligibility subsets keep original assignments and do not alter official coverage",
            "Foreground cuboid bbox overlap is potential occlusion,not a segmentation mask",
            "Below-threshold or low-IoU candidates do not prove physical object identity",
        ],
    }
    for engine in ("native", "e"):
        proposals = [row for frame in captures[engine].values() for row in frame["raw_rows"]]
        report.setdefault("raw_distributions", {})[engine] = {
            name: distribution(
                [r["confidence"] for r in proposals if name == "all" or r["class_name"] == name],
                CONFIDENCE_EDGES,
            )
            for name in ("all", "car", "person")
        }
        method = "a" if engine == "native" else "e"
        kept = [obj for frame in original[method].values() for obj in frame["objects"]]
        report.setdefault("kept_confidence_distributions", {})[engine] = {
            name: distribution(
                [r["confidence"] for r in kept if name == "all" or r["class_name"] == name],
                CONFIDENCE_EDGES,
            )
            for name in ("all", "car", "person")
        }
    write_diagnostics(output, report, frames, all_geometry, method_rows, original, summary)
    report["preservation"] = {
        "original_results_unchanged": before == snapshot(source),
        "original_sources_unchanged": protected == source_snapshot(),
        "weights_unchanged": weights == {key: digest(config[key]) for key in weights},
    }
    write_json(output / "diagnostic.json", report)
    write_json(output / "preservation.json", {"input_sha256": before, **report["preservation"]})
    if not all(report["preservation"].values()):
        raise RuntimeError("Preservation check failed")
    return report


def main(argv=None):
    """Functionality: Run an explicit fixed-standard six-frame diagnosis in a new directory.
    Inputs: original run,new output,optional diagnostic policy. Outputs: diagnosis artifacts.
    Logic: original confidence/IoU/weights come solely from the stored manifest.
    Constraints: no options can lower detection/IoU thresholds or change training/model weights.
    """
    parser = argparse.ArgumentParser(description="Diagnose fixed six-frame nuScenes coverage")
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--policy", type=Path, default=Path(__file__).with_name("diagnostic-policy.json")
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    report = diagnose(args.run, args.output, args.policy)
    print(
        json.dumps(
            {
                "status": report["status"],
                "raw_gt_counts": report["raw_gt_counts"],
                "person": report["methods"]["a"]["classes"]["person"],
            },
            allow_nan=False,
        )
    )


if __name__ == "__main__":
    main()
