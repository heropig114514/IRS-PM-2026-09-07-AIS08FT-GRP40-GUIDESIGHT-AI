# Phone Navigation and Local Inference MVP

The phone uses the existing server's trusted HTTPS endpoint to access the public guest version of the unified MVP; the phone and laptop do not need to share a local network. The only public page entries are [Phone Navigation and Detection](https://47.239.50.129/mvp/maps/) and [Phone MVP Tests](https://47.239.50.129/mvp/maps/test). Neither page requires a username or password. The application server permanently hosts both pages and their map APIs. Detection, depth, tracking, and risk inference run only on the laptop GPU while its inference worker and SSH tunnel are active. The existing `/prs/` service, navigation core, and algorithms remain independent.

## Connection and Responsibilities

```text
iPhone Safari: https://47.239.50.129/mvp/maps/
  └─ Existing HTTPS Nginx: internal /mvp/ relay mount with a fixed path allowlist
       └─ App server 127.0.0.1:18766: prs-mvp-web.service
            ├─ Official and diagnostic pages, maps API, navigation and speech scheduling
            ├─ Guest voice/text assistant over the exact /ws/agent/ path
            └─ App server 127.0.0.1:18767: inference-only reverse SSH tunnel
                 └─ Laptop 127.0.0.1:8767: isolated CUDA inference process
```

The permanent Web service and the inference tunnel use separate loopback ports. Port 18766 remains available on the application server when the laptop is off; only detection requires the laptop's tunnel on port 18767. Neither laptop Web port nor GPU port is publicly exposed. `MobileApplication` checks the exact Host, Origin, proxy marker, and allowed path at the outer boundary, then maps admitted requests to the original local contract. This is a public guest route, not user identity authentication. The page, assets, and four exact browser WebSockets retain the `/mvp/` prefix: control, assistant, local detection, and the diagnostic echo endpoint. Echo retains its existing connection, frame-size, idle-time, and same-origin checks; it serves the diagnostic page's ping/pong and byte-echo checks. Assistant tab ownership is verified through the control socket; this does not add account authentication. Public MCP, admin, and general API routes remain closed. Assistant requests use the provider settings isolated to `prs-mvp-web` and remain subject to the documented per-task limits. No second navigation controller or location watcher is added.

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

After `prs-mvp-web.service` is active, run the following on the laptop to start inference and connect it to the app server:

```powershell
.\.venv\Scripts\python.exe -B -m tools.run_phone_mvp
```

The launcher prompts for the app-server SSH password in the terminal and verifies the pinned server fingerprint. The password is not placed in command arguments, saved, or written to the run report. It checks that `prs-mvp-web.service` is already active, starts only `integration.inference.asgi` on the laptop, and establishes the loopback-only reverse tunnel. It reports model state from health without loading the model; native initialization begins only on the first real input. Logs and run details stay under the locally ignored `.venv/tmp/phone-mvp/` directory; `--log-dir` may select another directory under `backend`.

## iPhone Operation

1. Open the HTTPS entry above in foreground Safari; no website login is required. The certificate must be trusted by iPhone; opening the page in a desktop test that ignores certificate errors does not establish that phone permissions work.
2. The unified page enables speech output, navigation announcements, and risk alerts by default. Vibration is also enabled when the browser supports its API. iPhone Safari does not support that API and does not switch to another vibration method. Announcements follow the interface language and automatically select a matching local voice, preferring exact language tags and the system's default local voice. If no matching voice exists, the page reports that condition without using another language or remote voice. Select “Start speech” and wait for the speech status to indicate that the native engine has started. Product speech rate is fixed at 2× and app volume at 100%; adjust actual volume with the phone buttons.
3. Enable iPhone Location Services, Safari website location, and Precise Location. Get the device origin, select a destination on the map, plan, and start navigation; the original navigation module handles the route and continuous location updates.
4. Select “Environmental awareness” at the top or “Start detection” on the card and grant Safari camera permission. The product page requests the phone's rear camera during that same click; no later device selection is needed. The raw camera image and server-returned annotated image are both shown. Detection boxes use existing algorithm output; the returned image may have no boxes when there are no detections. Camera startup reports missing, denied or unavailable devices without switching to the front camera or retrying. Directions are relative to the camera image, not geographic orientation. Video replay and manual camera selection are on the dedicated test page; replay does not represent the road where the phone is located.
5. Keep Safari in the foreground and the screen unlocked. Switching to the background, leaving the page, or selecting “Stop navigation and detection” clears input and owned output; start manually after returning to the foreground. For speech errors, check permissions, voice, and output state, explicitly clear the error, then resume manually.

Enabled defaults express an output preference only; they do not mean camera/location permission was granted, detection/navigation started, or the phone produced sound. Page load does not request permissions, plan routes, or submit speech automatically.

Safari's first speech request must start during a real user action. Once a matching local voice is available, “Start speech” synchronously submits a startup prompt in the corresponding language. The first real click on camera detection, start navigation, or the test-page video-detection button also performs this speech calibration in the same click; there is no need to play text in the manual speech test area first. `SpeechActivation` blocks automatic navigation and risk speech until the native engine emits `start` for that request; request acceptance alone does not count as started. If status does not reach started or an error occurs, follow the prompt, check voice/device settings, and explicitly select “Start speech” again. No retry is automatic. Native engine start means only that the browser reported playback start; confirm actual audibility on the phone.

## Test and Diagnostic Interface

The diagnostic page is a separate public page at [the unified test URL](https://47.239.50.129/mvp/maps/test), without a trailing slash; the product page intentionally does not display a link to it. Manual speech, voice selection, rate/volume controls, manual camera selection, video replay, frame statistics, and output-lifecycle diagnostics are available only there. Both pages expose the voice assistant and permit a same-origin microphone request only after its record control is activated. The product page uses press-and-hold recording; the diagnostic page uses click-to-toggle recording. On first use, grant the browser prompt; if the initial hold was released while granting permission, hold again to record. Page load never requests microphone access. The product navigation area also offers “Fill NUS example”; it fills fixed origin/destination values only, and users must still plan the route. The example does not represent device location. The diagnostic page still requires manual local-voice selection. The two pages have separate browser sessions; stop input before switching and do not start detection on both pages simultaneously. Both interfaces reuse the original algorithms, navigation, and output drivers; the diagnostic page does not introduce a second risk policy.

After starting the services, use the existing Playwright tools from the repository root to verify product interaction and diagnostic isolation:

```powershell
$env:PRS_PLAYWRIGHT_PATH = '<absolute path to installed Playwright package>'
node frontend/tools/check_product_browser.mjs https://47.239.50.129/mvp/maps/ backend/.venv/tmp/NEW_PRODUCT.json
node frontend/tools/check_product_browser.mjs https://47.239.50.129/mvp/maps/ backend/.venv/tmp/NEW_BOUNDARIES.json --boundaries
node frontend/tools/check_product_browser.mjs https://47.239.50.129/mvp/maps/ backend/.venv/tmp/NEW_CAMERA_BOUNDARIES.json --camera-boundaries
node frontend/tools/check_phone_relay_browser.mjs https://47.239.50.129/mvp/maps/test backend/.venv/tmp/NEW_DIAGNOSTIC.json
```

Output files must not already exist. The product tool checks phone/desktop layouts, default output intent, automatic matching of local Chinese/English voices, start/cleanup from a real click, and visibility of the raw and decoded returned images. It records the exact rear-camera request issued by the product. Because Chrome's native fake camera has no physical camera orientation, the tool explicitly simulates support for the constraint and removes it only in the fixture before opening the fake camera. Synthetic routes/GPS are also explicitly labeled; inference results and speech events come from real runtime modules, but do not prove that a phone selected its actual rear camera. The diagnostic tool separately calls real ORS. Neither tool starts services, bypasses TLS, or requests a physical camera; neither represents actual iPhone location or listening verification.

To check target annotations returned by the real algorithm, explicitly set `PRS_FAKE_CAMERA_VIDEO` to the absolute path of an existing `.y4m` test file, then run the product tool. Chrome still uses its native fake camera, and the file should contain a recognizable target. The tool requires both the raw preview and decoded returned image to be visible, and at least one real model result to contain a target; it also saves a screenshot of the perception area. It does not download or convert assets. File content does not represent the phone's surroundings and is not used to assess real-world box accuracy.

The product tool's `--boundaries` mode does not start inference. It temporarily hides the native voice list and explicitly dispatches a list-change event, confirming that a local voice appearing later is matched automatically but not played. It also checks that submitting empty coordinates expands/focuses the error field, a cached page retains its language observer, and hiding clears state without automatically restarting when foregrounded. Voice-list events, page cache, and visibility changes use explicitly labeled browser fixtures; they do not represent iPhone OS background testing. Speech-start events still come from the native engine.

`--camera-boundaries` mode uses only explicit permission-Promise, late-permission, and error fixtures. It checks that a double-click makes only one camera request, stopping is possible while permission is pending, late streams are cleaned up, and permission denial/missing rear camera/unsupported orientation constraints produce explicit messages and allow manual recovery. It does not open a camera, connect to inference, or retry, and cannot replace real iPhone permission/camera verification.

Risk alerts take priority over navigation announcements. After an alert ends, only the latest still-valid navigation instruction resumes. Stopping perception does not stop navigation; stopping navigation does not end perception. Results past their original expiry are displayed only and do not create new risk alerts; silence does not mean no risk. The page does not automatically plan obstacle-avoidance routes from visual targets.

Safari camera access requires an HTTPS secure context; see [WebKit media-access requirements](https://webkit.org/blog/7763/a-closer-look-into-webrtc/). For a manually installed private certificate, enable full trust as described in [Apple's certificate-trust guidance](https://support.apple.com/en-gb/102390). See [Apple's location-permission guide](https://support.apple.com/en-ca/guide/iphone/iph3dd5f9be/ios) for Location Services and Precise Location.

## Shutdown and Failure Boundaries

The laptop must remain powered on, connected to the network, and running the inference launcher for detection. Press `Ctrl+C` in that terminal to stop only the GPU worker and tunnel. The app-server Web service continues serving both pages and map APIs. Without the laptop tunnel, page requests still work, while detection reports an unavailable inference connection; there is no provider fallback, reconnect or retry.

A 502 for either page means the app-server Nginx upstream at `127.0.0.1:18766` or `prs-mvp-web.service` is unavailable; the laptop GPU ports do not cause a page 502. An inference connection error while the pages load means the loopback tunnel at server port 18767 is unavailable. Check the corresponding service only. `/mvp/` does not fall back to `/prs/` or another inference provider.

The user must still verify the physical iPhone camera, precise location, audible speaker output, and navigation/detection integration over a mobile network. HTTPS page checks, synthetic location, model results, and native speech events do not replace acceptance on an actual phone.
