"""Responsibilities: Profile steady prediction calls without timing setup or evidence writing.
Implementation: Explicit cProfile or stage-wrapper diagnostics skip index zero, disable/restore
on exceptions and retain all calls. Inconsistent profiler timings are flagged, never corrected.
Related Modules: tools.benchmark_inference; core.test_inference_benchmark.
Declaration Index: SteadyProfile; SteadyProfile.__init__; SteadyProfile.call; SteadyProfile.write;
StageTrace; StageTrace.__init__; StageTrace.install; StageTrace.wrap; StageTrace.wrap.measured;
StageTrace.call; StageTrace.write.
Variable Index: profiler — process-local cProfile collector; attempted/completed — steady calls.
StageTrace.events/index — complete stage samples and actual frame identity; output excludes setup.
StageTrace.sources — SHA256 identities of installed original stage implementations.
"""

import cProfile
import hashlib
import inspect
import json
import time
from pathlib import Path
from unittest.mock import patch


class SteadyProfile:
    """Functionality: Collect diagnostic host-call timings for an explicitly selected benchmark.
    Logic: index zero runs unchanged; every later prediction is profiled with exception cleanup.
    Constraints: one serial worker; no CUDA synchronization, retries, filtering or accuracy claim.
    Profiling perturbs execution and measures native waits as host time, not GPU kernel duration.
    """

    def __init__(self):
        """Functionality: Allocate an inactive collector before the measured loop.
        Inputs: None. Outputs: empty profiler/counters. Logic: no profiling until a steady call.
        Constraints: construction does not execute model code or change any inference setting.
        """
        self.profiler = cProfile.Profile()
        self.attempted = self.completed = 0

    def call(self, index, predict, frame):
        """Functionality: Execute the same prediction once with explicit cold/steady separation.
        Inputs: zero-based index, bound predictor and original frame. Outputs: predictor result.
        Logic: cProfile.runcall always disables in finally, including when prediction raises.
        Constraints: propagate the original exception; failed attempts are never called complete.
        """
        if index == 0:
            return predict(frame)
        self.attempted += 1
        result = self.profiler.runcall(predict, frame)
        self.completed += 1
        return result

    def write(self, output):
        """Functionality: Retain every collected function and caller edge for later attribution.
        Inputs: benchmark-owned output directory. Outputs: profile.json/pstats and compact metadata.
        Logic: preserve primitive/total counts and self/cumulative host time; hash visited Python
        sources outside prediction timing. Native functions retain their names without fake hashes.
        Constraints: nested cumulative durations cannot be added; no top-N truncation or percentile
        claims; empty/cold-failed runs still produce an explicit empty profile and counters.
        """
        self.profiler.create_stats()
        self.profiler.dump_stats(str(output / "profile.pstats"))
        functions, sources = [], {}
        for (filename, line, name), values in self.profiler.stats.items():
            primitive, total, own, cumulative, callers = values
            edges = []
            for (caller_file, caller_line, caller_name), edge in callers.items():
                edges.append({"file": caller_file, "line": caller_line, "function": caller_name,
                              "primitive_calls": edge[1], "total_calls": edge[0],
                              "self_ms": edge[2] * 1000, "cumulative_ms": edge[3] * 1000})
            functions.append({"file": filename, "line": line, "function": name,
                              "primitive_calls": primitive, "total_calls": total,
                              "self_ms": own * 1000, "cumulative_ms": cumulative * 1000,
                              "callers": edges})
            source = Path(filename)
            if filename not in sources and source.suffix == ".py" and source.is_file():
                sources[filename] = hashlib.sha256(source.read_bytes()).hexdigest()
        functions.sort(key=lambda row: row["self_ms"], reverse=True)
        anomalies = [{key: row[key] for key in ("file", "line", "function", "self_ms",
                                              "cumulative_ms", "primitive_calls", "total_calls")}
                     for row in functions if row["self_ms"] > row["cumulative_ms"] + 1e-6
                     or row["primitive_calls"] > row["total_calls"]
                     or min(row["self_ms"], row["cumulative_ms"]) < 0]
        metadata = {"attempted_steady_calls": self.attempted,
                    "completed_steady_calls": self.completed, "cold_call_profiled": False,
                    "scope": "Prediction only; excludes first frame, capture and report writing",
                    "warning": "Host-call diagnostics; profiler perturbation; nested totals",
                    "function_count": len(functions), "source_sha256": sources,
                    "structural_anomalies": anomalies, "basic_consistency_passed": not anomalies}
        report = {**metadata, "functions": functions}
        (output / "profile.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return metadata


class StageTrace:
    """Functionality: Measure explicit stage boundaries independently of profiler call events.
    Logic: temporary wrappers call original methods exactly once and retain host elapsed time.
    Constraints: single benchmark process/thread; no new synchronization or altered model work;
    nested measurements and existing SDK waits must not be added or called pure kernel timing.
    """

    def __init__(self):
        """Functionality: Allocate inactive trace state. Inputs: None. Outputs: empty events.
        Logic: index None excludes setup. Constraints: no model imports or runtime patching yet.
        """
        self.events, self.index, self.sources = [], None, {}
        self.attempted = self.completed = 0

    def wrap(self, label, function):
        """Functionality: Build one transparent host-clock wrapper for a selected callable.
        Inputs: stage label and original function. Outputs: callable with identical result/error.
        Logic: measure in finally and retain failure samples. Constraints: no synchronization,
        result changes, retries, skipped failures or profiling while index is None.
        """
        def measured(*args, **kwargs):
            """Functionality: Time a stage. Inputs: original args/kwargs. Outputs: original result.
            Logic: append success/error duration in finally. Constraints: nested intervals overlap.
            """
            if self.index is None:
                return function(*args, **kwargs)
            started, completed = time.perf_counter(), False
            try:
                result = function(*args, **kwargs)
                completed = True
                return result
            finally:
                self.events.append({"frame_index": self.index, "stage": label,
                                    "elapsed_ms": (time.perf_counter() - started) * 1000,
                                    "completed": completed})
        return measured

    def install(self, detector, stack):
        """Functionality: Bind explicit warmed full-pipeline stages after graph preparation.
        Inputs: full detector and caller-owned ExitStack. Outputs: reversible wrappers.
        Logic: measure SDK methods and original profiling clocks, tracker, decode/letterbox,
        fusion, annotation and JPEG save. SDK clock calls preserve their original device waits.
        Constraints: no alternate algorithm; caller restores wrappers on success/failure; the six
        SDK clocks are expected in preprocess/inference/postprocess enter/exit order per frame.
        """
        from PIL import Image
        from ultralytics.utils import ops

        import realtime.detector as pipeline
        import realtime.distance as distance

        predictor = detector.depth_estimator.model.predictor
        targets = [(predictor, name, f"depth_{name}") for name in
                   ("preprocess", "inference", "postprocess")]
        targets += [(detector.object_tracker, name, f"tracking_{name}")
                    for name in ("associate", "enrich")]
        targets += [(pipeline, name, name) for name in ("decode_frame", "letterbox", "annotate")]
        targets += [(ops.Profile, "time", "depth_profile_clock"),
                    (distance, "fuse_object_depth", "depth_fusion"),
                    (Image.Image, "save", "jpeg_save")]
        for owner, name, label in targets:
            original = getattr(owner, name)
            source = Path(inspect.getsourcefile(original if hasattr(original, "__code__")
                                               else type(original)))
            self.sources[str(source)] = hashlib.sha256(source.read_bytes()).hexdigest()
            stack.enter_context(patch.object(owner, name, self.wrap(label, original)))

    def call(self, index, predict, frame):
        """Functionality: Associate all selected stage calls with one original frame index.
        Inputs: frame index, original predictor/frame. Outputs: unchanged result or exception.
        Logic: skip zero, reset active index in finally. Constraints: failures stay attempted only.
        """
        if index == 0:
            return predict(frame)
        self.index = index
        self.attempted += 1
        try:
            result = predict(frame)
            self.completed += 1
            return result
        finally:
            self.index = None

    def write(self, output):
        """Functionality: Save all explicit stage events without reducing nested measurements.
        Inputs: output directory. Outputs: stage-trace.json and compact counters.
        Logic: keep failure flags and original indices for independent count/order validation.
        Constraints: no trimmed samples, latency gates or GPU-kernel claims from wrapper times.
        """
        metadata = {"attempted_steady_calls": self.attempted,
                    "completed_steady_calls": self.completed, "cold_call_traced": False,
                    "timing": "Nested host intervals; original SDK clocks retain CUDA waits",
                    "source_sha256": self.sources}
        (output / "stage-trace.json").write_text(
            json.dumps({**metadata, "events": self.events}, indent=2) + "\n", encoding="utf-8")
        return metadata
