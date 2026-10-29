"""Responsibilities: Probe the actual local MVP with a public static image and ordered frames.
Implementation: Independent PRF2 client, selected 80/87-class model, explicit stop/new session;
verify advertised depth backend/fusion on every returned frame.
Related Modules: integration.asgi/inference; realtime.protocol; tools.fixtures/live_benchmark.
Declaration Index: endpoint — loopback URL check; status — read integration health;
probe_session — validate one real stream; run — inspect two streams; main — CLI/report boundary.
Variable Index: ROOT — backend assets; REQUEST — independent network-order PRF2 header.
No camera, ground-truth accuracy, physical speech or haptic claim is made by this probe.
"""

import argparse
import asyncio
import hashlib
import http.client
import json
import statistics
import struct
import time
from pathlib import Path
from urllib.parse import urlsplit

from core.detection_model import model_files
from integration.protocol import validate_contract, validate_result
from realtime.protocol import MAX_PACKET_BYTES, unpack_result
from tools.fixtures import fixture_bytes
from tools.live_benchmark import validate

ROOT = Path(__file__).resolve().parents[1]
REQUEST = struct.Struct("!4sId")


def endpoint(value):
    """Functionality: Admit only the explicit loopback MVP endpoint.
    Inputs: CLI WebSocket URL. Outputs: validated URL or ValueError.
    Logic: Disallow credentials, query and remote hosts before any network request.
    Constraints: This local probe is not a cloud deployment or credential testing tool.
    """
    parts = urlsplit(value)
    if (parts.scheme != "ws" or parts.hostname not in {"127.0.0.1", "localhost"}
            or parts.path != "/ws/detect/local/" or parts.query or parts.fragment
            or parts.username or parts.password):
        raise ValueError("Use the loopback /ws/detect/local/ endpoint")
    return value


def status(url):
    """Functionality: Inspect local integration status without invoking inference.
    Inputs: validated WS URL. Outputs: decoded status dictionary.
    Logic: Fixed same-host HTTP status path, five-second network deadline and 64KiB bound.
    Constraints: No retries, redirect following or external-service calls.
    """
    parts = urlsplit(url)
    connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=5)
    try:
        connection.request("GET", "/integration/status/")
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError("Integration status is unavailable")
        raw = response.read(65537)
    finally:
        connection.close()
    if len(raw) > 65536:
        raise ValueError("Integration status exceeded the probe boundary")
    return json.loads(raw)


async def probe_session(url, jpeg, manifest, frames):
    """Functionality: Process ordered public-image frames through the actual two-process service.
    Inputs: loopback endpoint, preencoded public JPEG, pinned manifest and positive frame count.
    Outputs: contract, every frame result and stop acknowledgement.
    Logic: Independent binary encoding; real response hash/codec/model checks; explicit stop.
    Constraints: Fixture roundtrip excludes sensor/encoding; it is not capture-to-alert latency.
    """
    from websockets.asyncio.client import connect

    report = {"frames": [], "complete": False}
    async with connect(url, origin="http://" + urlsplit(url).netloc, proxy=None,
                       compression=None, ping_interval=None, max_size=MAX_PACKET_BYTES,
                       open_timeout=10, close_timeout=5) as peer:
        hello = json.loads(await asyncio.wait_for(peer.recv(), 55))
        expected = {"kind": "session", "provider": "local", "protocol_version": 2,
                    "model": manifest["model"], "classes": len(manifest["names"]),
                    "precision": "FP32", "output_precision": "FP32",
                    "backend": "PyTorch resident CUDA", "source_sha256": manifest["source_sha256"]}
        if any(hello.get(key) != value for key, value in expected.items()):
            raise ValueError("Local admission identity mismatch")
        validate_contract(hello)
        if not isinstance(hello.get("session_id"), str) or not hello["session_id"]:
            raise ValueError("Missing local session identity")
        report["contract"] = hello
        for index in range(frames):
            captured = time.perf_counter()
            await peer.send(REQUEST.pack(b"PRF2", index, captured) + jpeg)
            packet = await asyncio.wait_for(peer.recv(), 55 if index == 0 else 10)
            if not isinstance(packet, bytes):
                raise ValueError("Expected a complete binary inference result")
            metadata, rendered = unpack_result(packet, index)
            validate(metadata, rendered, manifest)
            validate_result(metadata, hello, index, captured)
            if any(metadata.get(key) != hello.get(key)
                   for key in ("depth_backend", "depth_fusion")):
                raise ValueError("Depth execution identity changed after admission")
            expected_result = {"provider": "local", "session_id": hello["session_id"],
                               "capture_time_s": captured, "timestamp_source": "browser_capture",
                               "output_precision": "FP32", "tracking_status": "active",
                               "tracking_model": "botsort", "depth_units": "m",
                               "risk_model": "rules-v1"}
            if any(metadata.get(key) != value for key, value in expected_result.items()):
                raise ValueError("Local sequence, timing or algorithm-stage identity mismatch")
            required = {"distance_m", "track_id", "motion_state", "relative_speed_mps",
                        "smoothed_distance_m", "direction", "ttc_sec", "risk_score",
                        "risk_level", "alert_required", "alert_priority", "risk_reasons"}
            if not metadata.get("depth_model") or any(
                    not required.issubset(box) for box in metadata["boxes"]):
                raise ValueError("Missing fused algorithm results")
            elapsed_ms = (time.perf_counter() - captured) * 1000
            report["frames"].append({"index": index, "fixture_roundtrip_ms": elapsed_ms,
                                     "metadata": metadata})
        await peer.send('{"kind":"stop"}')
        report["end"] = json.loads(await asyncio.wait_for(peer.recv(), 10))
        terminal = report["end"]
        if (terminal.get("kind") != "ended" or any(
                type(terminal.get(key)) is not int or terminal[key] != frames
                for key in ("frames", "received", "forwarded", "worker_received",
                            "worker_verified", "delivered"))):
            raise ValueError("Missing or inconsistent explicit end acknowledgement")
    values = [frame["fixture_roundtrip_ms"] for frame in report["frames"]]
    report["first_frame_ms"] = values[0]
    report["steady_median_ms"] = statistics.median(values[1:]) if len(values) > 1 else None
    report["steady_max_ms"] = max(values[1:]) if len(values) > 1 else None
    report["complete"] = True
    return report


async def run(url, frames, input_path=None):
    """Functionality: Verify real inference, clean stop and a fresh second sequence.
    Inputs: local URL, count, optional local image and PRS_DETECTION_MODEL. Outputs: report.
    Logic: default public bus or explicit image; full probe then a one-frame fresh session.
    Constraints: No automatic retry or replacement inference; real video motion remains unverified.
    """
    input_path = ROOT / "model_tests/samples/bus.jpg" if input_path is None else input_path
    jpeg, _ = fixture_bytes(input_path)
    manifest = json.loads(model_files()[1].read_text(encoding="utf-8"))
    report = {"complete": False,
              "scope": "real native dual-model inference on one explicitly selected static image",
              "input": str(input_path), "input_sha256": hashlib.sha256(jpeg).hexdigest(),
              "camera_verified": False, "hardware_outputs_verified": False,
              "before": await asyncio.to_thread(status, url)}
    report["sessions"] = [await probe_session(url, jpeg, manifest, frames),
                          await probe_session(url, jpeg, manifest, 1)]
    if (report["sessions"][0]["contract"]["session_id"]
            == report["sessions"][1]["contract"]["session_id"]):
        raise ValueError("Independent streams reused a session identity")
    report["after"] = await asyncio.to_thread(status, url)
    if (report["after"].get("busy") is not False or report["after"].get("loaded") is not True
            or report["after"].get("ready") is not True):
        raise ValueError("Inference did not finish ready with its session slot released")
    report["complete"] = True
    return report


def main(argv=None):
    """Functionality: Execute one explicit probe and optionally save a new evidence artifact.
    Inputs: CLI URL, count, optional local image and unused output path. Outputs: exit status/JSON.
    Logic: Validate before network; preserve successful full metadata; failures expose only type.
    Constraints: Existing reports cannot be overwritten; no installation or service startup.
    """
    parser = argparse.ArgumentParser(description="Validate the actual isolated local MVP")
    parser.add_argument("--url", default="ws://127.0.0.1:8766/ws/detect/local/")
    parser.add_argument("--frames", type=int, default=6)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if not 1 <= args.frames <= 300:
        parser.error("frames must be 1..300")
    if args.output and args.output.exists():
        parser.error("Choose a new output report; existing evidence is preserved")
    try:
        report = asyncio.run(run(endpoint(args.url), args.frames, args.input))
    except Exception as error:
        print(json.dumps({"complete": False, "error_type": type(error).__name__}))
        return 1
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2,
                                         allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"complete": report["complete"],
                      "sessions": [{"frames": len(session["frames"]),
                                    "first_frame_ms": session["first_frame_ms"],
                                    "steady_median_ms": session["steady_median_ms"],
                                    "steady_max_ms": session["steady_max_ms"]}
                                   for session in report["sessions"]]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
