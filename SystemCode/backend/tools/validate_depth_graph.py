"""Responsibilities: Compare the experimental depth Graph on existing independent image inputs.
Implementation: One unchanged FP32 pipeline; explicit graph per observed shape; forward/reverse
input order checks cached graph reuse, raw maps, fused distances and complete JPEG outputs.
Related Modules: tools.depth_graph_probe/benchmark_inference/fixtures; realtime.depth/detector.
Declaration Index: run; main.
Variable Index: ROOT — backend assets; no ground-truth metrics or performance claims.
"""

import argparse
import hashlib
import json
import platform
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(inputs, output):
    """Functionality: Validate exact eager/Graph equivalence without changing production inference.
    Inputs: explicit source-image list and new report directory. Outputs: all checks and identities.
    Logic: traverse every input then reverse order; use existing camera preprocessing, one eager
    callable and one explicitly prepared Graph per shape; compare full maps/boxes/JPEGs exactly.
    Constraints: no tracking on unrelated stills, changed precision, resized evaluation split,
    hidden fallback or retry; this is numerical consistency, not measured depth accuracy.
    """
    if not inputs or output.exists():
        raise ValueError("Provide explicit images and a new output directory")
    for path in inputs:
        if not path.is_file():
            raise ValueError("Every input must be an existing image")
    output.mkdir(parents=True)
    report = {"status": "running", "scope": "Multi-input FP32 numerical diagnostic; no truth data",
              "requested_comparisons": len(inputs) * 2, "completed_comparisons": 0,
              "frames": [], "graphs": {}, "input_sha256": {
                  str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}}
    report["implementation_sha256"] = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in ("realtime/depth.py", "realtime/detector.py", "zerogpu/runtime.py",
                     "tools/depth_graph_probe.py", "tools/validate_depth_graph.py")}
    try:
        import numpy as np
        import torch

        from core.detector import decode_frame
        from tools.benchmark_inference import build_detector
        from tools.depth_graph_probe import DepthGraphProbe
        from tools.fixtures import fixture_bytes

        detector = build_detector("depth", ROOT / "models/yolo26n.pt", ROOT / "models/yolo26n.json",
                                  ROOT / "models/yolo26n-depth.pt")
        report["environment"] = {"python": platform.python_version(), "torch": torch.__version__,
                                 "cuda": torch.version.cuda, "gpu": torch.cuda.get_device_name()}
        report["assets_sha256"] = {}
        for name in ("yolo26n.pt", "yolo26n-depth.pt", "yolo26n.json"):
            with (ROOT / "models" / name).open("rb") as asset:
                report["assets_sha256"][name] = hashlib.file_digest(asset, "sha256").hexdigest()
        estimator, probes, eager = detector.depth_estimator, {}, None
        for index, path in enumerate([*inputs, *reversed(inputs)]):
            raw, size = fixture_bytes(path)
            image = decode_frame(raw)
            if eager is not None:
                estimator.model.predictor.inference = eager
            before = estimator.predict(image).clone()
            original, original_jpeg = detector.predict(raw)
            predictor = estimator.model.predictor
            if eager is None:
                eager = predictor.inference
            bgr = np.ascontiguousarray(np.asarray(image)[..., ::-1])
            example = predictor.preprocess([bgr])
            shape = tuple(example.shape)
            if shape not in probes:
                probes[shape] = DepthGraphProbe(eager, example)
                report["graphs"][str(shape)] = probes[shape].evidence
            predictor.inference = probes[shape]
            after = estimator.predict(image)
            candidate, candidate_jpeg = detector.predict(raw)
            equal = torch.equal(before, after)
            row = {"index": index, "input": str(path), "frame_size": size,
                   "prepared_sha256": hashlib.sha256(raw).hexdigest(), "graph_shape": shape,
                   "depth_exact": equal,
                   "max_depth_difference_m": (before - after).abs().max().item(),
                   "depth_sha256": hashlib.sha256(before.cpu().numpy().tobytes()).hexdigest(),
                   "candidate_depth_sha256": hashlib.sha256(
                       after.cpu().numpy().tobytes()).hexdigest(),
                   "detection_exact": original["raw_sha256"] == candidate["raw_sha256"],
                   "boxes_and_distances_exact": original["boxes"] == candidate["boxes"],
                   "jpeg_exact": original_jpeg == candidate_jpeg,
                   "count": original["count"], "depth_shape": list(before.shape)}
            report["frames"].append(row)
            if not all(row[key] for key in ("depth_exact", "detection_exact",
                                           "boxes_and_distances_exact", "jpeg_exact")):
                raise AssertionError("Experimental depth Graph differs from eager output")
            report["completed_comparisons"] += 1
        report.update(status="completed", graph_count=len(probes),
                      peak_allocated_mib=torch.cuda.max_memory_allocated() / 2**20)
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__)
        raise
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False),
                                            encoding="utf-8")
    return report


def main():
    """Functionality: Run one explicit image-suite diagnostic.
    Inputs: --inputs paths and --output new directory. Outputs: saved report/nonzero on mismatch.
    Logic: no implicit data selection or model substitution. Constraints: no quality acceptance.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", nargs="+", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.inputs, args.output)
    print(json.dumps({"status": result["status"], "comparisons": result["completed_comparisons"]}))


if __name__ == "__main__":
    main()
