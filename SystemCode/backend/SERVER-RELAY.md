# Server relay with local GPU compute

## Responsibilities and current route

The phone Web application runs on `47.239.50.129` (Ubuntu 22.04). The server has no GPU and
forwards inference to an explicitly owned reverse SSH binding. The NVIDIA RTX 3060 Laptop GPU
on the operator's Windows computer performs detection, depth, tracking and risk calculation.

```text
Phone Safari / Chrome
  -> existing public HTTPS entry
  -> 47.239.50.129: Nginx
  -> server 127.0.0.1:18766, prs-mvp-web / integration.mobile_asgi
  -> server 127.0.0.1:18767, inference-only reverse SSH binding
  -> operator computer 127.0.0.1:8877, integration.inference.asgi
  -> RTX 3060 Laptop GPU
```

The comparison uses local port `8877` to preserve the previous laptop-only demo on `8767`.
The ordinary phone launcher still defaults to `8767`; select `--inference-port 8877` explicitly
when reproducing this allocation. Both bind only to loopback. The phone does not need the same
Wi-Fi or a local certificate profile. Keep the local GPU process and SSH session running.

The server's direct entry is `https://47.239.50.129/mvp/maps/`. During this deployment, direct
ICMP and TCP 22/80/443 from the operator's computer timed out; the phone also could not open
the IP entry. SSH through the existing explicit proxy succeeded. Server listeners, host firewall
and certificate checks were normal. This does not identify the cloud security-group or ISP cause.

The reachable comparison entry is the **existing server-side** temporary HTTPS hostname:

`https://noble-treatment-canyon-garmin.trycloudflare.com/mvp/maps/`

Its actual route includes Cloudflare before the Ubuntu server. It is not a direct laptop tunnel
and must not be reported as a Cloudflare-free measurement. The existing `prs-phone-access`
connector targets server loopback `18770`, where Nginx validates the exact public Host/Origin
and translates them to the canonical IP-origin contract. Private MCP/admin/general API paths
remain unavailable. No new wildcard Origin or publicly bound GPU port is used.

The current SSH leg also uses the operator's explicitly selected HTTP CONNECT proxy at
`127.0.0.1:7897`, because direct SSH timed out. That proxy is part of this experiment's network
path; it does not establish a nearby egress route. It is not a repository-wide proxy default.
Temporary HTTPS names can expire. Inspect the running connector before distributing an entry.

## Explicit comparison settings

Merge the nonsecret assignments in [phone-mvp.env.example](deploy/phone-mvp.env.example) into
the server's `/etc/prs-mvp-web/app.env`, preserving its existing private keys and database settings.
Back up the private file on the server with restricted permissions. Do not download or commit it.
Restart only `prs-mvp-web` after checking the inactive source and selected configuration.

The profile retains `low-latency-2`, max640/JPEG0.8, compact `metadata-v1` replies, one native
camera preview, 150 ms recent-result hints, up to six local optical-flow tracks, prepared risk
audio and a 2000 ms warning deadline. `integration.mobile_asgi` wraps the canonical app in
prepared startup speech **inside** public admission, so normalized Host/Origin checks still apply.
Uncovered wording follows the server's existing explicit provider configuration; risk warnings
do not make per-warning synthesis requests.

The local GPU uses original 80-class `yolo26n`, same-frame depth/interval1, native FP32 detection
with a CUDA graph, TensorRT FP32 depth and host ROI reduction. The repository's global 87-class
`yolo26n-walk` default is preserved; explicitly choose the accepted 80-class model for comparisons.
The engine manifest and checkpoints must match the local runtime and input shapes, as described
in [DEPTH-EXPORT.md](DEPTH-EXPORT.md). Unsupported shapes fail rather than select another backend.

## Startup and ownership

Prepare the Web/relay and GPU environments through [LOCAL-QUICKSTART.md](LOCAL-QUICKSTART.md).
From `SystemCode/backend`, prepare once with Python 3.12, then start the `phone` entry. On Linux,
replace `py -3.12` with `python3.12`. Obtain authorized SSH access through the team; do not put a
password in a command, GitHub issue or committed file.

```powershell
py -3.12 tools/local_demo.py prepare --cuda cu126
py -3.12 tools/local_demo.py phone --check
py -3.12 tools/local_demo.py phone --password-prompt --public-origin https://noble-treatment-canyon-garmin.trycloudflare.com
```

`prepare` installs project-local Web/relay and inference environments and downloads missing,
hash-verified original weights. An NVIDIA GPU with a compatible driver is required. The default
`phone` profile is Torch depth with host reduction; it is portable across supported GPUs and
does not claim the measured TensorRT result. The command starts **no local Web service**. It pins
the server SSH identity, prompts without echo, prewarms eight local frames, checks the server Web
unit and public page, and forwards only inference. `--check` performs local preflight without
SSH, a password prompt, model execution or public traffic.

For the accepted TensorRT compute policy, prepare with `--with-tensorrt`, export engines on the
actual GPU as described in [DEPTH-EXPORT.md](DEPTH-EXPORT.md), and append:

```text
--profile tensorrt --engine-manifest models/experiments/my-gpu/manifest.json
```

Do not copy an RTX 3060 engine to a different GPU/runtime. Use `--inference-python` only to
select an already prepared alternative interpreter. `--inference-port 8877` preserves a separate
local demo on `8767`. The server owns camera/overlay/audio policy; local profile selection does
not reconfigure the shared Web service.

When direct SSH is unavailable and an explicitly chosen HTTP CONNECT proxy is already running,
append `--http-proxy http://127.0.0.1:7897`. This applies to SSH and the launcher's HTTPS readiness
check, not to the phone's network. It requires `--password-prompt`; no global proxy setting or
automatic fallback is used. Omit both `--http-proxy` and `--public-origin` when direct SSH and the
IP HTTPS entry work. Without `--password-prompt`, the lower-level launcher retains the existing
ignored `server_info.txt` credential-file option. Never commit that file.

Only one process may own remote `127.0.0.1:18767`: colleagues must take turns. The current owner
stops detection and presses Ctrl+C before the next colleague starts. Occupied-port startup fails
without taking over another tunnel. Multiple colleagues running this command simultaneously do
not receive separate sessions or per-user routing. Closing the owned SSH connection releases its
binding. Keep the terminal and GPU computer awake throughout a phone test.

## Publication and verification

`main` is the source for the existing GitHub Actions server deployment. Successful checks stage
`backend/` and `frontend/` from `SystemCode/`, validate the inactive release, switch only
`/opt/prs-practice/mvp/current`, and restart only `prs-mvp-web`. The workflow preserves server
environment values, Nginx, the independent application and the live reverse inference binding.
It does not start the laptop GPU or install private credentials. See [deployment operations](deploy/README.md).

The workflow is `.github/workflows/backend.yml`; a push/merge to `main` runs backend/frontend
checks, then the `Deploy isolated Phone MVP` job. PR checks alone do not deploy. Monitor both
jobs and confirm the active release path ends with the merged commit. Failed checks prevent
deployment; a later activation failure is visible as a failed job and is not silently rolled back.
The server's existing deployment secrets and `PRS_MVP_RUNTIME` variable must remain configured.
Each colleague should pull the same `main` revision before starting inference. GPU offline status
does not prevent the independent Web/database health check or source deployment.

Before phone measurement, verify the public page profile and the routed worker's model hashes,
TensorRT/host backend, `ready=true` and `busy=false`. Validate real returned frames and prepared
startup/risk audio through the actual public route. Export the phone latency report after a
foreground camera session. Report cold start, steady capture-to-result P50/P95, completed model
FPS, local visual updates and voice separately. Server processing time is not total latency;
network residuals include SSH/proxy/relay queues and are not a pure RTT measurement.

The 231-frame accepted measurement in [MVP-RELEASE.md](MVP-RELEASE.md) belongs to the earlier
Cloudflare-to-laptop path. Keep it as the comparison baseline; it is not a measurement of this
server-relay route. Structured evidence for the new route belongs under `releases/server-relay-2026-10-29/`.
