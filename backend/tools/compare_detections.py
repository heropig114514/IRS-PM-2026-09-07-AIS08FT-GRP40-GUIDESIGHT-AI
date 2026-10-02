"""Responsibilities: Compare output consistency of explicitly supplied detection reports.
Implementation: Same-class greedy matching at IoU >= 0.5 and raw-output fingerprint comparison.
Related Modules: tools.benchmark_space; not an accuracy evaluator or COCO mAP implementation.
Declaration Index: iou; compare; main.
Variable Index: None; CLI reference/reports/output paths define the explicit comparison set.
"""

import argparse
import json
from pathlib import Path


def iou(a, b):
    """Functionality: Measure overlap of valid xyxy boxes. Inputs: a/b nondegenerate rectangles.
    Outputs: intersection/union; Logic: clip intersection.
    Constraints: caller supplies decoded boxes.
    """
    x, y = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[2], b[2]), min(a[3], b[3])
    overlap = max(0, right - x) * max(0, bottom - y)
    return overlap / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - overlap)


def compare(reference, result):
    """Functionality: Compare one report's outputs.
    Inputs: reference/result evaluation dictionaries.
    Outputs: matches/missing/confidence differences and bit-equal frame count.
    Logic: descending reference score, match same class at IoU >= 0.5 without reusing detections.
    Constraints: reference is FP32 prediction, not ground truth; no recall, precision or mAP claims.
    """
    matched, total, exact = 0, 0, 0
    differences, missing = [], []
    if set(reference) != set(result):
        raise ValueError("Evaluation fixture sets differ")
    for name, sample in reference.items():
        candidate = result[name]
        exact += sample["raw_sha256"] == candidate["raw_sha256"]
        remaining = list(candidate["boxes"])
        for box in sorted(sample["boxes"], key=lambda item: item["score"], reverse=True):
            total += 1
            pairs = [
                (iou(box["xyxy"], item["xyxy"]), index)
                for index, item in enumerate(remaining)
                if item["class_id"] == box["class_id"]
            ]
            best = max(pairs, default=(0, -1))
            if best[0] >= 0.5:
                partner = remaining.pop(best[1])
                matched += 1
                differences.append(abs(partner["score"] - box["score"]))
            else:
                missing.append({"image": name, "reference_label": box["label"]})
    return {
        "reference_matches_iou_0_5": matched,
        "reference_total": total,
        "missing": missing,
        "bit_identical_frames": exact,
        "evaluation_frames": len(reference),
        "mean_confidence_abs_difference": sum(differences) / len(differences)
        if differences
        else None,
    }


def main():
    """Functionality: Save explicit output comparisons. Inputs: CLI reference/reports/output paths.
    Outputs: JSON artifact; Logic: read each named report and compare.
    Constraints: no inferred inputs.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--reports", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    reference = json.loads(args.reference.read_text(encoding="utf-8"))["evaluation"]
    summary = {
        path.stem: compare(reference, json.loads(path.read_text(encoding="utf-8"))["evaluation"])
        for path in args.reports
    }
    args.output.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
