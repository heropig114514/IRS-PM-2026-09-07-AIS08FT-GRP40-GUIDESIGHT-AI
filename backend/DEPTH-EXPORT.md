# Depth forward export and acceleration

## Responsibility and boundaries

`tools.benchmark_depth_export` exports the current YOLO26 depth checkpoint and compares its
device-resident forward pass using PyTorch eager, the existing PyTorch CUDA Graph probe, ONNX
Runtime CUDA and TensorRT. `tools.depth_export_runtime` contains explicit serial experiment
adapters. Default resident/PRF2 services do not import these adapters.

The workflow preserves depth `imgsz=768`, batch 1, FP32, disabled TF32, the released checkpoint's
calibration buffers, and SDK preprocessing/postprocessing. It uses the existing max640/JPEG80
benchmark fixture transformation, independently of the service's maximum accepted JPEG size.
There is no new random seed, dataset split, accuracy threshold, automatic retry or CPU fallback.

The pinned SDK's native depth head returns calibrated depth at one quarter of the tensor's spatial
resolution. Its ordinary export mode adds a bilinear 4x resize. This tool exports the warmed,
GPU-fused **native eval module with `head.export=False`**, using static ONNX opset 17. It avoids
comparing extra resize work against native forward. The unchanged SDK postprocessor performs
input-size interpolation, letterbox removal and original-frame alignment for every backend.

## Environment

Use a supported CUDA GPU, Python 3.12, the native environment's Torch `2.8.0+cu126` and Ultralytics
`8.4.171`. Install the optional dependencies from `requirements-depth-export.txt` into a separate
environment. The local workflow uses `.venv/depth-export` and an explicit `.pth` link to the
existing native environment's site-packages, preserving the service environment:

```powershell
# Run from backend. Create this directory only if it does not already exist.
.\.venv\Scripts\python.exe -m venv .venv/depth-export
$nativePackages = (Resolve-Path .venv/Lib/site-packages).Path
Set-Content .venv/depth-export/Lib/site-packages/native-runtime.pth $nativePackages
.\.venv\depth-export\Scripts\python.exe -m pip install --retries 0 -r requirements-depth-export.txt
```

The linked environment must remain available. For a self-contained server environment, install
the pinned native Torch/Ultralytics dependencies and export requirements together, then regenerate
artifacts on that server. A GPU engine is not a portable substitute for the checkpoint.
Keep `YOLO_OFFLINE=1`, `YOLO_AUTOINSTALL=0`, and writable `YOLO_CONFIG_DIR`, `MPLCONFIGDIR`, `TEMP`
and `TMP` directories. TensorRT's Python wheel supplies the build API; no `trtexec` is required.
Torch is imported before the ONNX session and its CUDA/cuDNN DLL directory is loaded explicitly.

## Export, build and measurement

Stop this project's owned inference processes before measuring to avoid competing GPU work.
Use local, trusted weights and a **new** artifact directory and report filename. Existing outputs
are never overwritten by an implicit rebuild. The following uses the existing public fixtures:

```powershell
$fixtures = @(
  'model_tests/samples/bus.jpg',
  'models/experiments/coco8/images/val/000000000036.jpg',
  'models/experiments/coco8/images/val/000000000042.jpg',
  'models/experiments/coco8/images/val/000000000049.jpg',
  'models/experiments/coco8/images/val/000000000061.jpg'
)
.\.venv\depth-export\Scripts\python.exe -B -m tools.benchmark_depth_export `
  --weights models/yolo26n-depth.pt --inputs $fixtures `
  --artifacts models/experiments/depth-export/current-weights `
  --output benchmarks/depth-export/current-weights.json --phase all
```

`--phase export` creates checked ONNX files and a manifest. `--phase build` requires that manifest
and creates engines once; `--phase benchmark` validates and reuses complete artifacts. Each phase
requires a fresh report filename and the same explicit weights/inputs. Inspect failures before
selecting a subsequent phase; there is no built-in retry. `all` records independent backend
failures while completing the other requested comparisons and returns a nonzero exit status.
Progress is appended to a `.jsonl` sidecar; the final `.json` is published only when the process
finishes. Read complete JSONL lines while running, and inspect manifests after their phase ends.
This avoids replacing a report held open by a Windows reader. Failed attempts remain separate files.

`--stream default` retains the original caller schedule. `--stream dedicated` is an explicit
comparison: PyTorch and TensorRT use the same dedicated Torch CUDA stream. ONNX Runtime owns its
internal stream and uses explicit completion boundaries. Use a new report filename for each run.
TensorRT warns that default-stream execution may insert additional synchronization; compare the
complete measured runs rather than assuming a gain from the warning alone. Stream selection does
not change input tensors, arithmetic precision or sample counts.

One artifact is created per unique **actual SDK-preprocessed shape**. Images are not forced square.
TensorRT uses strongly typed FP32, disabled TF32/FP16/INT8, a 1 GiB builder workspace and no auxiliary
streams. The workspace limit bounds builder resources; it is not a guarantee of total process VRAM.
ONNX Runtime uses only CUDA kernels, disables CPU execution fallback and TF32, and uses I/O binding
with an ORT-owned compute stream. The caller's producer stream completes before submission, and
`synchronize_outputs()` completes bound outputs before return. Local Windows ORT 1.23.2 diagnostics
reproduced a native abort when destroying sessions borrowing a nonzero Torch stream, even after
explicit synchronization; that borrowing path is not offered. Both adapters allocate owned output
tensors, avoid host tensor copies during forward,
and reject incompatible shapes, types, devices, stream changes and extra inference options.

## Contracts and evidence

The artifact manifest binds checkpoint, ONNX and engine SHA256 hashes; SDK depth-head/predictor
source hashes; Python, Torch, CUDA, cuDNN, ONNX, ONNX Runtime and TensorRT versions; GPU model and
compute capability; FP32 policy; calibration values; static input/output shapes; and build settings.
The tool verifies provenance before loading an artifact. Standalone adapter callers must perform
the same `validate_manifest` check. These adapters support serial calls only, retain their runtime
objects. TensorRT requires input lifetime through completion and synchronization before host or
cross-stream access. ONNX calls complete synchronously; `close()` explicitly releases the session
and subsequent calls are rejected. There is no leaked-session workaround or automatic backend swap.

Reports retain all 30 steady calls after one separately reported first measured call, backend setup
cost, ONNX export and TensorRT build cost. The first measured call is after SDK/backend setup; it is
**not process-cold startup**. CUDA events measure the stream interval, including launch gaps;
synchronized wall time measures submit-to-completion. For ONNX, the caller-stream event interval
includes the explicit wait for the ORT-owned stream; it is not isolated ONNX kernel time.
Neither includes image preparation, model
loading, depth alignment, detection, tracking, drawing or transport. These are forward measurements,
not video throughput or end-to-end service latency.

Every fixture receives eager-before and eager-after controls. Reports compare complete raw and
aligned maps, retain finite/nonfinite counts, exact equality and absolute/relative error distributions,
and verify that a later mirrored-input call preserves the previous output. Mirroring is a separate
buffer/numerical diagnostic, not a replacement for the original performance inputs or a new dataset.
No observed error is silently converted into an accuracy pass. In particular, faster FP32 kernels
can still differ numerically because of fusion or operation ordering.

Machine-readable evidence is under [benchmarks/2026-10-26/depth-export](benchmarks/2026-10-26/depth-export).
Model binaries, serialized engines and local environments remain ignored; reports and source are
versioned. Follow [the optimization plan](INFERENCE_OPTIMIZATION_PLAN.md) for service-level and
ground-truth validation requirements.

## Final weights and deployment

When final fine-tuned weights arrive, preserve the old checkpoint and artifacts, verify the new
checkpoint SHA256, choose a new artifact directory, and rerun export, build and numerical/forward
validation on the target GPU. Calibration is exported from the actual head, never assumed to be
identity. A changed checkpoint or environment invalidates old engine bindings.

Before default deployment, use the team's fixed depth/distance truth data and evaluation protocol,
then validate the actual video service, variable input-shape policy, output ownership, resource
budget and sustained concurrency. This workflow does not authorize default backend selection,
precision reduction, reduced depth frequency or a looser accuracy criterion. Shape changes require
explicitly prepared artifacts; there is no dynamic build or eager fallback in a request.

## Implementation references

- [Ultralytics depth task and output contract](https://docs.ultralytics.com/tasks/depth)
- [ONNX Runtime CUDA provider, stream binding and TF32](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html)
- [TensorRT Python installation](https://docs.nvidia.com/deeplearning/tensorrt/latest/installing-tensorrt/install-pip.html)

The installed SDK source is authoritative for the pinned implementation. Published GPU/FP16
performance numbers are not local FP32 measurements and are not used as a baseline.

The pinned ONNX Runtime CUDA [provider](https://github.com/microsoft/onnxruntime/blob/v1.23.2/onnxruntime/core/providers/cuda/cuda_execution_provider.cc)
and [stream implementation](https://github.com/microsoft/onnxruntime/blob/v1.23.2/onnxruntime/core/providers/cuda/cuda_stream_handle.cc)
distinguish provider-owned and external streams. Synchronizing a caller event alone does not establish
completion on an unrelated nonblocking stream; the adapter therefore waits on its bound outputs.
