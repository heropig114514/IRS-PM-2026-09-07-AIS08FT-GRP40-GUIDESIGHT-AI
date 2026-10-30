"""Responsibilities: Process an entire local video through the existing perception pipeline.
Implementation: One detector/tracker, source timestamps, streamed records and verified MP4 export.
Related Modules: video_export.media/overlay/refinement; integration.inference.services;
realtime.risk; tools.export_video; VIDEO-EXPORT.md.
Declaration Index: file_digest; write_report; prepare_frame; export_video.
Variable Index: ROOT — backend model/configuration root; RISK_COLORS — display-only level colors;
logger — phase/frame-count diagnostics, never source pixels or audio.
"""

import hashlib
import io
import json
import logging
import time
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path

from PIL import Image

from core.detection_model import selected_model
from realtime.depth_config import read_depth_backend
from realtime.distance_config import read_distance_config
from realtime.risk import DEFAULT_RISK_CONFIG, RISK_MODEL
from realtime.tracking import DEFAULT_TRACKING_CONFIG

from .media import VideoReader, VideoWriter, audio_fingerprints, copy_audio, verify_video
from .overlay import annotate_frame
from .refinement import VideoRefinement

ROOT = Path(__file__).resolve().parents[1]
logger = logging.getLogger(__name__)


def file_digest(path):
    """Functionality: Identify source and output bytes. Inputs: Local path. Outputs: SHA256.
    Logic: Stream fixed-size chunks. Constraints: No whole-video buffering or external requests.
    """
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_report(output, report):
    """Functionality: Atomically publish the owned run's current status.
    Inputs: Exclusive output directory and finite report. Outputs: Updated run.json.
    Logic: Replace an adjacent temporary manifest; encode configuration paths as strings.
    Constraints: Called only after this service successfully created the output directory.
    """
    temporary = output / "run.json.tmp"
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False,
                                    default=str) + "\n", encoding="utf-8")
    temporary.replace(output / "run.json")


def prepare_frame(frame, max_edge, pixel_aspect=1):
    """Functionality: Apply display rotation and established RGB/bilinear/JPEG80 preprocessing.
    Inputs: Decoded frame, longest-edge cap and source pixel aspect. Outputs: Model JPEG bytes.
    Logic: Normalize non-square pixels, bake display rotation, then cap display dimensions.
    Constraints: Export and model coordinates share this image; no model parameter changes.
    """
    image = frame.to_image().convert("RGB")
    if pixel_aspect != 1:
        display_width = max(1, round(image.width * pixel_aspect))
        image = image.resize((display_width, image.height), Image.Resampling.BILINEAR)
    if frame.rotation:
        image = image.rotate(frame.rotation, expand=True)
    scale = min(1., max_edge / max(image.size))
    size = tuple(max(1, round(value * scale)) for value in image.size)
    image = image.resize(size, Image.Resampling.BILINEAR)
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=80)
    return buffer.getvalue()


def export_video(source, output, *, detection_model=None, max_edge=640, detector_factory=None,
                 profile="stable", show_ids=False):
    """Functionality: Export an entire video with detection, distance, tracking and risk labels.
    Inputs: Source/new output directory, model/size/profile/ID options and optional test factory.
    Outputs: Completed manifest, annotated.mp4, results.jsonl; failed runs retain explicit status.
    Logic: Freeze existing configurations; initialize once; infer every frame on source time;
    apply the explicit stable profile unless baseline is selected; encode, copy original audio,
    verify decoded media and publish only after success. Preserve raw candidates in frame audits.
    Constraints: Requires native CUDA for real inference; no server/55-second budget, frame drops,
    model download, CPU fallback, generated speech, existing-output overwrite or HTTP upload.
    """
    source, output = Path(source).resolve(), Path(output).resolve()
    if not source.is_file():
        raise ValueError("Input must be an existing local video file")
    if output.exists():
        raise ValueError("Output already exists; choose a new output directory")
    if type(max_edge) is not int or not 64 <= max_edge <= 1280:
        raise ValueError("max-edge must be an integer between 64 and 1280")
    if profile not in ("stable", "baseline"):
        raise ValueError("profile must be stable or baseline")
    refinement = VideoRefinement() if profile == "stable" else None
    model = selected_model({"PRS_DETECTION_MODEL": detection_model}) if detection_model else (
        selected_model())
    distance, depth = read_distance_config(), read_depth_backend(ROOT)
    report = {"schema_version": 1, "status": "running", "frames": 0,
              "source": {"path": str(source), "sha256": file_digest(source)},
              "settings": {"refinement": refinement.profile if refinement else None,
                           "overlay": {"layout": "box-local-v2", "show_ids": show_ids},
                           "model": model, "max_edge": max_edge, "jpeg_quality": 80,
                           "distance": asdict(distance), "depth": asdict(depth),
                           "tracking": asdict(DEFAULT_TRACKING_CONFIG),
                           "risk": asdict(DEFAULT_RISK_CONFIG), "risk_model": RISK_MODEL,
                           "timestamps": "source presentation time relative to first video frame",
                           "video_codec": "libx264", "crf": 20,
                           "audio": "copy original tracks; no generated speech"}}
    output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    write_report(output, report)
    logger.info("Video export starting model=%s max_edge=%d", model, max_edge)
    video_path, records_path = output / "video.partial.mp4", output / "results.partial.jsonl"
    try:
        with ExitStack() as stack:
            reader = VideoReader(source)
            stack.callback(reader.close)
            report["source"].update(reader.info)
            if detector_factory is None:
                from integration.inference.services import load_detector

                detector_factory = load_detector
            detector, hashes = detector_factory(distance_config=distance, depth_config=depth,
                                                 detection_model=model)
            if refinement is not None:
                detector.video_refinement = refinement
            report["models"] = hashes
            writer = None
            records = stack.enter_context(records_path.open("x", encoding="utf-8"))
            for index, (frame, timestamp, duration) in enumerate(reader):
                jpeg = prepare_frame(frame, max_edge, reader.pixel_aspect)
                metadata, _ = detector.predict(jpeg, timestamp=float(timestamp))
                if metadata.get("tracking_model") != "botsort" or metadata.get(
                        "risk_model") != RISK_MODEL:
                    raise ValueError("Video export requires the complete tracking/risk pipeline")
                if refinement is not None and metadata.get("refinement_profile") != (
                        refinement.profile["name"]):
                    raise ValueError("Detector did not apply the selected video refinement profile")
                annotated = annotate_frame(jpeg, metadata, show_ids=show_ids)
                if writer is None:
                    writer = VideoWriter(video_path, annotated.size, reader.rate, reader.time_base)
                    stack.callback(writer.close)
                    report["output_size"] = list(writer.size)
                    report["inference_size"] = list(annotated.size)
                if list(annotated.size) != report["inference_size"]:
                    raise ValueError("Annotated frame geometry changed")
                writer.write(annotated, timestamp, duration)
                row = {"index": index, "source_pts": frame.pts,
                       "source_time_base": str(frame.time_base), "timestamp": str(timestamp),
                       "timestamp_s": float(timestamp), "duration": str(duration),
                       "metadata": metadata}
                records.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
                report["frames"] = index + 1
                if index == 0 or (index + 1) % 30 == 0:
                    records.flush()
                    write_report(output, report)
                    logger.info("Video export frames=%d source_seconds=%.3f", index + 1,
                                float(timestamp))
            writer.close()
        audio = reader.info["audio_streams"]
        if audio:
            combined = output / "annotated.partial.mp4"
            copy_audio(video_path, source, combined, audio, reader.origin)
            fingerprints = audio_fingerprints(source)
            if fingerprints != audio_fingerprints(combined):
                raise ValueError("Original audio packet payloads changed during export")
            report["audio_verification"] = fingerprints
            candidate = combined
        else:
            candidate = video_path
        report["verification"] = verify_video(candidate, records_path, report["frames"],
                                               report["output_size"])
        report["output_sha256"] = file_digest(candidate)
        candidate.rename(output / "annotated.mp4")
        records_path.rename(output / "results.jsonl")
        if audio:
            video_path.unlink()
        report.update(status="completed", processing_seconds=time.perf_counter() - started,
                      video="annotated.mp4", results="results.jsonl")
        write_report(output, report)
        logger.info("Video export completed frames=%d processing_seconds=%.3f", report["frames"],
                    report["processing_seconds"])
        return report
    except (Exception, KeyboardInterrupt) as error:
        report.update(status="cancelled" if isinstance(error, KeyboardInterrupt) else "failed",
                      error_type=type(error).__name__, error=str(error),
                      processing_seconds=time.perf_counter() - started)
        write_report(output, report)
        logger.error("Video export failed frames=%d error_type=%s", report["frames"],
                     type(error).__name__)
        raise
