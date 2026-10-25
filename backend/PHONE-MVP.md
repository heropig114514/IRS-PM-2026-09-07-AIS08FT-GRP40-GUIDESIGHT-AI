# Phone Navigation and Local Inference MVP

The phone uses the existing server's trusted HTTPS endpoint to access the local unified MVP; the phone and computer do not need to share a local network. Entry: [Phone Navigation and Detection](https://47.239.50.129/mvp/maps/). The server provides HTTPS, access authentication, and relay. Detection, depth, tracking, and risk inference continue to run on the local GPU. The server's original `/prs/` app/service, navigation core, and algorithms remain independent.

## Connection and Responsibilities

```text
iPhone Safari: https://47.239.50.129/mvp/maps/
  └─ Existing HTTPS Nginx: /mvp/, isolated access authentication
       └─ Server 127.0.0.1:18766
            └─ Reverse SSH tunnel with pinned server identity
                 └─ Local 127.0.0.1:8766: integration.mobile_asgi
                      ├─ Original map, device GPS, navigation, unified speech scheduling
                      └─ Local 127.0.0.1:8767: isolated CUDA inference process
```

The server exposes the tunnel port only on loopback; it does not expose the local Web or GPU ports directly. `MobileApplication` checks the exact Host, Origin, proxy marker, and allowed path at the outer boundary, then maps admitted requests to the original local contract. The page, assets, and both browser WebSockets retain the `/mvp/` prefix. Public MCP, admin, and other APIs are not exposed. The computer controls the same phone page's navigation controller through the original localhost MCP; no location watcher is added.

The relay provides derived responses for only two fixed JavaScript assets used by map configuration/planning requests, allowing same-origin requests to reuse Safari's completed web access authentication. Original frontend files, localhost responses, and navigation calculations remain unchanged. If the explicit source adaptation point changes, return 503; do not modify other code automatically, inject credentials, or bypass authentication.

The browser sends only JPEG frames used for detection; when a local video is selected, the original video remains in the browser. Media is processed in memory and is not recorded or saved. Mobile networks, the server relay, and local inference all affect result age; the original expiry, capture time, model parameters, and risk rules do not change for phone deployment.

## Environment Setup and Checks

First prepare `.venv`, `.venv-inference`, CUDA, and the weights required by the selected algorithm as described in [Local MVP](LOCAL-MVP.md#environment-and-model-setup). Install phone-relay dependencies in the Web environment. Run from `backend`:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-phone.txt --retries 0
.\.venv\Scripts\python.exe -B -m tools.run_phone_mvp --check
```

`requirements-phone.txt` includes existing development dependencies and a pinned password-hashing dependency. `--check` verifies both environments, direct dependencies, CUDA, models, and local ports. It does not connect over SSH, write server configuration, start services, or run model predictions. It does not verify public access, iPhone permissions, or actual phone speech output, and cannot be used together with `--install-relay`.

Default Web and inference ports are 8766 and 8767. An occupied port fails explicitly; the launcher does not kill processes or search for another port. Stop the corresponding instance in its original terminal, or explicitly select different ports with `--web-port` and `--inference-port`. The server's dedicated reverse port is fixed at 18766.

## Install the Relay and Start Daily

Run this for the initial installation or an explicit update to this project's relay configuration:

```powershell
.\.venv\Scripts\python.exe -B -m tools.run_phone_mvp --install-relay
```

This option adds an isolated `/mvp/` include and access-password hash to the existing HTTPS virtual host, preserves a backup of the original configuration, checks Nginx syntax, and then reloads it. It does not upload algorithm or software source, replace certificates, or restart the original `prs-camera` service. If syntax validation fails, the previous configuration is restored without a reload. After successful installation, the launcher continues with this run's local services and tunnel.

For daily startup after the same relay configuration and access password have been installed, run:

```powershell
.\.venv\Scripts\python.exe -B -m tools.run_phone_mvp
```

Daily startup checks only the installed template, include, and access-password hash; it does not rewrite configuration or reload Nginx. A mismatch fails explicitly. Review the difference and use the installation option explicitly if appropriate; there is no automatic repair or entry-point switch.

The launcher prompts for the SSH password in the terminal and verifies the pinned server host fingerprint. The SSH password is not placed in the command line, a file, or a run report. From `/etc/prs-camera/app.env` on the server, it reads only `ORS_API_KEY`, which is used only in memory by the local supervisor and Web child process. It does not save another configuration copy or provide the key to the inference process or browser. HF and DashScope remain disabled.

The phone website's isolated access account defaults to username `phone`. Its random password is stored in the private local file `backend/.env.phone-mvp`, using `PHONE_ACCESS_USER` and `PHONE_ACCESS_PASSWORD`. The file is created if missing on first run and reused afterward; select another file explicitly with `--access-env`. The `.env.*` ignore rule protects this file. Do not copy it into documentation, screenshots, or run logs. The server stores only the password hash, not the plaintext web password. The SSH password is not written to this file.

Wait for the launcher to display the phone URL and complete its HTTPS page check, then open the entry in Safari. Installed dependencies or an open listening port do not mean the model is loaded; initialization still occurs on the first real input. Run details are stored in the locally ignored `.venv/tmp/phone-mvp/` subdirectory; use `--log-dir` to select a directory under `backend`.

## iPhone Operation

1. Open the HTTPS entry above in foreground Safari and authenticate with the username/password in the private local access file. The certificate must be trusted by iPhone; opening the page in a desktop test that ignores certificate errors does not establish that phone permissions work.
2. The unified page enables speech output, navigation announcements, and risk alerts by default. Vibration is also enabled when the browser supports its API. iPhone Safari does not support that API and does not switch to another vibration method. Announcements follow the interface language and automatically select a matching local voice, preferring exact language tags and the system's default local voice. If no matching voice exists, the page reports that condition without using another language or remote voice. Select “Start speech” and wait for the speech status to indicate that the native engine has started. Product speech rate is fixed at 2× and app volume at 100%; adjust actual volume with the phone buttons.
3. Enable iPhone Location Services, Safari website location, and Precise Location. Get the device origin, select a destination on the map, plan, and start navigation; the original navigation module handles the route and continuous location updates.
4. Select “Environmental awareness” at the top or “Start detection” on the card and grant Safari camera permission. The product page requests the phone's rear camera during that same click; no later device selection is needed. The raw camera image and server-returned annotated image are both shown. Detection boxes use existing algorithm output; the returned image may have no boxes when there are no detections. The page does not request microphone access. If the browser cannot select a rear camera, permission is denied, or the camera is unavailable, it reports the failure without switching to the front camera or retrying. Directions are relative to the camera image, not geographic orientation. Video replay and manual camera selection are on the dedicated test page; replay does not represent the road where the phone is located.
5. Keep Safari in the foreground and the screen unlocked. Switching to the background, leaving the page, or selecting “Stop navigation and detection” clears input and owned output; start manually after returning to the foreground. For speech errors, check permissions, voice, and output state, explicitly clear the error, then resume manually.

Enabled defaults express an output preference only; they do not mean camera/location permission was granted, detection/navigation started, or the phone produced sound. Page load does not request permissions, plan routes, or submit speech automatically.

Safari's first speech request must start during a real user action. Once a matching local voice is available, “Start speech” synchronously submits a startup prompt in the corresponding language. The first real click on camera detection, start navigation, or the test-page video-detection button also performs this speech calibration in the same click; there is no need to play text in the manual speech test area first. `SpeechActivation` blocks automatic navigation and risk speech until the native engine emits `start` for that request; request acceptance alone does not count as started. If status does not reach started or an error occurs, follow the prompt, check voice/device settings, and explicitly select “Start speech” again. No retry is automatic. Native engine start means only that the browser reported playback start; confirm actual audibility on the phone.

## Test and Diagnostic Interface

The “Tests and Diagnostics” link at the bottom of the product page opens the [unified test page](https://47.239.50.129/mvp/maps/test), without a trailing slash. Manual speech, voice selection, rate/volume controls, manual camera selection, video replay, frame statistics, and output-lifecycle diagnostics are available only there. The product navigation area also offers “Fill NUS example”; it fills fixed origin/destination values only, and users must still plan the route. The example does not represent device location. The diagnostic page still requires manual local-voice selection. The two pages have separate browser sessions; stop input before switching and do not start detection on both pages simultaneously. Both interfaces reuse the original algorithms, navigation, and output drivers; the diagnostic page does not introduce a second risk policy.

After starting the services, use the existing Playwright tools from the repository root to verify product interaction and diagnostic isolation:

```powershell
$env:PRS_PLAYWRIGHT_PATH = '<absolute path to installed Playwright package>'
node frontend/tools/check_product_browser.mjs https://47.239.50.129/mvp/maps/ backend/.env.phone-mvp backend/.venv/tmp/NEW_PRODUCT.json
node frontend/tools/check_product_browser.mjs https://47.239.50.129/mvp/maps/ backend/.env.phone-mvp backend/.venv/tmp/NEW_BOUNDARIES.json --boundaries
node frontend/tools/check_product_browser.mjs https://47.239.50.129/mvp/maps/ backend/.env.phone-mvp backend/.venv/tmp/NEW_CAMERA_BOUNDARIES.json --camera-boundaries
node frontend/tools/check_phone_relay_browser.mjs https://47.239.50.129/mvp/maps/test backend/.env.phone-mvp backend/.venv/tmp/NEW_DIAGNOSTIC.json
```

Output files must not already exist. The product tool checks phone/desktop layouts, default output intent, automatic matching of local Chinese/English voices, start/cleanup from a real click, and visibility of the raw and decoded returned images. It records the exact rear-camera request issued by the product. Because Chrome's native fake camera has no physical camera orientation, the tool explicitly simulates support for the constraint and removes it only in the fixture before opening the fake camera. Synthetic routes/GPS are also explicitly labeled; inference results and speech events come from real runtime modules, but do not prove that a phone selected its actual rear camera. The diagnostic tool separately calls real ORS. Neither tool starts services, bypasses TLS, or requests a physical camera; neither represents actual iPhone location or listening verification.

To check target annotations returned by the real algorithm, explicitly set `PRS_FAKE_CAMERA_VIDEO` to the absolute path of an existing `.y4m` test file, then run the product tool. Chrome still uses its native fake camera, and the file should contain a recognizable target. The tool requires both the raw preview and decoded returned image to be visible, and at least one real model result to contain a target; it also saves a screenshot of the perception area. It does not download or convert assets. File content does not represent the phone's surroundings and is not used to assess real-world box accuracy.

The product tool's `--boundaries` mode does not start inference. It temporarily hides the native voice list and explicitly dispatches a list-change event, confirming that a local voice appearing later is matched automatically but not played. It also checks that submitting empty coordinates expands/focuses the error field, a cached page retains its language observer, and hiding clears state without automatically restarting when foregrounded. Voice-list events, page cache, and visibility changes use explicitly labeled browser fixtures; they do not represent iPhone OS background testing. Speech-start events still come from the native engine.

`--camera-boundaries` mode uses only explicit permission-Promise, late-permission, and error fixtures. It checks that a double-click makes only one camera request, stopping is possible while permission is pending, late streams are cleaned up, and permission denial/missing rear camera/unsupported orientation constraints produce explicit messages and allow manual recovery. It does not open a camera, connect to inference, or retry, and cannot replace real iPhone permission/camera verification.

Risk alerts take priority over navigation announcements. After an alert ends, only the latest still-valid navigation instruction resumes. Stopping perception does not stop navigation; stopping navigation does not end perception. Results past their original expiry are displayed only and do not create new risk alerts; silence does not mean no risk. The page does not automatically plan obstacle-avoidance routes from visual targets.

Safari camera access requires an HTTPS secure context; see [WebKit media-access requirements](https://webkit.org/blog/7763/a-closer-look-into-webrtc/). For a manually installed private certificate, enable full trust as described in [Apple's certificate-trust guidance](https://support.apple.com/en-gb/102390). See [Apple's location-permission guide](https://support.apple.com/en-ca/guide/iphone/iph3dd5f9be/ios) for Location Services and Precise Location.

## Shutdown and Failure Boundaries

The computer must remain powered on, connected to the network, and running the launcher; the model runs on the computer, and the server relay cannot replace it. Press `Ctrl+C` in the launch terminal. The launcher closes only the tunnel, SSH connection, and two local child processes it owns for this run. SSH interruption, forwarding failure, or child-process exit ends this run; there is no automatic reconnect, restart, or service switch.

After shutdown, server relay configuration and access authentication remain installed. Without the tunnel, authenticated `/mvp/` requests return 502; requests do not fall back to `/prs/` or another inference service. Rerun the daily startup command to restore the path. For 401, check the web access account; for 502, check the local launcher and tunnel. Resolve permission or speech errors while the phone is in the foreground; repeatedly restarting the server does not grant permission.

The user must still verify the physical iPhone camera, precise location, audible speaker output, and navigation/detection integration over a mobile network. HTTPS page checks, synthetic location, model results, and native speech events do not replace acceptance on an actual phone.
