"""Responsibilities: Protect conservative FIFO attribution and saved-report identity checks.
Implementation: Analytical interval cases and deterministic synthetic complete-session reports.
Related Modules: tools.analyze_browser_queue; realtime.resident_asgi/detector.
Declaration Index: make_session; write_matrix; BrowserQueueTests;
BrowserQueueTests.test_interior_extrema; BrowserQueueTests.test_uncertain_offset;
BrowserQueueTests.test_certain_predecessors; BrowserQueueTests.test_cold_wrapper_gap;
BrowserQueueTests.test_invalid_evidence; BrowserQueueTests.test_gate_has_no_clock_tolerance;
BrowserQueueTests.test_report_retention; BrowserQueueTests.test_matrix_identity_rejection.
Variable Index: None.
"""

import copy
import json
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from tools.analyze_browser_queue import (
    ROOT,
    SEMANTIC_SOURCES,
    STAGES,
    analyze_session,
    file_hash,
    overlap,
    queue_bounds,
    run,
    validate_session,
)


def make_session():
    """Functionality: Build deterministic timing evidence without running any inference.
    Inputs: None. Outputs: complete session with 50ms pipelines and 1ms inter-pipeline gaps.
    Logic: ordered counters and disjoint stage totals; queue grows then remains bounded.
    Constraints: synthetic clocks prove analysis semantics, never hardware speed or model equality.
    """
    rows = []
    stages = dict(zip(STAGES, (6, 10, 0.5, 8, 10, 6, 0.5, 9), strict=True))
    for index in range(181):
        queue = min(max(0, index - 1) * 10, 500)
        rows.append(
            {
                "ordinal": index + 1,
                "captureToDecodedMs": 60 + queue,
                "captureMs": 2,
                "transportMs": 53 + queue,
                "renderQueueMs": 1,
                "decodeMs": 4,
                "metadata": {
                    **stages,
                    "index": index,
                    "tracking_timestamp_s": 5 + index * 0.051,
                    "server_ms": 50,
                    "resident_queue_ms": queue,
                    "performance": {"total_frames": index + 1, "depth_model_calls": index + 1},
                    "tracking_status": "active",
                    "depth_interval": 1,
                    "precision": "FP32",
                },
            }
        )
    return {
        "contract": {"protocol": "resident-v1", "mode": "full"},
        "sent": 181,
        "received": 181,
        "max_inflight": 12,
        "failure": None,
        "rows": rows,
    }


def write_matrix(directory):
    """Functionality: Save one valid synthetic run and one retained failed-source run.
    Inputs: temporary root. Outputs: new input folder and manifest dictionary.
    Logic: use actual clock/FIFO source hashes and complete report digests.
    Constraints: no production source edits, GPU execution or replacement of real evidence.
    """
    source = directory / "input"
    source.mkdir()
    identity = {name: file_hash(ROOT / name) for name in SEMANTIC_SOURCES}
    complete = {
        "status": "completed",
        "source_code_sha256": identity,
        "conditions": {
            "input_fps": 30,
            "max_inflight": 12,
            "frames_per_session": 181,
            "mode": "full",
            "source_kind": "moving_video",
            "depth_graph_experiment": False,
        },
        "sessions": [make_session(), make_session()],
    }
    failure = {
        "status": "failed",
        "source_code_sha256": identity,
        "failure": {"type": "Error", "detail": "source ended"},
        "page_state": {"source_failure": "source ended"},
    }
    manifest = {"runs": []}
    for name, report in (("complete", complete), ("failure", failure)):
        path = source / f"{name}.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        manifest["runs"].append(
            {"report": path.name, "report_sha256": file_hash(path), "status": report["status"]}
        )
    (source / "summary.json").write_text(json.dumps(manifest), encoding="utf-8")
    return source, manifest


class BrowserQueueTests(SimpleTestCase):
    """Functionality: Require bounded conclusions from incomplete event-time information.
    Logic: analytic extrema, source identity and malformed timing tests.
    Constraints: no synthetic result serves as physical-GPU validation.
    """

    def test_interior_extrema(self):
        """Functionality: Find maximum overlap that neither offset endpoint reveals.
        Inputs: one interior pipeline interval and movable queue window. Outputs: [0,5] bound.
        Logic: extrema require alignment breakpoints. Constraints: no sampled-offset approximation.
        """
        bounds = queue_bounds(40, 5, 40, [(15, 20)])
        self.assertEqual(bounds["prior_pipeline_busy_ms"], [0, 5])
        self.assertEqual(bounds["certain_full_predecessor_ordinals"], [])
        self.assertEqual(queue_bounds(40, 0, 40, [(15, 20)])["possible_predecessor_ordinals"], [])

    def test_uncertain_offset(self):
        """Functionality: Keep the actual queue coverage within the derived bounds.
        Inputs: disjoint intervals with gaps and known candidate offsets. Outputs: bound checks.
        Logic: independently intersect actual windows throughout the allowed offset interval.
        Constraints: intervals represent host pipeline occupancy, not CUDA kernel occupancy.
        """
        previous = [(0, 11), (13, 22), (25, 34)]
        bounds = queue_bounds(40, 21, 6, previous)
        for offset in (0, 0.1, 1, 1.5, 2.7, 3, 5.5, 6):
            actual = sum(overlap(40 - offset - 21, 40 - offset, interval) for interval in previous)
            self.assertLessEqual(bounds["prior_pipeline_busy_ms"][0], actual)
            self.assertGreaterEqual(bounds["prior_pipeline_busy_ms"][1], actual)
        self.assertEqual(bounds["certain_full_predecessor_ordinals"], [3])

    def test_certain_predecessors(self):
        """Functionality: Assign full stages only to certainly enclosed predecessor frames.
        Inputs: complete synthetic stream. Outputs: exact whole-frame sums and all 181 rows.
        Logic: boundary predecessor may contribute busy time but not fabricated stage proportions.
        Constraints: no dropped first frame or double-counted unique stage totals.
        """
        evidence, summary = analyze_session(make_session())
        row = evidence[-1]
        self.assertEqual(len(evidence), 181)
        self.assertEqual(len(row["possible_predecessor_ordinals"]), 10)
        self.assertEqual(len(row["certain_full_predecessor_ordinals"]), 9)
        self.assertEqual(sum(row["certain_predecessor_stages_ms"].values()), 450)
        self.assertLess(450, row["prior_pipeline_busy_ms"][0])
        self.assertEqual(sum(summary["unique_steady_stage_totals_ms"].values()), 9000)
        self.assertIsNone(evidence[0]["offset_bound_ms"])

    def test_cold_wrapper_gap(self):
        """Functionality: Keep Graph preparation outside exact original-pipeline intervals.
        Inputs: first wrapper includes extra 400ms; subsequent worker starts move accordingly.
        Outputs: original 50ms interval and conservative 401ms next gap.
        Logic: pipeline_server_ms matches the worker timestamp.
        Constraints: no cold-cost hiding.
        """
        session = make_session()
        session["rows"][0]["metadata"].update(server_ms=450, pipeline_server_ms=50)
        session["rows"][0]["captureToDecodedMs"] += 400
        session["rows"][0]["transportMs"] += 400
        for row in session["rows"][1:]:
            row["metadata"]["tracking_timestamp_s"] += 0.4
        evidence, _ = analyze_session(session)
        self.assertEqual(evidence[0]["pipeline_end_ms"], 50)
        self.assertAlmostEqual(evidence[1]["offset_bound_ms"][1], 401)

    def test_invalid_evidence(self):
        """Functionality: Reject malformed counters/clocks, overlapping stages and mixed contracts.
        Inputs: independently corrupted complete reports. Outputs: explicit exceptions.
        Logic: never substitute defaults or discard offending rows. Constraints: no partial success.
        """
        bad = make_session()
        bad["rows"].pop()
        with self.assertRaises(ValueError):
            validate_session(bad)
        for key, value in (
            ("index", 999),
            ("tracking_timestamp_s", 0),
            ("server_ms", 49),
            ("depth_predict_ms", float("nan")),
            ("precision", "FP16"),
            ("tracking_ms", -1),
            ("depth_interval", 2),
            ("render_ms", True),
            ("stage_timing_model", "overlapping_cpu_depth"),
        ):
            session = make_session()
            session["rows"][90]["metadata"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_session(session)
        session = make_session()
        del session["rows"][90]["metadata"]["tracking_ms"]
        with self.assertRaises(KeyError):
            validate_session(session)
        session = make_session()
        session["rows"][90]["decodeMs"] += 1
        with self.assertRaises(ValueError):
            validate_session(session)

    def test_gate_has_no_clock_tolerance(self):
        """Functionality: Preserve a tail just above 500ms despite clock-identity epsilon.
        Inputs: one 500.000001ms row. Outputs: marked slow, unchanged raw latency.
        Logic: gate uses the original strict comparison. Constraints: no rounding before acceptance.
        """
        session = make_session()
        row = session["rows"][90]
        row["captureToDecodedMs"] = 500.000001
        row["transportMs"] = 493.000001
        row["metadata"]["resident_queue_ms"] = 400
        evidence, _ = analyze_session(session)
        self.assertTrue(evidence[90]["over_500ms"])
        self.assertEqual(evidence[90]["capture_to_decoded_ms"], 500.000001)

    def test_report_retention(self):
        """Functionality: Retain every valid frame and identify failed input separately.
        Inputs: temporary complete/failed matrix. Outputs: two session files and preserved failure.
        Logic: verify persisted row counts and status. Constraints: no overwritten output directory.
        """
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            source, _ = write_matrix(directory)
            output = directory / "output"
            report = run(source, output)
            self.assertEqual(report["status"], "completed")
            self.assertEqual(len(report["sessions"]), 2)
            self.assertFalse(report["runs"][1]["included"])
            self.assertEqual(report["runs"][1]["source_failure"], "source ended")
            for session in report["sessions"]:
                self.assertEqual(len((output / session["rows"]).read_text().splitlines()), 181)
            with self.assertRaises(FileExistsError):
                run(source, output)

    def test_matrix_identity_rejection(self):
        """Functionality: Reject tampering, duplicate reports and changed source interpretation.
        Inputs: independent mutated manifests/reports. Outputs: failed analysis records.
        Logic: verify content identities before deriving intervals. Constraints: no hash bypass.
        """
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            source, manifest = write_matrix(directory)
            variants = ("hash", "duplicate", "path", "empty", "source", "missing")
            for index, variant in enumerate(variants):
                changed = copy.deepcopy(manifest)
                if variant == "hash":
                    changed["runs"][0]["report_sha256"] = "0" * 64
                elif variant == "duplicate":
                    changed["runs"].append(changed["runs"][0])
                elif variant == "path":
                    changed["runs"][0]["report"] = "../escape.json"
                elif variant == "empty":
                    changed["runs"] = []
                elif variant == "source":
                    path = source / "complete.json"
                    report = json.loads(path.read_text())
                    report["source_code_sha256"][SEMANTIC_SOURCES[0]] = "0" * 64
                    path.write_text(json.dumps(report))
                    changed["runs"][0]["report_sha256"] = file_hash(path)
                else:
                    (source / "summary.json").unlink()
                if variant != "missing":
                    (source / "summary.json").write_text(json.dumps(changed))
                output = directory / f"rejected-{index}"
                with self.assertRaises((ValueError, FileNotFoundError)):
                    run(source, output)
                self.assertEqual(
                    json.loads((output / "report.json").read_text())["status"], "failed"
                )
