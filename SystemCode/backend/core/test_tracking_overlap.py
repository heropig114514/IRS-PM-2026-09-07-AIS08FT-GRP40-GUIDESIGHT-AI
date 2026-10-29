"""Responsibilities: Verify overlap ordering, one-frame ownership, timing semantics and failures.
Implementation: Explicit CPU/SDK/stream doubles; actual CUDA/equality evidence is separate.
Related Modules: tools.tracking_overlap_probe/benchmark_video/analyze_browser_queue.
Declaration Index: make_detector; make_detector.associate; make_detector.graph;
make_detector.depth; make_detector.predict; TrackingOverlapTests;
TrackingOverlapTests.test_order_and_restoration; TrackingOverlapTests.test_failure_drain;
TrackingOverlapTests.test_consumption_identity; TrackingOverlapTests.test_session_tracker;
TrackingOverlapTests.test_explicit_contract; TrackingOverlapTests.test_device_observation;
TrackingOverlapTests.test_timing_accounting; TrackingOverlapTests.test_reentrancy;
TrackingOverlapTests.test_bad_input_recovery.
Variable Index: None. Synthetic event order does not prove actual GPU overlap or speedup.
"""

from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from core.detector import DetectionError
from tools.tracking_overlap_probe import TrackingOverlapProbe


def make_detector():
    """Functionality: Build a transparent fake of the original sequential SDK pipeline.
    Inputs: None. Outputs: detector, graph, event list and unique map. Logic: dynamically dispatch
    instance hooks like the real pipeline. Constraints: no CUDA, image arithmetic or timing claim.
    """
    events, depth_map, image = [], object(), object()

    def associate(image, boxes):
        """Functionality: Identify CPU association order. Inputs: same image/boxes. Outputs: IDs.
        Logic: append one event. Constraints: synthetic association only.
        """
        events.append("associate")
        return ["tracked"]

    def graph(tensor, *args, **kwargs):
        """Functionality: Mark fake enqueue. Inputs: SDK tensor/options. Outputs: raw.
        Logic: append one event. Constraints: no GPU execution or simulated waiting.
        """
        events.append("graph")
        return "raw"

    predictor = SimpleNamespace(inference=graph)

    def depth(image):
        """Functionality: Exercise hooks inside the SDK's unmodified stage order.
        Inputs: image. Outputs: unique depth object. Logic: preprocess/inference/sync/postprocess.
        Constraints: sync is an order marker, not a real CUDA completion assertion.
        """
        events.append("depth_pre")
        predictor.inference("tensor")
        events.extend(["sdk_sync", "depth_post"])
        return depth_map

    detector = SimpleNamespace(precision="FP32", count=0,
        distance_strategy=SimpleNamespace(config=SimpleNamespace(distance_mode="depth")),
        object_tracker=SimpleNamespace(associate=associate, tracker=SimpleNamespace(
            args=SimpleNamespace(with_reid=False, gmc_method="sparseOptFlow"))),
        depth_estimator=SimpleNamespace(predict=depth, model=SimpleNamespace(predictor=predictor)))

    def predict(jpeg, *, timestamp=None):
        """Functionality: Run the original association then depth consumption boundary.
        Inputs: JPEG/source seconds. Outputs: deterministic metadata and original JPEG.
        Logic: call current methods once, verify same returned map, then mark fusion.
        Constraints: timing constants are synthetic values for orchestration tests only.
        """
        boxes = detector.object_tracker.associate(image, ["detected"])
        assert detector.depth_estimator.predict(image) is depth_map
        events.append("fusion")
        detector.count += 1
        return {"server_ms": 20, "tracking_ms": 10, "depth_predict_ms": .01,
                "depth_ms": 3, "distance_ms": 3, "boxes": boxes, "timestamp": timestamp,
                "performance": {"total_frames": detector.count, "average_distance_ms": 3}}, jpeg

    detector.predict = predict
    return detector, graph, events, depth_map


class TrackingOverlapTests(SimpleTestCase):
    """Functionality: Guard scheduling and fault boundaries independently of model numerics.
    Logic: real adapter and explicit dependency doubles. Constraints: no hardware performance claim.
    """

    def test_order_and_restoration(self):
        """Functionality: Enqueue before CPU work and consume exactly once after original SDK sync.
        Inputs: transparent pipeline and two frames. Outputs: expected order, identities and hooks.
        Logic: inspect events, source time and restored bound methods. Constraints: no extra sync.
        """
        detector, graph, events, _ = make_detector()
        original_associate = detector.object_tracker.associate
        original_depth = detector.depth_estimator.predict
        probe = TrackingOverlapProbe(detector, graph)
        with patch("torch.cuda.current_stream") as stream:
            for _ in range(2):
                metadata, jpeg = probe(b"input", timestamp=2.5)
                self.assertEqual(metadata["timestamp"], 2.5)
                self.assertEqual(jpeg, b"input")
                self.assertEqual(metadata["stage_timing_model"], "overlapping_cpu_depth")
                self.assertIs(detector.object_tracker.associate, original_associate)
                self.assertIs(detector.depth_estimator.predict, original_depth)
                self.assertIs(detector.depth_estimator.model.predictor.inference, graph)
                self.assertIsNone(probe.frame)
            stream.return_value.synchronize.assert_not_called()
        expected = ["depth_pre", "graph", "associate", "sdk_sync", "depth_post", "fusion"]
        self.assertEqual(events, expected * 2)

    def test_failure_drain(self):
        """Functionality: Drain after queued GPU work, latch failure and restore all hooks.
        Inputs: failing graph or CPU association. Outputs: original failure and no subsequent work.
        Logic: assert one stream drain and original binding restoration. Constraints: no retry.
        """
        for phase in ("graph", "associate"):
            detector, graph, _, _ = make_detector()
            failure = Mock(side_effect=ValueError("explicit failure"))
            if phase == "graph":
                graph = failure
                detector.depth_estimator.model.predictor.inference = graph
            else:
                detector.object_tracker.associate = failure
            original_depth = detector.depth_estimator.predict
            probe = TrackingOverlapProbe(detector, graph)
            with patch("torch.cuda.current_stream") as stream:
                with self.assertRaisesMessage(ValueError, "explicit failure"):
                    probe(b"input")
                stream.return_value.synchronize.assert_called_once()
                with self.assertRaises(RuntimeError):
                    probe(b"again")
            self.assertIsNone(probe.frame)
            self.assertIs(detector.depth_estimator.predict, original_depth)
            self.assertIs(detector.depth_estimator.model.predictor.inference, graph)
            failure.assert_called_once()

    def test_consumption_identity(self):
        """Functionality: Reject duplicate and foreign-image depth consumption.
        Inputs: one completed fake frame map and another image object. Outputs: exact one-time map.
        Logic: directly exercise ownership transitions. Constraints: no fallback to a model call.
        """
        detector, graph, _, depth = make_detector()
        probe = TrackingOverlapProbe(detector, graph)
        image = object()
        probe.frame = {"phase": "ready", "image": image, "depth_map": depth}
        with self.assertRaises(RuntimeError):
            probe.depth(object())
        self.assertIs(probe.depth(image), depth)
        with self.assertRaises(RuntimeError):
            probe.depth(image)

    def test_session_tracker(self):
        """Functionality: Respect tracker replacement on reconnect without recapturing Graph.
        Inputs: two tracker instances and reset frame counters. Outputs: only current tracker used.
        Logic: replace the owner between calls, like ResidentService.begin. Constraints: synthetic.
        """
        detector, graph, _, _ = make_detector()
        probe = TrackingOverlapProbe(detector, graph)
        with patch("torch.cuda.current_stream"):
            probe(b"first")
            replacement = Mock(return_value=["new-track"])
            detector.object_tracker = SimpleNamespace(associate=replacement,
                tracker=SimpleNamespace(args=SimpleNamespace(
                    with_reid=False, gmc_method="sparseOptFlow")))
            detector.count = 0
            second, _ = probe(b"second")
        self.assertEqual(second["boxes"], ["new-track"])
        self.assertEqual(second["performance"]["total_frames"], 1)
        self.assertIs(detector.object_tracker.associate, replacement)
        replacement.assert_called_once()

    def test_explicit_contract(self):
        """Functionality: Reject precision, mode, ReID or Graph substitutions before frame work.
        Inputs: individually invalid conditions. Outputs: latched contract error without GPU access.
        Logic: mutate test owners. Constraints: no implicit provider or scheduling substitution.
        """
        for field in ("precision", "distance_mode", "with_reid", "graph"):
            detector, graph, events, _ = make_detector()
            if field == "precision":
                detector.precision = "FP16"
            elif field == "distance_mode":
                detector.distance_strategy.config.distance_mode = "temporal_depth"
            elif field == "with_reid":
                detector.object_tracker.tracker.args.with_reid = True
            else:
                detector.depth_estimator.model.predictor.inference = Mock()
            probe = TrackingOverlapProbe(detector, graph)
            with self.assertRaises(ValueError):
                probe(b"input")
            self.assertTrue(probe.failed)
            self.assertEqual(events, [])

    def test_device_observation(self):
        """Functionality: Record optional events without successful-path synchronization.
        Inputs: explicit event states around CPU work. Outputs: unchanged observations/elapsed time.
        Logic: queries surround association; elapsed_time is read after original SDK completion.
        Constraints: fake event states verify reporting only, not actual concurrency.
        """
        detector, graph, _, _ = make_detector()
        begin, end = Mock(), Mock()
        end.query.side_effect = [False, True, True]
        begin.elapsed_time.return_value = 3.5
        probe = TrackingOverlapProbe(detector, graph, observe_device=True)
        with (patch("torch.cuda.current_stream") as stream,
              patch("torch.cuda.Event", side_effect=[begin, end])):
            metadata, _ = probe(b"input")
            stream.return_value.synchronize.assert_not_called()
        evidence = metadata["tracking_overlap_experiment"]
        self.assertFalse(evidence["device_done_before_association"])
        self.assertTrue(evidence["device_done_after_association"])
        self.assertEqual(evidence["graph_stream_elapsed_ms"], 3.5)

    def test_timing_accounting(self):
        """Functionality: Distinguish CPU association from the overlapping complete SDK call.
        Inputs: exact wall durations and two-frame statistics. Outputs: corrected fields.
        Logic: subtract the early depth slot from tracking, replace cache lookup with SDK duration.
        Constraints: shared-stage sums intentionally exceed wall time and are never speedup proof.
        """
        detector, graph, _, _ = make_detector()
        probe = TrackingOverlapProbe(detector, graph)
        probe.frame = {"depth_call_ms": 12, "association_cpu_ms": 5, "graph_submit_host_ms": 1}
        original = {"tracking_ms": 13, "depth_predict_ms": .1, "depth_ms": 4.1,
                    "distance_ms": 3.1, "server_ms": 20,
                    "performance": {"total_frames": 2, "average_distance_ms": 4}}
        result = probe.annotate_timings(original)
        self.assertEqual(result["tracking_ms"], 6)
        self.assertEqual(result["depth_predict_ms"], 12)
        self.assertEqual(result["depth_ms"], 16)
        self.assertEqual(result["distance_ms"], 15)
        self.assertEqual(result["performance"]["average_distance_ms"], 9.95)
        self.assertEqual(original["tracking_ms"], 13)
        self.assertEqual(result["server_ms"], 20)

    def test_reentrancy(self):
        """Functionality: Refuse a second frame while another frame owns shared buffers.
        Inputs: deliberately occupied adapter. Outputs: pre-work RuntimeError.
        Logic: validate frame ownership before hooks/Graph. Constraints: no queue or hidden retry.
        """
        detector, graph, events, _ = make_detector()
        probe = TrackingOverlapProbe(detector, graph)
        probe.frame = {"phase": "depth"}
        with self.assertRaises(RuntimeError):
            probe(b"input")
        self.assertEqual(events, [])

    def test_bad_input_recovery(self):
        """Functionality: Preserve recoverable input errors only before any frame/GPU mutation.
        Inputs: original invalid-frame/size rejection, then valid input; same error during Graph.
        Outputs: valid frame succeeds after preflight rejection; post-submission failure latches.
        Logic: inspect phase and stream ownership, restore hooks in both cases.
        Constraints: no automatic retry; explicit later calls exercise the original service policy.
        """
        for code in ("invalid_frame", "frame_size"):
            detector, graph, events, _ = make_detector()
            original = detector.predict
            detector.predict = Mock(side_effect=DetectionError(code, "synthetic input", 400))
            probe = TrackingOverlapProbe(detector, graph)
            with patch("torch.cuda.current_stream") as stream:
                with self.assertRaises(DetectionError):
                    probe(b"bad")
                self.assertFalse(probe.failed)
                self.assertEqual(events, [])
                stream.assert_not_called()
                probe.original_predict = original
                probe(b"explicit valid request")
                stream.return_value.synchronize.assert_not_called()
            detector, _, _, _ = make_detector()
            graph = Mock(side_effect=DetectionError(code, "post-submission failure", 400))
            detector.depth_estimator.model.predictor.inference = graph
            probe = TrackingOverlapProbe(detector, graph)
            with patch("torch.cuda.current_stream") as stream:
                with self.assertRaises(DetectionError):
                    probe(b"valid")
                self.assertTrue(probe.failed)
                stream.return_value.synchronize.assert_called_once()
