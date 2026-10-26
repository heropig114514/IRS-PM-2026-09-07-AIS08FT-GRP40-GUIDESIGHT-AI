"""Responsibilities: Guard experimental depth artifact provenance and measurement honesty.
Implementation: CPU-only manifest tampering, tensor contract and numerical-report regression tests.
Related Modules: realtime.depth_runtime; tools.depth_export_runtime; tools.benchmark_depth_export.
Declaration Index: DepthArtifactTests; DepthArtifactTests.setUp; DepthArtifactTests.validate;
DepthArtifactTests.test_identity_changes_require_reexport;
DepthArtifactTests.test_binary_and_path_changes_are_rejected;
DepthArtifactTests.test_quarter_resolution_and_static_batch_are_required;
DepthArtifactTests.test_progress_preserves_reader_and_final_snapshot;
DepthArtifactTests.test_serving_validates_engines_without_export_dependencies;
DepthNumericalTests; DepthNumericalTests.test_errors_do_not_claim_quality_acceptance;
DepthNumericalTests.test_nonfinite_and_shape_mismatch_remain_visible;
DepthNumericalTests.test_slow_samples_are_not_trimmed;
DepthNumericalTests.test_cpu_and_changed_shapes_are_rejected.
Variable Index: None. Temporary model bytes are inert fixtures, never deserialized as checkpoints.
"""

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from django.test import SimpleTestCase

from realtime.depth_runtime import file_hash, validate_manifest


class DepthArtifactTests(SimpleTestCase):
    """Functionality: Prevent stale or substituted depth artifacts from being loaded.
    Logic: mutate one provenance/contract field at a time and require an explicit rejection.
    Constraints: these checks do not prove ONNX export correctness or actual GPU compatibility.
    """

    def setUp(self):
        """Functionality: Prepare inert checkpoint and graph identities in an isolated directory.
        Inputs: temporary filesystem. Outputs: valid manifest and cleanup registration.
        Logic: hash exact fixture bytes. Constraints: no model, network or CUDA dependency.
        """
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.weights = self.root / "model.pt"
        self.weights.write_bytes(b"fine-tuned checkpoint identity")
        graph = self.root / "depth.onnx"
        graph.write_bytes(b"native depth graph identity")
        self.identity = {"gpu": "test-gpu", "sdk": "pinned-sdk"}
        self.manifest = {"schema": 1, "precision": "FP32", "tf32": False,
                         "weights_sha256": file_hash(self.weights), "identity": self.identity,
                         "artifacts": [{"input_shape": [1, 3, 768, 576],
                                        "output_shape": [1, 1, 192, 144], "onnx": graph.name,
                                        "onnx_sha256": file_hash(graph)}]}

    def validate(self, manifest):
        """Functionality: Apply the public provenance check to one test manifest.
        Inputs: test dictionary. Outputs: validation result or error.
        Logic: pass explicit current identity/weights. Constraints: no exception interception.
        """
        return validate_manifest(manifest, self.root, self.weights, self.identity)

    def test_progress_preserves_reader_and_final_snapshot(self):
        """Functionality: Keep progress readable without replacing an open Windows report.
        Inputs: append-only progress file held by a reader. Outputs: intact rows and no final JSON.
        Logic: publish two stages while the reader stays open; inspect after both closes.
        Constraints: final snapshots are written only when the run finishes, never per backend.
        """
        from tools.benchmark_depth_export import append_progress

        report = self.root / "report.json"
        progress = report.with_suffix(".jsonl")
        append_progress(report, {"stage": 1})
        with progress.open(encoding="utf-8") as reader:
            self.assertEqual(json.loads(reader.readline()), {"stage": 1})
            append_progress(report, {"stage": 2})
            self.assertEqual(json.loads(reader.readline()), {"stage": 2})
        self.assertFalse(report.exists())

    def test_identity_changes_require_reexport(self):
        """Functionality: Require new exports for new weights/runtime/precision.
        Inputs: valid manifest and individual mutations. Outputs: explicit rejection assertions.
        Logic: validate baseline then alter every binding. Constraints: no tolerated stale identity.
        """
        self.validate(self.manifest)
        for key, value in (("weights_sha256", "0" * 64), ("precision", "FP16"),
                           ("tf32", True), ("identity", {"gpu": "other"}), ("schema", 2)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.validate({**self.manifest, key: value})

    def test_serving_validates_engines_without_export_dependencies(self):
        """Functionality: Permit engine-only deployment without weakening weights/runtime checks.
        Inputs: manifest, inert engine and serving identity. Outputs: guard assertions.
        Logic: omit the ONNX file, retain export metadata, reject changed engine policy or bytes.
        Constraints: does not deserialize test bytes or claim actual engine compatibility.
        """
        manifest = copy.deepcopy(self.manifest)
        manifest["identity"]["onnx"] = "export-only-version"
        engine = self.root / "depth.engine"
        engine.write_bytes(b"explicit engine identity")
        entry = manifest["artifacts"][0]
        entry.update(engine=engine.name, engine_sha256=file_hash(engine), builder={
            "strongly_typed": True, "tf32": False, "workspace_bytes": 1 << 30,
            "max_aux_streams": 0, "fp16": False, "int8": False})
        (self.root / "depth.onnx").unlink()
        validate_manifest(manifest, self.root, self.weights, self.identity, engine_only=True)
        entry["builder"]["tf32"] = True
        with self.assertRaises(ValueError):
            validate_manifest(manifest, self.root, self.weights, self.identity, engine_only=True)
        entry["builder"]["tf32"] = False
        engine.write_bytes(b"unrelated engine")
        with self.assertRaises(ValueError):
            validate_manifest(manifest, self.root, self.weights, self.identity, engine_only=True)

    def test_binary_and_path_changes_are_rejected(self):
        """Functionality: Reject binary tampering and paths escaping the artifact directory.
        Inputs: valid manifest and replaced file/path. Outputs: rejection assertions.
        Logic: compare bytes and resolved parent. Constraints: no deserialization precedes checks.
        """
        changed = copy.deepcopy(self.manifest)
        changed["artifacts"][0]["onnx"] = "../depth.onnx"
        with self.assertRaises(ValueError):
            self.validate(changed)
        (self.root / "depth.onnx").write_bytes(b"replacement")
        with self.assertRaises(ValueError):
            self.validate(self.manifest)

    def test_quarter_resolution_and_static_batch_are_required(self):
        """Functionality: Prevent SDK full-resolution exports or shape changes entering comparisons.
        Inputs: incompatible graph metadata. Outputs: explicit rejection assertions.
        Logic: alter output resolution and batch independently. Constraints: no automatic resize.
        """
        for key, value in (("input_shape", [2, 3, 768, 576]),
                           ("output_shape", [1, 1, 768, 576])):
            changed = copy.deepcopy(self.manifest)
            changed["artifacts"][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.validate(changed)


@unittest.skipUnless(importlib.util.find_spec("torch"), "Torch not installed")
class DepthNumericalTests(SimpleTestCase):
    """Functionality: Preserve numerical differences and unsuccessful contracts in diagnostics.
    Logic: use tiny CPU tensors to isolate reporting semantics from GPU implementation.
    Constraints: actual engine speed, buffering and alignment require the real device benchmark.
    """

    def test_errors_do_not_claim_quality_acceptance(self):
        """Functionality: Expose small FP32 differences without treating them as accepted accuracy.
        Inputs: identical and changed maps. Outputs: exactness/error assertions.
        Logic: inspect all pixels. Constraints: no hidden tolerance or truth-based quality claim.
        """
        import torch

        from tools.benchmark_depth_export import difference

        reference = torch.tensor([[1.0, 2.0]])
        exact = difference(reference, reference.clone())
        changed = difference(reference, torch.tensor([[1.0, 2.125]]))
        self.assertTrue(exact["exact"])
        self.assertFalse(exact["quality_accepted"])
        self.assertFalse(changed["exact"])
        self.assertEqual(changed["absolute_m"]["max"], 0.125)
        self.assertEqual(changed["different_elements"], 1)

    def test_nonfinite_and_shape_mismatch_remain_visible(self):
        """Functionality: Reject changed output contracts and expose invalid output values.
        Inputs: shape mismatch and a nonfinite candidate. Outputs: failure and count assertions.
        Logic: compare all values before finite-only error statistics. Constraints: no substitution.
        """
        import torch

        from tools.benchmark_depth_export import difference

        reference = torch.tensor([1.0, 0.0])
        with self.assertRaises(ValueError):
            difference(reference, reference[None])
        result = difference(reference, torch.tensor([float("nan"), 1.0]))
        self.assertFalse(result["exact"])
        self.assertEqual(result["candidate_nonfinite"], 1)
        self.assertEqual(result["relative_excluded_zero_or_nonfinite"], 2)

    def test_slow_samples_are_not_trimmed(self):
        """Functionality: Keep a severe latency outlier in the reported distribution.
        Inputs: 30 samples with one slow call. Outputs: full count and maximum assertions.
        Logic: exercise shared statistics. Constraints: no percentile-based sample removal.
        """
        from tools.benchmark_depth_export import distribution

        result = distribution([1] * 29 + [1000])
        self.assertEqual(result["count"], 30)
        self.assertEqual(result["max"], 1000)
        self.assertGreater(result["mean"], 30)

    def test_cpu_and_changed_shapes_are_rejected(self):
        """Functionality: Reject unsafe device binding before any native runtime call.
        Inputs: CPU tensor and incompatible declared shapes. Outputs: ValueError assertions.
        Logic: apply common tensor contract. Constraints: this does not simulate CUDA execution.
        """
        import torch

        from realtime.depth_runtime import validate_input

        tensor = torch.zeros(1, 3, 32, 32)
        for shape in ((1, 3, 32, 32), (1, 3, 64, 32)):
            with self.subTest(shape=shape), self.assertRaises(ValueError):
                validate_input(tensor, shape, tensor.device)
