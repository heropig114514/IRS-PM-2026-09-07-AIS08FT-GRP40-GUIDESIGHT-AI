# PRS camera deployment

Phone access to the local GPU uses a separate `/mvp/` HTTPS relay and does not replace the `/prs/`
service described here. Reverse SSH, access authentication and explicit installation are documented
in the [phone MVP guide](../PHONE-MVP.md).

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

Standalone navigation releases use `--profile navigation --source-root <Git-snapshot> --git-commit <SHA>
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

Device output releases explicitly use `--profile outputs --source-root <reviewed-snapshot>
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

Manual vibration releases use `--profile vibration --source-root <reviewed-Git-snapshot> --git-commit <SHA>`.
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
