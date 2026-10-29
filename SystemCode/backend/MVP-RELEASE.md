# Phone perception MVP

## Release and scope

Version: `mvp-phone-2026-10-29.1`. Source branch: `codex/phone-speech-recovery`.
This patch includes the CPU inference test dependencies needed by GitHub CI; the accepted
phone/GPU runtime implementation is unchanged from the initial MVP tag.
This release combines the phone UI/audio/transport implementation with the resident GPU
optimization code in the repository's `SystemCode/` layout. It incorporates the current `main`
model update. The global detector default is the colleague's 87-class `yolo26n-walk`; the accepted
MVP launcher explicitly pins the original 80-class `yolo26n`. Do not compare the two models as
the same experiment. The release covers camera perception and risk speech. Live navigation,
the assistant provider and distance accuracy against physical ground truth are separate checks.

The operator accepted the physical iPhone Safari session as usable. Its sanitized receipt is
[phone-acceptance.json](releases/mvp-phone-2026-10-29/phone-acceptance.json). Metrics describe
that session, not a hardware-independent guarantee. Capture-to-decode age excludes camera sensor
buffering and physical display refresh. Local visual updates are optical-flow updates, not model FPS.

## Actual accepted deployment

```text
iPhone Safari (native camera preview + brief hints + local optical-flow boxes + Web Audio)
  -> HTTPS/WSS, Cloudflare temporary public hostname
  -> cloudflared HTTP/2 connector on the Windows GPU laptop
  -> 127.0.0.1:8766  integration.tunnel_asgi:application (web/ASGI gateway)
  -> 127.0.0.1:8767  integration.inference.asgi:application (resident CUDA worker)
  -> NVIDIA GeForce RTX 3060 Laptop GPU
```

The webpage and inference both run on the laptop. Cloudflare forwards requests; it does not run
the models. The accepted session's hostname and runtime identities are in
[deployment.json](releases/mvp-phone-2026-10-29/deployment.json). This is an operational snapshot,
not a durable service URL. The laptop and connector must remain running. Restarting a quick
tunnel normally produces a new hostname, printed by the launcher. The connector's removal of
inherited proxy variables does not bypass OS VPN routing or guarantee a nearby Cloudflare edge.

The previously supplied `47.239.50.129` / Ubuntu 22.04 server is the older web/relay host, with no
GPU. It is **not in this accepted session's image/inference path**. Its historical inference
forwarding endpoint was loopback port `18767` connected to a colleague's GPU. That route is not
started or changed by this release. No claim is made here that the old server/GPU path is online.

Startup voice and risk warnings use repository-owned, checksum-verified prepared audio. The
accepted supervisor also had an optional existing TTS upstream for wording outside that pack;
this is separate from image inference. The new portable launcher has no default TTS upstream.
Set `--tts-upstream` explicitly only when testing an owned speech service. No ORS, assistant,
HF or DashScope key is required for camera detection and the prepared risk voice pack.

## Fixed accepted settings

| Component | Setting |
| --- | --- |
| Host/runtime | Windows 11, Python 3.12; Torch 2.8.0+cu126, CUDA 12.6, Ultralytics 8.4.171 |
| Detection | Original YOLO26n, native FP32, persistent model, captured CUDA graph |
| Depth | YOLO26n Depth, TensorRT 10.13.3.9 FP32, TF32/FP16/INT8 disabled |
| Depth strategy | Same-frame depth, interval 1; host ROI reduction; original risk rules |
| Capture | JPEG quality 0.8, max edge 640, `low-latency-2`, at most two pending frames |
| Transfer | Same-origin binary WebSocket; `metadata-v1` replies; no returned annotated JPEG |
| Transport admission | Wait for existing browser send-buffer bytes before sampling another frame |
| Display | One native preview; all eligible recent boxes get 150 ms historical hints |
| Local tracking | At most six risk-prioritized boxes; 320 px worker input, target 12 Hz |
| Freshness | Original capture age retained; stale/unreliable tracks removed; no risk fabricated by tracking |
| Voice | 96 prepared risk clips plus two startup clips; spoken risk/direction/object content |
| Warning age | Maximum 2000 ms, checked again when playback starts |
| Service boundary | Loopback listeners, exact public Host/Origin, mobile path allowlist; one inference session |

Six is a local tracking budget, not a detector output cap. Additional detections receive brief
hints. The export reports tracking exclusions, losses and undrawn boxes. Zero undrawn detections
means every returned eligible box was submitted to the canvas at least once; it does not establish
model recall, correct localization or that every physical object was seen by the model.

The accepted depth bundle covers NCHW shapes `1x3x768x576`, `1x3x768x608`, `1x3x576x768`,
`1x3x608x768` and `1x3x448x768`. Other aspect ratios, including square `768x768`, need an explicit
export/build/validation. Unsupported shapes fail instead of silently switching to another backend.

## Download and prepare

```bash
git clone --branch mvp-phone-2026-10-29.1 --single-branch https://github.com/heropig114514/IRS-PM-2026-09-07-AIS08FT-GRP40-GUIDESIGHT-AI.git
cd IRS-PM-2026-09-07-AIS08FT-GRP40-GUIDESIGHT-AI/SystemCode/backend
```

An NVIDIA CUDA GPU and compatible driver are required. The commands below use Windows and
Python 3.12. Linux uses `.venv/bin/python` and `.venv-inference/bin/python`; Linux/other GPU
execution must be validated on that host. CPU-only/macOS execution is not this deployment.

```powershell
py -3.12 tools/local_demo.py prepare --cuda cu126 --with-tensorrt
```

This explicit preparation installs the pinned Web/GPU/export environments and downloads the
official models identified in `deploy/local-demo.toml`. If the files are already present under
`models/`, their full hashes must match. Trusted weights are loaded only after checksum validation.
The original `yolo26n.pt` / `yolo26n-depth.pt`, generated TensorRT engines, virtual environments,
private configuration and runtime logs are excluded from this upload. The colleague's model files
already tracked on `main` remain unchanged. Code, manifests, audio assets, tests and build tools
are versioned.

Build and compare a new depth engine bundle **on the deployment GPU** using
[DEPTH-EXPORT.md](DEPTH-EXPORT.md), with `.venv-inference/Scripts/python.exe` and portrait,
landscape and widescreen JPEG inputs matching the cameras being used. Use a fresh artifact
directory and report for each build. For example, after preparing those representative images:

```powershell
$inputs = Get-ChildItem models/experiments/my-camera-inputs/*.jpg | ForEach-Object FullName
.\.venv-inference\Scripts\python.exe -B -m tools.benchmark_depth_export --weights models/yolo26n-depth.pt --inputs $inputs --artifacts models/experiments/my-gpu --output benchmarks/my-gpu-validation.json --phase all
```

Inspect the numerical comparison and shape coverage before activating the resulting manifest.
Engines are bound to checkpoint hash, GPU/runtime identity, SDK sources and input shape; do not
copy the author's engine as a portable binary. Fine-tuned final weights need a new manifest,
re-export and validation. A portable Torch baseline is separately documented in
[LOCAL-QUICKSTART.md](LOCAL-QUICKSTART.md); it is not the accepted TensorRT preset.

## Start the accepted public preset

Supply a trusted `cloudflared` executable and its independently verified SHA256. The accepted
Windows connector was version `2026.10.5`, SHA256
`db4e4c413612a0916f3e1679d3caaf376f94911952d163028fb726fb2e0516a9`.
Other platform binaries have different hashes. The launcher does not download or update it.
Keep the downloaded executable outside tracked source, for example `.venv/tools/cloudflared.exe`.

```powershell
$options = @('--engine-manifest', 'models/experiments/my-gpu/manifest.json', '--cloudflared', '.venv/tools/cloudflared.exe', '--cloudflared-sha256', 'db4e4c413612a0916f3e1679d3caaf376f94911952d163028fb726fb2e0516a9')
.\.venv\Scripts\python.exe -B -m tools.run_public_mvp @options --check
.\.venv\Scripts\python.exe -B -m tools.run_public_mvp @options --state-dir .venv/tmp/public-mvp/session-1
```

`--check` verifies environment, CUDA, weights, executable and ports without a public tunnel.
Run performs a real eight-frame GPU warmup before publishing a URL. Warmup covers the fixture's
shapes only. The supplied manifest must cover the phone's actual shapes too. Keep the terminal
open; stop with Ctrl+C or create `stop.request` inside that exact session directory. Only owned
children are terminated. Use a new state directory for another run. Occupied ports fail; choose
matching explicit `--web-port`, `--inference-port` and `--metrics-port` if needed.

Open the printed `https://...trycloudflare.com/mvp/maps/` on the phone. Use “试听／恢复语音”,
then “开始检测”, allow the rear camera and keep Safari in the foreground. The diagnostic page
is the same origin at `/mvp/maps/test`. No certificate profile or common Wi-Fi is required for
this public path. Frames and requests transit Cloudflare; the random URL is an access address,
not authentication. Keep this single-session demo limited to intended testers and stop it afterward.

## Verification and operation

The live panel exports first-frame time, steady capture-to-decode P50/P95, server time, completed
model FPS, optical-flow update rate, per-frame display counts and speech outcomes. Record these
separately. Native-preview smoothness is independent of model completion FPS. Network conditions,
scene density and hardware can change the results. No automatic model/precision/queue fallback
is used to conceal errors.

Run `python manage.py check`, `python manage.py makemigrations --check --dry-run`,
`python manage.py test` and `ruff check .` from the prepared backend environment. Run
`node --test` from `SystemCode/frontend`. Physical phone playback confirmation and the real
phone export remain separate evidence from unit tests and desktop synthetic camera checks.
The release receipts keep those evidence types distinct and exclude frames, coordinates and keys.
