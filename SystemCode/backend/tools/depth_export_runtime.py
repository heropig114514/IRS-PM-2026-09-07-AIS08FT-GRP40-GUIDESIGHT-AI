"""Responsibilities: Run explicit ONNX CUDA depth diagnostics outside the native service.
Implementation: ORT-owned compute streams, producer/output synchronization and owned Torch outputs.
Related Modules: realtime.depth_runtime; tools.benchmark_depth_export.
Declaration Index: OrtDepthForward; OrtDepthForward.__init__; OrtDepthForward.__call__;
OrtDepthForward.close.
Variable Index: None. Session lifetime is explicit and serial.
"""

from pathlib import Path

from realtime.depth_runtime import validate_input


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
