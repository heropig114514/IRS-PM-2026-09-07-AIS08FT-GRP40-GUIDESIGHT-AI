# Local low-latency teammate quickstart

## Scope and supported environment

Run the Web service and GPU worker on your own computer. Desktop frames stay on loopback;
phone frames can stay on the same LAN. No account or SSH access to47.239.50.129 is needed.
This guide uses `SystemCode/backend/` and `SystemCode/frontend/` in the unified MVP release.
For the accepted public phone preset, prepared risk voice and local visual tracking, start with
[MVP-RELEASE.md](MVP-RELEASE.md). The profiles below retain explicit Torch/comparison options.

Required: Python3.12, Windows or Linux, a usable NVIDIA CUDA GPU/driver, disk space for two
environments and Internet access for initial packages/checkpoints. macOS/CPU-only machines are
not supported by this GPU path. CUDA12.8 wheels are the preparation default; CUDA12.6 is an
explicit alternative for supported hardware/drivers. See the
[official Torch2.8 wheel choices](https://pytorch.org/get-started/previous-versions/).
On Linux, the pinned OpenCV wheel may also require the distribution's `libgl1` and
`libglib2.0-0` runtime packages. Driver/system-library installation is not performed by this tool.

No particular FPS is guaranteed on a teammate's machine. The initial portable profile uses
native Torch and the verified queue/fusion mechanisms; TensorRT is a separate target-built option.
Model loading and first-frame warmup remain visible in measured latency.

## Download the complete implementation

Clone the explicitly selected branch:

```bash
git clone --branch mvp-phone-2026-10-29 --single-branch https://github.com/heropig114514/IRS-PM-2026-09-07-AIS08FT-GRP40-GUIDESIGHT-AI.git
cd IRS-PM-2026-09-07-AIS08FT-GRP40-GUIDESIGHT-AI/SystemCode/backend
```

Alternatively select that branch in GitHub and use **Code > Download ZIP**, then open its
`SystemCode/backend` directory. Repository access still requires your GitHub account's permission.
TensorRT export evidence uses Git revision metadata, so use a Git clone for that workflow.
The standard Torch demo and model provisioning do not require Git after extraction.

## Prepare, check and start

Windows, from `backend`:

```powershell
py -3.12 tools/local_demo.py prepare
py -3.12 tools/local_demo.py check
py -3.12 tools/local_demo.py run
```

Linux uses the same arguments with `python3.12`:

```bash
python3.12 tools/local_demo.py prepare
python3.12 tools/local_demo.py check
python3.12 tools/local_demo.py run
```

Preparation creates/reuses only `backend/.venv` (Web) and `backend/.venv-inference` (GPU), installs
pinned direct requirements, checks package consistency, and explicitly downloads the two official
checkpoints when missing. Complete SHA256 checks precede publication and model loading. Existing
different weights or unrelated environment directories fail without replacement. Downloads and
package installs have no automatic retry; correct the reported condition and rerun explicitly.
Use your own permitted network/proxy configuration if GitHub or package indexes are unreachable;
no author-specific proxy, mirror or server credential is embedded in the bootstrap.
No credentials, global Python packages, GPU drivers or compiled engines are installed by inference
startup. To select the other wheel build during preparation, add `--cuda cu126`.

The original weights are defined in `deploy/local-demo.toml`; `models` runs only provisioning:

```powershell
py -3.12 tools/local_demo.py models
```

If downloads are unavailable, obtain the exact official files from the sources in that manifest,
place them under `backend/models/`, then run `models` to verify their hashes without downloading.
Fine-tuned replacements require updated identities and separate accuracy/export validation.

`check` is read-only: Python/package pins, usable CUDA, checkpoint identities and loopback ports.
It does not install anything, generate secrets, load models, send inference frames or certify
TensorRT numerical accuracy. `run` reuses the existing foreground supervisor; keep that terminal
open and use Ctrl+C to stop its owned Web/GPU children. It does not stop unrelated processes or
move to another occupied port automatically. Per-process logs are under `.venv/tmp/local-demo/`.
An independent private Django key is generated locally on first startup and is ignored by Git.

Open [the product camera page](http://127.0.0.1:8766/maps/) or
[the diagnostic page](http://127.0.0.1:8766/maps/test). These URLs work on the computer hosting the
service. Select the appropriate camera on the diagnostic page if a desktop has no rear camera.
The default session is continuous, with an explicit stop; model objects are reused between frames.

Local inference needs no cloud credentials. This convenience entry clears inherited HF, ORS,
DashScope and agent API keys for reproducible perception testing. Maps, route planning and cloud
assistant/speech features are separate integrations and may be unavailable; online map assets
still require connectivity. Use the existing explicitly configured supervisor in
[LOCAL-MVP.md](LOCAL-MVP.md) when testing those external features.

## Explicit comparison profiles

The selected settings are printed at startup. All profiles retain original YOLO26n, FP32,
same-frame depth mode A/interval1, depth edge768, max640/JPEG0.8 and service deadlines.
The profile clears inherited distance/depth overrides so a stale shell variable cannot silently
change the comparison. Select `--detector yolo26n-walk` explicitly for the independent87-class model;
compare it only against runs with the same selected detector.

| Profile | Depth implementation | Depth reduction | Live-camera window |
| --- | --- | --- | --- |
| `baseline` | Torch | GPU | 12 |
| `low-latency` (visible default) | Torch | Host | 2 |
| `tensorrt` | Target-built TensorRT FP32 | Host | 2 |

All live-camera profiles include send-buffer admission before capture and separate queue-wait
diagnostics. `baseline` controls the window/fusion settings; it does not disable that shared
transport feature. See [PHONE-MVP.md](PHONE-MVP.md) for its explicit ungated code-level control.

```powershell
py -3.12 tools/local_demo.py run --profile baseline
# Stop the previous run with Ctrl+C before starting the next comparison.
py -3.12 tools/local_demo.py run --profile low-latency
```

For the separate recorded-video interface:

```powershell
py -3.12 tools/local_demo.py check --mode video
py -3.12 tools/local_demo.py run --mode video
```

Open [video replay](http://127.0.0.1:8766/integration/video/), choose an existing test clip and
select **低延迟演示** in its display-mode control before starting. That recorded-video sampler has
its own explicit three-frame option; camera profiles do not change it. Keep that selection fixed
when comparing model backends. The original12-frame video option remains available.

## Optional TensorRT on each teammate's GPU

Do not copy this author's engines or Python environments. Serialized engines, runtime identity
and input shapes are checked against the actual GPU. First prepare export dependencies explicitly:

```powershell
py -3.12 tools/local_demo.py prepare --with-tensorrt
```

Use the resulting `.venv-inference` interpreter for the export/build/validation commands in
[DEPTH-EXPORT.md](DEPTH-EXPORT.md), substituting it for that guide's historical depth-export
interpreter. Provide representative portrait, landscape and widescreen inputs and a new artifact
directory/report. Preserve the Torch control and inspect numerical differences before activation.
The reference export stack and optional install were validated on Windows; availability and
execution on Linux or another GPU still require that host's checks.

After validation, select its manifest explicitly (example path; create it first):

```powershell
py -3.12 tools/local_demo.py check --profile tensorrt --engine-manifest models/experiments/my-gpu/manifest.json
py -3.12 tools/local_demo.py run --profile tensorrt --engine-manifest models/experiments/my-gpu/manifest.json
```

Missing/stale engines and unsupported shapes are errors; no implicit rebuild, precision reduction
or Torch fallback occurs. `--inference-python /absolute/path/to/prepared/python` is available on
`check`/`run` for an already prepared environment and remains subject to all native preflight checks.

## Phone on the same LAN

Keep `run` in its default continuous assist mode active in terminal1. In terminal2, substitute
your computer's actual private address and subnet; the following is an example, not a discovered
address. The phone and computer must be mutually reachable on the LAN. Guest-Wi-Fi isolation or
host firewall rules can block this path and are not changed automatically.

```powershell
py -3.12 tools/local_demo.py lan --address 192.168.1.10 --subnet 192.168.1.0/24 --state-dir .venv/tmp/lan-demo/session-1 --prepare-certificate
py -3.12 tools/local_demo.py lan --address 192.168.1.10 --subnet 192.168.1.0/24 --state-dir .venv/tmp/lan-demo/session-1 --check
py -3.12 tools/local_demo.py lan --address 192.168.1.10 --subnet 192.168.1.0/24 --state-dir .venv/tmp/lan-demo/session-1
```

The command prints the actual setup/product/test URLs. The built-in private-certificate option
requires the phone user to install and trust its certificate; the setup page explains the steps.
This action cannot be automated by the repository. Use a new state directory for each run;
certificates last seven days and renewal needs a new `--tls-dir`. Phone access is HTTPS8443;
HTTP8080 serves enrollment instructions only. Both Web and inference run locally, with no SSH
tunnel. The wrapper aligns canonical Web port8766 with terminal1; if you change ports, provide
the same `--web-port` and `--inference-port` to both invocations.
LAN selects only the live camera window (`--camera-profile bounded-2` by default); it cannot
change the already-running worker's model/backend. Select those in terminal1 and inspect its
health/returned metadata for the actual identity.

For a no-profile iPhone entry, a domain you control, a trusted certificate and LAN DNS are a
separate deployment prerequisite. This bootstrap does not provision them, copy server TLS keys
or bypass Safari security. Never use `127.0.0.1` as the phone's address for another computer.

## Acceptance and troubleshooting

Record hardware/driver, selected profile, model hashes, source input, dimensions, codec, first
frame, steady P50/P95 age, completed FPS, queue waits and failures. Keep identical model/input
settings; a lower frame age without completion FPS does not establish higher throughput.
The existing browser tools in [LOCAL-MVP.md](LOCAL-MVP.md) and [PHONE-MVP.md](PHONE-MVP.md) provide
full-path measurement. Health/preflight success is not inference or physical-phone acceptance.

| Failure | Required action |
| --- | --- |
| Missing Python3.12 or venv/package | Prepare the stated interpreter and run explicit `prepare` |
| No usable CUDA | Check the NVIDIA device/driver and selected Torch wheel; CPU fallback is unavailable |
| Checkpoint hash mismatch | Preserve the file and obtain the exact versioned checkpoint |
| Occupied port | Stop its actual owner or explicitly select matching alternative ports |
| Cold first frame | Record model load/warmup separately; do not remove it from the evidence |
| Stale TensorRT identity/unknown shape | Re-export and validate on that GPU for the required inputs |
| Phone page/camera unavailable | Check LAN reachability and trusted HTTPS before model tuning |

Updates to this branch contain source/configuration, not private runtime state. Pull updates,
rerun preparation when requirements change, then `check` and your same-input acceptance tests.
