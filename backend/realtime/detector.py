"""Responsibilities: Keep the pinned native-CUDA model and selected-precision Graph resident.
Implementation: Original graph; persistent BoT-SORT; optional GPU depth; motion then scalar risk.
Related Modules: core.detector; realtime.depth/tracking/risk; zerogpu.runtime; zerogpu.live_worker.
Declaration Index: ResidentDetector; ResidentDetector.__init__; ResidentDetector.predict.
Variable Index: logger — count/timing diagnostics; model/manifest/tensor/runner/load_ms;
execution_config/precision/backend — explicit execution identity, original FP32 control by default.
depth_estimator — optional independent metric model; depth_ms includes inference/fusion.
object_tracker — instance-owned BoT-SORT/history; tracking_ms covers association/history only.
risk_config — immutable TTC/rule profile; risk_ms — separate stateless scalar scoring timer.
Metadata forward_event_ms is detection CUDA replay timing; inference_ms is transfer-inclusive.
"""

import hashlib
import json
import logging
import time

from core.detector import (
    CONFIDENCE_THRESHOLD,
    INPUT_EDGE,
    annotate,
    decode_boxes,
    decode_frame,
    letterbox,
)
from realtime.depth import fuse_object_depth
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
                 tracking_config=DEFAULT_TRACKING_CONFIG, risk_config=DEFAULT_RISK_CONFIG):
        """Functionality: Load verified local weights once on the GPU worker.
        Inputs: pinned weights/manifest, detection config, optional loaded depth estimator,
        immutable tracking/risk configs (tracking=None disables both for independent still images).
        Outputs: persistent model/tensor state. Logic: same source hash/build/head/fusion via
        load_model; declared precision/TF32-off, one CPU thread; default is original FP32 control.
        Constraints: native CUDA must exist; no downloads, random initialization or global service.
        GraphRunner remains None until first JPEG; optional depth activates once on the same GPU.
        """
        import numpy as np
        import torch

        started = time.perf_counter()
        if not torch.cuda.is_available():
            raise RuntimeError("Native CUDA is required for the approved local validation")
        torch.set_num_threads(1)
        torch.set_num_interop_threads(1)
        self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.execution_config = {} if config is None else dict(config)
        self.precision = {"float32": "FP32", "float16": "FP16", "bfloat16": "BF16"}[
            self.execution_config.get("dtype", "float32")
        ]
        self.backend = "PyTorch resident CUDA"
        self.model = load_model(weights_path, self.manifest, "cuda", self.execution_config)
        self.tensor = np.empty((1, 3, INPUT_EDGE, INPUT_EDGE), dtype=np.float32)
        self.runner = None
        self.depth_estimator = depth_estimator
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
        same-frame depth/fusion; history/motion; stateless TTC/risk; render.
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
        inferred_at = time.perf_counter()
        boxes = decode_boxes(prediction, image.size, (scale, left, top), self.manifest["names"])
        decoded_at = time.perf_counter()
        object_tracker = getattr(self, "object_tracker", None)
        if object_tracker is not None:
            boxes = object_tracker.associate(image, boxes)
        tracked_at = time.perf_counter()
        depth_estimator = getattr(self, "depth_estimator", None)
        if depth_estimator is not None:
            depth_map = depth_estimator.predict(image)
            boxes = fuse_object_depth(depth_map, boxes, image.size)
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
        metadata = {"schema_version": 1, "model": "yolo26n", "backend": self.backend,
                    "precision": self.precision, "output_precision": "FP32", "classes": 80,
                    "confidence_threshold": CONFIDENCE_THRESHOLD,
                    "source_sha256": self.manifest["source_sha256"], "width": image.width,
                    "height": image.height, "count": len(boxes), "boxes": boxes,
                    "raw_sha256": hashlib.sha256(prediction.tobytes()).hexdigest(),
                    "jpeg_sha256": hashlib.sha256(result).hexdigest(),
                    "inference_ms": inference_ms, "graph_setup_ms": setup_ms,
                    "prepare_ms": (prepared_at - started) * 1000,
                    "gpu_stage_ms": (inferred_at - prepared_at) * 1000,
                    "render_ms": (finished - (risk_at if object_tracker is not None else
                                              fused_at if depth_estimator is not None else
                                              inferred_at)) * 1000,
                    "server_ms": (finished - started) * 1000,
                    "forward_event_ms": self.runner.forward_event_ms,
                    "execution": {**self.runner.evidence,
                                  **({"jit_cache": self.cache_evidence}
                                     if getattr(self, "cache_evidence", None) else {})}}
        if depth_estimator is not None:
            metadata.update(depth_model=depth_estimator.model_name, depth_units="m",
                            depth_ms=((fused_at - tracked_at) + (decoded_at - inferred_at)) * 1000)
        if object_tracker is not None:
            metadata.update(object_tracker.metadata(),
                            tracking_ms=((tracked_at - decoded_at) + (motion_at - fused_at)) * 1000,
                            tracking_timestamp_s=started if timestamp is None else timestamp,
                            risk_model=RISK_MODEL, risk_ms=(risk_at - motion_at) * 1000)
        return metadata, result
