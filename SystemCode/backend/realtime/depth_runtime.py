"""Responsibilities: Verify and run prebuilt native FP32 TensorRT depth artifacts.
Implementation: Checked checkpoint/runtime identity, static tensor bindings and owned CUDA outputs.
Related Modules: realtime.depth; tools.benchmark_depth_export; tools.depth_export_runtime.
Declaration Index: file_hash; validate_manifest; validate_input; runtime_identity; TrtDepthForward;
TrtDepthForward.__init__; TrtDepthForward.__call__; TensorRTDepthPool;
TensorRTDepthPool.__init__; TensorRTDepthPool.__call__.
Variable Index: logger — safe artifact-load and unsupported-shape diagnostics.
"""

import hashlib
import importlib.metadata
import json
import logging
import platform
from pathlib import Path

from core.detector import DetectionError

logger = logging.getLogger(__name__)


def file_hash(path):
    """Functionality: Bind an artifact to its complete bytes.
    Inputs: local file path. Outputs: SHA256 hex digest. Logic: hash a bounded byte stream.
    Constraints: missing or unreadable files fail; filenames alone are never model identities.
    """
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()



def validate_manifest(manifest, directory, weights, runtime_identity, *, engine_only=False):
    """Functionality: Reject stale checkpoints, graphs, SDK code or GPU/runtime bindings.
    Inputs: manifest, artifact directory, weights, observed identity and engine-only selection.
    Outputs: None on success. Logic: compare provenance and hash each declared binary before load.
    Constraints: engine-only checks deployed dependencies and engine binaries; export packages and
    ONNX files are unnecessary for serving. No rebuild/fallback; manifests are trusted config.
    """
    recorded_identity = manifest.get("identity", {})
    if engine_only:
        recorded_identity = {key: recorded_identity.get(key) for key in runtime_identity}
    if (manifest.get("schema") != 1 or manifest.get("precision") != "FP32"
            or manifest.get("tf32") is not False
            or manifest.get("weights_sha256") != file_hash(weights)
            or recorded_identity != runtime_identity):
        raise ValueError("Depth artifact identity changed; explicitly export and build again")
    entries = manifest.get("artifacts", [])
    if not entries:
        raise ValueError("Depth manifest has no artifacts")
    root = Path(directory).resolve()
    shapes = set()
    for entry in entries:
        shape = entry["input_shape"]
        if (len(shape) != 4 or shape[:2] != [1, 3]
                or any(type(value) is not int or value <= 0 or value % 32 for value in shape[2:])
                or entry["output_shape"] != [1, 1, shape[2] // 4, shape[3] // 4]):
            raise ValueError("Depth manifest has an unsupported tensor contract")
        if tuple(shape) in shapes:
            raise ValueError("Depth manifest contains duplicate tensor shapes")
        shapes.add(tuple(shape))
        if engine_only and ("engine" not in entry or entry.get("builder") != {
                "strongly_typed": True, "tf32": False, "workspace_bytes": 1 << 30,
                "max_aux_streams": 0, "fp16": False, "int8": False}):
            raise ValueError("Depth engine is missing or its FP32 build policy changed")
        for kind in (("engine",) if engine_only else ("onnx", "engine")):
            if kind not in entry:
                continue
            path = (root / entry[kind]).resolve()
            if path.parent != root or file_hash(path) != entry[kind + "_sha256"]:
                raise ValueError("Depth artifact path or digest mismatch")


def runtime_identity():
    """Functionality: Observe deployed SDK/GPU identity without importing export-only packages.
    Inputs: installed native packages and active CUDA device. Outputs: provenance dictionary.
    Logic: use the same fields and SDK source hashes as the export manifest, excluding ONNX tools.
    Constraints: no network, implicit dependency install or runtime version substitution.
    """
    import torch
    import ultralytics

    sdk = Path(ultralytics.__file__).parent
    props = torch.cuda.get_device_properties(torch.cuda.current_device())
    return {"python": platform.python_version(), "platform": platform.platform(),
            "torch": torch.__version__, "cuda": torch.version.cuda,
            "cudnn": torch.backends.cudnn.version(), "ultralytics": ultralytics.__version__,
            "tensorrt": importlib.metadata.version("tensorrt-cu12"),
            "gpu": props.name, "compute_capability": [props.major, props.minor],
            "total_memory": props.total_memory,
            "depth_head_sha256": file_hash(sdk / "nn/modules/head.py"),
            "depth_predictor_sha256": file_hash(sdk / "models/yolo/depth/predict.py")}



def validate_input(image, shape, device):
    """Functionality: Reject incompatible input before binding device pointers.
    Inputs: Torch image tensor, fixed BCHW shape and captured CUDA device. Outputs: None or error.
    Logic: require contiguous float32, original shape and identical device.
    Constraints: no conversion, resize, automatic stream migration or CPU fallback.
    """
    import torch

    if (not isinstance(image, torch.Tensor) or tuple(image.shape) != tuple(shape)
            or image.dtype != torch.float32 or image.device != device
            or device.type != "cuda" or not image.is_contiguous()):
        raise ValueError("Depth runtime requires its fixed contiguous CUDA FP32 tensor")



class TrtDepthForward:
    """Functionality: Run a verified fixed-shape TensorRT engine on Torch's current stream.
    Logic: use named TensorRT 10 bindings and independently owned output tensors.
    Constraints: one context per serial runner; no compilation, precision change or fallback.
    """

    def __init__(self, path, entry, example, *, runtime=None, logger=None):
        """Functionality: Deserialize an explicitly selected engine and validate its I/O.
        Inputs: checked engine, entry, CUDA example and optional shared runtime/logger.
        Outputs: execution context.
        Logic: retain runtime/engine lifetimes; check names, float32 types and exact static shapes.
        Constraints: manifest provenance must be checked by the caller before deserialization.
        """
        import tensorrt as trt
        import torch

        validate_input(example, entry["input_shape"], example.device)
        self.device, self.shape = example.device, tuple(example.shape)
        self.stream = torch.cuda.current_stream(self.device)
        if (runtime is None) != (logger is None):
            raise ValueError("A shared TensorRT runtime requires its logger owner")
        self.logger = trt.Logger(trt.Logger.WARNING) if logger is None else logger
        self.runtime = trt.Runtime(self.logger) if runtime is None else runtime
        self.engine = self.runtime.deserialize_cuda_engine(Path(path).read_bytes())
        if self.engine is None:
            raise RuntimeError("TensorRT engine deserialization failed")
        if self.engine.num_io_tensors != 2:
            raise ValueError("TensorRT engine I/O count changed")
        for name, shape, mode in (("images", entry["input_shape"], trt.TensorIOMode.INPUT),
                                  ("depth", entry["output_shape"], trt.TensorIOMode.OUTPUT)):
            if (self.engine.get_tensor_mode(name) != mode
                    or list(self.engine.get_tensor_shape(name)) != shape
                    or self.engine.get_tensor_dtype(name) != trt.float32
                    or self.engine.get_tensor_location(name) != trt.TensorLocation.DEVICE):
                raise ValueError("TensorRT engine I/O contract changed")
        self.context = self.engine.create_execution_context()
        if self.context is None:
            raise RuntimeError("TensorRT execution context creation failed")
        self.output_shape = tuple(entry["output_shape"])

    def __call__(self, image, *args, **kwargs):
        """Functionality: Submit TensorRT forward and return independently owned raw depth.
        Inputs: fixed input tensor and no inference options. Outputs: CUDA FP32 depth tensor.
        Logic: bind device addresses then enqueue once on the captured Torch stream.
        Constraints: caller manages input lifetime and synchronization; all binding failures raise.
        """
        import torch

        validate_input(image, self.shape, self.device)
        if args or kwargs or torch.cuda.current_stream(self.device) != self.stream:
            raise ValueError("TensorRT depth stream or inference options changed")
        output = torch.empty(self.output_shape, device=self.device, dtype=torch.float32)
        if (not self.context.set_tensor_address("images", image.data_ptr())
                or not self.context.set_tensor_address("depth", output.data_ptr())
                or not self.context.execute_async_v3(self.stream.cuda_stream)):
            raise RuntimeError("TensorRT binding or forward submission failed")
        return output


class TensorRTDepthPool:
    """Functionality: Dispatch the unchanged SDK tensor to a verified prebuilt depth engine.
    Logic: load each declared shape once at activation; preserve native quarter-resolution output.
    Constraints: one serial GPU worker/current stream, no request-time compilation or fallback.
    """

    def __init__(self, manifest_path, weights):
        """Functionality: Validate provenance before loading every configured engine.
        Inputs: operator-selected manifest and current checkpoint. Outputs: ready shape runners.
        Logic: check all hashes/build policies first, then deserialize on the owning CUDA stream.
        Constraints: at most eight prebuilt shapes bound context residency; no eviction or retries.
        """
        import tensorrt as trt
        import torch

        path = Path(manifest_path)
        manifest = json.loads(path.read_text(encoding="utf-8"))
        validate_manifest(manifest, path.parent, weights, runtime_identity(), engine_only=True)
        if len(manifest["artifacts"]) > 8:
            raise ValueError("At most eight explicit depth engine shapes may be resident")
        self.runners = {}
        self.manifest_sha256 = file_hash(path)
        self.logger = trt.Logger(trt.Logger.WARNING)
        self.runtime = trt.Runtime(self.logger)
        for entry in manifest["artifacts"]:
            shape = tuple(entry["input_shape"])
            example = torch.empty(shape, dtype=torch.float32, device="cuda")
            self.runners[shape] = TrtDepthForward(path.parent / entry["engine"], entry, example,
                                                 runtime=self.runtime, logger=self.logger)
        self.shapes = [list(shape) for shape in self.runners]
        logger.info("Depth backend loaded backend=tensorrt precision=FP32 shapes=%s", self.shapes)

    def __call__(self, tensor, *args, **kwargs):
        """Functionality: Execute exactly one matching engine on current frame pixels.
        Inputs: SDK-preprocessed CUDA FP32 tensor. Outputs: independently owned raw depth tensor.
        Logic: exact shape lookup, then runner's type/device/stream/options checks.
        Constraints: unknown shape is a public error, never resize, engine build or Torch fallback.
        """
        shape = tuple(tensor.shape)
        if shape not in self.runners:
            logger.warning("Depth engine shape unavailable shape=%s", shape)
            raise DetectionError("depth_input_shape", "No prebuilt depth engine for this shape.",
                                 400)
        return self.runners[shape](tensor, *args, **kwargs)
