"""Responsibilities: Measure synchronized inference stages and observed process memory peaks.
Implementation: Explicit CUDA barriers, finite aggregate percentiles and a bounded RSS sampler.
Related Modules: unified_evaluation.adapters/runner; psutil is evaluation-only.
Declaration Index: synchronize; statistics; latency_summary; MemorySampler;
MemorySampler.__init__; MemorySampler.sample; MemorySampler._poll; MemorySampler.start;
MemorySampler.stop; SequenceClock; SequenceClock.__init__; SequenceClock.advance.
Variable Index: None.
"""

import threading

import numpy as np


def synchronize(device):
    """Functionality: Close the selected CUDA device's asynchronous timing boundary.
    Inputs: device string. Outputs: None. Logic: CUDA synchronization only for CUDA devices.
    Constraints: CPU is used only when explicitly requested for tests/E; A/B/C require CUDA.
    """
    import torch

    if torch.device(device).type == "cuda":
        torch.cuda.synchronize(device)


def statistics(values):
    """Functionality: Summarize finite millisecond observations without invented empty zeros.
    Inputs: timing list. Outputs: count,mean,P50,P95 or null statistics.
    Logic: NumPy percentiles; reject invalid times. Constraints: caller selects cold/warm/steady.
    """
    values = np.asarray(values, dtype=np.float64)
    if not np.isfinite(values).all() or (values < 0).any():
        raise ValueError("Timings must be finite and nonnegative")
    return {
        "count": len(values),
        "mean": float(values.mean()) if len(values) else None,
        "p50": float(np.percentile(values, 50)) if len(values) else None,
        "p95": float(np.percentile(values, 95)) if len(values) else None,
    }


def latency_summary(rows):
    """Functionality: Summarize each explicit stage and aggregate throughput.
    Inputs: steady frame timing dictionaries. Outputs: stage statistics and FPS.
    Logic: FPS=1000*N/sum(end_to_end_ms). Constraints: no mean of instantaneous frame FPS.
    """
    keys = sorted({key for row in rows for key in row if key.endswith("_ms")})
    result = {
        key: statistics([row[key] for row in rows if row.get(key) is not None]) for key in keys
    }
    elapsed = sum(row["end_to_end_ms"] for row in rows)
    result["frames"] = len(rows)
    result["fps"] = 1000 * len(rows) / elapsed if elapsed > 0 else None
    return result


class MemorySampler:
    """Functionality: Observe process RSS during model load/warmup/inference without blocking GPU.
    Logic: poll every10ms and sample explicit stage boundaries; retain observed maximum.
    Constraints: RSS polling can miss shorter peaks; Windows peak_wset is a separate lifetime peak.
    """

    def __init__(self):
        """Functionality: Allocate process-local memory instrumentation.
        Inputs: None. Outputs: independent stopped sampler. Logic: psutil current process.
        Constraints: child workers report their own RSS rather than the orchestrator's memory.
        """
        import psutil

        self.process = psutil.Process()
        self.stop_event = threading.Event()
        self.peak, self.lifetime_peak, self.baseline = 0, None, 0
        self.thread = threading.Thread(target=self._poll, daemon=True)

    def sample(self):
        """Functionality: Update observed resident bytes and OS lifetime peak when available.
        Inputs: None. Outputs: None. Logic: memory_info snapshot.
        Constraints: process private/committed bytes are not mislabeled as resident memory.
        """
        info = self.process.memory_info()
        self.peak = max(self.peak, info.rss)
        self.lifetime_peak = getattr(info, "peak_wset", None)

    def _poll(self):
        """Functionality: Sample until stopped. Inputs: None. Outputs: None.
        Logic: event wait10ms. Constraints: no busy loop or per-frame device synchronization.
        """
        while not self.stop_event.wait(0.01):
            self.sample()

    def start(self):
        """Functionality: Begin observing before model loading.
        Inputs: None. Outputs: None. Logic: sample baseline then launch thread.
        Constraints: one start per sampler.
        """
        self.sample()
        self.baseline = self.peak
        self.thread.start()

    def stop(self):
        """Functionality: Join sampling and return documented memory boundaries.
        Inputs: None. Outputs: byte counters and polling interval.
        Logic: final snapshot after thread shutdown. Constraints: no claim of exact sub10ms peaks.
        """
        self.stop_event.set()
        self.thread.join()
        self.sample()
        return {
            "process_rss_baseline_bytes": self.baseline,
            "peak_process_rss_bytes": self.peak,
            "os_process_lifetime_peak_rss_bytes": self.lifetime_peak,
            "rss_sampling_interval_ms": 10,
        }


class SequenceClock:
    """Functionality: Reset per-scene/camera model state and retain source frame intervals.
    Logic: invoke adapter.reset at stream boundaries; convert integer microseconds to seconds.
    Constraints: input order must already be sorted; processing wall time never drives tracking.
    """

    def __init__(self):
        """Functionality: Start without a stream. Inputs: None. Outputs: empty clock.
        Logic: instance ownership. Constraints: no clock state shared between evaluation passes.
        """
        self.key, self.previous = None, None

    def advance(self, frame, adapter):
        """Functionality: Start a stream or advance its real timestamp.
        Inputs: frame metadata and resettable adapter. Outputs: source seconds and delta seconds.
        Logic: reset once per boundary; reject nonincreasing source clocks.
        Constraints: reset includes tracker, cache and depth schedule, excluding loaded weights.
        """
        key, timestamp = (frame["scene_id"], frame["camera"]), frame["timestamp"]
        if type(timestamp) is not int or timestamp < 0:
            raise ValueError("Expected integer source microseconds")
        if key != self.key:
            adapter.reset()
            self.key, self.previous = key, None
        if self.previous is not None and timestamp <= self.previous:
            raise ValueError("Sequence timestamps must increase")
        delta = None if self.previous is None else (timestamp - self.previous) / 1e6
        self.previous = timestamp
        return timestamp / 1e6, delta
