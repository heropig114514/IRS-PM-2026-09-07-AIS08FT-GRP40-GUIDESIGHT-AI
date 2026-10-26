"""Responsibilities: Attribute CPU detection preparation and audit exact input tensors.
Implementation: perf_counter_ns stage probes, real PNG decode, separate JPEG ingress sample.
Related Modules: core.detector; core.detection_preprocess; sanpo_comparison/a_performance.
Declaration Index: baseline_stages; decode_stages; jpeg_probe; profile; main.
Variable Index: None. Diagnostic subtimings are separate from formal model latency.
"""

import argparse
import csv
import io
import json
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

from core.detection_preprocess import letterbox_fast
from core.detector import INPUT_EDGE, JPEG_QUALITY, decode_frame, letterbox
from object_distance.config import digest, write_json

from .a_performance import pair_order
from .performance import statistics


def baseline_stages(image, output, stages):
    """Functionality: Attribute the original letterbox expression sequence on CPU.
    Inputs: RGB image, reusable output and timing dict. Outputs: tensor plus inverse transform.
    Logic: reproduce original operations in order; separately audit against unmodified letterbox.
    Constraints: diagnostic replica only, never used in model inference or formal comparisons.
    """
    started = time.perf_counter_ns()
    if (
        output.shape != (1, 3, INPUT_EDGE, INPUT_EDGE)
        or output.dtype != np.float32
        or not output.flags.c_contiguous
    ):
        raise ValueError("Invalid output")
    scale = INPUT_EDGE / max(image.size)
    resized_size = (round(image.width * scale), round(image.height * scale))
    left, top = (INPUT_EDGE - resized_size[0]) // 2, (INPUT_EDGE - resized_size[1]) // 2
    now = time.perf_counter_ns()
    stages["geometry_buffer_ms"], started = (now - started) / 1e6, now
    canvas = Image.new("RGB", (INPUT_EDGE, INPUT_EDGE), (114, 114, 114))
    now = time.perf_counter_ns()
    stages["canvas_allocate_fill_ms"], started = (now - started) / 1e6, now
    resized = image.resize(resized_size, Image.Resampling.BILINEAR)
    now = time.perf_counter_ns()
    stages["resize_ms"], started = (now - started) / 1e6, now
    canvas.paste(resized, (left, top))
    now = time.perf_counter_ns()
    stages["paste_ms"], started = (now - started) / 1e6, now
    pixels = np.asarray(canvas)
    now = time.perf_counter_ns()
    stages["numpy_export_ms"], started = (now - started) / 1e6, now
    view = pixels.transpose(2, 0, 1)[None]
    now = time.perf_counter_ns()
    stages["layout_view_ms"], started = (now - started) / 1e6, now
    np.divide(view, np.float32(255), out=output, dtype=np.float32)
    stages["normalize_store_ms"] = (time.perf_counter_ns() - started) / 1e6
    return output, scale, left, top


def decode_stages(payload):
    """Functionality: Separate header opening, actual decompression and independent RGB copy.
    Inputs: original encoded bytes. Outputs: RGB image, file format, source mode and wall timings.
    Logic: force Image.load before copy, mirroring SANPO read_rgb's lazy decode plus copy.
    Constraints: libjpeg's YCbCr-to-RGB is fused into load and not falsely timed independently.
    """
    started = time.perf_counter_ns()
    with Image.open(io.BytesIO(payload)) as image:
        fmt, mode = image.format, image.mode
        opened = time.perf_counter_ns()
        image.load()
        loaded = time.perf_counter_ns()
        result = image.copy() if image.mode == "RGB" else image.convert("RGB")
        converted = time.perf_counter_ns()
    return (
        result,
        fmt,
        mode,
        {
            "header_ms": (opened - started) / 1e6,
            "decode_to_rgb_ms": (loaded - opened) / 1e6,
            "rgb_copy_or_conversion_ms": (converted - loaded) / 1e6,
        },
    )


def jpeg_probe(image):
    """Functionality: Profile a representative bounded JPEG80 production ingress separately.
    Inputs: decoded SANPO image. Outputs: decode timings and exact production-decoder audit.
    Logic: form a maximum-edge640 JPEG80 sample outside timing, then force decoding stages.
    Constraints: synthetic transport probe only; never replace dataset pixels or count it as PNG.
    """
    scale = INPUT_EDGE / max(image.size)
    bounded = image.resize(tuple(round(x * scale) for x in image.size), Image.Resampling.BILINEAR)
    stream = io.BytesIO()
    bounded.save(stream, format="JPEG", quality=JPEG_QUALITY)
    payload = stream.getvalue()
    decoded, fmt, mode, timings = decode_stages(payload)
    production = decode_frame(payload)
    return {
        "format": fmt,
        "mode": mode,
        "bytes": len(payload),
        **timings,
        "production_rgb_equal": np.array_equal(np.asarray(decoded), np.asarray(production)),
    }


def profile(options):
    """Functionality: Audit full-frame tensor identity and export operation-level CPU diagnostics.
    Inputs: existing source manifest/root and fresh output path. Outputs: JSON/CSV timing tables.
    Logic: decode real frames, alternate baseline/fast probes, compare bits to original output.
    Constraints: no GPU or changed weights; OpenCV candidate is measured and rejected on any
    pixel difference; isolated probes do not replace formal end-to-end model measurements.
    """
    import cv2

    root, output = Path(options["root"]), Path(options["output"])
    output.mkdir(parents=True, exist_ok=False)
    sources = [json.loads(s) for s in Path(options["frames"]).read_text().splitlines()]
    if options.get("limit"):
        sources = sources[: options["limit"]]
    buffers = {
        v: np.empty((1, 3, INPUT_EDGE, INPUT_EDGE), np.float32)
        for v in ("baseline", "optimized", "reference")
    }
    totals, formats, modes = defaultdict(list), Counter(), Counter()
    samples, mismatches, jpeg, rejected, memory = [], [], [], [], []
    for index, source in enumerate(sources):
        began = time.perf_counter_ns()
        payload = (root / source["rgb"]).read_bytes()
        io_ms = (time.perf_counter_ns() - began) / 1e6
        image, fmt, mode, decoded = decode_stages(payload)
        formats[fmt] += 1
        modes[mode] += 1
        samples.append(
            {
                "image_id": f"{source['session_id']}/{source['frame_index']:06d}",
                "variant": "source",
                "timings": {"file_read_ms": io_ms, **decoded},
            }
        )
        reference = letterbox(image, output=buffers["reference"])
        for variant in pair_order(0, index):
            stages = {}
            began = time.perf_counter_ns()
            actual = (
                baseline_stages(image, buffers[variant], stages)
                if variant == "baseline"
                else letterbox_fast(image, output=buffers[variant], stages=stages)
            )
            total = (time.perf_counter_ns() - began) / 1e6
            remainder = total - sum(stages.values())
            if remainder < -0.001:
                raise ValueError("Stage timing exceeds total")
            stages["cleanup_control_ms"] = max(0.0, remainder)
            stages["prepare_total_ms"] = total
            # Strict bit equality includes normalization; checks are outside stage timing.
            equal = actual[1:] == reference[1:] and np.array_equal(
                actual[0].view(np.uint32), reference[0].view(np.uint32)
            )
            if not equal:
                mismatches.append({"frame": index, "variant": variant})
            for key, value in stages.items():
                totals[variant, key].append(value)
            samples.append(
                {
                    "image_id": samples[-1]["image_id"],
                    "variant": variant,
                    "timings": stages,
                    "input_bit_exact": equal,
                }
            )
        if index % 50 == 0 or index == len(sources) - 1:
            scale = INPUT_EDGE / max(image.size)
            size = tuple(round(x * scale) for x in image.size)
            jpeg.append(jpeg_probe(image))
            rgb = np.asarray(image)
            began = time.perf_counter_ns()
            candidate = cv2.resize(rgb, size, interpolation=cv2.INTER_LINEAR_EXACT)
            cv_ms = (time.perf_counter_ns() - began) / 1e6
            expected = np.asarray(image.resize(size, Image.Resampling.BILINEAR))
            delta = np.abs(candidate.astype(np.int16) - expected.astype(np.int16))
            rejected.append(
                {
                    "frame": index,
                    "candidate": "OpenCV_INTER_LINEAR_EXACT",
                    "resize_ms": cv_ms,
                    "different_values": int(np.count_nonzero(delta)),
                    "max_pixel_delta": int(delta.max()),
                    "adopted": False,
                }
            )
            print(f"preprocess profile {index + 1}/{len(sources)}", flush=True)
        memory.append(
            {
                "baseline_numpy_export_bytes": INPUT_EDGE * INPUT_EDGE * 3,
                "optimized_numpy_export_bytes": round(image.width * reference[1])
                * round(image.height * reference[1])
                * 3,
            }
        )
    rows = [
        {"variant": v, "stage": key, **statistics(values)}
        for (v, key), values in sorted(totals.items())
    ]
    source_rows = [s["timings"] for s in samples if s["variant"] == "source"]
    report = {
        "status": "complete" if not mismatches else "input_mismatch",
        "frames": len(sources),
        "configuration": options,
        "manifest_sha256": digest(options["frames"]),
        "formats": dict(formats),
        "modes": dict(modes),
        "input_mismatches": mismatches,
        "stages": rows,
        "source_stages": {key: statistics([s[key] for s in source_rows]) for key in source_rows[0]},
        "jpeg_probe": {
            "samples": len(jpeg),
            "scope": "derived max-edge640 JPEG80, not eval data",
            "production_rgb_equal": all(s["production_rgb_equal"] for s in jpeg),
            "stages": {k: statistics([s[k] for s in jpeg]) for k in decoded},
        },
        "opencv_candidate": rejected,
        "memory": {
            "reused_output_bytes_both": buffers["baseline"].nbytes,
            "per_frame_output_allocations_both": 0,
            "baseline_pil_canvas_allocations_per_frame": 1,
            "optimized_pil_canvas_allocations_per_frame": 0,
            "mean_numpy_export_bytes": {
                v: float(np.mean([s[v + "_numpy_export_bytes"] for s in memory]))
                for v in ("baseline", "optimized")
            },
            "notes": "Resize workspace/export allocations remain; CHW/NCHW are views. "
            "Copy/store costs are included in export/normalization/padding, not added twice.",
        },
        "limitations": [
            "CPU attribution only; diagnostic replica is bit-audited against original.",
            "Header/read/decode/copy are excluded from the existing formal E2E scope.",
            "JPEG chroma conversion is part of native decoder load, not separately observable.",
        ],
    }
    write_json(output / "metrics.json", report)
    with (output / "stages.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with (output / "frames.jsonl").open("w", encoding="utf-8") as stream:
        stream.writelines(json.dumps(s) + "\n" for s in samples)
    return report


def main(argv=None):
    """Functionality: Expose reproducible preprocessing attribution without model inference.
    Inputs: manifest/root/limit/output options. Outputs: diagnostic tables and equality status.
    Logic: run one alternate-order pass. Constraints: input mutations and mismatches fail visibly.
    """
    parser = argparse.ArgumentParser(description="Pixel-exact detection preprocessing diagnostics")
    parser.add_argument("--root", default="model_tests/datasets/sanpo")
    parser.add_argument(
        "--frames",
        default="model_tests/datasets/sanpo/reports/continuous-diagnostic-01/frames.jsonl",
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument("--output", required=True)
    options = vars(parser.parse_args(argv))
    if options["limit"] is not None and options["limit"] < 1:
        parser.error("Require a positive limit")
    return 0 if profile(options)["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
