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
