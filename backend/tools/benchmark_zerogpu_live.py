"""Responsibilities: Validate bounded live-session delivery locally or on the owned HF Space.
Implementation: One WS/one allocation; original four fixtures and unchanged metrics; explicit
first-result transport echo is included in cold latency under the original deadline;
qualified low-precision plan switches cross-runtime fixture comparison to diagnostics only;
remote dispatch is guarded by sufficient free seconds AND a free run, never paid hardware.
Explicit inference-only mode checks eight ordered fixtures, cache hits and CUDA event timing;
it skips paced WAN acceptance without changing existing video benchmark criteria.
Related Modules: live_transport/live_worker; live_benchmark; hf_services/hf_quota.
Declaration Index: empty_app; native_factory; local_server; start_remote; run; main; main.local.
Variable Index: ROOT — backend assets/private token root; logger — safe lifecycle/error records;
TIMEOUT_SECONDS — approved55s cold receive budget.
The local option exercises real GPU/WS/HTTP but excludes HF fork/runtime and external network.
"""

import argparse
import asyncio
import json
import logging
import math
import socket
import time
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import dotenv_values
from websockets.asyncio.client import connect

from core.hf_quota import LIVE_GPU_QUOTA_SECONDS, QUOTA_URL, quota_decision
from core.hf_services import HOST, exchange
from core.inference_plan import read_plan
from realtime.detector import ResidentDetector
from realtime.protocol import MAX_PACKET_BYTES, pack_request, unpack_result
from tools.compare_detections import compare
from tools.fixtures import fixture_bytes
from tools.live_benchmark import stream, validate
from zerogpu.jit_cache import cache_hits
from zerogpu.live_transport import (
    MAX_INFLIGHT,
    PREFIX,
    SESSION_SECONDS,
    WARMUP_BYTES,
    LiveTransport,
    receive_live_result,
)
from zerogpu.live_worker import AllocatedDetector, run_worker

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT_SECONDS = 55
logger = logging.getLogger(__name__)


async def empty_app(scope, receive, send):
    """Functionality: Reject non-live native validation routes without importing Gradio/spaces.
    Inputs: ASGI scope/receive/send; Outputs:404; Logic: one HTTP response.
    Constraints: local server disables lifespan; this is not a mock GPU or alternate detection.
    """
    await LiveTransport.respond(send, 404)


def native_factory(model, manifest, config=None):
    """Functionality: Load the verified native model on the live handoff worker for local testing.
    Inputs: ignored factory placeholders, original assets and optional validated eager config.
    Outputs: AllocatedDetector; Logic: load native verified model at explicit precision/layout,
    use actual live constructor. AOT/JIT are rejected before loading on this local-only path.
    Constraints: local-only substitute for ZeroGPU's root-loaded emulated model and fork lifecycle;
    actual JPEG/Graph work runs on RTX4060, not a fake detector or HF environment;
    target-compiled AOT packages require the separate real Kaggle/HF validation path.
    """
    if config is not None and (config.get("aoti") or config.get("compile")):
        raise ValueError("Native probe supports eager Graph; compilation needs actual Kaggle/HF")
    native = ResidentDetector(ROOT / "models/yolo26n.pt", ROOT / "models/yolo26n.json", config)
    return AllocatedDetector(native.model, native.manifest, config)


@asynccontextmanager
async def local_server(origin):
    """Functionality: Run an ephemeral loopback ASGI broker for explicitly selected native tests.
    Inputs: explicit allowed origin; Outputs: (broker, port); Logic: Uvicorn on loopback only.
    Constraints:5-frame WS queue, original binary boundaries, no compression/GPU at server startup;
    worker is initialized separately by run; shutdown is awaited, no public listener is created.
    """
    import uvicorn

    broker = LiveTransport(empty_app, origins=[origin])
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            broker,
            log_level="warning",
            lifespan="off",
            ws_max_queue=MAX_INFLIGHT,
            ws_max_size=MAX_PACKET_BYTES,
            ws_per_message_deflate=False,
            access_log=False,
        )
    )
    running = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if running.done():
                    running.result()
                await asyncio.sleep(0.01)
        yield broker, port
    finally:
        server.should_exit = True
        await asyncio.wait_for(running, 10)
        listener.close()


def start_remote(capability, token, report):
    """Functionality: Start exactly one authenticated Gradio GPU session after a free-budget gate.
    Inputs: admitted WS capability, private HF token and report; Outputs: final Gradio summary.
    Logic: one authenticated quota GET; require unchanged60s task's90s reservation and a free run,
    then fixed Space POST/SSE with55s cold waiting plus55s session completion budget.
    Constraints: failed/unknown/exhausted quota aborts before GPU request; no credits/hardware,
    anonymous fallback/retry; token only in explicit private authorization arguments.
    Quota snapshot cannot reserve resources against other clients using this account concurrently.
    The110s completion budget separates accepted startup waiting from allocation processing;
    first WS receive remains55s, steady receives10s and actual frame latency target500ms.
    """
    request = urllib.request.Request(QUOTA_URL, headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(request, timeout=20) as response:
        decision = quota_decision(json.load(response), LIVE_GPU_QUOTA_SECONDS)
    report["quota_gate"] = decision
    if not decision["allowed"]:
        raise RuntimeError("Free ZeroGPU budget gate denied: " + decision["reason"])
    deadline = time.monotonic() + TIMEOUT_SECONDS + SESSION_SECONDS
    body = json.dumps({"data": [capability]}).encode()
    event = exchange("POST", "/gradio_api/call/detect_live", body, deadline, token=token)
    result = exchange(
        "GET",
        "/gradio_api/call/detect_live/" + event["event_id"],
        None,
        deadline,
        stream=True,
        token=token,
    )
    if not isinstance(result, list) or len(result) != 1 or not isinstance(result[0], dict):
        raise ValueError("Invalid live GPU completion summary")
    return result[0]


async def run(args, report, broker=None, port=None):
    """Functionality: Verify cold outputs, then12/24FPS-style paced inputs in one live allocation.
    Inputs: CLI/rates/seconds/optional plan/inference-only/report; optional local broker/port.
    Outputs: complete report with worker release/quality/true received latency evidence.
    Logic: validate explicit plan before dispatch; WS hello, one allocation, four references then
    continuous benchmark; acknowledge the negotiated startup echo within the original cold budget.
    FP32 retains original detection-count/match acceptance; approved
    low precision records fixture differences and uses its three full COCO runs for quality.
    Constraints:55s cold first receive allowed, unchanged10s later receives and10FPS/500ms target;
    cold phase is separate; capture/encode/display excluded; no reconnect or frame dropping.
    Final GPU summary must acknowledge every delivered frame before completion is recorded.
    Inference-only requires the remote cached-JIT plan; eight frames verify cache/Graph/timing
    and repeated same-allocation output hashes, without a WAN performance admission threshold.
    """
    token = ""
    if not args.local_native:
        token = dotenv_values(ROOT / ".env.hf").get("HF_TOKEN", "")
        if not token.startswith("hf_") or any(char.isspace() for char in token):
            raise ValueError("Configure valid HF_TOKEN in private .env.hf")
    url = f"ws://127.0.0.1:{port}{PREFIX}/ws" if args.local_native else f"wss://{HOST}{PREFIX}/ws"
    manifest = json.loads((ROOT / "models/yolo26n.json").read_text(encoding="utf-8"))
    plan = read_plan(args.plan, manifest) if args.plan is not None else None
    if plan is not None:
        report["inference_plan"] = plan
        report["conditions"] = (
            f"original model/{plan['precision']}/640/0.25/JPEG80; target>=10FPS/max500ms"
        )
    if args.inference_only:
        if args.local_native or plan is None or not plan["config"].get("compile"):
            raise ValueError("Inference-only verification requires the remote cached-JIT plan")
        report["conditions"] = "unchanged T4 config; cache/Graph/output verification"
        report["scope"] = "HF short inference verification; no WAN speed ranking or new COCO AP"
        report["inference_checks"] = []
    paths = sorted((ROOT / "models/experiments/coco8/images/val").glob("*.jpg"))
    fixtures = [(path.name, fixture_bytes(path)[0]) for path in paths]
    if len(fixtures) != 4:
        raise ValueError("Expected original four public fixtures")
    baseline = json.loads((ROOT / "benchmarks/2026-10-02/baseline.json").read_text())["evaluation"]
    worker = None
    async with connect(
        url,
        origin=args.origin,
        compression=None,
        proxy=None,
        additional_headers={"Authorization": "Bearer " + token} if token else None,
        max_size=MAX_PACKET_BYTES,
        max_queue=MAX_INFLIGHT,
        ping_interval=None,
        open_timeout=10,
    ) as peer:
        hello = json.loads(await asyncio.wait_for(peer.recv(), 10))
        if (
            hello.get("kind") != "session"
            or hello.get("max_inflight") != MAX_INFLIGHT
            or hello.get("session_seconds") != SESSION_SECONDS
            or hello.get("warmup_bytes") != WARMUP_BYTES
        ):
            raise ValueError("Live admission contract mismatch")
        if args.local_native:
            worker = asyncio.create_task(
                asyncio.to_thread(
                    run_worker, broker.session.worker_capability, None, None, port, native_factory,
                    None if plan is None else plan["config"]
                )
            )
        else:
            worker = asyncio.create_task(
                asyncio.to_thread(start_remote, hello["capability"], token, report)
            )
        try:
            references = {}
            checks = fixtures * 2 if args.inference_only else fixtures
            for index, (name, jpeg) in enumerate(checks):
                started = time.perf_counter()
                await peer.send(pack_request(index, jpeg))
                receiver = asyncio.create_task(
                    receive_live_result(peer, index, 55 if index == 0 else 10))
                try:
                    done, _ = await asyncio.wait(
                        (receiver, worker),
                        timeout=55 if index == 0 else 10,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if worker in done:
                        worker.result()
                        raise RuntimeError("GPU session ended before startup frames completed")
                    if receiver not in done:
                        raise TimeoutError("Live cold/startup receive deadline")
                    metadata, rendered = unpack_result(receiver.result(), index)
                finally:
                    receiver.cancel()
                    await asyncio.gather(receiver, return_exceptions=True)
                validate(metadata, rendered, manifest, plan)
                if args.inference_only:
                    cache = metadata["execution"]["jit_cache"]
                    cache_hits(cache["compiler_state"])
                    forward = metadata["forward_event_ms"]
                    if (not cache["new_backend_codegen_forbidden"] or
                            not metadata["execution"]["graph_captured"] or
                            not isinstance(forward, (int, float)) or
                            not math.isfinite(forward) or forward <= 0):
                        raise ValueError("Cached inference/timing evidence is invalid")
                    if index >= 4 and (metadata["graph_setup_ms"] != 0 or any(
                            metadata[key] != references[name][key] for key in
                            ("raw_sha256", "jpeg_sha256"))):
                        raise ValueError("Repeated inference recaptured Graph or changed output")
                    report["inference_checks"].append({"fixture": name, **metadata})
                references[name] = {
                    **metadata,
                    "round_trip_ms": (time.perf_counter() - started) * 1000,
                }
            report["evaluation"] = references
            report["comparison"] = compare(baseline, references)
            fixture_mismatch = report["comparison"]["missing"] or any(
                len(references[name]["boxes"]) != len(baseline[name]["boxes"])
                for name in references
            )
            report["fixture_acceptance"] = (
                "diagnostic comparison; complete COCO gate in explicit plan"
                if plan is not None and plan["precision"] != "FP32"
                else "original FP32 count/match equality"
            )
            if fixture_mismatch and (plan is None or plan["precision"] == "FP32"):
                raise ValueError("Live outputs differ from original reference detections")
            next_index = len(checks)
            for fps in args.fps:
                next_index = await stream(
                    peer, fixtures, references, manifest, next_index, fps, args.seconds, report,
                    plan=plan,
                )
            await peer.send('{"kind":"stop"}')
        finally:
            await peer.close()
            report["worker_summary"] = await asyncio.wait_for(worker, 10)
    expected = len(checks) + sum(run["received_frames"] for run in report["runs"])
    if report["worker_summary"]["frames"] != expected:
        raise ValueError("GPU worker did not acknowledge every delivered frame")
    report["complete"] = True


def main():
    """Functionality: Run local-native or free-guarded HF live validation and save evidence.
    Inputs: CLI origin/rates/duration/output/local-native/qualified plan/inference-only.
    Outputs: JSON, nonzero on any failure.
    Logic: total paced run <=40s leaves cold setup/control margin within unchanged60s GPU ceiling.
    Constraints: required explicit rates/duration, full-delivery/numerical integrity/latency checks;
    report distinguishes local architecture proof from actual ZeroGPU/WAN acceptance.
    Explicit inference-only skips pacing and WAN speed admission, retaining all identity/cache/
    output/delivery checks; it requires a remote cached-JIT plan and no fps/duration arguments.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-native", action="store_true")
    parser.add_argument("--origin", required=True)
    parser.add_argument("--fps", nargs="+", type=float)
    parser.add_argument("--seconds", type=float)
    parser.add_argument("--inference-only", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plan", type=Path)
    args = parser.parse_args()
    if args.inference_only:
        if args.fps is not None or args.seconds is not None:
            parser.error("Inference-only uses eight fixtures; omit fps/seconds")
        args.fps, args.seconds = [], 0
    elif (args.fps is None or args.seconds is None or
          not 1 <= args.seconds <= 40 / len(args.fps) or
          any(not 1 <= rate <= 60 for rate in args.fps)):
        parser.error("Use1–60FPS and total paced duration1–40s within original default60s GPU task")
    report = {
        "complete": False,
        "local_native": args.local_native,
        "runs": [],
        "conditions": "original model/FP32/640/0.25/JPEG80; target>=10FPS/max500ms",
        "scope": "native WS+loopback HTTP bridge" if args.local_native else "HF ZeroGPU+WAN",
        "excludes": "physical capture/JPEG encoding/display",
        "paid_hardware_requested": False,
        "max_inflight": MAX_INFLIGHT,
    }
    report["budgets"] = {
        "cold_receive_seconds": TIMEOUT_SECONDS,
        "gpu_session_seconds": SESSION_SECONDS,
        "gradio_completion_seconds": TIMEOUT_SECONDS + SESSION_SECONDS,
        "steady_receive_seconds": 10,
        "latency_target_ms": 500,
    }
    logging.basicConfig(level=logging.INFO)
    try:
        if args.local_native:
            # Functionality: Bind native broker and execute one complete validation context.
            # Inputs: enclosing args/report; Outputs: awaited result/server shutdown.
            # Logic: async server context; Constraints: loopback only, no HF calls.
            async def local():
                async with local_server(args.origin) as (broker, port):
                    await run(args, report, broker, port)

            asyncio.run(local())
        else:
            asyncio.run(run(args, report))
    except Exception as error:
        report["failure_type"] = type(error).__name__
        logger.exception("Live validation failed; inspect quota/Space/partial report")
    finally:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if not report["complete"] or (not args.inference_only and
            not all(run["meets_local_target"] for run in report["runs"])):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
