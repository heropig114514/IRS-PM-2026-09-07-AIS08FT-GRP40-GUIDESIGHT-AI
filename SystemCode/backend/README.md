# Backend development

Scheme E is an isolated shared-YOLO object camera-z experiment. See
[object_distance/README.md](object_distance/README.md) for local data preparation, Windows training,
evaluation, teammate-weight compatibility and comparison boundaries. A/B/C defaults are unchanged.

Distance estimation defaults to dual-model mode A, with optional geometric mode B and low-frequency
depth mode C. See [DISTANCE-MODES.md](DISTANCE-MODES.md) for configuration, commands, focal lengths,
class heights and performance records.

> The local dual-model MVP opens at `http://127.0.0.1:8766/live/`, using separate Web/GPU environments
> and an integration adapter. See [LOCAL-MVP.md](LOCAL-MVP.md) for setup, launchers and validation boundaries.

The same integration entry also provides `/integration/video/` for browser-selected local videos,
using its own controller, `/ws/detect/local/` and the original dual models. Public source provenance,
1x playback/two-clock constraints and Chrome checks are documented in the
[local video guide](LOCAL-MVP.md#continuous-local-video-entry); no computer camera is required.

Street playback uses moving-camera MOT17-07 daylight and MOT17-10 night sequences with H.264 browser
copies. Two original previews, two playback copies and their source manifest are versioned; complete
annotations and run reports are managed separately. See the
[data guide](model_tests/DATASETS.md#continuous-street-video-for-local-integration) and
[MVP validation guide](LOCAL-MVP.md#acceptance-method-and-boundaries).

All backend development belongs here. The stack is Python 3.12, Django, Django REST Framework and
Uvicorn, using dependency versions from the local AiInterviewer environment while implementing
independent configuration/public interfaces without interview business logic.

Local mode wraps the original entry with `integration.asgi` and connects to separate
`integration.inference.asgi`, reusing `ResidentDetector` without installing Torch in the Web environment.
HF mode forwards through backend HTTPS/WS without local GPU or SSH tunnels; see [ZEROGPU.md](ZEROGPU.md).

The optional independent YOLO26 Depth model uses median valid depth inside each detection box as
meter distance and draws it on the annotated JPEG. Integration, compatibility, GPU/Space configuration
and validation boundaries are in [DEPTH.md](DEPTH.md). The realtime path uses BoT-SORT with camera-motion
compensation, smooths distance by track ID and reports approaching/receding/stationary state; see
[TRACKING.md](TRACKING.md) for ownership, configuration, tests and limits. Stateless TTC and configurable
risk rules return score, level, alert request and priority; see [RISK.md](RISK.md) for formulas/defaults.
The algorithm emits risk fields only. After explicit user activation, local Guidance consumes them
and schedules shared browser speech/vibration without changing scoring rules.
Per-class traffic-light threshold, the centre ROI pass, front-light marking, and the pedestrian
light-colour classifier study (results and integration contract) are in [TRAFFIC-LIGHT.md](TRAFFIC-LIGHT.md).
The default 87-class walking-aid model (`PRS_DETECTION_MODEL=yolo26n-walk`: poles, bollards, cones,
stairs and pedestrian-light colour) is described in [WALK-MODEL.md](WALK-MODEL.md).

Qwen2.5-VL scene description (`POST /scene/describe/`) is documented in [VLM.md](VLM.md). Local model
tests live in [model_tests](model_tests/README.md), including entry points, samples and output conventions.
From this directory, the separate GPU environment runs
`.\.venv-inference\Scripts\python.exe -X utf8 -m model_tests.run_depth --output model_tests/outputs/bus-run-3`,
using the bundled bus image by default and saving annotations/JSONL. Public datasets, suggested
quantities and model comparison protocols are in [DATASETS.md](model_tests/DATASETS.md).

## Installation and startup (PowerShell)

The full local MVP uses [separate environments and launchers](LOCAL-MVP.md). The commands below start
the original Web entry with Echo/HF and existing HTTP endpoints, without the local inference adapter.
Local model tests require a separately prepared GPU environment. From the repository root:

```powershell
cd SystemCode/backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
```

Set `.env`'s `DJANGO_SECRET_KEY` to the generated value, then run:

```powershell
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe -m uvicorn config.asgi:application --host 127.0.0.1 --port 8766 --workers 1 --lifespan on --ws websockets-sansio --ws-max-size 1114120 --ws-per-message-deflate false
```

Copy the template only when first creating `.env`; do not overwrite existing settings. Process
variables override `.env`. Missing secrets fail startup. The default untracked SQLite file is
`SystemCode/backend/db.sqlite3`. Development uses port `8766` to avoid the existing local `8765` service.

The [health endpoint](http://127.0.0.1:8766/api/health/) returns `{"status":"ok","database":"sqlite"}`;
database failures return 503 without retrying or disclosing credentials. `/` redirects to `/live/`;
`/ws/echo/` is a single-process in-memory echo socket. To access Django administration at `/admin/`,
first create an administrator:

```powershell
.\.venv\Scripts\python.exe manage.py createsuperuser
```

`/maps/` serves Leaflet/OSM walking routes; `/maps/config/` exposes only ORS configuration status.
Set `ORS_API_KEY` in `.env` and restart this app; a missing key returns 503. `POST /maps/route/`
accepts JSON `start`/`end` coordinate arrays in [longitude,latitude] order, requiring valid distinct
points, strict same-origin Origin/Host and no extra fields. This anonymous diagnostic endpoint uses
no cookies; the proxy removes session credentials. Responses include GeoJSON LineString `geometry`,
`summary.distance_m/duration_s`, `steps` (instruction/type/distance_m/duration_s/way_points) and request_id.
Original ORS English instructions are preserved; the frontend also displays Chinese turn labels.

`core/services.py` calls `api.heigit.org/openrouteservice/v2/directions/foot-walking/geojson` with
credentials only in the HTTPS Authorization header. Each worker permits two concurrent calls and
20 requests/minute. Limits are 2 KiB requests, 1 MiB upstream responses, 20-second network I/O and
25-second client requests. Failures are explicit: no retries, redirects or substitute routes. Page,
configuration and route responses are uncached. Coordinates are processed without persistence;
logs exclude bodies, coordinates and keys. Mocked tests do not prove actual ORS/GPS/tile operation.

With `DEBUG=False`, Uvicorn does not serve admin static assets. For local administration only, use
Django's separate development server:

```powershell
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8766 --insecure
```

`--insecure` is for local static assets, never public deployment. Do not bind both servers to one port.

## Structure and interface boundaries

- `config/base.py`: shared applications, session authentication, CSRF and logging.
- `config/settings.py`: local SQLite and Host allowlist.
- `config/production.py`: explicit PostgreSQL/HTTPS, without development-database fallback.
- `core/views.py`: public read-only health check.
- `core/errors.py`: preserve DRF errors; log unexpected exceptions and return a fixed 500.
- `core/tests.py`: isolated database and synthetic-failure tests.

APIs require login by default; health explicitly permits anonymous access. Create new business apps
here, register them in `INSTALLED_APPS`/`config/urls.py`, and separate ORM (`models.py`), contracts
(`serializers.py`), business rules (`services.py`) and HTTP adaptation (`views.py`). Create files only
when their functionality exists.

`core/echo.py` provides unchanged binary echo and application ping/pong: 15-second input idle timeout,
1 MiB frames, 1024-byte text and four connections per process. Require explicitly configured Origin
and matching Host. Use one Uvicorn worker; limits are not shared across processes. `core/camera.py`
serves fixed allowlisted frontend files, never arbitrary paths. Anonymous diagnostics store no media
and need no admin account. See the [frontend guide](../frontend/README.md) for protocols, image defaults
and tests. No business tables, file storage, queues or product account API are provided.

The [MCP control interface](MCP.md) at `/mcp` provides page discovery, UI operations, backend calls,
ORM and SQL according to its configured catalog. Opening the camera/maps page registers a control
channel. No additional MCP authentication is introduced; require one worker and lifespan.

`config/asgi.py` dispatches `/ws/echo/`, `/ws/detect/hf/`, `/ws/control/`, `/ws/agent/` and MCP;
retired HTTP detection and other retired video sockets are rejected. `core/detector.py` contains shared
JPEG/preprocessing/box/annotation functions without a CPU inference session. Original model, 640,
0.25 and JPEG80 remain unchanged. Compute precision/backend come from the required COCO-qualified
`models/inference-plan.json`; output arrays remain FP32. GPU experiments and failure diagnostics are
in [ACCELERATION.md](ACCELERATION.md), with versioned notebook reruns. HF identity is private; check
daily free seconds/call counts before starting. Video sessions last at most 55 seconds without
automatic reallocation.

Local integration additionally forwards same-origin `/ws/detect/local/` to separate-process `/ws/infer/`.
`integration/` owns contracts/sessions/process boundaries; original `realtime/` algorithms and the HF
handler retain their logic. Explicitly install `requirements-dev.txt` and `requirements-inference.txt`
in their respective environments. The launcher does not install, download, migrate, restart or deploy.
Model health is recorded separately from Web/database health. The existing local MVP uses pretrained
weights, requires no training workflow and never automatically replaces/deploys models.

Use same-origin frontend proxies, never wildcard CORS. Session-authenticated writes require valid
CSRF tokens. API failures must not trigger implicit retries or algorithm changes. Parameter changes
must follow the authorization and reproducibility rules in [AGENTS.md](../AGENTS.md).

## Checks

From `SystemCode/backend/`:

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py test
.\.venv\Scripts\ruff.exe check .
```

Tests use temporary databases without modifying the development database. `.github/workflows/backend.yml`
runs automated checks under Python 3.12. There is no `tools/check_docs.py`; manually review comments
and declaration/variable indexes. CI does not establish comment semantics. Run frontend checks with
`node --test ../frontend/tests/*.test.mjs` using Node 22+. Backend queue tests and frontend socket mocks
do not validate actual phone permissions or cameras.

## Production configuration boundaries

Explicitly install `requirements-production.txt`, select `DJANGO_SETTINGS_MODULE=config.production`,
set all template `DJANGO_ALLOWED_HOSTS`/`POSTGRES_*` variables, then migrate and start. Missing settings
or PostgreSQL failures never fall back to SQLite.

The camera server instead explicitly uses `config.camera_server`, its own SQLite path and trusted
same-host Nginx `/prs/` HTTPS routing. Separate users, environment and resource limits are described
in the [deployment guide](deploy/README.md). This is a deliberate configuration, not database failover.
Future PostgreSQL business deployments must still select `config.production`; trust proxy headers
only after validating the HTTPS boundary. Run `manage.py collectstatic --noinput` and serve admin
assets through the Web server. Camera deployment does not expose `/prs/admin/` or require admin access.

## Voice/text assistant MVP

Maps/camera pages expose Qwen -> intent catalog -> Skill -> MCP -> existing controllers, with
missing-input questions, cross-page conversations, progress and cancellation. Configuration,
operation, capability limits and reproducible checks are in [AGENT.md](AGENT.md).

## External distance experiments

The independent [nuScenes A/B/C/E evaluation tool](model_tests/unified_evaluation/README.md)
compares center-z errors, detection/distance coverage, paired targets, temporal behavior and
synchronized latency/resources. It requires explicit smoke/full scope, uses local data/weights,
and preserves existing production and E training interfaces.
