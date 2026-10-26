# Local MVP Integration and Deployment

When the phone and computer are not on the same local network, the phone can reach the same local unified entry through the existing public HTTPS guest relay. See [Phone MVP](PHONE-MVP.md) for preparation, startup, and shutdown.

## Unified Navigation and Perception Entry

Start the existing two environments from `backend`:

```powershell
.\.venv\Scripts\python.exe -B -m tools.run_local_mvp --assist --check
.\.venv\Scripts\python.exe -B -m tools.run_local_mvp --assist --log-dir .venv/tmp/assist
# If an existing private ORS configuration file is needed, specify it explicitly; only ORS_API_KEY is read.
.\.venv\Scripts\python.exe -B -m tools.run_local_mvp --assist --ors-env <private-config-path>
```

Open `http://127.0.0.1:8766/maps/`. The unified entry uses `integration.assist_asgi` and reuses the original map, route planner, `navigation-ui` navigation instance, and output MCP. The product interface makes the voice assistant, navigation, and environmental awareness the primary actions. Set the origin from the current location and choose the destination on the map; bindings needed only by hidden controllers for coordinates and diagnostics are not exposed.

Speech output, navigation announcements, and risk alerts are enabled by default on the unified page. Vibration is also enabled by default when the browser supports the Vibration API. Announcements follow the interface language and automatically select a matching local voice, preferring exact language tags and the system's default local voice. If no matching voice exists, the page reports that condition without using another language or a remote voice. Speech rate is fixed at 2× and app volume at 100%; adjust actual listening volume with the device controls. The Voice assistant supports hold-to-speak and release-to-send, as well as text input. Speech and vibration settings remain in the shared controller interface and are not shown as frontend controls.

The original map module still obtains the origin, selects the destination, plans the route, and starts navigation. The UI and MCP share the same location watcher. Selecting “Environmental awareness” at the top or “Start detection” on the card requests the current device's rear camera and starts detection. On a phone, this uses the phone camera without requiring device selection. The raw camera image is used only as internal model input; the interface displays only the returned Detection results annotation and target list. Bounding boxes come from the existing algorithm. If the browser does not support the rear-camera constraint, permission is denied, or no rear camera is available, the request fails explicitly without switching to the front camera or requesting permission again. Users can stop while permission is pending; a late media stream is closed and does not start detection.

The unified test entry is `http://127.0.0.1:8766/maps/test` (no trailing slash) and must be opened directly. It retains diagnostic features such as manual camera selection, voice/rate/volume controls, speech testing, the NUS example, video replay, raw detection results, and statistics. Voice selection remains manual. Video replay and the camera may not run at the same time. A test video does not represent the road where the device is located. The product and test pages have separate sessions; stop current input before switching to avoid competing for the inference service.

Enabled output switches do not imply that device permissions were granted, input started, or sound was produced. Page load does not request camera or location access, start navigation, plan a route, or submit speech. When a local voice is available, “Start speech” or the first real click on camera detection, start navigation, or test-page video detection synchronously submits a startup prompt in the corresponding language. The user does not need to play a sample in the manual speech test area first. The isolated `SpeechActivation` gate blocks automatic navigation and risk speech until the current calibration request receives the native engine's `start` event. The status indicator distinguishes waiting, submission, and engine start; an accepted submission alone does not unlock output. If no matching voice exists, playback does not start, or an error occurs, the user follows the prompt and explicitly starts again; there is no automatic retry. Voice-list or interface-language changes update the match without playing automatically. An engine start event still does not prove that the speaker is audible; iPhone Safari does not support the Vibration API. This default policy applies only to the unified entry; the independent test pages described below keep their output switches off by default.

`AssistGuidanceManager` is the unified page's only automatic-output owner. It consumes the existing detection class, direction, risk level, and TTC fields, reusing the video short-phrase policy; navigation text reuses existing step/status wording. Risk alerts take priority over navigation. When an alert ends, only the latest unexpired navigation candidate is used; completed steps are not repeated. Turning off perception does not stop navigation, and stopping navigation does not stop perception. “Stop all”, page hiding, and page exit clear both inputs and owned outputs. Returning to the foreground requires a manual restart. If cancellation fails, ownership and the error-pause state are retained; the user must explicitly stop successfully before manually resuming. There is no automatic retry. Existing shared drivers protect manual speech and vibration requests from automatic alerts.

This mode explicitly selects a continuous local session, with `session_seconds=null` in the hello message. Models remain resident in the separate inference process; each connection resets tracking state, and automatic reconnection is not used to keep a session alive. The original independent entry retains its 55-second session. The 55-second first-frame and 10-second subsequent-frame budgets, 5-second idle timeout, 640/JPEG quality 80, FP32, A/B/C algorithm configuration, and risk rules remain unchanged. Browser capture time continues through PRF2 into the algorithm; GPS uses a separate Unix clock, and the two clocks are not subtracted from each other.

Local inference does not access HF. Online maps and route planning still require Leaflet, OSM, and ORS. `--assist` permits the Web process to use ORS credentials only when explicitly supplied through process environment or `--ors-env`; credentials are not provided to the inference process or browser. HF and DashScope remain disabled. Without ORS configuration, the page reports that route planning is unavailable and does not substitute an example route. Default offline startup does not enable any external service when the unified mode is added. In unified mode, the root path redirects to `/maps/`; `/live/` and `/integration/video/` return an explicit incompatibility notice so an old 55-second client cannot connect to a continuous session. To run those pages separately, use the default offline launcher; do not mix the two session contracts.

Validate the real model, browser speech events, synthetic GPS/ORS integration, actual device location, and real ORS routes separately. Passing feature tests does not establish model throughput, risk/distance accuracy, audible output, real phone walking, background operation, or cloud deployment. The unified page does not infer obstacle-avoidance routes from visual targets or claim that a road is safe to traverse.

### Browser Integration Validation

From `backend`, use the existing Playwright and Python installations. Report paths must not already exist:

```powershell
$env:PYTHON = (Resolve-Path .venv/Scripts/python.exe).Path
$env:PRS_PLAYWRIGHT_PATH = '<absolute path to installed Playwright package>'
node ../frontend/tools/check_assist_browser.mjs http://127.0.0.1:8766/maps/test .venv/tmp/NEW_ASSIST.json model_tests/datasets/street-video/mot17/MOT17-07-FRCNN-browser-h264.mp4
node ../frontend/tools/check_assist_lifecycle_browser.mjs http://127.0.0.1:8766/maps/test .venv/tmp/NEW_LIFECYCLE.json
```

The first tool uses real Chrome, MCP, and Chinese speech. GPS/ORS use explicit fixtures; synthetic risk arbitration and real video inference are saved separately. It checks each request ID for engine start, completion, or confirmed cancellation, and masks coordinates and message bodies in screenshots. Without a video path, it validates navigation and synthetic risk only; it does not establish model validation. The second tool uses an explicit canvas media-device fixture to run the original camera controller with real local inference. It checks manual/track termination, control unlocking, and cleanup of late permission, device-list, and video-metadata responses after hiding again. It does not request the physical camera or validate speech. Neither tool starts a server, downloads test assets, or replaces Leaflet dependencies; external-resource failures remain explicit in the report.

## Default Offline Mode

Native depth can explicitly select the verified FP32 TensorRT implementation through
`PRS_DEPTH_BACKEND` and `PRS_DEPTH_ENGINE_MANIFEST`; see [Depth deployment](DEPTH-EXPORT.md).
Prepare the optional GPU dependency and exact input-shape artifacts before startup. The handshake,
health response and result metadata identify depth separately from detection. Missing/stale engines
or unsupported shapes fail explicitly. An unconfigured service continues to use native Torch.

The default algorithm remains dual-model mode A. See [distance modes](DISTANCE-MODES.md) for environment variables, startup, and output compatibility when explicitly selecting geometric mode B or low-frequency depth mode C. The dual-model requirements below apply to default A; B does not require depth weights.

This stage integrates the camera/local video, algorithm results, browser alerts, and navigation examples on one computer. The integration layer adapts protocols, processes, and session lifecycle without changing detection/depth models, tracking association, distance/motion estimates, TTC, or risk rules. HF, ORS, and DashScope are not part of this local inference loop. See [ZeroGPU](ZEROGPU.md) for public-cloud mode configuration and the [operations guide](deploy/README.md) for deployment.

## Operational Boundaries

```text
Browser http://127.0.0.1:8766/live/
  └─ Web/integration process: backend/.venv
       ├─ integration.asgi → original Django, Echo, control WebSocket, and output MCP
       └─ same-origin local detection WebSocket → ws://127.0.0.1:8767/ws/infer/
            └─ inference process: backend/.venv-inference
                 └─ ResidentDetector → YOLO26n → BoT-SORT → YOLO26 Depth
                    → distance/motion state → TTC/rule-based risk → annotated JPEG and object metadata
```

Each process uses one worker. The Web environment does not install Torch; the GPU environment does not need Django, MCP, Gradio, or a Space account. The inference adapter calls the original `realtime/` algorithms directly. Each video session has an isolated tracker/history; reusing a model and Graph does not reuse target identity across sessions. Browser Guidance consumes server alert fields and does not recalculate risk. Existing Echo/HF entry points and external API implementations remain available; the local launcher explicitly disables their external credentials.

The new integration entry is `integration.asgi:application`; the original `config.asgi:application` remains independently runnable and does not thereby gain a local inference path. The inference entry is `integration.inference.asgi:application`. Both processes listen only on `127.0.0.1`. Default ports are Web 8766 and inference 8767; the launcher can explicitly select alternatives.

## Environment and Model Setup

From the repository root, enter `backend` and use Python 3.12. A base interpreter available on the local Windows machine is
`C:\Users\Kolosin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`；
Replace this with the Python 3.12 path on another computer. If an environment already exists, skip creation; do not overwrite another project's environment.

```powershell
cd backend
$localPython = 'C:\Users\Kolosin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $localPython -m venv .venv
& $localPython -m venv .venv-inference

# Web and development checks; do not install native CUDA dependencies here.
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt --retries 0

# The local RTX 5070 Ti Laptop uses the official CUDA 12.8 wheel; base versions remain Torch 2.8.0/Torchvision 0.23.0.
.\.venv-inference\Scripts\python.exe -m pip install -r requirements-inference.txt torch==2.8.0+cu128 torchvision==0.23.0+cu128 --extra-index-url https://download.pytorch.org/whl/cu128 --retries 0 --timeout 60

.\.venv\Scripts\python.exe -m pip check
.\.venv-inference\Scripts\python.exe -m pip check
```

`requirements-inference.txt` separately pins native inference dependencies. Do not mix it with `requirements-vision-build.txt` (Torch 2.5.1/Torchvision 0.20.1), which is used for offline export. `requirements-depth.txt` remains for existing combined-model tests and is not the dependency manifest for the new pure-GPU service environment. See [PyTorch previous versions](https://pytorch.org/get-started/previous-versions/) for official installation commands. The local GPU is an NVIDIA RTX 5070 Ti Laptop GPU with 12 GB VRAM; capacity alone does not prove model throughput or latency.

Place the explicitly downloaded official `v8.4.0` weights in `backend/models/`. The service never downloads or switches models automatically.

| File | Official source | Weight SHA-256 |
|---|---|---|
| `yolo26n.pt` | [Ultralytics release](https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26n.pt) | `9b09cc8bf347f0fc8a5f7657480587f25db09b34bf33b0652110fb03a8ad4fef` |
| `yolo26n-depth.pt` | [Ultralytics release](https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26n-depth.pt) | `befed1b8561d8b2eaa66274b070cdfc9c44853bda6d6d62a04759f9383af74e9` |

```powershell
Get-FileHash -Algorithm SHA256 models/yolo26n.pt, models/yolo26n-depth.pt
.\.venv\Scripts\python.exe -B -m tools.run_local_mvp --check
```

`--check` verifies both interpreter versions, direct-dependency pins, actual CUDA availability, model hashes, and ports. It does not write configuration, start services, or run model predictions. A successful check does not mean real dual-model inference or browser behavior has been verified. Missing packages, unavailable CUDA, missing/mismatched weights, or occupied ports fail explicitly; there is no CPU or single-model fallback. Weights and both virtual environments are ignored by Git. See [models/README.md](models/README.md) for official models and licensing boundaries.

## Startup and Shutdown

```powershell
.\.venv\Scripts\python.exe -B -m tools.run_local_mvp

# To save both child-process logs under an ignored venv directory:
.\.venv\Scripts\python.exe -B -m tools.run_local_mvp --log-dir .venv/tmp/local-mvp

# Explicitly use other loopback ports; the launcher does not search for free ports:
.\.venv\Scripts\python.exe -B -m tools.run_local_mvp --web-port 8776 --inference-port 8777
```

The launcher only creates/reads a random `DJANGO_SECRET_KEY` in the private `.env.local-mvp`; it does not overwrite `.env` or print secrets. It explicitly sets `HF_TOKEN`, `HF_INFERENCE_TOKEN`, `ORS_API_KEY`, and `DASHSCOPE_API_KEY` to empty for child processes so `config.base` cannot implicitly enable external services by reading an existing `.env`. It also sets `DJANGO_SETTINGS_MODULE=config.settings` and `PRS_MCP_PROFILE=outputs`. The default Web SQLite file remains `backend/db.sqlite3`; the local MVP does not depend on business-account or database writes. The launcher does not install, download, migrate, deploy, or restart failed processes.

The GPU child process explicitly sets `YOLO_OFFLINE=1`, so the pinned Ultralytics version returns `offline` during online-state initialization without performing Cloudflare/Google DNS probes. `YOLO_AUTOINSTALL=0` disables automatic package installation. `YOLO_CONFIG_DIR` points to `backend/.venv-inference`; library settings are written in its `Ultralytics/` child directory instead of the user's Roaming configuration. `--check` uses the same environment settings but only queries package metadata and imports Torch; it does not import Ultralytics or create the settings directory. These settings isolate runtime environments; they do not change algorithm implementations or experiment parameters.

The GPU process starts first, followed by Web. Each has at most 55 seconds to begin listening, followed by one HTTP health check; no test frame is sent. `/health/` reports GPU-process state and `/integration/status/` proxies that state through Web. Initial `loaded=false/ready=false` is normal cold-start state and means the first-frame model load has not happened. `accept_available` means a session can be accepted; listening or HTTP 200 alone does not prove inference succeeded. Database health at `/api/health/` is separate from inference health.

The launcher runs in the foreground by default and child output appears in the current terminal; Windows children do not open new windows. Ctrl+C stops only the two child processes owned by this launcher, Web first and then GPU. A child that has not exited after 10 seconds is terminated through that same process handle. The launcher does not kill by process name or persistent PID and does not affect other projects. Optional logs are stored in a separate directory for each run.

## Local Operation and Contract

Open the printed `/live/` page, read the local camera list, and explicitly choose a camera. If the device ID is empty on first permission request, click “Authorize and read camera list”: this requests video permission only and immediately stops temporary tracks; it does not start the algorithm or select a device automatically. After authorization, choose a device explicitly, select local inference, and use the original start button. The app does not switch cameras automatically if a device disappears.

Defaults remain detector size 640, confidence 0.25, JPEG quality 80, native detection FP32, and depth size 768/FP32. Local requests use PRF2 (sequence, browser capture time in seconds, original JPEG); results reuse PRS1. The original HF PRF1 protocol remains unchanged. Limits remain JPEG 1 MiB, JSON 256 KiB, and a 1,310,728-byte total result frame. The adapter validates ordering, strictly increasing capture time, image SHA, and the actual compute identity of the selected mode. It passes capture time to the existing `predict(..., timestamp=...)` interface without changing algorithm speed-estimation rules; timestamps are not subtracted from another machine's clock. COCO evidence for the original HF FP16 compilation is not evidence of local native FP32 dual-model performance.

Scheduling and failure budgets remain an input cap of 30 FPS, 12 in-flight frames, 55-second sessions, a 55-second first-frame budget, and 10-second subsequent-frame budgets; the worker ends after 5 seconds without input. The input cap is not measured output FPS, and failure wait budgets are not the 500 ms acceptance threshold. Stop/disconnect ends the current session and releases in-flight work; a new session clears tracker/history. There is no automatic reconnection or retry. The first frame includes weight loading, model initialization, and Graph preparation and must be recorded separately from subsequent frames.

After the user enables Guidance, the browser selects outputs using server-side `alert_required`, `alert_priority`, and `risk_level`. Speech and vibration remain subject to their respective user switches, foreground state, and device APIs. See the [frontend guide](../frontend/README.md) for scheduling, deduplication, and navigation-example behavior. Disabling alerts, stopping the stream, or hiding the page stops related output. Speech submission/engine-start events do not prove actual sound, and acceptance by the Vibration API does not prove the motor vibrated. The desktop local stage primarily verifies protocols and speech-engine events; a real phone's motor, speaker, permissions, and mobile network require separate acceptance.

Navigation examples in Guidance demonstrate arbitration between navigation events and risk alerts only. See the standalone navigation-replay entry below; neither proves device GPS, actual roads, or navigation accuracy. Maps and scene description remain optional external APIs. Before calling original handlers, the local integration entry rejects HF/ORS/DashScope paths, and the launcher clears their credentials. The local inference loop must not be described as running the map or vision-language model offline.

## Standalone Local Navigation Replay

The entry `http://127.0.0.1:8766/integration/navigation/` is served by the same Web process. Load the synthetic sample or select a local replay JSON, explicitly choose a local voice, enable speech output and navigation announcements, then start replay. The default rate is 2× and can be adjusted manually. The page does not play automatically, choose a voice, or enable output switches. The approximately 36-second built-in sample covers departure, turns, poor accuracy, off-route state, recovery, and arrival; replay can be stopped manually at any time.

The page injects locations through the separate `navigation-replay.js` interface and reuses the original `WalkingNavigation` route, accuracy, 15-second location lifetime, 40-meter off-route, and arrival rules. `navigation-speech.js` connects existing steps/status to the shared speech driver. The integration layer does not change navigation calculations, map client, algorithms, or video-alert policy. Each step is spoken only once; distance to the next action is not described as distance to the current action, and “destination ahead” does not mean “arrived”. Invalid location cancels old turn guidance. Only the latest speech candidate is retained, and a not-yet-started request keeps its original location lifetime. Stop, hide, or page exit clears replay and owned speech. Replay does not automatically continue, cancel manual speech, or retry errors. If speech cancellation fails, ownership, paused state, and stop control are retained; the user must explicitly stop successfully before manually resuming.

The Web wrapper adds only a fixed page and asset allowlist. Fixed aliases `/integration/navigation.js` and `/integration/maps-client.js` provide the unchanged shared modules and preserve module identity. The replay document's Permissions Policy blocks device location, camera, and microphone. The page does not load external maps, ORS, AI control, video, or inference WebSockets. It does not fall back to a sample location when an external navigation service is unavailable.

Replay JSON uses `schema_version=1`, `source=synthetic`, non-empty `label`/`description`, the original map-contract `route`, and `fixes`. Each location contains `offset_ms`, `longitude`, `latitude`, and `accuracy_m`; there are at most 2,000 points, the first offset is 0, later offsets strictly increase, the maximum offset is 2,147,483,647 ms, and the file limit is 1 MiB. Routes and steps each contain at most 5,000 points. Location timestamps equal the replay start time plus the original offset; scheduling delays do not refresh them. See [frontend navigation replay](../frontend/README.md#standalone-local-navigation-replay-and-speech) for the sample and operation. Every input is explicitly synthetic and does not prove real GPS, intersection distance, walking speed, or road passability.

Start the local service first, then run the independent native-Chrome check from `backend`. The output path must not exist:

```powershell
$env:PRS_PLAYWRIGHT_PATH = '<absolute path to installed Playwright package>'
node ../frontend/tools/check_local_navigation_browser.mjs http://127.0.0.1:8766/integration/navigation/ .venv/tmp/NEW_NAVIGATION_REPORT.json
```

The tool does not use GPU or online maps. It preserves navigation, replay, and speech lifecycle evidence and checks the actual engine text match, source, rate, complete playback, stop cleanup, and local-request boundary without recording speech text. Supplementary boundary checks explicitly delay same-origin sample-response delivery to verify that late reads after stop/hide are invalid; original payload content remains unchanged. An explicit visibility substitute verifies cleanup on hide and manual restart, but does not represent operating-system background testing. A single explicit rejection is injected into the local cancellation interface to verify ownership retention, one stop attempt without retry, and later explicit cleanup. Speech submission, engine start, completion, and successful cancellation are paired by request ID; one request's completion cannot prove another request finished. Supplementary checks preserve the native speech engine and events; the full replay uses native reads and speech. Engine completion does not prove the speaker is actually audible. This page remains standalone replay; real navigation/perception integration uses the explicit unified entry above and requires separate acceptance evidence.

## Existing Map Navigation and Speech Bridge

The `/maps/` page served by the original `config.asgi:application` continues using the existing navigation-side route planning, location, step, and state instance. The map page adds a speech control that is off by default. `integration/maps-navigation-bridge.js` consumes existing `prs-navigation-change` events and passes them to the existing `NavigationSpeechAdapter`; it does not create a second location watcher or change the navigation core, ORS business logic, algorithms, or video. UI and MCP start/stop continue through the original navigation module. Assets are served through the exact shared `/assets/integration/` allowlist; the original `/live/assets/` route retains its single-segment limit.

Speech defaults to 2×. Users must explicitly choose a local voice and enable both output switches. Terminal state comes from the original navigation snapshot. Stop, route change, hide, and page exit clear owned speech. Stopping speech alone does not change location or route; speech remains stopped for this run until navigation is explicitly started again. If cancellation fails, ownership is retained; the user can manually clear the error pause only after explicit successful cleanup. See the [frontend guide](../frontend/README.md) for detailed operation and native-Chrome verification commands.

The offline MVP wrapper still blocks `/maps/`, and the launcher still clears external credentials. Map speech integration uses the original ASGI entry; synthetic GPS/ORS fixtures and real Chinese speech are declared separately. It does not require GPU and does not count as real-location or ORS verification. The original map page's Leaflet/OSM network dependencies remain; tests do not implicitly replace resources or retry failures.

## Continuous Local Video Entry

The independent entry is `http://127.0.0.1:8766/integration/video/` and runs under the same launcher. Select a video file on the computer and explicitly start playback. The page decodes locally in the browser using File/Blob and HTMLVideo. It does not request computer-camera permission, use a fake camera, or upload the entire video file. Only captured JPEG frames and original PRF2 metadata enter the existing dual-model inference path through the same-origin WebSocket. Confirm that the selected file actually plays in the browser; the MP4 extension alone does not prove decodability. The page does not transcode or replace media implicitly.

The new page reuses `LiveDetectionClient`, `FramePipeline.dispatch/drain`, Guidance, and native speech/vibration output. Camera `FramePipeline.run`, `camera.js`, and `local-mvp.js` are not rewritten for video input. To receive alerts, users must still enable Guidance and the relevant output and explicitly choose a matching local voice.

The video page uses a separate TTC/risk-level speech policy. It considers only targets the algorithm marks as requiring an alert, selects by descending priority, then by valid positive TTC, current distance, and track ID. Medium/high/critical risk uses corresponding caution/warning/danger wording while preserving the algorithm's level. Speech gives only the target's direction relative to the image and class; it adds “approaching” only for faster approach and omits that word for slower motion without lowering the risk level. The approaching wording requires `motion_state=approaching`, finite negative relative speed, and both backend `risk_reasons` markers `approaching` and `closing_mps_ge_2`. It reuses the existing ≥2 m/s band and adds no speed threshold or inference from TTC. If the algorithm changes its speed bands, update the wording contract as well. Unconfirmed, stationary, receding, or inconsistent states must not be described as approaching. Classes come from validated detector labels; the Chinese mapping covers all 80 model classes, unmapped labels are called “object”, and English preserves the original label. Example wording: “Danger, a pedestrian is approaching from the front left” or “Caution: a pedestrian is ahead on the right.” Direction is not expanded into geographic orientation or avoidance advice. Distance, TTC, and speed values are not spoken, nor are unknown-value/action monologues appended; original fields remain available for display and target selection. The original 1-second source-frame validity, 3-second actual-start cooldown, priority escalation, and manual-output protection remain. Video speech defaults to 2×; rate/volume remain user-controlled and do not change with risk level.

The video entry lets an already-started alert finish naturally; a short empty alert or an expired latest source frame does not truncate a sentence already playing. Higher-level risk can still interrupt immediately. This policy does not extend source-frame validity: not-yet-started expired requests are still cancelled, expired results create no new alert, and old targets are neither queued nor replayed. Stop, disabling Guidance, hiding the page, errors, and video completion still clear owned output; vibration policy is unchanged. Speech acceptance separately checks start, full completion, and cancellation; counting starts alone does not prove complete announcements. Camera default speech and actual navigation are unaffected; video is accepted independently and needs no navigation input.

Video plays at native 1× and uses `requestVideoFrameCallback` to observe new media PTS; identical PTS values are not captured again. The 30 FPS input cap remains, and the 12-frame budget includes frames currently being encoded; the first frame is exclusive. Size 640, confidence 0.25, JPEG quality 0.8, native FP32, 55-second session/first-frame budgets, and 10-second subsequent-frame budget remain. Low-frame-rate video does not create 30 new frames by repeating the same image. Source frames that are not admitted because of the cap, first-frame wait, or full in-flight budget are counted separately as uncollected observations, not as inference requests captured and dropped. Video continues at 1× during cold loading, and source-PTS jumps after the first frame are preserved.

When the image is actually drawn to the capture canvas, `performance.now()` is recorded as PRF2 `capturedAt`, which the original algorithm uses for distance history, relative speed, and TTC. Media PTS, `presentedFrames`, and capture intervals are recorded only as observations. Media PTS is not substituted for algorithm time, and algorithm speed-estimation rules do not change. During natural 1× playback, capture intervals approximately represent elapsed source motion, but decoding stalls, browser scheduling, or cold loading can make them diverge. Reports preserve media-time intervals, capture-time intervals, and their drift separately; this replay path does not validate ground-truth speed, metric distance, or TTC accuracy.

At file end, capture stops first; the session ends after already-admitted frames drain. When the server's 55-second session ends, unsubmitted encoding work is not sent; only already-validated results are displayed. User stop, page hide, seek, or playback-rate change immediately cancels the current session and owned output. The page does not loop, resume, or restart automatically. A new run requires an explicit user start and a new tracker/history session. After disconnect, already-submitted GPU work is still awaited and its slot released; the UI stop time does not prove GPU cancellation. A file longer than 55 seconds cannot bypass the per-session limit.

Video acceptance separately records source-video FPS/unique PTS count, admitted captures, returned/decoded deliveries and FPS, uncollected source frames, capture-to-decode latency, and every slow frame. Risk output additionally records source-frame capture to actual speech-engine start or vibration API acceptance, cancellations, and expiry. The approved target remains ≥10 FPS and capture-to-alert under 500 ms; media frame rate and decode FPS cannot substitute for this alert metric. Speech-engine events still do not prove audible sound, and Vibration API acceptance does not prove the motor was tested. Continuous video and static input are accepted within their own scopes; do not mix sampling methods or result definitions.

Street-scene replay assets are the official MOT17 dataset's MOT17-07 daytime moving pedestrian scene and MOT17-10 nighttime moving street scene.
The two optional files are `backend/model_tests/datasets/street-video/mot17/MOT17-07-FRCNN-browser-h264.mp4` and `backend/model_tests/datasets/street-video/mot17/MOT17-10-FRCNN-browser-h264.mp4`.
They are explicitly transcoded browser-compatible copies; official original previews are retained, frame count and 30 FPS are unchanged, and encoded pixels differ. See [video dataset notes](model_tests/DATASETS.md#continuous-street-video-for-local-integration) for dataset choice, split, annotations, hashes, and conversion boundaries. The two original previews, two H.264 copies, and source manifest are versioned; full datasets, annotations, and per-frame run reports are managed separately.

After starting both processes, run the independent Chrome tool below from `backend`. It uses native local-file selection and Blob decoding, with separate explicit sessions for manual stop and natural EOF. It does not start a server, download, transcode, or use a fake camera. Choose a new output path. The tool retains every frame and failure; if a native local voice is unavailable, it records that speech was not verified and does not substitute another voice.

```powershell
$env:PRS_PLAYWRIGHT_PATH = '<absolute path to installed Playwright package>'
node ../frontend/tools/check_local_video_browser.mjs http://127.0.0.1:8766/integration/video/ .venv/tmp/local-mvp-video-next.json model_tests/datasets/street-video/mot17/MOT17-07-FRCNN-browser-h264.mp4
```

See the next section for acceptance methods. Video frame rate, decode throughput, and capture-to-alert latency are measured separately.

## Acceptance Method and Boundaries

Run development checks from `backend`. Use the Web environment for Django/protocol tests and the GPU environment for real dual-model inference:

```powershell
# Read the private local key without printing it.
$env:DJANGO_SECRET_KEY = (Get-Content .env.local-mvp | Where-Object { $_.StartsWith('DJANGO_SECRET_KEY=') }).Substring(18)
$env:DJANGO_SETTINGS_MODULE = 'config.settings'
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py test
.\.venv\Scripts\ruff.exe check .
node --test ../frontend/tests/*.test.mjs

# Static functional check; the output directory must not exist.
.\.venv-inference\Scripts\python.exe -X utf8 -m model_tests.run_depth --track --repeat 6 --output model_tests/outputs/NEW_STATIC_RUN
```

For continuous-video acceptance, use the Chrome tool in the previous section, choose a browser-decodable file, and specify a new JSON report path for each run. The tool checks user stop and natural EOF separately. The file used for a full EOF check should be shorter than 55 seconds; do not treat the server session deadline as file playback completion. The page does not request camera permission or convert input assets automatically.

| Item | Target or check |
| --- | --- |
| Processing throughput | At least 10 FPS; report source rate, captured frames, and decoded deliveries separately. |
| Risk-alert latency | Under 500 ms from original source-frame capture to risk-output start; record speech-engine start and vibration API acceptance separately. |
| Lifecycle | Clear owned requests, tracker session, and in-flight work after manual stop, EOF, page hide, and session end. |
| Local boundary | Detection, depth, tracking, and rule-based risk run locally; inspect external calls in browser and server. |
| Accuracy | Use matching ground truth and a fixed evaluation protocol; replays without ground truth validate only functionality and output association. |

These targets are not claims about observed performance. Reports must retain the first frame, every later frame, P95/maximum latency, uncollected source frames, cancellations, and failures. Do not filter slow frames or mix cold start into warm throughput. Record source PTS span and actual capture span separately; capture-to-decode, alert submission, native output start, and actual sound/motor output are distinct measurement boundaries.

Risk alerts must be associated with the original session, frame index, and `capturedAt`; newer frames must not refresh an older alert's timer. Repeated static images, synthetic transport, and output substitutes cannot replace real motion, risk-accuracy evidence, or physical-device verification. MOT17 replay samples have no metric distance, speed, TTC, or risk ground truth. Real phones, speakers, motors, and mobile networks require separate acceptance. Structured run reports stay local and are not repository-provided test inputs or performance guarantees.

## Cloud Migration Boundary

When moving to cloud, preserve the browser same-origin → Web/integration → private inference-service protocol, metadata, and error semantics while replacing deployment addresses and runtime configuration. Package inference/model dependencies separately from Web dependencies. Restrict inference workers to private/loopback connectivity; define private identity and TLS boundaries before migration. Do not expose the GPU port publicly just by changing its address. Recreate models/Graphs or compile caches on the target GPU; do not present notebook-GPU binaries as cloud-compatible artifacts.

The existing `deploy_phone_live` full profile covers the original frontend, software, and algorithms; `deploy_space` publishes HF source. Neither is a standalone deployer for the local integration layer. Cloud migration needs its own release configuration to avoid overwriting software and algorithm deployment layers. Web-process resource limits cannot be copied directly to the GPU inference service. Set CPU memory, VRAM, and concurrency budgets based on validation on the target GPU. Docker is not required for local Windows development; cloud components may be containerized separately.
