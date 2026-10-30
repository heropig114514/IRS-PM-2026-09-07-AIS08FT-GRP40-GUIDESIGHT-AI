# Selectable Object-Distance Modes

`distance_mode` defaults to `depth`, preserving the original YOLO26 detector, YOLO26-Depth model, and ROI median algorithm. Added modes reuse the existing detector, BoT-SORT, motion estimation, risk assessment, annotations, and binary interface. Web/integration forwards requests; the GPU process selects the distance mode at startup. Restart the inference process/session after changing modes.

## Data Flow and Output

| Mode | Distance stage | Depth weights/calls | Downstream |
|---|---|---|---|
| A `depth` | Median of positive finite depth inside each original-frame bounding box | Load once; call on every frame | Existing tracking, motion, TTC, risk, and Guidance |
| B `geometry` | `fy_px * object_height_m / bbox_height_px` | Not read, loaded, initialized, or called | Same distance fields |
| C `temporal_depth` | Run A's fusion on Depth frames; use Last Valid Distance by Track ID on other frames | Load once; call on the first frame and every N frames | Same distance fields; cached values are not new motion measurements |

Shared path: JPEG → original detection/unletterboxing → BoT-SORT association → selected distance strategy → existing motion history → original TTC/risk → annotated JPEG and object metadata. Tracking may be disabled for independent CLI photos; mode C requires it. Detector model, detection precision, input size 640, confidence 0.25, JPEG quality 80, and depth model size 768/FP32 remain unchanged.

Objects retain `class_id/label/score/xyxy`; distance output retains `class_name/confidence/bbox/distance_m`; tracked objects also retain `track_id` and other motion/risk fields. Units are meters. Missing distance is JSON `null`; no substitute `distance` field is added. Modes B/C also set `distance_source` to `geometry`, `depth`, `cached`, or `unavailable`. Mode A's object fields are unchanged. Mode B frame metadata uses `distance_estimator: pinhole-height-v1` and does not declare `depth_model`. The browser adds validation for the selected strategy identifier only; Guidance, speech/vibration scheduling, and risk rules do not need modification.

## Configuration

Centralized defaults are in [distance_config.py](realtime/distance_config.py); the editable full JSON example is [models/distance-config.json](models/distance-config.json). The file's default example still selects mode A:

```json
{
  "distance_mode": "depth",
  "depth_interval": 3,
  "focal_length_px": 700.0,
  "reference_width_px": 640,
  "object_heights_m": {
    "person": 1.7, "bicycle": 1.1, "motorcycle": 1.2,
    "car": 1.5, "bus": 3.2, "truck": 3.0
  }
}
```

Select a configuration file explicitly with `--distance-config` or `PRS_DISTANCE_CONFIG`. Precedence is centralized defaults < JSON file < environment variables < CLI arguments. Relative paths in `PRS_DISTANCE_CONFIG` resolve from the backend root; explicit CLI file paths resolve from the current terminal directory. `PRS_DISTANCE_MODE` overrides the mode; `PRS_DEPTH_INTERVAL` overrides the interval for C. Invalid modes, non-positive/non-integer intervals, invalid focal lengths/heights, and unknown JSON parameters raise errors directly.

`focal_length_px` is the **vertical focal length fy** for reference image width `reference_width_px`, not a focal length in millimeters. Because bounding boxes belong to the current decoded frame rather than the detector's letterboxed square image, the effective value is
`fy_current = focal_length_px * frame_width / reference_width_px`.
The calibrated camera intrinsic `K[1,1]` can be used directly by setting the reference width to the calibration image width. Scaling assumes the frame retains its aspect ratio and is not cropped; cameras that crop or rotate should use calibration parameters for the corresponding output image. The default 700-pixel focal length and class heights are experimental assumptions; pose, vehicle model, occlusion, and truncation affect mode B's distances. Classes absent from the height table and invalid, empty, or out-of-bounds boxes return `null`; other class heights are not guessed. The height table replaces the entire dictionary, allowing an experiment to explicitly restrict participating classes.

## Run Modes A/B/C Separately

Run from `backend`; `$gpuPython` selects the interpreter with the pinned CUDA inference dependencies installed. Existing split deployments typically use `.venv-inference`; a combined development environment may use the existing `.venv`. This feature requires no new dependencies. The output directory must not already exist.

```powershell
$gpuPython = '.\.venv-inference\Scripts\python.exe'

# A: original mode; omitting --distance-mode also defaults to A
& $gpuPython -X utf8 -m model_tests.run_depth --distance-mode depth --track --repeat 9 --output model_tests/outputs/abc-a

# B: explicitly configure focal length/class heights; Depth weights are not required
& $gpuPython -X utf8 -m model_tests.run_depth --distance-config models/distance-config.json --distance-mode geometry --track --repeat 9 --output model_tests/outputs/abc-b

# C: call Depth on frames 1, 4, 7, ...
& $gpuPython -X utf8 -m model_tests.run_depth --distance-mode temporal_depth --depth-interval 3 --track --repeat 9 --output model_tests/outputs/abc-c

# C with Depth on every frame, using the same measurement/fusion and downstream algorithms as A
& $gpuPython -X utf8 -m model_tests.run_depth --distance-mode temporal_depth --depth-interval 1 --track --repeat 9 --output model_tests/outputs/abc-c1
```

`depth_interval=2/3/5` runs inference every 2/3/5 frames and applies only to C; A always runs Depth on every frame. `--input <directory-of-consecutive-same-size-RGB-frames>` can replace the bundled sample; ordering and the CLI clock retain their original implementation. Run different photos/cameras separately. Repeated static images check the interface only and do not establish video-motion accuracy.

The local browser live/video entries use the same configuration. Set the environment variables in PowerShell before startup, for example:

```powershell
$env:PRS_DISTANCE_CONFIG = 'models/distance-config.json'
$env:PRS_DISTANCE_MODE = 'geometry'       # A: depth; C: temporal_depth
$env:PRS_DEPTH_INTERVAL = '3'             # C can use 1, 2, 3, or 5
.\.venv\Scripts\python.exe -B -m tools.run_local_mvp
```

Prepare the separate environment as described in [LOCAL-MVP.md](LOCAL-MVP.md), then open `/live/` or `/integration/video/`. The launcher passes these process environment variables through. Mode B preflight requires detector weights only; A/C still require both weight files. Starting `integration.inference.asgi:application` separately reads the same environment variables. The inference worker does not read Django `.env`; set process variables or pass a configuration file explicitly. The mode remains fixed for the worker's lifetime to prevent distance, motion-cache, and risk sources from being mixed within a session.

HF Space uses the same environment variables; A/C continue to require the original `PRS_DEPTH_WEIGHTS`. Mode B does not construct a DepthEstimator even if that variable is present. If HF has no depth weights configured and B/C is not selected, the existing detection-only behavior remains; C fails at startup without depth weights. The source-staging allowlist includes the new module and JSON file; this feature does not deploy or set remote variables automatically.

## Mode C Temporal Cache

`TemporalDepthCache` stores `track_id -> TemporalDepthEntry`, with fields `last_distance`, `last_depth_frame`, `timestamp`, and `class_name`. It does not retain dense depth maps. Only positive finite distances from Depth frames are written; non-Depth frames read the most recent valid value. If the current Depth frame is invalid, the output is `null` while the previous valid record remains available to later non-Depth frames. New targets, targets without IDs, and targets without a measurement produce `null` until the next Depth frame.

IDs absent from the current frame are removed immediately; if they reappear, they must wait for a new measurement. A class change clears the corresponding entry. An explicit tracker reset changes the epoch. A new session or frame-size change clears the entire cache and restarts with a Depth frame. A tracker failure returns empty IDs and clears the cache. Cache size depends only on the number of currently visible targets. Performance counters also reset when the tracker epoch changes.

Cached values are marked `distance_source=cached`. The motion module still receives the original object interface, but cached values are not appended to the distance/time sample queue; this prevents repeated stationary copies from creating false velocity measurements. The original motion-history timeout remains active: if valid Depth measurements are more than the default 2 seconds apart, state may return to `unknown` until enough new measurements accumulate. Risk consumes the current distance and existing motion estimate under the original rules; mode C's cache introduces response lag. Interpolation, velocity prediction, and Kalman filtering are not implemented and can be added through the cache/strategy interface.

## Performance Records and Experimental Boundaries

The original meanings of `inference_ms/forward_event_ms/depth_ms/tracking_ms/risk_ms/server_ms` are retained. The following per-frame fields are added:

| Field | Scope |
|---|---|
| `detection_ms` | JPEG decoding, preprocessing, detection, and bounding-box decoding, including first-frame Graph setup |
| `distance_ms` | Depth inference/fusion for A; geometric calculation for B; depth/cache stage for C |
| `end_to_end_ms` | Worker time from decode to completed annotation; same as `server_ms` |
| `fps` | `1000 / end_to_end_ms`, per-frame processing rate |
| `depth_frame` | Whether the Depth model was actually called for this frame |
| `depth_interval` | Actual strategy interval: A=1, B=0, C=configured N |

`performance` provides `total_frames/depth_model_calls/actual_depth_inference_ratio`, `average_detection_ms/average_distance_ms/average_end_to_end_ms/fps`, and `depth_frame_count/non_depth_frame_count/average_depth_frame_ms/average_non_depth_frame_ms`. Aggregate FPS is `1000 * total_frames / sum(end_to_end_ms)`, not the mean of per-frame FPS. Empty groups have `null` average duration. Because C always runs Depth on the first frame, the call ratio for a finite sequence is `ceil(frames/N)/frames`; for a long sequence it approaches `1/N`.

Statistics use only constant-size counts and sums; they add no per-frame logging or I/O and do not exclude cold-start frames. Existing GPU synchronization boundaries remain: detection is timed when the runner returns CPU results, and depth fusion when it returns a distance scalar. Here, end-to-end means server pipeline time and excludes network, input pacing, model loading, metadata validation, disk writes, and browser display. Actual browser duration/delivered FPS remain covered by existing frame-pipeline statistics.

CLI stores each frame in `results.jsonl.metadata` and the final aggregate summary in `run.json.performance`. Effective strategy/camera parameters and model hashes are also written to `run.json`. Mode B's depth hash and path are `null`. Accuracy comparisons require independent distance ground truth; a single-image smoke test and call count do not prove physical accuracy or sustained-video FPS.

## Implementation and Checks

`realtime/distance.py` encapsulates geometry, `TemporalDepthCache`, and strategy scheduling. `realtime/distance_config.py` manages configuration; `realtime/performance.py` manages statistics. `ResidentDetector` delegates only the original depth stage to the strategy; the algorithm in `realtime/depth.py` is unchanged. Local/ZeroGPU constructors and the CLI select whether to load the Depth model based on mode. Local handshake/browser validation adds explicit B/C branches while retaining the original detector identity, tracking, risk, and capture-clock requirements.

From `backend`, run `python manage.py check`, `python manage.py makemigrations --check --dry-run`, `python manage.py test`, and `ruff check .`; from `frontend`, run `node --test tests/*.test.mjs`. `core.test_distance_modes` checks formulas/scaling/missing values, compatibility of A's fusion, C intervals 1/2/3/5, old/new ID isolation, epoch and geometry resets, separation of cache from motion samples, no Depth loading in B, protocol behavior, and statistics. Real GPU, phone, HF, and ground-truth experiments require separate validation; unit tests do not replace them.
