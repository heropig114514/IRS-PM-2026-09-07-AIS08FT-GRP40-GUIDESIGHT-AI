"""Responsibilities: Explicit TensorRT FP32 inference on the laptop's NVIDIA GPU.
Implementation: Verified local engine, CUDA runtime buffers and serialized synchronous inference.
Related Modules: core.detector shares preprocessing/rendering; tools.build_tensorrt pins artifacts.
Declaration Index: Cuda/Cuda.__init__/Cuda.check/Cuda.allocate/Cuda.copy/Cuda.close;
GpuDetector/GpuDetector.__init__/GpuDetector.predict; get_gpu_detector.
Variable Index: logger — safe diagnostics; gpu_detector/gpu_error/gpu_lock — one-time lifecycle.
"""

import ctypes
import hashlib
import json
import logging
import os
import sys
import time
from pathlib import Path
from threading import Lock

from .detector import DetectionError, annotate, decode_boxes, letterbox

logger = logging.getLogger(__name__)
gpu_detector = None
gpu_error = None
gpu_lock = Lock()


class Cuda:
    """Functionality: Own the minimal Windows CUDA runtime ABI and allocated device memory.
    Logic: explicit CUDA device 0; checked copy/allocation/synchronization calls.
    Constraints: Windows worker only; CUDA errors never select a different inference engine.
    """

    def __init__(self):
        """Functionality: Load CUDA 12 runtime. Inputs: self, isolated Python installation.
        Outputs: library/function ABI and allocation list; Logic: absolute DLL, checked device.
        Constraints: does not modify system PATH, drivers or existing CUDA 11.8 installation.
        """
        dll = Path(sys.prefix) / "Lib/site-packages/nvidia/cuda_runtime/bin/cudart64_12.dll"
        self.library = ctypes.WinDLL(str(dll))
        self.allocations = []
        signatures = {
            "cudaSetDevice": [ctypes.c_int],
            "cudaMalloc": [ctypes.POINTER(ctypes.c_void_p), ctypes.c_size_t],
            "cudaFree": [ctypes.c_void_p],
            "cudaMemcpy": [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int],
            "cudaStreamCreate": [ctypes.POINTER(ctypes.c_void_p)],
            "cudaStreamDestroy": [ctypes.c_void_p],
            "cudaStreamSynchronize": [ctypes.c_void_p],
        }
        for name, arguments in signatures.items():
            function = getattr(self.library, name)
            function.argtypes, function.restype = arguments, ctypes.c_int
        self.check(self.library.cudaSetDevice(0), "set_device")
        self.stream = ctypes.c_void_p()
        self.check(self.library.cudaStreamCreate(ctypes.byref(self.stream)), "create_stream")

    def check(self, status, operation):
        """Functionality: Reject CUDA failures. Inputs: self, numeric status, operation name.
        Outputs: None or RuntimeError; Logic: nonzero is failure.
        Constraints: caller logs operation/status only, never device buffers or image data.
        """
        if status:
            raise RuntimeError(f"CUDA {operation} failed status={status}")

    def allocate(self, size):
        """Functionality: Allocate owned device storage. Inputs: self, byte size.
        Outputs: integer device pointer; Logic: track only successful allocation.
        Constraints: one pair of fixed buffers, no unbounded per-frame allocation.
        """
        pointer = ctypes.c_void_p()
        self.check(self.library.cudaMalloc(ctypes.byref(pointer), size), "allocate")
        self.allocations.append(pointer)
        return pointer.value

    def copy(self, destination, source, size, kind):
        """Functionality: Transfer tensor bytes. Inputs: self, pointers, byte size, CUDA copy kind.
        Outputs: memory write or checked exception; Logic: synchronous cudaMemcpy.
        Constraints: call only with owned fixed tensor/device buffers and correct kind 1/2.
        """
        self.check(self.library.cudaMemcpy(destination, source, size, kind), "copy")

    def close(self):
        """Functionality: Release allocations/stream. Inputs: self's successfully owned resources.
        Outputs: checked frees; Logic: remove each after release.
        Constraints: used on initialization failure; normal process termination releases context.
        """
        while self.allocations:
            self.check(self.library.cudaFree(self.allocations[-1]), "free")
            self.allocations.pop()
        self.check(self.library.cudaStreamDestroy(self.stream), "destroy_stream")


class GpuDetector:
    """Functionality: Hold one pinned TensorRT engine and reusable CPU/GPU tensors.
    Logic: enforce manifest/version/shape/FP32, then execute on CUDA stream.
    Constraints: worker serializes calls; engine is trusted local build, no downloads/fallback.
    """

    def __init__(self, engine_path):
        """Functionality: Load verified engine. Inputs: self, absolute engine_path and sidecar JSON.
        Outputs: engine/context/runtime/cuda/names/input/output/buffers.
        Logic: verify engine/source hash and FP32 version/shape before memory registration.
        Constraints: building is offline; FP16/TF32/INT8 are not enabled implicitly.
        """
        import numpy as np
        import tensorrt as trt

        path = Path(engine_path)
        metadata = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        manifest = json.loads(
            (Path(__file__).parents[1] / "models/yolo26n.json").read_text(encoding="utf-8")
        )
        artifact = path.read_bytes()
        if (
            not path.is_absolute()
            or hashlib.sha256(artifact).hexdigest() != metadata["engine_sha256"]
            or metadata["onnx_sha256"] != manifest["onnx_sha256"]
            or metadata["tensorrt"] != trt.__version__
            or metadata["precision"] != "FP32"
            or metadata["tf32"] is not False
            or manifest["model"] != "yolo26n"
            or len(manifest["names"]) != 80
        ):
            raise ValueError("TensorRT engine metadata mismatch")
        self.names = manifest["names"]
        self.runtime = trt.Runtime(trt.Logger(trt.Logger.ERROR))
        self.engine = self.runtime.deserialize_cuda_engine(artifact)
        if self.engine is None or self.engine.num_io_tensors != 2:
            raise ValueError("Invalid TensorRT engine")
        self.input_name, self.output_name = metadata["input_name"], metadata["output_name"]
        for name, shape, mode in (
            (self.input_name, (1, 3, 640, 640), trt.TensorIOMode.INPUT),
            (self.output_name, (1, 300, 6), trt.TensorIOMode.OUTPUT),
        ):
            if (
                tuple(self.engine.get_tensor_shape(name)) != shape
                or self.engine.get_tensor_dtype(name) != trt.float32
                or self.engine.get_tensor_mode(name) != mode
            ):
                raise ValueError("Unexpected TensorRT tensor contract")
        self.context = self.engine.create_execution_context()
        if self.context is None:
            raise ValueError("TensorRT context unavailable")
        self.input = np.empty((1, 3, 640, 640), dtype=np.float32)
        self.output = np.empty((1, 300, 6), dtype=np.float32)
        self.cuda = Cuda()
        try:
            self.device_input = self.cuda.allocate(self.input.nbytes)
            self.device_output = self.cuda.allocate(self.output.nbytes)
            if not self.context.set_tensor_address(self.input_name, self.device_input):
                raise RuntimeError("TensorRT input binding rejected")
            if not self.context.set_tensor_address(self.output_name, self.device_output):
                raise RuntimeError("TensorRT output binding rejected")
        except Exception:
            self.cuda.close()
            raise
        logger.info("Laptop YOLO initialized backend=TensorRT precision=FP32 tf32=false")

    def predict(self, image):
        """Functionality: Detect/annotate one bounded RGB image. Inputs: self and PIL image.
        Outputs: JPEG, boxes, inference_ms; Logic: same CPU transform, GPU execute, same rendering.
        Constraints: timing includes GPU transfers/synchronization; not comparable to kernel-only.
        """
        _, scale, left, top = letterbox(image, self.input)
        # perf_counter uses Windows QPC; monotonic in Python 3.12 can tick only every ~15.6 ms.
        started = time.perf_counter()
        self.cuda.check(self.cuda.library.cudaSetDevice(0), "set_device")
        self.cuda.copy(self.device_input, self.input.ctypes.data, self.input.nbytes, 1)
        if not self.context.execute_async_v3(self.cuda.stream.value):
            raise RuntimeError("TensorRT execution rejected")
        self.cuda.check(self.cuda.library.cudaStreamSynchronize(self.cuda.stream), "synchronize")
        self.cuda.copy(self.output.ctypes.data, self.device_output, self.output.nbytes, 2)
        inference_ms = (time.perf_counter() - started) * 1000
        boxes = decode_boxes(self.output, image.size, (scale, left, top), self.names)
        return annotate(image, boxes), boxes, inference_ms


def get_gpu_detector():
    """Functionality: Initialize explicit GPU engine once. Inputs: LAPTOP_ENGINE_PATH and state.
    Outputs: GpuDetector or safe persistent 503; Logic: lock, cache success/failure until restart.
    Constraints: no downloads, CPU fallback, precision changes or repeated initialization attempts.
    """
    global gpu_detector, gpu_error
    with gpu_lock:
        if gpu_detector is not None:
            return gpu_detector
        if gpu_error is not None:
            raise gpu_error
        try:
            gpu_detector = GpuDetector(os.environ["LAPTOP_ENGINE_PATH"])
            return gpu_detector
        except Exception as error:
            logger.error(
                "Laptop GPU initialization failed exception_type=%s; "
                "check engine hash/metadata and TensorRT/CUDA dependencies",
                type(error).__name__,
            )
            gpu_error = DetectionError("laptop_model_unavailable", "Laptop GPU 模型未就绪。", 503)
            raise gpu_error from error
