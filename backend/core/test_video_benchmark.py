"""Responsibilities: Protect full-video evidence, source time and failure-safe diagnostic ownership.
Implementation: Actual codec/tensor checks plus explicit decoder-boundary doubles; no performance
or pretrained-model equivalence claims from synthetic cases.
Related Modules: tools.benchmark_video/inference_profile; core.detector.
Declaration Index: VideoBenchmarkTests; VideoBenchmarkTests.test_real_codec;
VideoBenchmarkTests.test_decoder_boundaries; VideoBenchmarkTests.test_invalid_metadata;
VideoBenchmarkTests.test_depth_evidence; VideoBenchmarkTests.test_frame_identity;
VideoBenchmarkTests.test_reference_roundtrip; VideoBenchmarkTests.test_trace_restoration;
VideoBenchmarkTests.test_preconditions; VideoBenchmarkTests.test_overlap_trace.
Variable Index: None.
"""

import copy
import io
import json
import tempfile
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from django.test import SimpleTestCase
from PIL import Image

from tools.benchmark_video import (
    DepthEvidence,
    VideoFrames,
    frame_identity,
    install_tracking_trace,
    run,
    validate_reference,
)
from tools.inference_profile import StageTrace


class VideoBenchmarkTests(SimpleTestCase):
    """Functionality: Check complete-frame contracts without relying on model output.
    Logic: actual local codec for order/timestamps, explicit failures for truncated streams.
    Constraints: synthetic data proves diagnostic semantics only; no GPU speed/accuracy claim.
    """

    def test_real_codec(self):
        """Functionality: Preserve all decoded frames and presentation times through preparation.
        Inputs: three visibly distinct generated MP4 frames. Outputs: three JPEGs at source cadence.
        Logic: encode/decode locally and inspect size/pixels/hash uniqueness.
        Constraints: no network, fixture sampling or source-video changes.
        """
        import cv2

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.mp4"
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30, (96, 64))
            self.assertTrue(writer.isOpened())
            try:
                for value in (30, 100, 200):
                    writer.write(np.full((64, 96, 3), value, dtype=np.uint8))
            finally:
                writer.release()
            frames = VideoFrames(path)
            try:
                rows = list(frames)
            finally:
                frames.close()
            self.assertEqual([row[0]["index"] for row in rows], [0, 1, 2])
            np.testing.assert_allclose([row[0]["timestamp_s"] for row in rows], [0, 1/30, 2/30])
            self.assertEqual(len({row[0]["input_sha256"] for row in rows}), 3)
            for (_, raw), value in zip(rows, (30, 100, 200), strict=True):
                with Image.open(io.BytesIO(raw)) as image:
                    self.assertEqual(image.size, (96, 64))
                    self.assertLess(abs(np.asarray(image).mean() - value), 10)

    def test_decoder_boundaries(self):
        """Functionality: Reject truncation, duplicate source time, extra frames and shape changes.
        Inputs: explicit decoder doubles with declared two-frame video. Outputs: errors, no skip.
        Logic: iterate the same real transform and fail at the offending source boundary.
        Constraints: doubles verify failure handling only, never model or codec equivalence.
        """
        import cv2

        normal = np.zeros((16, 32, 3), dtype=np.uint8)
        cases = [
            ([(True, normal), (False, None)], [0], RuntimeError),
            ([(True, normal), (True, normal)], [0, 0], ValueError),
            ([(True, normal)] * 3, [0, 1000/30], RuntimeError),
            ([(True, normal[:8])], [0], ValueError),
        ]
        for reads, times, error in cases:
            decoder = Mock()
            decoder.isOpened.return_value = True
            decoder.get.side_effect = [2, 30, 32, 16, *times]
            decoder.read.side_effect = reads
            with patch.object(cv2, "VideoCapture", return_value=decoder):
                frames = VideoFrames(Path("synthetic.mp4"))
                try:
                    with self.assertRaises(error):
                        list(frames)
                finally:
                    frames.close()
            decoder.release.assert_called_once()

    def test_invalid_metadata(self):
        """Functionality: Release invalid decoders before any frame work.
        Inputs: NaN/count/fps/geometry contract violations. Outputs: ValueError and one release.
        Logic: metadata-only failures. Constraints: no guessed frame rate or integer truncation.
        """
        import cv2

        for values in ([float("nan"), 30, 32, 16], [2, 0, 32, 16], [2.5, 30, 32, 16],
                       [2, 30, 0, 16]):
            decoder = Mock()
            decoder.isOpened.return_value = True
            decoder.get.side_effect = values
            with (patch.object(cv2, "VideoCapture", return_value=decoder),
                  self.assertRaises(ValueError)):
                VideoFrames(Path("synthetic.mp4"))
            decoder.release.assert_called_once()
            decoder.read.assert_not_called()

    def test_depth_evidence(self):
        """Functionality: Hash actual returned values once and reject stale or missing evidence.
        Inputs: real CPU tensors through a counted estimator. Outputs: distinct exact hashes.
        Logic: retain the same tensor identity, require consume between calls, propagate failure.
        Constraints: CPU tensor checks ownership semantics; real CUDA equivalence runs separately.
        """
        import torch

        first, second = torch.arange(6).reshape(2, 3).float(), torch.ones((2, 3))
        predict = Mock(side_effect=[first, second, RuntimeError("original failure")])
        evidence = DepthEvidence(predict)
        with self.assertRaises(RuntimeError):
            evidence.consume()
        self.assertIs(evidence(None), first)
        with self.assertRaises(RuntimeError):
            evidence(None)
        a = evidence.consume()
        self.assertEqual((a["shape"], a["dtype"]), ([2, 3], "float32"))
        self.assertIs(evidence(None), second)
        self.assertNotEqual(a["sha256"], evidence.consume()["sha256"])
        with self.assertRaisesMessage(RuntimeError, "original failure"):
            evidence(None)
        self.assertIsNone(evidence.pending)
        self.assertEqual(predict.call_count, 3)

    def test_frame_identity(self):
        """Functionality: Detect source/depth/semantic differences while excluding measured timings.
        Inputs: minimal metadata and single-field mutations. Outputs: exact equality changes.
        Logic: motion/risk fields remain part of full box identity; no tolerance hides them.
        Constraints: synthetic hashes are contract values, not numerical-model evidence.
        """
        metadata = dict.fromkeys(("raw_sha256", "jpeg_sha256", "tracking_model", "tracking_status",
            "tracking_epoch", "gmc_method", "with_reid", "distance_mode", "depth_frame",
            "depth_interval"), 1)
        metadata.update(boxes=[{"distance_m": 2.0, "risk_level": "high"}], tracking_timestamp_s=0)
        row = {"index": 0, "timestamp_s": 0, "size": [640, 360], "input_sha256": "input",
               "depth": {"sha256": "depth", "shape": [360, 640], "dtype": "float32"},
               "metadata": metadata}
        other = copy.deepcopy(row)
        other["metadata"]["server_ms"] = 99
        self.assertEqual(frame_identity(row), frame_identity(other))
        other["metadata"]["boxes"][0]["distance_m"] += 0.000001
        self.assertNotEqual(frame_identity(row), frame_identity(other))
        for key in ("timestamp_s", "depth", "input_sha256"):
            other = copy.deepcopy(row)
            other[key] = None
            self.assertNotEqual(frame_identity(row), frame_identity(other))

    def test_trace_restoration(self):
        """Functionality: Restore shared native calls after a diagnostic error.
        Inputs: OpenCV function and minimal tracker owner. Outputs: exact restored identity.
        Logic: ExitStack unwinds on exception; methods remain untouched after the context.
        Constraints: this does not substitute a model or claim optical-flow correctness.
        """
        import cv2

        original = cv2.calcOpticalFlowPyrLK
        tracker = SimpleNamespace(args=SimpleNamespace(gmc_method="sparseOptFlow"),
                                  update=Mock(), gmc=SimpleNamespace(apply=Mock()))
        detector = SimpleNamespace(object_tracker=SimpleNamespace(tracker=tracker))
        with self.assertRaisesMessage(RuntimeError, "probe failed"):
            with ExitStack() as stack:
                install_tracking_trace(StageTrace(), detector, stack)
                self.assertIsNot(cv2.calcOpticalFlowPyrLK, original)
                raise RuntimeError("probe failed")
        self.assertIs(cv2.calcOpticalFlowPyrLK, original)

    def test_reference_roundtrip(self):
        """Functionality: Accept serialized tuple config without admitting actual value changes.
        Inputs: JSON-saved reference, original tuple settings and changed settings/source/status.
        Outputs: equal contract accepted; actual differences rejected.
        Logic: exercise the saved/in-memory boundary that previously blocked Graph before inference.
        Constraints: no precision tolerance, ignored keys or experiment parameter changes.
        """
        current = {key: {"value": "same"} for key in ("identities", "implementation_sha256",
                   "sdk_sha256", "opencv_native_sha256", "environment")}
        current.update(status="completed", settings={"heights": (("person", 1.7), ("car", 1.5))})
        reference = json.loads(json.dumps(current))
        validate_reference(reference, current)
        changed = copy.deepcopy(current)
        changed["settings"]["heights"] = (("person", 1.700001), ("car", 1.5))
        with self.assertRaisesMessage(ValueError, "settings differs"):
            validate_reference(reference, changed)
        changed = copy.deepcopy(current)
        changed["implementation_sha256"]["value"] = "different"
        with self.assertRaisesMessage(ValueError, "implementation_sha256 differs"):
            validate_reference(reference, changed)
        reference["status"] = "failed"
        with self.assertRaisesMessage(ValueError, "completed experiment"):
            validate_reference(reference, current)

    def test_preconditions(self):
        """Functionality: Reject absent source or occupied output before inference/assets work.
        Inputs: nonexistent/occupied paths or incompatible overlap flags. Outputs: ValueError.
        Logic: preflight only. Constraints: never overwrites results or initializes a model.
        """
        path = Path(__file__)
        with self.assertRaises(ValueError):
            run(path.with_suffix(".absent"), path.parent)
        with self.assertRaises(ValueError):
            run(path, path.parent)
        for options in ({"tracking_overlap": True}, {"trace_overlap": True},
                        {"trace_overlap_cpu": True},
                        {"host_fusion": True},
                        {"tracking_overlap": True, "depth_graph": True,
                         "trace_overlap": True, "trace_overlap_cpu": True},
                        {"tracking_overlap": True, "depth_graph": True, "trace_steady": True}):
            with self.assertRaisesMessage(ValueError, "Overlap requires"):
                run(path, path.parent, **options)

    def test_overlap_trace(self):
        """Functionality: Preserve Graph identity and restore diagnostic hooks after failure.
        Inputs: explicit host-only owners and malformed JPEG. Outputs: retained failed event.
        Logic: install real wrappers while keeping inference untouched; unwind every patched call.
        Constraints: no model initialization; this tests instrumentation, not GPU overlap speed.
        """
        import realtime.detector as pipeline
        from core.detector import DetectionError, decode_frame
        from tools.overlap_cpu_profile import OverlapStageTrace

        graph = object()
        predictor = SimpleNamespace(preprocess=decode_frame, postprocess=decode_frame,
                                    inference=graph)
        tracker = SimpleNamespace(associate=decode_frame, enrich=decode_frame)
        detector = SimpleNamespace(object_tracker=tracker,
            depth_estimator=SimpleNamespace(model=SimpleNamespace(predictor=predictor)))
        trace = OverlapStageTrace()
        with self.assertRaises(DetectionError), ExitStack() as stack:
            trace.install(detector, stack)
            self.assertIs(predictor.inference, graph)
            trace.call(1, pipeline.decode_frame, b"invalid JPEG fixture")
        self.assertIs(pipeline.decode_frame, decode_frame)
        self.assertIs(tracker.associate, decode_frame)
        self.assertEqual(trace.attempted, 1)
        self.assertEqual(trace.completed, 0)
        self.assertFalse(trace.events[0]["completed"])
        with tempfile.TemporaryDirectory() as directory:
            metadata = trace.write(Path(directory))
            self.assertEqual(metadata["stage_timing_model"], "overlapping_cpu_depth")
