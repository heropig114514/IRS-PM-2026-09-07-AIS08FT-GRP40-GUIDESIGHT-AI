"""Responsibilities: Score center-z error, detection/distance coverage and paired observations.
Implementation: Reuse existing deterministic IoU matcher and DistanceMetrics with explicit bins.
Related Modules: object_distance.metrics; unified_evaluation.runner/reporting.
Declaration Index: valid_distance; score_frame; summarize; paired_scores.
Variable Index: BINS — half-open GT-z lower edges; final edge extends to infinity.
"""

import math

from object_distance.metrics import DistanceMetrics, match_objects

BINS = (0, 1, 3, 5, 10, 20)


def valid_distance(value):
    """Functionality: Normalize unavailable or invalid estimates to None.
    Inputs: scalar estimate. Outputs: positive finite float or None.
    Logic: reject bool/non-numeric/NaN/Inf/nonpositive. Constraints: no zero-error substitution.
    """
    return (
        float(value)
        if not isinstance(value, bool)
        and isinstance(value, int | float)
        and math.isfinite(value)
        and value > 0
        else None
    )


def score_frame(frame, predictions, *, bbox_mode, match_iou, depth_frame):
    """Functionality: Retain every GT denominator and annotation identity for paired comparison.
    Inputs: frame, canonical predictions,gt/predicted mode,IoU threshold and actual depth flag.
    Outputs: observations and unmatched prediction count. Logic: aligned slots or shared matcher.
    Constraints: GT slots must retain class/box identity; no independent favorable subset scoring.
    """
    ground_truth = frame["objects"]
    if bbox_mode == "gt":
        if len(predictions) != len(ground_truth) or any(
            gt["class_name"] != pred["class_name"] or gt["bbox"] != pred["bbox"]
            for gt, pred in zip(ground_truth, predictions, strict=True)
        ):
            raise ValueError("GT predictions must preserve aligned object slots")
        matches = dict(enumerate(range(len(ground_truth))))
    elif bbox_mode == "predicted":
        matches = match_objects(ground_truth, predictions, match_iou)
    else:
        raise ValueError("Unknown bbox mode")
    observations = []
    for index, gt in enumerate(ground_truth):
        target = valid_distance(gt["distance_gt_m"])
        if target is None:
            raise ValueError("Invalid ground truth distance")
        prediction = predictions[matches[index]] if index in matches else None
        observations.append(
            {
                "key": f"{frame['frame_id']}:{gt['annotation_id']}",
                "class_name": gt["class_name"],
                "target_m": target,
                "prediction_m": valid_distance(prediction.get("distance_m"))
                if prediction is not None
                else None,
                "matched": prediction is not None,
                "depth_frame": depth_frame,
            }
        )
    return observations, len(predictions) - len(matches)


def summarize(observations):
    """Functionality: Add match rate, distance validity and total coverage to reused error groups.
    Inputs: object observations. Outputs: overall/class/range error and coverage dictionaries.
    Logic: matched/GT,valid/matched,valid/GT; errors conditional on valid predictions only.
    Constraints: all empty denominators and empty error groups are null, including empty bins.
    """
    metrics = DistanceMetrics(BINS)
    for row in observations:
        metrics.add(row["target_m"], row["prediction_m"], row["class_name"])
    result = metrics.summary()
    for name, values in result.items():
        if name == "overall":
            selected = observations
        elif name.startswith("class:"):
            selected = [r for r in observations if r["class_name"] == name[6:]]
        else:
            lower, upper = name[6:-1].split("-")
            selected = [r for r in observations if float(lower) <= r["target_m"] < float(upper)]
        total, valid = len(selected), values["valid_predictions"]
        matched = sum(row["matched"] for row in selected)
        values.update(
            matched_objects=matched,
            detection_match_rate=matched / total if total else None,
            distance_validity_rate=valid / matched if matched else None,
            coverage=valid / total if total else None,
        )
    return result


def paired_scores(results, requested):
    """Functionality: Compare identical valid annotation IDs across every requested method.
    Inputs: method observation dictionaries and explicit requested method names. Outputs: report.
    Logic: require all methods completed and identical GT identity/value sets, then intersect valid
    predictions. Constraints: failures never reduce a four-method comparison to a hidden subset.
    """
    if set(results) != set(requested):
        return {
            "status": "unavailable",
            "reason": "Not all requested methods completed",
            "methods": requested,
            "common_valid_objects": None,
            "scores": None,
        }
    indexed = {method: {row["key"]: row for row in rows} for method, rows in results.items()}
    first = next(iter(indexed.values()))
    for values in indexed.values():
        if values.keys() != first.keys() or any(
            (row["target_m"], row["class_name"])
            != (first[key]["target_m"], first[key]["class_name"])
            for key, row in values.items()
        ):
            raise ValueError("Paired comparison requires identical ground-truth observations")
    common = set.intersection(
        *[
            {key for key, row in values.items() if valid_distance(row["prediction_m"]) is not None}
            for values in indexed.values()
        ]
    )
    return {
        "status": "complete",
        "methods": requested,
        "common_valid_objects": len(common),
        "ground_truth_objects": len(first),
        "coverage": len(common) / len(first) if first else None,
        "scores": {
            method: summarize([values[key] for key in sorted(common)])
            for method, values in indexed.items()
        },
    }
