"""Responsibilities: Attribute A latency to synchronized predictor stages and individual ROIs.
Implementation: Isolated-worker wrappers, reusable CUDA events, unchanged original operations.
Related Modules: stage_profile; realtime.depth/distance; Ultralytics DepthPredictor.
Declaration Index: CudaTimer; CudaTimer.__init__; CudaTimer.call; DetailedProfile;
DetailedProfile.__init__; DetailedProfile.patch; DetailedProfile.patch.measured;
DetailedProfile.install_predictor; DetailedProfile.roi; DetailedProfile.fuse;
DetailedProfile.predict; DetailedProfile.close; export_box_timings.
Variable Index: EXCLUSIVE_STAGES lists disjoint wall-time fields, excluding nested CUDA intervals.
Temporary module wrappers require one serial isolated evaluation worker.
"""

import csv
import json
import time
from pathlib import Path

from .performance import statistics, synchronize
from .stage_profile import StageProfile

EXCLUSIVE_STAGES = (
    ("Detection CPU preprocessing", "prepare_ms"),
    ("YOLO runner including H2D/D2H", "detection_runner_ms"),
    ("Detection CPU decoding", "detection_decode_ms"),
    ("Detection control/sync residual", "detection_control_other_ms"),
    ("Tracking branch overhead (no tracker)", "tracking_ms"),
    ("Depth input/source conversion", "depth_source_wall_ms"),
    ("Depth preprocessing including H2D", "depth_preprocess_wall_ms"),
    ("Depth forward", "depth_forward_wall_ms"),
    ("Depth postprocessing", "depth_postprocess_wall_ms"),
    ("Depth API/control/sync residual", "depth_api_other_ms"),
    ("ROI median sum", "roi_median_wall_ms"),
    ("ROI collection/transfer/packaging residual", "roi_collection_other_ms"),
    ("Distance control/sync residual", "distance_control_other_ms"),
)


class CudaTimer:
    """Functionality: Measure completed wall time and optional CUDA stream elapsed time.
    Logic: drain prior work, record events on the current stream, await completion before reading.
    Constraints: event intervals may include launch gaps; never label them summed kernel busy time.
    """

    def __init__(self, device):
        """Functionality: Allocate timer state. Inputs: device. Outputs: empty accumulators.
        Logic: cache event pairs per stage. Constraints: serial execution outside graph capture.
        """
        self.device, self.values, self.events = device, {}, {}

    def call(self, key, operation, *args, events=False, **kwargs):
        """Functionality: Execute an original operation once and account completed elapsed time.
        Inputs: stage, callable, arguments and event flag. Outputs: original return value.
        Logic: full-device barriers prevent previous-stage work spilling into the measurement.
        Constraints: event duration is nested inside wall duration, not an additional cost.
        """
        import torch

        if events and key not in self.events:
            self.events[key] = [torch.cuda.Event(enable_timing=True) for _ in range(2)]
        synchronize(self.device)
        started = time.perf_counter()
        if events:
            begin, end = self.events[key]
            begin.record(torch.cuda.current_stream(self.device))
        result = operation(*args, **kwargs)
        if events:
            end.record(torch.cuda.current_stream(self.device))
            end.synchronize()
        synchronize(self.device)
        wall = (time.perf_counter() - started) * 1000
        self.values[key + "_wall_ms"] = self.values.get(key + "_wall_ms", 0.0) + wall
        if events:
            elapsed = begin.elapsed_time(end)
            self.values[key + "_cuda_ms"] = self.values.get(key + "_cuda_ms", 0.0) + elapsed
        return result


class DetailedProfile(StageProfile):
    """Functionality: Profile A without replacing its predictor, fusion or detector algorithms.
    Logic: install lazy predictor hooks after the excluded cold call; wrap original ROI functions.
    Constraints: A only, one serial isolated process; close restores temporary module hooks.
    """

    def __init__(self, adapter):
        """Functionality: Attach completed-time profiling to an isolated native A adapter.
        Inputs: initialized A adapter. Outputs: profiling wrapper with per-frame ROI records.
        Logic: retain original functions and defer lazy predictor attachment until initialized.
        Constraints: concurrent or nested module profiling is rejected; no precision changes.
        """
        from realtime import depth, distance

        if adapter.method != "a" or adapter.depth is None:
            raise ValueError("Detailed profiling requires native A")
        if getattr(depth.roi_median, "__self__", None) is not None:
            raise ValueError("Nested ROI profiling is not supported")
        super().__init__(adapter)
        self.timer, self.box_rows, self.restores = CudaTimer(adapter.device), [], []
        self.predictor = None
        self.original_roi, self.original_fuse = depth.roi_median, distance.fuse_object_depth
        self.restores.extend([(depth, "roi_median", self.original_roi),
                              (distance, "fuse_object_depth", self.original_fuse)])
        depth.roi_median, distance.fuse_object_depth = self.roi, self.fuse
        self.identity["detailed_profile"] = {
            "wall": "perf_counter with pre/post full-device synchronization",
            "cuda": "current-stream reusable events; end synchronized before elapsed_time",
            "caveat": "CUDA intervals include launch gaps and overlap their wall intervals; "
            "never add them together. Per-ROI barriers add diagnostic overhead. "
            "Cold initialization and warmup excluded; no image-file decoding or report I/O.",
        }

    def patch(self, owner, name, key, *, events=False):
        """Functionality: Time an instance method while preserving its return and arguments.
        Inputs: owner, method and stage name. Outputs: installed wrapper.
        Logic: retain exact original bound method for cleanup. Constraints: serial worker only.
        """
        original = getattr(owner, name)
        self.restores.append((owner, name, original))

        def measured(*args, **kwargs):
            """Functionality: Forward one timed call. Inputs: unchanged arguments.
            Outputs: original result. Logic: delegate to synchronized timer. Constraints: no retry.
            """
            return self.timer.call(key, original, *args, events=events, **kwargs)

        setattr(owner, name, measured)

    def install_predictor(self):
        """Functionality: Hook the actual lazy-created DepthPredictor after cold initialization.
        Inputs: current adapter. Outputs: four stage wrappers.
        Logic: cover source conversion, preprocessing, inference and postprocessing.
        Constraints: rebuilding a predictor mid-run fails instead of silently losing measurements.
        """
        predictor = self.adapter.depth.model.predictor
        if self.predictor is not None:
            if predictor is not self.predictor:
                raise RuntimeError("Depth predictor changed during detailed profiling")
            return
        self.predictor = predictor
        for name, key, events in (
            ("setup_source", "depth_source", False),
            ("preprocess", "depth_preprocess", False),
            ("inference", "depth_forward", True),
            ("postprocess", "depth_postprocess", True),
        ):
            self.patch(predictor, name, key, events=events)

    def roi(self, depth_map, bounds):
        """Functionality: Time each original ROI median without changing its math or dtype.
        Inputs: original depth plane and clamped bounds. Outputs: original scalar tensor.
        Logic: accumulate stage totals and retain individual wall/event samples.
        Constraints: invalid and unmatched detection ROIs are still measured, not filtered.
        """
        before = dict(self.timer.values)
        result = self.timer.call("roi_median", self.original_roi, depth_map, bounds, events=True)
        self.box_rows.append({
            "index": len(self.box_rows), "bounds": bounds,
            "pixels": 0 if bounds is None else (bounds[2] - bounds[0]) * (bounds[3] - bounds[1]),
            "wall_ms": self.timer.values["roi_median_wall_ms"]
            - before.get("roi_median_wall_ms", 0),
            "cuda_ms": self.timer.values["roi_median_cuda_ms"]
            - before.get("roi_median_cuda_ms", 0),
        })
        return result

    def fuse(self, depth_map, boxes, image_size):
        """Functionality: Time unchanged whole fusion including median collection and output work.
        Inputs: original map/boxes/frame size. Outputs: original fused detections.
        Logic: nested ROI timings remain separate from the inclusive fusion total.
        Constraints: the residual includes host transfer, packaging and instrumentation overhead.
        """
        result = self.timer.call("roi_fusion", self.original_fuse, depth_map, boxes, image_size)
        for row, box in zip(self.box_rows, boxes, strict=True):
            row["class_name"] = box["label"]
        return result

    def predict(self, *args, **kwargs):
        """Functionality: Add detailed steady-frame stages and retain original output fields.
        Inputs: unchanged inference arguments. Outputs: original predictions plus scalar timings.
        Logic: clear samples, call original pipeline, report disjoint residuals and nested events.
        Constraints: cold predictor creation is not falsely split into ordinary forward stages.
        """
        ready = self.predictor is not None
        self.timer.values, self.box_rows = {}, []
        objects, timings, flag = super().predict(*args, **kwargs)
        self.install_predictor()
        if ready:
            values = self.timer.values
            values.setdefault("roi_median_wall_ms", 0.0)
            values.setdefault("roi_median_cuda_ms", 0.0)
            values["depth_api_other_ms"] = timings["depth_model_ms"] - sum(
                values[key + "_wall_ms"] for key in
                ("depth_source", "depth_preprocess", "depth_forward", "depth_postprocess")
            )
            values["roi_collection_other_ms"] = (
                values["roi_fusion_wall_ms"] - values["roi_median_wall_ms"]
            )
            values["distance_control_other_ms"] = (
                timings["distance_ms"] - timings["depth_model_ms"] - values["roi_fusion_wall_ms"]
            )
            values["detection_control_other_ms"] = (
                timings["detection_ms"] - timings["detection_runner_ms"]
                - timings["detection_decode_ms"]
            )
            if any(v < -0.01 for k, v in values.items() if k.endswith("_other_ms")):
                residuals = {k: v for k, v in values.items() if k.endswith("_other_ms")}
                raise RuntimeError(f"Detailed timing intervals overlap unexpectedly: {residuals}")
            timings.update(values)
        return objects, timings, flag

    def close(self):
        """Functionality: Restore temporary hooks after success or failure.
        Inputs: None. Outputs: original module functions and predictor methods restored.
        Logic: undo in reverse order. Constraints: idempotent, no model reload or algorithm change.
        """
        for owner, name, original in reversed(self.restores):
            setattr(owner, name, original)
        self.restores.clear()


def export_box_timings(output):
    """Functionality: Export every timed detection ROI and pooled object-weighted statistics.
    Inputs: completed pass directory. Outputs: roi_timings.csv and roi_summary.json.
    Logic: read saved per-frame records after inference; empty frames remain in frame denominator.
    Constraints: no average-of-frame-averages, no GT-only selection, no inference or retiming.
    """
    output = Path(output)
    fields = ("image_id", "index", "class_name", "pixels", "bounds", "wall_ms", "cuda_ms")
    wall, cuda, frames = [], [], 0
    with ((output / "predictions.jsonl").open(encoding="utf-8") as source,
          (output / "roi_timings.csv").open("w", newline="", encoding="utf-8") as target):
        writer = csv.DictWriter(target, fieldnames=fields)
        writer.writeheader()
        for line in source:
            frame = json.loads(line)
            frames += 1
            for box in frame["roi_timings"]:
                writer.writerow({"image_id": frame["image_id"], **box,
                                 "bounds": json.dumps(box["bounds"])})
                wall.append(box["wall_ms"])
                cuda.append(box["cuda_ms"])
    report = {"frames": frames, "roi_calls": len(wall),
              "mean_rois_per_frame": len(wall) / frames if frames else None,
              "per_roi_wall_ms": statistics(wall), "per_roi_cuda_ms": statistics(cuda),
              "scope": "All detector ROIs, including unscored frames and unmatched detections; "
              "CUDA intervals overlap wall time and may include launch gaps."}
    (output / "roi_summary.json").write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
