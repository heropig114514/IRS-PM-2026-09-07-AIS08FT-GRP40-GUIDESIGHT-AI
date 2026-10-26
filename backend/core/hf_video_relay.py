"""Responsibilities: Connect a same-origin phone WebSocket to one free-quota HF video session.
Implementation: One provider slot; ordered binary relay with twelve credits; one private Gradio
allocation after the first frame. Disconnect closes HF before awaiting the allocation summary.
The first-result receive echoes one negotiated512KiB transport control inside the cold deadline.
Related Modules: config.asgi; hf_services/hf_quota/inference_plan; frontend live-detection-client;
HF zerogpu.live_transport/live_worker. No GPU/runtime is initialized on the PRS server.
Declaration Index: read_token; read_free_quota; model_contract;
allocate_live; RelaySession;
RelaySession.__init__; HFLiveRelay; HFLiveRelay.__init__; HFLiveRelay.__call__;
HFLiveRelay.read_browser; HFLiveRelay.run_provider; HFLiveRelay.write_provider;
HFLiveRelay.write_browser.
Variable Index: MAX_INFLIGHT — shared twelve-frame transport bound;
SESSION_SECONDS —55s allocated bound;
COLD_SECONDS/STEADY_SECONDS —55s first response/10s later failure budgets;
UPSTREAM_URL/UPSTREAM_ORIGIN — fixed owned HF entry/canonical PRS origin;
logger — safe IDs, phase/counters/timing; relay — single-worker shared relay instance.
slot — single nonqueued video admission; HTTP page/assets belong to core.camera.
prs_upstream_ms — same-PRS-clock submission to complete HF validation, including HF queue/GPU/I/O.
input_received_at/prs_admission_queue_ms — bounded ingress timestamps and PRS pre-forward queue;
prs_result_validation_ms — complete packet validation after recv, not the network read duration.
Quota snapshots do not reserve resources against other applications.
There is no anonymous fallback, retry or hardware change.
"""

import asyncio
import json
import logging
import math
import os
import re
import time
import urllib.request
import uuid
from threading import BoundedSemaphore
from urllib.parse import urlsplit

from django.conf import settings
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedOK

from realtime.protocol import MAX_PACKET_BYTES, pack_result, unpack_request, unpack_result
from zerogpu.live_transport import MAX_INFLIGHT, WARMUP_BYTES, receive_live_result

from .detector import DetectionError
from .hf_quota import LIVE_GPU_QUOTA_SECONDS, QUOTA_URL, quota_decision
from .hf_services import HOST, exchange
from .inference_plan import read_plan

SESSION_SECONDS = 55
COLD_SECONDS = 55
STEADY_SECONDS = 10
UPSTREAM_URL = f"wss://{HOST}/prs_live/ws"
UPSTREAM_ORIGIN = "https://47.239.50.129"
logger = logging.getLogger(__name__)
slot = BoundedSemaphore(1)


def read_token():
    """Functionality: Read the server's required private HF identity.
    Inputs: HF_INFERENCE_TOKEN environment; Outputs: validated token or safe503.
    Logic: reject absent/invalid credentials before any external call.
    Constraints: never expose/log credential, substitute anonymous identity or read a browser token.
    """
    token = os.environ.get("HF_INFERENCE_TOKEN", "")
    if not token.startswith("hf_") or len(token) > 4096 or any(c.isspace() for c in token):
        raise DetectionError(
            "hf_not_configured",
            "A valid server-side HF token is required to start a video session.",
            503,
        )
    return token


def read_free_quota(token):
    """Functionality: Check the unchanged60s task's90s reservation AND a free run.
    Inputs: private token; Outputs: strict quota decision. Logic: one bounded verified-TLS GET.
    Constraints:20s timeout,64KiB body, no dispatch/retry/credit extension or account switch.
    """
    request = urllib.request.Request(QUOTA_URL, headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read(65537)
    if len(raw) > 65536:
        raise ValueError("Quota response exceeds boundary")
    return quota_decision(json.loads(raw), LIVE_GPU_QUOTA_SECONDS)


def model_contract(plan_path=None):
    """Functionality: Publish fixed model/settings identity without credentials or GPU work.
    Inputs: original manifest from settings.BASE_DIR; optional explicit local plan_path for an
    isolated native control, otherwise the required production models/inference-plan.json.
    Outputs: public session dictionary with measured precision/backend and quality provenance.
    Logic: validate original80-class manifest/SHA and three full COCO processes;
    budgets match deployed HF broker. API buffers remain FP32 regardless of compute precision.
    Constraints: readiness means admission only, not GPU availability or real-time acceptance.
    """
    manifest = json.loads((settings.BASE_DIR / "models/yolo26n.json").read_text(encoding="utf-8"))
    if (
        manifest.get("model") != "yolo26n"
        or len(manifest.get("names", [])) != 80
        or not re.fullmatch(r"[0-9a-f]{64}", manifest.get("source_sha256", ""))
    ):
        raise ValueError("Invalid live model manifest")
    plan = read_plan(settings.BASE_DIR / "models/inference-plan.json"
                     if plan_path is None else plan_path, manifest)
    quality = plan["quality"]
    return {
        "kind": "session",
        "model": "yolo26n",
        "precision": plan["precision"],
        "backend": plan["backend"],
        "output_precision": "FP32",
        "validation": {"images": quality["images"],
                       "replicates": len(quality["replicates"]),
                       "baseline_ap": quality["baseline_ap"],
                       "worst_ap": min(row["ap"] for row in quality["replicates"]),
                       "maximum_ap_drop": quality["maximum_ap_drop"],
                       "summary_sha256": plan["provenance"]["summary_sha256"],
                       "notebook_version": plan["provenance"]["version"]},
        "classes": 80,
        "source_sha256": manifest["source_sha256"],
        "names": manifest["names"],
        "confidence_threshold": 0.25,
        "max_edge": 640,
        "jpeg_quality": 0.8,
        "max_inflight": MAX_INFLIGHT,
        "session_seconds": SESSION_SECONDS,
        "cold_seconds": COLD_SECONDS,
        "steady_seconds": STEADY_SECONDS,
    }


def allocate_live(capability, token):
    """Functionality: Dispatch exactly one already-gated live allocation and wait for its summary.
    Inputs: HF capability, private token matching immutable server environment; Outputs: summary.
    Logic: fixed-host POST/SSE under55s cold+55s completion, with the validated private token.
    Constraints: no media through Gradio; failures propagate; no retry/second allocation.
    """
    if token != read_token():
        raise ValueError("Server credential changed during live allocation")
    deadline = time.monotonic() + COLD_SECONDS + SESSION_SECONDS
    event = exchange(
        "POST",
        "/gradio_api/call/detect_live",
        json.dumps({"data": [capability]}).encode(),
        deadline,
        token=token,
    )
    event_id = event.get("event_id", "")
    if not isinstance(event_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", event_id):
        raise ValueError("Invalid live event identifier")
    result = exchange(
        "GET", "/gradio_api/call/detect_live/" + event_id, None, deadline, stream=True, token=token
    )
    if not isinstance(result, list) or len(result) != 1 or not isinstance(result[0], dict):
        raise ValueError("Invalid live completion summary")
    summary = result[0]
    if (
        type(summary.get("frames")) is not int
        or summary["frames"] < 0
        or type(summary.get("gpu_session_ms")) not in (int, float)
        or not math.isfinite(summary["gpu_session_ms"])
        or summary["gpu_session_ms"] < 0
    ):
        raise ValueError("Invalid live completion counters")
    return summary


class RelaySession:
    """Functionality: Keep bounded state for one admitted same-origin session.
    Logic: queue/credits cap browser frames; counts distinguish received/forwarded/delivered.
    Constraints: no persistent media, keys in diagnostics or CPU-side inference.
    """

    def __init__(self):
        """Functionality: Initialize empty queues, diagnostics and an unstarted allocation.
        Inputs: monotonic clock/UUID; Outputs: in-memory session.
        Logic: allocation remains None until one actual frame is forwarded; disconnected controls
        safe ASGI closing; phase/ID/counters identify errors; summary is retained for verification.
        upstream_started/input_received_at retain twelve same-clock times at most; no clock sync.
        Constraints: twelve queued/working frames in total, no infinite event history.
        """
        self.id = uuid.uuid4().hex[:12]
        self.incoming = asyncio.Queue(MAX_INFLIGHT)
        self.credits = asyncio.Semaphore(MAX_INFLIGHT)
        self.received = self.forwarded = self.delivered = 0
        self.disconnected = False
        self.allocation = self.summary = None
        self.upstream_started = {}
        self.input_received_at = {}
        self.phase = "preflight"
        self.send_max_ms = self.send_total_ms = 0.0


class HFLiveRelay:
    """Functionality: Relay bounded phone video to the existing HF live API.
    Logic: shared nonqueued slot, free-budget preflight, independent reader/transmit/receive.
    Constraints: exact origin+host/no query, fixed HF destination, one worker; injected dependencies
    are explicit test seams, never production fallback providers.
    """

    def __init__(
        self,
        opener=connect,
        quota_reader=read_free_quota,
        allocator=allocate_live,
        token_reader=read_token,
        contract_reader=model_contract,
        admission=slot,
    ):
        """Functionality: Bind production dependencies or explicit isolated test dependencies.
        Inputs: opener/quota_reader/allocator/token_reader/contract_reader/admission.
        Outputs: stateless callable; Logic: mutable queues exist per session, admission is shared.
        Constraints: constructor performs no I/O or inference; test substitutions must be explicit.
        """
        self.opener, self.quota_reader, self.allocator = opener, quota_reader, allocator
        self.token_reader, self.contract_reader = token_reader, contract_reader
        self.admission = admission

    async def __call__(self, scope, receive, send):
        """Functionality: Admit and clean up one phone WS, including its provider allocation.
        Inputs: ASGI scope/receive/send and injected dependencies; Outputs: session/results/errors.
        Logic: reader watches disconnect even during preflight; cancel relay siblings, close HF,
        then await the uncancelled blocking control task before releasing the shared provider slot.
        Constraints: errors contain safe codes/ID only; GPU already running may finish its current
        frame; cancelling a Python future alone does not cancel the external GPU task.
        """
        first = await receive()
        if first["type"] != "websocket.connect":
            return
        headers = dict(scope.get("headers", []))
        origin = headers.get(b"origin", b"").decode("ascii", "replace")
        host = headers.get(b"host", b"").decode("ascii", "replace")
        if (
            scope.get("query_string")
            or origin not in settings.CAMERA_ALLOWED_ORIGINS
            or host != urlsplit(origin).netloc
        ):
            logger.warning("HF live rejected origin_or_host_or_query")
            return await send({"type": "websocket.close", "code": 1008})
        await send({"type": "websocket.accept"})
        if not self.admission.acquire(blocking=False):
            await send(
                {
                    "type": "websocket.send",
                    "text": json.dumps(
                        {"kind": "error", "code": "hf_busy", "detail": "ZeroGPU is busy."}
                    ),
                }
            )
            return await send({"type": "websocket.close", "code": 1013})
        session, tasks, error = RelaySession(), [], None
        logger.info("HF live admitted id=%s GPU_not_requested=true", session.id)
        try:
            token = self.token_reader()
            tasks = [
                asyncio.create_task(self.read_browser(session, receive)),
                asyncio.create_task(self.run_provider(session, send, token)),
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
            try:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                if session.allocation is not None:
                    try:
                        session.summary = await asyncio.shield(session.allocation)
                        if session.summary["frames"] < session.delivered:
                            raise ValueError("GPU summary acknowledges fewer than delivered frames")
                    except Exception as caught:
                        error = caught if error is None else error
                if error is not None:
                    logger.error(
                        "HF live failed id=%s phase=%s received=%d forwarded=%d "
                        "delivered=%d type=%s",
                        session.id,
                        session.phase,
                        session.received,
                        session.forwarded,
                        session.delivered,
                        type(error).__name__,
                    )
                if not session.disconnected:
                    if error is not None:
                        code = error.code if isinstance(error, DetectionError) else "hf_live_failed"
                        detail = (
                            error.detail
                            if isinstance(error, DetectionError)
                            else (
                                "The video session failed; check server logs before starting again."
                            )
                        )
                        await send(
                            {
                                "type": "websocket.send",
                                "text": json.dumps(
                                    {
                                        "kind": "error",
                                        "code": code,
                                        "detail": detail,
                                        "request_id": session.id,
                                    }
                                ),
                            }
                        )
                    else:
                        await send(
                            {
                                "type": "websocket.send",
                                "text": json.dumps(
                                    {
                                        "kind": "ended",
                                        "received": session.received,
                                        "delivered": session.delivered,
                                    }
                                ),
                            }
                        )
                    await send({"type": "websocket.close", "code": 1011 if error else 1000})
            except OSError:
                logger.info("HF live peer unavailable id=%s during_close=true", session.id)
            finally:
                self.admission.release()
                logger.info(
                    "HF live released id=%s received=%d forwarded=%d delivered=%d "
                    "send_max_ms=%.1f worker_frames=%s",
                    session.id,
                    session.received,
                    session.forwarded,
                    session.delivered,
                    session.send_max_ms,
                    session.summary["frames"] if session.summary else None,
                )

    async def read_browser(self, session, receive):
        """Functionality: Validate ordered input while continuously watching stop/disconnect.
        Inputs: bounded session and ASGI receive; Outputs: queued packets/counters or completion.
        Logic: reject a thirteenth outstanding packet without blocking lifecycle reads;
        require exact stop text and retain one ingress time per accepted sequence for attribution.
        Constraints: no skipping/queue growth; JPEG codec stays on HF; no GPU start here.
        """
        while True:
            message = await receive()
            if message["type"] == "websocket.disconnect":
                session.disconnected = True
                return
            if message.get("text") == '{"kind":"stop"}':
                return
            packet = message.get("bytes")
            received_at = time.perf_counter()
            jpeg = unpack_request(packet, session.received)
            if not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9"):
                raise ValueError("Invalid request JPEG markers")
            if session.credits.locked():
                raise ValueError(f"Browser exceeded {MAX_INFLIGHT} outstanding frames")
            await session.credits.acquire()
            session.input_received_at[session.received] = received_at
            session.incoming.put_nowait(packet)
            session.received += 1

    async def run_provider(self, session, send, token):
        """Functionality: Preflight free budget, connect HF and start once on first input.
        Inputs: session/send/private token; Outputs: public hello and independent forwarders.
        Logic: strict quota gate before allocation; HF capabilities never cross to the phone;
        allocation task is deliberately excluded from cancellation of socket forwarding tasks.
        Normal allocation completion stops input forwarding but drains results until HF closes;
        a faster SSE summary must not discard an already-sent final JPEG.
        Constraints: verified TLS, no compression/proxy/reconnect; no frames through Gradio;
        close the upstream context on every branch so the allocated worker sees stop promptly.
        """
        session.phase = "quota"
        decision = await asyncio.to_thread(self.quota_reader, token)
        logger.info(
            "HF live quota id=%s allowed=%s reason=%s required_seconds=%s "
            "free_seconds=%s free_runs=%s",
            session.id,
            decision["allowed"],
            decision.get("reason"),
            decision.get("required_gpu_seconds"),
            decision.get("free_gpu_seconds"),
            decision.get("free_runs_remaining"),
        )
        if not decision["allowed"]:
            reason = decision.get("reason")
            detail = "ZeroGPU free quota is insufficient; wait for it to reset."
            if reason == "insufficient_free_seconds":
                detail = (
                    f"ZeroGPU free quota is insufficient: this request needs "
                    f"{LIVE_GPU_QUOTA_SECONDS:g} seconds, "
                    f"with {decision['free_gpu_seconds']:.2f} seconds remaining. "
                    "Wait for quota to reset."
                )
            elif reason == "free_run_limit_exhausted":
                detail = "Today's free ZeroGPU calls are exhausted; wait for quota to reset."
            elif reason == "unknown_free_quota":
                detail = "Could not confirm ZeroGPU free quota; check the server configuration."
            raise DetectionError("hf_quota_exceeded", detail, 503)
        contract = self.contract_reader()
        session.phase = "upstream_connect"
        async with self.opener(
            UPSTREAM_URL,
            origin=UPSTREAM_ORIGIN,
            additional_headers={"Authorization": "Bearer " + token},
            compression=None,
            proxy=None,
            open_timeout=10,
            close_timeout=5,
            max_size=MAX_PACKET_BYTES,
            max_queue=MAX_INFLIGHT,
            ping_interval=None,
        ) as peer:
            hello = json.loads(await asyncio.wait_for(peer.recv(), 10))
            if (
                hello.get("kind") != "session"
                or hello.get("max_inflight") != MAX_INFLIGHT
                or hello.get("session_seconds") != SESSION_SECONDS
                or hello.get("warmup_bytes") != WARMUP_BYTES
                or not re.fullmatch(r"[0-9a-f]{64}", hello.get("capability", ""))
            ):
                raise ValueError("HF live admission contract mismatch")
            await send({"type": "websocket.send", "text": json.dumps(contract)})
            session.phase = "first_input"
            first = await asyncio.wait_for(session.incoming.get(), 5)
            session.upstream_started[session.forwarded] = time.perf_counter()
            await peer.send(first)
            session.forwarded += 1
            session.allocation = asyncio.create_task(
                asyncio.to_thread(self.allocator, hello["capability"], token)
            )
            logger.info(
                "HF live allocation requested id=%s max_seconds=60 session_seconds=55", session.id
            )
            session.phase = "streaming"
            forwarding = [
                asyncio.create_task(self.write_provider(session, peer)),
                asyncio.create_task(self.write_browser(session, peer, send)),
            ]
            try:
                done, _ = await asyncio.wait(
                    (*forwarding, session.allocation), return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    task.result()
                if session.allocation in done or forwarding[0] in done:
                    forwarding[0].cancel()
                    await forwarding[1]
            finally:
                for task in forwarding:
                    task.cancel()
                await asyncio.gather(*forwarding, return_exceptions=True)

    async def write_provider(self, session, peer):
        """Functionality: Forward admitted original binary requests in order.
        Inputs: incoming queue/upstream WS; Outputs: packets and forwarded count.
        Logic: one awaited writer propagates network pressure; Constraints: reader remains separate,
        no copied JPEG list, retries or re-encoding; normal HF close ends admission, abnormal close
        still raises. Final response draining is owned by run_provider.
        """
        while True:
            packet = await session.incoming.get()
            session.upstream_started[session.forwarded] = time.perf_counter()
            try:
                await peer.send(packet)
            except ConnectionClosedOK:
                return
            session.forwarded += 1

    async def write_browser(self, session, peer, send):
        """Functionality: Validate ordered HF results and add same-clock upstream timing.
        Inputs: upstream WS/send/submission map; Outputs: unchanged JPEG with extended metadata.
        Logic: first receive55s, later10s; order/hash/markers; credit released after awaited send.
        Independently observe PRS pre-forward queue and result validation with this PRS clock;
        preserve all HF parent/worker duration metadata and the original JPEG fingerprints.
        Mandatory first-result warmup is handled inside the same55s absolute receive budget;
        its control never reaches the browser, increments image counts or starts another GPU task.
        Constraints: ASGI send is not browser ACK; phone holds its own credit through display;
        no success substitution on failure, no high-frequency media logs or JPEG transformation.
        """
        while True:
            receive_started = time.perf_counter()
            try:
                packet = await receive_live_result(
                    peer, session.delivered,
                    COLD_SECONDS if not session.delivered else STEADY_SECONDS
                )
            except ConnectionClosedOK:
                return
            validation_started = time.perf_counter()
            metadata, jpeg = unpack_result(packet, session.delivered)
            validated_at = time.perf_counter()
            receive_ms = (time.perf_counter() - receive_started) * 1000
            upstream_started = session.upstream_started.pop(session.delivered, None)
            if upstream_started is None:
                raise ValueError("Unsolicited HF result")
            accepted_at = session.input_received_at.pop(session.delivered)
            metadata["prs_upstream_ms"] = (validated_at - upstream_started) * 1000
            metadata["prs_admission_queue_ms"] = (upstream_started - accepted_at) * 1000
            metadata["prs_result_validation_ms"] = (validated_at - validation_started) * 1000
            packet = pack_result(metadata, jpeg)
            started = time.perf_counter()
            await send({"type": "websocket.send", "bytes": packet})
            wait_ms = (time.perf_counter() - started) * 1000
            session.send_total_ms += wait_ms
            session.send_max_ms = max(session.send_max_ms, wait_ms)
            session.delivered += 1
            session.credits.release()
            if session.delivered == 1 or session.delivered % 60 == 0 or wait_ms > 500:
                logger.info(
                    "HF live delivery id=%s frames=%d receive_ms=%.1f send_ms=%.1f "
                    "send_max_ms=%.1f server_ms=%s graph_setup_ms=%s upstream_ms=%.1f "
                    "prs_queue_ms=%.2f hf_queue_ms=%s hf_local_cycle_ms=%s",
                    session.id,
                    session.delivered,
                    receive_ms,
                    wait_ms,
                    session.send_max_ms,
                    metadata.get("server_ms"),
                    metadata.get("graph_setup_ms"),
                    metadata["prs_upstream_ms"],
                    metadata["prs_admission_queue_ms"],
                    metadata.get("hf_parent_queue_ms"),
                    metadata.get("hf_local_cycle_ms"),
                )


relay = HFLiveRelay()
