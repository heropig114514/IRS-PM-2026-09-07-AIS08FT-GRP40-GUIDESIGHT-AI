# GUIDESIGHT AI

## SECTION 1 : PROJECT TITLE

GUIDESIGHT AI — Voice-Assisted Navigation and Perception

## SECTION 2 : EXECUTIVE SUMMARY / PAPER ABSTRACT

This project integrates browser-based walking navigation, a bilingual voice assistant, and camera-based environmental awareness. The backend provides agent, routing, and inference interfaces; the frontend uses native browser APIs for maps, speech, and camera interaction. The repository includes local and public-service guides, automated checks, and model evaluation fixtures. See the engineering documentation below for scope and verification boundaries.

## SECTION 3 : CREDITS / PROJECT CONTRIBUTION

Verified contributor names, identifiers, and individual work items have not been provided in this repository. Add the approved contribution table before submission; no contributor details are inferred here.

## SECTION 4 : VIDEO OF SYSTEM MODELLING & USE CASE DEMO

The [`Video/`](Video/README.md) directory is reserved for the project demonstration. No presentation video is included yet. The MOT17 clips under `SystemCode/backend/model_tests/datasets/` are test fixtures, not a presentation recording.

## SECTION 5 : USER GUIDE

Project website: [GuideSight AI](https://47.239.50.129/mvp/maps/)

- [System code guide](SystemCode/README.md)
- [Current phone deployment: Ubuntu relay and local GPU](SystemCode/backend/SERVER-RELAY.md)
- [Accepted phone MVP: deployment and reproducible startup](SystemCode/backend/MVP-RELEASE.md)
- [Backend setup and checks](SystemCode/backend/README.md)
- [Frontend guide](SystemCode/frontend/README.md)
- [Local MVP setup and verification boundaries](SystemCode/backend/LOCAL-MVP.md)
- [Operations and deployment guide](SystemCode/backend/deploy/README.md)

The repository follows the five top-level submission sections in the [IRS-PM project submission template](https://github.com/IRS-PM/Workshop-Project-Submission-Template) and places application code directly under `SystemCode/`. The additional root `.github/` directory is retained because GitHub Actions only discovers workflows stored in root `.github/workflows/`; it contains CI configuration, not a project deliverable.

## SECTION 6 : PROJECT REPORT / PAPER

The [`ProjectReport/`](ProjectReport/README.md) directory is reserved for the final report, paper, and appendices. No report or paper is included yet.

## SECTION 7 : MISCELLANEOUS

The [`Miscellaneous/`](Miscellaneous/README.md) directory is reserved for optional supporting materials. No additional submission material is included yet.

## Engineering Overview and Operations

This repository contains the frontend and backend under `SystemCode/`. The backend follows the Django, DRF, and ASGI stack and layered design used by AiInterviewer while maintaining its own business logic and database. The frontend uses native browser APIs.

The code root contains `SystemCode/backend/` and `SystemCode/frontend/`; run commands from the relevant directory. The local MVP connects existing software and algorithms through a separate web process and CUDA inference process. See [Local MVP](SystemCode/backend/LOCAL-MVP.md) for environment setup, official weights, launchers, and verification boundaries.

The current phone deployment serves the webpage on the Ubuntu relay `47.239.50.129` and sends
inference through an owned reverse SSH connection to the operator's local NVIDIA GPU. See
[Server relay](SystemCode/backend/SERVER-RELAY.md) for the actual public entry, explicit proxy
path, matching low-latency settings and operation. The tagged
[MVP release](SystemCode/backend/MVP-RELEASE.md) retains the accepted direct-laptop deployment
and its physical-phone measurement as a separate comparison baseline. The existing `/prs/`
service remains independent.

```text
.
├── README.md
├── Miscellaneous/
├── ProjectReport/
├── SystemCode/
│   ├── .gitattributes
│   ├── .gitignore
│   ├── AGENTS.md
│   ├── README.md
│   ├── backend/      Django/ASGI services, inference, integrations, deployment, and tests
│   └── frontend/     Browser app, maps, speech/camera clients, and frontend checks
├── Video/
└── .github/
    └── workflows/   Required root-level GitHub Actions checks
```

The live page is `/live/`. The original software provides Echo/HF modes; the local integration entry additionally enables local CUDA dual-model detection. The browser sends JPEG frames over a same-origin WebSocket and displays annotated results. Live images are processed in memory: there is no recording, storage, automatic reconnection, or retry. Echo permits at most four connections; detection sessions are rate-limited according to their protocol.

Start the local entry point with `SystemCode/backend/tools/run_local_mvp.py`; the default URL is `http://127.0.0.1:8766/live/`. The algorithms reuse YOLO26n, YOLO26 Depth, BoT-SORT, distance-motion state, and TTC/rule-based risk logic. Browser Guidance arbitrates speech/vibration alerts and navigation examples after the user enables it. The integration layer adapts protocols and runtime environments without changing model parameters or risk rules. See [model documentation](SystemCode/backend/models/README.md) for artifacts and licenses.

The unified MVP uses the launcher's explicit `--assist` mode at `http://127.0.0.1:8766/maps/`. The product page provides walking navigation, environmental perception, tap-to-toggle voice input, and stop-all controls. Page entry requests one device location fix as the route origin and temporary camera/microphone permission; temporary media tracks are immediately stopped. These permission checks do not plan a route, record audio, run inference, or speak. Users select a destination on the map and start navigation explicitly. Starting perception opens the rear-facing camera and displays the returned annotated image and target list; raw video remains internal. Unsupported camera constraints and permission failures are reported without camera substitution or automatic retry. Sample endpoints, coordinate fields, manual camera selection, video replay, and output diagnostics remain on `/maps/test` (or `/mvp/maps/test` through the phone relay).

Speech, navigation announcements, and risk alerts are enabled by default; vibration is enabled when supported. Both unified pages play mono 24 kHz PCM through Web Audio and do not select browser-local voices. The server uses the fixed Cherry voice; product speech follows the interface language at 2× rate and full application volume, with audible volume controlled by the device. Manual speech, assistant replies, navigation, and uncovered warning phrases use the same-origin `/ws/tts/` stream. A real detection/navigation start or assistant gesture activates speech; automatic announcements unlock only after the first PCM is scheduled for playback. Enabled controls and browser playback events do not establish permission or physical audibility. The diagnostic page retains language, rate, volume, and playback checks; standalone entries retain their separate output policies.

Risk speech preloads `assets/risk-audio/manifest.json` and 66 prepared Chinese/English WAVs under the current mount point. These resources cover 72 warnings per language for person, bicycle, car, and motorcycle, including direction, alert grade, and approaching/present wording. At 2× rate, exact covered phrases are assembled from verified PCM and played as one continuous buffer without a TTS request. Other phrases or rates retain the original TTS path; a failed prepared pack reports an error without silently switching to online synthesis or retrying. Prepared audio still requires a current, matching risk before playback. A short sentence already playing finishes for the same confirmed target; a higher-priority different target can interrupt it. Manual stop cancels pending output.

The unified page reuses the existing map, navigation MCP, inference modules, and shared speech/vibration arbiter without changing navigation or risk calculations. Risk alerts take priority, and only a still-valid navigation instruction can resume afterward. This mode supports continuous local inference; real route planning and unprepared speech remain subject to their configured online-service prerequisites. Stopping perception or navigation stops its own work; stop-all and page hiding stop both, without automatic resume. See the [frontend guide](SystemCode/frontend/README.md#unified-navigation-and-perception) and [unified entry documentation](SystemCode/backend/LOCAL-MVP.md#unified-navigation-and-perception-entry) for startup and acceptance boundaries.

The independent local-video entry point is `/integration/video/`. After the user selects a local file, it plays at native 1× speed and does not request computer-camera access. It reuses the same dual models and alert path, preserving existing parameters and the 55-second session budget. Current samples are MOT17 daytime/nighttime moving street scenes; see [video dataset notes](SystemCode/backend/model_tests/DATASETS.md#continuous-street-video-for-local-integration) for sources and explicit transcoding records, and [continuous video notes](SystemCode/backend/LOCAL-MVP.md#continuous-local-video-entry) for the capture/media clocks and acceptance commands.

The two original MOT17 previews, two H.264 playback copies, and source manifest are repository test assets. The full dataset, annotations, and runtime outputs are managed separately according to directory rules. Acceptance checks distinguish throughput, capture-to-alert latency, and actual device output. See [local acceptance notes](SystemCode/backend/LOCAL-MVP.md#acceptance-method-and-boundaries) for details.

See the [backend guide](SystemCode/backend/README.md) for development startup and checks, the [frontend guide](SystemCode/frontend/README.md) for protocols and operations, and the [operations guide](SystemCode/backend/deploy/README.md) for isolated deployment. Persistent media and business-account APIs are not implemented.

The optional [Hugging Face ZeroGPU Space](https://huggingface.co/spaces/Heropig114514/prs-yolo26) mode requires a server token and sufficient free quota. A video is limited to 55 seconds, with 55/10-second budgets for the first and subsequent frames. See [ZeroGPU notes](SystemCode/backend/ZEROGPU.md) for configuration and quota boundaries. The local MVP's default offline mode explicitly disables external credentials; the unified mode permits ORS only when explicitly configured.

The repository currently provides pass-through, local CUDA integration, HF GPU detection, optional scene description, and location/walking routes. Model export, Space publishing, and verification tools are in `SystemCode/backend/tools/`; structured experiment results are in `SystemCode/backend/benchmarks/`.

The [walking-route test page](https://47.239.50.129/prs/maps/) uses Leaflet 1.9.4, an OpenStreetMap basemap, and ORS `foot-walking` routes. The server configures `ORS_API_KEY`; the browser receives only the configuration status. Select “Get device origin”, choose a destination on the map, and submit the route, or explicitly select the fixed NUS example. The origin and destination are sent to PRS and ORS for planning and are not stored in a database. The response includes the route, distance, estimated duration, and turn-by-turn steps. The initial NUS map preview and example are not device locations.

At the user's request, ORS replaces the Google route implementation from the original proposal; the proposal file itself was not rewritten. The map page now supports continuous location updates, turn instructions, remaining distance, off-route warnings, arrival detection, and manual stopping. The agent can use `navigation_start`, `navigation_stop`, and `navigation_status` on the same controller. Route planning and browser location permission are prerequisites. Hiding the page stops navigation; it does not automatically replan. The unified entry connects navigation TTS and risk alerts while keeping their inputs and original calculations independent; it does not generate obstacle-avoidance routes. See the [frontend guide](SystemCode/frontend/README.md) and [MCP contract](SystemCode/backend/MCP.md) for thresholds and verification boundaries. Future formal experiments should record the navigation implementation and map-data source.

Local Guidance navigation examples are for alert-arbitration integration only; they do not verify GPS, ORS, or complete navigation. Actual phone audio, vibration, and risk accuracy require separate verification.

Course reports, presentation videos, survey materials, and archives are not included in this repository yet; the corresponding submission directories contain indexes only. Test videos remain under `SystemCode/backend/model_tests/datasets/` and are not presentation deliverables. The code-root `.gitignore` uses an allowlist for `SystemCode/`; add any new code-root path intended for version control to that allowlist.
