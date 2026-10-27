"""Responsibilities: Report four fixed distance bands and a user-defined weighted MAE.
Implementation: Reuse collected GT observations and existing error/coverage semantics.
Related Modules: object_distance.metrics/evaluate; checkpoint training remains unchanged.
Declaration Index: distance_report; write_distance_report.
Variable Index: REPORT_BINS_M and REPORT_WEIGHTS define the fixed reporting protocol.
"""

from pathlib import Path

from object_distance.metrics import DistanceMetrics

REPORT_BINS_M = (0, 10, 20, 40)
REPORT_WEIGHTS = (0.4, 0.3, 0.2, 0.1)


def distance_report(metrics):
    """Functionality: Calculate MAE/AbsRel/coverage and fixed-weight MAE for four GT bands.
    Inputs: DistanceMetrics observations. Outputs: JSON-compatible protocol and metrics.
    Logic: regroup observations at 0/10/20/40 meters; last band includes every farther target.
    Constraints: missing predictions retain their GT denominator; any band without a valid
    prediction makes Weighted MAE unavailable. Never renormalize weights or use zero errors.
    """
    collector = DistanceMetrics(REPORT_BINS_M)
    collector.values = list(metrics.values)
    grouped = collector.summary()
    bands = []
    for index, (lower, weight) in enumerate(zip(REPORT_BINS_M, REPORT_WEIGHTS, strict=True)):
        upper = REPORT_BINS_M[index + 1] if index + 1 < len(REPORT_BINS_M) else None
        values = grouped[f"range:{lower}-{upper if upper is not None else 'inf'}m"]
        bands.append(
            {
                "lower_m": lower,
                "upper_m": upper,
                "weight": weight,
                **values,
                "coverage": values["valid_predictions"] / values["ground_truth_objects"]
                if values["ground_truth_objects"]
                else None,
            }
        )
    missing = [index + 1 for index, band in enumerate(bands) if band["mae_m"] is None]
    return {
        "bands": bands,
        "weighted_mae_m": sum(band["weight"] * band["mae_m"] for band in bands)
        if not missing
        else None,
        "unavailable_bands": missing,
        "formula": "0.4*MAE[0,10) + 0.3*MAE[10,20) + 0.2*MAE[20,40) + 0.1*MAE[40,inf)",
        "grouping": "ground_truth_distance_m; lower inclusive, upper exclusive",
        "error_scope": "valid matched predictions only; always report coverage alongside errors",
        "abs_rel_definition": "mean(abs(prediction_m-target_m)/target_m); dimensionless",
        "empty_band_policy": "weighted_mae_m=null; fixed weights are never renormalized",
    }


def write_distance_report(output, mode, report):
    """Functionality: Export a readable distance table alongside the complete evaluation JSON.
    Inputs: owned output directory, mode name and evaluation report. Outputs: mode_report.md.
    Logic: show target/split provenance, original overall MAE and four-band weighted result.
    Constraints: this report does not rename validation as independent test performance.
    """
    detail = report["distance_report"]
    overall = report["metrics"]["overall"]
    lines = [
        f"# Object distance evaluation: {mode}",
        "",
        f"- Split: {report.get('split', 'unspecified')}",
        f"- Target: {report['distance_target']}",
        f"- Images: {report['evaluated_images']}",
        f"- Split SHA256: {report['split_manifest_sha256']}",
        f"- Overall MAE (m): {overall['mae_m']}",
        f"- Overall AbsRel: {overall['abs_rel']}",
        f"- Valid predictions / GT: {overall['valid_predictions']} / "
        f"{overall['ground_truth_objects']}",
        f"- Weighted MAE (m): {detail['weighted_mae_m']}",
        "",
        "| GT distance (m) | Weight | GT | Valid | Coverage | MAE (m) | AbsRel |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for band in detail["bands"]:
        upper = band["upper_m"] if band["upper_m"] is not None else "inf"
        coverage = f"{band['coverage']:.2%}" if band["coverage"] is not None else "N/A"
        mae = f"{band['mae_m']:.6f}" if band["mae_m"] is not None else "N/A"
        relative = f"{band['abs_rel']:.6f}" if band["abs_rel"] is not None else "N/A"
        lines.append(
            f"| [{band['lower_m']}, {upper}) | {band['weight']} | "
            f"{band['ground_truth_objects']} | {band['valid_predictions']} | "
            f"{coverage} | {mae} | {relative} |"
        )
    lines.extend(
        [
            "",
            detail["formula"],
            "",
            "Errors use valid predictions only. AbsRel is dimensionless. Missing predictions "
            "remain in the coverage denominator; no band is silently dropped or assigned zero.",
            "",
            "GT-box evaluation measures regression with supplied boxes. Predicted-box evaluation "
            "includes detection/matching coverage. Validation used for checkpoint selection is "
            "not an independent final test set.",
        ]
    )
    (Path(output) / f"{mode}_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
