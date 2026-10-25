# 2026-10-10 TTC and Rule-Based Risk (Local Verification)

A stateless TTC calculation and configurable five-factor risk score were added after the existing
motion estimate; no second `TrackState` or model was introduced. Distance still comes from existing
depth, and TTC uses smoothed meters divided by valid negative speed. If the current frame lacks
depth, TTC is empty and risk is unknown while existing motion history is retained. Output includes
direction, TTC, score/level, alert flag/priority, and explanatory reasons; no TTS or navigation was
added. Only high/critical annotations add concise TTC and level text; protocol fields and the
256 KiB metadata limit are unchanged. Defaults, JSON configuration, entry points, changed files,
and boundaries are in [RISK.md](RISK.md).

The 142 backend/70 frontend tests, Django checks, migration checks, Ruff, and diff checks passed;
comments and indexes were reviewed manually. On a local RTX 3060, the dual-model six-frame run
kept stable IDs for all five targets. Original detection hashes/boxes/raw depths matched
`bus-run-2`; all targets ended stationary with no alerts. Median `risk_ms` was 0.233 ms for five
targets. The CPU rule microbenchmark averaged 0.844/4.165 ms for 100/300 targets, excluding
models, drawing, and transport. Evidence: [local-validation.json](benchmarks/2026-10-10/risk/local-validation.json).

Handheld real-world video, external HF/PRS services, and risk accuracy against ground truth were
not verified. No commit or deployment was made, and no dependency was added.

# 2026-10-10 Distance Motion-Trend Improvement (Local Test)

The existing `TrackState`/`TrackHistory` is reused. After EMA smoothing, speed is estimated by
linear regression over all valid samples using their actual timestamps. Estimation starts with at
least three samples; both initial classification and transitions require two consecutive valid
candidates to agree. Brief depth gaps retain historical estimates; motion history is cleared only
after the separate `motion_history_timeout_s` (two seconds by default) elapses since the last
valid depth. The new instance API is `update_motion_state(...)->MotionEstimate`, and labels add
confirmed signed speed in m/s. Detection, depth, BoT-SORT/CMC, raw distances, and protocol fields
are unchanged; no dependency or GPU transfer was added. See [TRACKING.md](TRACKING.md) for the
current method, configuration, and accuracy boundaries. GPU timing below describes the initial
implementation.

This round passed 124 backend/67 frontend tests and Django/migration/Ruff/diff checks; comments
and indexes were reviewed manually. The pure-CPU motion logic averaged about 0.57 ms per 100 ID
updates, excluding detection, depth, and tracking association. Real phone/HF/model-video
verification was not repeated, and no commit or deployment was made.

# 2026-10-10 BoT-SORT and Distance Motion State (Local Verification)

Ultralytics BoT-SORT was added after decoding the original detection boxes, with `sparseOptFlow`
camera-motion compensation enabled and ReID disabled. Each video session's detector owns one
tracker/history. After depth fusion, up to eight valid samples are retained per ID and smoothed
with EMA; speed thresholds and consecutive confirmation produce
`approaching/moving_away/stationary/unknown`. Original detection/depth parameters and GPU fusion
are unchanged; dependency `lap==0.5.12` was added. No TTC or risk score was added.

On an RTX 3060, a real dual-model six-frame check kept five IDs stable throughout and ended with
stationary states. Original detection hashes/boxes/distances matched `bus-run-2` exactly. The
non-first-frame tracking stage took 10.46–29.22 ms; this does not establish phone-video FPS. JSON
limit is 256 KiB, JPEG 1 MiB, and total packet limit 1,310,728 bytes. A release must coordinate
HF/PRS/frontend and service parameters. See [TRACKING.md](TRACKING.md) for configuration, clock
limits, test coverage, and remaining boundaries.

The 119 backend/67 frontend tests, Django check, migration dry-run, Ruff, pip check, and diff checks
passed. Comments and declaration/variable indexes were reviewed manually; real-SDK CMC/occlusion/
ID-continuity checks ran. No commit or deployment was made. Real phone motion, HF allocation,
long-term frame rate, and distance/speed accuracy remain unverified.

# 2026-10-10 Optional YOLO26 Depth Integration (Local Verification)

Independent metric-depth inference and ROI-median fusion were added after decoding existing
detection boxes and before JPEG annotation. The original detection model/parameters/image
contract remain unchanged; the depth model is loaded from explicit weight configuration and reused
across video frames. A local RTX 3060 three-frame dual-model verification passed, with original
detection-output hashes and box fields matching detection-only execution. The 97 backend/64
frontend tests and Django/migration/Ruff/dependency checks passed. HF/PRS were not deployed, and
physical devices, long-term frame rate, and distance accuracy were not verified; historical speed
measurements below cover detection mode only. See [DEPTH.md](DEPTH.md) for enablement and
verification records. Current tracking fields raise the metadata limit to 256 KiB; both protocol
ends must be updated together at release.

Local tests are organized under `model_tests/`. From `backend`, run
`python -m model_tests.run_depth --output model_tests/outputs/bus-run-3`.
The default input is `model_tests/samples/bus.jpg`. The tool supports a single image, a recursive
RGB directory, and an explicit repeat count; it saves annotated images, per-frame JSONL,
input/weight hashes, and run status. Two predictions completed through the real local CLI and
detected five objects, with the pedestrian at about 3.69 m and bus at about 5.25 m. Full `predict`
took 7.41 s/119.32 ms, including first-call initialization. This example has no distance ground
truth. See [DATASETS.md](model_tests/DATASETS.md) for public detection/depth/instance datasets and
comparison protocols. The current script exports predictions only and has not calculated
accuracy against ground truth, downloaded large datasets, or changed existing evaluation
splits/metrics. Datasets go under `model_tests/datasets/`; existing results such as `bus-run-1`
and `bus-run-2` were moved unchanged into `model_tests/outputs/`. See the
[test README](model_tests/README.md) for terminal and directory usage.

# 2026-10-07 HF Measurement, 30 FPS Release, and Startup-Transport Fix

The current public page has been coordinated to use 30 FPS input, a 12-frame in-flight window,
and first-frame transport warmup. A 601-frame independent short run through Chrome → public PRS →
real HF ZeroGPU → Chrome delivered every frame, at **29.5357 FPS steady state**. The 600
non-first frames covered 20.3144 seconds; capture-to-decode P95 was 317.335 ms and maximum was
442.6 ms. The original acceptance criteria of at least 10 FPS and no non-first frame over 500 ms
passed. The 11,047.8 ms first frame is reported separately; no startup frame or slow frame was
excluded. Input remains a canvas MediaStream from the original public bus image. The run includes
real 640 resizing/JPEG80 encoding, TLS/WS, result validation, ordered display, and `image.decode`,
but excludes phone sensors, mobile networks, and physical screen rendering. The conclusion applies
only to this short run; it does not establish performance for a full 55-second session, long-term
stability, diverse moving scenes, or phones.

All three video-inference calls in this round retained the original model, compiled FP16 CUDA
configuration, 640/confidence 0.25/JPEG80, the same Chrome 154.0.8037.98, public input, and
original 500 ms criterion. Six comparison phases totaling 606 frames ran within one allocation:
24/8, 30/8, 30/12, 30/12, 30/8, and 24/8. Completion-interval FPS for 30/8 was 26.400/26.565;
for 30/12 it was 30.029/30.113, with no window waits in either phase. This demonstrates that an
eight-frame window constrained 30 FPS at roughly 300 ms round-trip latency. The original short
phase metric still includes pipeline refill/drain; original means for the two 30/12 phases were
28.461/28.527 and must not be replaced by the alternate interval metric. All 606 frames were
delivered, but the opening 24/8 phase reached 704.9 ms, so the overall original latency acceptance
still failed.

The default 30/12 was then retained for a 601-frame startup check in an independent session.
Profiling informed a fix, followed by a rerun under the same conditions:

| Independent Session, Both 30/12, 601/601 Frames | Before Fix | After Fix |
|---|---:|---:|
| Overall FPS after first frame, including all steady-state pauses |29.1610|29.5357|
| Capture-to-decode P95 |312.34 ms|317.33 ms|
| Maximum capture-to-decode |712.2 ms|442.6 ms|
| Non-first frames over 500 ms |12 frames (index 1–12)|0 frames|
| First frame, including allocation/restore/Graph/warmup |9711.4 ms|11047.8 ms|
| Original full-delivery/≥10 FPS/500 ms maximum acceptance |Failed|Passed|

Before the fix, slow frames clustered in the first 12 frames after startup, with most waiting in
the PRS↔HF segment. HF internal processing took about 10 ms per frame, and GPU forward about
0.69 ms. Based on same-gateway TCP/CPU profiling and 512 KiB warmup comparisons retained from
2026-10-06, strict negotiation of `warmup_bytes=524288` was added. Once HF has its first detection
result ready, it sends a random 512 KiB `PRW1` control message on the existing WS; PRS returns the
same bytes, and HF releases the first frame only after exact equality is confirmed. This occurs
once, totaling 1 MiB in both directions. Incorrect/duplicate confirmation, missing control, and a
three-second timeout fail explicitly. There is no implicit fallback/retry, new GPU call, or video
frame drop. The control does not use image sequence numbers or frame counts; existing five-second
worker idle, 55-second session, and 55-second cold-wait budgets are unchanged. Measured warmup of
1,661.77 ms is fully included in the first-frame time, and its duration/byte count remain in first
frame metadata. P95 did not improve; maximum startup latency did. This short run does not establish
that all future network tail latency has disappeared.

After the fix, mean segments were: capture/encode 7.39 ms, full PRS↔HF return 238.74 ms
(including 9.67 ms HF local cycle and 0.690 ms model forward), browser decode 3.05 ms, and total
capture-to-decode 302.77 ms. PRS/HF/GPU intervals are nested and cannot be added. `worker_pull`
includes waiting for the next sample and is not computation time. Predictions and output JPEG
SHA256 matched exactly before/after, with no repeated Graph initialization during steady state.
This single-image fingerprint comparison is not a new COCO AP evaluation.

Final HF commit: `3d2182c41aaf3f9cde5d20c380c5214c7ba1be8b`; PRS release:
`live-tuning-dd6bfeb8deedfbe9`. The new `live-tuning` deployment configuration covers only camera
scheduling/transport and tests. Model metadata was validated byte-for-byte; GPU computation,
speech/vibration/maps, MCP configuration, interpreter, systemd/nginx/env, and CPU 20%/256 MiB/32
task limits were retained. The original HF commit, two preceding PRS releases, and all failure/
comparison reports remain available. No local Git commit was created; single-commit atomicity is
not claimed.

Evidence is under `benchmarks/2026-10-07/fps-live/`: `final-summary.json` is the summary;
`paired-browser.json`, `independent-browser.json`, and `warmup-browser.json` preserve all per-frame
data. Deployment manifests, source hashes, quota records, and `provider-release-evidence.log`
confirm three workers independently processed and delivered 606/601/601 frames. Final HF health
reported `active_session=false`. Account quota usage was 94.631748 seconds and three video calls,
leaving 205.368252 seconds/five calls. Two source releases also triggered existing startup-cache
preparation, but before the first video call the account still had 300 seconds/eight calls. Source
release counts are not video-call counts. No further inference performance testing was done.

Checks: 117 backend and 61 frontend tests passed; Django check, migration dry-run, Ruff on changed
Python files, and diff checks for the changed scope passed. Remote Django/migration/pip checks,
ASGI import, health, 11-file hashes, and service-proxy configuration hash checks passed. Full-repo
Ruff still reports historical Kaggle-v11 Notebook I001/E501 issues. Full-repo diff also reports
trailing whitespace in unchanged `i18n/maps-page` files. systemd validation retains the existing
system snapd `RestartMode` notice. The project has no `tools/check_docs.py`; function/module
comments and declaration/variable indexes were reviewed manually, and no automated documentation
check is claimed.

Retesting consumes quota; first pass the gate for 90 seconds of free quota and at least one call.
Commands:

```powershell
.\.venv\Scripts\python.exe -m tools.check_zerogpu_quota --gpu-budget-seconds 90 --output <NEW_QUOTA_REPORT.json>
$env:PRS_PLAYWRIGHT_PATH='<absolute path to the local Playwright package>'
node ../frontend/tools/benchmark_phone_browser.mjs https://47.239.50.129/prs/live/ 601 <NEW_FRAME_REPORT.json>
```

## Commit-Snapshot Verification (2026-10-07)

After the user requested a commit, the Git index was exported independently for checks. The
snapshot passed 80 backend and 61 frontend tests, Django check, migration dry-run, full-snapshot
Ruff, and syntax checks for two browser benchmark scripts. Another 37 historical untracked
experiment tests remained in the workspace; no tracked tests were deleted or disabled, so this
scope differs from the 117-test full-worktree results above. See `fps-live/commit-validation.json`.
The CI reference to a deleted detector script was also corrected, and the two browser benchmark
tools actually used, public sample, free-quota gate, and complete per-frame data from this round
were included. The earlier "no commit" statement described the state at benchmark completion;
implementation, comments/indexes, tests, and documentation were subsequently committed together.
Online HF/PRS code was not redeployed and no additional GPU call was used. Confirm remote CI
results separately after pushing.

# 2026-10-06 30 FPS Candidate Adjustment and Zero-GPU Browser Verification

The local candidate was changed to 30 FPS input and 12 total jobs. Default `FramePipeline`
sampling and the shared client/PRS/HF-parent window were adjusted together; ordered validation,
display-held credits, independent first-frame completion, and error-stop semantics were retained.
Model, weights, 640, batch 1, threshold 0.25, JPEG80, FP16 compiled Graph, and FP32 output are
unchanged. No frame dropping or warmup was enabled in this round. The original 24/8 source
snapshot and prior real-HF per-frame records are retained. A 19-file HF candidate was staged in a
local content-addressed directory but was not released and did not trigger an HF build. Public
production remains at 24/8, commit `2f5dd14e730afe7c94d4bb3a98766e5f4bd6cfb4`.

The quota API explicitly rejected another GPU test: 106.879660 seconds remained, but free calls
were zero because of `free_run_limit_exhausted`. Two quota reads agreed; this round made zero GPU
calls and deducted zero caller seconds. This does not establish that real HF detection reached 30
FPS, and no paid resource or alternate identity was used to bypass the call limit.

Desktop Chrome 154 ran CameraApp capture, JPEG80 encoding, HUD, and ordered decoding; only
transport was explicitly replaced with a 300 ms or 380 ms timer. The fake camera used a 30 FPS
MediaStream from the same public image, and replies were original JPEGs, not new-model detections.
There was no video WS or HF inference. These are browser-pipeline capacity controls, not public
model benchmarks. The first two groups had 846 frames each; the final quota-saving entry-point
recheck had 606 frames. All 2,298/2,298 frames were delivered, with no slow frames dropped.

| Explicit CPU Control | Result-Completion Interval FPS (two runs) | Actual Capture FPS (two runs) | Credit Waits |
|---|---:|---:|---:|
| Final 300 ms, 30/8 | 25.190/25.192 | 25.131/25.162 | 64/73 frames, about 552/549 ms |
| Final 300 ms, 30/12 | **29.984/29.949** | **30.001/30.000** | **0/0** |
| Fixed 380 ms stress, 30/12 | 29.685/29.663 | 29.589/29.619 | 20 frames each, about 62/55 ms |

The two final 300 ms 30/12 phases delivered all 302 frames, with maximum capture-to-decode of
341.4/340.4 ms and peak in-flight count 11. The original short-run FPS metric remains
28.369/28.381, including pipeline refill/drain; interval rate does not replace the original
acceptance metric. With encoding/decoding added, the fixed 380 ms delay produced about 395 ms
end-to-end and the 12-frame window approached the occupancy needed for 30 FPS, so slight jitter
caused credit waits. This control deliberately models persistent high latency and is not equivalent
to the P95 where only a few frames were slow on the earlier real chain. The results support
30-FPS feasibility around 300 ms and show limited margin for a 12-frame window near 400 ms. The
original real-HF startup frame at 688.5 ms and its failed 500 ms threshold were neither eliminated
nor relabeled as passing.

The benchmark tool adds an explicit `--30fps` balanced order: 24/8, 30/8, 30/12, 30/12, 30/8,
24/8. Final frame counts were 61/91/151/151/91/61, totaling 606. Each 30/12 phase retains 150
non-first frames; shorter comparison phases limit later HF time. Earlier 846-frame records and
the exact run-source snapshot remain, so condition changes are visible. Each phase has an
independent first frame and full drain; sequence numbers remain continuous across the connection.
The original short-run metric and the new completion-interval rate/capture rate/credit waits are
recorded separately.

Before starting media/provider, the entry point checks the loaded client's window. Running
`--30fps` against the public eight-frame page produced an explicit preflight rejection with empty
phases and `GPU_calls=0`; it did not consume an allocation before failing during a 12-frame phase,
and it did not reduce the test window automatically. The CPU entry point for the 12-frame candidate
source passed. The final entry-point change only clarified scope text; values, scheduling, and
protocol remain unchanged. Each report and run-source SHA is retained.

Future real-HF testing needs only one allocation, but first confirm a 90-second reservation and at
least one free call, then coordinate the 12-frame contract across HF parent, PRS relay, and client.
Do not update just one side; this round was staged but not released. From `frontend`, run
`node tools/benchmark_phone_windows.mjs https://47.239.50.129/prs/live/ OUTPUT --30fps`; the
current public eight-frame version will reject it at preflight. Reproduce without GPU using
`--echo-delay 300 --30fps` or `--echo-delay 380 --30fps`; the tool loads and records SHA values for
the three local candidate modules. There is no automatic retry, account switching, or scheduling
of future GPU tasks. Actual phone frame rate, wireless networks, and long-duration end-to-end
performance remain to be verified.

Evidence: `benchmarks/2026-10-06/30fps/adjustment/adjustment-summary.json`; the same directory
contains full per-frame CPU reports, public preflight rejection, before/after source snapshots,
HF candidate manifest, production fingerprint, and quota readings. Checks: 108 backend and 49
frontend tests passed, along with Django, migration dry-run, changed-Python Ruff, and JS syntax
checks. Full Ruff reports only the historical Kaggle-v11 Notebook I001/E501 issues. The repository
has no `tools/check_docs.py`; function descriptions and declaration/variable indexes were checked
manually. No Git commit was made and single-commit atomicity is not claimed.

# 2026-10-06 30 FPS Stage Diagnosis and First-Hop Measurement

Review of the current public runtime source still shows 24 FPS sampling and eight total in-flight
jobs, with SHA matching the previous release. Production sampling/window/deployment were not
changed and no HF GPU was called. The diagnostic tool adds explicit `--rate`; the default 24 FPS
and original 93.5 KB/eight-frame/two-independent-connection conditions are retained. New tests
explicitly use 30 FPS and do not replace existing 24 FPS results.

Two size-matched binary controls ran in desktop Chrome 154 on the existing public HTTPS page and
`/prs/ws/echo/` endpoint. Each 121-buffer set was checked byte-for-byte and in order; all 242/242
buffers returned. The test did not use HF, a model, JPEG capture/decode, physical phone wireless,
or screen rendering. Each packet was 93,500 bytes in both directions; test upload was slightly
larger than the original 84,682-byte image request. TLS/CSP remained enabled.

| Explicit Idle Condition after First Packet | Actual Send FPS | Completion-Interval FPS | Non-First-Packet Median Latency | Non-First-Packet Maximum Latency |
|---|---:|---:|---:|---:|
| No idle | 29.9236 | 30.1992 | 48.30 ms | 95.30 ms |
| 10-second idle | 29.9899 | 30.2391 | 47.35 ms | 95.40 ms |

Completion intervals slightly above 30 reflect a short-window difference caused by lower endpoint
latency; the conclusion is about 30 FPS, not throughput above the supply rate. First packets of
213.8/200.7 ms are reported separately. The size-matched controls used about 22.38/22.43 Mbps in
both directions, indicating the current desktop-browser↔PRS first hop can carry about 30 FPS of
traffic. This does not establish 30 FPS for a real phone or the full HF detection path.

Per-frame recomputation on the existing real-HF session found a median full PRS↔HF interval of
238.251 ms, including a 13.192 ms HF-local frame-to-result-validation cycle. Browser JPEG capture
was 6.90 ms, decode 3.05 ms, and end-to-end median 296.70 ms. For each frame, subtracting its PRS
upstream/admission duration from `transportMs` gives a median residual of 46.935 ms for the first
hop, PRS re-packetization/send, and browser validation, consistent in scale with independent Echo
results. This is a difference between durations, not a subtraction of cross-host absolute
timestamps and not pure network RTT. Nested median intervals cannot all be added directly.

At 30 FPS, the supply interval is 33.33 ms. Measured local GPU-cycle and browser encode/decode
service times have headroom. Direct constraints are first the 24 FPS input cap and then the
eight-credit limit for the full pipeline: an occupancy of about 300 ms suggests roughly 9–10
in-flight slots; the earlier P95 of about 381.265 ms corresponds to roughly 12. Twelve is a future
candidate window, not a released parameter, and does not establish that the 688.5 ms startup slow
frame is resolved. Longer HF round trips combined with a finite window can trigger application
backpressure; stable RTT alone must not again be called an inevitable FPS bottleneck.

At the original JPEG size, 30 FPS requires about 20.324 Mbps upload and 22.440 Mbps return.
Previous private CPU controls through the same gateway with a large window returned complete
results at 102.51–106.44 FPS, supporting available capacity, but they are not 30-FPS detection
benchmarks on the production Space. Current evidence primarily supports configuration/in-flight
capacity limits; it has not shown that the raw bandwidth ceiling is the constraint.

Future real 30-FPS verification should adjust sampling and the PRS/HF/client window protocol
together and explicitly compare 24/8 with 30/12. Retain the model, size 640, threshold, JPEG80,
and output. Preserve startup slow frames, full-duration timing, and dropped-frame counts. Transport
warmup/frame-drop strategies must be recorded as separate conditions; the original full-delivery
test will not be silently changed to meet the 30-FPS target.

Full results are in `benchmarks/2026-10-06/30fps/diagnosis-summary.json` and the raw first-hop
per-frame JSON in the same directory. Tool JS syntax, 48 frontend/108 backend tests, Django check,
and migration dry-run passed. Full Ruff still reports only the original Kaggle-v11 Notebook
I001/E501. The repository has no `tools/check_docs.py`; changed function descriptions and
declaration/variable indexes were reviewed manually. Full-chain 30 FPS on real HF, long-term
stability, and physical phones remain unverified. No Git commit was made.

# 2026-10-06 Throughput Attribution Correction: Stable RTT and Pipeline Backpressure

Stable RTT around 200 ms should not, by itself, reduce throughput at 24 FPS: if stage capacity,
link throughput, and the in-flight window are sufficient, propagation latency mainly increases
single-frame response time. The earlier statement that "transport contributes the most to
single-frame latency" meant it was the "largest FPS bottleneck," conflating two different metrics.
This work recomputed saved real-HF per-frame records directly, with zero GPU calls and no changes
to runtime source, model, window, experimental parameters, or acceptance thresholds.

The implementation already uses an asynchronous pipeline: except for independent first-frame
completion and a full window, `FramePipeline` does not wait for each result before capturing the
next frame. PRS receive, upstream send, and downstream return run separately. Browser credit is
released only after validation and ordered decode/display, so the window must be configured using
**full capture-to-display occupancy**, not just TCP RTT. At 24 FPS and about 302 ms end-to-end
occupancy, an average of about 7.25 in-flight jobs is needed; five credits cause backpressure,
while eight are roughly sufficient. At stable 200 ms with no other overhead, the average is about
4.8; serial service time and bandwidth at each stage must also meet the supply rate.

Recomputation used the same HF allocation, images/model/JPEG80 conditions and excluded only each
phase's independent first frame:

| 24 FPS Condition | Capture-Interval FPS | Result-Completion-Interval FPS | Median Frame Latency | Credit Wait |
|---|---:|---:|---:|---:|
| Five-frame window | 16.5197 | 16.5117 | 301.95 ms | 78 frames, 2,214.1 ms total |
| Eight-frame window | 23.9794 | 23.9678 | 302.45 ms | 1 frame, 3.5 ms |

For the five-frame group, 119 capture intervals ideally took 4,958.33 ms but actually took
7,203.50 ms, an excess of 2,245.17 ms. Of this, 2,214.1 ms came from explicit `creditWaitMs`, about
98.6%. The eight-frame group took 4,962.60 ms, close to the ideal cadence. This is
**application-level backpressure caused by limited concurrency**; increasing the window did not
reduce single-frame propagation latency.

The original short-run metric, named `steady_fps`, is `(total frames−1)/(last completion−independent
first-frame completion)`. Although first-frame cold time is reported separately, this metric still
includes post-first-frame pipeline refill and startup pauses, so it cannot directly be treated as
a sustained steady-state output rate. Keep the original metric and acceptance unchanged; also
record completion-interval rate for the same non-first-frame set using `(K−1)/(last completion−first
non-first-frame completion)`. If endpoint latency varies, this metric can also be affected by
bursts of returned outputs. Interpret it alongside capture intervals, full-run slow frames, and
stable periods; do not select the higher value to replace the acceptance metric.

For a new 181-frame session, the denominator of the original 22.1880 FPS metric was 8,112.5 ms:
0.7 ms from first-frame completion to the next capture, 7,817.5 ms of non-first-frame capture
span, and 294.3 ms from final-frame capture to decode. The 179 capture intervals ideally took
7,458.33 ms; the actual total was 359.17 ms longer, with 354.8 ms of explicit credit waits.
Waits occurred at frames 10/12/16/148: the first three totaled 353.3 ms and the last only 1.5 ms.
The primary cause was capture pausing after the startup transient when the window filled. The
previously reported frame 12–181 diagnostic range—23.8697 FPS capture, 23.8714 FPS completion—is
retained with its original scope/limitations. Sustained throughput is therefore close to 24 FPS;
remaining issues are the 688.5 ms slow frame caused by startup network queueing/window ramp-up and
the subsequent exhaustion of eight credits. The original 22.1880 FPS report, frames 2–9 over
500 ms, and failed acceptance remain unchanged.

Recomputation evidence and formulas:
`benchmarks/2026-10-06/fine-profiling/pipeline-throughput-attribution.json`. SHA values for both
source JSON files are recorded; recomputed capture rates match the original reports and the
denominator decomposition closes. Only diagnostic documentation and derived data were updated;
no new external inference/performance test ran, and no Git commit was made.

# 2026-10-06 Startup-Transport Follow-Up: Bidirectional Window and Warmup Path

This round made **zero GPU calls and deducted zero caller GPU-quota seconds**. A CPU-only diagnostic
service ran temporarily on the existing private probe Space. The formal HF commit
`2f5dd14e730afe7c94d4bb3a98766e5f4bd6cfb4` was retained; no warmup strategy lacking real-GPU
verification was deployed. The probe's original `README`/`app.py`/`requirements.txt` forward content
was restored byte-for-byte and the Space was PAUSED. Temporary loopback processes and the SSH
tunnel were stopped. The quota remains 106.879660 seconds with zero free calls.

Input was exported from the same Chrome/CameraApp/public image stream: 84,682 bytes, 480×640,
with the same SHA as before. An 91,181-byte JPEG generated using recorded real detection boxes
and the existing JPEG80 renderer also had a SHA identical to the real GPU response. The CPU
diagnostic fixed each full packet at 93,500 bytes and used an explicit 13.2 ms processing wait and
10-second first-frame initialization wait. It ran no model, CUDA, or GPU API. The service returned
`probe_result/gpu=false`; this is not a new detection result. Protocol compression was disabled,
and JSON padding only fixed the total packet size. All image sequence numbers, slow frames, and
control costs were retained.

The same current DNS gateway, `3.211.147.15`, was fixed; the original URI host remained in use
for TLS/SNI/Host validation, with TLS verification enabled. Forward and reverse order were each
run once, for 10 new-connection controls. Each 24-FPS group had 121 frames and the stress group
had 240; all were fully delivered. The following data are **PRS host↔private HF CPU controls**;
they exclude phone, browser encoding/decoding, and actual GPU processing.

| Explicit Control Condition | Post-First-Frame FPS (two runs) | Maximum Steady-State Transport Delay (two runs) | Approx. First Frame |
|---|---:|---:|---:|
| Original 24 FPS/eight-frame window | 22.18/22.13 | 470.3/475.1 ms | 11.04 s |
| Start at 2 frames, increase to 8 per result | 21.15/21.75 | 444.3/402.0 ms | 11.05 s |
| 256 KiB bidirectional control warmup after first result is ready | 22.58/22.66 | 435.5/418.9 ms | 12.22 s |
| 512 KiB bidirectional control warmup after first result is ready | **23.08/23.05** | **275.2/287.6 ms** | 12.56 s |

The 512 KiB group sampled at 23.991/23.995 FPS. The first normal image still completed
independently; warmup was a separate PRW1 control echo, not an image/inference request. Its
roughly 1.5 seconds and about 1 MiB of bidirectional traffic are included in first-frame cold
cost. This did not remove the first slow images; the remaining 120 images use the original FPS and
latency definitions. A progressive window reduced early queueing but lowered short-run throughput;
256 KiB had limited effect. The 512 KiB bidirectional warmup is currently the best-supported
candidate, but **a passing CPU control does not mean the formal GPU 500 ms threshold has passed**.

The separate capacity group explicitly used a 120-FPS supply, 32 diagnostic credits, zero
processing/initialization wait, and 1 MiB bidirectional warmup; it is not conflated with the
normal 24-FPS/eight-frame condition. Complete return measured 102.51–106.44 FPS, with input
payload around 69.45–72.11 Mbps and return around 76.68–79.62 Mbps. The current video needs about
16.3/18.0 Mbps. This shows that available throughput on this gateway path is well above target
video demand, but **it is not a maximum physical-bandwidth benchmark and cannot fully rule out
production-Space-specific scheduling/throttling**. The window limit must not be described as a
bandwidth limit.

10 ms Linux TCP_INFO sampling further showed an initial congestion window of 69 MSS and maximum
unsent bytes of about 350 KB in the normal group. The 512 KiB group entered video with a
congestion window around 432 MSS, maximum unsent bytes around 56 KB, and maximum service queue
around 0.05 ms. These samples had no retransmissions and receiver-limited time did not increase;
original retransmit/window counters remain available. At write checkpoints with empty TLS user
buffers and zero retransmissions, estimated request TCP byte boundaries and sampled ACKs indicate
about 403 ms of upload-confirmation wait on some startup images, while other images waited about
235 ms after upload confirmation; the latter had only about 14 ms of service processing. Waiting
involved **both upload and return directions**, not only the PRS send queue. ACK checkpoints are
sampled estimates, not packet-level captures. Server/client absolute clocks are not subtracted;
only each side's durations and interval changes are compared.

The original production HTTPS browser↔PRS Echo also ran a byte-exact binary control of 93,500
bytes at 24 FPS/eight in-flight. All 121 buffers in each run returned. Steady-state maximum was
88.7/85.4 ms with no idle/10 seconds idle after the first packet; first packets were 212.5/219.3 ms.
This control excludes capture/decode and HF, but did not reproduce sustained additional waiting
of around 200 ms.

Automatic approval blocked the command to add a temporary Nginx route/systemd service before
execution, returning only `blocked by policy` without a more specific reason; the command did not
run. The replacement experiment used an SSH-forwarded loopback page, retaining same-origin CSP and
HF TLS validation without changing Nginx/systemd. All four real Chrome/CameraApp/FramePipeline
groups delivered 121/121 frames, but the SSH first hop introduced substantial additional waits,
with median decode around 447–464 ms and output of 15–16 FPS. It therefore cannot explain the
original HTTPS 22 FPS or verify the formal 500 ms threshold. An early diagnostic client opened the
next single-slot connection too soon after receiving the summary, causing 1013. This was corrected
by waiting for normal server closure; both failure records and final results remain, without
automatic retries.

The proposed next implementation boundary is: after HF has prepared its first real result, request
one bidirectional control warmup of at most 512 KiB; allow the first JPEG only after PRS verifies
the echoed bytes exactly. Images continue to use the original PRF1/PRS1 sequence numbers,
with no frame drops and no changes to JPEG/threshold/model. Root identity and worker private keys
remain server-side only. Explicitly negotiate the new control protocol, limit it to once per
session, and set a warmup failure deadline **shorter than the worker's five-second idle budget**.
Failure ends the session explicitly; there is no fallback. Record warmup/first-frame cold time,
extra traffic, and normal-frame metrics separately. A CPU prototype of this path ran in
`tools/cpu_transport_probe.py`, but production implementation and real-GPU verification await
restoration of free calls. This round did not change the formal inference interface with an
unverified method.

Full evidence is in `benchmarks/2026-10-06/startup-transport/continuation-summary.json`. The same
directory contains the fixed-IP matrix, per-frame ACK/service times, 10 ms byte trace, original
HTTPS first hop, SSH limitations/failures, original-file restoration, and quota readings. Tools
include `profile_transport_startup.py`, `cpu_transport_probe.py`, `cpu_transport_proxy.py`, and
three independent frontend diagnostic entry points. The temporary private runtime source was
committed as `61e8d06419d04e6aeea24a5e200f74ce0eee7d0e`; subsequent local changes only added clearer
route rejection and logging.

Checks: 108 current backend and 48 frontend tests passed, along with five new real local socket/
protocol tests, Ruff on changed Python files, JS syntax, Django, and migration dry-run. Full Ruff
still reports only historical Kaggle v11 Notebook I001/E501 issues. The repository has no
`tools/check_docs.py`; comments and declaration/variable indexes were reviewed manually. No Git
commit was made.

The following records the previous real-GPU diagnosis and the released 24-FPS/eight-frame
configuration.

# 2026-10-06 Fine-Grained Pipeline Profiling and Eight-Frame Window Fix

The released configuration currently uses a **24-FPS input cap and eight total jobs**. A new real
Chrome session delivered all 181/181 frames. The 10.996-second first frame is reported separately;
the original post-first-frame output metric was **22.1880 FPS** and actual capture rate 22.8973 FPS.
Capture-to-decode P95 was 381.265 ms and maximum was 688.5 ms. Throughput improved, but the original
500 ms maximum-latency threshold still failed and `meets_target=false` remains. The image source is
still a MediaStream from the public bus image and excludes a physical phone camera, mobile CPU/
wireless network, and final screen rendering. The original 12-FPS/five-frame condition and all
previous results are retained.

First, zero-GPU protocol Ping/Pong, JPEG upload confirmation, Linux TCP_INFO, and real-Chrome
synthetic-transport controls were run. Then one HF allocation ran four explicit phases, totaling
364 frames. Model/weights/640/batch 1/compiled FP16 configuration/threshold 0.25/
JPEG80/FP32 output remained unchanged. Each phase used an independent first frame and full drain;
client wire sequence numbers remained continuous within the same connection. Each phase's first
frame is separated for FPS calculation. For the whole-session 500 ms threshold, only the actual
first frame is excluded and all other slow frames remain.

| Same Actual HF Allocation | Post-First-Frame Output FPS | Actual Capture FPS | Maximum Steady-State Decode Latency |
|---|---:|---:|---:|
| 12 FPS/five frames, 61 frames | 11.1661 | 11.6254 | 553.7 ms |
| 24 FPS/five frames, 121 frames | 15.9834 | 16.5197 | 329.6 ms |
| 24 FPS/eight frames, 121 frames | **22.7661** | **23.9794** | 336.0 ms |
| 12 FPS/eight frames, 61 frames | 11.4679 | 11.9666 | 322.3 ms |

All 364 frames were delivered, Graph was not recaptured, and raw/JPEG hashes matched throughout.
Increasing from five to eight frames mainly removed credit waits at high RTT: waits during the
24-FPS phase fell from 78 frames to one, and the HF input queue did not accumulate as the window
expanded. Results with different input caps/windows/phases are not model-forward speedup ratios;
the table is a descriptive comparison within one allocation. The overall allocation still failed
the maximum-latency acceptance because the 12-FPS/five-frame phase reached 553.7 ms.

Fine-grained intervals use monotonic timing within the same process; timestamps from different
hosts are not subtracted. Medians from the formal 24-FPS/eight-frame new session:

| Observable Interval | Duration | Interpretation |
|---|---:|---|
| PRS receive-to-forward wait | 0.050 ms | Negligible local application queueing |
| HF complete input-to-worker pickup | 0.135 ms | Small pre-GPU parent-process queue |
| HF parent-process frame pickup/response prep through full result validation | 13.192 ms | Full local handoff, inference, and result-upload cycle |
| Worker full predict | 11.352 ms | Includes preprocessing, GPU, postprocessing, and metadata construction |
| Difference of prior two medians, approximate handoff overhead only | 1.840 ms | Not the roughly 200 ms bottleneck |
| HF result dequeue wait | 0.264 ms | Small downstream application queue |
| Full PRS result validation | 0.384 ms | Small validation overhead |
| Full PRS↔HF interval | **238.251 ms** | Most waiting is outside the internal intervals above |
| CUDA event forward | 0.7156 ms | Included in predict; do not add it again |

After subtracting non-overlapping HF input queue, local cycle, output queue, and PRS validation per
frame, the residual interval median is 224.225 ms. It still includes network, edge proxy, TLS,
serialization, and ASGI send, so it is not pure network RTT. `worker_pull_ms` includes waiting for
the next frame captured on cadence and must not be misattributed to inherent HTTP overhead. Key
states and limited logs are recorded every 60/120 frames. Metadata keeps full interval breakdowns;
camera media is not stored.

Protocol Ping/Pong controls measured about 39.28 ms local→PRS, 243.34 ms local→HF, and 229.28 ms
PRS→HF. Kernel TCP RTT on PRS was about 210–223 ms; `TCP_NODELAY` was enabled. The event-loop
clock used by the Windows library's built-in latency metric showed visible quantization, so final
Ping measurements used explicit `perf_counter`; both original sets of readings are retained.
Waiting for Pong after a real JPEG upload confirms only the upload and excludes image-result
download; it does not replace complete inference latency.

The final real session also inspected TLS socket state owned by the PRS process cgroup. At video
start, the media connection had `cwnd=70` and MSS 1448, or about 101 KB of in-flight capacity,
with **71,804 bytes not sent**. One second later the sample showed `cwnd=718`; the media byte
stream had recovered to a stable rate and the receive-autotuning metric `rcv_space` had also grown
substantially. TCP RTT remained about 212 ms. Later SendQ was about 424 KB, mostly sent but
unacknowledged in-flight data; it must not all be treated as unsent backlog. This observation
supports **byte-level send queueing while startup transport capacity ramps up**, rather than HF
model/application queue accumulation. One-second sampling cannot distinguish all per-frame TLS/
proxy events or fully rule out network loss.

The formal 181-frame test had eight frames over 500 ms: frames 2–9. The slowest, frame 8 at
688.5 ms, comprised PRS↔HF 631.818 ms, HF input queue 20.791 ms, local cycle 11.182 ms, service
9.666 ms, and forward 0.7322 ms. The frame 12–181 diagnostic range, used only for diagnosis, had
about 23.8714 FPS completion intervals and a maximum of 334.8 ms. **It does not replace the
official short-test metric of 22.1880 FPS or change the failed acceptance result.** Remaining tail
latency is concentrated at the start of continuous video. Transport warmup or a progressive
startup window merits a later test; no startup strategy lacking HF verification was deployed in
this round, and 55-second sustained behavior was not measured.

The following sources were reviewed: [websockets latency/keepalive](https://websockets.readthedocs.io/en/15.0/topics/keepalive.html),
[buffering/backpressure](https://websockets.readthedocs.io/en/stable/topics/memory.html),
the [FastRTC object-detection implementation](https://github.com/gradio-app/fastrtc/blob/main/demo/object_detection/app.py), and
[SANA-WM's ZeroGPU streaming implementation](https://huggingface.co/spaces/multimodalart/sana-wm-streaming/blob/main/app.py).
The design uses a persistent connection, independent send/receive, and a bounded queue. SANA-WM's
frame-drop policy would change this project's full-delivery contract, so this implementation uses
a bounded credit window. Image encoding and model were not changed, and WebRTC was not introduced
to confound comparisons.

The 100 ms protocol Ping keepalive was tested explicitly. In the CPU control with 10 seconds of
idle, upload confirmation for the next 74 KB image dropped from 417.8/435.7 ms to 224.4/230.0 ms.
However, a real 24-FPS/eight-frame/181-frame rerun reached only 22.1607 FPS with a maximum of
688.1 ms and did not resolve the startup spike. Keepalive added no GPU calls; Ping heartbeat
timeout was set to None and the original response budget was retained. Because real benefit was
not confirmed, this experiment was **explicitly withdrawn**; code, failed result, and withdrawal
record are preserved. PRS was also found to have `tcp_slow_start_after_idle=0`, and the media
socket's `cwnd` stayed at 70 during GPU initialization; therefore the problem cannot be
attributed to "idle-window reduction caused by that setting." No system TCP configuration was
modified.

Current PRS is `phone-24fps-5596b747c05363ce`; the original 12-FPS version, eight-frame
transition, and withdrawn keepalive version are all retained. Three public JS files and three
backend modules matched local source byte-for-byte. New HF commit is
`2f5dd14e730afe7c94d4bb3a98766e5f4bd6cfb4`; it changed only transport boundaries/timing and
documentation, while the quality plan remained unchanged. HF health was ready with
`active_session=false`; PRS logs independently verified full-delivery counts for each real
session. Source releases used the original startup GPU initialization. Caller free-quota readings
did not decrease before/after redeployment, but startup work was not timed from the caller's billed
quota difference; this does not claim that deployment involved no GPU work.

This round made three real GPU calls, deducting 36.207295, 18.527998, and 17.982993 seconds, for
a total of **72.718286 seconds**. Remaining quota was 106.879660 seconds and **zero** free calls;
the gate now rejects requests because the call limit is exhausted. Official runs reset at
2026-10-06T16:08:22.097Z (2026-10-07 00:08:22 Beijing time). No further request or paid increase
was made.

Data is under `benchmarks/2026-10-06/fine-profiling/`, summarized by
`fine-diagnosis-summary.json`, including all frames, source SHAs/release/withdrawal records,
TCP/Ping/idle controls, failures, and quota. The new tool
`frontend/tools/benchmark_phone_windows.mjs URL OUTPUT [--echo-delay 300]` explicitly distinguishes
zero-GPU synthetic controls from one real allocation; `benchmark_phone_browser.mjs URL 181 OUTPUT`
uses the public production page. Real-mode reproduction must wait for free calls to reset and
first check a 90-second reservation. No automatic retry is used.

Delivery checks: 101 current backend tests and 48 frontend tests passed; Django check,
migration dry-run, changed-Python Ruff, JS syntax, and remote dependency/ASGI/readiness checks
passed. Full Ruff still has the two historical Kaggle v11 Notebook I001/E501 issues; workspace
diff still reports trailing whitespace in unchanged `maps-page.js`. The repository has no
`tools/check_docs.py`; comments and declaration/variable indexes were reviewed manually. No Git
commit was made and commit atomicity is not claimed.

The following records the earlier 12-FPS/five-frame scheduling fix and initial tests.

# 2026-10-06 FPS Profiling, Historical 24-FPS Metric Review, and Scheduling Fix

Browser sampling's cumulative timer drift was fixed and released. In the same 61-frame short test,
full-browser output improved from **10.6323 to 11.1204 FPS**, and actual capture from 11.0206 to
11.6323 FPS; both runs delivered 61/61 frames. In a zero-GPU control with the CPU sampling
profiler disabled, 12-FPS capture recovered from 11.1899 to 12.0007; with an explicit 250 ms
synthetic Echo round trip it recovered from 11.3612 to 11.9887. Current public performance still
does not reach 24 FPS, maximum decode latency is 567 ms, and the original 500 ms acceptance
threshold remains failed with `meets_target=false`. No slow frames were removed and no acceptance
condition was changed.

Three earlier records near 24 FPS were 23.9966 in `2026-10-03/resident-continuous.json`,
23.9195 in `resident-independent-service-smoke.json`, and 24.0240 in
`zerogpu-live-native-bridge.json`. All used a local RTX 4060, loopback WS, or loopback HTTP bridge
and excluded the public network, physical camera, and browser capture/decode. The third explicitly
reported `local_native=true`. On the same public HF endpoint, a prior pre-encoded 12-FPS input
produced 12.0012 FPS. This round used one GPU allocation for sequential 12/24-FPS tests; four
correctness inputs plus 48 and 96 frames passed identity/order/prediction-hash checks, producing
**12.0126/17.2679 FPS**. Thus no throughput degradation was observed for the 12-FPS interface on
the same path; historical local 24-FPS measurements are not a current public 24-FPS baseline.

| Current Pre-Encoded Public Endpoint | 12-FPS Input | 24-FPS Input |
|---|---:|---:|
| Actual Input FPS | 12.0016 | 17.2780 |
| Result-Completion FPS | 12.0126 | 17.2679 |
| Median Request-to-Validated-Result | 289.403 ms | 288.624 ms |
| Median PRS↔HF Interval | 236.125 ms | 235.145 ms |
| Median Service Processing/CUDA-Event Forward | 12.879/0.7148 ms | 12.852/0.7157 ms |

Capacity estimated for a five-frame in-flight window at about 289 ms average occupancy is around
17.3 FPS, consistent with the 24-FPS input experiment. Actual sampling occurred after a slot
became available; the maximum sampling delay of 1,540 ms is retained as measured. No stale image
was queued or frame dropped. The zero-GPU network control in this round also found a median of
242.356 ms across eight steady-state CPU-only health requests from the PRS host to HF on a reused
HTTPS connection; the local machine to PRS page measured 45.221 ms. The health endpoint does not
allocate GPU. These small HTTP responses still include proxy/service processing and use different
routing, size, and queueing from binary WS. They support substantial waiting across the service
link, but do not justify labeling all duration as pure network RTT or assigning a precise share to
proxy/process handoff.

The original browser schedule used `actual capture time + interval` for every frame, so delayed
ordinary timers accumulated drift. Before the fix, 60 steady-state frames averaged 5.773 ms late,
with capture averaging 8.745 ms and decode 2.420 ms; only two frames had credit waits. The fix
retains the planned timeline and subtracts ordinary wakeup lateness from the next remaining wait.
If credit wait or delay reaches a full frame interval, scheduling restarts from actual capture time
without replaying missed sampling slots. First-frame completion remains independent. Defaults and
model settings remain: 12 FPS, at most five jobs including capture/transport/display wait, model
weights/FP16 configuration/640/threshold 0.25/JPEG80. Per-frame observations add planned time,
requested/actual wait, wakeup lateness, and credit wait. The page aggregates only every five
seconds and on stop; it does not store unbounded frame history, media, or credentials. Serial Echo
responses lacking schedule fields are not counted as zero-wait samples.

| Same Chrome, 61-Frame Full Public Path | Before Fix | After Release |
|---|---:|---:|
| Post-First-Frame Result-Decode FPS | 10.6323 | 11.1204 |
| Actual Capture FPS | 11.0206 | 11.6323 |
| First Frame, Reported Separately | 13.918 s | 11.453 s |
| Decode Latency Median/P95/Maximum | 294.85/454.25/540.5 ms | 316.00/465.165/567.0 ms |
| Median PRS↔HF/Service Processing/Forward | 237.070/13.458/0.7252 ms | 257.723/11.613/0.6978 ms |

The 4.59% output increase is a descriptive observation from two short allocations, not a confirmed
gain repeated across independent allocations; the later run actually had a longer network interval
and capture time. Zero-GPU controls and scheduling regression tests independently support the
drift fix. After release, one 148.1 ms credit wait shows network jitter can still block sampling.
The roughly 5.07-second capture span, first steady-state capture offset, and final-frame return/
decode are all included in the short run's 5.40-second completion interval. Actual capture FPS
cannot replace full output FPS. In the slowest 567 ms frame, transport was 555.2 ms, PRS↔HF
476.104 ms, service 12.464 ms, forward 0.7016 ms, and decode 2.2 ms. There was no repeated Graph
capture, and steady-state prediction/JPEG hashes remained stable. The 20% PRS CPU quota was
unchanged; the 12/24 endpoint experiment added no throttling. The post-restart browser test added
one 36.098 ms throttle, recorded separately from the startup-period total of 1.844 s; it cannot
explain roughly 250 ms of cross-service wait per frame.

The CPU sampling profiler may itself affect timer behavior: a zero-transport 12-FPS control
captured at 11.893 with 1 kHz sampling enabled versus 11.190 with it disabled before the fix.
Original profiles and frame timings are retained. CPU sampling was disabled for actual-HF before/
after comparison and main controls; CDP Performance cumulative duration was also observed.
Controls use real Chrome/CameraApp/canvas encoding/decoding/HUD with explicit 0 or 250 ms synthetic
Echo transport; they do not establish network or model acceptance.

Only five files were released: `camera.js`, `frame-pipeline.js`, their two tests, and the frontend
README. Ordinary-file hash comparison across the new release confirmed that the remaining 134
files were inherited from the original release. Current release is
`phone-cadence-ceb81d60166dcab1`; original `phone-live-4838d71f86078c02` is retained. SHA values
for the three public runtime JS files match local files. HF remains at
`eb063ebcccc4461a77c38341456e3dbf22aed962`; HF was not restarted and the model was not recompiled.
PRS checks and service readiness passed; HF health was ready with `active_session=false`.

This round used three GPU calls and deducted 59.074721 seconds: 23.767360 seconds for same-
allocation 12/24 endpoint tests, 19.005585 seconds for the pre-fix browser, and 16.301776 seconds
after release. Remaining quota was 179.597946 seconds and three free calls. CPU controls, profiling,
and health checks use no GPU. Physical phone sensors/CPU/wireless, final rendering, and sustained
55-second performance were not verified. If public 24-FPS output is required later, the same
five-job window needs average total occupancy no greater than about 208 ms. The cross-service path
must be shortened or the window/sampling explicitly redesigned and latency verified; speeding up
the current roughly 0.7 ms forward alone is not enough to remove this bottleneck.

Reproduction entry points: frontend `tools/profile_frame_pipeline.mjs URL OUTPUT PIPELINE_FILE
[--cpu-profile]` and `tools/benchmark_phone_browser.mjs URL 61 OUTPUT [--pipeline-file PATH]`;
the post-release rerun did not override source files. Backend `tools.benchmark_live_remote` uses
the same URL/origin with `--fps 12 24 --seconds 4 --plan models/inference-plan.json`. All raw
rows, pre-change source snapshots, source SHAs, CPU profile, quota, release, logs, and interval
calculations are under `benchmarks/2026-10-06/profiling/`.
The summary is `diagnosis-summary.json`; older reports are retained.

Checks: 40 frontend and 100 backend tests passed, along with Django check, migration dry-run,
changed-Python Ruff/syntax, JS syntax, and remote Django/migration/dependency/ASGI checks. Full
Ruff still reports only the historical Kaggle v11 Notebook I001/E501 issues; the full-workspace
diff still reports trailing whitespace in unchanged `maps-page.js`. The repository has no
`tools/check_docs.py`; comments and declaration/variable indexes in changed files were reviewed
manually. No Git commit was made.

The following records FPS and forward comparisons from before today's fix.

# 2026-10-06 Current Camera FPS and HF Forward Comparison (Before Fix)

The current public-page short run measured **10.3118 FPS**: all 61/61 frames were delivered, the
12.190-second first frame was reported separately, and the next 60 frames decoded within 5.8186
seconds. The default input cap remains 12 FPS with at most five jobs. Sampling strategy, model,
size 640, threshold 0.25, and JPEG80 were unchanged, and no slow frames were removed. Real Chrome
executed the page/canvas encoding/public WS/SHA validation/ordered image decoding; a MediaStream
generated from the public bus image substituted only for the physical camera. The result therefore
excludes phone sensors, phone CPU/wireless network, and final screen rendering, and is not a
55-second sustained-run conclusion.

Capture-to-decode median was 302.7 ms, P95 412.555 ms, and maximum 522 ms; Graph was not
recaptured after the first frame, and raw/JPEG hashes remained stable. Full delivery and ≥10 FPS
passed, but maximum latency still exceeded the existing 500 ms threshold, so `meets_target=false`
is retained accurately. PRS logs independently confirmed 61 received/forwarded/delivered/worker
frames.

Browser-path steady-state medians: CUDA-event forward 0.732224 ms, GPU inference including
transport 1.901715 ms, full service processing 11.951792 ms, and PRS↔HF interval 246.485082 ms.
In the slowest 522 ms frame, PRS↔HF was 461.380890 ms. Most waiting was outside forward; these
intervals cannot individually separate network, queueing, and process handoff. SHA values for
public `camera.js`, `frame-pipeline.js`, and `live-detection-client.js` all matched local source.

After quota became available, three methods were compared within **one GPU allocation** on an
isolated private `Heropig114514/prs-forward-probe-20261006`. The production camera Space was not
restarted or modified. Hardware matched production: NVIDIA RTX PRO6000 Blackwell Server Edition
MIG 2g.48gb, 94 SM, Torch 2.8.0/cu128. Five public inputs were the bus image and the original four
COCO8 images, using JPEG80/project letterbox, batch 1/640, and seed20261004. TF32 and cuDNN
benchmark were disabled; CPU/intra-op/inter-op were single-threaded. Each method had at least 10
runs and one second of warmup. Method order rotated across three rounds, each with 30 samples per
method, for 270 total; all samples are retained. Timing used start/forward/end events on the same
CUDA stream and synchronized end. Inputs were already resident on the GPU; H2D/D2H, JPEG, network,
rendering, loading, warmup, and compilation were excluded. Consistent FP32 output conversion was
included. CPU submission gaps between ordinary eager kernels remain in CUDA-event elapsed time; it
is not claimed to equal the sum of kernel execution durations.

| Method | Forward Median | Current-Method Speedup |
|---|---:|---:|
| Original FP32 eager, NCHW, unfused Conv/BN, 114 BatchNorm layers | 8.979840 ms | **12.9086x** |
| Eager with the same FP16, Conv/BN fusion, and channels-last | 6.631296 ms | **9.5325x** |
| Current FP16, fused, channels-last, compile(default/fullgraph/static), production CUDA Graph | 0.695648 ms | 1x |

The 12.9086x result includes combined gains from precision, fusion, layout, compilation, and
Graph. The 9.5325x result controls the first three and represents the joint compilation+Graph
gain; it cannot be split into standalone gains. Across three rounds, speedup versus FP32 eager was
12.7677/12.8110/12.9254x and versus the same FP16 eager was 9.6581/9.8144/8.9926x. This is a
descriptive comparison within one short allocation, not repeated across independent GPU
allocations and not a complete HF-method ranking. This forward speedup cannot replace public FPS
speedup. Raw SHA values for the current method on all four COCO8 images exactly matched previous
official HF cached-inference records; raw top-k row-order differences between eager/compiled are
diagnostic only and do not add an AP conclusion. The original three full 5,000-image COCO
qualification results remain, and HF did not rerun full COCO.

The temporary probe's 33.909-second cold compilation, 2.964-second Graph preparation, and
41.857-second total allocation work are retained separately. The initial CPU startup failed due
to a Gradio 6 API registration-argument mismatch. The failure report was kept, and registration
was changed to the project's existing hidden `Button.click` pattern. This failure made no GPU call
and did not change numeric conditions or automatically retry GPU. After testing, the temporary
Space was PAUSED; source and results are retained. Formal HF remains at
`eb063ebcccc4461a77c38341456e3dbf22aed962`, with CPU health ready and `active_session=false`.

The two free-GPU calls deducted **61.327333 seconds** total: 17.391253 seconds for the page
short run and 43.936080 seconds for the forward comparison. Remaining quota was 238.672667
seconds/six free calls. No paid hardware was requested and no further quota was used. The 90-second
reservation is an admission buffer for each default 60-second Blackwell task; actual deduction is
the quota difference reported above.

Raw evidence is under `benchmarks/2026-10-06/`: `browser-61frames.json`, `browser-summary.json`,
`browser-relay-log.txt`、`public-camera-source.json`、`forward-comparison.json`、
`forward-summary.json`、`forward-runtime-log.json`、`quota-before.json`、
`quota-after-browser.json`, `quota-after-forward.json`, and `production-after-tests.json`.
Probe source is `tools/hf_forward_probe.py`; the entry point for single calls/quota/raw-sample
validation is `tools/benchmark_hf_forward.py`. Original failure, creation, commit, and pause
records are preserved as well.

Checks passed for Django, migration dry-run, the existing 100 backend tests, Ruff on the two new
scripts, and Python syntax. FPS/forward medians/speedups were recomputed from raw timings; 61-frame
delivery, output identity, quota difference, formal source commit, and paused state were checked.
Full Ruff still has the historical Kaggle v11 Notebook I001/E501 issues; workspace
`git diff --check` also reports trailing whitespace in unchanged `frontend/maps-page.js`. The
repository has no `tools/check_docs.py`; comments and declaration/variable indexes for both scripts
were reviewed manually. No Git commit was made and commit atomicity is not claimed. Physical phones,
sustained 55-second FPS, repeated forward tests across independent allocations, and new full HF AP
remain unverified.

# 2026-10-05 Current Public Page Short Run and Browser-Contract Fix

The current full-browser path measured **10.3486 FPS**, below 12 FPS; the default input sampling
cap remains 12 FPS. This 61-frame short run used real Chrome page/canvas JPEG encoding, PRS↔HF
round trips, integrity validation, and ordered decoding. It delivered all 61/61 frames, reported
the 14.718-second first frame separately, and decoded the remaining 60 frames within 5.798 seconds.
No slow frame was filtered. The source was a canvas MediaStream generated from the public bus image
and did not include real phone sensors, mobile network, or screen rendering.

| Observation Path | Delivered | Post-First-Frame FPS | Maximum Capture-to-Decode |
|---|---:|---:|---:|
| Original HF FP32, public raw-byte input/return, 2026-10-03 | All 4+180 | 12.001 | Excludes browser capture/decode |
| Original HF FP32, full Chrome page, 2026-10-03 | 241/241 | 10.707 | 443.8 ms |
| Current HF FP16 compiled+Graph, full Chrome page, this short run | 61/61 | 10.349 | 557.0 ms |

These observations used different time periods, HF states, and network conditions and are not used
to calculate end-to-end speedup. The 12.001-FPS record does not directly represent equivalent
throughput including browser capture/decode. Comparing send implementation and per-frame
timestamps shows that the original byte script sent pre-encoded JPEGs at absolute times
`started + ordinal/fps`, reaching 12.008 FPS with an average interval of 83.276 ms. The browser
schedules the next frame from actual capture time and does not send catch-up frames after a pause.
This run's average capture interval was 93.108 ms and actual input rate 10.740 FPS; the historical
241-frame browser run also captured only 10.803 FPS. Scheduling-source SHA is identical to the
historical deployment. The interval from frame 6→7 was 175.4 ms: at the planned next capture all
five credits were occupied, and the earliest prior decode needed another 91.667 ms. Specific
sources of delay during other ordinary sampling intervals were not separately recorded, so they
cannot all be attributed to network or browser timers.

The 5.7979-second FPS denominator comprises a 0.6 ms initial interval, 5.4934-second capture
span, and 303.9 ms for final-frame return/decode; the final-frame wait contributes a larger share
of this short run. A diagnostic using the same first 61 consecutive saved frames measured 10.464
FPS for the historical browser and 10.349 FPS currently. The official 10.707 FPS from the original
241-frame run and all raw records remain; the shortened comparison does not replace acceptance.
This evidence supports effects from the test path, sampling cadence, and short window, but does not
show that model optimization caused a drop from 12 to 10 FPS. Detailed calculations, source
identity, and limits are in `hf-jit-browser-pacing-analysis.json`; this analysis made no GPU calls.

Current pure CUDA-event forward median was 0.73736 ms, GPU-stage median 2.408 ms, service-processing
median 15.436 ms, and PRS↔HF interval median 248.722 ms. In the slowest 557 ms frame, PRS↔HF
was 497.664 ms, service processing 15.304 ms, forward 0.734 ms, and decode 2.9 ms. Most wait was
outside computation; the contribution of network, queueing, and process handoff within that
interval is not yet separated. Sampling settings were unchanged, Graph was not recaptured, and all
steady-state raw/JPEG hashes remained stable. No frames were dropped and there was no implicit
retry or fallback. Original acceptance also requires maximum latency ≤500 ms; although all frames
were delivered and rate was ≥10 FPS, `meets_target=false` is explicitly recorded. This short
observation does not establish 12 FPS over a long session and does not change Kaggle's pure-
inference ranking or COCO quality conclusions.

The first page attempt returned "invalid video session configuration" before sending the first
frame because the browser backend allowlist omitted the deployed `PyTorch compiled CUDA` name.
Earlier direct eight-frame HF inference checks did not cover this browser contract, leaving an
interface gap. The original failure report, PRS zero-input/zero-GPU logs, and unchanged quota are
retained. The fix only adds the explicit deployed backend name and uses the real
`models/inference-plan.json` to regression-check admission and per-frame identity matching; quality
and output checks remain unchanged. Current PRS release is `phone-live-61004595d4ff8c1a`, with the
previous `phone-live-ed296ac59e1be232` retained. Release-manifest comparison shows only the client
and its corresponding test changed. Public client SHA matches local byte-for-byte. HF remains at
`eb063ebcccc4461a77c38341456e3dbf22aed962`; HF restart and target-cache recompilation were not
needed.

This run used one free GPU call and deducted 19.904338 seconds; 143.937195 seconds and zero free
calls remained. The call count was expected to reset at 2026-10-05 21:42:40 (UTC+8), and the
quota gate would reject new HF video requests until then. After the session, HF CPU health was
ready with `active_session=false`. No further long HF experiment was run.

Raw evidence is under `benchmarks/2026-10-04/`: `hf-jit-browser-61frames.json` stores all per-frame
timings; `hf-jit-browser-admission-failed.json` preserves the pre-fix failure;
`hf-jit-browser-comparison.json` stores metric definitions, interval decomposition, and verification
limits; `phone-jit-browser-relay-log.txt` independently confirms 61 input/forward/return/worker
frames; `phone-jit-browser-fix-deployment.json` and `phone-jit-browser-public-source.json` record
release and public-source identity.

Latest HF model timings were also summarized in `core-data-tables/hf-inference-observations.xlsx`:
two independent allocations each checked eight frames, with pure-forward medians of
0.70288/0.70038 ms; 60 current steady-state browser frames measured 0.73736 ms. Latest synchronized
H2D/Graph/D2H inference median was 2.320224 ms. The workbook retains 109 raw frames from current
and five historical reports, lists initialization separately, and has source SHAs/review records
in the matching JSON. Historical FP32 reports lack comparable pure-forward timing and verified
GPU identity, so HF speedup versus baseline remains unmeasured; Kaggle T4's 8.574x must not be
applied to HF. This export used existing data only; original reports and the T4 core CSV/ranking
table remain unchanged, with no new GPU call.

The 37 frontend and 88 backend tests passed, as did Django check, migration dry-run, server checks,
Nginx, and service readiness. Full Ruff still reports two retained formatting issues in the Kaggle
v11 Notebook (import blank line and 124-character line); the original commit was not modified.
The repository has no `tools/check_docs.py`; comments and declaration/variable indexes in both
changed code files were reviewed manually. No Git commit was made.

# 2026-10-05 Deploying the Best T4 Pure-Inference Configuration (Original Migration Record)

The current inference configuration comes from the repeated best result in the same Kaggle
Notebook v8: FP16, Conv/BN fusion, channels-last, torch.compile(default/fullgraph/static), CUDA
Graph, and pinned-buffer reuse. Original model weights/640/threshold 0.25/JPEG80 are unchanged;
public arrays are FP32. The T4 pure-forward group median was 1.742272 ms versus 14.938976 ms for
the same-version fair baseline, about 8.574x. All three full 5,000-image COCO AP runs were
39.156308%, a 0.014207-point decrease versus baseline, within the approved 0.5-point quality limit.

HF released corrected commit `eb063ebcccc4461a77c38341456e3dbf22aed962`; Git blob identities of
the 19 released source files were verified. The actual GPU remains Blackwell MIG 2g.48gb. Startup
compilation on the target GPU took 40.398 seconds. Target-specific compiler artifacts and
Inductor/Triton cache directories are retained; no T4 binary was migrated and no paid hardware was
selected. Cache SHA: `4c1a0062c0d54563a9288f20f454d6f27058c6dd7ebccb6511437f9751eed62d`.

Two independent GPU allocations passed 8/8 frames; HF reused PID 1376. Each run explicitly reset
Dynamo frontend state and restored the disk cache. FX hit=1 with no FX miss/bypass/disabled; new
backend code generation is prohibited and later recompilation raises an error. Loads took
8.507/4.375 seconds, Graph preparation 0.548/0.536 seconds, and pure CUDA-event medians
0.70288/0.70038 ms. Each allocation captured Graph once. Raw and rendered JPEG hashes matched
across allocations; four images each matched 13/13 reference boxes. These short observations
validate deployment/reuse, not HF method ranking or public speed guarantees. HF did not rerun full
COCO.

The first call on initial version `f72689d2` succeeded. The second call reused the same PID, bypassed
FX lookup, and returned an empty counter, triggering strict validation. Original failure report and
logs are retained in full. The fix did not relax cache-hit/output checks or add fallback/implicit
retry. Two fresh processes in Kaggle v11 each processed 20 frames matching v8 raw predictions;
v12 adds implementation diagnostics for repeated same-PID restoration. PRS activation identity,
two short calls, source identity, cache contract, and free quota are in
`benchmarks/2026-10-04/hf-jit-migration-contract.json` and `phone-jit-deployment.json`. The original
migration stage did not run long HF video or end-to-end performance tests; a later browser short
run and contract fix are documented at the beginning of this file. The following are historical
AOT deployment/public-test records.

# 2026-10-04 Acceleration-Plan Migration (Historical)

Kaggle Notebook v10 completed, with a deployment plan of FP16/serial AOT/fusion/pinned buffers/
CUDA Graph. Three full COCO runs had AP 39.1569%, 0.0136 percentage points below the fair
baseline. HF released commit `78f5bf06b040d47d08f7a0e53ff067577d8e588b`, and PRS activated release
`phone-live-1c35eaf5cef2a6cc`. Git blob identities of all 18 HF release files matched the pinned
commit. Source and deployment logs are in `hf-published-source-verification.json` and
`phone-qualified-deployment.json`.

The actual HF GPU was NVIDIA RTX PRO 6000 Blackwell Server Edition MIG 2g.48gb, Torch 2.8/cu128.
The original free `zero-a10g` hardware configuration was unchanged and no paid hardware was
requested; platform labels do not identify the actual GPU model. AOT was recompiled at target-GPU
startup without using a T4 binary. Compilation took 45.748 seconds; first-allocation load
(1.186 s) and Graph preparation (1.847 s) are reported separately. Package SHA:
`9f2ff3bc942e5cd99554affd536e95d98f8cd4b68fc164ffeea2d1ec2339752e`. Each allocation verifies
hardware, runtime, configuration, and package, and records load/capture/cleanup context. Public
arrays remain FP32 while actual convolution uses FP16; weights, 640, threshold 0.25, and JPEG80 are
fixed.

| Verification Path | Delivered | Post-First-Frame FPS | Maximum Latency | Result |
|---|---:|---:|---:|---|
| Original HF FP32, public Chrome, 16 frames | 16/16 | 8.866 | 553.9 ms decode | Original comparison retained |
| New protocol, local RTX 4060 FP16 Graph | 16/16 | 11.909 | 44.6 ms decode | Local control passed; AOT not loaded |
| HF AOT, public Chrome, 16 frames | 16/16 | 9.772 | 447.9 ms decode | Full delivery; throughput below 10 FPS |
| HF AOT, four-image diagnostic + 12-frame continuous input | 16/16 | 4.029 | 1,634.3 ms return | Full delivery; public target failed |
| HF AOT, public Chrome, natural 55-second end | 566/571 | 10.992 | 565.5 ms decode | Five frames not returned; full acceptance failed |

Both independent HF calls delivered 16/16 frames and worker processing counts agreed. On four
images, all 13 reference boxes matched at IoU 0.5, with mean absolute confidence difference
0.001051. This four-image diagnostic does not replace full COCO; HF did not rerun AP on 5,000
images. Chrome used a canvas MediaStream from a public image, not phone footage; results exclude
sensor exposure and screen rendering. First frame, all slow frames, sampling pauses, and in-flight
counts remain; no frame was dropped and timing was not changed to meet the target.

HF steady-state medians in the short run were 1.623 ms GPU inference, 10.407 ms full service
processing, and 235.003 ms PRS-to-HF round trip. For four-image continuous input, the slowest frame
took 1,634.272 ms; service processing for that frame was 9.776 ms/GPU stage 1.566 ms, and
`reader_pause_ms=0`. Evidence indicates most waiting was in transport, queueing, and handoff but
does not isolate a single cause. Two public-network short runs do not guarantee identical GPU or
network state, so 8.866→9.772 does not prove model speedup; speed conclusions come from Kaggle.

The 55-second test retains independent logs for 571 submitted, 570 forwarded, 566 returned, and 566
worker-processed frames; five jobs were incomplete at the hard deadline. Code inspection shows the
worker's 55-second hard cutoff and continued client sampling can leave in-flight work at the
boundary. The session ended naturally and later CPU health reported `active_session=false`. Hashes
of all returned frames were stable, and Graph was not recaptured. Existing 55/60-second budgets,
five credits, and failure semantics are unchanged; this is reported as incomplete delivery, not
as fixed.

Raw evidence, all under `benchmarks/2026-10-04`: `hf-browser-qualified-aoti-16frames.json`,
`hf-qualified-four-fixtures-and-12frames.json`, `hf-browser-qualified-aoti-lifetime.json`,
`prs-final-live-log.txt`, `hf-live-selected-runtime-log.json`, `hf-target-package-contract.json`,
and `hf-final-cpu-health.json`. The summary `hf-migration-contract.json` clearly distinguishes
model deployment/GPU execution from public-network acceptance. Final free quota was 218.284 GPU
seconds/four calls; no extra retry or identity change was made.

The following preserves public observations and implementation notes from the original version,
including historical tail latency.

# Mobile Video Pipeline Optimization and Public Verification (2026-10-03)

The new entry point is **https://47.239.50.129/prs/live/**. Select Hugging Face ZeroGPU, then enable
the camera. `/prs/` currently redirects to the same page and retains only Echo and the latest HF
video detection. The old CPU/HF single-frame/browser-box-drawing/20-second-session/segmented
interfaces were removed. HF still uses free `zero-a10g`. Performance tables below are baselines from
the saved version; verification after simplification is recorded separately.

**Conclusion: deployed throughput exceeded 10 FPS, but per-frame latency ≤500 ms is not
guaranteed.** All frames passed in the 241-frame short run; a 55-second full session previously
had a 1.10-second tail. A later run with additional interval timing had a 467 ms maximum among
returned frames, but did not reproduce the earlier pause; non-reproduction is not a fix. Most
waiting was in PRS↔HF round trips and handoff; GPU computation and browser decode were a small share.

## Implementation and Rationale

Phone video uses a persistent same-origin WS, relayed by `core/hf_video_relay.py` to the deployed
HF `/prs_live/ws`. Each video invokes `detect_live` once, using the default 60-second GPU task and
at most 55 seconds in the worker. Stop, disconnect, timeout, or five seconds without input ends
the session. The service does not reconnect/retry, switch identity, request another GPU, or use paid
hardware. The public sample-detection button and corresponding HTTP endpoint were removed; the
original image was moved to a development-test directory.

`frame-pipeline.js` decouples capture/send from ordered decode/display: the first frame completes
independently; afterwards input is capped at 12 FPS by default, with at most five jobs capturing,
transmitting, or waiting for display. Acquire a slot before capture. After a pause, continue from
the current time; do not replay missed sampling times, accumulate stale early captures, or discard
submitted frames to increase reported rate. Neither Echo nor HF adds an extra 200 ms wait. Reuse
the canvas when its dimensions are unchanged; do not alter aspect ratio or encoding parameters.

This project uses a persistent connection and bounded queue. Checking browser `bufferedAmount`
alone does not indicate processing completion because it counts only bytes not yet sent over the
network. The browser therefore also retains job credits during result validation, queueing, and
decode. See [MDN's bufferedAmount definition](https://developer.mozilla.org/en-US/docs/Web/API/WebSocket/bufferedAmount)
and [websockets bounded-buffer documentation](https://websockets.readthedocs.io/en/stable/topics/memory.html).
GPU resources are reused only within a bounded function and released when it returns, consistent
with the [HF ZeroGPU allocation model](https://huggingface.co/docs/hub/spaces-zerogpu).

Before video starts, require both at least 60 free GPU seconds and more than zero free calls; reject
unknown/insufficient quota directly without requesting a GPU. A PRS video connection exclusively
holds one non-queued slot until the actual GPU-control task ends. HF client/worker capabilities
and private tokens are never placed in the browser, source archive, or logs. Validate binary results
in order for model, FP32, 80 classes, original weight SHA, box bounds, and JPEG SHA256; after
browser decoding, validate dimensions as well. At most five frames are in flight, and images are
not base64-encoded.

FP32, model, maximum dimension 640, threshold 0.25, capture JPEG 0.8, and GPU-side JPEG80 were
unchanged. Network/protocol changed; original four-image input bytes, baseline results, and old
probe reports are retained for comparison. Numerical results across CUDA runtime environments are
not claimed to be bit-identical: 13/13 boxes matched under the original IoU 0.5 rule, with mean
absolute confidence difference around 3.90e-7. Within each new session, all steady-state raw/JPEG
fingerprints matched.

Capture-to-result timing starts before calling capture and ends after full-packet validation;
capture-to-decode also includes display queueing and `image.decode()`. Report the first frame
separately; retain every other slow frame, pause, and queue wait. These intervals are not sensor
exposure or screen-render time. A 600 ms negative test confirmed latency can exceed 500 ms; no
timestamps were reset and no slow frame was excluded.

On session end, finish asynchronous hashes in wire order before processing end/close. If the GPU
control summary completes first, drain trailing results first. Inputs still incomplete at automatic
timeout remain in the sent/returned difference and are not counted as successful; the page does
not automatically start another allocation. Per-frame PRS adds `prs_upstream_ms`, measured on one
monotonic PRS clock from HF submission (including send wait) through full HF-result validation. It
includes HF queueing, parent-process/GPU handoff, processing, and network. Host clock synchronization
is not required. Only metadata is rewrapped; JPEG bytes and model output remain unchanged.

## Evidence and Boundaries

| Verification Path | Successful Frames | Post-First-Frame FPS | Maximum Capture-to-Result/Decode | Repeated Graph Initialization |
|---|---:|---:|---:|---:|
| Chrome→local PRS relay→original RTX 4060→Chrome |121/121|10.834|66.0/94.0 ms|0|
| Original-byte four-image comparison + public PRS/HF continuous input |All 4+180|12.001|376.49 ms (excludes browser capture/display)|0|
| Real Chrome page→public PRS→HF→Chrome |241/241|10.707|441.3/443.8 ms|0|
| Same path, natural 55-second end |570/575|10.619|1095.0/1097.4 ms|0|
| Same path, per-frame PRS timing added, natural end |577/582|10.767|464.4/466.9 ms|0|

The third row includes real canvas resizing/JPEG encoding, TLS/WS, metadata/full-JPEG validation,
ordered queueing, and browser decode. The test stream came from a canvas MediaStream generated
from a public bus image; **it is not phone footage** and is not an accuracy evaluation. The first
frame took about 22.44 seconds and is reported separately from steady state; the next 240 frames
took about 22.41 seconds, with P95 result/decode latency of 297.55/300.23 ms. The original-byte
15-second test delivered 180 frames, also used four original public COCO comparison images and 13
reference boxes, and confirmed 184 worker-end frames. The original-byte 15-second and 241-frame
short runs met the original ≥10-FPS and ≤500-ms maximum-latency criteria. In the later full session,
P95 decode latency was 318.68 ms, but seven steady-state frames exceeded 500 ms and the peak was
1,097.4 ms. That peak frame comprised 9.6 ms capture, 1,085.4 ms transport/handoff, 2.96 ms GPU,
10.76 ms server processing, and 2.4 ms decode. This rules out slow computation or decoding for
that frame. Per-frame PRS timing was not available then, so **the pause cannot be retrospectively
assigned to a specific PRS↔HF or browser↔PRS network segment**.

After adding timing, a full rerun had 308.20 ms P95 to decode, and all 576 returned steady-state
frames were ≤500 ms. Mean breakdown under the same conditions follows (intervals overlap; do not
add the GPU row to the PRS↔HF row):

| Interval | Mean Duration | Meaning |
|---|---:|---|
| Browser capture/JPEG encode |6.60 ms|From the page's capture call through encoding completion|
| PRS HF submission through full-result validation |242.47 ms|HF round trip, queueing, process handoff, and processing|
| Server processing within that interval |11.46 ms|Preprocessing, GPU, box drawing/encoding; GPU-stage mean 3.05 ms|
| Remaining transport/validation |45.25 ms|Browser↔PRS, relay wrapping, and browser result validation; not pure network time|
| Browser display queue + decode |2.38 ms|Excludes final physical screen rendering|
| Total capture-to-decode |296.71 ms|All completed steady-state frames, with no slow-frame filtering|

In the current maximum frame of 466.9 ms, the PRS↔HF interval was 408.45 ms and server processing
11.83 ms, so most waiting in this run was outside that interval's processing. This cannot be
extrapolated to precisely locate the earlier 1.10-second event. Both natural-end runs still had
five sent inputs incomplete. HF ended explicitly and returned results were drained; incomplete
inputs were neither resent nor counted as successful. Therefore both full-session reports remain
`complete=false/meets_target=false`, with acceptance exit code 1. Although completed frames in the
last run met latency targets, the full session cannot be described as complete delivery.

The remaining issue is network/cloud-handoff tail latency; there is no current evidence that further
reducing GPU compute would remove these pauses. One possible later evaluation is to return boxes
only and draw them in the browser to reduce response bytes, but this changes the returned-image
contract and was not implemented here. Lower resolution/precision, frame dropping, slow-frame
filtering, and paid hardware were not used.

Main reports are under `benchmarks/2026-10-03/`:

- `phone-browser-native-final.json`: original model/full local browser; worker confirmed 121 frames.
- `phone-relay-deployed-original-inputs.json`: original four-image quality/byte comparison and 184 public frames.
- `phone-browser-deployed.json`: full public page, 241 frames.
- `phone-browser-deployed-natural-end.json`: full session preserving the 1.10-second slow frame and five incomplete inputs.
- `phone-browser-deployed-traced.json`: full rerun with PRS timing, also preserving five incomplete inputs.
- `phone-live-hop-analysis.json`: per-frame interval statistics for the rerun; P95 uses the same linear interpolation as the browser tool.
- `phone-browser-deployed-traced-startup-failed.json`: deployment-start 502 without a GPU request; failure report retained.
- `phone-live-deployment-traced.json`: 18-file hashes, previous version, and config backups from the performance rerun.
- `phone-live-deployment-reviewed.json`: release after correcting only client module-relation comments; executable statements unchanged.
- `phone-live-comment-audit.json`: remaining 17 files were byte-identical, and the client file was byte-identical after its module header.
- `phone-live-server-final.json`: final service state, source/public-asset hashes, health, and HF-session release evidence.
- `phone-live-quota-before*.json`: read-only free-quota gates; post-test quota is recorded separately.
- `phone-live-quota-final.json`: final read-only query, with 142.54 free GPU seconds/four calls remaining; both seconds and call count are enforced.

The local test venv inherits system CUDA and adds the project's pinned Django/DRF/WS dependencies
to run the full page. Inherited but unused Streamlit/Pillow and lark-oapi/websockets packages have
version conflicts; `pip check` is not claimed to pass in that test venv. Production retains the
original pinned dependencies in `runtime-venv`, without installing CUDA or increasing the resource
budget.

## Deployment and Reproduction

At the user's request, server login information is stored in private `backend/server_info.txt`,
which Git explicitly ignores. `tools.deploy_phone_live` reads that file and private `.env.hf`, and
verifies the observed SSH host fingerprint. The server already has extra modes and an older HF
implementation with the same names, so new modules use distinct names and the new page is placed
under deployed `frontend/live/`. Only the new page/WS routes are combined with the existing server
implementation; old `hf_live.py/HFSession` are not overwritten. The local development root page
uses the same optimized source; the separate `/live/` mount is generated by the deployment tool.

Deployment copies the server's `current` tree to a new application directory, overlays 18 allowlisted
files, and verifies their SHA byte-for-byte. Generated assembly, comments/indexes, and manifest are
inspectable under Git-ignored `.space-bundle/phone-live/`. No local Git commit was made, so release
uses the manifest without mixing other workspace changes or credentials into the source archive;
single-commit atomicity is not claimed. Previous `current`, unit/include/env backups are retained.
Only PRS is restarted. Nginx is checked before a graceful reload; 256 MiB, 20% CPU, 32 tasks, one
worker, and existing TLS remain. A startup 502 showed that systemd marked the service active before
the socket was ready. The deployment script now performs a bounded 20-second local-listener check,
then one health request, before reloading the proxy. It does not retry external APIs, request GPU,
or roll back automatically. After GPU sessions end, the CPU health endpoint reports
`active_session=false`; PRS process memory measured about 39–42.3 MB and resource limits were not
increased. Current release is `phone-live-919785e958531fe3`; performance data is from the previous
`phone-live-7af1fda36c02237e`. They differ only in module-relation comments and this did not use
additional GPU quota. The new WS has a separate 65-second proxy budget; application first-frame
55-second/subsequent 10-second limits are failure budgets, not the 500 ms acceptance threshold.

Locally, Django check, migration dry-run, 81 backend/45 frontend tests, and Ruff passed; comments
and declaration/variable indexes were reviewed manually. The repository has no
`tools/check_docs.py`, and no automated documentation-check success is claimed. Server-side Django
check/migration dry-run, systemd-unit and Nginx checks, and public HTTPS page/health/original-entry
checks passed. systemd also emitted the existing snapd unknown-`RestartMode` notice; that service
was not modified. Original experimental GPU modes were not each rerun.

```powershell
# Original-byte/model comparison; the server checks free quota before requesting GPU.
.\.venv\Scripts\python.exe -m tools.benchmark_live_remote --url wss://47.239.50.129/prs/ws/detect/hf/ --origin https://47.239.50.129 --fps 12 --seconds 15 --output benchmarks/2026-10-03/phone-repeat-original.json

# Real browser page; requires local Chrome and an explicit test-dependency path. Never disable TLS verification.
$env:PRS_PLAYWRIGHT_PATH='<absolute path to the local Playwright package>'
node ../frontend/tools/benchmark_phone_browser.mjs https://47.239.50.129/prs/live/ 241 benchmarks/2026-10-03/phone-repeat-browser.json

# Natural session end; any incomplete input or slow frame still fails strictly and is not reported as full delivery.
node ../frontend/tools/benchmark_phone_browser.mjs https://47.239.50.129/prs/live/ session benchmarks/2026-10-03/phone-repeat-end.json
```

Every rerun consumes free quota; check that enough is available first. No scheduled rerun was set
up for this work. Phone CPU, physical camera, mobile network, and final display rendering still
require on-device verification.


## Simplified-Path Version (2026-10-03)

The page exposes only Echo and latest HF video; the root entry redirects to `/prs/live/`. Old-mode
page items, clients, HTTP/WS interfaces, HF APIs, server implementation, dedicated tests/scripts,
and configuration were removed. A complete version snapshot is retained outside the repository at
`saved_versions/phone-live-2026-10-03-919785e958531fe3.zip`; benchmark JSON was not deleted.

Executable AST for model/image functions matches the snapshot. CUDA execution program/model
manifest/test image/client scheduling/protocol comparisons are retained.
`phone-live-minimal-preservation.json` records 16 source comparisons. The new page on local Chrome
and RTX 4060 verified 61/61 frames, 10.902 FPS, and a 56.9 ms capture-to-decode maximum; this applies
only to local use and synthetic image input, not public service or real phones. Record:
`phone-browser-minimal-native.json`.

HF simplification commit is `8513c413a3bddca7482ce960220c38a2e0153753`; actual release/deletion
lists are in `phone-live-minimal-hf-deployment.json` and
`phone-live-minimal-hf-deployment-final.json`. PRS deployment, old-interface rejection, byte-exact
Echo return, public original four-image comparison, and browser rerun are recorded respectively
in `phone-live-minimal-deployment.json`, `phone-live-minimal-public.json`,
`phone-relay-minimal-original.json`, and `phone-browser-minimal-deployed.json`. The rerun retains
the same 10-FPS/500-ms criteria; historical tail-latency limits still apply, and short-test success
does not claim that they are resolved.

This repository does not provide `tools/check_docs.py`; declaration and variable indexes and their
descriptions must be checked manually against the implementation. Local checks included Django
check, `makemigrations --check --dry-run`, 53 backend tests, 36 frontend tests, Ruff, and real-browser
tests. This work did not create a local Git commit and makes no claim of single-commit atomicity;
the Hugging Face release has an explicit commit.


The simplified public run completed the original four-image comparison and 180-frame continuous
detection: all 13/13 boxes matched at the original IoU 0.5 threshold; all 180/180 steady-state frames
returned at 11.990 FPS, with 316.1 ms P95 and 350.4 ms maximum round-trip latency. Graph
initialization did not repeat. This run did not include physical-camera encoding or browser decoding;
a separate full-page rerun is recorded elsewhere. After removing uncalled eager/clip helpers, the
executable AST for `load_model`/`GraphRunner`/`export_program` still matched the saved version, with
no selectable legacy inference path retained. See the JSON above for data and provenance checks.

## GPU Allocation Rejection Diagnosis (2026-10-04, Beijing Time)

The final browser call in the simplified run did not reach a GPU worker. A controlled reproduction
using the same account, API, and default `@spaces.GPU` parameters confirmed the cause: the HF SSE
response returned `ZeroGPU quota exceeded`, specifically `90s requested vs. 78s left`. The exact
balance was 77.850766 seconds; HF rounded it to 78 seconds. One free call remained. The seconds and
call count were unchanged before and after rejection, `active_session=false`, and no GPU was
allocated. The original safe, structured record is in
`benchmarks/2026-10-03/hf-allocation-diagnostic.json`.

The token on the local machine matched the PRS server token in an in-memory comparison; the
account was a non-PRO free personal account. The evidence ruled out a token mismatch, exhausted free
calls, concurrent quota use, or a request duration above the account limit. No other identity or
paid hardware was used for reproduction. HF's rejection text directly confirmed the requested
duration.

The implementation defect was that preflight compared the balance against 60 seconds, while the
deployed, pinned `spaces==0.51.3` multiplies the default 60 seconds by the Blackwell
`duration_factor=1.5`, requesting 90 seconds from the scheduler. The package's `configs.json` and
`spaces.zero.client.get_duration_seconds` confirm the calculation. The earlier real-GPU report
identified the device as RTX PRO6000 Blackwell MIG2g.48gb. The official PyPI package was unpacked
read-only after its wheel SHA256 was verified. Thus, a 77.85-second balance passed the old 60-second
gate but did not meet HF's 90-second admission requirement.

The fix changes only the PRS/test-tool free-quota preflight to 90 seconds and records the required
seconds, classified error reason, and HF's rounded balance. The task's default duration (60 seconds),
video session (55 seconds), GPU size, model, FP32/640/0.25/JPEG80 settings, and client scheduling
remain unchanged. The 90 seconds are an admission reservation, not a fixed charge for each video or
a 90-second video runtime. Admission also checks the remaining free-call count. Preflight cannot
reserve HF quota or guarantee GPU availability.

Error parsing no longer reports insufficient quota merely because an error contains the word
"quota". It distinguishes seconds, call count, concurrency, duration, Space identity quota, no
available GPU, and worker termination; unknown errors retain inference-failure semantics. Logs
contain only the classified reason and numeric values, never provider response bodies, tokens,
session capabilities, or video. A later GPU performance rerun requires enough free quota for the
90-second reservation and at least one free call. No further GPU request was made after this
diagnosis.
