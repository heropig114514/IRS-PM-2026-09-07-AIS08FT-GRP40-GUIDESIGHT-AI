---
title: Prs Yolo26
emoji: 🚀
colorFrom: green
colorTo: pink
sdk: gradio
sdk_version: 6.29.0
python_version: '3.12.12'
app_file: app.py
pinned: false
license: mit
short_description: prs-yolo26
---

The only inference API is `detect_live`, paired with `/prs_live/ws`. The owned PRS page offers
Echo or this bounded ZeroGPU live path. Retired single-frame, clip, segmented stream, boxes and
profiling APIs have been removed. Source weights remain SHA256 verified; YOLO26n/640/
confidence0.25/JPEG80 remain fixed. `models/inference-plan.json` declares the measured compute
precision/backend and the same-version COCO evidence: all three independent full5000-image
confirmations must lose no more than0.5 AP percentage points against the fair FP32 baseline.
Inference outputs and the public transport remain FP32. Missing/invalid plans stop startup.

The selected compiled plan builds once in a separate startup allocation on the actual HF GPU.
The T4 best repeated pure-forward configuration is FP16, Conv/BN fusion, channels-last,
torch.compile(default/fullgraph/static), reusable pinned buffers and CUDA Graph. Its original
configuration and three full COCO records are retained; deployment policy is stored separately.
Pinned Torch2.8 official save/load_cache_artifacts and owned persistent Inductor/Triton directories
carry compiler artifacts across allocation workers. HF can reuse the same worker PID, so each
allocation resets Dynamo frontend state before guarded restoration of the persistent disk cache.
Each worker validates model/config/GPU/
Torch/CUDA/Triton/content identity, requires an FX cache hit, blocks new backend code generation
during restoration and fails on later recompilation. Cache misses are errors, not rebuilds.
Kaggle T4 binaries are never copied to a different HF GPU; HF startup generates its own cache.
The measured AOT adapter remains an explicit supported plan, with a verified serial package loader
and native-file cleanup. Neither implementation is an automatic fallback for the other.

One connected session then requests one GPU allocation with the default ZeroGPU hardware/duration.
The worker reuses an allocation-local CUDA Graph for at most55 seconds; stop/disconnect/idle
ends the task. A new test requires an explicit new start; no paid hardware or automatic allocation.
Private HF identity and distinct worker capability stay server-side. Frames are not saved to disk.
The30FPS configuration admits twelve frames across PRS/HF/browser including ordered display.
All three strict transport contracts are published together; the input ceiling does not prove
achieved HF output FPS. Real allocation measurements are recorded separately. The prior
five/eight-credit source snapshots and measurements are retained. Model/codec/quality conditions
are unchanged. CPU browser controls do not prove real HF30FPS. Per-frame
hf_parent_queue_ms/local_cycle_ms/result_ingest_ms/output_queue_ms use
the CPU parent's clock; worker_pull_ms includes intentional paced-input waits, while
worker_predict_ms includes the entire predictor call. Durations are nested and not additive.
No clocks are subtracted across machines/processes, and diagnostic fields add no network calls.

The first prepared result now waits for exactly one512KiB random binary control to travel
HF→PRS→HF on the existing WebSocket. Admission declares `warmup_bytes`; missing/malformed
controls, corrupt/duplicate echoes and a3s timeout fail explicitly. This1MiB/session transport
warmup is included in first-frame latency and does not consume image credits, change sequence
numbers, trigger another GPU allocation, or alter inference/JPEG bytes. The worker's5s idle,
55s session and relay's55s cold budgets remain unchanged. First-result metadata records
`hf_transport_warmup_ms` and duplex `hf_transport_warmup_bytes`; its output-queue span includes
this intentional control wait. Later frames are neither filtered nor labelled as warmup.

`ssr_mode=False` makes Python ASGI serve the public port so custom WebSocket upgrades reach
LiveMiddleware. Gradio queues one allocation; only `detect_live` is registered. CPU health
`/prs_live/healthz` does not allocate GPU or prove inference performance. Frame metadata records
actual GPU/CUDA/Torch, compute/output dtypes, pinned buffers, Graph setup and runtime flags;
cached JIT also returns artifact SHA, worker PID, restoration time and compiler hit counters.
forward_event_ms measures CUDA Graph replay alone, separately from transfer-inclusive inference_ms.
Startup compilation, allocation loading/capture and steady frame costs are separate measurements.
HF verification uses eight ordered public fixtures per allocation; it checks cache/Graph/output
integrity without long video runs, full COCO repetition or an HF method-ranking claim.

Ultralytics software and YOLO26n weights use AGPL-3.0 or separately acquired commercial terms.
Existing MIT repository metadata does not relicense dependencies. Deployment source is maintained
in the PRS project under `backend/zerogpu`; `tools.deploy_space` stages an explicit source allowlist.
