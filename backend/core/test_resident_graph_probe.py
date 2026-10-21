"""Responsibilities: Verify experimental capture cost, reuse and failure boundaries.
Implementation: Explicit graph preparation double and actual resident worker/session lifecycle;
real CUDA replay/buffer ownership is separately covered by core.test_inference_benchmark.
Related Modules: tools.resident_graph_probe; realtime.services; core.test_resident.
Declaration Index: GraphServiceTests; GraphServiceTests.test_first_cost_and_reuse;
GraphServiceTests.test_failure_and_input_rejection; GraphServiceTests.test_session_worker_reuse;
GraphServiceTests.test_session_worker_reuse.factory;
GraphServiceTests.test_session_worker_reuse.predict;
GraphServiceTests.test_default_entry_isolation; GraphServiceTests.test_overlap_bridge.
Variable Index: None. Synthetic timing assertions do not claim model speed or quality.
"""

import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from core.detector import DetectionError
from realtime.services import ResidentService
from tools.resident_graph_probe import GraphServiceProbe
from tools.resident_overlap_probe import OverlapServiceProbe


class GraphServiceTests(SimpleTestCase):
    """Functionality: Guard first-response accounting and lifetime across reconnects/errors.
    Logic: independent expected clock deltas plus the actual single-thread service.
    Constraints: test doubles cover orchestration only; browser trials provide CUDA evidence.
    """

    def test_first_cost_and_reuse(self):
        """Functionality: Include preparation in first response while capturing only once.
        Inputs: original result and deterministic clock; then a second frame.
        Outputs: full response cost, retained pipeline timing, capture metadata and original JPEG.
        Logic: assert 500ms preparation within1750ms first response, then replay without setup.
        Constraints: no artificial sleep or runtime performance assertions.
        """
        detector = SimpleNamespace(predict=MagicMock(return_value=({"server_ms": 10}, b"jpeg")))
        graph = SimpleNamespace(evidence={"setup_ms": 450})
        probe = GraphServiceProbe(detector)
        with patch("tools.resident_graph_probe.prepare_depth_graph", return_value=graph) as prepare:
            with patch("tools.resident_graph_probe.time.perf_counter",
                       side_effect=[1, 2, 2.5, 2.75, 3, 3.1]):
                first, jpeg = probe(b"input")
                second, _ = probe(b"next")
        self.assertEqual(jpeg, b"jpeg")
        self.assertEqual(first["server_ms"], 1750)
        self.assertEqual(first["pipeline_server_ms"], 10)
        self.assertEqual(first["depth_graph_experiment"]["prepare_ms"], 500)
        self.assertFalse(first["depth_graph_experiment"]["replayed"])
        self.assertTrue(second["depth_graph_experiment"]["replayed"])
        self.assertEqual(second["depth_graph_experiment"]["prepare_ms"], 0)
        self.assertAlmostEqual(second["server_ms"], 100)
        prepare.assert_called_once_with(detector, b"input")
        self.assertEqual(detector.predict.call_count, 2)

    def test_failure_and_input_rejection(self):
        """Functionality: Never recapture after a pipeline failure; keep normal bad-input policy.
        Inputs: malformed input, then successful prediction with failing graph preparation.
        Outputs: original input error and latched capture error without a second inference.
        Logic: assert call counts before/after retry attempts. Constraints: no automatic fallback.
        """
        detector = SimpleNamespace(predict=MagicMock(side_effect=DetectionError(
            "invalid_frame", "test invalid input", 400)))
        probe = GraphServiceProbe(detector)
        with self.assertRaises(DetectionError):
            probe(b"bad")
        self.assertFalse(probe.failed)
        detector.predict.side_effect = None
        detector.predict.return_value = ({"server_ms": 1}, b"jpeg")
        with patch("tools.resident_graph_probe.prepare_depth_graph",
                   side_effect=ValueError("test capture mismatch")) as prepare:
            with self.assertRaises(ValueError):
                probe(b"valid")
            with self.assertRaises(RuntimeError):
                probe(b"next")
        prepare.assert_called_once()
        self.assertEqual(detector.predict.call_count, 2)

    async def test_session_worker_reuse(self):
        """Functionality: Reuse one graph on the same actual worker through two session resets.
        Inputs: real ResidentService with explicit model/graph doubles, then a shape-like failure.
        Outputs: one capture, two independent trackers, worker ownership and unavailable service.
        Logic: exercise start/begin/predict/release/close without replacing lifecycle methods.
        Constraints: no CUDA, socket, accuracy or timing claim from synthetic predictions.
        """
        threads = []
        original = MagicMock()

        def predict(raw):
            """Functionality: Record actual worker execution. Inputs: bytes. Outputs: result.
            Logic: append thread identity. Constraints: no model computation.
            """
            threads.append(threading.get_ident())
            return {"server_ms": 1}, raw

        original.side_effect = predict
        detector = SimpleNamespace(predict=original, manifest={"build": {"ultralytics": "test"}})

        def factory(root, mode):
            """Functionality: Build the explicit test adapter. Inputs: root/mode. Outputs: detector.
            Logic: bind once on worker. Constraints: no real weights or default-path substitution.
            """
            threads.append(threading.get_ident())
            detector.predict = GraphServiceProbe(detector)
            return detector

        service = ResidentService(Path("."), "full", factory=factory)
        graph = SimpleNamespace(evidence={"setup_ms": 1})
        trackers = [object(), object()]
        with patch("realtime.tracking.ObjectTracker", side_effect=trackers), patch(
                "tools.resident_graph_probe.prepare_depth_graph", return_value=graph) as prepare:
            try:
                await service.start()
                for tracker in trackers:
                    lease = service.claim()
                    await service.begin(lease)
                    self.assertIs(detector.object_tracker, tracker)
                    await service.predict(lease, b"input")
                    service.release(lease)
                self.assertIs(detector.predict.graph, graph)
                prepare.assert_called_once()
                self.assertEqual(len(set(threads)), 1)
                self.assertNotEqual(threads[0], threading.get_ident())
                lease = service.claim()
                original.side_effect = ValueError("test changed graph shape")
                with self.assertRaises(ValueError):
                    await service.predict(lease, b"different")
                self.assertFalse(service.ready)
                self.assertTrue(detector.predict.failed)
                service.release(lease)
                self.assertIsNone(service.claim())
            finally:
                await service.close()

    def test_overlap_bridge(self):
        """Functionality: Keep first capture on original pipeline and reuse one overlap adapter.
        Inputs: explicit pipeline/Graph/overlap doubles. Outputs: one setup and two overlap calls.
        Logic: detector.predict points to wrapper, but the adapter must bind the original callable.
        Constraints: orchestration proof only; actual browser tests provide model/timing evidence.
        """
        original = MagicMock(return_value=({"server_ms": 1}, b"first"))
        detector = SimpleNamespace(predict=original)
        probe = OverlapServiceProbe(detector)
        detector.predict = probe
        adapter = MagicMock(return_value=({"server_ms": 2}, b"next"))
        graph = SimpleNamespace(evidence={})
        with (patch("tools.resident_graph_probe.prepare_depth_graph", return_value=graph)
              as prepare,
              patch("tools.resident_overlap_probe.TrackingOverlapProbe", return_value=adapter)
              as install):
            first, jpeg = probe(b"input")
            self.assertEqual(jpeg, b"first")
            self.assertFalse(first["depth_graph_experiment"]["replayed"])
            for _ in range(2):
                later, jpeg = probe(b"input")
                self.assertEqual(jpeg, b"next")
                self.assertTrue(later["depth_graph_experiment"]["replayed"])
        self.assertIs(adapter.original_predict, original)
        self.assertEqual(adapter.call_count, 2)
        original.assert_called_once()
        prepare.assert_called_once()
        install.assert_called_once_with(detector, graph)

    def test_default_entry_isolation(self):
        """Functionality: Keep experimental imports out of both production entry and service.
        Inputs: actual source files. Outputs: no graph-probe imports in production paths.
        Logic: inspect dependency boundary without constructing a second executor.
        Constraints: complements actual browser baseline; no deployment verification.
        """
        root = Path(__file__).resolve().parents[1]
        for name in ("config/resident_asgi.py", "realtime/services.py", "realtime/depth.py"):
            source = (root / name).read_text(encoding="utf-8")
            for experiment in ("tools.resident_graph", "tools.resident_overlap",
                               "tools.tracking_overlap", "tools.host_fusion",
                               "tools.resident_host_fusion"):
                self.assertNotIn(experiment, source)
