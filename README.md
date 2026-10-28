# PRS Practice Module

## SECTION 1 : PROJECT TITLE

PRS Practice Module — Voice-Assisted Navigation and Perception

## SECTION 2 : EXECUTIVE SUMMARY / PAPER ABSTRACT

This project integrates browser-based walking navigation, a bilingual voice assistant, and camera-based environmental awareness. The backend provides agent, routing, and inference interfaces; the frontend uses native browser APIs for maps, speech, and camera interaction. The repository includes local and public-service guides, automated checks, and model evaluation fixtures. See the engineering documentation below for scope and verification boundaries.

## SECTION 3 : CREDITS / PROJECT CONTRIBUTION

Verified contributor names, identifiers, and individual work items have not been provided in this repository. Add the approved contribution table before submission; no contributor details are inferred here.

## SECTION 4 : VIDEO OF SYSTEM MODELLING & USE CASE DEMO

The [`Video/`](Video/README.md) directory is reserved for the project demonstration. No presentation video is included yet. The MOT17 clips under `backend/model_tests/datasets/` are test fixtures, not a presentation recording.

## SECTION 5 : USER GUIDE

- [Teammate local low-latency quickstart](backend/LOCAL-QUICKSTART.md) — explicit branch download,
  one preparation entry, portable Torch profiles and optional target-built TensorRT/LAN operation.
- [System code index](SystemCode/README.md)
- [Backend setup and checks](backend/README.md)
- [Frontend guide](frontend/README.md)
- [Local MVP setup and verification boundaries](backend/LOCAL-MVP.md)
- [Operations and deployment guide](backend/deploy/README.md)

The repository follows the [IRS-PM project submission template](https://github.com/IRS-PM/Workshop-Project-Submission-Template). The canonical source remains at root-level `backend/` and `frontend/` paths because CI, deployment tooling, and developer commands use those paths directly; `SystemCode/` indexes them without copying source files.

## SECTION 6 : PROJECT REPORT / PAPER

The [`ProjectReport/`](ProjectReport/README.md) directory is reserved for the final report, paper, and appendices. No report or paper is included yet.

## SECTION 7 : MISCELLANEOUS

The [`Miscellaneous/`](Miscellaneous/README.md) directory is reserved for optional supporting materials. No additional submission material is included yet.

## Engineering Overview and Operations

This repository contains the frontend and backend. The backend follows the Django, DRF, and ASGI stack and layered design used by AiInterviewer while maintaining its own business logic and database. The frontend uses native browser APIs.

The repository is organized around `backend/` and `frontend/`; run commands from the relevant directory. The local MVP connects existing software and algorithms through a separate web process and CUDA inference process. See [Local MVP](backend/LOCAL-MVP.md) for environment setup, official weights, launchers, and verification boundaries.

The public phone service exposes exactly two page entries: [Phone Navigation and Detection](https://47.239.50.129/mvp/maps/) and [Phone MVP Tests](https://47.239.50.129/mvp/maps/test). Their required API, asset, WebSocket, and inference-tunnel paths remain internal support routes; the bare `/mvp` and `/mvp/` paths are not pages. See [Phone MVP](backend/PHONE-MVP.md) for access, startup, and shutdown. The existing `/prs/` service remains independent.

```text
backend/              Backend development entry point
  config/             Development/production configuration and ASGI/WSGI entry points
  core/               Public interfaces, error handling, and framework tests
  deploy/             Isolated camera service, Nginx routes, and operations notes
  requirements*.txt   Pinned direct dependencies
  README.md           Setup, startup, checks, and extension guide
frontend/             Camera streaming, Leaflet/OSM walking routes, and client tests
.github/workflows/    Backend automated checks
```

The live page is `/live/`. The original software provides Echo/HF modes; the local integration entry additionally enables local CUDA dual-model detection. The browser sends JPEG frames over a same-origin WebSocket and displays annotated results. Live images are processed in memory: there is no recording, storage, automatic reconnection, or retry. Echo permits at most four connections; detection sessions are rate-limited according to their protocol.

Start the local entry point with `backend/tools/run_local_mvp.py`; the default URL is `http://127.0.0.1:8766/live/`. The algorithms reuse YOLO26n, YOLO26 Depth, BoT-SORT, distance-motion state, and TTC/rule-based risk logic. Browser Guidance arbitrates speech/vibration alerts and navigation examples after the user enables it. The integration layer adapts protocols and runtime environments without changing model parameters or risk rules. See [model documentation](backend/models/README.md) for artifacts and licenses.

The unified MVP uses the launcher's explicit `--assist` mode at `http://127.0.0.1:8766/maps/`. The product page groups walking navigation, the device camera, alert settings, and stop controls. The “Fill NUS example” action fills fixed endpoints only; users must still plan the route, and the example does not represent device location. Selecting “Environmental awareness” requests the phone's rear camera and displays both the raw image and returned annotated image without requiring device selection. Failures are reported explicitly; the app does not switch cameras or retry. The page reuses the existing map, navigation MCP, and single automatic speech/vibration arbiter. Risk alerts take priority, and only the currently valid navigation instruction resumes afterward. Speech, navigation announcements, and risk alerts are enabled by default; vibration is also enabled when supported by the device. The app matches a local voice to the interface language, fixes speech rate at 2×, and leaves actual volume to the device controls. If no matching voice exists, it reports that condition without switching languages or using a remote voice. The first real click on “Start speech”, detection, or navigation synchronously activates the native engine; automatic speech is released only after the engine emits its start event. Defaults do not imply granted permissions, active input, or audible output. Page load does not request camera/location access, plan routes, or speak. Test buttons, manual camera selection, voice controls, video replay, and detailed statistics are under `/maps/test` (or `/mvp/maps/test` through the phone relay). The diagnostic page retains manual voice selection. This mode supports continuous local inference; the web process uses ORS credentials only when explicitly configured, while HF and scene services remain disabled. Other independent test entry points retain their default offline mode and output-switch policy. See [unified entry documentation](backend/LOCAL-MVP.md#unified-navigation-and-perception-entry) for startup and acceptance boundaries.

The independent local-video entry point is `/integration/video/`. After the user selects a local file, it plays at native 1× speed and does not request computer-camera access. It reuses the same dual models and alert path, preserving existing parameters and the 55-second session budget. Current samples are MOT17 daytime/nighttime moving street scenes; see [video dataset notes](backend/model_tests/DATASETS.md#continuous-street-video-for-local-integration) for sources and explicit transcoding records, and [continuous video notes](backend/LOCAL-MVP.md#continuous-local-video-entry) for the capture/media clocks and acceptance commands.

The two original MOT17 previews, two H.264 playback copies, and source manifest are repository test assets. The full dataset, annotations, and runtime outputs are managed separately according to directory rules. Acceptance checks distinguish throughput, capture-to-alert latency, and actual device output. See [local acceptance notes](backend/LOCAL-MVP.md#acceptance-method-and-boundaries) for details.

See the [backend guide](backend/README.md) for development startup and checks, the [frontend guide](frontend/README.md) for protocols and operations, and the [operations guide](backend/deploy/README.md) for isolated deployment. Persistent media and business-account APIs are not implemented.

The optional [Hugging Face ZeroGPU Space](https://huggingface.co/spaces/Heropig114514/prs-yolo26) mode requires a server token and sufficient free quota. A video is limited to 55 seconds, with 55/10-second budgets for the first and subsequent frames. See [ZeroGPU notes](backend/ZEROGPU.md) for configuration and quota boundaries. The local MVP's default offline mode explicitly disables external credentials; the unified mode permits ORS only when explicitly configured.

The repository currently provides pass-through, local CUDA integration, HF GPU detection, optional scene description, and location/walking routes. Model export, Space publishing, and verification tools are in `backend/tools/`; structured experiment results are in `backend/benchmarks/`.

The [walking-route test page](https://47.239.50.129/prs/maps/) uses Leaflet 1.9.4, an OpenStreetMap basemap, and ORS `foot-walking` routes. The server configures `ORS_API_KEY`; the browser receives only the configuration status. Select “Get device origin”, choose a destination on the map, and submit the route, or explicitly select the fixed NUS example. The origin and destination are sent to PRS and ORS for planning and are not stored in a database. The response includes the route, distance, estimated duration, and turn-by-turn steps. The initial NUS map preview and example are not device locations.

At the user's request, ORS replaces the Google route implementation from the original proposal; the proposal file itself was not rewritten. The map page now supports continuous location updates, turn instructions, remaining distance, off-route warnings, arrival detection, and manual stopping. The agent can use `navigation_start`, `navigation_stop`, and `navigation_status` on the same controller. Route planning and browser location permission are prerequisites. Hiding the page stops navigation; it does not automatically replan. The unified entry connects navigation TTS and risk alerts while keeping their inputs and original calculations independent; it does not generate obstacle-avoidance routes. See the [frontend guide](frontend/README.md) and [MCP contract](backend/MCP.md) for thresholds and verification boundaries. Future formal experiments should record the navigation implementation and map-data source.

Local Guidance navigation examples are for alert-arbitration integration only; they do not verify GPS, ORS, or complete navigation. Actual phone audio, vibration, and risk accuracy require separate verification.

Course reports, presentation videos, survey materials, and archives are not included in this repository yet; the corresponding submission directories contain indexes only. Test videos remain under `backend/model_tests/datasets/` and are not presentation deliverables. `.gitignore` uses a root-directory allowlist, so any new root-level path intended for version control must be added to that allowlist explicitly.
