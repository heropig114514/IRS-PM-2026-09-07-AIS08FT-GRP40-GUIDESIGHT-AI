# PRS camera deployment

## Automated Phone MVP deployment

The repository can deploy the Phone MVP after a successful push to `main`. The workflow is
`.github/workflows/backend.yml`, so deployment runs only after the existing backend and frontend
checks pass. It uses the already isolated `prs-mvp-web.service` boundary:

- source releases are stored under `/opt/prs-practice/mvp/releases/<commit-sha>`;
- only `/opt/prs-practice/mvp/current` is switched;
- only `prs-mvp-web.service` is restarted on `127.0.0.1:18766`;
- `/opt/prs-practice/current`, `prs-camera.service`, Nginx, the inference tunnel, and the MVP
  database are not changed by the workflow;
- old releases are retained and no automatic migration, rollback, dependency installation, or
  provider retry is performed.

The upload archive keeps the `backend/` and `frontend/` release roots but omits local credentials,
virtual environments, backend test modules, development tools, generated reports, large test datasets,
benchmark data, the frontend test suite, and model weight files. The MVP server uses the committed
model manifests and the separately managed inference service; it does not need those artifacts.

### One-time server preparation

The server must already have the MVP service, environment file, runtime and Nginx relay configured
as described in [PHONE-MVP.md](../PHONE-MVP.md). The SSH account used by Actions must be able to
run the following commands non-interactively with `sudo -n`:

```text
install, tar, chown, ln, mv, systemctl cat/show/restart/is-active, and rm
```

The account must be able to write `/opt/prs-practice/mvp/releases/` through the listed sudo
commands and belong to the `prs-mvp-web` group so it can source `/etc/prs-mvp-web/app.env` for
read-only checks as an unprivileged user. The service user is `prs-mvp-web`, and the runtime must be
an existing directory under `/opt/prs-practice/runtimes/`. Configure a dedicated deployment account and a narrowly scoped
sudoers rule for this project; do not grant it control over the other project's service or Nginx.
The deployment script rejects any root, runtime or service outside these fixed MVP boundaries.

The runtime path must be recorded as a GitHub Actions repository variable, for example:

```text
PRS_MVP_RUNTIME=/opt/prs-practice/runtimes/outputs-c6eb9264bd29b084
```

### GitHub configuration

Add these repository **secrets**:

```text
PRS_DEPLOY_HOST       server hostname or address
PRS_DEPLOY_USER       dedicated SSH deployment account
PRS_DEPLOY_SSH_KEY    private key for that account
PRS_DEPLOY_KNOWN_HOSTS
```

`PRS_DEPLOY_KNOWN_HOSTS` must contain the reviewed `known_hosts` line for the server. Add this
repository **variable**:

```text
PRS_DEPLOY_PORT=22
PRS_MVP_RUNTIME=/opt/prs-practice/runtimes/outputs-c6eb9264bd29b084
```

The first push to `main` runs the normal checks, then uploads only `SystemCode/backend` and
`SystemCode/frontend` as a commit-named archive. The remote script performs Django checks and
`pip check`, atomically switches the MVP link, restarts the one service, waits for its listener, and
checks `/api/health/` once. A failed health check leaves the new release selected for explicit
operator diagnosis; use the retained previous link for a manual recovery. Pull requests never
deploy.

Phone access exposes exactly [the navigation page](https://47.239.50.129/mvp/maps/) and
[the diagnostic page](https://47.239.50.129/mvp/maps/test) through a separate public HTTPS relay; the
bare `/mvp` and `/mvp/` mount paths return 404. Required API, asset, WebSocket, and inference tunnel
paths remain internal support routes. The relay does not replace the `/prs/` service described here.
The dedicated `prs-mvp-web.service` owns the two public Web pages on
127.0.0.1:18766. A laptop-hosted GPU worker may connect through a distinct reverse SSH inference
port, 127.0.0.1:18767; Web availability does not depend on that tunnel. Its service template,
deployment boundary, and operator steps are documented in the [phone MVP guide](../PHONE-MVP.md).

For every `--source-root` option below, pass the repository root. The deployment tool reads code from
`SystemCode/backend/` and `SystemCode/frontend/` within that snapshot.
Run the commands from `SystemCode/backend/`, where the deployment tool and `.venv` live.

The [MCP control interface](../MCP.md) uses `/prs/mcp` in the production template. Voice assistance
requires a complete release of Skills, runtime and browser recording assets. To retain the restricted
public catalog:

```powershell
.\.venv\Scripts\python.exe -m tools.deploy_phone_live --stage --profile full --mcp-profile outputs --runtime <verified-existing-runtime-directory> --git-commit <source-base-SHA> --manifest <manifest-path>
.\.venv\Scripts\python.exe -m tools.deploy_phone_live --activate --agent-env .env --manifest <manifest-path>
```

`--agent-env` transfers only explicitly configured allowlisted `PRS_AGENT_*`, `PRS_ASR_MODEL`,
`DASHSCOPE_*` and `VLM_MODEL` fields, never local Django/database/HF/ORS fields. File hashes record
actual released content, including uncommitted files; Git SHA is only a baseline. This path preserves
HF/ORS credentials and the public outputs catalog without adding public database/DOM tools. It
restarts only prs-camera, checks health and retains backups for manual recovery. Legacy small UI
overlays require an explicit pre-agent source snapshot and cannot publish assistant pages.

Standalone navigation releases use `--profile navigation --source-root <reviewed-repository-root> --git-commit <SHA>
--runtime <verified-active-runtime>`, followed by `--activate` with the same manifest. They update only
map/navigation, shared language, MCP control and static dependencies, preserving camera/pipeline,
inference/models, HTTP routes, systemd/nginx/environment and credentials. The shared asset allowlist
includes `scene-client.js` without publishing scene UI/backend endpoints. No dependencies are added;
staging verifies navigation registration and unchanged model bytes. Activation switches current,
restarts prs-camera and checks listeners/health without nginx reload or database migration.

Map speech requires precisely allowlisted `/assets/integration/` files. Navigation/outputs profiles
preserve `config/urls.py`; staging checks that the active release already has `assets/<path:name>`.
An older single-segment route rejects staging and requires a separately reviewed route upgrade;
the tool does not rewrite unrelated HTTP routes. Full releases include that route and all speech
dependencies. Updating source/manifests does not establish that the live environment was upgraded.
`models/inference-plan.json` and `models/yolo26n.json` retain original bytes in Git via `.gitattributes`
`-text`, including their existing CRLF. Deployment requires byte equality; this does not change JSON,
inference parameters or server files, or relax hash checks.

Explicitly install updated `requirements.txt` before releasing. Staging verifies dependencies and
ASGI imports without installing automatically. Service templates require `--lifespan on` and one
worker. Backend control code, assets and dependencies enter the same allowlist; the manifest records
actual activation.

Device output releases explicitly use `--profile outputs --source-root <reviewed-repository-root>
--runtime <isolated-runtime-directory> --git-commit <SHA>`. They cover vibration/speech/navigation UI,
MCP/control connections and dependencies while preserving camera/inference source, model metadata
and plan. Both model metadata files must match active bytes. Runtimes under `/opt/prs-practice/runtimes/`
are copied from the old environment before installing exact direct dependencies in the new copy;
record resolved versions and pip check, preserving `/opt/prs-practice/runtime-venv`. Staging explicitly
renders the service interpreter and records both Git template and rendered hashes. Before activation,
verify current and every file hash, back up dedicated service/nginx/environment files, and set
`PRS_MCP_PROFILE=outputs` while preserving other fields/credentials. Restart only prs-camera, verify
health and reload nginx for this output deployment path.

The outputs catalog contains session discovery, seven output tools and three navigation tools
(11 total). Navigation tools return device location; review that contract before deploying. Generic
DOM/database tools remain unregistered. Output manifests include map/navigation dependencies required
by `ai-control.js`; source changes alone do not mean deployment. Navigation adds no runtime dependencies
and can reuse a verified outputs runtime with an exported Git snapshot. No database migration,
automatic retry or rollback occurs; retain old releases/runtimes/configuration for manual recovery.

The public entry is [the camera page](https://47.239.50.129/prs/live/); `/prs/` reaches the same page,
with Echo and the current HF video path. Video tuning uses `--profile live-tuning --runtime <active-runtime>`
and subsequent activation of that manifest. It overlays only camera/pipeline/client, PRS/HF transport
and their tests. Model metadata/plan must equal active bytes. Copying current preserves speech/vibration,
maps, MCP registry, systemd/nginx/environment, GPU computation and dependencies. Activation switches
source and restarts PRS only. Coordinate HF/PRS window/startup contracts: verify the new HF SHA with
CPU health, activate PRS, then verify public asset hashes. Health is not GPU performance measurement.
`warmup_bytes=524288` is one bidirectional same-connection warmup before the first result, adding 1 MiB
and counting its duration in first-frame latency. Preserve the 3-second control deadline, 5-second
worker idle limit and 55-second session/cold-wait budget. Direct HF tools acknowledge controls with
`receive_live_result`; browsers receive original detection results only.

Manual vibration releases use `--profile vibration --source-root <reviewed-repository-root> --git-commit <SHA>`.
They overlay only vibration UI/styles, shared translations, Python asset allowlist and related docs/tests,
requiring identical model metadata/plan bytes. Copy current into a new release, preserving camera/inference,
maps, ASGI and dependencies; do not delete old paths or overwrite systemd/nginx templates/tokens. Verify
staged hashes and unchanged current, switch the link, restart prs-camera and check health without nginx
reload, retries or rollback. Record Git SHA and retain the original release. Browser regression uses
`tools/check_vibration_browser.mjs` with an explicit vibration double; physical motor validation still
requires foreground manual testing on an Android phone.

`tools/deploy_phone_live.py` validates a pinned SSH host fingerprint, copies current into a new release,
removes explicitly listed retired source and overlays project-owned routes/pages/stream modules and
map frontend without changing the map API or other applications. Publish `maps.html`, `maps-page.js`,
`maps-client.js`, `i18n.js` and `translations.js` together so camera/maps language support stays aligned.
Map language shares the same-origin saved preference; changing it alters display only, without new
location/ORS requests, and preserves ORS English instructions. Verify the actual COCO-qualified
`models/inference-plan.json`, uploaded hashes, Django configuration and migration status before switching
current, restarting prs-camera and reloading nginx on the applicable full-deployment path. PRS and HF
must share one plan; clients verify precision/backend against session declarations for every frame.
No automatic rollback, retry or GPU invocation occurs. Retain old releases, backups and versions.

```powershell
.\.venv\Scripts\python.exe -m tools.deploy_phone_live --stage --manifest benchmarks/2026-10-05/maps-i18n-deployment.json
.\.venv\Scripts\python.exe -m tools.deploy_phone_live --activate --manifest benchmarks/2026-10-05/maps-i18n-deployment.json
```

Run frontend `node --test tests/*.test.mjs` before deployment; see the [frontend guide](../../frontend/README.md)
for browser commands and doubles. After deployment verify public map source hashes and run the tool's
`--live-route` mode for one explicitly identified NUS example route. Preserve diagnostics on failure,
without automatically retrying, rolling back or switching providers.

Local `server_info.txt` and `.env.hf` are Git-excluded credentials. Server tokens are written only to
root0600 `/etc/prs-camera/app.env`. systemd uses a separate runtime, one worker, MemoryMax256MiB,
CPUQuota20% and TasksMax32; WebSocket queue 5, frames 1 MiB, compression disabled. Nginx read timeouts
are 65 seconds for video WS, 70 seconds for MCP and 30 seconds for other PRS paths. Disable upstream
retry/buffering, strip Cookie/Authorization/Forwarded and return 404 for public admin. HF publishing
and retired-interface removal are in [ZEROGPU.md](../ZEROGPU.md); phone tests and historical tails are
in [PHONE-LIVE.md](../PHONE-LIVE.md).
