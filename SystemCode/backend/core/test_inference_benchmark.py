"""Responsibilities: Protect benchmark cold/steady separation and complete sample accounting.
Implementation: Known durations/output guards, profiler failure/accounting checks and optional
real CUDA buffer-ownership verification.
Related Modules: tools.benchmark_inference/depth_graph_probe/inference_profile;
no network or downloads.
Declaration Index: BenchmarkTests; BenchmarkTests.test_cold_and_slow_frames;
BenchmarkTests.test_small_and_invalid_samples; BenchmarkTests.test_run_preconditions;
BenchmarkTests.test_graph_ownership; BenchmarkTests.test_steady_profile;
BenchmarkTests.test_steady_profile.cold; BenchmarkTests.test_steady_profile.recursive;
BenchmarkTests.test_steady_profile.failed; BenchmarkTests.test_empty_profile;
BenchmarkTests.test_stage_trace; BenchmarkTests.test_stage_trace.predict.
Variable Index: None.
"""

import importlib.util
import json
import pstats
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import skipUnless

from django.test import SimpleTestCase

from tools.benchmark_inference import distribution, run, summarize_frames


class BenchmarkTests(SimpleTestCase):
    """Functionality: Validate evidence handling independently of any inference implementation.
    Logic: assert exact counts, preserved slow samples and failure before model initialization.
    Constraints: synthetic cases establish semantics only, not model quality or performance.
    """

    def test_cold_and_slow_frames(self):
        """Functionality: Keep a steady outlier while separating only the first call.
        Inputs: cold 5000ms then 10/20/900ms. Outputs: count/median/max and cold identity.
        Logic: inspect aggregate statistics and absent stages. Constraints: no timing sleeps.
        """
        frames = [{"predict_wall_ms": value} for value in (5000, 10, 20, 900)]
        result = summarize_frames(frames)
        steady = result["steady"]["predict_wall_ms"]
        self.assertEqual(result["first_frame"], frames[0])
        self.assertEqual((steady["count"], steady["p50_ms"], steady["max_ms"]), (3, 20, 900))
        self.assertGreater(steady["p95_ms"], 800)
        self.assertIsNone(result["steady"]["depth_ms"])

    def test_small_and_invalid_samples(self):
        """Functionality: Avoid invented percentiles for empty input and reject invalid evidence.
        Inputs: empty/singleton and negative/nonfinite durations. Outputs: None/exact value/errors.
        Logic: explicit boundary cases. Constraints: no outlier deletion or numeric coercion.
        """
        self.assertIsNone(distribution([]))
        self.assertEqual(distribution([7])["p99_ms"], 7)
        for value in (-1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                distribution([value])
        self.assertIsNone(summarize_frames([])["first_frame"])

    def test_run_preconditions(self):
        """Functionality: Reject incomplete experiments and preserve existing output directories.
        Inputs: invalid counts/mode, conflicting probes or occupied output.
        Outputs: ValueError before CUDA imports.
        Logic: only preflight fields are supplied, so any model work would fail the test.
        Constraints: no file deletion, GPU initialization or external dependency substitution.
        """
        for frames, mode in ((1, "detection"), (0, "depth"), (2, "unknown")):
            with self.assertRaises(ValueError):
                run(SimpleNamespace(frames=frames, mode=mode))
        with self.assertRaises(ValueError):
            run(SimpleNamespace(frames=2, mode="full", output=Path(__file__).resolve().parent))
        with self.assertRaises(ValueError):
            run(SimpleNamespace(frames=2, mode="detection", depth_graph=True))
        with self.assertRaises(ValueError):
            run(SimpleNamespace(frames=2, mode="detection", depth_fusion_sort=True))
        with self.assertRaises(ValueError):
            run(SimpleNamespace(frames=2, mode="full", depth_graph=True, depth_fusion_sort=True))
        with self.assertRaises(ValueError):
            run(SimpleNamespace(frames=2, mode="detection", depth_cached_predictor=True))
        with self.assertRaises(ValueError):
            run(SimpleNamespace(frames=2, mode="full", depth_graph=True,
                                depth_cached_predictor=True))
        with self.assertRaises(ValueError):
            run(SimpleNamespace(frames=2, mode="depth", trace_steady=True))
        with self.assertRaises(ValueError):
            run(SimpleNamespace(frames=2, mode="full", trace_steady=True, profile_steady=True))

    def test_steady_profile(self):
        """Functionality: Verify cold exclusion, recursive counts and failure-safe collection.
        Inputs: distinct cold call, recursive steady call and an exception. Outputs: exact counters,
        original result/error, disabled profiler and inspectable complete JSON/pstats evidence.
        Logic: known call tree reveals reversed primitive/total fields and unwanted cold inclusion.
        Constraints: synthetic timing is not performance evidence; no sleeps or CUDA work.
        """
        from tools.inference_profile import SteadyProfile

        def cold(value):
            """Functionality: Identify an unprofiled first call. Inputs: value. Outputs: value.
            Logic: unique callable name. Constraints: no startup work simulated.
            """
            return value

        def recursive(value):
            """Functionality: Supply distinct total/primitive counts. Inputs: depth. Outputs: depth.
            Logic: recurse to zero. Constraints: bounded synthetic call tree.
            """
            return 1 + recursive(value - 1) if value else 0

        def failed(value):
            """Functionality: Expose exception accounting. Inputs: message. Outputs: ValueError.
            Logic: raise once. Constraints: no automatic retry.
            """
            raise ValueError(value)

        probe = SteadyProfile()
        self.assertEqual(probe.call(0, cold, 7), 7)
        self.assertEqual(probe.call(1, recursive, 3), 3)
        with self.assertRaisesMessage(ValueError, "original failure"):
            probe.call(2, failed, "original failure")
        self.assertIsNone(sys.getprofile())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = probe.write(root)
            report = json.loads((root / "profile.json").read_text(encoding="utf-8"))
            pstats.Stats(str(root / "profile.pstats"))
        self.assertEqual(metadata["attempted_steady_calls"], 2)
        self.assertEqual(metadata["completed_steady_calls"], 1)
        names = {row["function"] for row in report["functions"]}
        self.assertNotIn("cold", names)
        self.assertIn("failed", names)
        row = next(row for row in report["functions"] if row["function"] == "recursive")
        self.assertEqual((row["primitive_calls"], row["total_calls"]), (1, 4))
        edge = next(edge for edge in row["callers"] if edge["function"] == "recursive")
        self.assertEqual((edge["primitive_calls"], edge["total_calls"]), (1, 3))

    def test_empty_profile(self):
        """Functionality: Retain explicit evidence even if initialization fails before prediction.
        Inputs: unused profiler. Outputs: empty JSON/pstats and zero attempts/completions.
        Logic: inspect report without inventing timings. Constraints: no inference is executed.
        """
        from tools.inference_profile import SteadyProfile

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = SteadyProfile().write(root)
            report = json.loads((root / "profile.json").read_text(encoding="utf-8"))
            self.assertTrue((root / "profile.pstats").is_file())
        self.assertEqual(metadata["attempted_steady_calls"], 0)
        self.assertEqual(metadata["completed_steady_calls"], 0)
        self.assertEqual(report["functions"], [])

    def test_stage_trace(self):
        """Functionality: Keep stage timing independent from profiler events and errors visible.
        Inputs: wrapped predictor with a cold call, successful steady call and failure.
        Outputs: unchanged return/error, exact frame indices, no active trace after exception.
        Logic: verify complete JSON evidence and success flags. Constraints: no model equivalence.
        """
        from tools.inference_profile import StageTrace

        calls = []

        def predict(value):
            """Functionality: Count invocations and propagate a deliberate failure.
            Inputs: scalar or None. Outputs: same scalar or ValueError.
            Logic: record once per call. Constraints: synthetic stage only.
            """
            calls.append(value)
            if value is None:
                raise ValueError("original failure")
            return value

        trace = StageTrace()
        measured = trace.wrap("predict", predict)
        self.assertEqual(trace.call(0, measured, 1), 1)
        self.assertEqual(trace.call(1, measured, 2), 2)
        with self.assertRaisesMessage(ValueError, "original failure"):
            trace.call(2, measured, None)
        self.assertIsNone(trace.index)
        self.assertEqual(calls, [1, 2, None])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = trace.write(root)
            report = json.loads((root / "stage-trace.json").read_text(encoding="utf-8"))
        self.assertEqual((metadata["attempted_steady_calls"],
                          metadata["completed_steady_calls"]), (2, 1))
        self.assertEqual([event["frame_index"] for event in report["events"]], [1, 2])
        self.assertEqual([event["completed"] for event in report["events"]], [True, False])

    @skipUnless(importlib.util.find_spec("torch"), "Torch is optional in the web runtime")
    def test_graph_ownership(self):
        """Functionality: Verify replay accepts new pixels and does not overwrite earlier results.
        Inputs: deterministic CUDA tensors and parameter-free average pooling. Outputs: exact
        eager/replay equality for two inputs; retained output unchanged; invalid shape rejected.
        Logic: run actual CUDA capture/replay, not a mocked graph. Constraints: skip without CUDA;
        this checks the adapter's ownership contract, not pretrained depth accuracy.
        """
        import torch

        from tools.depth_graph_probe import DepthGraphProbe

        if not torch.cuda.is_available():
            self.skipTest("Native CUDA required for graph ownership")
        forward = torch.nn.AvgPool2d(2).eval()
        original = torch.arange(48, dtype=torch.float32, device="cuda").reshape(1, 3, 4, 4)
        probe = DepthGraphProbe(forward, original)
        first = probe(original)
        second = probe(original + 10)
        torch.testing.assert_close(first, forward(original), rtol=0, atol=0)
        torch.testing.assert_close(second, forward(original + 10), rtol=0, atol=0)
        with self.assertRaises(ValueError):
            probe(original[:, :, :2, :2])
