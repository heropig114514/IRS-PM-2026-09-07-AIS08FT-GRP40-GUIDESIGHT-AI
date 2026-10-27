"""Responsibilities: Validate the explicitly selected inference identity and quality evidence.
Implementation: Read a local deployment plan; require original weights/640/threshold/JPEG and
three complete COCO quality processes before exposing its backend/precision to consumers;
admit the unchanged T4 JIT winner through an explicit cache-only deployment policy.
Related Modules: tools.prepare_hf_plan writes the artifact; runtime/relay/benchmarks consume it.
Declaration Index: validate_plan; read_plan.
Variable Index: None. Plan config/quality/provenance are explicit inputs, never environment flags.
"""

import json
import math
import re


def validate_plan(plan, manifest):
    """Functionality: Verify a trusted local plan's identity and full-validation admission gate.
    Inputs: decoded plan and pinned model manifest. Outputs: the validated plan or ValueError.
    Logic: exact original settings; require the measured serial adapter or unchanged JIT winner,
    derive precision/backend from explicit Torch configuration;
    check each of at least three distinct process results, common dataset hashes and finite AP.
    Constraints: this validates evidence metadata, not the external experiment itself or HF GPU;
    unimplemented backends/compile modes fail explicitly, with no alternative execution chosen.
    """
    # "threshold" is the DEFAULT class threshold (core.detector.CONFIDENCE_THRESHOLD). Per-class
    # overrides (core.detector.CLASS_CONF, traffic light 0.12) are runtime postprocessing only and
    # not part of the AP-validated plan, so the plan JSON stays at 0.25.
    expected = {"schema_version": 1, "model": "yolo26n", "classes": 80, "edge": 640,
                "batch": 1, "threshold": 0.25, "jpeg_quality": 80,
                "source_sha256": manifest["source_sha256"]}
    if any(plan.get(key) != value for key, value in expected.items()):
        raise ValueError("Inference plan changes the original model/settings identity")
    config = plan["config"]
    if config.get("backend") != "torch":
        raise ValueError("This HF adapter requires measured Torch execution")
    jit = bool(config.get("compile"))
    if jit and (config != {"backend": "torch", "dtype": "float16", "fuse": True,
                         "buffers": True, "pinned": True, "preprocess_buffer": True,
                         "compile": "default", "graph": True, "layout": True} or
                plan.get("deployment") != {"adapter": "cached_jit_graph",
                                          "cache_miss_policy": "error"}):
        raise ValueError("JIT deployment requires the unchanged T4 winner and cache-only policy")
    precision = {"float32": "FP32", "float16": "FP16", "bfloat16": "BF16"}.get(
        config.get("dtype")
    )
    backend = ("PyTorch compiled CUDA" if jit else
               "PyTorch AOT CUDA" if config.get("aoti") else "PyTorch resident CUDA")
    allowed = {"backend", "dtype", "fuse", "buffers", "pinned", "graph", "preprocess_buffer",
               "layout", "benchmark", "tf32", "aoti", "aoti_single_threaded", "serving_adapter",
               "compile"}
    if (precision is None or plan.get("precision") != precision or plan.get("backend") != backend
            or config.get("spaces_aoti") or config.get("tf32")
            or (not jit and config.get("serving_adapter") is not True) or set(config) - allowed
            or not config.get("fuse") or not config.get("buffers")
            or not config.get("pinned") or not config.get("graph")
            or not config.get("preprocess_buffer")
            or (config.get("aoti") and not config.get("aoti_single_threaded"))):
        raise ValueError("Inference plan is outside the measured serial Graph adapter contract")
    quality = plan["quality"]
    baseline = quality["baseline_ap"]
    replicas = quality["replicates"]
    if (type(baseline) not in (int, float) or not math.isfinite(baseline)
            or not 0 <= baseline <= 1 or quality.get("images") != 5000
            or quality.get("score") != 0.001 or quality.get("max_dets") != 100
            or quality.get("preprocessing") != "project640/bilinear/JPEG80/RGB/letterbox"
            or quality.get("maximum_ap_drop") != 0.005
            or len(replicas) < 3 or len({row["name"] for row in replicas}) != len(replicas)):
        raise ValueError("Inference plan lacks three complete comparable COCO quality processes")
    for row in replicas:
        ap = row["ap"]
        if (row["status"] != "complete" or row["images"] != 5000
                or type(ap) not in (int, float) or not math.isfinite(ap) or not 0 <= ap <= 1
                or baseline - ap > 0.005 + 1e-12):
            raise ValueError("Inference plan contains an incomplete or unqualified quality process")
    provenance = plan["provenance"]
    if type(provenance.get("version")) is not int or provenance["version"] < 1:
        raise ValueError("Inference plan requires an actual positive Notebook version")
    evidence_hashes = [provenance["summary_sha256"], quality["annotation_sha256"],
                      quality["image_manifest_sha256"],
                      *(row["prediction_sha256"] for row in replicas)]
    for value in evidence_hashes:
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError("Inference plan evidence hash is invalid")
    return plan


def read_plan(path, manifest):
    """Functionality: Read an explicitly named local deployment plan.
    Inputs: Path and original manifest. Outputs: validated JSON plan.
    Logic: UTF8 read followed by the shared strict validator; file/schema errors propagate.
    Constraints: no network, GPU initialization, missing-file default, retry or backend fallback.
    """
    return validate_plan(json.loads(path.read_text(encoding="utf-8")), manifest)
