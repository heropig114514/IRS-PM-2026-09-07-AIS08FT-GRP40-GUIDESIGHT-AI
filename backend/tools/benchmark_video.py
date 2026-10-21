"""Responsibilities: Compare full-video native FP32/Graph execution and trace moving-scene costs.
Implementation: Every decoded source frame and timestamp, first-call Graph readiness, full aligned
depth hashes outside prediction timing, optional original-call stage tracing and exact references.
Related Modules: tools.benchmark_inference/inference_profile/resident_graph_probe;
tools.tracking_overlap_probe; realtime.detector.
Declaration Index: VideoFrames; VideoFrames.__init__; VideoFrames.__iter__; VideoFrames.close;
DepthEvidence; DepthEvidence.__init__; DepthEvidence.__call__; DepthEvidence.consume;
frame_identity; validate_reference; install_tracking_trace; file_hash; run; main.
Variable Index: ROOT — backend assets; SOURCES — measured project implementation identities.
"""

import argparse
import hashlib
import io
import json
import logging
import math
import platform
import time
from contextlib import ExitStack
from dataclasses import asdict
from functools import partial
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from tools.benchmark_inference import TIMINGS, build_detector, distribution
from tools.inference_profile import StageTrace
from tools.resident_graph_probe import prepare_depth_graph

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ("core/detector.py", "realtime/detector.py", "realtime/depth.py",
           "realtime/tracking.py", "realtime/risk.py", "realtime/distance.py",
           "realtime/distance_config.py", "realtime/performance.py", "zerogpu/runtime.py",
           "tools/benchmark_video.py", "tools/benchmark_inference.py",
           "tools/inference_profile.py", "tools/depth_graph_probe.py",
           "tools/resident_graph_probe.py", "tools/tracking_overlap_probe.py")


class VideoFrames:
    """Functionality: Read a complete constant-rate fixture without skipping source frames.
    Logic: require declared count/rate and strictly increasing matching decoder timestamps.
    Constraints: serial one-pass diagnostic; source geometry fixed, no alternate decoder or retry.
    """

    def __init__(self, path):
        """Functionality: Open an existing video and validate its declared timing contract.
        Inputs: explicit local path. Outputs: open decoder and finite frame/rate/geometry metadata.
        Logic: inspect before iteration; release on invalid metadata. Constraints: no downloads.
        """
        import cv2

        self.capture = cv2.VideoCapture(str(path))
        count = self.capture.get(cv2.CAP_PROP_FRAME_COUNT)
        self.fps = self.capture.get(cv2.CAP_PROP_FPS)
        width = self.capture.get(cv2.CAP_PROP_FRAME_WIDTH)
        height = self.capture.get(cv2.CAP_PROP_FRAME_HEIGHT)
        if (not self.capture.isOpened()
                or not all(math.isfinite(value) for value in (count, self.fps, width, height))
                or count < 2 or self.fps <= 0 or min(width, height) <= 0
                or any(value != int(value) for value in (count, width, height))):
            self.close()
            raise ValueError("Video requires valid frame count, frame rate and geometry")
        self.count, self.width, self.height = int(count), int(width), int(height)

    def __iter__(self):
        """Functionality: Deliver every source frame with the established camera JPEG transform.
        Inputs: open decoder. Outputs: index/source seconds, JPEG80, prepared shape and input hash.
        Logic: sequential decode, BGR to RGB, max640 bilinear resize; verify exact declared count.
        Constraints: no realtime pacing, resampling, duplicated frames, early cutoff or rewind.
        """
        import cv2

        previous = -1.0
        for index in range(self.count):
            ok, bgr = self.capture.read()
            if not ok:
                raise RuntimeError("Video ended before its declared frame count")
            timestamp = self.capture.get(cv2.CAP_PROP_POS_MSEC) / 1000
            if not previous < timestamp or abs(timestamp - index / self.fps) > 1e-6:
                raise ValueError("Source timestamps differ from the declared frame cadence")
            previous = timestamp
            if bgr.shape[:2] != (self.height, self.width):
                raise ValueError("Source frame geometry changed")
            image = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
            scale = min(1, 640 / max(image.size))
            image = image.resize((round(image.width * scale), round(image.height * scale)),
                                 Image.Resampling.BILINEAR)
            output = io.BytesIO()
            image.save(output, "JPEG", quality=80)
            jpeg = output.getvalue()
            yield {"index": index, "timestamp_s": timestamp, "size": list(image.size),
                   "input_sha256": hashlib.sha256(jpeg).hexdigest()}, jpeg
        if self.capture.read()[0]:
            raise RuntimeError("Video exceeds its declared frame count")

    def close(self):
        """Functionality: Release only this tool's decoder. Inputs: open decoder. Outputs: None.
        Logic: direct release. Constraints: no changes to the input file or unrelated processes.
        """
        self.capture.release()


class DepthEvidence:
    """Functionality: Retain one actual aligned depth result for independent hash comparison.
    Logic: call the unchanged estimator once, consume the tensor after prediction timing ends.
    Constraints: full-map host copies are diagnostic only; not imported by production paths.
    """

    def __init__(self, predict):
        """Functionality: Bind the original estimator. Inputs: callable. Outputs: empty evidence.
        Logic: no warmup/allocation. Constraints: one outstanding result, one serial worker.
        """
        self.predict, self.pending = predict, None

    def __call__(self, image):
        """Functionality: Retain the same tensor returned to original fusion.
        Inputs: actual decoded RGB frame. Outputs: unchanged depth tensor.
        Logic: invoke once and retain reference. Constraints: unread previous evidence fails.
        """
        if self.pending is not None:
            raise RuntimeError("Previous depth evidence was not consumed")
        self.pending = self.predict(image)
        return self.pending

    def consume(self):
        """Functionality: Hash every aligned depth value outside prediction timing.
        Inputs: one retained tensor. Outputs: shape/dtype/SHA256 or explicit missing-result error.
        Logic: copy to CPU, hash contiguous values, then release reference.
        Constraints: no reduction, tolerance, quantization or model recomputation.
        """
        if self.pending is None:
            raise RuntimeError("Depth estimator did not produce evidence")
        depth = self.pending.detach().cpu().numpy()
        self.pending = None
        return {"shape": list(depth.shape), "dtype": str(depth.dtype),
                "sha256": hashlib.sha256(depth.tobytes()).hexdigest()}


def frame_identity(row):
    """Functionality: Select all deterministic input/output fields for exact paired comparison.
    Inputs: recorded complete frame. Outputs: identity including full boxes/motion/risk and JPEG.
    Logic: exclude measured wall times; require source time and tracker lifecycle to match.
    Constraints: no timestamp tolerance, rounded distances, dropped labels or timing-derived FPS.
    """
    metadata = row["metadata"]
    return {**{key: row[key] for key in ("index", "timestamp_s", "size", "input_sha256", "depth")},
            **{key: metadata[key] for key in ("raw_sha256", "boxes", "jpeg_sha256",
                "tracking_timestamp_s", "tracking_model", "tracking_status", "tracking_epoch",
                "gmc_method", "with_reid", "distance_mode", "depth_frame", "depth_interval")}}


def validate_reference(original, current):
    """Functionality: Compare saved and in-memory experiment contracts in their JSON representation.
    Inputs: completed reference report and current identities/settings/environment.
    Outputs: None or ValueError for changed status or contract.
    Logic: canonicalize tuple containers through the same lossless JSON serialization as reports.
    Constraints: no changed values, numeric tolerance, ignored configuration keys or missing fields.
    """
    if original["status"] != "completed":
        raise ValueError("Reference must be a completed experiment")
    for field in ("identities", "settings", "implementation_sha256", "sdk_sha256",
                  "opencv_native_sha256", "environment"):
        serialized = json.loads(json.dumps(current[field], allow_nan=False))
        if original[field] != serialized:
            raise ValueError(f"Reference {field} differs")


def install_tracking_trace(trace, detector, stack):
    """Functionality: Attribute actual tracking calls to SDK update, GMC and optical-flow stages.
    Inputs: full detector, inactive StageTrace and owning ExitStack. Outputs: restored-on-exit
    wrappers. Logic: original calls only; native OpenCV identity is recorded with SDK sources.
    Constraints: no algorithm/feature/iteration changes or extra GPU synchronization; stages nest.
    """
    import cv2

    tracker = detector.object_tracker.tracker
    if tracker.args.gmc_method != "sparseOptFlow":
        raise ValueError("Tracking trace requires the unchanged sparseOptFlow configuration")
    targets = [(tracker, "update", "tracking_sdk_update"), (tracker.gmc, "apply", "tracking_gmc"),
               (cv2, "goodFeaturesToTrack", "tracking_features"),
               (cv2, "calcOpticalFlowPyrLK", "tracking_flow"),
               (cv2, "estimateAffinePartial2D", "tracking_affine")]
    for owner, name, label in targets:
        stack.enter_context(patch.object(owner, name, trace.wrap(label, getattr(owner, name))))


def file_hash(path):
    """Functionality: Identify a complete local asset without retaining its bytes.
    Inputs: existing path. Outputs: SHA256 string. Logic: streamed file digest.
    Constraints: errors propagate; no download or replacement asset.
    """
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def run(video, output, *, depth_graph=False, trace_steady=False, reference=None,
        tracking_overlap=False, trace_overlap=False):
    """Functionality: Record a complete original or independent Graph full-video experiment.
    Inputs: local video/new output, explicit Graph/trace/overlap flags and completed reference.
    Outputs: every frame, first/steady distributions, equality verdict and failure-safe report.
    Logic: one model/process; source timestamps; first real frame then one Graph preparation;
    aligned-depth transfer/hash and output writing outside prediction timing; retain all mismatches.
    Constraints: no label cache, discarded outliers, source resampling, fallback/retry, accuracy or
    browser-FPS claim. Trace and diagnostic host copies can perturb timing; controls share copies.
    Overlap requires Graph and uses separate optional device observations, never serial StageTrace.
    """
    if (tracking_overlap and (not depth_graph or trace_steady)
            or trace_overlap and not tracking_overlap):
        raise ValueError("Overlap requires Graph and separate device observations, no serial trace")
    if not video.is_file() or output.exists():
        raise ValueError("Provide an existing video and a new output directory")
    output.mkdir(parents=True)
    report = {"status": "running", "completed_frames": 0, "mismatched_frames": 0,
              "scope": "Full-video numerical/timing diagnostic; not browser/accuracy acceptance",
              "depth_graph": depth_graph, "trace_steady": trace_steady,
              "tracking_overlap": tracking_overlap, "trace_overlap": trace_overlap,
              "reference": str(reference) if reference else None}
    frames, source, trace, observer = [], None, StageTrace() if trace_steady else None, None
    overlap = None
    try:
        report["implementation_sha256"] = {name: file_hash(ROOT / name) for name in SOURCES}
        report["identities"] = {"video": file_hash(video), **{
            name: file_hash(ROOT / "models" / name)
            for name in ("yolo26n.pt", "yolo26n-depth.pt", "yolo26n.json")}}
        import cv2
        import torch
        import ultralytics

        from realtime.distance_config import DEFAULT_DISTANCE_CONFIG
        from realtime.risk import DEFAULT_RISK_CONFIG
        from realtime.tracking import DEFAULT_TRACKING_CONFIG

        if not torch.cuda.is_available():
            raise RuntimeError("Native CUDA required")
        report["environment"] = {"python": platform.python_version(), "pillow": Image.__version__,
            "opencv": cv2.__version__, "torch": torch.__version__,
            "ultralytics": ultralytics.__version__, "cuda": torch.version.cuda,
            "gpu": torch.cuda.get_device_name()}
        sdk = Path(ultralytics.__file__).resolve().parent
        report["sdk_sha256"] = {name: file_hash(sdk / name) for name in (
            "engine/model.py", "engine/predictor.py", "models/yolo/depth/predict.py",
            "trackers/bot_sort.py", "trackers/byte_tracker.py", "trackers/utils/gmc.py",
            "trackers/utils/matching.py", "trackers/utils/stracks.py",
            "cfg/trackers/botsort.yaml")}
        report["opencv_native_sha256"] = {
            p.name: file_hash(p) for p in Path(cv2.__file__).parent.glob("*.pyd")}
        source = VideoFrames(video)
        report["settings"] = {"declared_frames": source.count, "source_fps": source.fps,
            "source_size": [source.width, source.height], "detection_edge": 640, "confidence": .25,
            "depth_edge": 768, "precision": "FP32", "jpeg_quality": 80, "batch": 1,
            "tracking": asdict(DEFAULT_TRACKING_CONFIG), "risk": asdict(DEFAULT_RISK_CONFIG),
            "distance": asdict(DEFAULT_DISTANCE_CONFIG), "label_cache": False,
            "timestamps": "decoder presentation time; no realtime pacing"}
        reference_rows = None
        if reference is not None:
            original = json.loads((reference / "report.json").read_text(encoding="utf-8"))
            validate_reference(original, report)
            reference_rows = [json.loads(line) for line in
                              (reference / "frames.jsonl").read_text(encoding="utf-8").splitlines()]
            if len(reference_rows) != source.count:
                raise ValueError("Reference frame count differs")
        torch.cuda.reset_peak_memory_stats()
        started = time.perf_counter()
        detector = build_detector("full", ROOT / "models/yolo26n.pt", ROOT / "models/yolo26n.json",
                                  ROOT / "models/yolo26n-depth.pt")
        report["model_initialization_ms"] = (time.perf_counter() - started) * 1000
        observer = DepthEvidence(detector.depth_estimator.predict)
        with (ExitStack() as stack,
              (output / "frames.jsonl").open("w", encoding="utf-8", newline="\n") as records):
            stack.enter_context(patch.object(detector.depth_estimator, "predict", observer))
            for identity, jpeg in source:
                index = identity["index"]
                predict = partial(overlap or detector.predict, timestamp=identity["timestamp_s"])
                started = time.perf_counter()
                metadata, _ = trace.call(index, predict, jpeg) if trace else predict(jpeg)
                prepare_ms = 0.0
                if index == 0 and depth_graph:
                    preparing = time.perf_counter()
                    graph = prepare_depth_graph(detector, jpeg)
                    if tracking_overlap:
                        from tools.tracking_overlap_probe import TrackingOverlapProbe

                        overlap = TrackingOverlapProbe(detector, graph,
                                                       observe_device=trace_overlap)
                    prepare_ms = (time.perf_counter() - preparing) * 1000
                    report["depth_graph_evidence"] = graph.evidence
                    logging.getLogger(__name__).info("Depth Graph prepared once ms=%.3f",
                                                     prepare_ms)
                wall = (time.perf_counter() - started) * 1000
                verifying = time.perf_counter()
                depth = observer.consume()
                evidence_ms = (time.perf_counter() - verifying) * 1000
                row = {**identity, "metadata": metadata, "depth": depth, "predict_wall_ms": wall,
                       "depth_evidence_ms": evidence_ms, "depth_graph_prepare_ms": prepare_ms,
                       "depth_graph_replayed": depth_graph and index > 0,
                       "tracking_overlap_replayed": tracking_overlap and index > 0}
                if reference_rows is not None:
                    expected, actual = frame_identity(reference_rows[index]), frame_identity(row)
                    row["unequal_fields"] = [
                        key for key in expected if expected[key] != actual[key]]
                    report["mismatched_frames"] += bool(row["unequal_fields"])
                records.write(json.dumps(row, allow_nan=False) + "\n")
                records.flush()
                frames.append({**{key: metadata[key] for key in TIMINGS if key in metadata},
                    "predict_wall_ms": wall, "depth_evidence_ms": evidence_ms,
                    "depth_graph_prepare_ms": prepare_ms,
                    **metadata.get("tracking_overlap_experiment", {})})
                report["completed_frames"] += 1
                if (metadata["tracking_status"] != "active"
                        or metadata["performance"]["total_frames"] != index + 1
                        or metadata["performance"]["depth_model_calls"] != index + 1):
                    raise AssertionError("Full pipeline tracking or frame/depth counters differ")
                if index == 0 and trace:
                    installing = time.perf_counter()
                    trace.install(detector, stack)
                    install_tracking_trace(trace, detector, stack)
                    report["trace_setup_ms"] = (time.perf_counter() - installing) * 1000
        report.update(status="failed_equivalence" if report["mismatched_frames"] else "completed",
                      gpu_peak_allocated_bytes=torch.cuda.max_memory_allocated(),
                      gpu_peak_reserved_bytes=torch.cuda.max_memory_reserved())
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__)
        logging.getLogger(__name__).error("Video experiment failed type=%s", type(error).__name__)
        raise
    finally:
        if source is not None:
            source.close()
        if observer is not None:
            observer.pending = None
        if trace is not None:
            report["trace"] = trace.write(output)
        report["first_frame"] = frames[0] if frames else None
        report["steady"] = {key: distribution([frame[key] for frame in frames[1:] if key in frame])
                            for key in (*TIMINGS, "depth_evidence_ms", "depth_graph_prepare_ms")}
        report["overlap_steady"] = {key: distribution([
            frame[key] for frame in frames[1:] if key in frame]) for key in (
                "depth_call_ms", "association_cpu_ms", "graph_submit_host_ms",
                "graph_stream_elapsed_ms")}
        (output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n",
                                           encoding="utf-8", newline="\n")
    return report


def main():
    """Functionality: Execute one explicitly selected full-video experiment.
    Inputs: --video/--output, optional --depth-graph/--trace-steady/--reference,
    --tracking-overlap and its separate --trace-overlap device observations.
    Outputs: status/count and exit 2 for exact mismatch, 1 for runtime error, 0 for completion.
    Logic: parse and run once. Constraints: no dataset download or automatic repeat/acceptance.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--depth-graph", action="store_true")
    parser.add_argument("--trace-steady", action="store_true")
    parser.add_argument("--tracking-overlap", action="store_true")
    parser.add_argument("--trace-overlap", action="store_true")
    parser.add_argument("--reference", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    report = run(args.video, args.output, depth_graph=args.depth_graph,
                 trace_steady=args.trace_steady, reference=args.reference,
                 tracking_overlap=args.tracking_overlap, trace_overlap=args.trace_overlap)
    print(json.dumps({key: report[key] for key in
                      ("status", "completed_frames", "mismatched_frames")}))
    return 2 if report["status"] == "failed_equivalence" else 0


if __name__ == "__main__":
    raise SystemExit(main())
