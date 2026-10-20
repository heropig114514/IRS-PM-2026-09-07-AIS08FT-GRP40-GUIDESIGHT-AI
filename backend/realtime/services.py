"""Responsibilities: Own one resident GPU pipeline and exclusive video-session state.
Implementation: One executor loads once, resets tracking/distance/statistics per session and
infers serially.
Related Modules: realtime.detector/depth/tracking/distance/performance;
realtime.resident_asgi; config.resident_asgi.
Declaration Index: build_detector; ResidentService; ResidentService.__init__;
ResidentService.execute; ResidentService.start; ResidentService.claim; ResidentService.begin;
ResidentService.reset_tracking; ResidentService.predict; ResidentService.release;
ResidentService.close; ResidentService.contract.
Variable Index: logger — state/counter/error-type diagnostics without media;
ResidentService.executor/detector — one process-owned GPU worker and reusable model;
mode/ready/active/load_ms — explicit workload, readiness, exclusive lease and startup duration.
"""

import asyncio
import json
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from core.detector import DetectionError

logger = logging.getLogger("core.resident.services")


def build_detector(root, mode):
    """Functionality: Load the unchanged FP32 native CUDA pipeline from verified local assets.
    Inputs: backend root and explicit detection/depth/full workload. Outputs: ResidentDetector.
    Logic: optional depth and default tracking/risk; no production FP16-plan substitution.
    Constraints: no download, alternate provider, retry, depth Graph probe or CPU fallback.
    """
    from realtime.depth import DepthEstimator
    from realtime.detector import ResidentDetector
    from realtime.tracking import DEFAULT_TRACKING_CONFIG

    manifest_path = root / "models/yolo26n.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    depth = (DepthEstimator(root / "models/yolo26n-depth.pt", manifest["build"]["ultralytics"])
             if mode != "detection" else None)
    return ResidentDetector(root / "models/yolo26n.pt", manifest_path, config={},
                            depth_estimator=depth,
                            tracking_config=DEFAULT_TRACKING_CONFIG if mode == "full" else None)


class ResidentService:
    """Functionality: Reuse models while isolating mutable tracking across exclusive sessions.
    Logic: event-loop admission plus a single executor; cancellation waits for actual worker exit.
    Constraints: one Uvicorn worker; one lifecycle; unbounded multi-user scheduling is unsupported.
    """

    def __init__(self, root, mode="full", *, factory=build_detector):
        """Functionality: Configure a GPU service without starting CUDA at import time.
        Inputs: asset root, explicit workload and optional test factory. Outputs: idle service.
        Logic: reject unknown modes before creating the executor; no model created until start.
        Constraints: injected factory is a test seam, never a runtime fallback.
        """
        if mode not in {"detection", "depth", "full"}:
            raise ValueError("RESIDENT_MODE must be detection, depth or full")
        self.root, self.mode, self.factory = root, mode, factory
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="resident-gpu")
        self.detector = None
        self.ready = False
        self.active = None
        self.load_ms = 0.0

    async def execute(self, function, *args):
        """Functionality: Run work on the owning thread without releasing it on cancellation.
        Inputs: synchronous callable/arguments. Outputs: result or original failure/cancellation.
        Logic: shield the executor future and drain even repeated caller cancellation.
        Constraints: Python cancellation cannot interrupt CUDA; do not admit new work until done.
        """
        future = asyncio.get_running_loop().run_in_executor(self.executor, function, *args)
        cancelled = False
        while not future.done():
            try:
                await asyncio.shield(future)
            except asyncio.CancelledError:
                cancelled = True
            except Exception:
                break
        if cancelled:
            # Retrieve a worker failure too, so cancellation cannot hide an unhandled future.
            if not future.cancelled():
                error = future.exception()
                if error is not None:
                    raise error
            raise asyncio.CancelledError
        return future.result()

    async def start(self):
        """Functionality: Load exactly once before ASGI startup acknowledgement.
        Inputs: configured factory. Outputs: ready service or startup failure.
        Logic: include imports/model activation in load_ms; graph capture stays on first real input.
        Constraints: no synthetic warmup image or repeated start in the same Torch process.
        """
        if self.detector is not None:
            raise RuntimeError("Resident service already started")
        started = time.perf_counter()
        self.detector = await self.execute(self.factory, self.root, self.mode)
        self.load_ms = (time.perf_counter() - started) * 1000
        self.ready = True
        logger.info("Resident ready mode=%s load_ms=%.1f", self.mode, self.load_ms)

    def claim(self):
        """Functionality: Reserve the one GPU stream without queuing other users.
        Inputs: event-loop service state. Outputs: opaque lease or None.
        Logic: atomically inspect readiness/occupancy without await, then assign a fresh ID.
        Constraints: call on the same ASGI event loop; lease is diagnostic, not authentication.
        """
        if not self.ready or self.active is not None:
            return None
        self.active = uuid.uuid4().hex[:12]
        return self.active

    async def begin(self, lease):
        """Functionality: Create fresh session tracking while preserving models and graph buffers.
        Inputs: current exclusive lease. Outputs: None. Logic: reset on the owning GPU thread.
        Constraints: admission must precede this call; frame processing cannot overlap it.
        """
        if lease != self.active or lease is None:
            raise RuntimeError("Invalid resident lease")
        await self.execute(self.reset_tracking)

    def reset_tracking(self):
        """Functionality: Discard prior IDs, distance state and processing counters on reconnect.
        Inputs: loaded detector and workload. Outputs: fresh tracker/distance/statistics state.
        Logic: preserve selected immutable configuration while resetting mutable per-stream state.
        Constraints: worker-only; does not reload either model or recreate the detection Graph.
        """
        from realtime.distance import DistanceStrategy
        from realtime.distance_config import DEFAULT_DISTANCE_CONFIG
        from realtime.performance import PerformanceStats
        from realtime.tracking import DEFAULT_TRACKING_CONFIG, ObjectTracker

        strategy = getattr(self.detector, "distance_strategy", None)
        self.detector.distance_strategy = DistanceStrategy(
            strategy.config if strategy is not None else DEFAULT_DISTANCE_CONFIG)
        self.detector.performance, self.detector.performance_epoch = PerformanceStats(), 0
        self.detector.object_tracker = (
            ObjectTracker(DEFAULT_TRACKING_CONFIG, self.detector.manifest["build"]["ultralytics"])
            if self.mode == "full" else None)
        logger.info("Resident session state reset mode=%s", self.mode)

    async def predict(self, lease, jpeg):
        """Functionality: Process one ordered frame for the active owner.
        Inputs: lease and validated packet JPEG. Outputs: metadata/JPEG or explicit failure.
        Logic: serialize on the worker; pre-inference input errors reject only the session,
        while pipeline failures invalidate service readiness.
        Constraints: no retry after a potentially corrupted CUDA state; restart explicitly.
        """
        if lease != self.active or lease is None or not self.ready:
            raise RuntimeError("Resident service is unavailable for this lease")
        try:
            return await self.execute(self.detector.predict, jpeg)
        except Exception as error:
            if not isinstance(error, DetectionError) or error.code not in {
                "invalid_frame", "frame_size"
            }:
                self.ready = False
            logger.error("Resident inference failed id=%s type=%s", lease, type(error).__name__)
            raise

    def release(self, lease):
        """Functionality: Release exclusive admission after all session work has terminated.
        Inputs: current lease. Outputs: idle occupancy. Logic: reject stale/double releases.
        Constraints: adapter must drain worker tasks first; this method never cancels GPU work.
        """
        if self.active != lease or lease is None:
            raise RuntimeError("Invalid resident release")
        self.active = None

    async def close(self):
        """Functionality: Stop accepting work and join the executor on ASGI shutdown.
        Inputs: lifecycle end. Outputs: closed worker. Logic: shutdown joins actual thread work.
        Constraints: no new startup/reload in this process; model memory is freed on process exit.
        """
        self.ready = False
        await asyncio.to_thread(self.executor.shutdown, wait=True, cancel_futures=False)
        logger.info("Resident stopped")

    def contract(self):
        """Functionality: Describe actual resident execution without borrowing HF quality claims.
        Inputs: loaded detector. Outputs: public session identity and explicit workload.
        Logic: report verified manifest/precision; no 55-second allocation or fabricated AP results.
        Constraints: weights loaded means model-ready; first frame may still capture CUDA Graph.
        """
        return {"kind": "session", "protocol": "resident-v1", "mode": self.mode,
                "model": "yolo26n", "precision": self.detector.precision,
                "output_precision": "FP32", "backend": self.detector.backend,
                "source_sha256": self.detector.manifest["source_sha256"],
                "names": self.detector.manifest["names"], "classes": 80,
                "confidence_threshold": 0.25, "max_edge": 640, "jpeg_quality": 0.8,
                "model_load_ms": self.load_ms, "session_seconds": None}
