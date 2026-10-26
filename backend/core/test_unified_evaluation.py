"""Responsibilities: Verify external dataset geometry, coverage, sequencing and failure isolation.
Implementation: Independent numerical expectations, tiny official-Devkit fixtures and fake workers.
Related Modules: model_tests.unified_evaluation; existing A/B/C/E modules are reused unchanged.
Declaration Index: make_nuscenes; frame; UnifiedScoringTests;
UnifiedScoringTests.test_class_mapping; UnifiedScoringTests.test_one_to_one_and_coverage;
UnifiedScoringTests.test_errors_and_bins; UnifiedScoringTests.test_paired_intersection;
UnifiedScoringTests.test_gt_identity; UnifiedScoringTests.test_invalid_and_empty;
UnifiedScoringTests.test_sequence_boundaries; UnifiedScoringTests.test_cuda_timing_boundary;
UnifiedScoringTests.test_memory_sampler; UnifiedScoringTests.test_e_checkpoint_required;
UnifiedScoringTests.test_geometry_uses_calibration; UnifiedScoringTests.test_original_components;
UnifiedDatasetTests; UnifiedDatasetTests.test_camera_transform;
UnifiedDatasetTests.test_projection; UnifiedDatasetTests.test_prepare_and_integrity;
UnifiedDatasetTests.test_missing_files; UnifiedDatasetTests.test_partial_method_failure;
UnifiedDatasetTests.test_evaluate_temporal_pass.
UnifiedDatasetTests.test_partial_method_failure.launch;
UnifiedDatasetTests.test_evaluate_temporal_pass.reset;
UnifiedDatasetTests.test_evaluate_temporal_pass.predict.
Variable Index: HAS_DEVKIT/HAS_TORCH/HAS_PSUTIL — optional evaluation dependencies, not GPU claims.
"""

import ast
import importlib.util
import json
import math
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import Mock, patch

import numpy as np
from django.test import SimpleTestCase
from PIL import Image

from model_tests.unified_evaluation.adapters import EAdapter, NativeAdapter
from model_tests.unified_evaluation.dataset import (
    TABLES,
    audit_raw,
    camera_box,
    load_records,
    map_category,
    ordered_records,
    prepare,
    project_box,
)
from model_tests.unified_evaluation.performance import (
    MemorySampler,
    SequenceClock,
    latency_summary,
    statistics,
    synchronize,
)
from model_tests.unified_evaluation.runner import evaluate_pass, run, source_snapshot
from model_tests.unified_evaluation.scoring import paired_scores, score_frame, summarize
from object_distance.config import ROOT, write_json
from realtime.distance import DistanceStrategy
from realtime.distance_config import DistanceConfig

HAS_DEVKIT = importlib.util.find_spec("nuscenes") is not None
HAS_TORCH = importlib.util.find_spec("torch") is not None
HAS_PSUTIL = importlib.util.find_spec("psutil") is not None


def make_nuscenes(root):
    """Functionality: Build tiny locally owned tables accepted by the actual official SDK.
    Inputs: temporary root. Outputs: raw directory with two annotated RGB camera keyframes.
    Logic: identity extrinsics,one10m car and complete table/reference links.
    Constraints: synthetic data verifies implementation only, never external accuracy.
    """
    raw = root / "raw"
    for folder in ("samples/CAM_FRONT", "sweeps", "maps", "v1.0-mini"):
        (raw / folder).mkdir(parents=True)
    Image.new("L", (10, 10)).save(raw / "maps/map.png")
    identity = [1, 0, 0, 0]
    tables = {name: [] for name in TABLES}
    tables.update(
        category=[{"token": "category", "name": "vehicle.car", "description": "car"}],
        visibility=[{"token": "4", "level": "v80-100", "description": "visible"}],
        sensor=[{"token": "sensor", "channel": "CAM_FRONT", "modality": "camera"}],
        calibrated_sensor=[
            {
                "token": "calib",
                "sensor_token": "sensor",
                "translation": [0, 0, 0],
                "rotation": identity,
                "camera_intrinsic": [[100, 0, 80], [0, 100, 50], [0, 0, 1]],
            }
        ],
        ego_pose=[
            {"token": "ego", "translation": [0, 0, 0], "rotation": identity, "timestamp": 1000000}
        ],
        log=[
            {
                "token": "log",
                "logfile": "fixture",
                "vehicle": "fixture",
                "date_captured": "fixture",
                "location": "fixture",
            }
        ],
        scene=[
            {
                "token": "scene",
                "name": "scene-test",
                "log_token": "log",
                "nbr_samples": 2,
                "first_sample_token": "sample0",
                "last_sample_token": "sample1",
                "description": "synthetic fixture",
            }
        ],
        map=[
            {
                "token": "map",
                "filename": "maps/map.png",
                "log_tokens": ["log"],
                "category": "semantic_prior",
            }
        ],
        instance=[
            {
                "token": "instance",
                "category_token": "category",
                "nbr_annotations": 2,
                "first_annotation_token": "ann0",
                "last_annotation_token": "ann1",
            }
        ],
    )
    for index in range(2):
        Image.new("RGB", (160, 100), "gray").save(raw / f"samples/CAM_FRONT/{index}.jpg")
        previous = "" if index == 0 else "sample0"
        following = "sample1" if index == 0 else ""
        tables["sample"].append(
            {
                "token": f"sample{index}",
                "timestamp": 1000000 + index * 500000,
                "prev": previous,
                "next": following,
                "scene_token": "scene",
            }
        )
        tables["sample_data"].append(
            {
                "token": f"frame{index}",
                "sample_token": f"sample{index}",
                "calibrated_sensor_token": "calib",
                "ego_pose_token": "ego",
                "is_key_frame": True,
                "filename": f"samples/CAM_FRONT/{index}.jpg",
                "width": 160,
                "height": 100,
                "timestamp": 1000000 + index * 500000,
                "fileformat": "jpg",
                "prev": "",
                "next": "",
            }
        )
        tables["sample_annotation"].append(
            {
                "token": f"ann{index}",
                "sample_token": f"sample{index}",
                "instance_token": "instance",
                "translation": [0, 0, 10],
                "size": [2, 2, 2],
                "rotation": identity,
                "visibility_token": "4",
                "attribute_tokens": [],
                "num_lidar_pts": 5,
                "num_radar_pts": 0,
                "prev": "ann0" if index else "",
                "next": "" if index else "ann1",
            }
        )
    for name, rows in tables.items():
        write_json(raw / "v1.0-mini" / f"{name}.json", rows)
    return raw


def frame(identity="f", timestamp=1000000, scene="scene-a", camera="CAM_FRONT"):
    """Functionality: Produce an independent canonical observation fixture.
    Inputs: identity/source clock/stream. Outputs: one car GT at10m.
    Logic: fixed100px box and calibrated fy100. Constraints: not real data or detections.
    """
    return {
        "frame_id": identity,
        "scene_id": scene,
        "camera": camera,
        "timestamp": timestamp,
        "camera_intrinsic": [[100, 0, 50], [0, 100, 50], [0, 0, 1]],
        "objects": [
            {
                "annotation_id": "ann",
                "class_name": "car",
                "bbox": [0, 0, 100, 100],
                "distance_gt_m": 10,
            }
        ],
    }


class UnifiedScoringTests(SimpleTestCase):
    """Functionality: Validate metrics and orchestration without real model inference.
    Logic: independent expected arithmetic,explicit cache behavior and injected timing boundary.
    Constraints: mocked CUDA calls do not establish real GPU latency or model accuracy.
    """

    def test_class_mapping(self):
        """Functionality: Guard declared categories. Inputs: car/pedestrian/other names.
        Outputs: exact names/null. Logic: no category guessing. Constraints: only first-version map.
        """
        self.assertEqual(map_category("vehicle.car"), "car")
        self.assertEqual(map_category("human.pedestrian.child"), "person")
        for category in ("vehicle.bus.rigid", "vehicle.bicycle", "human.other"):
            self.assertIsNone(map_category(category))

    def test_one_to_one_and_coverage(self):
        """Functionality: Count unmatched GTs and invalid distances rather than hiding them.
        Inputs: duplicate GTs,one correct box and one wrong-class box. Outputs:50% match/coverage.
        Logic: use original one-to-one matcher. Constraints: wrong classes cannot satisfy car GT.
        """
        sample = frame()
        sample["objects"].append({**sample["objects"][0], "annotation_id": "second"})
        predictions = [
            {"class_name": "car", "bbox": [0, 0, 100, 100], "distance_m": 12},
            {"class_name": "person", "bbox": [0, 0, 100, 100], "distance_m": 10},
        ]
        rows, unused = score_frame(
            sample, predictions, bbox_mode="predicted", match_iou=0.5, depth_frame=True
        )
        result = summarize(rows)["overall"]
        self.assertEqual((unused, result["matched_objects"], result["mae_m"]), (1, 1, 2))
        self.assertEqual(result["coverage"], 0.5)
        self.assertEqual(result["distance_validity_rate"], 1)

    def test_errors_and_bins(self):
        """Functionality: Check independent error arithmetic and every half-open bin boundary.
        Inputs:10/20m targets,12/16m estimates and exact interval edges. Outputs:MAE3/RMSEsqrt10.
        Logic: compare independently calculated expectations. Constraints: group by GT distance.
        """
        rows = [
            {"target_m": gt, "prediction_m": pred, "class_name": "car", "matched": True}
            for gt, pred in ((10, 12), (20, 16))
        ]
        result = summarize(rows)["overall"]
        self.assertEqual(result["mae_m"], 3)
        self.assertAlmostEqual(result["rmse_m"], math.sqrt(10))
        self.assertAlmostEqual(result["abs_rel"], 0.2)
        rows = [
            {"target_m": gt, "prediction_m": gt, "class_name": "car", "matched": True}
            for gt in (0.5, 1, 3, 5, 10, 20)
        ]
        ranges = {key: value for key, value in summarize(rows).items() if key.startswith("range:")}
        self.assertEqual(len(ranges), 6)
        self.assertTrue(all(value["ground_truth_objects"] == 1 for value in ranges.values()))

    def test_paired_intersection(self):
        """Functionality: Compare methods on the same available annotation identities.
        Inputs: one missing prediction in B. Outputs: one paired object; failure invalidates pair.
        Logic: intersect valid IDs and retain full-GT coverage elsewhere.
        Constraints: a failed requested E must never silently disappear from an all-method report.
        """
        rows = [
            {
                "key": key,
                "target_m": 10,
                "prediction_m": value,
                "class_name": "car",
                "matched": True,
            }
            for key, value in (("one", 12), ("two", 14))
        ]
        second = [dict(row) for row in rows]
        second[1]["prediction_m"] = None
        result = paired_scores({"a": rows, "b": second}, ["a", "b"])
        self.assertEqual(result["common_valid_objects"], 1)
        self.assertEqual(result["scores"]["a"]["overall"]["mae_m"], 2)
        self.assertEqual(paired_scores({"a": rows}, ["a", "e"])["status"], "unavailable")
        with self.assertRaises(ValueError):
            paired_scores({"a": rows, "b": []}, ["a", "b"])

    def test_gt_identity(self):
        """Functionality: Preserve GT slots. Inputs: correct and moved GT boxes.
        Outputs: matched distance or error. Logic: direct slot alignment with identity validation.
        Constraints: arbitrary predictions cannot enter GT scoring as if they were true boxes.
        """
        obj = {**frame()["objects"][0], "distance_m": 9}
        rows, _ = score_frame(frame(), [obj], bbox_mode="gt", match_iou=0.5, depth_frame=False)
        self.assertEqual(summarize(rows)["overall"]["mae_m"], 1)
        with self.assertRaises(ValueError):
            score_frame(
                frame(),
                [{**obj, "bbox": [1, 1, 2, 2]}],
                bbox_mode="gt",
                match_iou=0.5,
                depth_frame=False,
            )

    def test_invalid_and_empty(self):
        """Functionality: Keep unavailable estimates and empty bins null.
        Inputs:NaN/Inf/bool/zero/negative/null distances and no predictions.
        Outputs:zero coverage,null error. Logic: all-GT denominator. Constraints: zero is invalid.
        """
        for distance in (None, float("nan"), float("inf"), True, 0, -1, "10"):
            obj = {**frame()["objects"][0], "distance_m": distance}
            rows, _ = score_frame(frame(), [obj], bbox_mode="gt", match_iou=0.5, depth_frame=False)
            result = summarize(rows)
            self.assertEqual(result["overall"]["coverage"], 0)
            self.assertIsNone(result["overall"]["mae_m"])
        self.assertIsNone(summarize([])["overall"]["coverage"])
        self.assertIsNone(summarize([])["range:1-3m"]["mae_m"])

    def test_sequence_boundaries(self):
        """Functionality: Sort source times and reset scene/camera caches/schedules.
        Inputs: shuffled scenes/cameras with irregular microsecond gaps. Outputs: exact deltas.
        Logic: exercise original DistanceStrategy with actual immutable cache entries.
        Constraints: synthetic track IDs test cache lifecycle, not BoT-SORT association accuracy.
        """
        samples = [
            frame("b", 2800000),
            frame("a", 1000000),
            frame("c", 1500000),
            frame("new", 100000, scene="scene-b"),
            frame("camera", 1000000, camera="CAM_FRONT_LEFT"),
        ]
        samples = ordered_records(samples)
        adapter, clock = Mock(), SequenceClock()
        values = [clock.advance(row, adapter) for row in samples]
        self.assertEqual(values[:3], [(1.0, None), (1.5, 0.5), (2.8, 1.3)])
        self.assertEqual(adapter.reset.call_count, 3)
        with self.assertRaises(ValueError):
            ordered_records([frame(), frame()])
        image = Image.new("RGB", (100, 100))
        depth = SimpleNamespace(predict=Mock(return_value=np.full((100, 100), 3.8)))
        for interval in (1, 2, 3, 5):
            strategy = DistanceStrategy(
                DistanceConfig(distance_mode="temporal_depth", depth_interval=interval)
            )
            depth.predict.reset_mock()
            for index in range(6):
                boxes = [{"label": "car", "score": 1, "xyxy": [0, 0, 10, 10], "track_id": 5}]
                result, actual = strategy.apply(image, boxes, depth, timestamp=index * 0.5)
                self.assertEqual(actual, index % interval == 0)
                self.assertAlmostEqual(result[0]["distance_m"], 3.8)
            self.assertEqual(depth.predict.call_count, math.ceil(6 / interval))
            strategy.apply(image, boxes, depth, timestamp=0.1, epoch=1)
            self.assertEqual(strategy.frame_index, 1)
            self.assertEqual(strategy.cache.entries[5].last_depth_frame, 0)

    @skipUnless(HAS_TORCH, "Optional Torch timing dependency")
    def test_cuda_timing_boundary(self):
        """Functionality: Check explicit CUDA synchronization and aggregate FPS arithmetic.
        Inputs: mocked CUDA barrier and10/30ms timings. Outputs:one sync,50FPS,20ms mean.
        Logic: CPU never triggers CUDA sync. Constraints: mocked timing is not a GPU benchmark.
        """
        with patch("torch.cuda.synchronize") as barrier:
            synchronize("cuda:0")
            synchronize("cpu")
            barrier.assert_called_once_with("cuda:0")
        rows = [{"end_to_end_ms": 10}, {"end_to_end_ms": 30}]
        result = latency_summary(rows)
        self.assertEqual((result["fps"], result["end_to_end_ms"]["p50"]), (50, 20))
        self.assertEqual(result["end_to_end_ms"]["p95"], 29)
        self.assertIsNone(statistics([])["mean"])
        with self.assertRaises(ValueError):
            statistics([float("nan")])

    @skipUnless(HAS_PSUTIL, "Optional process memory dependency")
    def test_memory_sampler(self):
        """Functionality: Observe actual local process memory without a GPU.
        Inputs: fresh sampler. Outputs:positive RSS and a joined worker thread.
        Logic: start/sample/stop. Constraints: observed peak is not exact sub10ms memory usage.
        """
        sampler = MemorySampler()
        sampler.start()
        result = sampler.stop()
        self.assertGreater(result["peak_process_rss_bytes"], 0)
        self.assertFalse(sampler.thread.is_alive())

    def test_e_checkpoint_required(self):
        """Functionality: Reject untrained missing E before model construction.
        Inputs: no checkpoint. Outputs: actionable ValueError.
        Logic: fail before load_checkpoint. Constraints: no random initialized distance head.
        """
        with self.assertRaisesRegex(ValueError, "trained --e-checkpoint"):
            EAdapter({})

    @skipUnless(HAS_TORCH, "Optional Torch timing dependency")
    def test_geometry_uses_calibration(self):
        """Functionality: Use original-frame camera fy instead of configured foreign-camera fy.
        Inputs:640x320 image,fy100,bbox height100,car1.5m. Outputs:1.5m,depth not constructed.
        Logic: exercise actual NativeAdapter.predict with fake detection executor only.
        Constraints: synthetic detections/timing do not count as real B GPU evaluation.
        """
        adapter = NativeAdapter.__new__(NativeAdapter)
        adapter.device, adapter.method = "cpu", "b"
        adapter.config = DistanceConfig(distance_mode="geometry", focal_length_px=999)
        adapter.strategy = DistanceStrategy(adapter.config)
        adapter.tensor = np.empty((1, 3, 640, 640), dtype=np.float32)
        adapter.depth, adapter.tracker = None, None
        adapter.names = ["person", "bicycle", "car"]
        prediction = np.zeros((1, 300, 6), dtype=np.float32)
        adapter.runner = Mock(return_value=(prediction, 1))
        adapter.runner.forward_event_ms = 1
        objects, _, depth = adapter.predict(
            Image.new("RGB", (640, 320)), frame(), bbox_mode="gt", timestamp=1
        )
        self.assertEqual(objects[0]["distance_m"], 1.5)
        self.assertFalse(depth)

    def test_original_components(self):
        """Functionality: Guard independent adapters and existing production defaults.
        Inputs: module AST and production profile. Outputs: expected reuse/no training calls.
        Logic: inspect called original APIs; compare source fingerprints before/after pure checks.
        Constraints: runtime manifests separately fingerprint actual original source and weights.
        """
        before = source_snapshot()
        tree = ast.parse((ROOT / "model_tests/unified_evaluation/adapters.py").read_text())
        names = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertTrue(
            {
                "DistanceStrategy",
                "GraphRunner",
                "load_model",
                "load_checkpoint",
                "run_frame",
                "ObjectTracker",
            }.issubset(names)
        )
        self.assertNotIn("train", names)
        self.assertEqual(DistanceConfig().distance_mode, "depth")
        self.assertEqual(before, source_snapshot())


@skipUnless(HAS_DEVKIT, "Install isolated nuScenes evaluation dependencies")
class UnifiedDatasetTests(SimpleTestCase):
    """Functionality: Use actual official Box/Quaternion/Devkit on tiny complete fixtures.
    Logic: independent3D expected coordinates and real dataset conversion/worker persistence.
    Constraints: synthetic annotations and fake inference do not establish nuScenes accuracy.
    """

    def test_camera_transform(self):
        """Functionality: Verify inverse pose order including nonidentity camera rotation.
        Inputs: global(12,0,0),ego(10,0,0),sensor(1,0,0),camera yaw90degrees.
        Outputs:camera(0,-1,0),unchanged input. Logic: independent inverse transform expectation.
        Constraints: global z must not be reused as camera depth.
        """
        from nuscenes.utils.data_classes import Box
        from pyquaternion import Quaternion

        box = Box([12, 0, 0], [2, 2, 2], Quaternion())
        result = camera_box(
            box,
            {"translation": [10, 0, 0], "rotation": [1, 0, 0, 0]},
            {
                "translation": [1, 0, 0],
                "rotation": Quaternion(axis=[0, 0, 1], angle=math.pi / 2).elements,
            },
        )
        np.testing.assert_allclose(result.center, [0, -1, 0], atol=1e-12)
        np.testing.assert_array_equal(box.center, [12, 0, 0])
        result = camera_box(
            Box([10, 0, 0], [1, 1, 1], Quaternion()),
            {"translation": [0, 0, 0], "rotation": [1, 0, 0, 0]},
            {
                "translation": [0, 0, 0],
                "rotation": Quaternion(axis=[0, 1, 0], angle=math.pi / 2).elements,
            },
        )
        self.assertAlmostEqual(result.center[2], 10)

    def test_projection(self):
        """Functionality: Check exact projected cuboid envelope,near clipping and invalid filters.
        Inputs:2m cube at10m,K focal100,center50. Outputs:50+-100/9px extent.
        Logic: front corners z9 independently determine silhouette bounds.
        Constraints: intersect silhouette with image, including boxes that surround the image.
        """
        from nuscenes.utils.data_classes import Box
        from pyquaternion import Quaternion

        intrinsic = [[100, 0, 50], [0, 100, 50], [0, 0, 1]]
        box = Box([0, 0, 10], [2, 2, 2], Quaternion())
        bbox, reason = project_box(box, intrinsic, (100, 100))
        self.assertIsNone(reason)
        np.testing.assert_allclose(bbox, [50 - 100 / 9] * 2 + [50 + 100 / 9] * 2)
        for center, expected in (
            ([0, 0, -10], "center_behind_near_plane"),
            ([100, 0, 10], "outside_image"),
        ):
            self.assertEqual(
                project_box(Box(center, [2, 2, 2], Quaternion()), intrinsic, (100, 100))[1],
                expected,
            )
        clipped, _ = project_box(Box([0, 0, 0.5], [2, 2, 2], Quaternion()), intrinsic, (100, 100))
        self.assertEqual(clipped, [0, 0, 100, 100])
        box.wlh[0] = -1
        self.assertEqual(project_box(box, intrinsic, (100, 100))[1], "invalid_3d_box")

    def test_prepare_and_integrity(self):
        """Functionality: Run official SDK conversion then reject altered frames/images.
        Inputs:complete two-frame local fixture. Outputs:two10m targets and valid fingerprints.
        Logic: real tables,image headers,poses,projection and immutable output.
        Constraints: no network/media download or modified source files.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw, output = make_nuscenes(root), root / "processed"
            result = prepare(raw, output)
            self.assertEqual((result["frames"], result["objects"]), (2, 2))
            summary, frames = load_records(output)
            self.assertEqual(summary["raw_audit"]["tables"]["sample"], 2)
            self.assertEqual(frames[0]["objects"][0]["distance_gt_m"], 10)
            with self.assertRaises(ValueError):
                prepare(raw, output)
            (raw / frames[0]["image_path"]).write_bytes(b"changed")
            with self.assertRaises(ValueError):
                load_records(output)

    def test_missing_files(self):
        """Functionality: Fail incomplete data explicitly. Inputs:missing camera/map/table files.
        Outputs:actionable exception. Logic: full referenced-media preflight.
        Constraints: no hidden exclusion, replacement or dataset download.
        """
        with tempfile.TemporaryDirectory() as directory:
            raw = make_nuscenes(Path(directory))
            (raw / "samples/CAM_FRONT/0.jpg").unlink()
            with self.assertRaisesRegex(FileNotFoundError, "count=1"):
                audit_raw(raw, "v1.0-mini")

    def test_partial_method_failure(self):
        """Functionality: Persist completed methods and explicit failed rows under worker failure.
        Inputs:fake serial workers where E fails. Outputs:partial_failure,no four-method pairing.
        Logic: exercise real orchestration/report generation with converted tiny dataset.
        Constraints: injected predictions are mock tests, never GPU/accuracy evidence.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepare(make_nuscenes(root), root / "processed")
            _, frames = load_records(root / "processed")

            def launch(options):
                """Functionality: Inject one intentional E failure into a serial worker sequence.
                Inputs:resolved pass options. Outputs:exit status and fixture artifacts.
                Logic: fake valid A/B/C observations. Constraints: no real model inference.
                """
                folder = Path(options["pass_output"])
                if options["method"] == "e":
                    write_json(folder / "result.json", {"status": "failed", "error": "expected"})
                    return 1
                rows = []
                for sample in frames:
                    predictions = [{**obj, "distance_m": 10} for obj in sample["objects"]]
                    observed, _ = score_frame(
                        sample, predictions, bbox_mode="gt", match_iou=0.5, depth_frame=True
                    )
                    rows.extend(observed)
                (folder / "observations.jsonl").write_text(
                    "\n".join(json.dumps(row) for row in rows)
                )
                steady = latency_summary([{"end_to_end_ms": 10, "distance_ms": 2}])
                result = {
                    "status": "complete",
                    "metrics": summarize(rows),
                    "frames": 2,
                    "identity": {
                        key: value
                        for key, value in (
                            ("detector_sha256", "same"),
                            ("classes", ["car", "person"]),
                            ("confidence", 0.25),
                            ("image_size", 640),
                            ("preprocessing", "same"),
                        )
                    },
                    "performance": {
                        "steady": steady,
                        "peak_gpu_allocated_bytes": None,
                        "peak_gpu_reserved_bytes": None,
                    },
                    "memory": {},
                    "temporal": {"depth_calls": 2, "actual_depth_ratio": 1},
                }
                write_json(folder / "result.json", result)
                return 0

            options = {
                "processed": str(root / "processed"),
                "output": str(root / "run"),
                "method": "all",
                "bbox_mode": "gt",
                "limit": 2,
                "full": False,
            }
            result = run(options, launch=launch)
            self.assertEqual(result["status"], "partial_failure")
            self.assertEqual(result["paired"]["gt"]["status"], "unavailable")
            self.assertEqual(result["results"]["gt"]["a"]["status"], "complete")
            table = (root / "run/comparison.csv").read_text()
            self.assertIn("E,failed,null", table)
            for name in (
                "metrics.json",
                "run_manifest.json",
                "report.md",
                "distance_bins.csv",
                "latency.csv",
                "resources.csv",
                "paired_comparison.csv",
            ):
                self.assertTrue((root / "run" / name).is_file())
            with self.assertRaises(ValueError):
                run(options, launch=launch)

    @skipUnless(HAS_TORCH, "Optional Torch timing dependency")
    def test_evaluate_temporal_pass(self):
        """Functionality: Distinguish cold/warm calls from scored temporal frame metrics.
        Inputs:fake adapter using real strategy and two labeled keyframes. Outputs:one depth call.
        Logic: actual runner resets cold/warm state; source delta remains0.5s.
        Constraints: mock distance map/association, not real GPU inference.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepare(make_nuscenes(root), root / "processed")
            summary, frames = load_records(root / "processed")
            config = DistanceConfig(distance_mode="temporal_depth", depth_interval=3)
            adapter = SimpleNamespace(device="cpu", identity={})

            def reset():
                """Functionality: Reset fake adapter's original strategy. Inputs:None.
                Outputs:fresh cache/schedule. Logic: create original strategy.
                Constraints: simulated tracking lifecycle only.
                """
                adapter.strategy = DistanceStrategy(config)

            def predict(image, sample, *, bbox_mode, timestamp):
                """Functionality: Inject deterministic depth into actual cache logic.
                Inputs:RGB/frame/clock. Outputs:canonical objects,timing,actual call flag.
                Logic: reuse original strategy. Constraints: not real model inference.
                """
                boxes = [
                    {
                        "label": "car",
                        "score": 1,
                        "xyxy": sample["objects"][0]["bbox"],
                        "track_id": 1,
                    }
                ]
                depth = SimpleNamespace(predict=lambda image: np.full((100, 160), 10.0))
                objects, actual = adapter.strategy.apply(image, boxes, depth, timestamp=timestamp)
                return objects, {"end_to_end_ms": 1, "distance_ms": 0.5}, actual

            adapter.reset, adapter.predict = reset, predict
            output = root / "pass"
            output.mkdir()
            result = evaluate_pass(
                adapter,
                summary,
                frames,
                output,
                {
                    "method": "c",
                    "bbox_mode": "gt",
                    "warmup": 3,
                    "device": "cpu",
                    "match_iou": 0.5,
                    "depth_interval": 3,
                },
            )
            self.assertEqual(result["temporal"]["depth_calls"], 1)
            self.assertEqual(result["temporal"]["actual_depth_ratio"], 0.5)
            self.assertEqual(result["temporal"]["non_depth_frame_metrics"]["overall"]["mae_m"], 0)
            predictions = [
                json.loads(line) for line in (output / "predictions.jsonl").read_text().splitlines()
            ]
            self.assertEqual(predictions[1]["source_delta_seconds"], 0.5)
            self.assertEqual(predictions[1]["objects"][0]["distance_source"], "cached")
