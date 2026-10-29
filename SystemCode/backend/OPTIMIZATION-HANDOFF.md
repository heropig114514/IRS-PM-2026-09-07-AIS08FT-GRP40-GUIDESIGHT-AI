# Inference optimization handoff

## Responsibilities and package layout

This handoff contains the complete tracked optimization branch, its history, developer methods,
dependency pins, tests, public fixtures and benchmark evidence. The Git bundle is self-contained;
the accompanying delivery manifest binds its revision and every checked-out file to SHA256.
The separate checkpoint payload supplies the original detection and depth weights that are
ignored by Git. Already tracked optional walk weights remain in the source tree.

The server delivery directory contains `source.bundle`, `manifest.json`, `source/`, `weights/`,
this entry document and a verification receipt. `source/backend/` and `source/frontend/` retain
the optimization branch's layout. The colleague's newer main branch uses `SystemCode/backend/`
and `SystemCode/frontend/`; account for that relocation when integrating changes.

Credentials, private environment files, SSH keys, local virtual environments, temporary control
scripts and Windows/RTX3060 TensorRT engines are excluded. Reports may identify historical engine
paths; those paths are evidence, not usable Linux artifacts. Uploading this package does not
install dependencies or start inference on the Web server.

## Deployment topology and ownership

The public HTTPS entry is `https://47.239.50.129/mvp/maps/`. The Ubuntu22.04 host runs Nginx and
`prs-mvp-web`, which listens on loopback18766. It has no detected NVIDIA GPU. The original inference
route is `ws://127.0.0.1:18767/ws/infer/`, supplied by the colleague's separately owned GPU worker.
Restoring this route does not start that worker or create its reverse tunnel.

The temporary operator-laptop bindings18768/18769 and local GPU8767 are no longer required for
this handoff. The user requested their removal and explicitly accepted waiting for the colleague's
original worker. Do not silently redirect to the laptop, CPU inference or HF if18767 is offline.
Actual service state and cleanup evidence belong in `benchmarks/2026-10-28/server-handoff/` and
the server delivery receipt, not in this operating guide.

Keep the current Web release and its prepared bilingual speech features. A whole-tree replacement
with this branch would lose independent colleague changes. Limit compatible Web overlays to
capture-profile wiring and the three camera transport modules. An independent release switch can remove an overlay; integrate
the changes into the authoritative release source before depending on automatic deployments.

## Implemented optimization map

| Concern | Implementation and method | Boundary |
| --- | --- | --- |
| Browser upload queue | `frontend/frame-pipeline.js`, `live-detection-client.js`, `camera.js`; cancellable send-buffer admission before live capture and separate queue diagnostics | Native buffer emptiness is not remote receipt; keep frame window, JPEG policy and deadlines; explicit ungated pipeline control retained |
| Small TCP writes | `integration/relay.py`, `integration/phone_deploy.py`; TCP_NODELAY on owned local-peer and SSH sockets | New tunnel process required; no propagation/bandwidth guarantee, automatic retry or provider fallback |
| Resident model reuse | `realtime/services.py`, `zerogpu/runtime.py`; one native execution owner, reused detection model/graph and session-local tracking | One worker per GPU; native work drains before ownership is released |
| Depth forward | `realtime/depth_runtime.py`, `realtime/depth.py`; static-shape TensorRT FP32 engine pool and native predictor adapter | Preserve depth edge768, calibration and SDK alignment; reject stale/unsupported engines |
| Export and numerical comparison | `tools/benchmark_depth_export.py`, `tools/depth_export_runtime.py`; Torch, graph, ONNX CUDA and TensorRT controls | ONNX remains an experiment; no automatic backend selection |
| Depth ROI fusion | `PRS_DEPTH_FUSION=host`; existing host partition reduction in modeA | Depth forward still uses GPU; no reduced depth frequency or precision |
| Optional detector | `PRS_DETECTION_MODEL=yolo26n-walk`, model manifests and `WALK-MODEL.md` |87 classes are a separate evaluation group; default remains original80-class yolo26n |
| Frame admission | `frontend/camera.js`, `integration/assist.py`; explicit `bounded-2` profile |30FPS capture ceiling, two outstanding frames, unchanged640/JPEG0.8 and first-frame gate |
| SSH relay | `integration/relay.py`; continue a writable partial send immediately after progress | Bounded polling when blocked; preserve all bytes, full duplex and half-close |
| Browser/video validation | Frontend benchmark tools and `benchmarks/` | Retain first-frame cost, errors, numerical differences and exact conditions |

No native FP16/INT8 quantization claim is made by this handoff. TensorRT FP32 kernel fusion can
still produce small numerical differences. No change to risk rules, confidence thresholds, model
class definitions, accepted-frame retention or service deadlines is implied by the network profile.

## Authoritative methods and evidence

- [Depth export and deployment](DEPTH-EXPORT.md): native-quarter-resolution export, target identity,
  input shapes, engine lifecycle, CUDA stream ownership and final-weight replacement.
- [Resident service](RESIDENT.md), [local integration](LOCAL-MVP.md), [phone operation](PHONE-MVP.md):
  process ownership, PRF1 versus PRF2, explicit backend selection and failure boundaries.
- [Depth modes](DISTANCE-MODES.md), [model selection](WALK-MODEL.md), [model manifests](models/README.md):
  original baselines and independent detector/configuration choices.
- [Forward evidence](benchmarks/2026-10-26/depth-export/summary.json): forward-only controls, failed
  attempts, first/steady timings and numerical differences; these are not end-to-end FPS.
- [Whole-video depth evidence](benchmarks/2026-10-26/depth-integration/summary.json) and
  [walk/fusion evidence](benchmarks/2026-10-27/walk-fusion/summary.json): same-input before/candidate/
  after comparisons, exact/approximate outputs and real service probes.
- [Full local browser evidence](benchmarks/2026-10-28/demo-browser/summary.json) and
  [phone network evidence](benchmarks/2026-10-28/phone-latency/acceptance-summary.json): capture,
  encode, transport, inference and decode, with network failures preserved.

In the matched90-frame desktop/LAN fixture comparison, reducing the queue from12 to2 while
retaining the30FPS ceiling reduced steady P95 from291.36ms to77.76ms (73.3%), with approximately
27.5 delivered FPS in both runs. Two60-frame motion fixtures delivered approximately26–27FPS
with P95 of86–88ms. These exclude phone radio/CPU/display and the public relay.
The same-fixture SSH controls measured approximately30–36% lower median transport/inference
round trip for the partial-write change. Public browser trials still showed approximately10s
first-frame latency and early termination; this package does not certify a stable phone demo.

## Preparing the actual GPU host

For teammate-owned local testing without this Web relay, follow
[LOCAL-QUICKSTART.md](LOCAL-QUICKSTART.md). It provides standard-library preparation, explicit
Torch comparison profiles and the same checked native supervisors on Windows/Linux.

Use the exact trusted checkpoints and verify them against `manifest.json` and repository model
manifests before loading them. Copy the separately delivered `weights/yolo26n.pt` and
`weights/yolo26n-depth.pt` into the selected checkout's `backend/models/` directory. Do not replace
existing different checkpoints; keep versions separately. The optional walk checkpoint is already
tracked. Fine-tuned final weights require a new hash, export directory, numerical comparison and
deployment acceptance; old engines do not become valid by renaming a checkpoint.

The target needs a supported NVIDIA driver, CUDA-capable Torch and Python3.12. The source includes
`requirements-inference.txt`, `requirements-inference-tensorrt.txt` and
`requirements-depth-export.txt`. The original validated environment was Windows/RTX3060 Laptop;
Ubuntu package availability, driver compatibility and target-GPU behavior must be checked on the
actual GPU host. Do not install a GPU stack on47.239.50.129 merely because it stores this package.

On the selected Linux GPU host, the explicit environment preparation pattern is:

```bash
cd /path/to/source/backend
python3.12 -m venv .venv-gpu
.venv-gpu/bin/python -m pip install --retries 0 torch==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cu126
.venv-gpu/bin/python -m pip install --retries 0 -r requirements-inference-tensorrt.txt -r requirements-depth-export.txt
```

These are deployment instructions, not evidence that a Linux GPU installation has been executed.
Preserve the existing worker/environment while preparing a separate candidate. Export on that
GPU using representative portrait, landscape and widescreen fixtures; one image cannot establish
all supported input shapes. The existing workflow records actual SDK-preprocessed tensor shapes:

```bash
.venv-gpu/bin/python -B -m tools.benchmark_depth_export \
  --weights models/yolo26n-depth.pt \
  --inputs model_tests/samples/bus.jpg /path/to/landscape.jpg /path/to/widescreen.jpg \
  --artifacts models/experiments/target-gpu-fp32-NEW \
  --output benchmarks/target-gpu-export-NEW.json --phase all
```

Select real existing fixture paths. Preserve failures and use fresh output directories. Inspect
the manifest, every backend result and numerical differences before selecting TensorRT. Do not
copy local serialized engines or edit their identity fields to bypass validation.

## GPU service activation

Run the timestamped PRF2 worker, not the different resident-v1 entry, for the existing phone Web
gateway. The following Linux command expresses the selected deployment after target validation:

```bash
export LOCAL_MVP_MODE=assist
export LOCAL_WEB_ORIGIN=http://127.0.0.1:18766
export PRS_DETECTION_MODEL=yolo26n
export PRS_DISTANCE_MODE=depth
export PRS_DEPTH_BACKEND=tensorrt
export PRS_DEPTH_FUSION=host
export PRS_DEPTH_ENGINE_MANIFEST=models/experiments/target-gpu-fp32-NEW/manifest.json
export YOLO_OFFLINE=1
export YOLO_AUTOINSTALL=0
.venv-gpu/bin/python -B -m uvicorn integration.inference.asgi:application \
  --host 127.0.0.1 --port 8767 --workers 1 --lifespan on \
  --ws websockets-sansio --ws-max-size 1310728 --ws-max-queue 12 \
  --ws-per-message-deflate false --no-proxy-headers
```

Use an approved supervisor and the existing pinned SSH relay workflow to map the GPU host's
loopback8767 to server loopback18767. `tools.run_phone_mvp` documents environment/ownership
preflight and interactive authentication; run its `--check` first in the target's prepared
environment. Do not bind the inference process publicly or run another tunnel over an occupied
colleague-owned port. Start a new relay process to load the partial-write optimization.

Set `PRS_CAMERA_PROFILE=bounded-2` on the Web service only after its template and camera module
contain the matching implementation. Unset profile remains the original `standard` control.
Retain the current canonical origin and public URL. Check `/health/`, the public PRF2 handshake,
actual returned model/engine identity, unsupported-shape errors, stop/drain and idle release.
Health alone does not load the lazy model or prove real inference.

## Acceptance and unresolved constraints

Repeat same-model Torch-before/TensorRT/Torch-after video comparisons and the GPU/host fusion
controls described in `DEPTH-EXPORT.md` on the actual GPU. Keep checkpoints, fixture frames,
timestamps, depth resolution, precision, quality and deadlines fixed. Validate a real phone
separately. Street-video consistency does not replace held-out distance truth or87-class quality
data supplied by the modeling colleague.

Exact source revisions, automated checks, clean-copy preparation and deployment receipts belong
in the structured reports under `benchmarks/`. Preflight and checksum verification do not establish
inference speed, physical-phone behavior or successful GPU deployment on another machine.

The phone previously could not reach the public IP from either Wi-Fi or cellular, while proxy
HTTPS and the host Web services were healthy. Restoring the original GPU route does not resolve
that independent reachability problem. Cloud security-group/public-access rules and upstream
routing still require verification by the server operator; no phone certificate installation,
implicit provider fallback or longer inference timeout is a substitute.
