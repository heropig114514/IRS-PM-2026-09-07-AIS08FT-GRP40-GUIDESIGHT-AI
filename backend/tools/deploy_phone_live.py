"""Responsibilities: Stage/publish the deployed phone live frontend and manual vibration UI.
Implementation: Pinned SSH and explicit source allowlists; copy the current release, validate
inference identity and upload hashes before activation. The explicit vibration profile preserves
runtime code, service/proxy configuration and credentials, then restarts only prs-camera.
Related Modules: core.inference_plan; core.camera; frontend/vibration.js; deployment templates.
Declaration Index: PinnedHost; PinnedHost.missing_host_key; connect_server; execute; stage;
wait_ready; activate_vibration; activate; main.
Variable Index: ROOT — private credential/bundle location; PREFIX — dedicated remote workspace;
FINGERPRINT — previously inspected public host identity; logger — non-secret transition logs;
FRONTEND/BACKEND/RETIRED — full-profile source and cleanup sets;
VIBRATION_FRONTEND/VIBRATION_BACKEND — reviewed UI overlay only. source_root identifies a
reviewed project snapshot; git_commit records its Git identity. No retries or automatic rollback.
"""

import argparse
import base64
import hashlib
import io
import json
import logging
import re
import shlex
from pathlib import Path

import paramiko
from dotenv import dotenv_values

from core.inference_plan import read_plan

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "/opt/prs-practice"
FINGERPRINT = "hIrYH0S2vhyiU53ScT9Ei03o1ClcuHAb2pL/VrMMg90="
logger = logging.getLogger(__name__)
VIBRATION_FRONTEND = (
    "index.html", "camera.css", "translations.js", "vibration.js", "README.md",
    "tests/vibration.test.mjs", "tools/check_vibration_browser.mjs",
)
VIBRATION_BACKEND = (
    "core/camera.py", "core/test_echo.py", "core/test_deploy_phone_live.py",
    "tools/deploy_phone_live.py", "deploy/README.md",
)
FRONTEND = (
    "index.html",
    "camera.js",
    "camera.css",
    "live-detection-client.js",
    "frame-pipeline.js",
    "echo-client.js",
    "i18n.js",
    "translations.js",
    "maps.html",
    "maps-page.js",
    "maps-client.js",
    "vibration.js",
    "README.md",
    "tests/camera.test.mjs",
    "tests/i18n.test.mjs",
    "tests/maps-client.test.mjs",
    "tests/live-detection-client.test.mjs",
    "tests/frame-pipeline.test.mjs",
    "tests/vibration.test.mjs",
)
BACKEND = (
    "config/asgi.py",
    "config/urls.py",
    "core/camera.py",
    "core/detector.py",
    "core/hf_video_relay.py",
    "core/inference_plan.py",
    "core/hf_quota.py",
    "core/hf_services.py",
    "realtime/__init__.py",
    "realtime/protocol.py",
    "realtime/detector.py",
    "zerogpu/__init__.py",
    "zerogpu/app.py",
    "zerogpu/entrypoint.py",
    "zerogpu/runtime.py",
    "zerogpu/aoti.py",
    "zerogpu/jit_cache.py",
    "zerogpu/live_transport.py",
    "zerogpu/live_worker.py",
    "zerogpu/README.md",
    "core/test_echo.py",
    "core/test_hf.py",
    "core/test_zerogpu.py",
    "core/test_frames.py",
    "core/test_protocol.py",
    "core/test_hf_live.py",
    "core/test_zerogpu_live.py",
    "core/test_zerogpu_quota.py",
    "requirements-vision.txt",
    "README.md",
    "ZEROGPU.md",
    "PHONE-LIVE.md",
    "ACCELERATION.md",
    "models/README.md",
    "models/yolo26n.json",
    "models/inference-plan.json",
    ".env.example",
    "core/test_maps.py",
    "core/test_deploy_phone_live.py",
    "tools/__init__.py",
    "tools/deploy_space.py",
    "tools/deploy_phone_live.py",
    "tools/compare_detections.py",
    "tools/fixtures.py",
    "deploy/README.md",
)
RETIRED = (
    "frontend/live",
    "frontend/detection-client.js",
    "frontend/stream-client.js",
    "frontend/boxes-client.js",
    "frontend/detection-sample.jpg",
    "frontend/tests/detection-client.test.mjs",
    "frontend/tests/hf-client.test.mjs",
    "frontend/tests/stream-client.test.mjs",
    "frontend/tests/boxes-client.test.mjs",
    "backend/core/vision.py",
    "backend/core/hf_vision.py",
    "backend/core/video.py",
    "backend/core/hf_live.py",
    "backend/core/hf_stream.py",
    "backend/core/test_vision.py",
    "backend/core/test_boxes.py",
    "backend/core/test_gpu_profiling.py",
    "backend/core/test_live.py",
    "backend/core/test_persistent_hf.py",
    "backend/core/test_video_socket.py",
    "backend/zerogpu/protocol.py",
    "backend/zerogpu/streaming.py",
    "backend/zerogpu/experiments.py",
    "backend/zerogpu/boxes.py",
    "backend/zerogpu/live.py",
    "backend/zerogpu/profiling.py",
    "backend/tools/benchmark_transport.py",
    "backend/tools/benchmark_live.py",
    "backend/tools/benchmark_boxes.py",
    "backend/tools/benchmark_gpu_profile.py",
    "backend/tools/benchmark_vision.py",
    "backend/tools/benchmark_space.py",
    "backend/VISION.md",
    "backend/STREAMING.md",
    "backend/PIPELINE.md",
    "backend/LIVE_SESSION.md",
    "backend/GPU_PROFILING.md",
    "backend/PERSISTENT_HF.md",
    "backend/BOXES.md",
)


class PinnedHost(paramiko.MissingHostKeyPolicy):
    """Functionality: Require the previously inspected SSH identity.
    Logic: SHA256 fingerprint match; Constraints: no accept-any host or private key output.
    """

    def missing_host_key(self, client, hostname, key):
        """Functionality: Validate an identity absent from local known_hosts.
        Inputs: client/hostname/server public key; Outputs: accept only exact inspected fingerprint.
        Logic: constant public SHA match; Constraints: mismatches abort before password auth.
        """
        if base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode() != FINGERPRINT:
            raise ValueError("SSH host fingerprint changed")


def connect_server():
    """Functionality: Authenticate using the user's excluded private server file.
    Inputs: server_info.txt and known_hosts; Outputs: authenticated client.
    Logic: validate explicit owned target/root/22, disable agent/key discovery, pin host identity.
    Constraints: password never appears in commands, manifest or diagnostics; no retry.
    Logs only the fixed target and successful connection state.
    """
    fields = dict(
        line.split(":", 1)
        for line in (ROOT / "server_info.txt").read_text(encoding="utf-8").splitlines()
        if ":" in line
    )
    if fields["Server"].strip() != "47.239.50.129" or fields["Port"].strip() != "22":
        raise ValueError("Unexpected deployment target")
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(PinnedHost())
    logger.info("Connecting to inspected PRS deployment target 47.239.50.129:22")
    client.connect(
        "47.239.50.129",
        port=22,
        username="root",
        password=fields["Password"].strip(),
        allow_agent=False,
        look_for_keys=False,
        timeout=12,
    )
    logger.info("PRS deployment SSH connection established")
    return client


def execute(client, script):
    """Functionality: Execute a bounded explicit remote script and retain safe output.
    Inputs: authenticated client and non-secret script; Outputs: stdout or failure.
    Logic: stdin to bash avoids shell interpolation of code; pipefail preserves failures.
    Constraints: scripts never contain credentials/env values, no automatic retries or recovery.
    """
    stdin, stdout, stderr = client.exec_command("bash -s", timeout=60)
    stdin.write("set -euo pipefail\n" + script + "\n")
    stdin.channel.shutdown_write()
    output, error = stdout.read().decode(), stderr.read().decode()
    if stdout.channel.recv_exit_status():
        raise RuntimeError("Remote deployment check failed: " + error[:2000] + output[:2000])
    return output + error


def stage(client, sftp, profile="full", source_root=None, git_commit=None):
    """Functionality: Build a checked live/maps release while preserving unrelated services.
    Inputs: client/sftp, profile(full/vibration), optional project source_root and git_commit;
    current release, measured inference plan and owned source/template allowlists.
    Outputs: safe hash manifest including the measured plan identity.
    Logic: validate the plan, copy current release, prune explicit retired paths in the new release,
    replace canonical routes/source, verify uploaded bytes, Django and systemd before activation.
    Constraints: active/archived releases and map API/business code remain untouched;
    no credentials, database, weights, browser fixtures or environment upload.
    No implicit promotion or rollback; logs release identity and verification transitions.
    Full profile dependencies must be installed explicitly; no implicit pip install.
    Vibration profile verifies the active plan is byte-identical and does not replace runtime,
    service/proxy templates or credentials; no retirement pruning outside the full profile.
    """
    if profile not in {"full", "vibration"}:
        raise ValueError("Unknown deployment profile")
    source = ROOT if source_root is None else Path(source_root).resolve() / "backend"
    plan = read_plan(source / "models/inference-plan.json",
                     json.loads((source / "models/yolo26n.json").read_bytes()))
    old = execute(client, f"readlink -f {PREFIX}/current").strip()
    if not re.fullmatch(re.escape(PREFIX) + r"/releases/[A-Za-z0-9_-]+", old):
        raise ValueError("Unexpected current release path")
    if profile == "vibration":
        for name in ("models/inference-plan.json", "models/yolo26n.json"):
            with sftp.open(old + "/backend/" + name, "rb") as active:
                if active.read() != (source / name).read_bytes():
                    raise ValueError("Vibration deployment changes the active inference identity")
    frontend = VIBRATION_FRONTEND if profile == "vibration" else FRONTEND
    backend = VIBRATION_BACKEND if profile == "vibration" else BACKEND
    retired = () if profile == "vibration" else RETIRED
    files = {"backend/" + name: (source / name).read_bytes() for name in backend}
    files.update(
        {"frontend/" + name: (source.parent / "frontend" / name).read_bytes() for name in frontend}
    )
    if profile == "full":
        for name in ("prs-camera.service", "nginx.locations.conf"):
            files["backend/deploy/" + name] = (source / "deploy" / name).read_bytes()
    hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}
    identity = {"files": hashes, "removed": retired}
    release = (
        ("vibration-" if profile == "vibration" else "phone-live-")
        + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:16]
    )
    target = PREFIX + "/releases/" + release
    logger.info("Staging release=%s previous=%s files=%s", release, old, len(files))
    manifest = {
        "release": release,
        "path": target,
        "previous": old,
        "files": hashes,
        "removed": list(retired),
        "scope": ("Manual vibration UI; preserve inference, service/proxy config and credentials"
                  if profile == "vibration" else
                  "Echo, latest HF live and bilingual maps; root redirects"),
        "profile": profile,
        "git_commit": git_commit,
        "inference": {"backend": plan["backend"], "precision": plan["precision"],
                      "provenance": plan["provenance"]},
    }
    local = ROOT / ".space-bundle/phone-live" / release
    for name, raw in files.items():
        path = local / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    execute(
        client, f"test ! -e {shlex.quote(target)}\ncp -a {shlex.quote(old)} {shlex.quote(target)}"
    )
    # Functionality: Remove reviewed obsolete source only in this new inactive release.
    # Logic: resolve every file, including directory descendants, against target before unlink.
    # Constraints: symlinks escaping the target abort; old releases never enter this operation.
    prune = (
        "from pathlib import Path\nimport json\n"
        + (f"root=Path({target!r}).resolve()\nnames=json.loads({json.dumps(retired)!r})\n")
        + """
for name in names:
    candidate = root / name
    if not candidate.resolve().is_relative_to(root):
        raise ValueError('Retired path escapes staged release')
    if not candidate.exists():
        continue
    entries = (sorted(candidate.rglob('*'), key=lambda p: len(p.parts), reverse=True)
               if candidate.is_dir() else [])
    for item in [*entries, candidate]:
        if not item.resolve().is_relative_to(root):
            raise ValueError('Retired descendant escapes staged release')
        if item.is_symlink() or item.is_file():
            item.unlink()
        elif item.is_dir():
            item.rmdir()
print('Retired source removed from inactive release')
"""
    )
    if retired:
        execute(client, "python3 - <<'PY'\n" + prune + "\nPY")
    for name, raw in files.items():
        remote = target + "/" + name
        execute(client, "mkdir -p " + shlex.quote(str(Path(remote).parent).replace("\\", "/")))
        sftp.putfo(io.BytesIO(raw), remote)
        sftp.chmod(remote, 0o644)
        if hashlib.sha256(sftp.open(remote).read()).hexdigest() != hashes[name]:
            raise ValueError("Uploaded file hash mismatch: " + name)
    logger.info("Upload hashes verified release=%s; running remote checks", release)
    manifest["remote_checks"] = execute(
        client,
        f"""
set -a
source /etc/prs-camera/app.env
set +a
export PYTHONDONTWRITEBYTECODE=1
cd {shlex.quote(target + "/backend")}
{PREFIX}/runtime-venv/bin/python manage.py check
{PREFIX}/runtime-venv/bin/python manage.py makemigrations --check --dry-run
systemd-analyze verify deploy/prs-camera.service
""",
    )
    (local / "deployment-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    with sftp.open(target + "/phone-live-manifest.json", "wb") as output:
        output.write(json.dumps(manifest, indent=2).encode())
    logger.info("Release staged and checked, pending activation: %s", release)
    return manifest


def wait_ready(client):
    """Functionality: Wait for the single restarted ASGI listener then check health exactly once.
    Inputs: existing SSH/client and dedicated loopback8766; Outputs: readiness/health diagnostics.
    Logic: bounded20s socket readiness observation, then one HTTP health request with proxy headers.
    Constraints: no API/GPU retry or restart; an active service may still be starting Django.
    """
    return execute(
        client,
        """
/opt/prs-practice/runtime-venv/bin/python - <<'PY'
import socket, time
deadline = time.monotonic() + 20
while time.monotonic() < deadline:
    with socket.socket() as probe:
        probe.settimeout(0.2)
        if probe.connect_ex(('127.0.0.1', 8766)) == 0:
            print('ASGI listener ready')
            break
    time.sleep(0.1)
else:
    raise TimeoutError('ASGI listener was not ready within20s')
PY
curl --fail --silent --show-error --max-time 10 \\
  -H 'Host: 47.239.50.129' -H 'X-Forwarded-Proto: https' http://127.0.0.1:8766/api/health/
""",
    )


def activate_vibration(client, manifest):
    """Functionality: Activate the checked UI overlay without changing configuration or tokens.
    Inputs: client and precondition-verified vibration manifest with path/release/previous.
    Outputs: activated flag and readiness evidence; old release remains available.
    Logic: atomically switch current, restart only prs-camera for its Python asset whitelist,
    then perform the existing bounded listener/health check once.
    Constraints: No nginx reload, migrations, GPU calls, credential writes or automatic rollback;
    failures propagate and the manifest is never marked activated before readiness succeeds.
    """
    target, release = manifest["path"], manifest["release"]
    logger.info("Activating vibration UI release=%s; preserving runtime configuration", release)
    manifest["activation_checks"] = execute(
        client,
        f"ln -s {shlex.quote(target)} {shlex.quote(PREFIX + '/current-' + release)}\n"
        f"mv -Tf {shlex.quote(PREFIX + '/current-' + release)} {PREFIX}/current\n"
        "systemctl restart prs-camera\nsystemctl is-active prs-camera",
    )
    manifest["activation_checks"] += wait_ready(client)
    manifest["activated"] = True
    logger.info("Vibration UI activated release=%s; previous release retained", release)


def activate(client, sftp, manifest):
    """Functionality: Publish one staged, checked minimal-mode release and preserve recovery
    material.
    Inputs: client/sftp/manifest; full profile also reads PRS configs and private HF token.
    Outputs: activation info.
    Logic: check old-current match, backup unit/include/env, update token through SFTP mode0600,
    validate nginx, atomic current link, restart only PRS, check readiness, graceful nginx reload.
    Constraints: no migration, other application restart, auto rollback or GPU call;
    logs activation/readiness transitions without credential values.
    """
    target, previous, release = manifest["path"], manifest["previous"], manifest["release"]
    if execute(client, f"readlink -f {PREFIX}/current").strip() != previous:
        raise ValueError("Current release changed since staging")
    for name, expected in manifest["files"].items():
        if hashlib.sha256(sftp.open(target + "/" + name).read()).hexdigest() != expected:
            raise ValueError("Staged release changed")
    logger.info("Activation preconditions verified release=%s", release)
    if manifest.get("profile", "full") == "vibration":
        activate_vibration(client, manifest)
        return
    backup = "/etc/prs-camera/backups/" + release
    backup_unit = shlex.quote(backup + "/prs-camera.service")
    backup_include = shlex.quote(backup + "/nginx.locations.conf")
    execute(
        client,
        f"mkdir -m 700 -p {shlex.quote(backup)}\n"
        f"cp -a /etc/prs-camera/app.env {shlex.quote(backup + '/app.env')}\n"
        f"cp -a /etc/systemd/system/prs-camera.service {backup_unit}\n"
        f"cp -a /etc/nginx/prs-camera.locations.conf {backup_include}",
    )
    token = dotenv_values(ROOT / ".env.hf").get("HF_TOKEN", "")
    if not re.fullmatch(r"hf_[A-Za-z0-9]+", token):
        raise ValueError("Configure the authorized private HF token")
    env = sftp.open("/etc/prs-camera/app.env").read().decode()
    lines = [
        line
        for line in env.splitlines()
        if not line.startswith(("HF_INFERENCE_TOKEN=", "YOLO_MODEL_PATH="))
    ]
    with sftp.open("/etc/prs-camera/app.env.phone-next", "wb") as output:
        output.write(("\n".join(lines) + "\nHF_INFERENCE_TOKEN=" + token + "\n").encode())
    sftp.chmod("/etc/prs-camera/app.env.phone-next", 0o600)
    logger.info("Activating release=%s; restarting only prs-camera", release)
    manifest["activation_checks"] = execute(
        client,
        f"""
prs_unit_source={shlex.quote(target + "/backend/deploy/prs-camera.service")}
prs_nginx_source={shlex.quote(target + "/backend/deploy/nginx.locations.conf")}
install -m 644 "$prs_unit_source" /etc/systemd/system/prs-camera.service
install -m 644 "$prs_nginx_source" /etc/nginx/prs-camera.locations.conf
nginx -t
mv /etc/prs-camera/app.env.phone-next /etc/prs-camera/app.env
systemctl daemon-reload
ln -s {shlex.quote(target)} {shlex.quote(PREFIX + "/current-" + release)}
mv -Tf {shlex.quote(PREFIX + "/current-" + release)} {PREFIX}/current
systemctl restart prs-camera
systemctl is-active prs-camera
""",
    )
    manifest["activation_checks"] += wait_ready(client)
    logger.info("PRS listener and health verified release=%s", release)
    manifest["activation_checks"] += execute(client, "systemctl reload nginx")
    manifest["activated"] = True
    manifest["backup"] = backup
    logger.info("Release activated=%s; previous release/config backups retained", release)


def main():
    """Functionality: Stage first or activate the explicit reviewed manifest.
    Inputs: --stage/--activate, --manifest, --profile, optional --source-root and --git-commit.
    Outputs: non-secret manifest/exit failure.
    Logic: configure transition logging; operations remain separate, never implicit promotion.
    Constraints: credentials stay private; exceptions preserve a failing exit status.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--stage", action="store_true")
    action.add_argument("--activate", action="store_true")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--profile", choices=("full", "vibration"), default="full")
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--git-commit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    client = connect_server()
    try:
        with client.open_sftp() as sftp:
            manifest = (stage(client, sftp, args.profile, args.source_root, args.git_commit)
                        if args.stage else json.loads(args.manifest.read_text()))
            if args.activate:
                activate(client, sftp, manifest)
            args.manifest.parent.mkdir(parents=True, exist_ok=True)
            args.manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({key: value for key, value in manifest.items() if key != "files"}))
    finally:
        client.close()


if __name__ == "__main__":
    main()
