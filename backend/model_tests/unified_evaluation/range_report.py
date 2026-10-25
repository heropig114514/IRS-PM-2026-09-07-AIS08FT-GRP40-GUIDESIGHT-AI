"""Responsibilities: Recompute near-distance errors from saved evaluation observations.
Implementation: Reuse DistanceMetrics; group by GT meters without rerunning models or matching.
Related Modules: object_distance.metrics; unified_evaluation.scoring/reporting.
Declaration Index: summarize_ranges; export_report; main.
Variable Index: EDGES defines half-open requested GT distance intervals in meters.
"""

import argparse
import hashlib
import json
from pathlib import Path

from object_distance.metrics import DistanceMetrics

from .reporting import write_csv
from .scoring import valid_distance

EDGES = (0, 5, 10, 20)


def summarize_ranges(observations):
    """Functionality: Calculate conditional errors and all-GT coverage for three near bands.
    Inputs: original per-GT observations. Outputs: band rows and accounted observation counts.
    Logic: validate identities/targets, retain misses, group by GT rather than predicted distance.
    Constraints: no rematching, filtering by class, zero-error substitution or weight fitting.
    """
    metrics = DistanceMetrics(EDGES)
    seen = set()
    for row in observations:
        key = row["key"]
        if key in seen:
            raise ValueError(f"Duplicate GT observation: {key}")
        seen.add(key)
        target = valid_distance(row["target_m"])
        prediction = valid_distance(row["prediction_m"])
        if target is None or type(row["matched"]) is not bool:
            raise ValueError(f"Invalid GT distance or match flag: {key}")
        if not row["matched"] and prediction is not None:
            raise ValueError(f"Unmatched GT cannot have a distance prediction: {key}")
        metrics.add(target, prediction, row["class_name"])
    summary = metrics.summary()
    bands = []
    for lower, upper in zip(EDGES[:-1], EDGES[1:], strict=True):
        selected = [r for r in observations if lower <= r["target_m"] < upper]
        scores = summary[f"range:{lower}-{upper}m"]
        total, valid = scores["ground_truth_objects"], scores["valid_predictions"]
        bands.append(
            {
                "gt_range_m": f"[{lower}, {upper})",
                "gt_objects": total,
                "matched_objects": sum(r["matched"] for r in selected),
                "valid_predictions": valid,
                "coverage": valid / total if total else None,
                "mae_m": scores["mae_m"],
                "rmse_m": scores["rmse_m"],
                "abs_rel": scores["abs_rel"],
            }
        )
    return {
        "total_gt_objects": len(observations),
        "gt_objects_outside_requested_ranges": sum(
            r["target_m"] >= EDGES[-1] for r in observations
        ),
        "bands": bands,
    }


def export_report(run, method, output):
    """Functionality: Export reproducible near-band statistics beside an existing experiment.
    Inputs: run directory, lower-case method and fresh output directory. Outputs: JSON/CSV report.
    Logic: require a completed pass; hash saved inputs and preserve the original target definition.
    Constraints: read-only source access; an existing output directory is never overwritten.
    """
    source = Path(run).resolve() / "passes" / f"{method}-predicted"
    result_bytes = (source / "result.json").read_bytes()
    result = json.loads(result_bytes)
    if result["status"] != "complete" or result["method"] != method:
        raise ValueError("Require a completed pass for the requested method")
    data = (source / "observations.jsonl").read_bytes()
    observations = [json.loads(line) for line in data.decode("utf-8").splitlines()]
    summary = summarize_ranges(observations)
    if summary["total_gt_objects"] != result["metrics"]["overall"]["ground_truth_objects"]:
        raise ValueError("Saved GT count disagrees with completed pass")
    report = {
        "method": method.upper(),
        "source_run": str(Path(run).resolve()),
        "source_observations_sha256": hashlib.sha256(data).hexdigest(),
        "source_result_sha256": hashlib.sha256(result_bytes).hexdigest(),
        "distance_target": result["distance_target"],
        "split": result["split"],
        "scope": "all_GT_within_each_requested_range",
        "protocol": "GT-distance half-open bands; errors use matched positive finite predictions; "
        "coverage includes missed GT; absent valid predictions produce null errors. "
        "GT at or above 20 meters is counted outside these bands, not discarded from source data.",
        **summary,
    }
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    (output / "metrics.json").write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    rows = [{"method": method.upper(), **band} for band in report["bands"]]
    write_csv(output / "distance_ranges.csv", list(rows[0]), rows)
    return report


def main(argv=None):
    """Functionality: Expose saved-output range evaluation without model inference.
    Inputs: --run, --method and --output. Outputs: report files and printed band JSON.
    Logic: delegate to validated export. Constraints: execute from backend; no GPU required.
    """
    parser = argparse.ArgumentParser(description="Report GT ranges 0-5, 5-10 and 10-20 meters")
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--method", choices=list("abce"), default="a")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = export_report(args.run, args.method, args.output)
    print(json.dumps(report["bands"], indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
