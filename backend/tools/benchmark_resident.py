"""Responsibilities: Measure the real local resident ASGI/WebSocket/GPU path across reconnects.
Implementation: One lifespan/model; explicit rates; bounded credits; retain all frame records.
Related Modules: config.asgi; realtime.services/resident_asgi/protocol; tools.fixtures;
tools.benchmark_inference.distribution.
Declaration Index: stream; stream.transmit; stream.collect; run; main.
Variable Index: ROOT — backend assets; no remote calls, hidden retries or candidate Graph switch.
"""

import argparse
import asyncio
import hashlib
import json
import os
import platform
import socket
import time
from pathlib import Path

import uvicorn
from websockets.asyncio.client import connect

from realtime.protocol import MAX_PACKET_BYTES, pack_request, unpack_result
from tools.benchmark_inference import distribution
from tools.fixtures import fixture_bytes

ROOT = Path(__file__).resolve().parents[1]


async def stream(url, origin, raw, fps, seconds, records, report):
    """Functionality: Time one cold/reconnected stream without dropping slow frames.
    Inputs: local URL/origin, fixed JPEG, rate/duration and mutable evidence containers.
    Outputs: complete session counters and first/steady summaries.
    Logic: first frame alone; then scheduled sends with twelve credits and independent receive.
    Constraints: static-image probe, no camera/accuracy claim; client validation is inside latency.
    """
    count = fps * seconds
    sent_at = {}
    credits = asyncio.Semaphore(12)
    report.update(requested=count + 1, sent=0, delivered=0, fps_target=fps)
    async with connect(url, origin=origin, compression=None, proxy=None, max_size=MAX_PACKET_BYTES,
                       max_queue=12, open_timeout=15, close_timeout=5) as ws:
        contract = json.loads(await asyncio.wait_for(ws.recv(), 15))
        if contract.get("protocol") != "resident-v1" or contract["max_inflight"] != 12:
            raise ValueError("Unexpected resident contract")
        report["contract"] = contract
        first_started = time.perf_counter()
        await ws.send(pack_request(0, raw))
        report["sent"] += 1
        metadata, _ = unpack_result(await asyncio.wait_for(ws.recv(), 60), 0)
        report["delivered"] += 1
        records.append({**metadata, "client_ms": (time.perf_counter() - first_started) * 1000})
        started = time.perf_counter()

        async def transmit():
            """Functionality: Schedule every steady frame with bounded in-flight ownership.
            Inputs: outer schedule/credits. Outputs: sent counters and exact per-frame timestamps.
            Logic: await capacity, then send overdue frames without dropping them.
            Constraints: pacing lateness is recorded, not hidden by reduced requested counts.
            """
            for index in range(1, count + 1):
                target = started + (index - 1) / fps
                await asyncio.sleep(max(0, target - time.perf_counter()))
                await credits.acquire()
                sent_at[index] = (time.perf_counter(), target)
                await ws.send(pack_request(index, raw))
                report["sent"] += 1
            await ws.send('{"kind":"stop"}')

        async def collect():
            """Functionality: Validate and account for every steady response and final drain.
            Inputs: expected count and sent timestamps. Outputs: metadata/latency records.
            Logic: verify order/JPEG hashes before returning credit; require ended counters match.
            Constraints: any missing/error/duplicate frame fails the run; no retries.
            """
            for index in range(1, count + 1):
                metadata, _ = unpack_result(await asyncio.wait_for(ws.recv(), 60), index)
                finished = time.perf_counter()
                sent, target = sent_at.pop(index)
                records.append({**metadata, "client_ms": (finished - sent) * 1000,
                                "pacing_lateness_ms": max(0, sent - target) * 1000})
                report["delivered"] += 1
                credits.release()
            ended = json.loads(await asyncio.wait_for(ws.recv(), 15))
            if ended.get("kind") != "ended" or ended["received"] != ended["delivered"]:
                raise ValueError("Stream did not drain normally")
            if ended["delivered"] != count + 1:
                raise ValueError("Delivery count mismatch")
            report["ended"] = ended

        tasks = [asyncio.create_task(transmit()), asyncio.create_task(collect())]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        elapsed = time.perf_counter() - started
        report.update(first_frame=records[0], elapsed_s=elapsed, delivery_fps=count / elapsed,
                      latency=distribution([r["client_ms"] for r in records[1:]]),
                      queue=distribution([r["resident_queue_ms"] for r in records[1:]]),
                      server=distribution([r["server_ms"] for r in records[1:]]),
                      pacing=distribution([r["pacing_lateness_ms"] for r in records[1:]]))
        report["meets_existing_transport_gate"] = (
            report["delivery_fps"] >= 10 and report["latency"]["max_ms"] <= 500)


async def run(args):
    """Functionality: Start the real service once, run reconnect probes and save complete evidence.
    Inputs: workload, explicit rates/duration/new output directory and optional second image.
    Outputs: report.json and per-session JSONL even on failure; nonzero result for unmet gate.
    Logic: ephemeral loopback socket, original MCP lifespan, one resident service, proper shutdown.
    Constraints: local single-user transport only; unchanged FP32 models and image transforms.
    """
    if args.output.exists() or min(args.fps) < 1 or args.seconds < 1:
        raise ValueError("Require new output directory, positive rates and seconds")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from django.conf import settings

    from config.asgi import application as original_application
    from realtime.resident_asgi import ResidentApplication
    from realtime.services import ResidentService

    inputs = [args.input] + ([args.reconnect_input] if args.reconnect_input else [])
    prepared = [(path, *fixture_bytes(path)) for path in inputs]
    args.output.mkdir(parents=True)
    report = {"status": "running", "mode": args.mode, "sessions": [], "inputs": [
        {"path": str(path), "size": size, "sha256": hashlib.sha256(raw).hexdigest()}
        for path, raw, size in prepared], "scope": "Local single-user static-image WS probe"}
    report["implementation_sha256"] = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        for path in ("realtime/services.py", "realtime/resident_asgi.py", "realtime/detector.py",
                     "realtime/depth.py", "realtime/tracking.py", "realtime/risk.py",
                     "zerogpu/runtime.py", "tools/benchmark_resident.py")}
    report["settings"] = {"precision": "FP32", "detection_edge": 640, "depth_edge": 768,
                          "confidence_threshold": 0.25, "jpeg_quality": 80, "depth_graph": False}
    report["assets_sha256"] = {}
    for name in ("yolo26n.pt", "yolo26n.json", "yolo26n-depth.pt"):
        if name == "yolo26n-depth.pt" and args.mode == "detection":
            continue
        with (ROOT / "models" / name).open("rb") as asset:
            report["assets_sha256"][name] = hashlib.file_digest(asset, "sha256").hexdigest()
    records_by_session = []
    service = ResidentService(ROOT, args.mode)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    original_origins = settings.CAMERA_ALLOWED_ORIGINS
    settings.CAMERA_ALLOWED_ORIGINS = [origin]
    server = uvicorn.Server(uvicorn.Config(ResidentApplication(service, original_application),
        lifespan="on", log_level="warning", ws="websockets-sansio", ws_max_size=MAX_PACKET_BYTES,
        ws_max_queue=12, ws_per_message_deflate=False))
    boot = time.perf_counter()
    running = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(120):
            while not server.started:
                if running.done():
                    running.result()
                    raise RuntimeError("Resident startup failed")
                await asyncio.sleep(0.01)
        report["startup_ms"] = (time.perf_counter() - boot) * 1000
        import torch

        report["environment"] = {"python": platform.python_version(), "torch": torch.__version__,
                                 "cuda": torch.version.cuda, "gpu": torch.cuda.get_device_name()}
        original_detector = service.detector
        original_runner = None
        trackers = []
        for input_index, (_, raw, _) in enumerate(prepared):
            for fps in args.fps:
                records, session = [], {"input_index": input_index}
                records_by_session.append(records)
                report["sessions"].append(session)
                await stream(f"ws://127.0.0.1:{port}/ws/detect/resident/", origin, raw,
                             fps, args.seconds, records, session)
                if service.detector is not original_detector:
                    raise AssertionError("Model was reloaded")
                if original_runner is None:
                    original_runner = service.detector.runner
                elif service.detector.runner is not original_runner:
                    raise AssertionError("Detection Graph was recreated on reconnect")
                if args.mode == "full":
                    tracker = service.detector.object_tracker
                    if any(tracker is previous for previous in trackers):
                        raise AssertionError("Tracking state leaked across sessions")
                    trackers.append(tracker)
                    if any(row["tracking_status"] != "active" for row in records):
                        raise AssertionError("Tracking became unavailable")
        report.update(status="completed", model_instances=1, detection_graph_instances=1,
                      isolated_trackers=len(trackers),
                      peak_allocated_mib=torch.cuda.max_memory_allocated() / 2**20,
                      peak_reserved_mib=torch.cuda.max_memory_reserved() / 2**20)
        return all(s["meets_existing_transport_gate"] for s in report["sessions"])
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__)
        raise
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(running, 60)
            report["clean_shutdown"] = not (server.lifespan.error_occurred
                                            or server.lifespan.shutdown_failed)
            if not report["clean_shutdown"]:
                report["status"] = "failed_shutdown"
                raise RuntimeError("Resident lifespan failed during shutdown")
        finally:
            listener.close()
            settings.CAMERA_ALLOWED_ORIGINS = original_origins
            for index, records in enumerate(records_by_session):
                (args.output / f"session-{index}.jsonl").write_text(
                    "".join(json.dumps(row, allow_nan=False) + "\n" for row in records),
                    encoding="utf-8")
            (args.output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False),
                                                   encoding="utf-8")


def main():
    """Functionality: Parse explicit local benchmark options and preserve failure exit status.
    Inputs: CLI arguments. Outputs: report files and process code. Logic: one asyncio service run.
    Constraints: does not install models/dependencies, alter environment secrets or overwrite runs.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("detection", "depth", "full"), default="full")
    parser.add_argument("--fps", nargs="+", type=int, default=[12, 24])
    parser.add_argument("--seconds", type=int, default=10)
    parser.add_argument("--input", type=Path, default=ROOT / "model_tests/samples/bus.jpg")
    parser.add_argument("--reconnect-input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    raise SystemExit(0 if asyncio.run(run(parser.parse_args())) else 1)


if __name__ == "__main__":
    main()
