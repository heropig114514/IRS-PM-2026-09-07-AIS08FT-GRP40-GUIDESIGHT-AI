"""Responsibilities: Evaluate camera-z errors and class/IoU object correspondence transparently.
Implementation: Valid-prediction MAE/RMSE/AbsRel with all-GT missing coverage; greedy one-to-one
IoU.
Related Modules: object_distance.evaluate/train; comparable prediction files from other strategies.
Declaration Index: bbox_iou; match_objects; DistanceMetrics; DistanceMetrics.__init__;
DistanceMetrics.add; DistanceMetrics.summary.
Variable Index: None. Unmatched detections are counted separately, not claimed as official KITTI AP.
"""

import math

import numpy as np


def bbox_iou(first, second):
    """Functionality: Compute continuous xyxy IoU without pixel-inclusive offsets.
    Inputs: two valid boxes. Outputs:0..1 float. Logic: intersection divided by positive union.
    Constraints: touching/empty regions have zero IoU; no coordinate rescaling here.
    """
    area = max(0.0, min(first[2], second[2]) - max(first[0], second[0])) * max(
        0.0, min(first[3], second[3]) - max(first[1], second[1])
    )
    union = (
        (first[2] - first[0]) * (first[3] - first[1])
        + (second[2] - second[0]) * (second[3] - second[1])
        - area
    )
    return area / union if union > 0 else 0.0


def match_objects(ground_truth, predictions, threshold=0.5):
    """Functionality: Associate predicted boxes to GT once using class and IoU.
    Inputs: canonical class_name/bbox lists and IoU threshold. Outputs: GT-index ->
    prediction-index.
    Logic: greedily accept highest-IoU eligible pairs with deterministic index tie-breaking.
    Constraints: a prediction never satisfies multiple GTs; unmatched GTs count as missing distance.
    """
    candidates = []
    for gi, gt in enumerate(ground_truth):
        for pi, prediction in enumerate(predictions):
            if gt["class_name"] == prediction["class_name"]:
                overlap = bbox_iou(gt["bbox"], prediction["bbox"])
                if overlap >= threshold:
                    candidates.append((-overlap, gi, pi))
    assigned, used = {}, set()
    for _, gi, pi in sorted(candidates):
        if gi not in assigned and pi not in used:
            assigned[gi] = pi
            used.add(pi)
    return assigned


class DistanceMetrics:
    """Functionality: Separate valid regression accuracy from missing-prediction coverage.
    Logic: errors use valid positive predictions only; missing ratio denominator is all GT objects.
    Constraints: MAE/RMSE are camera-z errors, not nearest-surface distance or risk validation.
    """

    def __init__(self, bins=(0, 10, 20, 40, 80)):
        """Functionality: Initialize explicit distance intervals and object observations.
        Inputs: lower bin edges. Outputs: empty collector. Logic: retain scalar tuples.
        Constraints: offline evaluation only; no unbounded history in production inference.
        """
        self.bins, self.values = tuple(bins), []

    def add(self, target, prediction, class_name):
        """Functionality: Record one GT reference depth and nullable corresponding prediction.
        Inputs: positive target meters, nullable meters, canonical class. Outputs: None.
        Logic: invalid predictions count missing; reject invalid GT instead of distorting metrics.
        Constraints: unsupported/unmatched objects need explicit None from correspondence logic.
        """
        if isinstance(target, bool) or not math.isfinite(target) or target <= 0:
            raise ValueError("Ground-truth z must be finite and positive")
        valid = (
            not isinstance(prediction, bool)
            and isinstance(prediction, int | float)
            and math.isfinite(prediction)
            and prediction > 0
        )
        self.values.append((target, prediction if valid else None, class_name))

    def summary(self):
        """Functionality: Return overall/per-class/per-distance regression errors and coverage.
        Inputs: collected objects. Outputs: finite JSON-compatible metrics with null empty errors.
        Logic: same error/coverage definition for each subset; final bin extends to infinity.
        Constraints: distances partition by GT z; missing predictions are never silently discarded.
        """
        groups = {"overall": self.values}
        groups.update(
            {
                f"class:{name}": [v for v in self.values if v[2] == name]
                for name in sorted({v[2] for v in self.values})
            }
        )
        for i, lower in enumerate(self.bins):
            upper = self.bins[i + 1] if i + 1 < len(self.bins) else float("inf")
            groups[f"range:{lower}-{upper}m"] = [v for v in self.values if lower <= v[0] < upper]
        result = {}
        for name, values in groups.items():
            valid = [(target, pred) for target, pred, _ in values if pred is not None]
            errors = np.asarray([abs(pred - target) for target, pred in valid], dtype=np.float64)
            result[name] = {
                "ground_truth_objects": len(values),
                "valid_predictions": len(valid),
                "missing_predictions": len(values) - len(valid),
                "missing_prediction_ratio": (len(values) - len(valid)) / len(values)
                if values
                else None,
                "mae_m": float(errors.mean()) if valid else None,
                "rmse_m": float(np.sqrt(np.mean(errors**2))) if valid else None,
                "abs_rel": float(np.mean([abs(pred - gt) / gt for gt, pred in valid]))
                if valid
                else None,
            }
        return result
