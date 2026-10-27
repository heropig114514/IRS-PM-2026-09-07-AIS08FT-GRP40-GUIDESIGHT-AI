# BoT-SORT Tracking and Relative Distance Change

The realtime pipeline is now: existing YOLO26n detection → BoT-SORT association → existing
YOLO26 Depth → existing ROI median fusion → per-ID distance history/EMA/relative speed → TTC/rule
risk → annotation and PRS1 output. This document describes tracking and motion; see [RISK.md](RISK.md)
for the subsequent stateless TTC/risk stage. Distance still comes from metric depth output; it is
not estimated from box size, focal length, or the tracker's Kalman position.

## Pipeline Position and State Ownership

The project detector uses a validated custom CUDA Graph. Therefore, Ultralytics
`BOTSORT.update()` is called after `decode_boxes()`; inference is not replaced with a separate
`model.track()` path. Detection indices returned by the SDK attach IDs to the original boxes. All
detections are retained, including targets whose IDs are not yet confirmed. Tracker-predicted boxes
do not replace original boxes, so depth ROIs, confidence, classes, and detection output remain
unchanged. Detection size 640/confidence 0.25/JPEG 80, the existing NMS/Graph/precision plan, and
depth size 768/FP32 and GPU ROI fusion are unchanged.

`ResidentDetector.object_tracker` is created once during construction; the HF
`AllocatedDetector` is created once within each video-session GPU allocation. Each owns its
`ObjectTracker.tracker` and `ObjectTracker.history`; mutable state is not stored on shared models,
module globals, or per-frame functions. `TrackHistory.tracks[track_id]` stores bounded raw/time/EMA
queues and state-confirmation counts in `TrackState`. One instance processes one video serially;
IDs are meaningful only within that session and `tracking_epoch`.

Tracking is enabled by default for realtime inference. It is disabled by default for independent
photo tests; pass `--track` explicitly to treat all inputs and repeats as one ordered video
sequence. Start another video with a new session/instance or explicitly call
`detector.object_tracker.reset()` between videos; reset also clears GMC, IDs, and distance history,
and increments the epoch. Per-frame `predict()` does not construct or reset the tracker. Do not
share an instance across cameras or combine unrelated photos into one track.

## BoT-SORT and Camera-Motion Compensation

The implementation uses the official `botsort.yaml` bundled with pinned Ultralytics 8.4.171:
`tracker_type: botsort`, `gmc_method: sparseOptFlow`, and `with_reid: false`. Other association
parameters retain that version's YAML values. ByteTrack is not selected and no ReID model is
loaded. See the [official tracking guide](https://docs.ultralytics.com/modes/track/) and
[version-pinned configuration](https://github.com/ultralytics/ultralytics/blob/v8.4.171/ultralytics/cfg/trackers/botsort.yaml)
for GMC and ReID settings.

OpenCV sparse optical flow on the CPU estimates affine transforms between adjacent frames to
compensate for camera motion during association. On the first frame, or without valid features or
transforms, the SDK may use an identity transform; compensation is not guaranteed for all
handheld shake. The project overrides only the GMC call hook for the pinned SDK version so
unexpected GMC exceptions explicitly mark tracking unavailable. Recheck this interface and CMC
tests when upgrading Ultralytics. The new `lap==0.5.12` dependency is explicitly pinned and checked
before importing the tracking SDK to prevent runtime dependency installation.

GMC improves visual association; it does not convert depth-derived speed into target velocity in
world coordinates. The output is distance change relative to the phone: a stationary target may
still be `approaching` while the phone moves toward it. Camera pose estimation and depth-scale
correction are not currently implemented.

## Configuration and Classification

Numeric defaults are centralized in `TrackingConfig` in `realtime/tracking.py`:

| Parameter | Default | Meaning |
|---|---:|---|
| tracker | botsort.yaml | Pinned official BoT-SORT configuration |
| history_size | 8 | Retain up to eight valid distance samples per ID |
| min_valid_samples | 3 | Fewer than three samples produce unknown state and null speed |
| ema_alpha | 0.5 | EMA weight for valid distance |
| stale_timeout_s | 2.0 | Clear history for targets no longer detected |
| motion_history_timeout_s | 2.0 | Clear motion history after this long without valid depth |
| min_time_span_s | 0.1 | Minimum valid-sample time span for speed estimation |
| approaching_threshold_mps | 0.2 | Speed at or below the negative threshold is approaching |
| moving_away_threshold_mps | 0.2 | Speed at or above the positive threshold is moving_away |
| confirmation_updates | 2 | Require two consecutive candidate states to change classification |

EMA is `s = alpha * distance + (1-alpha) * previous_s`; the original `distance_m` is unchanged.
Ordinary least-squares regression `s = a*t + b` uses all valid samples in the window, with real
timestamps in seconds and EMA-smoothed distances; `relative_speed_mps = a`. The implementation
subtracts the first timestamp before computing centered covariance/time variance to avoid losing
precision with large absolute timestamps. It does not use a first-to-last two-point difference.
Negative values mean approaching, positive values mean receding, and threshold equality is
included; values between thresholds are `stationary`.

With fewer than three valid samples or a time span under 0.1 seconds, speed is `null` and state is
`unknown`. Both initial classification and later state transitions require two consecutive valid
regression candidates to agree. Estimation starts with the third valid sample by default, and
classification can be confirmed no earlier than the fourth. A changed candidate restarts the
count. Missing depth does not count or modify the published classification. EMA and hysteresis
introduce delay; the smoothing time scale of a fixed per-sample EMA weight also depends on sampling
frequency.

The existing `TrackHistory` is reused with a new instance API and no additional state manager:

```python
from realtime.tracking import TrackHistory, TrackingConfig

history = TrackHistory(TrackingConfig())  # Create once when the video starts
for timestamp, distance in [(0.0, 5.0), (0.5, 4.5), (1.0, 4.0), (1.5, 3.5)]:
    estimate = history.update_motion_state(7, distance, timestamp)
# estimate: MotionEstimate(3.9375, -0.7125, "approaching")
```

`MotionEstimate` is an immutable scalar snapshot containing smoothed distance, relative speed, and
stable state. Different IDs in one frame may share a timestamp; duplicate or decreasing times for
the same ID raise an error. The pipeline's `TrackHistory.update()` calls the same API and passes
class information to handle class changes; queues are fully independent between IDs. One valid
update performs simple operations on at most eight points (configuration may change the bounded
queue length) and adds no dependency or GPU transfer.

JSON configuration needs to contain only overridden fields, for example `{"history_size": 10}`.
HF can set `PRS_TRACKING_CONFIG=models/tracking.json`; the path is relative to the Space root and
is read once at startup. Supply the file yourself; source releases do not upload custom
configuration or weights. The CLI accepts `--tracking-config <JSON path>` and also requires
`--track`. Unknown fields, invalid parameters, and non-BoT-SORT configurations raise explicit
errors; parameters are not corrected automatically.

By default, timestamps are per-frame inference start times from monotonic `perf_counter()` on the
same worker. Queueing and processing-time variation can affect speed estimation; the current input
protocol does not include camera capture time. Locally, pass real increasing frame times from the
same video through `predict(jpeg, timestamp=seconds)`; do not mix system clocks or timestamps from
different processes. CLI image directories use runtime timestamps and do not read the source
video's FPS, so they cannot measure real offline-video speed accuracy.

## Output and Failures

The original `class_id/label/score/xyxy/class_name/confidence/bbox/distance_m` fields are retained.
The following object fields are added:

```json
{
  "track_id": 7,
  "class_name": "person",
  "confidence": 0.93,
  "bbox": [120, 80, 360, 620],
  "distance_m": 3.5,
  "smoothed_distance_m": 3.9375,
  "relative_speed_mps": -0.7125,
  "motion_state": "approaching"
}
```

The example corresponds to one ID with distances 5.0/4.5/4.0/3.5 m at 0/0.5/1/1.5 seconds.
The JPEG label is `ID 7 | person 0.93 | 3.5 m | approaching | -0.71 m/s`; it retains the raw
distance display. `unknown` does not display speed, even if the third valid sample has an
unconfirmed speed estimate.

- Without an ID, ID/smoothed distance/speed are `null`, state is `unknown`, and the original
  annotation format is used.
- `None/NaN/Inf/zero/negative` depth is not added to history and does not change EMA, regression,
  or confirmation counts. During a brief depth gap, return unexpired smoothed distance, speed,
  and published state as historical estimates. After `motion_history_timeout_s` elapses from the
  last valid depth, clear all motion history; speed/smoothed distance become `null` and state
  becomes `unknown`. Resume accumulation when depth returns. While depth remains valid, retain the
  latest eight samples by count; the fixed two-second timeout is not the whole sample window, so
  slower frames can still contribute if the gap has not expired. A label with an ID but no depth
  retains its original format. A depth-model loading/inference or mapping-contract failure still
  terminates the session; depth is not disabled automatically.
- BoT-SORT's own lost-track buffer handles brief occlusion; predicted boxes without a detection
  are not emitted. Clear disappeared-target history after `stale_timeout_s`, including on frames
  with no detections. This is independent of the valid-depth gap timeout.
- A class change clears that ID's distance history. A new ID starts with empty history; continuity
  with an old ID is not inferred.
- Missing dependencies, an incompatible version, or invalid YAML produce `tracking_unavailable`
  during initialization. An unexpected per-frame association/GMC exception or a frame-size change
  records the exception type and locks `tracking_status: unavailable`. Original detection and depth
  labels continue to be emitted until an explicit reset/new session; there is no retry or tracker
  substitution.
- Non-increasing, negative, or non-finite frame times raise an error to prevent infinite speed.

Frame metadata adds `tracking_model/status/epoch/gmc_method/with_reid/tracking_timestamp_s/tracking_ms`.
The frontend validates and retains the four object fields, accepts an unexpired historical estimate
when raw distance is `null`, and still requires a depth-model identifier when motion-depth fields
are present. It displays the server JPEG and continues to accept the legacy response format. A
complete response with 300 targets may exceed the former 128 KiB metadata limit, so the JSON limit
is now 256 KiB; JPEG remains 1 MiB and the total packet limit is 1,310,728 bytes. Deployment must
update the HF and PRS protocols, frontend, and service argument `--ws-max-size 1310728` together.
This change modified source only and has not been published to the current public service.

## Local Execution and Verification

Install local GPU dependencies and run from `backend`; see the
[test README](model_tests/README.md) for CUDA Torch installation steps:

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pip install -r requirements-depth.txt
.\.venv\Scripts\python.exe -X utf8 -m model_tests.run_depth --track --repeat 6 --output model_tests/outputs/tracking-run-1

# Ordered, same-size, continuous video RGB frames; zero-padded names such as 000001.jpg are recommended
.\.venv\Scripts\python.exe -X utf8 -m model_tests.run_depth --track --input model_tests/datasets/sequence-frames --output model_tests/outputs/sequence-run-1

# Django configuration still requires an existing DJANGO_SECRET_KEY
.\.venv\Scripts\python.exe -X utf8 manage.py test model_tests.test_tracking model_tests.test_cli
```

The following records actual GPU verification of the initial BoT-SORT implementation on
2026-10-10 (using the former first/last-sample difference and initial-confirmation logic). The raw
evidence is retained and is not a GPU performance conclusion for the current regression logic.
Two original pretrained models processed six repeats of the bus image; all five targets retained
IDs 1–5 throughout and were `stationary` from frame four. First-frame initialization made the gap
between inference timestamps exceed the history timeout, so history was accumulated again without
resetting the tracker. Input hash, original detection-output hash, and all original boxes/classes/
confidence/raw distances matched the earlier `bus-run-2` exactly. The detection Graph initialized
only on the first frame. Non-first-frame `tracking_ms` ranged from 10.46 to 29.22 ms (median
14.77 ms); full `predict` ranged from 107.98 to 201.17 ms. The first frame took 7.38 seconds,
including original Graph/depth-predictor initialization. Additional work was CPU image conversion,
optical flow, association, and a small scalar history; no GPU model was added and dense depth was
not copied to the CPU—the original distance vector is still returned. These six-frame smoke-check
measurements do not establish continuous-video FPS or compare against historical HF performance.
Results are in `model_tests/outputs/tracking-smoke-20261010/`; the summary is
[local-gpu.json](benchmarks/2026-10-10/tracking/local-gpu.json).

`model_tests.test_tracking` includes real-SDK ID continuity, occlusion, and translated-frame GMC
tests, plus checks for bounded history, three motion states, insufficient samples, invalid depth,
expiry cleanup, class/ID changes, explicit reset, exception lockout, labels, the 300-target
protocol, and worker lifecycle. Integration tests attach real BoT-SORT to the original `predict()`
path with controlled detection output and 4.8/4.3/3.7/3.2 m depth images. They verify approaching
on frame four, preservation of the motion estimate without valid depth on frame five, and binary
protocol round trips. Additional cases check noise, irregular times/large timestamps, isolation
between IDs 7 and 9, inclusive thresholds, consecutive state confirmation, and brief depth gaps/
valid-depth timeout. Controlled depth does not establish real-world distance accuracy. A web-only
environment without the GPU SDK skips real-SDK checks; the local machine has the SDK installed and
executed those tests.

Historical delivery checks for the initial BoT-SORT version: 119 backend and 67 frontend tests
passed, with no real-SDK checks skipped. `manage.py check`,
`makemigrations --check --dry-run`, full `ruff check .`, `pip check`, and `git diff --check` passed.
Comments and declaration/variable indexes in modified code were reviewed manually, with AST used
to cross-check Python declaration names. The project has no `tools/check_docs.py`; no complete
automated documentation check is claimed. Annotated JPEGs were inspected; the original layout is
retained for dense boxes, so long labels may overlap. No Git commit was created and no external
service was deployed.

Handheld real-world video, HF allocation/public frontend deployment, long-duration performance,
distance/speed ground truth, and ID-switch rate remain unverified. With ReID disabled, long
occlusion, rapid turns, and crossing similar targets may still change IDs. Monocular-depth noise
and background within a box can affect speed. No model was trained and no data split, random seed,
or evaluation metric was changed.

## Motion-Trend Improvement and Verification

On 2026-10-10, all 124 backend and 67 frontend tests passed. The 26 tracking/motion tests include
requested scenarios A–I and real BoT-SORT/CMC tests; no real-SDK checks were skipped. Integration
tests use controlled depth images and do not establish physical speed accuracy. Django checks,
migration dry-run, full Ruff, and diff checks passed. Changed comments and declaration/variable
indexes were reviewed manually, with AST used to confirm Python names and documentation fields.
The CPU scalar check made 100,000 calls by updating 100 IDs for 1,000 consecutive frames, taking
about 0.57 ms per batch of 100 IDs on average. Each track retained only eight samples. This timing
excludes detection, depth, tracking association, and rendering and is not video FPS. This round did
not rerun real-model video, phone, or external HF verification; no commit or deployment was made.

Files changed in this round: `realtime/tracking.py`, `core/detector.py`, `model_tests/test_tracking.py`,
`SystemCode/frontend/live-detection-client.js`, `SystemCode/frontend/tests/live-detection-client.test.mjs`,
`TRACKING.md`, `model_tests/README.md`, `PHONE-LIVE.md`, and `SystemCode/frontend/README.md`. No dependency
was added; the model, BoT-SORT/depth implementations, and raw-distance calculation were unchanged.

## Initial BoT-SORT File Changes (Historical)

| Category | Files (relative to backend, except frontend paths) |
|---|---|
| Tracking and inference | `realtime/tracking.py` (added), `realtime/detector.py`, `core/detector.py`, `zerogpu/live_worker.py`, `zerogpu/app.py` |
| Protocol and frontend | `realtime/protocol.py`, `SystemCode/frontend/live-detection-client.js` |
| CLI and automated tests | `model_tests/run_depth.py`, `model_tests/test_tracking.py` (added), `model_tests/test_cli.py`, `core/test_depth.py` (documentation updated), `SystemCode/frontend/tests/live-detection-client.test.mjs` |
| Dependencies and deployment configuration | `requirements-depth.txt`, `zerogpu/requirements.txt`, `deploy/prs-camera.service`, `tools/deploy_phone_live.py`, `tools/deploy_space.py` |
| Documentation | `TRACKING.md` (added), `model_tests/README.md`, `README.md`, `MCP.md`, `DEPTH.md`, `PHONE-LIVE.md`, `zerogpu/README.md`, `SystemCode/frontend/README.md` |
| Verification evidence | `benchmarks/2026-10-10/tracking/local-gpu.json` (added) |

Generated locally and Git-ignored: `model_tests/outputs/tracking-smoke-20261010/run.json`,
`results.jsonl`, and `annotations/bus.jpg/0000.jpg` through `0005.jpg`. Existing historical results
were not overwritten.
