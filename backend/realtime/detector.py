"""Responsibilities: Keep the pinned native-CUDA model and selected-precision Graph resident.
Implementation: Original graph/fusion; configured A/B/C distance; motion then scalar risk.
Related Modules: core.detector; realtime.distance/distance_config/performance/tracking/risk;
zerogpu.runtime; zerogpu.live_worker.
Declaration Index: ResidentDetector; ResidentDetector.__init__; ResidentDetector.predict.
Variable Index: logger — count/timing diagnostics; model/manifest/tensor/runner/load_ms;
execution_config/precision/backend — explicit execution identity, original FP32 control by default.
roi — optional extra centre-top traffic-light pass (sequential batch-1 replays of the same Graph).
light_rule — walk-model pedestrian-light colour rule from the manifest (None for COCO yolo26n).
class_conf — decode thresholds: CLASS_CONF, plus the colour classes' threshold in walk mode only.
depth_estimator — optional independent metric model; depth_ms includes inference/fusion.
object_tracker — instance-owned BoT-SORT/history; tracking_ms covers association/history only.
risk_config — immutable TTC/rule profile; risk_ms — separate stateless scalar scoring timer.
distance_strategy/performance/performance_epoch — owned strategy and per-epoch frame statistics.
Metadata forward_event_ms is detection CUDA replay timing; inference_ms is transfer-inclusive.
"""

import hashlib
import json
import logging
import time

from core.detection_model import light_rule, validate_manifest
from core.detector import (
    CLASS_CONF,
    CONFIDENCE_THRESHOLD,
    INPUT_EDGE,
    PEDESTRIAN_LIGHT,
    annotate,
    decode_boxes,
    decode_frame,
    letterbox,
    mark_front_light,
    merge_boxes,
    pedestrian_lights,
    roi_crop,
)
from realtime.distance import DistanceStrategy
from realtime.distance_config import DEFAULT_DISTANCE_CONFIG
from realtime.performance import PerformanceStats
from realtime.risk import DEFAULT_RISK_CONFIG, RISK_MODEL, enrich_object_risk
from realtime.tracking import DEFAULT_TRACKING_CONFIG, ObjectTracker
from zerogpu.runtime import GraphRunner, load_model

logger = logging.getLogger(__name__)


class ResidentDetector:
    """Functionality: Process ordered frames through one persistent native-CUDA model/graph.
    Logic: lazy graph setup from first real input; FP32 API buffers and explicit compute precision.
    Constraints: caller serializes GPU work; no ZeroGPU decorator, alternate model or CPU fallback.
    """

    def __init__(self, weights_path, manifest_path, config=None, *, depth_estimator=None,
                 tracking_config=DEFAULT_TRACKING_CONFIG, risk_config=DEFAULT_RISK_CONFIG,
                 distance_config=DEFAULT_DISTANCE_CONFIG, roi=False):
        """Functionality: Load verified local weights once on the GPU worker.
        Inputs: pinned weights/manifest, detection config, optional loaded depth estimator,
        immutable tracking/risk/distance configs (tracking=None disables tracking/risk for stills).
        Outputs: persistent model/tensor state. Logic: same source hash/build/head/fusion via
        load_model; declared precision/TF32-off, one CPU thread; default is original FP32 control.
        Constraints: native CUDA must exist; no downloads, random initialization or global service.
        roi=True adds one native-resolution centre-top traffic-light pass per frame.
        The manifest may be the COCO yolo26n or the walk model (core.detection_model); a declared
        pedestrian-light rule is applied to every frame.
        GraphRunner remains None until first JPEG; optional depth activates once on the same GPU.
        """
        import numpy as np
        import torch

        started = time.perf_counter()
        if not torch.cuda.is_available():
            raise RuntimeError("Native CUDA is required for the approved local validation")
        torch.set_num_threads(1)
        torch.set_num_interop_threads(1)
        self.manifest = validate_manifest(json.loads(manifest_path.read_text(encoding="utf-8")))
        self.light_rule = light_rule(self.manifest)
        self.class_conf = dict(CLASS_CONF)
        if self.light_rule is not None:
            self.class_conf.update(dict.fromkeys(self.light_rule["classes"],
                                                 self.light_rule["class_confidence"]))
        self.execution_config = {} if config is None else dict(config)
        self.precision = {"float32": "FP32", "float16": "FP16", "bfloat16": "BF16"}[
            self.execution_config.get("dtype", "float32")
        ]
        self.backend = "PyTorch resident CUDA"
        self.model = load_model(weights_path, self.manifest, "cuda", self.execution_config)
        self.tensor = np.empty((1, 3, INPUT_EDGE, INPUT_EDGE), dtype=np.float32)
        self.runner = None
        self.roi = roi
        self.roi_tensor = np.empty((1, 3, INPUT_EDGE, INPUT_EDGE), dtype=np.float32)
        if distance_config.distance_mode == "temporal_depth" and (
                depth_estimator is None or tracking_config is None):
            raise ValueError("temporal_depth requires depth and tracking")
        self.distance_strategy = DistanceStrategy(distance_config)
        self.performance, self.performance_epoch = PerformanceStats(), 0
        self.depth_estimator = (None if distance_config.distance_mode == "geometry" else
                                depth_estimator)
        if self.depth_estimator is not None:
            self.depth_estimator.activate()
        self.object_tracker = (ObjectTracker(tracking_config, self.manifest["build"]["ultralytics"])
                               if tracking_config is not None else None)
        self.risk_config = risk_config
        self.load_ms = (time.perf_counter() - started) * 1000
        logger.info("Resident model initialized load_ms=%.1f precision=%s edge=%d config=%s",
                    self.load_ms, self.precision, INPUT_EDGE, self.execution_config)

    def predict(self, jpeg, *, timestamp=None):
        """Functionality: Decode, infer and render one complete JPEG through shared processing.
        Inputs: JPEG, persistent state; optional increasing same-stream source seconds for tests
        or timestamped video. Default uses same-worker perf_counter at frame start, not wall time.
        Outputs: metadata and original-contract annotated JPEG; first call initializes the graph.
        Logic: unchanged detection preprocessing/graph/boxes; associate IDs with original boxes;
        selected distance strategy (default exact same-frame depth/fusion); history/motion;
        stateless TTC/risk; render. C cached distances are not new motion measurements.
        Never constructs/resets tracking here; current missing depth produces unknown risk.
        Depth failures still propagate; unavailable tracking is explicitly reported with old labels.
        Constraints: explicit compute identity is returned; no added NMS, threshold/codec change;
        production callers must supply a qualified plan before selecting lower precision.
        Failures propagate.
        Timings include server processing; graph_setup_ms is nonzero only on the first real frame.
        """
        started = time.perf_counter()
        image = decode_frame(jpeg)
        tensor, scale, left, top = letterbox(image, output=self.tensor)
        prepared_at = time.perf_counter()
        setup_ms = 0.0
        if self.runner is None:
            self.runner = GraphRunner(self.model, tensor, self.execution_config)
            setup_ms = self.runner.setup_ms
            logger.info("Resident graph initialized setup_ms=%.1f", setup_ms)
        prediction, inference_ms = self.runner(tensor)
        forward_event_ms = self.runner.forward_event_ms
        roi = getattr(self, "roi", False)
        roi_ms = 0.0
        crop = roi_crop(image) if roi else None
        roi = crop is not None  # tiny frames have no usable ROI: full-frame result only
        if roi:
            # The compiled Graph is captured for batch 1, so the ROI is a second replay.
            crop, x_offset, y_offset = crop
            roi_tensor, roi_scale, roi_left, roi_top = letterbox(crop, output=self.roi_tensor)
            roi_prediction, roi_ms = self.runner(roi_tensor)
            forward_event_ms += self.runner.forward_event_ms
        inferred_at = time.perf_counter()
        class_conf = getattr(self, "class_conf", CLASS_CONF)
        boxes = decode_boxes(prediction, image.size, (scale, left, top), self.manifest["names"],
                             class_conf=class_conf)
        if roi:
            roi_boxes = decode_boxes(roi_prediction, crop.size, (roi_scale, roi_left, roi_top),
                                     self.manifest["names"], class_conf=class_conf)
            for box in roi_boxes:
                x1, y1, x2, y2 = box["xyxy"]
                box["xyxy"] = [x1 + x_offset, y1 + y_offset, x2 + x_offset, y2 + y_offset]
            boxes = merge_boxes(boxes, roi_boxes)
        boxes = pedestrian_lights(boxes, getattr(self, "light_rule", None))
        boxes = mark_front_light(boxes, image.size)
        decoded_at = time.perf_counter()
        object_tracker = getattr(self, "object_tracker", None)
        if object_tracker is not None:
            boxes = object_tracker.associate(image, boxes)
        tracked_at = time.perf_counter()
        depth_estimator = getattr(self, "depth_estimator", None)
        # Old explicit test/embedding instances may supply only the original model attributes.
        if not hasattr(self, "distance_strategy"):
            self.distance_strategy = DistanceStrategy(DEFAULT_DISTANCE_CONFIG)
        if self.distance_strategy.geometry is not None:
            depth_estimator = None
        if not hasattr(self, "performance"):
            self.performance = PerformanceStats()
        epoch = object_tracker.epoch if object_tracker is not None else 0
        if getattr(self, "performance_epoch", None) != epoch:
            self.performance, self.performance_epoch = PerformanceStats(), epoch
        boxes, depth_frame = self.distance_strategy.apply(
            image, boxes, depth_estimator, timestamp=started if timestamp is None else timestamp,
            epoch=epoch)
        fused_at = time.perf_counter()
        if object_tracker is not None:
            boxes = object_tracker.enrich(boxes, started if timestamp is None else timestamp)
        motion_at = time.perf_counter()
        if object_tracker is not None:
            boxes = enrich_object_risk(boxes, image.size,
                                       config=getattr(self, "risk_config", DEFAULT_RISK_CONFIG))
        risk_at = time.perf_counter()
        result = annotate(image, boxes)
        finished = time.perf_counter()
        metadata = {"schema_version": 1, "model": self.manifest.get("model", "yolo26n"),
                    "backend": self.backend, "precision": self.precision,
                    "output_precision": "FP32", "classes": len(self.manifest["names"]),
                    "confidence_threshold": CONFIDENCE_THRESHOLD,
                    "class_confidence_thresholds": dict(class_conf),
                    "source_sha256": self.manifest["source_sha256"], "width": image.width,
                    "height": image.height, "count": len(boxes), "boxes": boxes,
                    "raw_sha256": hashlib.sha256(prediction.tobytes()).hexdigest(),
                    "jpeg_sha256": hashlib.sha256(result).hexdigest(),
                    "inference_ms": inference_ms + roi_ms, "graph_setup_ms": setup_ms,
                    "prepare_ms": (prepared_at - started) * 1000,
                    "gpu_stage_ms": (inferred_at - prepared_at) * 1000,
                    "render_ms": (finished - (risk_at if object_tracker is not None else
                                              fused_at if depth_estimator is not None or
                                              self.distance_strategy.geometry is not None else
                                              inferred_at)) * 1000,
                    "server_ms": (finished - started) * 1000,
                    "forward_event_ms": forward_event_ms,
                    "execution": {**self.runner.evidence,
                                  **({"jit_cache": self.cache_evidence}
                                     if getattr(self, "cache_evidence", None) else {})}}
        if roi:
            metadata.update(roi=True, roi_ms=roi_ms)
        if getattr(self, "light_rule", None) is not None:
            metadata.update(light_model=self.manifest["model"],
                            light_green_threshold=self.light_rule["green_threshold"])
            # The merged signal label inherits its colour classes' threshold for client checks.
            metadata["class_confidence_thresholds"][PEDESTRIAN_LIGHT] = (
                self.light_rule["class_confidence"])
        if depth_estimator is not None:
            metadata.update(depth_model=depth_estimator.model_name, depth_units="m",
                            depth_ms=((fused_at - tracked_at) + (decoded_at - inferred_at)) * 1000)
        if object_tracker is not None:
            metadata.update(object_tracker.metadata(),
                            tracking_ms=((tracked_at - decoded_at) + (motion_at - fused_at)) * 1000,
                            tracking_timestamp_s=started if timestamp is None else timestamp,
                            risk_model=RISK_MODEL, risk_ms=(risk_at - motion_at) * 1000)
        mode = self.distance_strategy.config.distance_mode
        detection_ms = (decoded_at - started) * 1000
        distance_ms = (fused_at - tracked_at) * 1000
        end_to_end_ms = (finished - started) * 1000
        metadata.update(
            distance_mode=mode, distance_units="m", depth_frame=depth_frame,
            depth_interval=self.distance_strategy.config.depth_interval if mode == "temporal_depth"
            else 1 if mode == "depth" else 0,
            detection_ms=detection_ms, distance_ms=distance_ms, end_to_end_ms=end_to_end_ms,
            fps=1000 / end_to_end_ms if end_to_end_ms > 0 else None,
            performance=self.performance.record(detection_ms=detection_ms, distance_ms=distance_ms,
                                                end_to_end_ms=end_to_end_ms,
                                                depth_frame=depth_frame))
        if mode == "geometry":
            metadata["distance_estimator"] = "pinhole-height-v1"
        return metadata, result
