# YOLO26 metric depth and object fusion

The insertion point is `ResidentDetector.predict()` after unchanged object detection and
inverse-letterbox box decoding, before JPEG annotation. `AllocatedDetector` inherits this method,
so native CUDA and HF ZeroGPU use the same fusion code. Echo remains an image-byte diagnostic.

Depth is explicitly enabled by supplying a separate `DepthEstimator`. Its local `.pt` weights
are deserialized once, then activated once per CUDA allocation and reused for every frame.
HF loads its CPU model at application startup before the live allocation; its predictor/device
state lives in the allocated worker. No model is loaded inside the frame loop. Missing configured
weights stop startup; a failed depth frame stops the session, with no detection-only fallback.

The existing pinned Ultralytics **8.4.171** supports the depth task. Use the official
`yolo26n-depth.pt` from [Ultralytics assets](https://github.com/ultralytics/assets/releases).
Provision the weights locally before starting; this integration does not download them implicitly.
Existing detection weights, COCO evidence, 640 letterbox, confidence0.25, precision/Graph plan
and JPEG80 remain unchanged. Depth independently uses768px, FP32 and the official predictor.
In this pinned version the depth predictor removes its own padding and scales to the original
image ([source](https://github.com/ultralytics/ultralytics/blob/v8.4.171/ultralytics/models/yolo/depth/predict.py)).
Any remaining full-frame map resolution mismatch is resized on GPU using nearest interpolation
to preserve invalid values. Incorrect source identity, empty/channel/batch maps or CPU outputs
are errors; blindly stretching a raw padded network output is not permitted.

`get_object_distance(depth_map, bbox)` accepts an aligned floating NumPy array or Torch tensor.
It clamps original-frame xyxy coordinates, covers fractional pixels using floor/ceil,
filters NaN/Inf/zero/negative values, and returns the statistical median in meters.
An even sample count averages its two central values. Empty/reversed/nonfinite/outside ROIs
and ROIs with no valid samples return `None`. Malformed maps raise `depth_map_size`.
`fuse_object_depth()` preserves existing box fields, adds canonical aliases and `distance_m`,
and transfers only one vector of object distances from GPU; the dense map stays on GPU.
Frames and depth maps remain in memory. Logs contain error types/shapes/counts, not image data.

Ultralytics documents `result.depth.data` as metric depth and its released weights as calibrated
([official documentation](https://docs.ultralytics.com/tasks/depth/)). These are predicted
camera-axis depths, not guaranteed tape-measure distances or slant ranges. Bounding-box ROIs
can include background/occluders. Camera/domain accuracy must be measured separately; no
automatic calibration, focal-length/bounding-box heuristic, filtering or alternative model
is introduced. Old detection-only FPS/latency/COCO records do not validate fused-depth speed
or distance accuracy.

## Native CUDA example (PowerShell)

From `backend`, use an isolated environment. Install the appropriate CUDA Torch build first;
the example below selects CUDA12.6 and the existing HF Torch/Torchvision versions:

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pip install torch==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cu126
.\.venv\Scripts\python.exe -X utf8 -m pip install -r requirements-depth.txt
```

Place the original verified `yolo26n.pt` and official `yolo26n-depth.pt` in `backend/models/`.
Both binaries are Git-ignored. This workspace already has both weights and the CUDA environment.
The reusable CLI explicitly uses the native detector's original FP32 control (not the production
compiled-FP16 benchmark), constructs both models once, and preprocesses source images through
the existing maximum-edge640/JPEG80 camera contract. No Django server or secret is needed.

```powershell
# Bundled public image: no dataset download needed
.\.venv\Scripts\python.exe -X utf8 -m model_tests.run_depth --output model_tests/outputs/bus-run-3

# Explicit source image and repeats; the first frame is retained
.\.venv\Scripts\python.exe -X utf8 -m model_tests.run_depth --input model_tests/samples/bus.jpg --output model_tests/outputs/bus-repeat-1 --repeat 3

# Every image in a directory, recursively and deterministically
.\.venv\Scripts\python.exe -X utf8 -m model_tests.run_depth --input model_tests/datasets/my-photos --output model_tests/outputs/photos-run-1

.\.venv\Scripts\python.exe -X utf8 -m model_tests.run_depth --help
```

`--output` must be a new directory outside the input directory. Inputs support
jpg/jpeg/png/bmp/webp; point only at RGB files, never a dataset root containing depth/label PNGs.
The CLI does not download assets, train, skip failed files, retry, shuffle, sample or select a
different production plan. It requires actual CUDA, the pinned dependencies and verified
original detection weights. `--depth-weights` can explicitly select another compatible YOLO26
Depth checkpoint; `--weights` does not bypass the detector's original manifest verification.
There is no automatic CPU or detection-only fallback.

Outputs are `run.json` (status, settings, source/model hashes), `results.jsonl` (one record per
image/repeat with boxes, distance, timings, source/frame dimensions and input JPEG hash), and
`annotations/<source-filename>/<repeat-index>.jpg`. Boxes use resized frame coordinates.
Existing outputs are not overwritten; errors return exit code1 and retain any partial run.
`predict_wall_ms` covers the complete predict call, including first-use initialization, but
excludes checkpoint loading, input preprocessing and file saving. It is not a video FPS metric.
The bundled image has no ground truth: this CLI exports predictions, not accuracy scores.
The entry point, sample, datasets and all previous local outputs now live in `model_tests/`.
Direct execution from that folder is also supported: see [test README](model_tests/README.md).
Public datasets, sample-size guidance and future comparison protocols: [DATASETS.md](model_tests/DATASETS.md).

Depth uses the pinned Ultralytics/Torch/Torchvision/NumPy/Pillow/OpenCV packages;
the subsequent BoT-SORT integration adds pinned `lap==0.5.12` to both GPU dependency profiles.
The web relay still needs no Torch or depth weights.
`requirements-depth.txt` adds the existing GPU packages to a native web/vision environment.

## Existing HF video path

Provision `models/yolo26n-depth.pt` in the Space (for example with HF's file upload/LFS),
then set the Space variable `PRS_DEPTH_WEIGHTS=models/yolo26n-depth.pt` and restart it.
Relative paths resolve from the Space application root; absolute local paths also work.
An empty/unset variable explicitly keeps the existing detection-only mode.
The source staging allowlist includes `realtime/depth.py`; it does not upload model binaries
or set remote variables. This change does not publish or configure the live Space automatically.

Deploy the updated HF source, PRS `realtime/protocol.py`, and
`frontend/live-detection-client.js` together before enabling depth. Maximum JSON metadata is
now256KiB to carry300 fused/tracked boxes and their legacy aliases; the JPEG limit remains1MiB.
The service template/MCP command use `--ws-max-size 1310728` (8+256KiB+1MiB).
All55s session/cold budgets,10s steady budget,30FPS input ceiling and12-frame window remain
unchanged; added inference cost can lower achieved FPS or trigger those existing limits.

Open `/live/`, select **Hugging Face ZeroGPU**, and start the camera. Returned JPEG labels
include `person 0.93 | 2.4 m`, or `person 0.93 | N/A`. Object metadata is additive:

```json
{
  "class_id": 0,
  "label": "person",
  "score": 0.93,
  "xyxy": [120, 80, 360, 620],
  "class_name": "person",
  "confidence": 0.93,
  "bbox": [120, 80, 360, 620],
  "distance_m": 2.4
}
```

Frames also include `depth_model`, `depth_units: "m"` and `depth_ms` (box decoding plus
depth inference/fusion). Detection `inference_ms`/`forward_event_ms` remain detection-only;
`server_ms` includes depth and tracking; `tracking_ms` separately measures association/history.
Real-time tracking adds IDs after box decoding, before depth, and history after fusion; see
[TRACKING.md](TRACKING.md). With a valid ID/depth the label now includes ID and motion, e.g.
`ID 7 | person 0.93 | 2.4 m | unknown`. Independent CLI stills retain the earlier labels by default.
Missing ROI data is JSON `null`, never NaN/zero. Model-loading,
inference and map-contract failures have stable `DetectionError` codes in the worker;
the existing live transport closes failed sessions and sends its safe generic service error.

## Verification

From `backend`, run `python manage.py check`,
`python manage.py makemigrations --check --dry-run`, `python manage.py test`, and `ruff check .`
using the configured interpreter and an explicit `DJANGO_SECRET_KEY`. Frontend verification:
`node --test ../frontend/tests/*.test.mjs`.
`core.test_depth` covers numerical filtering, even medians, clipping, map failures, model lifetime,
same-frame integration, labels,300-object packets, worker handoff and deployment closure.
`model_tests.test_cli` covers deterministic input discovery, model reuse, original/resized
coordinates, hashes/JSONL/annotations, output protection and partial failure without retry.
Torch tests are optional in the web-only test environment; actual tensor/device checks run
when Torch/CUDA are installed. Synthetic tests do not verify physical distance accuracy,
HF deployment, phone-camera behavior or video performance.

On2026-10-10, local verification passed92 backend tests (including actual CPU/CUDA tensor
checks),64 frontend tests, Django check, migration dry-run, full Ruff and `pip check`.
A real RTX3060 Laptop CUDA12.6/Torch2.8.0/Ultralytics8.4.171 smoke test processed the official
bus image three times using both real checkpoints. Five objects had positive finite distances;
one person was3.69m and the bus5.25m. The depth model/predictor and detection Graph were reused;
Graph initialization occurred only on the first frame. Detection raw-output SHA and all legacy
box fields matched a detection-only control using the same resident graph. Original-frame depth,
annotated JPEG and binary result envelopes passed real processing/round-trip checks.
Evidence: [local-gpu.json](benchmarks/2026-10-10/depth/local-gpu.json).
The annotated preview is now located at `model_tests/outputs/annotated.jpg` (Git-ignored).
Depth-stage times for the three frames were10.71s/104.22ms/16.35ms; these include initial
predictor setup and are smoke-test observations, not a video FPS benchmark. No HF deployment,
phone hardware, sustained video or ground-truth distance validation was performed.

The terminal CLI was also run on2026-10-10 with the bundled source and `--repeat 2`:
`model_tests/outputs/agent-cli-verification-20261010/` contains the preserved completed run, two JSONL
records and two annotated JPEGs. Both frames returned the same five objects and distances;
person3.69m and bus5.25m. Complete predict wall times were7.41s and119.32ms, with initial
Graph/predictor setup retained. This verifies the real command-line path, not physical accuracy.
After adding the CLI, all97 backend tests and64 frontend tests passed, together with Django
check, migration dry-run, full Ruff, `pip check` and `git diff --check`. Changed code-file
comments and declaration/variable indexes were manually reviewed; no documentation checker
exists in this project. Changes have not been committed or deployed.
