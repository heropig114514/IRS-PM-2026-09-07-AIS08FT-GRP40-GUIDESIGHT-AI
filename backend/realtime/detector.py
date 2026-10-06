"""Responsibilities: Keep the pinned native-CUDA model and selected-precision Graph resident.
Implementation: Original codec/preprocessing/postprocessing; graph setup on first real frame only.
Related Modules: core.detector; zerogpu.runtime; zerogpu.live_worker owns one serial allocation.
Declaration Index: ResidentDetector; ResidentDetector.__init__; ResidentDetector.predict.
Variable Index: logger — count/timing diagnostics; model/manifest/tensor/runner/load_ms;
execution_config/precision/backend — explicit execution identity, original FP32 control by default.
Metadata forward_event_ms is CUDA replay timing; inference_ms remains transfer-inclusive.
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
from zerogpu.runtime import GraphRunner, load_model

logger = logging.getLogger(__name__)


class ResidentDetector:
    """Functionality: Process ordered frames through one persistent native-CUDA model/graph.
    Logic: lazy graph setup from first real input; FP32 API buffers and explicit compute precision.
    Constraints: caller serializes GPU work; no ZeroGPU decorator, alternate model or CPU fallback.
    """

    def __init__(self, weights_path, manifest_path, config=None):
        """Functionality: Load verified local weights once on the GPU worker.
        Inputs: pinned weight/manifest paths, optional explicit dtype/layout/benchmark config.
        Outputs: persistent model/tensor state. Logic: same source hash/build/head/fusion via
        load_model; declared precision/TF32-off, one CPU thread; default is original FP32 control.
        Constraints: native CUDA must exist; no downloads, random initialization or global service.
        GraphRunner remains None until the first real JPEG; load_ms excludes subsequent graph setup.
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
        self.load_ms = (time.perf_counter() - started) * 1000
        logger.info("Resident model initialized load_ms=%.1f precision=%s edge=%d config=%s",
                    self.load_ms, self.precision, INPUT_EDGE, self.execution_config)

    def predict(self, jpeg):
        """Functionality: Decode, infer and render one complete JPEG through shared processing.
        Inputs: original bounded JPEG and persistent model/manifest/tensor/runner state.
        Outputs: metadata and original-contract annotated JPEG; first call initializes the graph.
        Logic: decode/letterbox, existing10-warmup GraphRunner, replay, original boxes/rendering.
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
                    "render_ms": (finished - inferred_at) * 1000,
                    "server_ms": (finished - started) * 1000,
                    "forward_event_ms": self.runner.forward_event_ms,
                    "execution": {**self.runner.evidence,
                                  **({"jit_cache": self.cache_evidence}
                                     if getattr(self, "cache_evidence", None) else {})}}
        return metadata, result
