"""Responsibilities: Process live frames inside one decorated ZeroGPU allocation.
Implementation: Reuse original detector prediction code with allocation-local graph/buffers;
pull/return binary packets over capability-protected loopback HTTP; each handoff closes its socket.
Related Modules: app.gpu_live supplies detection/depth models or compiled detection descriptor;
live_transport owns parent queues; aoti/jit_cache validate and load in this allocation;
realtime.detector/tracking/risk/protocol preserve FP32/640/0.25/JPEG80 and binary contracts;
explicit compute config must agree with the verified allocated model.
Declaration Index: AllocatedDetector; AllocatedDetector.__init__; exchange; run_worker.
Variable Index: logger — safe worker phase/count/error diagnostics; worker_pull_ms measures the
HTTP pull including intentional input waits; worker_predict_ms includes the full predict call,
metadata/checksum construction as well as its existing nested model/service timers.
depth_estimator — optional separately root-loaded model, activated once per allocation.
object_tracker — allocation-local BoT-SORT/GMC/history, never retained by the root GPU model.
risk_config — immutable scalar TTC/rule profile, no additional per-object state.
distance_config/strategy/performance — allocation-local distance mode/cache and timing sums.
No retained cross-task graph or subtraction of worker clocks from parent/PRS clocks.
"""

import http.client
import logging
import re
import time

from realtime.detector import ResidentDetector
from realtime.distance import DistanceStrategy
from realtime.distance_config import DEFAULT_DISTANCE_CONFIG
from realtime.performance import PerformanceStats
from realtime.protocol import MAX_PACKET_BYTES, pack_result, unpack_request
from realtime.risk import DEFAULT_RISK_CONFIG
from realtime.tracking import DEFAULT_TRACKING_CONFIG, ObjectTracker
from zerogpu.live_transport import PREFIX, SESSION_SECONDS

logger = logging.getLogger(__name__)


class AllocatedDetector(ResidentDetector):
    """Functionality: Use original prediction logic on an allocated model or verified target AOT.
    Logic: override construction only; inherited predict owns one lazy graph for this allocation.
    Constraints: constructed inside spaces.GPU; never persist this instance across allocations.
    """

    def __init__(self, model, manifest, config=None, *, depth_estimator=None,
                 tracking_config=DEFAULT_TRACKING_CONFIG, risk_config=DEFAULT_RISK_CONFIG,
                 distance_config=DEFAULT_DISTANCE_CONFIG):
        """Functionality: Initialize allocation-local CPU/Graph state around the root-loaded model.
        Inputs: model/descriptor/manifest/config; depth and tracking/risk/distance profiles.
        Outputs: ready original predict state. Logic: FP32 [1,3,640,640] API input storage;
        graph remains None until first real JPEG; metadata declares selected precision.
        AOT or JIT-cache restoration prepares one callable before JPEG processing; load cost and
        cache hit evidence are saved. No live backend code generation is permitted.
        Constraints: config must agree with the verified model/package; no weight download,
        thread reconfiguration, implicit precision change, recompile or CPU/backend fallback;
        callable/Graph and one BoT-SORT/GMC/history remain this allocation; depth activates once.
        """
        import numpy as np

        from core.detector import INPUT_EDGE

        self.model, self.manifest = model, manifest
        self.execution_config = {} if config is None else dict(config)
        self.precision = {"float32": "FP32", "float16": "FP16", "bfloat16": "BF16"}[
            self.execution_config.get("dtype", "float32")
        ]
        self.backend = ("PyTorch compiled CUDA" if self.execution_config.get("compile") else
                        "PyTorch AOT CUDA" if self.execution_config.get("aoti") else
                        "PyTorch resident CUDA")
        self.tensor = np.empty((1, 3, INPUT_EDGE, INPUT_EDGE), dtype=np.float32)
        self.runner, self.load_ms, self.cache_evidence = None, 0.0, None
        if distance_config.distance_mode == "temporal_depth" and (
                depth_estimator is None or tracking_config is None):
            raise ValueError("temporal_depth requires depth and tracking")
        self.distance_strategy = DistanceStrategy(distance_config)
        self.performance, self.performance_epoch = PerformanceStats(), 0
        self.depth_estimator = (None if distance_config.distance_mode == "geometry" else
                                depth_estimator)
        if self.execution_config.get("aoti"):
            from zerogpu.aoti import load_package

            started = time.monotonic()
            self.model = load_package(model, manifest, self.execution_config)
            self.load_ms = (time.monotonic() - started) * 1000
        elif self.execution_config.get("compile"):
            from pathlib import Path

            from zerogpu.jit_cache import load_cache

            self.model, self.cache_evidence = load_cache(
                model, Path(__file__).resolve().parent.parent / "models/yolo26n.pt",
                manifest, self.execution_config)
            self.load_ms = self.cache_evidence["cache_restore_ms"]
        if self.depth_estimator is not None:
            self.depth_estimator.activate()
        self.object_tracker = (ObjectTracker(tracking_config, self.manifest["build"]["ultralytics"])
                               if tracking_config is not None else None)
        self.risk_config = risk_config
        logger.info("Allocated detector initialized backend=%s precision=%s load_ms=%.2f",
                    self.backend, self.precision, self.load_ms)


def exchange(connection, capability, action, body=None):
    """Functionality: Execute one bounded loopback handoff with an explicitly request-local socket.
    Inputs: HTTP connection, secret capability, fixed action and optional bytes body.
    Outputs: (status, bytes); Logic: GET pull or POST control/result, full read then close socket.
    Constraints: capability only in Authorization header; no retries or remote/alternate endpoint.
    Explicit Connection: close prevents server keep-alive expiry while cold CUDA setup is running;
    persistent WS remains the external transport, local TCP overhead is measured by the benchmark.
    """
    try:
        connection.request("GET" if action == "next" else "POST", PREFIX + "/" + action, body,
                           headers={"Authorization": "Bearer " + capability,
                                    "Connection": "close"})
        response = connection.getresponse()
        packet = response.read(MAX_PACKET_BYTES + 1)
        if len(packet) > MAX_PACKET_BYTES:
            raise ValueError("Oversized worker HTTP response")
        return response.status, packet
    finally:
        connection.close()


def run_worker(capability, model, manifest, port=7860, factory=AllocatedDetector, config=None,
               *, depth_estimator=None, tracking_config=None, risk_config=None,
               distance_config=None):
    """Functionality: Hold one model/graph allocation only while this bounded live session runs.
    Inputs:64-hex capability, emulated model or package descriptor/manifest, local port, explicit
    factory, detection config, optional depth and explicit tracking/risk/distance profiles.
    Optional keywords are forwarded only when provided; actual allocated detector defaults to
    one BoT-SORT per session. No tracker/history lives in the root-loaded model.
    Outputs: count/timing; Logic: claim parent, pull ordered JPEG, predict and return binary.
    Add same-worker-clock HTTP-pull and full-predict durations without another HTTP request.
    Pull duration includes waiting for the next paced input and is not inference overhead.
    Constraints: caller MUST decorate with spaces.GPU under original60s/default-size semantics;
    no new live GPU requests, retained cross-allocation graph or skipped frames;
    explicit selected precision/config must already have passed the shared full-COCO plan gate.
    Parent's5s input idle limit,6s HTTP budget and55s loop bound limit allocation lifetime;
    decorated platform deadline remains authoritative if native inference itself blocks.
    Exceptions preserve failure semantics; parent gets failure marker when still reachable.
    """
    if not isinstance(capability, str) or re.fullmatch(r"[0-9a-f]{64}", capability) is None:
        raise ValueError("Invalid live session capability")
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=6)
    count, started, succeeded = 0, time.monotonic(), False
    try:
        status, _ = exchange(connection, capability, "start")
        if status != 200:
            raise RuntimeError(f"Live allocation claim rejected status={status}")
        arguments = (model, manifest) if config is None else (model, manifest, config)
        options = {}
        if depth_estimator is not None:
            options["depth_estimator"] = depth_estimator
        if tracking_config is not None:
            options["tracking_config"] = tracking_config
        if risk_config is not None:
            options["risk_config"] = risk_config
        if distance_config is not None:
            options["distance_config"] = distance_config
        detector = factory(*arguments, **options)
        while time.monotonic() - started < SESSION_SECONDS:
            pull_started = time.perf_counter()
            status, packet = exchange(connection, capability, "next")
            pulled_at = time.perf_counter()
            if status == 410:
                break
            if status != 200:
                raise RuntimeError(f"Live frame handoff rejected status={status}")
            jpeg = unpack_request(packet, count)
            predict_started = time.perf_counter()
            metadata, rendered = detector.predict(jpeg)
            metadata.update(worker_pull_ms=(pulled_at - pull_started) * 1000,
                            worker_predict_ms=(time.perf_counter() - predict_started) * 1000)
            status, _ = exchange(connection, capability, "result",
                                 pack_result({**metadata, "index": count}, rendered))
            if status == 410:  # Peer ended after inference; do not allocate or infer again.
                break
            if status != 200:
                raise RuntimeError(f"Live result handoff rejected status={status}")
            count += 1
        succeeded = True
        elapsed_ms = (time.monotonic() - started) * 1000
        logger.info("ZeroGPU live worker completed frames=%d elapsed_ms=%.1f", count, elapsed_ms)
        return {"frames": count, "gpu_session_ms": elapsed_ms}
    except Exception as error:
        logger.exception("ZeroGPU live worker failed frames=%d type=%s",
                         count, type(error).__name__)
        raise
    finally:
        try:
            exchange(connection, capability, "end", b"ok" if succeeded else b"error")
        except Exception as error:
            logger.warning("ZeroGPU live end notification unavailable type=%s frames=%d",
                           type(error).__name__, count)
        connection.close()
