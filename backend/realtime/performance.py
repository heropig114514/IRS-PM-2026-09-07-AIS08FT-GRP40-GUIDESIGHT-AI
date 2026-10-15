"""Responsibilities: Expose bounded A/B/C server-processing experiment statistics.
Implementation: O(1) cumulative sums and depth/non-depth buckets; no frame storage or logging I/O.
Related Modules: realtime.detector; model_tests.run_depth.
Declaration Index: PerformanceStats; PerformanceStats.__init__; PerformanceStats.record.
Variable Index: totals — detection/distance/end-to-end milliseconds; bucket counts/sums are
successful depth/non-depth frames only; FPS excludes network, input pacing, loading and file I/O.
"""


class PerformanceStats:
    """Functionality: Aggregate successful frame-stage timings without slowing real-time loops.
    Logic: constant-sized sums/counters. Constraints: cold graph/predictor setup is retained.
    """

    def __init__(self):
        """Functionality: Initialize counters. Inputs: None. Outputs: zeroed independent statistics.
        Logic: fixed-size scalar state. Constraints: no global or unbounded frame samples.
        """
        self.total_frames = self.depth_model_calls = 0
        self.totals = dict.fromkeys(("detection_ms", "distance_ms", "end_to_end_ms"), 0.0)
        self.depth_total_ms = self.non_depth_total_ms = 0.0

    def record(self, *, detection_ms, distance_ms, end_to_end_ms, depth_frame):
        """Functionality: Add one complete server frame and return a serializable summary.
        Inputs: wall-clock stage timings in ms and actual depth invocation flag.
        Outputs: averages, processing FPS, invocation ratio and separated frame latency buckets.
        Logic: sum/count; empty bucket averages are None. Constraints: processing FPS is not
        camera/delivered FPS; end-to-end means decode through annotation on this worker only.
        """
        self.total_frames += 1
        self.depth_model_calls += int(depth_frame)
        for key, value in (("detection_ms", detection_ms), ("distance_ms", distance_ms),
                           ("end_to_end_ms", end_to_end_ms)):
            self.totals[key] += value
        if depth_frame:
            self.depth_total_ms += end_to_end_ms
        else:
            self.non_depth_total_ms += end_to_end_ms
        non_depth = self.total_frames - self.depth_model_calls
        return {"total_frames": self.total_frames, "depth_model_calls": self.depth_model_calls,
                "actual_depth_inference_ratio": self.depth_model_calls / self.total_frames,
                **{f"average_{key}": value / self.total_frames
                   for key, value in self.totals.items()},
                "fps": 1000 * self.total_frames / self.totals["end_to_end_ms"]
                if self.totals["end_to_end_ms"] > 0 else None,
                "depth_frame_count": self.depth_model_calls, "non_depth_frame_count": non_depth,
                "average_depth_frame_ms": self.depth_total_ms / self.depth_model_calls
                if self.depth_model_calls else None,
                "average_non_depth_frame_ms": self.non_depth_total_ms / non_depth
                if non_depth else None}
