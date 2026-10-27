"""Responsibilities: Coarse completed-stage A timing without detailed per-ROI instrumentation.
Implementation: Instance-local CPU timers; one completed-depth barrier before ROI fusion.
Related Modules: adapters.NativeAdapter; realtime.depth/distance; a_performance.
Declaration Index: FormalA; FormalA.__init__; FormalA.wrap; FormalA.wrap.measured;
FormalA.reset; FormalA.predict.
Variable Index: values holds one frame's coarse timings; experiment selects distance/preprocess.
No per-ROI events or barriers are added.
"""

import time

from .performance import synchronize


class FormalA:
    """Functionality: Compare A implementations with identical lightweight timing boundaries.
    Logic: time CPU input work and completed whole-ROI fusion, sharing one loaded FP32 model.
    Constraints: serial A only; no detailed profiler, per-box events or intermediate downloads.
    """

    def __init__(self, adapter, *, experiment="distance"):
        """Functionality: Install local coarse timers before cold/warmup calls.
        Inputs: initialized native A adapter and experiment axis. Outputs: formal timing wrapper.
        Logic: source packing and source setup are CPU-only; synchronize once after depth.
        Constraints: weight and image-size changes are forbidden in this wrapper.
        """
        if adapter.method != "a" or adapter.depth.image_size != 768 or adapter.depth.half:
            raise ValueError("Formal A requires original 768/FP32")
        if experiment not in {"distance", "preprocess"}:
            raise ValueError("Unknown A experiment")
        self.experiment = experiment
        self.adapter, self.values, self.predictor = adapter, {}, None
        self.wrap(adapter.depth, "prepare_source", "depth_pack_ms")
        self.wrap(adapter.depth, "predict", "depth_model_ms", complete=True)
        self.reset()

    def wrap(self, owner, name, key, *, complete=False):
        """Functionality: Time one original bound operation without per-kernel instrumentation.
        Inputs: owner/method/key and optional completion barrier. Outputs: installed wrapper.
        Logic: perf_counter; CPU returns complete naturally, fusion performs its own final read.
        Constraints: only depth completion adds a GPU barrier; identical for both variants.
        """
        original = getattr(owner, name)

        def measured(*args, **kwargs):
            """Functionality: Accumulate completed operation time. Inputs: original arguments.
            Outputs: original result. Logic: optional final depth barrier then wall elapsed.
            Constraints: no retries or additional GPU synchronization for individual boxes.
            """
            started = time.perf_counter()
            result = original(*args, **kwargs)
            if complete:
                synchronize(self.adapter.device)
            self.values[key] = self.values.get(key, 0.0) + (time.perf_counter() - started) * 1000
            return result

        setattr(owner, name, measured)

    def reset(self):
        """Functionality: Reattach fusion timing after original session reset.
        Inputs: None. Outputs: fresh A strategy. Logic: reset then wrap new strategy once.
        Constraints: model and predictor remain resident; A has no temporal history.
        """
        self.adapter.reset()
        self.wrap(self.adapter.strategy, "fuse", "roi_fusion_ms")

    def predict(self, image, frame, *, fast, timestamp):
        """Functionality: Run one baseline/optimized frame with formal coarse timing.
        Inputs: original image/frame, explicit variant and timestamp. Outputs: objects,times,flag.
        Logic: distance experiment switches packing/ROI; preprocess experiment holds both on
        and switches only detector preparation. Lazily time source setup after cold.
        Constraints: same weights, precision, thresholds and native adapter for both variants.
        """
        self.adapter.depth.fast_input = fast or self.experiment == "preprocess"
        self.adapter.strategy.fast_roi = fast or self.experiment == "preprocess"
        if self.experiment == "preprocess":
            self.adapter.preprocess_fast = fast
        predictor = self.adapter.depth.model.predictor
        if predictor is not None and self.predictor is None:
            self.predictor = predictor
            self.wrap(predictor, "setup_source", "depth_source_setup_ms")
        self.values = {}
        objects, timings, flag = self.adapter.predict(
            image, frame, bbox_mode="predicted", timestamp=timestamp
        )
        timings.update(self.values)
        timings["depth_input_ms"] = self.values.get("depth_pack_ms", 0.0) + self.values.get(
            "depth_source_setup_ms", 0.0
        )
        return objects, timings, flag
