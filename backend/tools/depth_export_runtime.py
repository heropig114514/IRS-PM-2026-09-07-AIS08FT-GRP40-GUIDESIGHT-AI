"""Responsibilities: Explicit device-resident FP32 depth export experiment runtimes.
Implementation: Validate fixed artifacts and owned Torch outputs; explicit runtime completion rules.
Related Modules: tools.benchmark_depth_export; realtime.depth; tools.depth_graph_probe.
Declaration Index: file_hash; validate_manifest; validate_input; OrtDepthForward;
OrtDepthForward.__init__; OrtDepthForward.__call__; OrtDepthForward.close; TrtDepthForward;
TrtDepthForward.__init__; TrtDepthForward.__call__.
Variable Index: None. Sessions, engines, streams and buffers belong to individual runner instances.
"""

import hashlib
from pathlib import Path


def file_hash(path):
    """Functionality: Bind an artifact to its complete bytes.
    Inputs: local file path. Outputs: SHA256 hex digest. Logic: hash a bounded byte stream.
    Constraints: missing or unreadable files fail; filenames alone are never model identities.
    """
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def validate_manifest(manifest, directory, weights, runtime_identity):
    """Functionality: Reject stale checkpoints, graphs, SDK code or GPU/runtime bindings.
    Inputs: parsed manifest, artifact directory, current weights and observed identity dictionary.
    Outputs: None on success. Logic: compare provenance and hash each declared binary before load.
    Constraints: no rebuild, download or fallback; manifests are trusted local configuration.
    """
    if (manifest.get("schema") != 1 or manifest.get("precision") != "FP32"
            or manifest.get("tf32") is not False
            or manifest.get("weights_sha256") != file_hash(weights)
            or manifest.get("identity") != runtime_identity):
        raise ValueError("Depth artifact identity changed; explicitly export and build again")
    entries = manifest.get("artifacts", [])
    if not entries:
        raise ValueError("Depth manifest has no artifacts")
    root = Path(directory).resolve()
    for entry in entries:
        shape = entry["input_shape"]
        if (len(shape) != 4 or shape[:2] != [1, 3]
                or any(type(value) is not int or value <= 0 or value % 32 for value in shape[2:])
                or entry["output_shape"] != [1, 1, shape[2] // 4, shape[3] // 4]):
            raise ValueError("Depth manifest has an unsupported tensor contract")
        for kind in ("onnx", "engine"):
            if kind not in entry:
                continue
            path = (root / entry[kind]).resolve()
            if path.parent != root or file_hash(path) != entry[kind + "_sha256"]:
                raise ValueError("Depth artifact path or digest mismatch")


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


class OrtDepthForward:
    """Functionality: Execute a fixed ONNX graph entirely on the CUDA execution provider.
    Logic: bind Torch pointers; synchronize the producer and ORT-owned stream explicitly.
    Constraints: serial calls on the construction stream; CPU fallback and TF32 are disabled.
    Borrowed nonzero Torch streams aborted during Windows ORT 1.23.2 teardown in local diagnostics.
    """

    def __init__(self, path, entry, example):
        """Functionality: Construct a CUDA-only ONNX session with explicit precision and I/O.
        Inputs: checked ONNX path, manifest entry and CUDA example. Outputs: initialized runner.
        Logic: load Torch CUDA DLLs, disable CPU fallback and let ORT own its compute stream.
        Constraints: provider errors propagate; this does not change the resident service backend.
        """
        import onnxruntime as ort
        import torch

        validate_input(example, entry["input_shape"], example.device)
        ort.preload_dlls(directory=str(Path(torch.__file__).parent / "lib"), msvc=False)
        options = ort.SessionOptions()
        options.add_session_config_entry("session.disable_cpu_ep_fallback", "1")
        self.device, self.shape = example.device, tuple(example.shape)
        self.stream = torch.cuda.current_stream(self.device)
        self.session = ort.InferenceSession(str(path), sess_options=options, providers=[
            ("CUDAExecutionProvider", {"device_id": self.device.index,
                                      "use_tf32": "0"}),
        ])
        self.session.disable_fallback()
        if "CUDAExecutionProvider" not in self.session.get_providers():
            raise RuntimeError("ONNX CUDA provider was not activated")
        inputs, outputs = self.session.get_inputs(), self.session.get_outputs()
        if (len(inputs) != 1 or len(outputs) != 1 or inputs[0].name != "images"
                or outputs[0].name != "depth" or inputs[0].shape != entry["input_shape"]
                or outputs[0].shape != entry["output_shape"]
                or inputs[0].type != "tensor(float)" or outputs[0].type != "tensor(float)"):
            raise ValueError("ONNX I/O contract changed")
        self.output_shape = tuple(entry["output_shape"])
        self.options = ort.RunOptions()
        self.options.add_run_config_entry("disable_synchronize_execution_providers", "1")
        self.providers = self.session.get_provider_options()
        if self.providers["CUDAExecutionProvider"].get("use_tf32") != "0":
            raise RuntimeError("ONNX Runtime did not honor TF32 disabled")

    def __call__(self, image, *args, **kwargs):
        """Functionality: Complete ONNX forward with owned output and no host tensor copy.
        Inputs: fixed input tensor and no inference options. Outputs: CUDA FP32 raw depth tensor.
        Logic: complete the producer stream, bind pointers, run, then synchronize ORT outputs.
        Constraints: synchronous forward; no borrowed-stream lifetime or implicit stream dependency.
        """
        import numpy as np
        import torch

        validate_input(image, self.shape, self.device)
        if self.session is None:
            raise RuntimeError("ONNX depth session is closed")
        if args or kwargs or torch.cuda.current_stream(self.device) != self.stream:
            raise ValueError("ONNX depth stream or inference options changed")
        self.stream.synchronize()
        output = torch.empty(self.output_shape, device=self.device, dtype=torch.float32)
        binding = self.session.io_binding()
        binding.bind_input("images", "cuda", self.device.index, np.float32,
                           self.shape, image.data_ptr())
        binding.bind_output("depth", "cuda", self.device.index, np.float32,
                            self.output_shape, output.data_ptr())
        self.session.run_with_iobinding(binding, self.options)
        binding.synchronize_outputs()
        return output

    def close(self):
        """Functionality: Release the explicitly owned, synchronous ONNX session.
        Inputs: this serial runner. Outputs: closed state; subsequent inference fails explicitly.
        Logic: each call already waits for completion; release the session before its stream owner.
        Constraints: serial use only; no resource leak, implicit runtime fallback or retry.
        """
        if self.session is not None:
            self.session = None


class TrtDepthForward:
    """Functionality: Run a verified fixed-shape TensorRT engine on Torch's current stream.
    Logic: use named TensorRT 10 bindings and independently owned output tensors.
    Constraints: one context per serial runner; no compilation, precision change or fallback.
    """

    def __init__(self, path, entry, example):
        """Functionality: Deserialize an explicitly selected engine and validate its I/O.
        Inputs: checked engine path, manifest entry and CUDA example. Outputs: execution context.
        Logic: retain runtime/engine lifetimes; check names, float32 types and exact static shapes.
        Constraints: manifest provenance must be checked by the caller before deserialization.
        """
        import tensorrt as trt
        import torch

        validate_input(example, entry["input_shape"], example.device)
        self.device, self.shape = example.device, tuple(example.shape)
        self.stream = torch.cuda.current_stream(self.device)
        self.logger = trt.Logger(trt.Logger.WARNING)
        self.runtime = trt.Runtime(self.logger)
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
