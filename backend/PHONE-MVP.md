# Phone Navigation and Local Inference MVP

The phone uses the existing server's trusted HTTPS endpoint to access the public guest version of the unified MVP; the phone and laptop do not need to share a local network. The only public page entries are [Phone Navigation and Detection](https://47.239.50.129/mvp/maps/) and [Phone MVP Tests](https://47.239.50.129/mvp/maps/test). Neither page requires a username or password. The application server permanently hosts both pages and their map APIs. Detection, depth, tracking, and risk inference run only on the laptop GPU while its inference worker and SSH tunnel are active. The existing `/prs/` service, navigation core, and algorithms remain independent.

## Connection and Responsibilities

```text
iPhone Safari: https://47.239.50.129/mvp/maps/
  └─ Existing HTTPS Nginx: internal /mvp/ relay mount with a fixed path allowlist
       └─ App server 127.0.0.1:18766: prs-mvp-web.service
            ├─ Official and diagnostic pages, maps API, navigation and speech scheduling
            ├─ Guest voice/text assistant over /ws/agent/; bilingual TTS streams over /ws/tts/
            └─ App server 127.0.0.1:18767: inference-only reverse SSH tunnel
                 └─ Laptop 127.0.0.1:8767: isolated CUDA inference process
```

The permanent Web service and the inference tunnel use separate loopback ports. Port 18766 remains available on the application server when the laptop is off; only detection requires the laptop's tunnel on port 18767. Neither laptop Web port nor GPU port is publicly exposed. `MobileApplication` checks the exact Host, Origin, proxy marker, and allowed path at the outer boundary, then maps admitted requests to the original local contract. This is a public guest route, not user identity authentication. The page, assets, and five exact browser WebSockets retain the `/mvp/` prefix: control, assistant, local detection, diagnostic echo, and backend TTS. Echo retains its existing connection, frame-size, idle-time, and same-origin checks; it serves the diagnostic page's ping/pong and byte-echo checks. TTS uses the configured server-side Qwen realtime provider, fixes the Cherry bilingual voice, and forwards PCM audio; provider credentials remain on the app server. Assistant tab ownership is verified through the control socket; this does not add account authentication. Public MCP, admin, and general API routes remain closed. Assistant and TTS requests use provider settings isolated to `prs-mvp-web` and remain subject to the documented limits. No second navigation controller or location watcher is added.

The Nginx relay forwards public page assets unchanged to the app-server Web service. Map requests omit browser credentials and use the existing same-origin route; no login cookie, browser password or access token is injected. The relay path and HTTP/WebSocket allowlists remain fixed. The inference-only SSH listener is also loopback-only and carries no browser traffic.

The browser sends only JPEG frames used for detection; when a local video is selected, the original video remains in the browser. Media is processed in memory and is not recorded or saved. Mobile networks, the server relay, and local inference all affect result age; the original expiry, capture time, model parameters, and risk rules do not change for phone deployment.

## Application Server Web Service

The app-server service is defined by [`deploy/prs-mvp-web.service`](deploy/prs-mvp-web.service), runs as the dedicated `prs-mvp-web` user, and serves only the fixed MVP ASGI allowlist on `127.0.0.1:18766`. Its environment file is `/etc/prs-mvp-web/app.env`; it contains a separate Django key, the existing ORS key for maps, `LOCAL_WEB_ORIGIN=http://127.0.0.1:18766`, and `LOCAL_INFERENCE_URL=ws://127.0.0.1:18767/ws/infer/`. When the guest assistant is enabled, the same file holds only the allowlisted `PRS_AGENT_*` and `PRS_ASR_MODEL` provider settings. These credentials remain scoped to the MVP service and are never copied to the laptop or committed. Store the SQLite state in `/var/lib/prs-mvp-web/db.sqlite3`.

Deploy each source revision to a versioned directory under `/opt/prs-practice/mvp/releases/`, update only `/opt/prs-practice/mvp/current`, and install the unit file to `/etc/systemd/system/prs-mvp-web.service`. Then run `systemctl daemon-reload` and `systemctl enable --now prs-mvp-web`. Check `systemctl status prs-mvp-web` and request both public page URLs. Bare `/mvp` and `/mvp/` requests return 404; the mount is used only by the two pages and their required internal paths. This deployment does not switch `/opt/prs-practice/current`, restart `prs-camera`, or change the Nginx virtual host. The existing `/mvp/` Nginx include continues to proxy only to 18766.

## Laptop Inference Setup and Startup

The laptop needs Python 3.12, a supported NVIDIA driver, the pinned CUDA environment, and the exact configured model weights. This repository's current default distance mode uses the official YOLO26n depth checkpoint, so both detection and depth weights are required. From `backend`, provision a fresh inference environment as follows; the CUDA 12.6 wheels are compatible with the inspected CUDA 12.7 laptop driver:

```powershell
python -m venv .venv-inference
.\.venv-inference\Scripts\python.exe -m pip install torch==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cu126
.\.venv-inference\Scripts\python.exe -m pip install -r requirements-inference.txt --retries 0
Invoke-WebRequest https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26n-depth.pt -OutFile models/yolo26n-depth.pt
Get-FileHash -Algorithm SHA256 models/yolo26n.pt, models/yolo26n-depth.pt
```

The depth checkpoint must have SHA-256 `befed1b8561d8b2eaa66274b070cdfc9c44853bda6d6d62a04759f9383af74e9`; `yolo26n.pt` must match `models/yolo26n.json`. These files are not downloaded implicitly by the launcher. Install relay-client dependencies in `.venv`, then run from `backend`:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-phone.txt --retries 0
.\.venv\Scripts\python.exe -B -m tools.run_phone_mvp --check
```

`requirements-phone.txt` reuses the Web/deployment dependencies. `--check` verifies both laptop environments, direct dependency pins, CUDA, model hashes, and the local inference port. It does not connect over SSH, write server configuration, start services, load a model, or submit frames. It does not verify iPhone permissions, camera behavior, actual phone speech output, or detection quality.

The permanent Web port is 18766 on the application server. The laptop worker defaults to `127.0.0.1:8767`; the pinned SSH connection maps that worker to server loopback port 18767. An occupied local port fails explicitly. The launcher does not kill processes, start a Web service, edit Nginx, or select another port.

An existing verified GPU interpreter can be selected explicitly with `--inference-python`.
The default remains `.venv-inference`; an invalid explicit path fails without substitution.
This option preserves package/CUDA/checkpoint/port checks and interactive SSH authentication.
For the prepared local TensorRT environment, use the following from `backend` after stopping
the owned offline demo launcher to release port 8767:

```powershell
$env:PRS_DETECTION_MODEL = 'yolo26n'
$env:PRS_DISTANCE_MODE = 'depth'
$env:PRS_DEPTH_BACKEND = 'tensorrt'
$env:PRS_DEPTH_ENGINE_MANIFEST = 'models/experiments/depth-serving-fp32/manifest.json'
$env:PRS_DEPTH_FUSION = 'host'
.\.venv\Scripts\python.exe -B -m tools.run_phone_mvp --inference-python .venv/depth-export/Scripts/python.exe --check
.\.venv\Scripts\python.exe -B -m tools.run_phone_mvp --inference-python .venv/depth-export/Scripts/python.exe
```

The engine bundle must match this worker's weights/runtime/GPU and cover the phone's actual
preprocessed input shapes. Unsupported shapes fail explicitly. This configuration does not
change the camera's admission policy or establish phone/network latency from local-video results.

### Temporary GPU replacement

When another operator already owns the server's port 18767, keep that tunnel running. Validate
the selected replacement worker on its local port, allocate a separate server loopback binding
(18768 for the temporary RTX 3060 setup), and verify health through that binding before changing
only `LOCAL_INFERENCE_URL` in `/etc/prs-mvp-web/app.env`. Preserve a private backup of the
original route and all other environment values. Restart only `prs-mvp-web`, then wait for its
18766 listener and routed worker health; a systemd `active` state alone is insufficient.
An inference-source archive can be stored separately under
`/opt/prs-practice/mvp/inference-releases/`; uploading it does not activate GPU execution.

Acceptance must check the public WSS contract and real returned frames for the selected GPU,
model, precision, depth backend and fusion mode. Use one fixed image geometry per session and
the browser's bounded streaming admission. A sequential request/response test can exhaust the
worker's five-second input-idle budget when WAN round-trip time exceeds that budget. Preserve
such failures as network/test evidence; do not extend deadlines or claim a model speedup from
changing the input schedule. Actual phone camera and mobile-network acceptance remain separate.

The replacement's supervisor owns only its GPU child and independent SSH binding. Keep the
laptop powered, awake and connected while the phone uses it. To restore the original target,
first check that the previous worker is available, restore the original inference URL, restart
the named Web service and verify the routed health, then stop the replacement's owned resources.
Do not terminate the colleague's tunnel. A lost replacement tunnel does not silently switch
providers; recover or restore explicitly. Runtime process identities, source hashes, selected
proxy paths and private-backup locations belong in the structured deployment report.

After `prs-mvp-web.service` is active, run the following on the laptop to start inference and connect it to the app server:

```powershell
.\.venv\Scripts\python.exe -B -m tools.run_phone_mvp
```

The launcher prompts for the app-server SSH password in the terminal and verifies the pinned server fingerprint. The password is not placed in command arguments, saved, or written to the run report. It checks that `prs-mvp-web.service` is already active, starts only `integration.inference.asgi` on the laptop, and establishes the loopback-only reverse tunnel. It reports model state from health without loading the model; native initialization begins only on the first real input. Logs and run details stay under the locally ignored `.venv/tmp/phone-mvp/` directory; `--log-dir` may select another directory under `backend`.

## iPhone Operation

The existing public HTTPS entry is the shared teammate entry; the optional LAN certificate
workflow is not required for it. The browser needs only its normal camera permission. The
temporary setup still sends frames through the public Web server to the laptop GPU, so the
laptop must remain awake and the selected tunnel must stay connected. A trusted HTTPS page
does not establish acceptable inference latency.

### Camera admission and relay configuration

Set `PRS_CAMERA_PROFILE` on the Web service, then restart that service and reload the page
before starting a new session. `integration.assist` validates the exact name and renders it on
both product and diagnostic pages. `CameraApp` freezes the policy before requesting permission;
changing the page setting cannot change an active session. An unknown name fails explicitly.

| Profile | Capture ceiling | Outstanding frames |
| --- | ---: | ---: |
| `standard` (unset default/control) | 30 FPS | 12 |
| `bounded-2` (measured low-queue candidate) | 30 FPS | 2 |
| `bounded-3` | 30 FPS | 3 |
| `responsive-2` / `responsive-3` | 10 FPS | 2 / 3 |
| `conservative-2` / `conservative-3` | 5 FPS | 2 / 3 |

The profile applies only to local inference. Other processing modes keep their original
policy. Capture remains max640/JPEG0.8, with the first frame alone, ordered results, original
timestamps and no discarded accepted frames. The protocol's twelve-frame ceiling is unchanged;
the selected browser window may be smaller. Capture FPS is a ceiling, not delivered throughput.
The five-second worker input-idle budget and ten-second steady-frame deadline are unchanged.
If the first result takes longer than the idle budget to return, the worker can end before the
next input arrives; a smaller queue does not solve that transport delay.

`integration.relay.RelayForward` drains a writable partial SSH send immediately after positive
progress. It retains bounded polling under backpressure, bounded buffers, full-duplex forwarding
and half-close handling. Start a new owned relay to load changed Python code; editing the source
does not change an already-running relay. Use a separate loopback binding for comparison, verify
its routed worker identity, and change only the Web inference URL for activation. Never stop
another operator's tunnel or the supervisor that still owns the selected GPU.

For rollback, verify the current route and source hashes against the activation report. Restore
the two changed source files from their recorded backups, and restore only `LOCAL_INFERENCE_URL`
and `PRS_CAMERA_PROFILE` to their prior values while preserving unrelated environment changes.
Restart only `prs-mvp-web`, wait for its listener and routed health, then stop the candidate relay.
If the original temporary GPU supervisor must also be removed, first restore its expected route;
only then use its owned restore-and-stop control to return to the colleague's worker.

A temporary release overlay is not durable across an independent deployment. Guard both the
resolved release directory and source hashes, preserve new features when reapplying a minimal
overlay, and verify the actual rendered profile after every release switch. Carry the camera
and template changes into the authoritative release source before relying on automated deploys.
The profile environment variable alone has no effect on an older template/camera implementation.

The browser benchmark below uses the real capture/encode/WS/inference/hash/decode pipeline with
an explicitly selected public fixture. Use an unused output path. `-` selects normal public
Chrome certificate validation and the explicitly configured development proxy at127.0.0.1:7897;
it does not install a trust anchor or ignore certificate errors. The LAN mode uses the dedicated
CA directory and an exact leaf pin confined to the disposable browser. Both modes retain errors
and first-frame timing separately. Desktop fixture tests do not establish phone radio, camera,
display latency, risk accuracy or physical speech output.

```powershell
$env:PRS_PLAYWRIGHT_PATH = '<absolute path to installed Playwright package>'
node frontend/tools/benchmark_lan_camera.mjs https://47.239.50.129/mvp/maps/test 24 bounded-2 backend/model_tests/samples/bus.jpg - backend/.venv/tmp/NEW_PUBLIC_CAMERA.json
```

Measured trials and failures live under `backend/benchmarks/2026-10-28/lan-camera/` and
`backend/benchmarks/2026-10-28/phone-latency/`. Compare identical fixture/count/quality conditions
before attributing changes to the queue or relay. LAN measurements exclude the public relay;
server-origin WSS measurements exclude the phone-to-server leg.

### Optional direct LAN diagnostic

`tools.run_lan_mvp` reuses a ready continuous GPU process and owns only two local Web children.
It requires an explicit RFC1918 IPv4 address and a /24 or narrower subnet, exact Host/Origin,
direct HTTPS and the existing mobile API allowlist. It rejects forwarding headers and unrelated
paths. Install `requirements-phone.txt` in the Web environment; its pinned cryptography dependency
generates a dedicated seven-day CA and exact-IP server certificate. The CA signing key is never
serialized, and no operating-system trust or firewall configuration changes automatically.

From `backend`, substitute the actual interface and new runtime paths:

```powershell
.\.venv\Scripts\python.exe -B -m tools.run_lan_mvp --address 192.168.10.8 --subnet 192.168.10.0/24 --state-dir .venv/tmp/lan-demo/NEW_RUN --prepare-certificate
.\.venv\Scripts\python.exe -B -m tools.run_lan_mvp --address 192.168.10.8 --subnet 192.168.10.0/24 --state-dir .venv/tmp/lan-demo/NEW_RUN --camera-profile bounded-2 --check
.\.venv\Scripts\python.exe -B -m tools.run_lan_mvp --address 192.168.10.8 --subnet 192.168.10.0/24 --state-dir .venv/tmp/lan-demo/NEW_RUN --camera-profile bounded-2
```

The default TLS directory must be new for preparation; select a new `--tls-dir` explicitly when
renewing. The canonical Web origin defaults to port18766 and must match the existing worker.
The setup listener at8080 serves only instructions and public certificate/profile bytes; it
does not serve the leaf private key or arbitrary files. A physical iPhone would need to install
and fully trust this dedicated certificate. This is optional and is not the shared public route.
Use Ctrl+C or `stop.request` in the selected state directory to stop only these Web children.
Certificates expire after seven days; do not copy the public server's TLS private keys locally.

### Phone interaction

1. Open the HTTPS entry above in foreground Safari; no website login is required. The certificate must be trusted by iPhone; opening the page in a desktop test that ignores certificate errors does not establish that phone permissions work.
2. The unified page enables speech output, navigation announcements, and risk alerts by default. Vibration is also enabled when the browser supports its API. iPhone Safari does not support that API and does not switch to another vibration method. Speech is synthesized by the app-server's configured Qwen realtime service and streamed as PCM through the same-origin TTS socket; the browser plays the stream using Web Audio and does not depend on installed local voices. Assistant replies use automatic Chinese/English detection; navigation and risk announcements follow the interface language. Select “Start speech” and wait for the status to report that streamed audio playback has started. Product speech rate is fixed at 2× and app volume at 100%; adjust actual volume with the phone buttons.
3. Enable iPhone Location Services, Safari website location, and Precise Location. Get the device origin, select a destination on the map, plan, and start navigation; the original navigation module handles the route and continuous location updates.
4. Select “Environmental awareness” at the top or “Start detection” on the card and grant Safari camera permission. The product page requests the phone's rear camera during that same click; no later device selection is needed. The raw camera image and server-returned annotated image are both shown. Detection boxes use existing algorithm output; the returned image may have no boxes when there are no detections. Camera startup reports missing, denied or unavailable devices without switching to the front camera or retrying. Directions are relative to the camera image, not geographic orientation. Video replay and manual camera selection are on the dedicated test page; replay does not represent the road where the phone is located.
5. Keep Safari in the foreground and the screen unlocked. Switching to the background, leaving the page, or selecting “Stop navigation and detection” clears input and owned output; start manually after returning to the foreground. For speech errors, check permissions, voice, and output state, explicitly clear the error, then resume manually.

Enabled defaults express an output preference only; they do not mean camera/location permission was granted, detection/navigation started, or the phone produced sound. Page load does not request permissions, plan routes, or submit speech automatically.

Safari's first speech request must start during a real user action so Web Audio can be unlocked. The first real click on camera detection, start navigation, or the test-page video-detection button also submits a short backend prompt in the selected interface language; there is no need to play text in the manual speech test area first. `SpeechActivation` blocks automatic navigation and risk speech until the browser schedules the first backend PCM chunk for playback; request acceptance alone does not count as started. If status does not reach started or an error occurs, check the network and configured TTS service, then explicitly use the speech control again. No retry is automatic. A scheduled/ended browser stream does not prove that the phone speaker was audible; confirm actual audibility on the phone.

## Test and Diagnostic Interface

The diagnostic page is a separate public page at [the unified test URL](https://47.239.50.129/mvp/maps/test), without a trailing slash; the product page intentionally does not display a link to it. Manual speech, fixed backend voice metadata, rate/volume controls, manual camera selection, video replay, frame statistics, and output-lifecycle diagnostics are available only there. Both pages expose the voice assistant and permit a same-origin microphone request only after its record control is activated. The product page uses press-and-hold recording; the diagnostic page uses click-to-toggle recording. On first use, grant the browser prompt; if the initial hold was released while granting permission, hold again to record. Page load never requests microphone access. The product navigation area also offers “Fill NUS example”; it fills fixed origin/destination values only, and users must still plan the route. The example does not represent device location. Both pages use backend TTS; the diagnostic page can submit English and Chinese explicitly or let the service detect either language. The two pages have separate browser sessions; stop input before switching and do not start detection on both pages simultaneously. Both interfaces reuse the original algorithms, navigation, and output drivers; the diagnostic page does not introduce a second risk policy.

After starting the services, use the existing Playwright tools from the repository root to verify product interaction and diagnostic isolation:

```powershell
$env:PRS_PLAYWRIGHT_PATH = '<absolute path to installed Playwright package>'
node frontend/tools/check_product_browser.mjs https://47.239.50.129/mvp/maps/ backend/.venv/tmp/NEW_PRODUCT.json
node frontend/tools/check_product_browser.mjs https://47.239.50.129/mvp/maps/ backend/.venv/tmp/NEW_BOUNDARIES.json --boundaries
node frontend/tools/check_product_browser.mjs https://47.239.50.129/mvp/maps/ backend/.venv/tmp/NEW_CAMERA_BOUNDARIES.json --camera-boundaries
node frontend/tools/check_phone_relay_browser.mjs https://47.239.50.129/mvp/maps/test backend/.venv/tmp/NEW_DIAGNOSTIC.json
```

Output files must not already exist. The product tool checks phone/desktop layouts, fixed backend voice metadata, real-click PCM activation on the product page, and visibility of the raw and decoded returned images. Its diagnostic-page phase performs real English and Chinese synthesis with both explicit language tags and automatic detection, then requires actual PCM start and stream completion. Reports include only request metadata and lifecycle evidence, not text. It records the exact rear-camera request issued by the product. Because Chrome's native fake camera has no physical camera orientation, the tool explicitly simulates support for the constraint and removes it only in the fixture before opening the fake camera. Synthetic routes/GPS are also explicitly labeled; inference results and speech events come from real runtime modules, but do not prove that a phone selected its actual rear camera. The diagnostic tool separately calls real ORS. Neither tool starts services, bypasses TLS, or requests a physical camera; neither represents actual iPhone location or listening verification.

To check target annotations returned by the real algorithm, explicitly set `PRS_FAKE_CAMERA_VIDEO` to the absolute path of an existing `.y4m` test file, then run the product tool. Chrome still uses its native fake camera, and the file should contain a recognizable target. The tool requires both the raw preview and decoded returned image to be visible, and at least one real model result to contain a target; it also saves a screenshot of the perception area. It does not download or convert assets. File content does not represent the phone's surroundings and is not used to assess real-world box accuracy.

The product tool's `--boundaries` mode does not start inference. It checks the fixed backend voice metadata, empty-coordinate feedback, language observer behavior after page cache, product-page PCM activation, diagnostic-page explicit Chinese/English and auto-detected Chinese/English playback, and hide cleanup without automatic foreground restart. Page cache and visibility changes use explicitly labeled browser fixtures; they do not represent iPhone OS background testing. These checks do not prove physical audibility.

`--camera-boundaries` mode uses only explicit permission-Promise, late-permission, and error fixtures. It checks that a double-click makes only one camera request, stopping is possible while permission is pending, late streams are cleaned up, and permission denial/missing rear camera/unsupported orientation constraints produce explicit messages and allow manual recovery. It does not open a camera, connect to inference, or retry, and cannot replace real iPhone permission/camera verification.

Risk alerts take priority over navigation announcements. After an alert ends, only the latest still-valid navigation instruction resumes. Stopping perception does not stop navigation; stopping navigation does not end perception. Results past their original expiry are displayed only and do not create new risk alerts; silence does not mean no risk. The page does not automatically plan obstacle-avoidance routes from visual targets.

Safari camera access requires an HTTPS secure context; see [WebKit media-access requirements](https://webkit.org/blog/7763/a-closer-look-into-webrtc/). For a manually installed private certificate, enable full trust as described in [Apple's certificate-trust guidance](https://support.apple.com/en-gb/102390). See [Apple's location-permission guide](https://support.apple.com/en-ca/guide/iphone/iph3dd5f9be/ios) for Location Services and Precise Location.

## Shutdown and Failure Boundaries

The laptop must remain powered on, connected to the network, and running the inference launcher for detection. Press `Ctrl+C` in that terminal to stop only the GPU worker and tunnel. The app-server Web service continues serving both pages and map APIs. Without the laptop tunnel, page requests still work, while detection reports an unavailable inference connection; there is no provider fallback, reconnect or retry.

A 502 for either page means the app-server Nginx upstream at `127.0.0.1:18766` or `prs-mvp-web.service` is unavailable; the laptop GPU ports do not cause a page 502. An inference connection error while the pages load requires checking the loopback tunnel selected by `LOCAL_INFERENCE_URL` (18767 for the baseline, or the explicitly selected replacement). Check the corresponding service only. `/mvp/` does not fall back to `/prs/` or another inference provider.

For a browser connection timeout before any page appears, compare direct TCP80/443 reachability
with a separately labeled proxy path, then inspect Nginx/Web state, public listeners, certificate
identity/expiry and host firewall. A successful proxy request does not prove a phone can connect
directly. If both Wi-Fi and cellular fail while the service and host admission are healthy, inspect
cloud security-group/public-access rules and upstream routing using the provider console. SSH
root access does not expose those cloud control-plane rules. Do not disable TLS, broaden unrelated
ports, install a private phone certificate or alter inference deadlines to mask a TCP-path failure.

The user must still verify the physical iPhone camera, precise location, audible speaker output, and navigation/detection integration over a mobile network. HTTPS page checks, synthetic location, model results, and browser PCM lifecycle events do not replace acceptance on an actual phone.
