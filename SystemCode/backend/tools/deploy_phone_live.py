"""Responsibilities: Stage/publish Echo, the latest phone live path, bilingual maps, MCP
and manual vibration/speech UI with shared walking navigation and voice-agent dependencies.
Implementation: Pinned SSH, private local credentials, explicit source allowlist over the
current release; validate the full-COCO qualified plan before staging;
prune retired modes and replace owned page/asset/WS routes, including maps and shared language
modules, AI control and manual vibration assets as one release; verify preinstalled MCP
dependencies, record hashes, back up and switch
one service, then check readiness.
An explicit vibration profile overlays only reviewed UI assets and their serving whitelist,
requiring the same inference plan and preserving active service/proxy/credential configuration.
The live-tuning profile overlays only capture/transport scheduling and its tests, requires an
explicit runtime, and preserves the current model, device outputs and service configuration.
The navigation profile overlays map guidance and its shared MCP/static dependencies, using an
explicit current runtime; preserves camera/inference sources, routes, templates and credentials.
Navigation/outputs require the active shared nested asset route before staging any files.
Related Modules: hf_video_relay/inference_plan/ai_mcp/ai_bridge; realtime.depth/tracking/risk;
frontend live pipeline; core.agent and agent_skills Markdown/JSON intent catalog, routing prompts
and tool scopes (full releases only);
deploy templates;
private server_info.txt.
Declaration Index: PinnedHost — SSH policy; PinnedHost.missing_host_key — fingerprint check;
connect_server — private authentication; execute — checked remote command;
require_shared_asset_route — read-only compatibility contract for scoped navigation/output staging;
wait_ready — listener/health observation; stage — hash-verified inactive release;
activate_overlay — configuration-preserving vibration, live-tuning or navigation release;
activate_outputs — isolated-runtime device-output deployment;
load_agent_environment — explicitly select and validate private voice/VLM settings;
merge_agent_environment — preserve unrelated private environment bytes;
activate — publish reviewed release; main — explicit stage/activate CLI.
Variable Index: ROOT — local backend source under SystemCode/backend; PREFIX — dedicated
remote workspace; FINGERPRINT — inspected SSH host key; logger — deployment transition diagnostics
without secrets;
FRONTEND — explicit asset set; BACKEND — owned runtime set; RETIRED — explicit
obsolete paths. No credential output.
VIBRATION_FRONTEND/VIBRATION_BACKEND — narrow UI-profile overlay; source_root is a reviewed
repository root containing SystemCode/backend and SystemCode/frontend, and git_commit
records its Git identity without reading private files there.
OUTPUT_FRONTEND/OUTPUT_BACKEND — output/navigation UI/MCP overlay preserving inference code;
LIVE_FRONTEND/LIVE_BACKEND — coordinated browser/PRS live transport overlay, excluding GPU compute;
NAVIGATION_FRONTEND/NAVIGATION_BACKEND — map/MCP files, preserving camera/inference sources;
runtime_path — explicit interpreter root under the owned workspace, recorded in the manifest;
shared_asset_route_sha256 — verified active route identity for navigation/output prerequisites.
Requires local Paramiko/python-dotenv. No automatic rollback, retry, GPU call or hardware change.
AGENT_ENV_FIELDS — permitted voice/VLM/search-scope settings;
mcp_profile — explicit public registry.
"""

import argparse
import ast
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
AGENT_ENV_FIELDS = (
    "PRS_AGENT_API_KEY", "PRS_AGENT_BASE_URL", "PRS_AGENT_MODEL", "PRS_ASR_MODEL",
    "DASHSCOPE_API_KEY", "DASHSCOPE_HOST", "VLM_MODEL",
    "PRS_AGENT_SEARCH_COUNTRY", "PRS_AGENT_SEARCH_LANGUAGE",
)
NAVIGATION_FRONTEND = (
    "maps.html", "maps-page.js", "maps-client.js", "navigation.js", "navigation-ui.js",
    "ai-control.js", "i18n.js", "translations.js", "vibration.js", "speech-output.js",
    # Shared asset whitelist also names this inert module; no scene UI or HTTP route is enabled.
    "scene-client.js", "README.md", "tests/navigation.test.mjs", "tests/maps-client.test.mjs",
    "tools/check_maps_browser.mjs", "tools/check_navigation_browser.mjs",
    "integration/maps-navigation-speech.js", "integration/maps-navigation-bridge.js",
    "integration/maps-navigation-speech.css", "integration/navigation-speech.js",
    "integration/events.js", "integration/catalog.js",
    "tests/integration-maps-navigation-bridge.test.mjs",
    "tools/check_maps_navigation_speech_browser.mjs",
    "tools/check_local_navigation_browser.mjs",
)
NAVIGATION_BACKEND = (
    "core/camera.py", "core/ai_mcp.py", "core/test_ai_control.py", "core/test_mcp_profile.py",
    "core/test_maps.py", "core/test_deploy_phone_live.py", "tools/deploy_phone_live.py",
    "MCP.md", "deploy/README.md",
)
LIVE_FRONTEND = (
    "camera.js", "frame-pipeline.js", "live-detection-client.js",
    "tests/frame-pipeline.test.mjs", "tests/live-detection-client.test.mjs",
)
LIVE_BACKEND = (
    "core/hf_video_relay.py", "zerogpu/live_transport.py",
    "core/test_hf_live.py", "core/test_zerogpu_live.py",
    "tools/deploy_phone_live.py", "core/test_deploy_phone_live.py",
)
VIBRATION_FRONTEND = (
    "index.html", "camera.css", "translations.js", "vibration.js", "README.md",
    "tests/vibration.test.mjs", "tools/check_vibration_browser.mjs",
)
VIBRATION_BACKEND = (
    "core/camera.py", "core/test_echo.py", "core/test_deploy_phone_live.py",
    "tools/deploy_phone_live.py", "deploy/README.md",
)
OUTPUT_FRONTEND = (
    "index.html", "camera.css", "i18n.js", "translations.js", "vibration.js", "speech-output.js",
    "ai-control.js", "README.md", "tests/vibration.test.mjs", "tests/speech-output.test.mjs",
    "maps.html", "maps-page.js", "maps-client.js", "navigation.js", "navigation-ui.js",
    "tests/navigation.test.mjs",
    "tools/check_vibration_browser.mjs", "tools/check_vibration_mcp_browser.mjs",
    "tools/check_speech_browser.mjs",
    "integration/maps-navigation-speech.js", "integration/maps-navigation-bridge.js",
    "integration/maps-navigation-speech.css", "integration/navigation-speech.js",
    "integration/events.js", "integration/catalog.js",
    "tests/integration-maps-navigation-bridge.test.mjs",
    "tools/check_maps_navigation_speech_browser.mjs",
    "tools/check_local_navigation_browser.mjs",
)
OUTPUT_BACKEND = (
    "config/asgi.py", "core/camera.py", "core/ai_bridge.py", "core/ai_database.py",
    "core/ai_mcp.py",
    "core/test_ai_control.py", "core/test_mcp_profile.py", "core/test_echo.py",
    "core/test_deploy_phone_live.py", "requirements.txt", "MCP.md", "deploy/README.md",
    "tools/deploy_phone_live.py", "tools/check_mcp.py", "tools/check_vibration_mcp.py",
    "tools/check_speech_mcp.py",
)
FRONTEND = (
    "agent-client.js", "agent-device.js", "agent.css", "voice-input.js", "voice-worklet.js",
    "tests/agent.test.mjs", "tools/check_agent_browser.mjs", "tools/check_agent_extended.mjs",
    "tools/check_agent_location.mjs",
    "tools/check_agent_discovery.mjs",
    "tools/check_agent_intents.mjs",
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
    "navigation.js",
    "navigation-ui.js",
    "ai-control.js",
    "vibration.js",
    "speech-output.js",
    "scene-client.js",
    "README.md",
    "tests/camera.test.mjs",
    "tests/i18n.test.mjs",
    "tests/maps-client.test.mjs",
    "tests/navigation.test.mjs",
    "tests/live-detection-client.test.mjs",
    "tests/frame-pipeline.test.mjs",
    "tests/vibration.test.mjs",
    "tests/speech-output.test.mjs",
    "tests/scene-client.test.mjs",
    "integration/maps-navigation-speech.js", "integration/maps-navigation-bridge.js",
    "integration/maps-navigation-speech.css", "integration/navigation-speech.js",
    "integration/events.js", "integration/catalog.js",
    "tests/integration-maps-navigation-bridge.test.mjs",
    "tools/check_maps_navigation_speech_browser.mjs",
    "tools/check_local_navigation_browser.mjs",
)
BACKEND = (
    "core/agent/__init__.py", "core/agent/provider.py", "core/agent/skills.py",
    "core/agent/intents.py", "core/test_agent_intents.py",
    "core/agent/place_resolution.py", "core/test_place_resolution.py",
    "core/agent/selection.py",
    "core/agent/places.py", "core/agent/poi.py", "core/agent/tools.py", "core/agent/runtime.py",
    "core/agent/transport.py", "core/agent/tts.py", "core/test_agent.py",
    "core/test_agent_discovery.py",
    "core/test_agent_tts.py",
    "agent_skills/system.md", "agent_skills/navigation/SKILL.md",
    "agent_skills/navigation/tools.json", "agent_skills/camera/tools.json",
    "agent_skills/outputs/tools.json",
    "agent_skills/intents.json", "agent_skills/intent_router.md",
    "agent_skills/general_assistant.md",
    "agent_skills/camera/SKILL.md", "agent_skills/outputs/SKILL.md", "AGENT.md",
    "config/asgi.py",
    "config/urls.py",
    "core/camera.py",
    "core/maps.py",
    "core/scene.py",
    "core/vlm.py",
    "core/test_scene.py",
    "core/ai_bridge.py",
    "core/ai_database.py",
    "core/ai_mcp.py",
    "core/test_ai_control.py",
    "core/detector.py",
    "core/hf_video_relay.py",
    "core/inference_plan.py",
    "core/hf_quota.py",
    "core/hf_services.py",
    "realtime/__init__.py",
    "realtime/protocol.py",
    "realtime/detector.py",
    "realtime/depth.py",
    "realtime/distance.py", "realtime/distance_config.py", "realtime/performance.py",
    "realtime/tracking.py",
    "realtime/risk.py",
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
    "core/test_depth.py",
    "core/test_protocol.py",
    "core/test_hf_live.py",
    "core/test_zerogpu_live.py",
    "core/test_zerogpu_quota.py",
    "requirements-vision.txt",
    "requirements-depth.txt",
    "requirements.txt",
    "MCP.md",
    "README.md",
    "ZEROGPU.md",
    "PHONE-LIVE.md",
    "DEPTH.md",
    "TRACKING.md",
    "ACCELERATION.md",
    "VLM.md",
    "models/README.md",
    "models/yolo26n.json",
    "models/inference-plan.json",
    "models/distance-config.json", "DISTANCE-MODES.md", "core/test_distance_modes.py",
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
    "backend/agent_skills/navigation/references/place-knowledge.md",
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


def require_shared_asset_route(raw):
    """Functionality: Verify an active route file can serve the nested navigation speech assets.
    Inputs: Read-only bytes from the active backend/config/urls.py. Outputs: Verified source SHA256.
    Logic: Parse bounded UTF-8 source without executing it and require the explicit shared path
    converter to target camera_asset; comments and the old single-segment route do not qualify.
    Constraints: No route patching, code execution or fallback; scoped publication requires an
    explicit prior shared-asset-route upgrade when the active release lacks this contract.
    """
    message = (
        "Scoped navigation/output deployment requires existing shared assets/<path:name>; "
        "upgrade the shared asset route explicitly before publishing this scope"
    )
    try:
        if not isinstance(raw, bytes) or len(raw) > 262144:
            raise ValueError(message)
        tree = ast.parse(raw.decode("utf-8"))
        routes = [
            value for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "urlpatterns"
                    for target in node.targets)
            and isinstance(node.value, (ast.List, ast.Tuple))
            for value in node.value.elts
        ]
        compatible = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name) and node.func.id == "path"
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "assets/<path:name>"
            and isinstance(node.args[1], ast.Name) and node.args[1].id == "camera_asset"
            for node in routes
        )
    except (SyntaxError, UnicodeDecodeError, ValueError) as error:
        raise ValueError(message) from error
    if not compatible:
        raise ValueError(message)
    return hashlib.sha256(raw).hexdigest()


def stage(client, sftp, profile="full", source_root=None, git_commit=None, runtime_path=None,
          mcp_profile=None):
    """Functionality: Build a checked live/maps/MCP release while preserving unrelated services.
    Inputs: client/sftp, profile(full/vibration/outputs/live-tuning/navigation),
    repository-level source_root/git_commit, runtime_path and optional full-release mcp_profile;
    current release, measured inference plan and owned source/template allowlists.
    Outputs: safe hash manifest including the measured plan identity.
    Logic: validate the plan, copy current release, prune explicit retired paths in the new release,
    replace canonical routes/source, verify uploaded bytes, preinstalled dependencies,
    Django/ASGI imports and systemd before activation.
    Constraints: active/archived releases and server map API contracts remain untouched;
    no credentials, database, weights, browser fixtures or environment upload.
    No implicit promotion or rollback; logs release identity and verification transitions.
    Full profile dependencies must be installed explicitly; no implicit pip install.
    Agent-enabled HTML requires full publication; legacy UI overlays must use an explicit
    pre-agent snapshot so their narrow manifests cannot produce missing runtime/assets.
    Vibration profile verifies the active plan is byte-identical and does not replace runtime,
    service/proxy templates or credentials; no retirement pruning outside the full profile.
    Outputs includes shared map/navigation UI and requires an isolated runtime; preserves inference;
    service rendering substitutes only the reviewed interpreter path and records its source hash.
    Live-tuning requires an explicit existing runtime; it replaces only scheduling/transport
    and tests, with no service template, registry setting, model or GPU implementation changes.
    Navigation similarly requires the existing runtime; it ships map/MCP dependencies and checks
    the selected registry, leaving camera, server routing, inference and service config untouched.
    Explicit source_root values are repository roots; this module resolves source files only from
    SystemCode/backend and SystemCode/frontend, with no legacy root-level layout branch.
    Navigation/outputs verify the active shared nested-asset route before local or remote staging;
    incompatible routes require a separate explicit upgrade and are never patched by this scope.
    """
    if profile not in {"full", "vibration", "outputs", "live-tuning", "navigation"}:
        raise ValueError("Unknown deployment profile")
    if mcp_profile is not None and (profile != "full" or mcp_profile not in {"outputs", "full"}):
        raise ValueError("Explicit MCP profile requires a full release and full/outputs value")
    if profile == "live-tuning" and runtime_path is None:
        raise ValueError("Live-tuning profile requires an explicit existing runtime")
    if profile == "navigation" and runtime_path is None:
        raise ValueError("Navigation profile requires an explicit existing runtime")
    if profile == "outputs" and runtime_path is None:
        raise ValueError("Outputs profile requires an explicit isolated runtime")
    runtime = runtime_path or PREFIX + "/runtime-venv"
    if runtime != PREFIX + "/runtime-venv" and not re.fullmatch(
        re.escape(PREFIX) + r"/runtimes/[A-Za-z0-9_-]+", runtime
    ):
        raise ValueError("Unexpected runtime path")
    if profile == "outputs" and runtime == PREFIX + "/runtime-venv":
        raise ValueError("Outputs profile requires an isolated runtime under owned runtimes")
    source = (ROOT if source_root is None else
              Path(source_root).resolve() / "SystemCode" / "backend")
    # Functionality: Reject incomplete legacy overlays of agent-enabled sources.
    # Logic: New HTML/ASGI imports need the complete runtime and skill bundle.
    # Constraints: Explicit old snapshots remain usable; never silently widen an overlay.
    if profile in {"vibration", "outputs", "navigation"}:
        page = source.parent / "frontend" / "index.html"
        if page.exists() and "agent-client.js" in page.read_text(encoding="utf-8"):
            raise ValueError("Agent-enabled sources require a full release; legacy overlays "
                             "must use an explicit pre-agent source snapshot")
    plan = read_plan(source / "models/inference-plan.json",
                     json.loads((source / "models/yolo26n.json").read_bytes()))
    old = execute(client, f"readlink -f {PREFIX}/current").strip()
    if not re.fullmatch(re.escape(PREFIX) + r"/releases/[A-Za-z0-9_-]+", old):
        raise ValueError("Unexpected current release path")
    if profile in {"vibration", "outputs", "live-tuning", "navigation"}:
        for name in ("models/inference-plan.json", "models/yolo26n.json"):
            with sftp.open(old + "/backend/" + name, "rb") as active:
                if active.read() != (source / name).read_bytes():
                    raise ValueError(
                        "Scoped deployment changes the active inference identity: " + name
                    )
    shared_asset_route_sha256 = None
    if profile in {"navigation", "outputs"}:
        try:
            with sftp.open(old + "/backend/config/urls.py", "rb") as active:
                shared_asset_route_sha256 = require_shared_asset_route(active.read(262145))
        except OSError as error:
            raise ValueError(
                "Cannot verify active shared asset route; "
                "upgrade it explicitly before scoped publication"
            ) from error
    frontend = {"full": FRONTEND, "vibration": VIBRATION_FRONTEND,
                "outputs": OUTPUT_FRONTEND, "live-tuning": LIVE_FRONTEND,
                "navigation": NAVIGATION_FRONTEND}[profile]
    backend = {"full": BACKEND, "vibration": VIBRATION_BACKEND,
               "outputs": OUTPUT_BACKEND, "live-tuning": LIVE_BACKEND,
               "navigation": NAVIGATION_BACKEND}[profile]
    retired = RETIRED if profile == "full" else ()
    files = {"backend/" + name: (source / name).read_bytes() for name in backend}
    files.update(
        {"frontend/" + name: (source.parent / "frontend" / name).read_bytes() for name in frontend}
    )
    generated = {}
    if profile in {"full", "outputs"}:
        for name in ("prs-camera.service", "nginx.locations.conf"):
            files["backend/deploy/" + name] = (source / "deploy" / name).read_bytes()
        service_name = "backend/deploy/prs-camera.service"
        original = files[service_name]
        marker = (PREFIX + "/runtime-venv/bin/python").encode()
        if original.count(marker) != 1:
            raise ValueError("Service template must declare exactly one reviewed interpreter")
        rendered = original.replace(marker, (runtime + "/bin/python").encode())
        files[service_name] = rendered
        if rendered != original:
            generated[service_name] = {"template_sha256": hashlib.sha256(original).hexdigest(),
                                       "runtime": runtime}
    hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}
    identity = {"files": hashes, "removed": retired}
    release = (
        {"full": "phone-live-", "vibration": "vibration-", "outputs": "outputs-",
         "live-tuning": "live-tuning-", "navigation": "navigation-"}[profile]
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
        "scope": ("Live scheduling/transport; preserve compute, device outputs and configuration"
                  if profile == "live-tuning" else
                  "Walking navigation/MCP; preserve camera, inference, routes and configuration"
                  if profile == "navigation" else
                  "Device output/navigation UI/MCP; preserve inference; explicit isolated runtime"
                  if profile == "outputs" else
                  "Manual vibration UI; preserve inference, service/proxy config and credentials"
                  if profile == "vibration" else
                  "Echo, latest HF live, bilingual maps and MCP control; root redirects"),
        "profile": profile,
        "mcp_profile": (mcp_profile or ("outputs" if profile == "outputs" else "full"))
                       if profile in {"full", "outputs"} else None,
        "runtime": runtime,
        "generated": generated,
        "git_commit": git_commit,
        "shared_asset_route_sha256": shared_asset_route_sha256,
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
    registry_profile = manifest["mcp_profile"]
    profile_export = (f"export PRS_MCP_PROFILE={registry_profile}\n"
                      if profile in {"full", "outputs"} else "")
    registry_check = (
        f"{runtime}/bin/python -c \"import asyncio; from core.ai_mcp import mcp, MCP_PROFILE, "
        "OUTPUT_TOOL_NAMES; assert MCP_PROFILE == 'outputs'; "
        "assert {tool.name for tool in asyncio.run(mcp.list_tools())} == OUTPUT_TOOL_NAMES; "
        "print('Device output/navigation MCP registry verified')\"\n"
    ) if profile in {"full", "outputs"} and registry_profile == "outputs" else ""
    if profile == "navigation":
        registry_check = (
            f"{runtime}/bin/python -c \"import asyncio; from core.ai_mcp import mcp; "
            "assert {'navigation_start','navigation_stop','navigation_status'} <= "
            "{tool.name for tool in asyncio.run(mcp.list_tools())}; "
            "print('Navigation MCP registry verified')\"\n"
        )
    manifest["remote_checks"] = execute(
        client,
        f"""
set -a
source /etc/prs-camera/app.env
set +a
{profile_export}
export PYTHONDONTWRITEBYTECODE=1
cd {shlex.quote(target + "/backend")}
{runtime}/bin/python manage.py check
{runtime}/bin/python manage.py makemigrations --check --dry-run
{runtime}/bin/python -m pip check
{runtime}/bin/python -c "import config.asgi"
{registry_check}
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


def activate_overlay(client, manifest):
    """Functionality: Activate a checked source overlay without changing configuration or tokens.
    Inputs: client and verified vibration/live-tuning/navigation manifest;
    manifest supplies path/release/previous.
    Outputs: activated flag and readiness evidence; old release remains available.
    Logic: atomically switch current, restart only prs-camera to load the changed sources,
    then perform the existing bounded listener/health check once.
    Constraints: No nginx reload, migrations, GPU calls, credential writes or automatic rollback;
    failures propagate and the manifest is never marked activated before readiness succeeds.
    """
    target, release = manifest["path"], manifest["release"]
    logger.info("Activating source overlay release=%s; preserving runtime configuration", release)
    manifest["activation_checks"] = execute(
        client,
        f"ln -s {shlex.quote(target)} {shlex.quote(PREFIX + '/current-' + release)}\n"
        f"mv -Tf {shlex.quote(PREFIX + '/current-' + release)} {PREFIX}/current\n"
        "systemctl restart prs-camera\nsystemctl is-active prs-camera",
    )
    manifest["activation_checks"] += wait_ready(client)
    manifest["activated"] = True
    logger.info("Source overlay activated release=%s; previous release retained", release)


def load_agent_environment(path):
    """Functionality: Read explicitly selected private voice/VLM and place-scope configuration.
    Inputs: local dotenv path; Outputs: whitelisted nonempty settings held only in memory.
    Logic: Require complete voice config, fixed regional endpoint and valid model identifiers.
    Constraints: No unrelated keys, newline injection, credential logs or model/region fallback.
    """
    source = dotenv_values(path)
    values = {name: source[name] for name in AGENT_ENV_FIELDS if source.get(name)}
    if any(name not in values for name in AGENT_ENV_FIELDS[:4]):
        raise ValueError("Explicit agent environment must contain all four voice settings")
    if any(any(char in value for char in "\r\n\0") for value in values.values()):
        raise ValueError("Private environment contains a forbidden control character")
    if values["PRS_AGENT_BASE_URL"] not in {
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    }:
        raise ValueError("Agent region must be an explicitly supported HTTPS endpoint")
    for name in ("PRS_AGENT_MODEL", "PRS_ASR_MODEL", "VLM_MODEL"):
        if name in values and not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", values[name]):
            raise ValueError("Invalid model identifier in private environment")
    if "PRS_AGENT_SEARCH_COUNTRY" in values and not re.fullmatch(
        r"[A-Z]{3}", values["PRS_AGENT_SEARCH_COUNTRY"]
    ):
        raise ValueError("Search country must be an uppercase alpha-3 code")
    if "PRS_AGENT_SEARCH_LANGUAGE" in values and not re.fullmatch(
        r"[a-z]{2,3}", values["PRS_AGENT_SEARCH_LANGUAGE"]
    ):
        raise ValueError("Search language must be a lowercase language code")
    if "DASHSCOPE_HOST" in values and values["DASHSCOPE_HOST"] not in {
        "dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com",
    }:
        raise ValueError("Invalid VLM region in private environment")
    return values


def merge_agent_environment(original, values):
    """Functionality: Apply only explicitly supplied agent settings to private env bytes.
    Inputs: original bytes and already validated values; Outputs: replacement bytes.
    Logic: Remove exact updated assignments, retain every other line, append shell-safe values.
    Constraints: Reject non-whitelisted names; never modifies HF/ORS/database/registry settings.
    """
    if set(values) - set(AGENT_ENV_FIELDS):
        raise ValueError("Unrelated private environment update refused")
    prefixes = tuple((name + "=").encode() for name in values)
    retained = b"".join(line for line in original.splitlines(keepends=True)
                        if not line.startswith(prefixes))
    if retained and not retained.endswith(b"\n"):
        retained += b"\n"
    return retained + "".join(f"{name}={shlex.quote(value)}\n"
                               for name, value in values.items()).encode()


def activate(client, sftp, manifest, agent_env_path=None):
    """Functionality: Publish one staged, checked minimal-mode release and preserve recovery
    material.
    Inputs: client/sftp/manifest and optional explicit agent_env_path;
    legacy full registry also reads private HF token.
    Outputs: activation info.
    Logic: check old-current match and staged hashes;
    vibration/live-tuning/navigation only switch sources.
    Full release with outputs registry: preserve HF/ORS and apply only explicit voice/VLM values.
    Legacy full registry: backup unit/include/env, update token through SFTP mode0600,
    validate nginx, atomic current link, restart only PRS, check readiness, graceful nginx reload.
    Constraints: no migration, other application restart, auto rollback or GPU call;
    logs activation/readiness transitions without credential values.
    """
    target, previous, release = manifest["path"], manifest["previous"], manifest["release"]
    agent_values = None
    if agent_env_path is not None:
        if manifest.get("profile") != "full" or manifest.get("mcp_profile") != "outputs":
            raise ValueError("Agent settings require a full bundle with explicit outputs registry")
        agent_values = load_agent_environment(agent_env_path)
    if execute(client, f"readlink -f {PREFIX}/current").strip() != previous:
        raise ValueError("Current release changed since staging")
    for name, expected in manifest["files"].items():
        if hashlib.sha256(sftp.open(target + "/" + name).read()).hexdigest() != expected:
            raise ValueError("Staged release changed")
    logger.info("Activation preconditions verified release=%s", release)
    if manifest.get("profile", "full") in {"vibration", "live-tuning", "navigation"}:
        activate_overlay(client, manifest)
        return
    if manifest.get("profile", "full") == "outputs" or manifest.get("mcp_profile") == "outputs":
        activate_outputs(client, sftp, manifest, agent_values=agent_values)
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
        if not line.startswith(("HF_INFERENCE_TOKEN=", "YOLO_MODEL_PATH=", "PRS_MCP_PROFILE="))
    ]
    with sftp.open("/etc/prs-camera/app.env.phone-next", "wb") as output:
        output.write(("\n".join(lines) + "\nHF_INFERENCE_TOKEN=" + token +
                      "\nPRS_MCP_PROFILE=full\n").encode())
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


def activate_outputs(client, sftp, manifest, agent_values=None):
    """Functionality: Activate reviewed device outputs with an isolated interpreter and registry.
    Inputs: client/sftp, precondition-verified manifest and optional validated agent_values;
    existing private service environment.
    Outputs: Activation/health evidence and recoverable previous release/config backups.
    Logic: Back up configuration, set outputs registry and explicit agent values, install
    reviewed service/proxy templates, atomically switch current, restart only prs-camera, check
    listener/health then reload nginx. HF/ORS credentials are preserved byte-for-byte.
    Constraints: No migration, inference change, other service restart, retry or automatic rollback;
    private configuration never enters public manifests or logs.
    """
    target, release = manifest["path"], manifest["release"]
    backup = "/etc/prs-camera/backups/" + release
    backup_unit = shlex.quote(backup + "/prs-camera.service")
    backup_proxy = shlex.quote(backup + "/nginx.locations.conf")
    execute(client, f"mkdir -m 700 -p {shlex.quote(backup)}\n"
            f"cp -a /etc/prs-camera/app.env {shlex.quote(backup + '/app.env')}\n"
            f"cp -a /etc/systemd/system/prs-camera.service {backup_unit}\n"
            f"cp -a /etc/nginx/prs-camera.locations.conf {backup_proxy}")
    with sftp.open("/etc/prs-camera/app.env", "rb") as source:
        original = source.read()
    preserved = b"".join(line for line in original.splitlines(keepends=True)
                         if not line.startswith(b"PRS_MCP_PROFILE="))
    updated = preserved + (b"" if preserved.endswith(b"\n") else b"\n")
    updated += b"PRS_MCP_PROFILE=outputs\n"
    if agent_values is not None:
        updated = merge_agent_environment(updated, agent_values)
        manifest["updated_environment_names"] = sorted(agent_values)
    with sftp.open("/etc/prs-camera/app.env.outputs-next", "wb") as output:
        output.write(updated)
    sftp.chmod("/etc/prs-camera/app.env.outputs-next", 0o600)
    logger.info("Activating device outputs release=%s; isolated runtime=%s", release,
                manifest["runtime"])
    manifest["activation_checks"] = execute(client, f"""
install -m 644 {shlex.quote(target + '/backend/deploy/prs-camera.service')} \\
  /etc/systemd/system/prs-camera.service
install -m 644 {shlex.quote(target + '/backend/deploy/nginx.locations.conf')} \\
  /etc/nginx/prs-camera.locations.conf
nginx -t
mv /etc/prs-camera/app.env.outputs-next /etc/prs-camera/app.env
systemctl daemon-reload
ln -s {shlex.quote(target)} {shlex.quote(PREFIX + '/current-' + release)}
mv -Tf {shlex.quote(PREFIX + '/current-' + release)} {PREFIX}/current
systemctl restart prs-camera
systemctl is-active prs-camera
""")
    manifest["activation_checks"] += wait_ready(client)
    manifest["activation_checks"] += execute(client, "systemctl reload nginx")
    manifest["activated"] = True
    manifest["backup"] = backup
    logger.info("Device outputs activated release=%s; previous configs/release retained", release)


def main():
    """Functionality: Stage first or activate the explicit reviewed manifest.
    Inputs: stage/activate, manifest/profile, source-root/git-commit/runtime/mcp-profile;
    agent-env supplies only explicit activation-time private voice/VLM values.
    Outputs: non-secret manifest/exit failure.
    Logic: configure transition logging; operations remain separate, never implicit promotion.
    Constraints: credentials stay private; exceptions preserve a failing exit status.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--stage", action="store_true")
    action.add_argument("--activate", action="store_true")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--profile", choices=("full", "vibration", "outputs", "live-tuning",
                                             "navigation"),
                        default="full")
    parser.add_argument("--source-root", type=Path,
                        help="Repository root; source files must be under SystemCode/")
    parser.add_argument("--git-commit")
    parser.add_argument("--runtime")
    parser.add_argument("--mcp-profile", choices=("full", "outputs"))
    parser.add_argument("--agent-env", type=Path)
    args = parser.parse_args()
    if args.agent_env and not args.activate:
        parser.error("--agent-env is only used during explicit activation")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    client = connect_server()
    try:
        with client.open_sftp() as sftp:
            manifest = (stage(client, sftp, args.profile, args.source_root,
                              args.git_commit, args.runtime, args.mcp_profile)
                        if args.stage else json.loads(args.manifest.read_text()))
            if args.activate:
                activate(client, sftp, manifest, args.agent_env)
            args.manifest.parent.mkdir(parents=True, exist_ok=True)
            args.manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({key: value for key, value in manifest.items() if key != "files"}))
    finally:
        client.close()


if __name__ == "__main__":
    main()
