"""Responsibilities: Protect benchmark cold/steady separation and complete sample accounting.
Implementation: Known durations/output guards; optional real CUDA buffer-ownership verification.
Related Modules: tools.benchmark_inference/depth_graph_probe; no network or model downloads.
Declaration Index: BenchmarkTests; BenchmarkTests.test_cold_and_slow_frames;
BenchmarkTests.test_small_and_invalid_samples; BenchmarkTests.test_run_preconditions;
BenchmarkTests.test_graph_ownership.
Variable Index: None.
"""

import importlib.util
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
