"""Responsibilities: Verify explicit local depth-test inputs, model reuse and result ownership.
Implementation: Actual filesystem/JPEG fixtures, injected detector predictions; no downloads/GPU.
Related Modules: model_tests.run_depth; core.test_depth; tools.fixtures.
Declaration Index: DepthCliTests; DepthCliTests.arguments; DepthCliTests.test_inputs_and_order;
DepthCliTests.test_single_load_repeats_and_outputs; DepthCliTests.test_explicit_tracking_sequence;
DepthCliTests.test_refuses_overwrite_and_nesting;
DepthCliTests.test_failure_preserves_partial_results; DepthCliTests.test_main_failures.
Variable Index: None. Synthetic outputs are not evidence of pretrained performance or accuracy.
"""

import argparse
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from PIL import Image

from core.detector import DetectionError
from model_tests import run_depth as cli


class DepthCliTests(SimpleTestCase):
    """Functionality: Exercise deterministic test runs and preserve existing user artifacts.
    Logic: independent input/output trees and a counted model factory.
    Constraints: mock detector predictions do not validate CUDA/metric model accuracy.
    """

    def arguments(self, root, source):
        """Functionality: Build explicit CLI arguments around temporary local assets.
        Inputs: temp root and source image/directory. Outputs: argparse.Namespace.
        Logic: write tiny model identity fixtures. Constraints: never deserialized as real weights.
        """
        weights, depth, manifest = root / "detect.pt", root / "depth.pt", root / "manifest.json"
        for path in (weights, depth, manifest):
            path.write_bytes(b"explicit identity test fixture")
        return argparse.Namespace(input=source, output=root / "output", weights=weights,
                                  depth_weights=depth, manifest=manifest, repeat=1, label=None)

    def test_inputs_and_order(self):
        """Functionality: Discover nested image extensions deterministically, reject empty inputs.
        Inputs: supported/unsupported files. Outputs: stable image list and missing/empty errors.
        Logic: actual directory discovery. Constraints: no sampling or skipped bad images.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "nested").mkdir()
            for name in ("z.PNG", "a.jpg", "nested/a.jpg", "ignored.txt"):
                (root / name).write_bytes(b"discovery only")
            base, images = cli.collect_inputs(root)
            self.assertEqual([path.relative_to(base).as_posix() for path in images],
                             ["a.jpg", "nested/a.jpg", "z.PNG"])
            self.assertEqual(cli.collect_inputs(root / "a.jpg"), (root, [root / "a.jpg"]))
            for source in (root / "missing", root / "ignored.txt"):
                with self.assertRaises(ValueError):
                    cli.collect_inputs(source)
            empty = root / "empty"
            empty.mkdir()
            with self.assertRaises(ValueError):
                cli.collect_inputs(empty)

    def test_single_load_repeats_and_outputs(self):
        """Functionality: Load once for multiple images/repeats and retain full input identities.
        Inputs: two original resolutions and repeated predictions. Outputs: four JSONL/JPEG records.
        Logic: real640 fixture transform; check coordinates/hash/outputs and model-call count.
        Constraints: no throughput or accuracy metrics inferred from synthetic predictions.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input"
            source.mkdir()
            for name in ("same.jpg", "same.png"):
                Image.new("RGB", (1200, 600), "red").save(source / name)
            args = self.arguments(root, source)
            args.repeat = 2
            metadata = {"boxes": [{"class_name": "person", "confidence": .9,
                                   "bbox": [1, 1, 5, 5], "distance_m": 2.4}]}
            detector = MagicMock()
            detector.predict.return_value = (metadata, b"explicit rendered test bytes")
            with patch.object(cli, "build_detector", return_value=detector) as factory, \
                    patch("builtins.print"):
                report = cli.run(args)
            factory.assert_called_once_with(args.weights, args.manifest, args.depth_weights)
            self.assertEqual(detector.predict.call_count, 4)
            self.assertEqual(report["status"], "complete")
            self.assertEqual(report["frames"], 4)
            self.assertEqual(report["label"], "depth")
            saved = json.loads((args.output / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(saved, report)
            records = [json.loads(line) for line in
                       (args.output / "results.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([item["repeat_index"] for item in records], [0, 1, 0, 1])
            self.assertEqual(len({item["annotation"] for item in records}), 4)
            for item in records:
                self.assertEqual(item["source_size"], [1200, 600])
                self.assertEqual(item["frame_size"], [640, 320])
                self.assertEqual(item["coordinate_space"], "frame")
                self.assertEqual(item["metadata"], metadata)
                self.assertEqual(len(item["input_jpeg_sha256"]), 64)
                self.assertTrue((args.output / item["annotation"]).is_file())
            self.assertEqual(records[0]["input_jpeg_sha256"], records[1]["input_jpeg_sha256"])

    def test_refuses_overwrite_and_nesting(self):
        """Functionality: Reject old output directories, input-nested output and invalid repeats.
        Inputs: explicit paths/repeat counts. Outputs: early errors and no model factory calls.
        Logic: preserve a sentinel file. Constraints: no deletion, overwrite or silent count change.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input"
            source.mkdir()
            Image.new("RGB", (20, 20)).save(source / "a.jpg")
            args = self.arguments(root, source)
            args.output.mkdir()
            sentinel = args.output / "keep.txt"
            sentinel.write_text("keep", encoding="utf-8")
            with patch.object(cli, "build_detector") as factory:
                with self.assertRaises(ValueError):
                    cli.run(args)
                self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
                args.output = source / "new-output"
                with self.assertRaises(ValueError):
                    cli.run(args)
                args.output, args.repeat = root / "new-output", 0
                with self.assertRaises(ValueError):
                    cli.run(args)
                factory.assert_not_called()

    def test_explicit_tracking_sequence(self):
        """Functionality: Enable one persistent tracker only for explicitly ordered CLI runs.
        Inputs: three repeats and a selected JSON profile. Outputs: one factory/profile and report.
        Logic: exercise real profile loading and reject orphan --tracking-config before loading.
        Constraints: mocked predictions verify lifecycle/configuration, not identity accuracy.
        """
        from realtime.tracking import TrackingConfig

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "a.jpg"
            Image.new("RGB", (20, 20)).save(source)
            args = self.arguments(root, source)
            args.repeat, args.track = 3, True
            args.tracking_config = root / "tracking.json"
            args.tracking_config.write_text('{"history_size": 5}', encoding="utf-8")
            detector = MagicMock()
            detector.predict.return_value = ({"boxes": []}, b"explicit test JPEG")
            with patch.object(cli, "build_detector", return_value=detector) as factory, \
                    patch("builtins.print"):
                report = cli.run(args)
            factory.assert_called_once_with(args.weights, args.manifest, args.depth_weights,
                                            tracking_config=TrackingConfig(history_size=5))
            self.assertEqual(detector.predict.call_count, 3)
            self.assertEqual(report["settings"]["tracking"]["history_size"], 5)
            self.assertIn("ordered sequence tracking", report["scope"])
            args.output, args.track = root / "unused", False
            with patch.object(cli, "build_detector") as factory, self.assertRaises(ValueError):
                cli.run(args)
            factory.assert_not_called()

    def test_failure_preserves_partial_results(self):
        """Functionality: Mark failed runs without retrying or discarding completed predictions.
        Inputs: one successful frame then depth failure. Outputs: partial JSONL/failure code.
        Logic: assert error propagation and two calls only. Constraints: no detection-only fallback.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "a.jpg"
            Image.new("RGB", (20, 20)).save(source)
            args = self.arguments(root, source)
            args.repeat = 3
            detector = MagicMock()
            detector.predict.side_effect = [
                ({"boxes": []}, b"test JPEG"),
                DetectionError("depth_inference_failed", "failure", 502)]
            with patch.object(cli, "build_detector", return_value=detector), \
                    patch("builtins.print"), self.assertRaises(DetectionError):
                cli.run(args)
            report = json.loads((args.output / "run.json").read_text(encoding="utf-8"))
            self.assertEqual((report["status"], report["frames"]), ("failed", 1))
            self.assertEqual(report["error_code"], "depth_inference_failed")
            self.assertEqual(detector.predict.call_count, 2)
            self.assertEqual(len((args.output / "results.jsonl").read_text().splitlines()), 1)

    def test_main_failures(self):
        """Functionality: Verify relocated defaults and meaningful terminal failure.
        Inputs: default model/sample options and explicit missing input. Outputs: pinned asset
        paths and exit1 diagnostic. Logic: actual parser/error boundary with controlled run.
        Constraints: no environment/SDK side effects or automatic asset selection.
        """
        with tempfile.TemporaryDirectory() as directory, patch("builtins.print") as output:
            root = Path(directory)
            self.assertEqual(cli.main(["--input", str(root / "missing"),
                                       "--output", str(root / "output")]), 1)
            self.assertIn("Input must be an existing", output.call_args.args[0])
            with patch.object(cli, "run", return_value={"frames": 0}) as run:
                self.assertEqual(cli.main(["--output", str(root / "output")]), 0)
            args = run.call_args.args[0]
            self.assertEqual(args.input, cli.TEST_ROOT / "samples/bus.jpg")
            self.assertEqual(args.weights, cli.ROOT / "models/yolo26n-walk.pt")
            self.assertEqual(args.depth_weights, cli.ROOT / "models/yolo26n-depth.pt")
