"""Responsibilities: Measure existing depth and BoT-SORT sub-stages without algorithm changes.
Implementation: Instance-local call timers around original functions; synchronized depth timing.
Related Modules: adapters.NativeAdapter; realtime.depth/tracking; performance.synchronize.
Declaration Index: StageProfile; StageProfile.__init__; StageProfile.wrap;
StageProfile.wrap.measured; StageProfile.reset; StageProfile.predict.
Variable Index: None. Nested stage measurements must not be added to inclusive stage totals.
"""

import time

from .performance import synchronize


class StageProfile:
    """Functionality: Profile one resident adapter without changing inputs, outputs or settings.
    Logic: delegate calls, time depth prediction, GMC, association and tracker update separately.
    Constraints: profiling adds timing overhead; only evaluation instances are wrapped.
    """

    def __init__(self, adapter):
        """Functionality: Install local wrappers on unchanged adapter dependencies.
        Inputs: initialized adapter. Outputs: profiler retaining the original adapter identity.
        Logic: wrap bound methods only; tracker.reset retains the wrapped tracker/GMC instances.
        Constraints: no global monkeypatches, tracker configuration changes or external model calls.
        """
        self.adapter, self.identity, self.values = adapter, adapter.identity, {}
        self.identity = {**self.identity, "stage_profile": True}
        if getattr(adapter, "depth", None) is not None:
            self.wrap(adapter.depth, "predict", "depth_model_ms", cuda=True)
        tracker = getattr(adapter, "tracker", None)
        if tracker is not None:
            self.wrap(tracker.tracker, "update", "tracking_update_ms")
            self.wrap(tracker.tracker.gmc, "apply", "tracking_gmc_ms")
            for name in (
                "_first_association",
                "_post_first_association",
                "_second_association",
                "_unconfirmed_association",
            ):
                self.wrap(tracker.tracker, name, "tracking_association_ms")

    def wrap(self, owner, name, key, cuda=False):
        """Functionality: Time a bound method while preserving its result and exceptions.
        Inputs: instance, method name, accumulator key and CUDA boundary flag. Outputs: None.
        Logic: sum repeated stage calls within the current frame. Constraints: serial adapter only.
        """
        original = getattr(owner, name)

        def measured(*args, **kwargs):
            """Functionality: Time the preserved operation. Inputs: unchanged call arguments.
            Outputs: original result. Logic: optional CUDA barriers then elapsed accumulation.
            Constraints: errors propagate; no retries or substitutions.
            """
            if cuda:
                synchronize(self.adapter.device)
            started = time.perf_counter()
            result = original(*args, **kwargs)
            if cuda:
                synchronize(self.adapter.device)
            self.values[key] = self.values.get(key, 0.0) + (time.perf_counter() - started) * 1000
            return result

        setattr(owner, name, measured)

    def reset(self):
        """Functionality: Forward actual stream reset. Inputs: None. Outputs: None.
        Logic: call original reset once. Constraints: profiling never resets within a sequence.
        """
        return self.adapter.reset()

    def predict(self, *args, **kwargs):
        """Functionality: Add exclusive stage estimates to existing inclusive timings.
        Inputs: original prediction call. Outputs: same objects/depth flag plus timing fields.
        Logic: subtract nested measurements to isolate image/Boxes handling and tracker state work.
        Constraints: association includes its Kalman updates; depth timing includes preprocessing.
        """
        self.values = {}
        objects, timings, flag = self.adapter.predict(*args, **kwargs)
        values = {
            key: self.values.get(key, 0.0)
            for key in (
                "depth_model_ms",
                "tracking_update_ms",
                "tracking_gmc_ms",
                "tracking_association_ms",
            )
        }
        values["tracking_io_ms"] = max(
            0.0, timings.get("tracking_ms", 0) - values["tracking_update_ms"]
        )
        values["tracking_state_ms"] = max(
            0.0,
            values["tracking_update_ms"]
            - values["tracking_gmc_ms"]
            - values["tracking_association_ms"],
        )
        values["distance_postprocess_ms"] = max(
            0.0, timings["distance_ms"] - values["depth_model_ms"]
        )
        values["other_ms"] = max(
            0.0,
            timings["end_to_end_ms"]
            - sum(
                timings.get(key, 0)
                for key in ("prepare_ms", "detection_ms", "tracking_ms", "distance_ms")
            ),
        )
        return objects, {**timings, **values}, flag
