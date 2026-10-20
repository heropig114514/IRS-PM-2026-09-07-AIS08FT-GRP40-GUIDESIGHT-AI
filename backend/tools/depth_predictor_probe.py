"""Responsibilities: Probe fixed-configuration reuse of the initialized official depth predictor.
Implementation: Bypass only Model.predict argument reconstruction; retain the entire predictor
call including its lock, source setup, profiling, callbacks, warmup, inference and postprocessing.
Related Modules: realtime.depth; tools.benchmark_inference/validate_depth_predictor.
Declaration Index: CachedDepthPredictor; CachedDepthPredictor.__init__;
CachedDepthPredictor.__call__.
Variable Index: CALL_OPTIONS — exact existing DepthEstimator invocation, not new model settings.
"""

import copy
import time

CALL_OPTIONS = {"imgsz": 768, "device": "cuda", "half": False,
                "verbose": False, "save": False, "stream": False}


class CachedDepthPredictor:
    """Functionality: Evaluate removing repeated SDK configuration work for a resident model.
    Logic: retain a warmed predictor and configuration snapshot; delegate unchanged image inputs.
    Constraints: experiment only, serial use, pinned native FP32 depth model; any drift fails
    explicitly without fallback or reconfiguration. No Graph, compilation or precision changes.
    """

    def __init__(self, owner):
        """Functionality: Snapshot a predictor after the normal first-frame initialization.
        Inputs: already initialized YOLO depth owner. Outputs: callable and preparation evidence.
        Logic: require the existing native CUDA/FP32 and fixed-option contract before reuse.
        Constraints: performs no warmup or model calls; first-frame costs remain in the baseline.
        """
        started = time.perf_counter()
        predictor = owner.predictor
        if (predictor is None or not predictor.done_warmup or owner.task != "depth"
                or predictor.model.format != "pt" or predictor.model.fp16
                or predictor.device.type != "cuda"):
            raise ValueError("Probe requires an initialized native CUDA depth predictor")
        expected = {"imgsz": 768, "device": "cuda", "quantize": None, "verbose": False,
                    "save": False, "batch": 1, "rect": True, "embed": None}
        if any(getattr(predictor.args, key, object()) != value for key, value in expected.items()):
            raise ValueError("Probe requires the unchanged FP32 depth configuration")
        self.owner, self.predictor = owner, predictor
        self.model, self.backend = owner.model, predictor.model
        self.options = copy.deepcopy(vars(predictor.args))
        self.overrides = copy.deepcopy(owner.overrides)
        self.evidence = {"adapter": "experimental_cached_depth_predictor",
                         "setup_ms": (time.perf_counter() - started) * 1000,
                         "scope": ("Skip repeated configuration only; "
                                   "full official predictor retained")}

    def __call__(self, *, source, **kwargs):
        """Functionality: Run the same SDK predictor without reconstructing constant arguments.
        Inputs: original PIL RGB source and exact DepthEstimator keyword options.
        Outputs: original SDK results list. Logic: check ownership/configuration, then delegate.
        Constraints: reject options/model/predictor changes before inference; propagate errors
        without retry. Source loading and shape handling still execute on every frame.
        """
        if (kwargs != CALL_OPTIONS or self.owner.predictor is not self.predictor
                or self.owner.model is not self.model or self.predictor.model is not self.backend
                or vars(self.predictor.args) != self.options
                or self.owner.overrides != self.overrides):
            raise ValueError("Cached depth predictor contract changed")
        return self.predictor(source=source, stream=False)
