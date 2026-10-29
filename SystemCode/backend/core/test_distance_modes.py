"""Responsibilities: Verify A compatibility, geometry isolation, temporal identity and metrics.
Implementation: Real shared predict/fusion/history/packet code with counted synthetic model calls.
Related Modules: realtime.distance/distance_config/performance/detector; integration.protocol;
integration.inference.services; model_tests.run_depth; tools.deploy_space.
Declaration Index: box; resident; DistanceModeTests; DistanceModeTests.test_configuration;
DistanceModeTests.test_geometry_formula_and_scaling; DistanceModeTests.test_geometry_unavailable;
DistanceModeTests.test_cache_identity_and_last_valid; DistanceModeTests.test_temporal_schedule;
DistanceModeTests.test_temporal_epoch_and_geometry; DistanceModeTests.test_shared_pipeline;
DistanceModeTests.test_interval_one_matches_a; DistanceModeTests.test_cached_motion_is_not_measured;
DistanceModeTests.test_geometry_loader_without_depth; DistanceModeTests.test_geometry_local_loader;
DistanceModeTests.test_contracts; DistanceModeTests.test_performance;
DistanceModeTests.test_deployment_closure; DistanceModeTests.test_cli_profiles;
DistanceModeTests.test_allocated_strategy_lifetime; DistanceModeTests.test_epoch_resets_statistics.
DistanceModeTests.test_local_profile_is_fixed.
Variable Index: None. Synthetic latency/distances are not CUDA or physical accuracy evidence.
"""

import ast
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
from django.test import SimpleTestCase
from PIL import Image

from core.detector import DetectionError
from core.test_frames import jpeg
from integration.errors import IntegrationError
from integration.protocol import model_contract, validate_contract, validate_result
from realtime.depth import fuse_object_depth
from realtime.detector import ResidentDetector
from realtime.distance import DistanceStrategy, GeometryDistanceEstimator, TemporalDepthCache
from realtime.distance_config import DEFAULT_DISTANCE_CONFIG, DistanceConfig, read_distance_config
from realtime.performance import PerformanceStats
from realtime.protocol import pack_result, unpack_result
from realtime.tracking import DEFAULT_TRACKING_CONFIG, TrackHistory


def box(identity=5, *, distance=None, label="person"):
    """Functionality: Build one independently specified detector observation.
    Inputs: ID/distance/class. Outputs: legacy-compatible box. Logic: fixed 100px height.
    Constraints: synthetic coordinates and labels only.
    """
    return {"track_id": identity, "label": label, "class_id": 0, "score": .93,
            "xyxy": [20., 20., 80., 120.], "distance_m": distance}


def resident(config):
    """Functionality: Inject only model/association boundaries around actual shared prediction.
    Inputs: DistanceConfig. Outputs: resident detector with persistent real motion history.
    Logic: deterministic model row and stable ID; actual decoding, distance, risk and annotation.
    Constraints: no actual CUDA, model initialization or external service.
    """
    instance = object.__new__(ResidentDetector)
    rows = np.zeros((1, 300, 6), dtype=np.float32)
    rows[0, 0] = [64, 64, 320, 576, .93, 0]
    instance.runner = MagicMock(return_value=(rows, 1.))
    instance.runner.forward_event_ms, instance.runner.evidence = .5, {}
    instance.tensor = np.empty((1, 3, 640, 640), dtype=np.float32)
    instance.manifest = {"names": ["person"] * 80, "source_sha256": "a" * 64}
    instance.precision, instance.backend = "FP32", "PyTorch resident CUDA"
    instance.depth_estimator = MagicMock(model_name="yolo26n-depth")
    instance.depth_estimator.predict.return_value = np.full((80, 120), 3.8, dtype=np.float32)
    instance.distance_strategy = DistanceStrategy(config)
    instance.object_tracker = MagicMock(epoch=0)
    instance.object_tracker.associate.side_effect = lambda image, boxes: [
        {**item, "track_id": 5} for item in boxes]
    instance.object_tracker.enrich.side_effect = TrackHistory(DEFAULT_TRACKING_CONFIG).update
    instance.object_tracker.metadata.return_value = {
        "tracking_model": "botsort", "tracking_status": "active", "tracking_epoch": 0,
        "gmc_method": "sparseOptFlow", "with_reid": False}
    return instance


class DistanceModeTests(SimpleTestCase):
    """Functionality: Guard explicit experiment branches and existing A output behavior.
    Logic: independent numerical expectations, model-call counts and identity changes.
    Constraints: actual GPU/physical measurement remains separate from these deterministic tests.
    """

    def test_local_profile_is_fixed(self):
        """Functionality: Bind lazy model loading to the profile advertised at worker startup.
        Inputs: counted config reader and loader boundary. Outputs: exact profile forwarding.
        Logic: hello precedes model initialization, so edits cannot alter its selected identity.
        Constraints: no actual model/files/GPU work; operator restarts to change a profile.
        """
        from integration.inference import services

        config = DistanceConfig(distance_mode="geometry")
        with patch.object(services, "read_distance_config", return_value=config) as reader, \
                patch.object(services, "load_detector") as loader:
            runtime = services.InferenceRuntime(factory=loader)
            try:
                runtime.factory()
                loader.assert_called_once_with(distance_config=config)
                reader.assert_called_once()
            finally:
                runtime.executor.shutdown()

    def test_allocated_strategy_lifetime(self):
        """Functionality: Initialize per-allocation strategy and forward it through the HF worker.
        Inputs: synthetic model/manifest/depth and explicit profiles. Outputs: no B activation,
        one C activation and exact factory handoff. Logic: real constructors, no CUDA invocation.
        Constraints: verifies worker wiring, not a remote HF allocation or inference.
        """
        from zerogpu.live_worker import AllocatedDetector, run_worker

        depth = MagicMock()
        geometry = DistanceConfig(distance_mode="geometry")
        detector = AllocatedDetector(None, {}, depth_estimator=depth, tracking_config=None,
                                     distance_config=geometry)
        self.assertIsNone(detector.depth_estimator)
        depth.activate.assert_not_called()
        temporal = DistanceConfig(distance_mode="temporal_depth", depth_interval=5)
        with patch("zerogpu.live_worker.ObjectTracker") as tracker:
            detector = AllocatedDetector(None, {"build": {"ultralytics": "fixture"}},
                                         depth_estimator=depth, distance_config=temporal)
            self.assertEqual(detector.distance_strategy.config, temporal)
            depth.activate.assert_called_once()
            tracker.assert_called_once()
        factory = MagicMock()
        with patch("zerogpu.live_worker.exchange", side_effect=[(200, b""), (410, b""),
                                                               (200, b"")]):
            run_worker("a" * 64, None, None, factory=factory, distance_config=geometry)
        factory.assert_called_once_with(None, None, distance_config=geometry)

    def test_epoch_resets_statistics(self):
        """Functionality: Keep metrics/cache local when a resident tracker begins another session.
        Inputs: two same-stream frames then a new epoch with reused ID/source-clock reset.
        Outputs: immediate fresh depth, independent1-frame count and cache timestamp.
        Logic: actual predict with reset history boundary. Constraints: model/graph remain reused.
        """
        detector = resident(DistanceConfig(distance_mode="temporal_depth"))
        detector.predict(jpeg(), timestamp=1.)
        detector.predict(jpeg(), timestamp=2.)
        detector.object_tracker.epoch = 1
        detector.object_tracker.enrich.side_effect = TrackHistory(DEFAULT_TRACKING_CONFIG).update
        detector.depth_estimator.predict.return_value = np.full((80, 120), 2., dtype=np.float32)
        metadata, _ = detector.predict(jpeg(), timestamp=.5)
        self.assertTrue(metadata["depth_frame"])
        self.assertEqual(metadata["boxes"][0]["distance_m"], 2.)
        self.assertEqual(metadata["performance"]["total_frames"], 1)
        self.assertEqual(detector.distance_strategy.cache.entries[5].timestamp, .5)

    def test_configuration(self):
        """Functionality: Validate defaults, JSON, precedence and invalid experimental values.
        Inputs: explicit file/environment/CLI. Outputs: exact selected config or early error.
        Logic: no model construction. Constraints: caller parameters never silently corrected.
        """
        self.assertEqual(read_distance_config(environ={}), DEFAULT_DISTANCE_CONFIG)
        for settings in ({"distance_mode": "bad"}, {"depth_interval": 0}, {"depth_interval": True},
                         {"depth_interval": 1.2}, {"focal_length_px": float("nan")},
                         {"reference_width_px": 0}, {"object_heights_m": {"person": -1}},
                         {"object_heights_m": [("person", 1), ("person", 2)]}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                DistanceConfig(**settings)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            path.write_text(json.dumps({"distance_mode": "geometry", "depth_interval": 5,
                                        "object_heights_m": {"person": 1.8}}), encoding="utf-8")
            config = read_distance_config(path, environ={})
            self.assertEqual(config.object_heights_m, (("person", 1.8),))
            selected = read_distance_config(path, environ={"PRS_DISTANCE_MODE": "temporal_depth",
                                                         "PRS_DEPTH_INTERVAL": "2"})
            self.assertEqual((selected.distance_mode, selected.depth_interval),
                             ("temporal_depth", 2))
            self.assertEqual(read_distance_config(path, distance_mode="depth", depth_interval=1,
                                                 environ={}).depth_interval, 1)

    def test_geometry_formula_and_scaling(self):
        """Functionality: Match the independent pinhole formula and resize calibration correctly.
        Inputs: fy=800 at640 width, person1.7m,100/50px boxes. Outputs:13.6m at both resolutions.
        Logic: scale fy and bbox together. Constraints: frame coordinates, no padded input geometry.
        """
        estimator = GeometryDistanceEstimator(DistanceConfig(focal_length_px=800))
        original = box()
        result = estimator.fuse([original], (640, 480))[0]
        self.assertAlmostEqual(result["distance_m"], 13.6)
        half = {**original, "xyxy": [10, 10, 40, 60]}
        self.assertAlmostEqual(estimator.estimate(half, (320, 240)), 13.6)
        self.assertIsNone(original["distance_m"])
        self.assertEqual(result["track_id"], 5)
        self.assertEqual(result["bbox"], original["xyxy"])
        custom = GeometryDistanceEstimator(DistanceConfig(object_heights_m={"bus": 4}))
        self.assertEqual(custom.estimate(box(label="bus"), (640, 480)), 28.)

    def test_geometry_unavailable(self):
        """Functionality: Keep unsupported/degenerate/nonfinite geometry unavailable.
        Inputs: malformed observations. Outputs: None without model calls or mutation.
        Logic: exercise invalid boxes and unknown categories. Constraints: no substitute class.
        """
        estimator = GeometryDistanceEstimator(DEFAULT_DISTANCE_CONFIG)
        for coordinates in (None, [], [0, 0, 1], [0, 0, 0, 100], [0, 100, 1, 0],
                            [0, 0, float("inf"), 100], [-1, 0, 1, 100], [0, 0, 1, 500]):
            self.assertIsNone(estimator.estimate({**box(), "xyxy": coordinates}, (640, 480)))
        self.assertIsNone(estimator.estimate(box(label="dog"), (640, 480)))

    def test_cache_identity_and_last_valid(self):
        """Functionality: Reuse last valid depth, isolate new/missing/reused/class-changed IDs.
        Inputs: measured/invalid/skipped frames. Outputs: last-valid or null and bounded entries.
        Logic: absent IDs purge immediately. Constraints: missing fresh depth stays null this frame.
        """
        cache = TemporalDepthCache()
        cache.apply([box(distance=3.8)], depth_frame=True, frame_index=0, timestamp=1.)
        cached = cache.apply([box(), box(6), box(None)], depth_frame=False,
                             frame_index=1, timestamp=2.)
        self.assertEqual([item["distance_m"] for item in cached], [3.8, None, None])
        self.assertEqual(cache.entries[5].last_depth_frame, 0)
        self.assertEqual(cache.entries[5].timestamp, 1.)
        invalid = cache.apply([box()], depth_frame=True, frame_index=3, timestamp=4.)
        self.assertIsNone(invalid[0]["distance_m"])
        self.assertEqual(cache.apply([box()], depth_frame=False, frame_index=4,
                                     timestamp=5.)[0]["distance_m"], 3.8)
        cache.apply([], depth_frame=False, frame_index=5, timestamp=6.)
        self.assertEqual(cache.entries, {})
        self.assertIsNone(cache.apply([box()], depth_frame=False, frame_index=6,
                                      timestamp=7.)[0]["distance_m"])
        cache.apply([box(distance=2)], depth_frame=True, frame_index=7, timestamp=8.)
        self.assertIsNone(cache.apply([box(label="car")], depth_frame=False, frame_index=8,
                                      timestamp=9.)[0]["distance_m"])
        cache.reset()
        self.assertEqual(cache.entries, {})

    def test_temporal_schedule(self):
        """Functionality: Run depth only on frame1 then every selected interval including1.
        Inputs: intervals1/2/3/5 over11 frames. Outputs: exact calls and last-valid values.
        Logic: counted changing synthetic depth maps. Constraints: non-depth frames never infer.
        """
        image = Image.new("RGB", (160, 160))
        for interval in (1, 2, 3, 5):
            strategy = DistanceStrategy(DistanceConfig(distance_mode="temporal_depth",
                                                       depth_interval=interval))
            depth = MagicMock()
            depth.predict.side_effect = [np.full((160, 160), value, dtype=float)
                                         for value in range(1, 12)]
            for index in range(11):
                result, called = strategy.apply(image, [box()], depth, timestamp=float(index))
                self.assertEqual(called, index % interval == 0)
                self.assertEqual(result[0]["distance_m"], index // interval + 1)
            self.assertEqual(depth.predict.call_count, 10 // interval + 1)
        depth.predict.side_effect = DetectionError("depth_inference_failed", "fixture", 502)
        with self.assertRaises(DetectionError):
            DistanceStrategy(DistanceConfig(distance_mode="temporal_depth")).apply(
                image, [box()], depth, timestamp=20.)

    def test_temporal_epoch_and_geometry(self):
        """Functionality: Clear temporal identities and force first depth after explicit resets.
        Inputs: reused ID under new epoch/frame geometry. Outputs: new measured values immediately.
        Logic: stream keys reset schedule/cache together. Constraints: no stale cross-session depth.
        """
        strategy = DistanceStrategy(DistanceConfig(distance_mode="temporal_depth",
                                                   depth_interval=5))
        depth = MagicMock()
        for index, (epoch, size) in enumerate(((0, (160, 160)), (1, (160, 160)), (1, (200, 200)))):
            depth.predict.return_value = np.full(size, index + 1, dtype=float)
            result, called = strategy.apply(Image.new("RGB", size), [box()], depth,
                                            timestamp=float(index), epoch=epoch)
            self.assertTrue(called)
            self.assertEqual(result[0]["distance_m"], index + 1)
            self.assertEqual(len(strategy.cache.entries), 1)

    def test_shared_pipeline(self):
        """Functionality: Keep detection/packets/risk compatible for all three strategies.
        Inputs: actual JPEG/shared predict with stable synthetic model. Outputs: original boxes,
        correct depth invocation counts, nullable distances and processing statistics.
        Logic: compare raw hashes/legacy boxes across strategies. Constraints: no GPU/FPS claim.
        """
        outputs = []
        for mode, count in (("depth", 7), ("geometry", 0), ("temporal_depth", 3)):
            detector = resident(DistanceConfig(distance_mode=mode, depth_interval=3))
            for index in range(7):
                metadata, rendered = detector.predict(jpeg(), timestamp=index / 10)
                wire = {**metadata, "index": index}
                self.assertEqual(unpack_result(pack_result(wire, rendered), index)[0], wire)
                self.assertEqual(metadata["boxes"][0]["track_id"], 5)
                self.assertIn("risk_level", metadata["boxes"][0])
                self.assertGreater(metadata["boxes"][0]["distance_m"], 0)
            self.assertEqual(detector.depth_estimator.predict.call_count, count)
            self.assertEqual(metadata["performance"]["depth_model_calls"], count)
            self.assertEqual(metadata["performance"]["total_frames"], 7)
            if mode == "geometry":
                self.assertNotIn("depth_model", metadata)
            outputs.append(metadata)
        for metadata in outputs[1:]:
            self.assertEqual(metadata["raw_sha256"], outputs[0]["raw_sha256"])
            for key in ("xyxy", "label", "score", "class_id"):
                self.assertEqual(metadata["boxes"][0][key], outputs[0]["boxes"][0][key])

    def test_interval_one_matches_a(self):
        """Functionality: Prove A still delegates exact fusion and C interval1 has A object values.
        Inputs: same measurements and source clocks. Outputs: equal boxes/JPEG except provenance.
        Logic: independent A and C histories. Constraints: additive timing/mode fields may differ.
        """
        a = resident(DEFAULT_DISTANCE_CONFIG)
        c = resident(DistanceConfig(distance_mode="temporal_depth", depth_interval=1))
        for index in range(5):
            first, rendered_a = a.predict(jpeg(), timestamp=index / 10)
            second, rendered_c = c.predict(jpeg(), timestamp=index / 10)
            c_box = {key: value for key, value in second["boxes"][0].items()
                     if key != "distance_source"}
            self.assertEqual(c_box, first["boxes"][0])
            self.assertEqual(rendered_a, rendered_c)
        legacy = {key: first["boxes"][0][key] for key in ("label", "score", "class_id", "xyxy")}
        direct = fuse_object_depth(a.depth_estimator.predict.return_value, [legacy], (120, 80))[0]
        self.assertEqual(first["boxes"][0]["distance_m"], direct["distance_m"])

    def test_cached_motion_is_not_measured(self):
        """Functionality: Prevent repeated historical distances from fabricating velocity samples.
        Inputs: fresh then two cached samples. Outputs: one measurement/unknown motion.
        Logic: actual TrackHistory update. Constraints: raw cached meters remain available to risk.
        """
        detector = resident(DistanceConfig(distance_mode="temporal_depth"))
        for index in range(3):
            metadata, _ = detector.predict(jpeg(), timestamp=index / 10)
        self.assertEqual(metadata["boxes"][0]["distance_source"], "cached")
        self.assertIsNone(metadata["boxes"][0]["relative_speed_mps"])
        self.assertEqual(metadata["boxes"][0]["motion_state"], "unknown")

    def test_geometry_loader_without_depth(self):
        """Functionality: Prove B loader never initializes depth even with nonexistent weights.
        Inputs: manifest and missing depth path. Outputs: resident constructor with None depth.
        Logic: patch actual constructor boundaries. Constraints: CUDA/model execution is separate.
        """
        from model_tests.run_depth import build_detector

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text('{"build":{"ultralytics":"fixture"}}', encoding="utf-8")
            with patch("realtime.depth.DepthEstimator") as depth, \
                    patch("realtime.detector.ResidentDetector") as factory:
                build_detector(path, path, Path(directory) / "missing.pt",
                               distance_config=DistanceConfig(distance_mode="geometry"))
                depth.assert_not_called()
                self.assertIsNone(factory.call_args.kwargs["depth_estimator"])
            with self.assertRaises(ValueError):
                build_detector(path, path, path,
                               distance_config=DistanceConfig(distance_mode="temporal_depth"))

    def test_geometry_local_loader(self):
        """Functionality: Permit local B without depth weight access or a partial pipeline error.
        Inputs: real detection file/hash and no depth file. Outputs: B resident constructor only.
        Logic: inject minimal Torch CUDA probe. Constraints: no actual model deserialization.
        """
        import hashlib
        import sys

        from integration.inference import services

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "models").mkdir()
            weights = root / "models/yolo26n.pt"
            weights.write_bytes(b"explicit detection fixture")
            manifest = {"source_sha256": hashlib.sha256(weights.read_bytes()).hexdigest()}
            torch = MagicMock()
            torch.cuda.is_available.return_value = True
            config = DistanceConfig(distance_mode="geometry")
            with patch.object(services, "ROOT", root), \
                    patch.object(services, "read_distance_config", return_value=config), \
                    patch.object(services, "read_manifest", return_value=manifest), \
                    patch.dict("os.environ", {"PRS_DETECTION_MODEL": "yolo26n"}), \
                    patch.dict(sys.modules, {"torch": torch}), \
                    patch("realtime.depth.DepthEstimator") as depth, \
                    patch("realtime.detector.ResidentDetector") as factory:
                _, hashes = services.load_detector()
                depth.assert_not_called()
                self.assertIsNone(hashes["depth_sha256"])
                self.assertEqual(factory.call_args.kwargs["distance_config"], config)

    def test_contracts(self):
        """Functionality: Admit explicit A/B/C sessions without weakening default A requirements.
        Inputs: independently shaped hello/result records. Outputs: acceptance or stable rejection.
        Logic: B omits model identity and selects geometry explicitly. Constraints: tracking/risk
        and source correlation remain mandatory in all modes.
        """
        manifest = {"names": ["person"] * 80, "source_sha256": "a" * 64}
        for mode in ("depth", "geometry", "temporal_depth"):
            contract = model_contract("a" * 32, manifest, distance_mode=mode)
            self.assertIs(validate_contract(contract), contract)
            result = {**contract, "index": 0, "capture_time_s": 1.,
                      "timestamp_source": "browser_capture"}
            validate_result(result, contract, 0, 1.)
            with self.assertRaises(IntegrationError):
                validate_result({**result, "risk_model": None}, contract, 0, 1.)
        default = model_contract("a" * 32, manifest)
        with self.assertRaises(IntegrationError):
            validate_contract({**default, "depth_model": None})
        with self.assertRaises(IntegrationError):
            validate_contract({**default, "distance_mode": "unknown"})

    def test_performance(self):
        """Functionality: Verify measured buckets/averages/counts with independent exact timings.
        Inputs: one30ms depth frame and two3ms skipped frames. Outputs: ratio1/3,avg12ms,FPS83.33.
        Logic: fixed scalar arithmetic. Constraints: no benchmark assertions on wall-clock speed.
        """
        stats = PerformanceStats()
        for total, called in ((30., True), (3., False), (3., False)):
            result = stats.record(detection_ms=1., distance_ms=total - 2,
                                  end_to_end_ms=total, depth_frame=called)
        self.assertEqual(result["total_frames"], 3)
        self.assertEqual(result["depth_model_calls"], 1)
        self.assertEqual(result["average_end_to_end_ms"], 12)
        self.assertEqual(result["average_depth_frame_ms"], 30)
        self.assertEqual(result["average_non_depth_frame_ms"], 3)
        self.assertAlmostEqual(result["actual_depth_inference_ratio"], 1 / 3)
        self.assertAlmostEqual(result["fps"], 1000 / 12)

    def test_deployment_closure(self):
        """Functionality: Stage all new worker dependencies and the editable geometry profile.
        Inputs: actual publisher AST. Outputs: source mapping exists on disk.
        Logic: inspect static allowlist only. Constraints: no staging/publishing/network action.
        """
        root = Path(__file__).resolve().parents[1]
        tree = ast.parse((root / "tools/deploy_space.py").read_text(encoding="utf-8"))
        mapping = next(ast.literal_eval(node.value) for node in tree.body
                       if isinstance(node, ast.Assign) and any(
                           isinstance(target, ast.Name) and target.id == "FILES"
                           for target in node.targets))
        for name in ("distance", "distance_config", "performance"):
            path = f"realtime/{name}.py"
            self.assertEqual(mapping[path], path)
            self.assertTrue((root / path).is_file())
        self.assertIn("models/distance-config.json", mapping)

    def test_cli_profiles(self):
        """Functionality: Persist B calibration/no depth hash and reject C without tracking early.
        Inputs: real tiny image plus CLI fixture paths. Outputs: structured report and no depth I/O.
        Logic: call production run with mocked inference only. Constraints: no CUDA proof.
        """
        from model_tests import run_depth
        from model_tests.test_cli import DepthCliTests

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "image.jpg"
            Image.new("RGB", (20, 20)).save(image)
            args = DepthCliTests().arguments(root, image)
            args.distance_mode = "geometry"
            args.depth_weights = root / "missing.pt"
            detector = MagicMock()
            detector.predict.return_value = ({"boxes": [], "performance": {"total_frames": 1}},
                                             b"explicit rendered bytes")
            with patch.object(run_depth, "build_detector", return_value=detector) as factory, \
                    patch("builtins.print"):
                report = run_depth.run(args)
            self.assertIsNone(report["models"]["depth_sha256"])
            self.assertEqual(report["performance"]["total_frames"], 1)
            self.assertEqual(factory.call_args.kwargs["distance_config"].distance_mode, "geometry")
            self.assertEqual(json.loads((args.output / "run.json").read_text(encoding="utf-8")),
                             report)
            args.distance_mode = "temporal_depth"
            with patch.object(run_depth, "build_detector") as factory, \
                    self.assertRaises(ValueError):
                run_depth.run(args)
            factory.assert_not_called()
