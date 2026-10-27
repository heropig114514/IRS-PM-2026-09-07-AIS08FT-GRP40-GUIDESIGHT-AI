"""Responsibilities: Wrap existing A/B/C/E components without changing their algorithms.
Implementation: Native graph plus original DistanceStrategy, or strict E checkpoint/run_frame.
Related Modules: core.detector; zerogpu.runtime; realtime.depth/distance/tracking; object_distance.
Declaration Index: legacy_boxes; NativeAdapter; NativeAdapter.__init__; NativeAdapter.reset;
NativeAdapter.predict; EAdapter; EAdapter.__init__; EAdapter.reset; EAdapter.predict;
create_adapter.
Variable Index: MODES — CLI method to existing distance strategy mapping.
NativeAdapter.preprocess_fast — opt-in exact detector transform, independent of fast_a.
"""

import json
import time
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np

from object_distance.config import digest, seed_runtime
from realtime.distance_config import read_distance_config

from .performance import synchronize

MODES = {"a": "depth", "b": "geometry", "c": "temporal_depth", "e": "object_distance"}


def legacy_boxes(objects, names):
    """Functionality: Feed canonical GT slots to existing fusion and tracking interfaces.
    Inputs: GT objects and verified detector names. Outputs: legacy dictionaries in original order.
    Logic: explicit class index and confidence1. Constraints: GT annotation IDs never become IDs
    for temporal tracking; GT temporal mode still uses actual BoT-SORT association.
    """
    return [
        {
            "label": obj["class_name"],
            "score": 1.0,
            "class_id": names.index(obj["class_name"]),
            "xyxy": list(obj["bbox"]),
        }
        for obj in objects
    ]


class NativeAdapter:
    """Functionality: Evaluate original graph detection and A/B/C distance components on RGB.
    Logic: retain detector640/threshold.25/FP32; depth defaults768/FP32 with explicit experiments;
    only omit rendering,
    navigation, motion and risk. Constraints: raw RGB experimental ingress differs from JPEG80
    production ingress; graph/eager and inverse-coordinate differences are recorded explicitly.
    """

    def __init__(self, options):
        """Functionality: Load explicit verified native weights and selected strategy once.
        Inputs: worker option mapping. Outputs: resident adapter and comparison identity.
        Logic: existing load_model/GraphRunner/DepthEstimator/ObjectTracker are reused directly.
        Constraints: CUDA:0 only, fixed640/.25; B never constructs or reads depth model weights.
        """
        import torch

        from core.detector import CONFIDENCE_THRESHOLD, INPUT_EDGE
        from realtime.depth import DEPTH_EDGE, DepthEstimator
        from realtime.distance import DistanceStrategy
        from realtime.tracking import DEFAULT_TRACKING_CONFIG, ObjectTracker
        from zerogpu.runtime import load_model

        if options["device"] != "cuda:0" or not torch.cuda.is_available():
            raise ValueError("Native A/B/C require explicit cuda:0; no CPU fallback")
        torch.set_num_threads(1)
        torch.set_num_interop_threads(1)
        self.device, self.method = options["device"], options["method"]
        self.fast_a = bool(options.get("a_fast", False))
        self.preprocess_fast = bool(options.get("a_preprocess_fast", False))
        if self.preprocess_fast and (
                self.method != "a" or options.get("depth_image_size", 768) != 768
                or options.get("depth_precision", "fp32") != "fp32"):
            raise ValueError("Fast A preprocessing requires method a, depth768/FP32")
        if self.fast_a and (self.method != "a" or options.get("depth_image_size", 768) != 768
                            or options.get("depth_precision", "fp32") != "fp32"):
            raise ValueError("Fast A requires method a, depth768/FP32")
        self.manifest = json.loads(Path(options["detection_manifest"]).read_text(encoding="utf-8"))
        weights = Path(options["detection_weights"])
        if not weights.is_file() or weights.suffix != ".pt":
            raise ValueError("Provide existing trusted detection .pt weights")
        self.model = load_model(weights, self.manifest, self.device, {"dtype": "float32"})
        self.names, self.runner = self.manifest["names"], None
        self.tensor = np.empty((1, 3, INPUT_EDGE, INPUT_EDGE), dtype=np.float32)
        self.config = read_distance_config(
            options.get("distance_config"),
            distance_mode=MODES[self.method],
            depth_interval=options["depth_interval"],
            environ={},
        )
        self.strategy = DistanceStrategy(self.config)
        self.strategy.fast_roi = self.fast_a
        self.depth = None
        depth_edge = options.get("depth_image_size", DEPTH_EDGE)
        depth_precision = options.get("depth_precision", "fp32")
        if depth_precision not in {"fp32", "fp16"}:
            raise ValueError("Depth precision must be fp32 or fp16")
        if self.method != "b":
            self.depth = DepthEstimator(
                options["depth_weights"], self.manifest["build"]["ultralytics"],
                image_size=depth_edge, half=depth_precision == "fp16",
            )
            self.depth.activate()
            self.depth.fast_input = self.fast_a
        self.tracker = (
            ObjectTracker(DEFAULT_TRACKING_CONFIG, self.manifest["build"]["ultralytics"])
            if self.method == "c"
            else None
        )
        self.identity = {
            "method": self.method,
            "a_fast": self.fast_a,
            "a_preprocess_fast": self.preprocess_fast,
            "detector_sha256": digest(weights),
            "detector_manifest_sha256": digest(options["detection_manifest"]),
            "architecture": {
                "model": type(self.model).__name__,
                "head": type(self.model.model[-1]).__name__,
                "fused": True,
                "head_max_det": self.model.model[-1].max_det,
            },
            "classes": self.names,
            "confidence": CONFIDENCE_THRESHOLD,
            "class_confidence_thresholds": {},
            "traffic_light_roi_pass": False,
            "image_size": INPUT_EDGE,
            "detector_precision": "FP32",
            "executor": "original_CUDA_Graph_fused",
            "preprocessing": "original_RGB_bilinear_letterbox114_FP32_no_extra_JPEG",
            "box_inverse": "production_scalar_resize_scale",
            "distance_definition": "ROI_positive_finite_depth_median"
            if self.method != "b"
            else "pinhole_assumed_category_height_camera_z",
            "distance_config": asdict(self.config),
            "depth_image_size": depth_edge if self.depth is not None else None,
            "depth_precision": depth_precision if self.depth is not None else None,
            "depth_weights_sha256": digest(options["depth_weights"])
            if self.depth is not None
            else None,
            "geometry_calibration": "per_frame_fy_at_original_image_width_no_test_GT_fitting",
        }

    def reset(self):
        """Functionality: Reset scene/camera temporal state without reloading any model.
        Inputs: None. Outputs: fresh tracker epoch/cache/schedule.
        Logic: reset actual tracker and create the original independent strategy.
        Constraints: no warmup distances or IDs leak into scored sequences.
        """
        from realtime.distance import DistanceStrategy

        if self.tracker is not None:
            self.tracker.reset()
        self.strategy = DistanceStrategy(self.config)
        self.strategy.fast_roi = self.fast_a

    def predict(self, image, frame, *, bbox_mode, timestamp):
        """Functionality: Infer one canonical RGB frame with synchronized stage boundaries.
        Inputs: image/metadata,bbox policy,real source seconds. Outputs: objects,timings,depth flag.
        Logic: original detection every frame, GT replacement only in GT pass, original tracking
        and strategy. B sets fy at original width so its existing resize formula cancels exactly.
        Constraints: GT detection compute is retained for comparable pipeline scope; no render,
        disk decode, metrics or output I/O inside end-to-end timing; tracker failures are fatal.
        """
        from core.detector import decode_boxes, letterbox
        from realtime.distance import GeometryDistanceEstimator
        from zerogpu.runtime import GraphRunner

        synchronize(self.device)
        started = time.perf_counter()
        if getattr(self, "preprocess_fast", False):
            from core.detection_preprocess import letterbox_fast

            tensor, scale, left, top = letterbox_fast(image, output=self.tensor)
        else:
            tensor, scale, left, top = letterbox(image, output=self.tensor)
        prepared = time.perf_counter()
        if self.runner is None:
            self.runner = GraphRunner(self.model, tensor, {"dtype": "float32"})
        runner_started = time.perf_counter()
        prediction, _ = self.runner(tensor)
        runner_ms = (time.perf_counter() - runner_started) * 1000
        decode_started = time.perf_counter()
        # Freeze the historical experiment protocol independently of production class overrides.
        boxes = decode_boxes(prediction, image.size, (scale, left, top), self.names, class_conf={})
        if bbox_mode == "gt":
            boxes = legacy_boxes(frame["objects"], self.names)
        decode_ms = (time.perf_counter() - decode_started) * 1000
        synchronize(self.device)
        detected = time.perf_counter()
        if self.tracker is not None:
            boxes = self.tracker.associate(image, boxes)
            if not self.tracker.available:
                raise RuntimeError("Original BoT-SORT association failed; no fallback allowed")
        tracked = time.perf_counter()
        if self.method == "b":
            calibrated = replace(
                self.config,
                focal_length_px=frame["camera_intrinsic"][1][1],
                reference_width_px=image.width,
            )
            self.strategy.geometry = GeometryDistanceEstimator(calibrated)
        objects, depth_frame = self.strategy.apply(
            image,
            boxes,
            self.depth,
            timestamp=timestamp,
            epoch=self.tracker.epoch if self.tracker is not None else 0,
        )
        for obj in objects:
            obj.setdefault("distance_source", "depth")
        synchronize(self.device)
        finished = time.perf_counter()
        return (
            objects,
            {
                "prepare_ms": (prepared - started) * 1000,
                "detection_ms": (detected - prepared) * 1000,
                "detection_runner_ms": runner_ms,
                "detection_decode_ms": decode_ms,
                "tracking_ms": (tracked - detected) * 1000,
                "distance_ms": (finished - tracked) * 1000,
                "model_pipeline_ms": ((detected - prepared) + (finished - tracked)) * 1000,
                "detection_forward_event_ms": self.runner.forward_event_ms,
                "end_to_end_ms": (finished - started) * 1000,
            },
            depth_frame,
        )


class EAdapter:
    """Functionality: Reuse the existing strict trained E checkpoint and inference entry point.
    Logic: load_checkpoint/run_frame with optional GT boxes; no random heads or depth fallback.
    Constraints: checkpoint preprocessing,classes,precision and trained detector state are retained.
    """

    def __init__(self, options):
        """Functionality: Load and identify a trained head with explicit detector binding.
        Inputs: worker checkpoint/weight/device options. Outputs: loaded E adapter identity.
        Logic: require a positive training epoch and checkpoint feature/config integrity.
        Constraints: external nuScenes reference is box-center z; E learned KITTI reference z,
        which is a recorded semantic mismatch, not silently calibrated using test labels.
        """
        checkpoint = options.get("e_checkpoint")
        if not checkpoint or not Path(checkpoint).is_file():
            raise ValueError(
                "E requires an existing trained --e-checkpoint; random heads forbidden"
            )
        import torch

        from object_distance.model import load_checkpoint

        torch.set_num_threads(1)
        torch.set_num_interop_threads(1)
        self.model, payload = load_checkpoint(
            checkpoint, detection_weights=options["detection_weights"], device=options["device"]
        )
        if (
            type(payload.get("epoch")) is not int
            or payload["epoch"] < 1
            or type(payload.get("extra", {}).get("steps")) is not int
            or payload["extra"]["steps"] < 1
        ):
            raise ValueError(
                "E checkpoint must record a completed training epoch and positive steps"
            )
        self.model.eval()
        seed_runtime(self.model.config)
        self.device = self.model.config.device
        config = self.model.config
        self.identity = {
            "method": "e",
            "detector_sha256": self.model.weight_sha256,
            "e_checkpoint_sha256": digest(checkpoint),
            "e_checkpoint_epoch": payload["epoch"],
            "architecture": self.model.signature,
            "classes": self.model.names,
            "confidence": config.confidence,
            "image_size": config.image_size,
            "detector_precision": "AMP"
            if config.amp and self.device.startswith("cuda")
            else "FP32",
            "executor": "original_E_eager_unfused",
            "preprocessing": "original_RGB_bilinear_letterbox114_FP32_no_extra_JPEG",
            "box_inverse": "E_effective_rounded_x_y_resize_scales",
            "distance_definition": config.distance_target,
            "distance_config": asdict(config),
            "detector_feature_tuning": not config.freeze_backbone
            or config.unfreeze_last_layers > 0,
            "parameters": self.model.parameter_counts(),
        }

    def reset(self):
        """Functionality: Satisfy stream lifecycle without introducing E temporal logic.
        Inputs: None. Outputs: None. Logic: E is stateless per frame.
        Constraints: no tracker/cache or interpolation added to the trained distance algorithm.
        """

    def predict(self, image, frame, *, bbox_mode, timestamp):
        """Functionality: Run unchanged E preprocessing/extraction/regression on selected ROIs.
        Inputs: RGB/GT metadata,bbox mode and unused source seconds. Outputs: objects,timing,False.
        Logic: call existing prepare_image/run_frame; preserve original GT slot boxes exactly.
        Constraints: end-to-end includes preparation and synchronized output, excludes disk I/O.
        """
        import torch

        from object_distance.data import prepare_image
        from object_distance.predict import run_frame

        synchronize(self.device)
        started = time.perf_counter()
        tensor, transform = prepare_image(image, self.model.config.image_size)
        prepared = time.perf_counter()
        arguments = {}
        if bbox_mode == "gt":
            arguments = {
                "gt_boxes": torch.tensor(
                    [o["bbox"] for o in frame["objects"]], dtype=torch.float32
                ).reshape(-1, 4),
                "gt_classes": torch.tensor(
                    [
                        self.model.config.supported_classes.index(o["class_name"])
                        for o in frame["objects"]
                    ],
                    dtype=torch.long,
                ),
            }
        objects, timings = run_frame(self.model, tensor, transform, started=started, **arguments)
        if bbox_mode == "gt":
            for obj, gt in zip(objects, frame["objects"], strict=True):
                obj["bbox"] = list(gt["bbox"])
        timings.pop("fps")
        timings.update(
            prepare_ms=(prepared - started) * 1000,
            tracking_ms=0.0,
            model_pipeline_ms=timings["detection_ms"] + timings["distance_ms"],
        )
        return objects, timings, False


def create_adapter(options):
    """Functionality: Select only the requested existing method implementation.
    Inputs: worker options. Outputs: native or trained E adapter. Logic: explicit method dispatch.
    Constraints: unknown methods fail instead of falling back or substituting checkpoints.
    """
    if options["method"] not in MODES:
        raise ValueError("Unknown evaluation method")
    return EAdapter(options) if options["method"] == "e" else NativeAdapter(options)
