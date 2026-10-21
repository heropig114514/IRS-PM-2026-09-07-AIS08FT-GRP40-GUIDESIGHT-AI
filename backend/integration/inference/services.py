"""Responsibilities: Adapt the existing complete algorithm to one isolated local GPU worker.
Implementation: Lazy load-once dedicated thread; serialized sessions; PRF2 in and PRS1 out.
Related Modules: realtime.detector/depth/tracking/risk; integration.protocol/errors;
integration.inference.asgi. No Django/HF/DashScope/ORS import or external model download.
Declaration Index: load_detector; InferenceRuntime; InferenceRuntime.__init__;
InferenceRuntime.contract; InferenceRuntime.status; InferenceRuntime.predict;
InferenceRuntime.submit; InferenceRuntime.close; WorkerSession; WorkerSession.__init__;
InferenceWorker; InferenceWorker.__init__; InferenceWorker.__call__;
InferenceWorker.read_frames; InferenceWorker.process_frames; InferenceWorker.lifetime;
InferenceWorker.cleanup; InferenceWorker.drain; InferenceWorker.finish.
Variable Index: logger — safe lifecycle diagnostics; runtime — one process-local algorithm owner;
executor — single native worker thread; load_error — explicit initialization failure latch;
detector/hashes/active_session/frame_size — worker-owned resident/state/provenance;
distance_config — startup-selected A/B/C profile; B has no depth dependency or hash;
incoming/received/delivered/inflight — bounded session accounting and uncancellable GPU work.
session_seconds/stopping — frozen explicit session policy and cancellation-safe continuous wait.
"""

import asyncio
import hashlib
import json
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from threading import BoundedSemaphore

from core.detector import DetectionError, decode_frame
from integration.errors import IntegrationError
from integration.protocol import (
    COLD_SECONDS,
    IDLE_SECONDS,
    MAX_INFLIGHT,
    ROOT,
    SESSION_SECONDS,
    STEADY_SECONDS,
    is_loopback,
    is_stop,
    local_session_seconds,
    model_contract,
    read_manifest,
    unpack_request,
    validate_result,
    web_origins,
)
from realtime.distance_config import read_distance_config
from realtime.protocol import pack_result, unpack_result

logger = logging.getLogger(__name__)


def load_detector(distance_config=None):
    """Functionality: Load the existing native FP32 complete pipeline and record actual assets.
    Inputs: fixed model files, optional startup-frozen profile. Outputs: detector and fingerprints.
    Logic: preflight selected local files/CUDA; A uses original constructors/default rules;
    B does not open/hash/initialize depth weights; C wraps the same depth fusion with tracking.
    Constraints: lazy imports, no download/CPU fallback/implicit detection parameter changes;
    depth hash records supplied bytes, not independently verified calibration or provenance.
    """
    weights, depth_weights = ROOT / "models/yolo26n.pt", ROOT / "models/yolo26n-depth.pt"
    distance_config = read_distance_config() if distance_config is None else distance_config
    needs_depth = distance_config.distance_mode != "geometry"
    if not weights.is_file() or (needs_depth and not depth_weights.is_file()):
        raise IntegrationError("local_model_missing", "请准备当前测距模式需要的本地模型权重。")
    import torch

    if not torch.cuda.is_available():
        raise IntegrationError("local_cuda_unavailable", "本地推理需要可用的 NVIDIA CUDA。")
    from realtime.depth import DepthEstimator
    from realtime.detector import ResidentDetector

    manifest_path = ROOT / "models/yolo26n.json"
    manifest = read_manifest(manifest_path)
    hashes = {
        "detection_sha256": hashlib.sha256(weights.read_bytes()).hexdigest(),
        "depth_sha256": (hashlib.sha256(depth_weights.read_bytes()).hexdigest()
                         if needs_depth else None),
    }
    if hashes["detection_sha256"] != manifest["source_sha256"]:
        raise IntegrationError("local_model_invalid", "检测权重与模型清单指纹不匹配。")
    depth = DepthEstimator(depth_weights, manifest["build"]["ultralytics"]) if needs_depth else None
    return ResidentDetector(weights, manifest_path, depth_estimator=depth,
                            distance_config=distance_config), hashes


class InferenceRuntime:
    """Functionality: Own one persistent native detector outside the software server process.
    Logic: all model/reset/predict work uses one thread; immutable assets survive sessions.
    Constraints: one configured pipeline; initialization failure requires operator restart;
    no repeated constructor/thread settings, automatic retries, CPU or service substitutions.
    """

    def __init__(self, factory=load_detector, manifest_reader=read_manifest):
        """Functionality: Initialize unloaded process state without Torch or model I/O.
        Inputs: explicit factory/manifest test seams. Outputs: lazy runtime.
        Logic: thread creation is lazy inside ThreadPoolExecutor. Constraints: no inference here.
        """
        self.factory, self.manifest_reader = factory, manifest_reader
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="prs-local-gpu")
        self.detector = self.load_error = self.last_inference = self.active_session = None
        self.hashes, self.frame_size, self.busy = {}, None, False
        self.distance_config = read_distance_config()
        self.session_seconds = local_session_seconds()
        if factory is load_detector:
            # Freeze configuration before hello; lazy model loading must use that same identity.
            self.factory = partial(factory, distance_config=self.distance_config)

    def contract(self, session_id):
        """Functionality: Declare identity without initializing GPU state.
        Inputs: session ID. Outputs: native pipeline hello.
        Logic: lightweight validated manifest and actual resident flag.
        Constraints: loaded false does not imply GPU, depth accuracy or runtime readiness.
        """
        return model_contract(session_id, self.manifest_reader(), self.detector is not None,
                              distance_mode=self.distance_config.distance_mode,
                              depth_interval=self.distance_config.depth_interval,
                              session_seconds=self.session_seconds)

    def status(self):
        """Functionality: Report process admission, resident state and observed inference only.
        Inputs: in-memory state and file existence. Outputs: safe health dictionary.
        Logic: no hashing or loading in health; expose asset fingerprints after successful load.
        Constraints: no private paths, credentials, GPU invocation or accuracy claims.
        """
        try:
            expected = self.manifest_reader()["source_sha256"]
            manifest_valid = True
        except IntegrationError:
            expected, manifest_valid = None, False
        loaded = self.detector is not None
        return {
            "status": "ok",
            "provider": "local",
            "loaded": loaded,
            "ready": loaded and self.load_error is None,
            "busy": self.busy,
            "accept_available": not self.busy and self.load_error is None and manifest_valid,
            "last_inference": self.last_inference,
            "error_code": self.load_error.code if self.load_error else None,
            "models": {
                "detection": {
                    "present": (ROOT / "models/yolo26n.pt").is_file(),
                    "expected_sha256": expected,
                    "verified_sha256": self.hashes.get("detection_sha256"),
                },
                "depth": {
                    "present": (ROOT / "models/yolo26n-depth.pt").is_file(),
                    "sha256": self.hashes.get("depth_sha256"),
                },
            },
            "distance_mode": self.distance_config.distance_mode,
            "session_seconds": self.session_seconds,
            "depth_interval": self.distance_config.depth_interval,
            "capabilities": {"detection": True,
                             "depth": self.distance_config.distance_mode != "geometry",
                             "tracking": True, "risk": True},
        }

    def predict(self, jpeg, timestamp, session_id, index):
        """Functionality: Validate geometry and invoke the unchanged algorithm once.
        Inputs: JPEG/capture seconds/session/index. Outputs: original metadata and annotated JPEG.
        Logic: decode before initialization, lazy load once, explicit new-session tracker reset,
        preserve Graph/model buffers, then source-timestamped predict and provenance enrichment.
        Constraints: dedicated executor only; initialization failures latch rather than retrying
        process-global Torch thread configuration; geometry changes require a new session.
        """
        size = decode_frame(jpeg).size
        if self.load_error is not None:
            raise self.load_error
        if self.detector is None:
            try:
                detector, hashes = self.factory()
                if (detector.depth_estimator is None and
                    self.distance_config.distance_mode != "geometry") or (
                        detector.object_tracker is None):
                    raise IntegrationError(
                        "local_pipeline_invalid", "本地推理必须启用完整算法链路。"
                    )
                self.detector, self.hashes = detector, hashes
            except Exception as error:
                self.load_error = (
                    error
                    if isinstance(error, IntegrationError | DetectionError)
                    else IntegrationError(
                        "local_model_load_failed", "本地模型初始化失败，请检查推理日志后重启。"
                    )
                )
                logger.error(
                    "Local model load failed type=%s code=%s",
                    type(error).__name__,
                    self.load_error.code,
                )
                raise self.load_error from None
        if self.active_session != session_id:
            if self.active_session is not None:
                self.detector.object_tracker.reset()
            self.active_session, self.frame_size = session_id, None
            logger.info("Local algorithm stream started session=%s resident=true", session_id)
        if self.frame_size is not None and size != self.frame_size:
            raise IntegrationError(
                "frame_geometry_changed", "画面尺寸变化，请停止并开启新会话。", 400
            )
        self.frame_size = size
        metadata, rendered = self.detector.predict(jpeg, timestamp=timestamp)
        metadata = {
            **metadata,
            "index": index,
            "provider": "local",
            "session_id": session_id,
            "capture_time_s": timestamp,
            "timestamp_source": "browser_capture",
        }
        self.last_inference = {
            "session_id": session_id,
            "index": index,
            "completed_at_unix_s": time.time(),
            "capture_time_s": timestamp,
            "server_ms": metadata.get("server_ms"),
        }
        return metadata, rendered

    def submit(self, jpeg, timestamp, session_id, index):
        """Functionality: Schedule serial native work without blocking the ASGI loop.
        Inputs: one admitted frame. Outputs: asyncio future wrapping the native thread call.
        Logic: one dedicated executor. Constraints: caller must shield and drain before release.
        """
        return asyncio.get_running_loop().run_in_executor(
            self.executor, self.predict, jpeg, timestamp, session_id, index
        )

    async def close(self):
        """Functionality: Wait for owned native work before interpreter shutdown.
        Inputs: executor. Outputs: None. Logic: wait=True shutdown outside the ASGI thread.
        Constraints: never kill an in-flight CUDA operation or restart the executor.
        """
        await asyncio.to_thread(self.executor.shutdown, wait=True)


class WorkerSession:
    """Functionality: Keep one bounded stream's request and cancellation state.
    Logic: queues and counters remain session-local. Constraints: no stored media or cross-stream
    IDs.
    """

    def __init__(self):
        """Functionality: Create one unstarted stream.
        Inputs: UUID/asyncio primitives. Outputs: bounded mutable session state.
        Logic: no model work. Constraints: in-flight native future is retained through cancellation.
        """
        self.id = uuid.uuid4().hex
        self.incoming = asyncio.Queue(MAX_INFLIGHT)
        self.received = self.delivered = 0
        self.previous_timestamp = self.inflight = None
        self.disconnected = False
        self.started = asyncio.Event()
        self.stopping = asyncio.Event()


class InferenceWorker:
    """Functionality: Run one loopback-only timestamped GPU stream at a time.
    Logic: independent input watcher, serial GPU processor and bounded session lifetime.
    Constraints: admission remains held until uncancellable native work actually finishes.
    """

    def __init__(self, runtime=None, admission=None):
        """Functionality: Bind owned runtime and single-worker capacity.
        Inputs: optional explicit test dependencies. Outputs: callable worker socket handler.
        Logic: no GPU startup. Constraints: production runs one Uvicorn worker.
        """
        self.runtime = runtime if runtime is not None else InferenceRuntime()
        self.admission = admission if admission is not None else BoundedSemaphore(1)

    async def __call__(self, scope, receive, send):
        """Functionality: Admit, process and safely close one inference WebSocket.
        Inputs: ASGI scope/receive/send. Outputs: session, PRS1 frames, safe error/end and close.
        Logic: enforce local peer/Origin/no-query before admission; cancel async siblings and drain
        native future on stop/disconnect/error; only then send terminal and release capacity.
        Constraints: no public worker, retries, media logging or cancellation-as-GPU-completion.
        """
        first = await receive()
        headers = dict(scope.get("headers", []))
        client, server = scope.get("client", (None, 0)), scope.get("server", (None, 0))
        origin = headers.get(b"origin", b"").decode("ascii", "replace")
        if (
            first["type"] != "websocket.connect"
            or scope.get("query_string")
            or not is_loopback(client[0])
            or not is_loopback(server[0])
            or origin not in web_origins()
        ):
            return await send({"type": "websocket.close", "code": 1008})
        await send({"type": "websocket.accept"})
        if not self.admission.acquire(blocking=False):
            return await self.finish(send, None, IntegrationError("local_busy", "本地推理忙。"))
        session, tasks, error = WorkerSession(), [], None
        self.runtime.busy = True
        logger.info("Local worker admitted session=%s", session.id)
        try:
            contract = self.runtime.contract(session.id)
            await send({"type": "websocket.send", "text": json.dumps(contract)})
            tasks = [
                asyncio.create_task(self.read_frames(session, receive)),
                asyncio.create_task(self.process_frames(session, contract, send)),
                asyncio.create_task(self.lifetime(session)),
            ]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except asyncio.CancelledError:
            session.disconnected = True
            raise
        except Exception as caught:
            error = caught
        finally:
            cleanup = asyncio.create_task(self.cleanup(session, tasks, send, error))
            interrupted = False
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    session.disconnected = True
                    interrupted = True
            cleanup.result()
            if interrupted:
                raise asyncio.CancelledError

    async def cleanup(self, session, tasks, send, error):
        """Functionality: Complete cleanup in a task shielded from outer cancellation.
        Inputs: session, async siblings, send and prior error. Outputs: terminal/released capacity.
        Logic: cancel watchers, await native completion, then release in one finally block.
        Constraints: repeated cancellation cannot bypass gather/drain or free the slot early.
        """
        try:
            session.stopping.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            try:
                await self.drain(session)
            except Exception as caught:
                error = caught if error is None else error
            if not session.disconnected:
                await self.finish(send, session, error)
        except OSError:
            logger.info("Local worker peer unavailable session=%s", session.id)
        finally:
            self.runtime.busy = False
            self.admission.release()
            logger.info(
                "Local worker released session=%s received=%d delivered=%d error=%s",
                session.id,
                session.received,
                session.delivered,
                type(error).__name__ if error else None,
            )

    async def read_frames(self, session, receive):
        """Functionality: Watch stop/disconnect even during cold model initialization.
        Inputs: session/receive. Outputs: queued ordered frames or completion.
        Logic: PRF2/time validation and total outstanding cap; idle belongs to processor pulls.
        Constraints: no queue growth, timestamp fabrication, skips or GPU calls.
        """
        while True:
            message = await receive()
            if message["type"] == "websocket.disconnect":
                session.disconnected = True
                return
            if is_stop(message.get("text")):
                return
            timestamp, jpeg = unpack_request(
                message.get("bytes"), session.received, session.previous_timestamp
            )
            if session.received - session.delivered >= MAX_INFLIGHT:
                raise IntegrationError("local_capacity", "超过允许的在途帧数量。", 400)
            session.incoming.put_nowait((session.received, timestamp, jpeg))
            session.previous_timestamp = timestamp
            session.received += 1

    async def process_frames(self, session, contract, send):
        """Functionality: Infer and validate each frame in order using the single native thread.
        Inputs: bounded queue/contract/send. Outputs: hash-checked PRS1 packets.
        Logic: idle pull budget, first-frame load budget then steady budget; retain shielded future.
        Constraints: no alternate algorithm, lowered precision or dropping accepted active frames.
        """
        while True:
            try:
                index, timestamp, jpeg = await asyncio.wait_for(
                    session.incoming.get(), IDLE_SECONDS
                )
            except TimeoutError:
                return
            session.started.set()
            session.inflight = self.runtime.submit(jpeg, timestamp, session.id, index)
            try:
                metadata, rendered = await asyncio.wait_for(
                    asyncio.shield(session.inflight),
                    COLD_SECONDS if session.delivered == 0 else STEADY_SECONDS,
                )
            except TimeoutError:
                raise IntegrationError(
                    "local_inference_timeout", "本地推理响应超时，请检查推理进程。"
                ) from None
            session.inflight = None
            packet = pack_result(metadata, rendered)
            parsed, _ = unpack_result(packet, index)
            validate_result(parsed, contract, index, timestamp)
            await send({"type": "websocket.send", "bytes": packet})
            session.delivered += 1

    async def lifetime(self, session):
        """Functionality: Bound active session lifetime without counting admission-only waiting.
        Inputs: first-processing/stop events and frozen session policy. Outputs: policy completion.
        Logic: offline ends 55 seconds after first submission; assist waits for explicit cleanup.
        Constraints: cancellation wakes this wait; native work still drains before release.
        """
        await session.started.wait()
        if self.runtime.session_seconds is None:
            await session.stopping.wait()
        else:
            await asyncio.sleep(SESSION_SECONDS)

    async def drain(self, session):
        """Functionality: Await real native completion despite additional async cancellation.
        Inputs: retained native future. Outputs: None or native failure after it finishes.
        Logic: shield repeatedly on task cancellation; never release the GPU slot early.
        Constraints: a genuinely wedged native call requires operator process intervention.
        """
        if session.inflight is None:
            return
        while not session.inflight.done():
            try:
                await asyncio.shield(session.inflight)
            except asyncio.CancelledError:
                session.disconnected = True
        session.inflight.result()

    async def finish(self, send, session, error):
        """Functionality: Send a stable terminal message with safe counts or public errors.
        Inputs: ASGI send, optional session, caught failure. Outputs: text terminal and close.
        Logic: trusted algorithm/integration failures preserve fixed detail; unknown errors are
        generic.
        Constraints: never send raw dependency/GPU exception bodies or camera contents.
        """
        if error is not None:
            safe = (
                error
                if isinstance(error, IntegrationError | DetectionError)
                else IntegrationError(
                    "local_inference_failed", "本地推理失败，请检查推理进程日志。"
                )
            )
            logger.error(
                "Local worker failed session=%s code=%s type=%s",
                session.id if session else None,
                safe.code,
                type(error).__name__,
            )
            message = {
                "kind": "error",
                "code": safe.code,
                "detail": safe.detail,
                "request_id": session.id if session else None,
            }
        else:
            message = {
                "kind": "ended",
                "frames": session.delivered,
                "received": session.received,
                "delivered": session.delivered,
            }
        await send({"type": "websocket.send", "text": json.dumps(message)})
        await send({"type": "websocket.close", "code": 1011 if error else 1000})
