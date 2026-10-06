"""Responsibilities: Validate and measure the current ordered binary live protocol.
Implementation: Exact model/JPEG/box validation; explicitly qualified precision when supplied;
bounded eight-slot pacing and full-result timings, plus optional provider/relay stage observations.
Original FP32 control and existing FPS/latency acceptance remain unchanged.
Related Modules: realtime.protocol; core.detector;
tools.benchmark_live_remote/benchmark_zerogpu_live.
Declaration Index: validate; stream; stream.capture; stream.receive.
Variable Index: ROOT — backend; RECEIVE_TIMEOUT_SECONDS — steady failure budget;
MAX_INFLIGHT — shared eight-frame production bound; old five-window reports remain archived.
"""

import asyncio
import json
import math
import statistics
import time
from pathlib import Path

from core.detector import decode_frame
from core.inference_plan import validate_plan
from realtime.protocol import pack_request, unpack_result
from zerogpu.live_transport import MAX_INFLIGHT

ROOT = Path(__file__).resolve().parents[1]
RECEIVE_TIMEOUT_SECONDS = 10


def validate(metadata, jpeg, manifest, plan=None):
    """Functionality: Verify actual returned model identity, JPEG dimensions and finite boxes.
    Inputs: response metadata/JPEG, original manifest, optional explicitly qualified plan.
    Outputs: no value or ValueError. Logic: exact model/settings/source and declared precision/
    backend; original JPEG decoder and box boundaries. No plan means the original FP32 control.
    Constraints: a supplied plan must pass complete COCO metadata validation; this frame check
    does not itself measure mAP. Checksum/order already checked by protocol; no camera assertion.
    """
    expected = {
        "schema_version": 1,
        "model": "yolo26n",
        "backend": "PyTorch resident CUDA",
        "precision": "FP32",
        "classes": 80,
        "confidence_threshold": 0.25,
        "source_sha256": manifest["source_sha256"],
    }
    if plan is not None:
        validate_plan(plan, manifest)
        expected.update({key: plan[key] for key in ("precision", "backend")})
        expected["output_precision"] = "FP32"
    if any(metadata.get(key) != value for key, value in expected.items()):
        raise ValueError("Resident model contract mismatch")
    image = decode_frame(jpeg)
    if image.size != (metadata["width"], metadata["height"]):
        raise ValueError("Resident JPEG dimension mismatch")
    boxes = metadata["boxes"]
    if type(metadata["count"]) is not int or metadata["count"] != len(boxes):
        raise ValueError("Resident count mismatch")
    for box in boxes:
        cls, score, xyxy = box["class_id"], box["score"], box["xyxy"]
        if (
            type(cls) is not int
            or not 0 <= cls < 80
            or box["label"] != manifest["names"][cls]
            or not math.isfinite(score)
            or not 0.25 <= score <= 1
            or len(xyxy) != 4
            or not all(math.isfinite(value) for value in xyxy)
            or not 0 <= xyxy[0] < xyxy[2] <= image.width
            or not 0 <= xyxy[1] < xyxy[3] <= image.height
        ):
            raise ValueError("Resident boxes invalid")


async def stream(
    websocket, fixtures, references, manifest, start_index, fps, seconds, report, reader_pause_ms=0,
    plan=None,
):
    """Functionality: Pace public fixture samples and measure complete validated results.
    Inputs: WS, fixtures/reference hashes, manifest, index/fps/duration, report, reader pause,
    optional explicitly qualified plan used for response precision/backend validation.
    Outputs: run metrics and next index; Logic: separate sender/receiver and eight-slot admission.
    Constraints: sampling occurs after a slot is available, no queued old captures or dropped input;
    target10FPS/500ms unchanged. This clock excludes real JPEG capture/encoding and screen display.
    All returned raw/JPEG fingerprints must equal the same-runtime four-frame correctness phase.
    An explicit reader pause before result13 tests application admission; delayed results retain
    their original timestamps and may fail the unchanged latency target even if all frames arrive.
    Optional forward/provider/parent/worker timings are retained when supplied, without synthetic
    zeros or changes to timing boundaries, numerical settings or FPS/latency acceptance thresholds.
    """
    count = round(fps * seconds)
    started = time.perf_counter()
    slots = asyncio.Semaphore(MAX_INFLIGHT)
    pending, frames = {}, []
    result = {
        "input_fps": fps,
        "requested_duration_s": seconds,
        "requested_frames": count,
        "reader_pause_ms": reader_pause_ms,
        "success": False,
        "frames": frames,
        "max_inflight": 0,
        "max_sampling_lag_ms": 0.0,
    }
    report["runs"].append(result)

    # Functionality: Sample only when both its cadence and a bounded in-flight slot permit.
    # Inputs: enclosed WS/fixtures/index/rate/slots/pending; Outputs: full ordered binary requests.
    # Logic: monotonic pacing, acquire before capture timestamp, retain only timestamp/source/size.
    # Constraints: no skipped sequence, JPEG persistence or growing application input queue.
    async def capture():
        for ordinal in range(count):
            scheduled = started + ordinal / fps
            await asyncio.sleep(max(0, scheduled - time.perf_counter()))
            await slots.acquire()
            captured = time.perf_counter()
            index = start_index + ordinal
            name, jpeg = fixtures[ordinal % 4]
            pending[index] = (captured, name, len(jpeg))
            result["max_inflight"] = max(result["max_inflight"], len(pending))
            result["max_sampling_lag_ms"] = max(
                result["max_sampling_lag_ms"], (captured - scheduled) * 1000
            )
            await websocket.send(pack_request(index, jpeg))

    # Functionality: Record arrival only after complete JPEG, pinned contract and hash validation.
    # Inputs: enclosed expected sequence/pending/reference map; Outputs: frame metrics and slots.
    # Logic: bounded10s receives, optional one-time reader stall, actual codec/hash validation.
    # Constraints: stall is measured as latency; corruption/failure raises, no partial-success FPS.
    async def receive():
        for ordinal in range(count):
            index = start_index + ordinal
            if ordinal == 12 and reader_pause_ms:
                await asyncio.sleep(reader_pause_ms / 1000)
            packet = await asyncio.wait_for(websocket.recv(), RECEIVE_TIMEOUT_SECONDS)
            metadata, jpeg = unpack_result(packet, index)
            validate(metadata, jpeg, manifest, plan)
            captured, name, input_bytes = pending.pop(index)
            expected = references[name]
            if any(metadata[key] != expected[key] for key in ("raw_sha256", "jpeg_sha256")):
                raise ValueError("Resident steady-state prediction/JPEG fingerprint changed")
            received = time.perf_counter()
            frames.append(
                {
                    "index": index,
                    "fixture": name,
                    "capture_ms": (captured - started) * 1000,
                    "received_ms": (received - started) * 1000,
                    "latency_ms": (received - captured) * 1000,
                    "input_jpeg_bytes": input_bytes,
                    "response_bytes": len(packet),
                    **{
                        key: metadata[key]
                        for key in (
                            "inference_ms",
                            "graph_setup_ms",
                            "prepare_ms",
                            "gpu_stage_ms",
                            "render_ms",
                            "server_ms",
                            "raw_sha256",
                            "jpeg_sha256",
                        )
                    },
                    **{key: metadata[key] for key in ("forward_event_ms", "prs_upstream_ms",
                                                    "prs_admission_queue_ms",
                                                    "prs_result_validation_ms",
                                                    "hf_parent_queue_ms", "hf_local_cycle_ms",
                                                    "hf_result_ingest_ms", "hf_output_queue_ms",
                                                    "worker_pull_ms", "worker_predict_ms")
                       if key in metadata},
                }
            )
            slots.release()
            if len(frames) % 120 == 0:
                print(f"live input_fps={fps:g} verified_frames={len(frames)}", flush=True)

    try:
        async with asyncio.timeout(seconds + RECEIVE_TIMEOUT_SECONDS):
            async with asyncio.TaskGroup() as group:
                group.create_task(capture())
                group.create_task(receive())
        result["success"] = True
    except BaseException as error:
        result["failure_type"] = type(error).__name__
        raise
    finally:
        result["wall_ms"] = (time.perf_counter() - started) * 1000
        result["received_frames"] = len(frames)
        if len(frames) > 1:
            latencies = [frame["latency_ms"] for frame in frames]
            gaps = [
                b["received_ms"] - a["received_ms"]
                for a, b in zip(frames, frames[1:], strict=False)
            ]
            result.update(
                delivered_fps=(len(frames) - 1)
                * 1000
                / (frames[-1]["received_ms"] - frames[0]["received_ms"]),
                p95_latency_ms=statistics.quantiles(latencies, n=100, method="inclusive")[94],
                max_latency_ms=max(latencies),
                max_frame_gap_ms=max(gaps),
                repeated_graph_setups=sum(frame["graph_setup_ms"] > 0 for frame in frames),
            )
            result["meets_local_target"] = (
                result["success"]
                and result["delivered_fps"] >= 10
                and result["max_latency_ms"] <= 500
                and result["repeated_graph_setups"] == 0
            )
        print(
            json.dumps({key: value for key, value in result.items() if key != "frames"}), flush=True
        )
    return start_index + count
