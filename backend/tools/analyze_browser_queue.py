"""Responsibilities: Bound browser queue wait attributable to earlier resident pipeline work.
Implementation: Offline same-lease interval intersections with uncertain dequeue-to-worker offset;
strict evidence/source checks, complete row retention and exclusive-stage accounting.
Related Modules: realtime.resident_asgi/services/detector; tools.resident_graph_probe;
tools.benchmark_resident_browser; core.test_browser_queue.
Declaration Index: number; distribution; overlap; queue_bounds; validate_session; analyze_session;
file_hash; run; main.
Variable Index: ROOT — backend; STAGES — disjoint original-pipeline duration fields;
SEMANTIC_SOURCES — source identities required for clock/FIFO interpretation;
CLOCK_EPS_MS — floating-point identity check only, never a performance-gate tolerance.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGES = (
    "prepare_ms",
    "gpu_stage_ms",
    "decode_boxes_ms",
    "tracking_ms",
    "depth_predict_ms",
    "depth_fusion_ms",
    "risk_ms",
    "render_ms",
)
SEMANTIC_SOURCES = (
    "realtime/resident_asgi.py",
    "realtime/services.py",
    "realtime/detector.py",
    "tools/resident_graph_probe.py",
)
CLOCK_EPS_MS = 1e-5


def number(value):
    """Functionality: Reject absent, nonfinite or negative timing data.
    Inputs: one JSON value. Outputs: original number. Logic: booleans are not durations.
    Constraints: no default-zero substitution, clipping or sample removal.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Timing must be a finite number")
    if value < 0:
        raise ValueError("Timing must be nonnegative")
    return value


def distribution(values):
    """Functionality: Describe every supplied sample without trimming.
    Inputs: nonnegative numeric sequence. Outputs: count/min/mean/p50/p95/max or empty count.
    Logic: inclusive linear percentile interpolation. Constraints: no change to the 500ms gate.
    """
    ordered = sorted(number(value) for value in values)
    if not ordered:
        return {"count": 0}
    result = {
        "count": len(ordered),
        "min": ordered[0],
        "max": ordered[-1],
        "mean": sum(ordered) / len(ordered),
    }
    for label, fraction in (("p50", 0.5), ("p95", 0.95)):
        position = (len(ordered) - 1) * fraction
        left, right = math.floor(position), math.ceil(position)
        result[label] = ordered[left] + (ordered[right] - ordered[left]) * (position - left)
    return result


def overlap(start, end, interval):
    """Functionality: Measure one interval intersection in the same worker clock.
    Inputs: window endpoints and pipeline interval pair. Outputs: overlap milliseconds.
    Logic: intersect endpoints. Constraints: touching endpoints contribute zero duration.
    """
    return max(0.0, min(end, interval[1]) - max(start, interval[0]))


def queue_bounds(start, queue_ms, gap_ms, previous):
    """Functionality: Bound earlier pipeline occupancy during an observed FIFO queue wait.
    Inputs: current worker start S, wait Q, preceding pipeline gap G, ordered prior intervals.
    Outputs: occupancy bounds, possible predecessor ordinals and certainly enclosed ordinals.
    Logic: dequeue offset d lies in [0,G]; evaluate intersection sums for [S-d-Q,S-d] at
    endpoints and every piecewise-linear breakpoint. Entirely enclosed frames must fit every d.
    Constraints: caller validates nonoverlap/FIFO; no exact arrival-clock reconstruction or
    proportional stage allocation for boundary frames. Pipeline gaps include unmeasured work.
    """
    number(start), number(queue_ms), number(gap_ms)
    candidates = {0.0, gap_ms}
    for left, right in previous:
        candidates.update(
            value
            for value in (
                start - left,
                start - right,
                start - queue_ms - left,
                start - queue_ms - right,
            )
            if 0 <= value <= gap_ms
        )
    occupied = [
        sum(overlap(start - offset - queue_ms, start - offset, interval) for interval in previous)
        for offset in candidates
    ]
    lower, upper = min(occupied), max(occupied)
    possible = [
        index + 1
        for index, interval in enumerate(previous)
        if queue_ms > 0 and overlap(start - gap_ms - queue_ms, start, interval) > 0
    ]
    enclosed = [
        index + 1
        for index, (left, right) in enumerate(previous)
        if left >= start - queue_ms and right <= start - gap_ms
    ]
    return {
        "offset_bound_ms": [0.0, gap_ms],
        "queue_start_bound_ms": [start - gap_ms - queue_ms, start - queue_ms],
        "queue_end_bound_ms": [start - gap_ms, start],
        "prior_pipeline_busy_ms": [lower, upper],
        "prior_pipeline_busy_fraction": [lower / queue_ms, upper / queue_ms] if queue_ms else None,
        "possible_predecessor_ordinals": possible,
        "certain_full_predecessor_ordinals": enclosed,
    }


def validate_session(session):
    """Functionality: Require complete original-clock resident-v1 full-mode timing evidence.
    Inputs: saved browser session. Outputs: normalized original-pipeline intervals.
    Logic: verify order, counters, timestamps and both disjoint timing decompositions.
    Constraints: no partial-session inference, mixed clocks, rounded metrics or missing stages;
    floating-point identity tolerance never changes latency acceptance; overlapping-stage
    experiments are rejected even if their numbers happen to match a disjoint sum.
    """
    rows = session["rows"]
    if (
        session["contract"]["protocol"] != "resident-v1"
        or session["contract"]["mode"] != "full"
        or session["sent"] != 181
        or session["received"] != 181
        or len(rows) != 181
        or not 0 < number(session["max_inflight"]) <= 12
        or session.get("failure")
    ):
        raise ValueError("A complete 181-frame resident-v1 full session is required")
    origin = number(rows[0]["metadata"]["tracking_timestamp_s"])
    intervals = []
    for index, row in enumerate(rows):
        metadata = row["metadata"]
        if metadata.get("stage_timing_model", "disjoint") != "disjoint":
            raise ValueError("Queue stage attribution requires disjoint timing semantics")
        if (
            row["ordinal"] != index + 1
            or metadata["index"] != index
            or metadata["performance"]["total_frames"] != index + 1
            or metadata["performance"]["depth_model_calls"] != index + 1
            or metadata["tracking_status"] != "active"
            or metadata["depth_interval"] != 1
            or metadata["precision"] != "FP32"
        ):
            raise ValueError("Frame identity or original workload differs")
        start = (number(metadata["tracking_timestamp_s"]) - origin) * 1000
        elapsed = number(metadata.get("pipeline_server_ms", metadata["server_ms"]))
        if elapsed <= 0 or start < 0 or (intervals and start < intervals[-1][1] - CLOCK_EPS_MS):
            raise ValueError("Pipeline intervals overlap or worker clock went backwards")
        if not math.isclose(
            sum(number(metadata[key]) for key in STAGES), elapsed, rel_tol=0, abs_tol=CLOCK_EPS_MS
        ):
            raise ValueError("Exclusive stages do not sum to original pipeline time")
        if number(metadata["server_ms"]) < elapsed - CLOCK_EPS_MS:
            raise ValueError("Wrapper time is shorter than original pipeline time")
        number(metadata["resident_queue_ms"])
        total = number(row["captureToDecodedMs"])
        parts = sum(
            number(row[key]) for key in ("captureMs", "transportMs", "renderQueueMs", "decodeMs")
        )
        if not math.isclose(parts, total, rel_tol=0, abs_tol=CLOCK_EPS_MS):
            raise ValueError("Browser latency components disagree")
        intervals.append((start, start + elapsed))
    return intervals


def analyze_session(session):
    """Functionality: Attribute every steady wait without inventing missing event timestamps.
    Inputs: complete session. Outputs: per-frame evidence and same-session summary.
    Logic: conservative prior-work bounds, certain whole-frame stage sums and exact browser
    decomposition; cold frame is retained separately with no guessed predecessor bound.
    Constraints: stage totals use each steady request once; overlapping queue-window summaries
    are exposure descriptions, never a sum of unique GPU work or pure-kernel duration.
    """
    intervals = validate_session(session)
    evidence, rows = [], session["rows"]
    for index, row in enumerate(rows):
        metadata = row["metadata"]
        record = {
            "ordinal": index + 1,
            "cold_frame": index == 0,
            "worker_start_ms": intervals[index][0],
            "pipeline_end_ms": intervals[index][1],
            "capture_to_decoded_ms": row["captureToDecodedMs"],
            "over_500ms": row["captureToDecodedMs"] > 500,
            "stages_ms": {key: metadata[key] for key in STAGES},
            "latency_parts_ms": {
                "capture": row["captureMs"],
                "queue": metadata["resident_queue_ms"],
                "server": metadata["server_ms"],
                "transport_residual": row["transportMs"]
                - metadata["resident_queue_ms"]
                - metadata["server_ms"],
                "render_queue": row["renderQueueMs"],
                "decode": row["decodeMs"],
            },
        }
        if index:
            bounds = queue_bounds(
                intervals[index][0],
                metadata["resident_queue_ms"],
                max(0, intervals[index][0] - intervals[index - 1][1]),
                intervals[:index],
            )
            record.update(bounds)
            record["certain_predecessor_stages_ms"] = {
                key: sum(
                    rows[ordinal - 1]["metadata"][key]
                    for ordinal in bounds["certain_full_predecessor_ordinals"]
                )
                for key in STAGES
            }
            if sum(record["certain_predecessor_stages_ms"].values()) > (
                bounds["prior_pipeline_busy_ms"][0] + CLOCK_EPS_MS
            ):
                raise ValueError("Certain predecessor work exceeds conservative overlap")
        else:
            record["offset_bound_ms"] = None
        evidence.append(record)
    steady = evidence[1:]
    slow = [row for row in steady if row["over_500ms"]]
    stage_totals = {key: sum(row["stages_ms"][key] for row in steady) for key in STAGES}
    pipeline_total = sum(stage_totals.values())
    summary = {
        "frames": len(evidence),
        "steady_frames": len(steady),
        "slow_steady_frames": len(slow),
        "cold_capture_to_decoded_ms": evidence[0]["capture_to_decoded_ms"],
        "latency_ms": distribution([row["capture_to_decoded_ms"] for row in steady]),
        "slow_queue_busy_lower_fraction": distribution(
            [
                row["prior_pipeline_busy_fraction"][0]
                for row in slow
                if row["prior_pipeline_busy_fraction"] is not None
            ]
        ),
        "slow_dispatch_offset_upper_ms": distribution([row["offset_bound_ms"][1] for row in slow]),
        "slow_certain_predecessor_count": distribution(
            [len(row["certain_full_predecessor_ordinals"]) for row in slow]
        ),
        "unique_steady_stage_totals_ms": stage_totals,
        "unique_steady_stage_fraction": {
            key: value / pipeline_total for key, value in stage_totals.items()
        },
        "stage_ms": {
            key: distribution([row["stages_ms"][key] for row in steady]) for key in STAGES
        },
        "worst_frame": max(steady, key=lambda row: row["capture_to_decoded_ms"]),
    }
    return evidence, summary


def file_hash(path):
    """Functionality: Identify the complete source bytes. Inputs: local path. Outputs: SHA256.
    Logic: read-only digest. Constraints: not a signature or an accuracy assertion.
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(input_root, output):
    """Functionality: Analyze an explicit saved matrix while preserving incomplete runs.
    Inputs: moving-browser matrix root and new output directory. Outputs: JSON/JSONL evidence.
    Logic: verify manifest hashes and clock/FIFO implementations before interpreting each report;
    retain failed-run identity separately, and fail the analysis on malformed completed evidence.
    Constraints: offline only, no model or browser execution, no overwritten reports or retries.
    """
    input_root, output = Path(input_root).resolve(), Path(output).resolve()
    output.mkdir()
    result = {
        "status": "running",
        "source_manifest": str(input_root / "summary.json"),
        "analysis_sha256": file_hash(Path(__file__)),
        "runs": [],
        "sessions": [],
        "timing_basis": "Same-lease worker clock; dequeue offset bounded by prior pipeline gap",
        "limits": [
            "No exact queue arrival or worker-dispatch timestamp is inferred.",
            "Only certainly enclosed whole predecessor frames receive stage attribution.",
            "Transport residual includes validation/serialization/scheduling as well as network.",
            "Host pipeline intervals include device waits; not pure GPU kernel occupancy.",
            "Cold frames and failed source runs never enter steady acceptance statistics.",
        ],
    }
    try:
        result["source_manifest_sha256"] = file_hash(input_root / "summary.json")
        manifest = json.loads((input_root / "summary.json").read_text(encoding="utf-8"))
        seen = set()
        for item in manifest["runs"]:
            path = (input_root / item["report"]).resolve()
            if not path.is_relative_to(input_root) or file_hash(path) != item["report_sha256"]:
                raise ValueError("Report path or SHA256 differs from the matrix manifest")
            if path in seen:
                raise ValueError("Duplicate report in matrix manifest")
            seen.add(path)
            report = json.loads(path.read_text(encoding="utf-8"))
            if report["status"] != item["status"] or report["status"] not in {
                "completed",
                "failed",
            }:
                raise ValueError("Report status differs from the matrix manifest")
            for source in SEMANTIC_SOURCES:
                if report["source_code_sha256"][source] != file_hash(ROOT / source):
                    raise ValueError(f"Timing semantics source differs: {source}")
            run_info = {
                "report": str(path),
                "sha256": item["report_sha256"],
                "status": report["status"],
                "included": report["status"] == "completed",
            }
            result["runs"].append(run_info)
            if report["status"] != "completed":
                run_info["failure"] = report.get("failure")
                run_info["source_failure"] = report.get("page_state", {}).get("source_failure")
                continue
            conditions = report["conditions"]
            if len(report["sessions"]) != 2:
                raise ValueError("Completed browser run requires two sessions")
            if (
                conditions["input_fps"],
                conditions["max_inflight"],
                conditions["frames_per_session"],
                conditions["mode"],
                conditions["source_kind"],
            ) != (30, 12, 181, "full", "moving_video"):
                raise ValueError("Matrix workload differs from the original browser conditions")
            for index, session in enumerate(report["sessions"]):
                evidence, summary = analyze_session(session)
                name = f"run-{len(result['runs']):02d}-session-{index}.jsonl"
                (output / name).write_text(
                    "".join(json.dumps(row, allow_nan=False) + "\n" for row in evidence),
                    encoding="utf-8",
                    newline="\n",
                )
                result["sessions"].append(
                    {
                        "report": str(path),
                        "session": index,
                        "rows": name,
                        "depth_graph": conditions["depth_graph_experiment"],
                        **summary,
                    }
                )
        if not result["sessions"]:
            raise ValueError("No completed sessions available for queue attribution")
        result["status"] = "completed"
    except Exception as error:
        result["status"] = "failed"
        result["failure"] = {"type": type(error).__name__, "detail": str(error)}
        raise
    finally:
        (output / "report.json").write_text(
            json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8", newline="\n"
        )
    return result


def main():
    """Functionality: Run one explicit offline queue analysis.
    Inputs: --input-root and --output. Outputs: saved evidence and concise status.
    Logic: parse paths and invoke strict analyzer. Constraints: no fallback or overwrite.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.input_root, args.output)
    print(json.dumps({"status": report["status"], "sessions": len(report["sessions"])}))


if __name__ == "__main__":
    main()
