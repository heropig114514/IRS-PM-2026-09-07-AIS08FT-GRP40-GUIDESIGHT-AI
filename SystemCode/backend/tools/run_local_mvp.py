"""Responsibilities: Supervise the isolated web and local CUDA inference processes.
Implementation: Check default or explicitly selected inference environment/weights, prepare a
private key, start once on
loopback, observe listeners then health once, and terminate only owned Popen children on exit.
Related Modules: integration.asgi/assist_asgi; integration.inference.asgi; config.base;
requirements.txt/requirements-inference.txt; LOCAL-MVP.md.
Declaration Index: StartupError; python_path; inference_settings; inspect_environment;
verify_models; read_or_create_key; read_ors_key; child_environment; verify_ports; launch;
wait_listener; read_health; stop_children; handle_signal; run; main.
Variable Index: ROOT — backend directory; WEB_PACKAGES/INFERENCE_PACKAGES — required direct pins;
DEPTH_SHA256 — explicitly provisioned official depth checkpoint identity;
PRS_DISTANCE_* — inherited explicit strategy profile; B preflights only detection weights;
STOP_REQUESTED — foreground supervisor shutdown event; children — owned process handles only;
log_handles — optional owned log files; key — private local Django key, never logged.
assist/ors_key — explicit maps-enabled transport mode and web-only ORS credential.
inference_python — optional operator-selected interpreter; default .venv-inference is unchanged.
"""

import argparse
import hashlib
import http.client
import io
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
WEB_PACKAGES = {
    "Django": "5.2.17",
    "djangorestframework": "3.18.1",
    "uvicorn": "0.52.4",
    "python-dotenv": "1.2.3",
    "websockets": "16.1.1",
    "mcp": "2.3.0",
    "httpx2": "2.13.1",
    "numpy": "1.26.4",
    "Pillow": "12.3.0",
}
INFERENCE_PACKAGES = {
    "torch": "2.8.0",
    "torchvision": "0.23.0",
    "ultralytics": "8.4.171",
    "numpy": "1.26.4",
    "Pillow": "12.3.0",
    "opencv-python": "4.11.0.86",
    "lap": "0.5.12",
    "uvicorn": "0.52.4",
    "websockets": "16.1.1",
}
DEPTH_SHA256 = "befed1b8561d8b2eaa66274b070cdfc9c44853bda6d6d62a04759f9383af74e9"
STOP_REQUESTED = threading.Event()


class StartupError(Exception):
    """Functionality: Carry an actionable diagnostic without credentials or child log content.
    Logic: Supervisor failures stop owned children and return failure; Constraints: no retries.
    """


def python_path(environment):
    """Functionality: Resolve an explicitly owned project's interpreter.
    Inputs: environment directory. Outputs: existing interpreter Path or StartupError.
    Logic: Select the platform's standard venv location; Constraints: no global Python fallback.
    """
    path = ROOT / environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not path.is_file():
        raise StartupError(f"Missing interpreter: {path}; prepare this environment explicitly.")
    return path


def inference_settings():
    """Functionality: Isolate Ultralytics settings and disable its own online/install probes.
    Inputs: existing project inference environment root. Outputs: explicit library environment.
    Logic: pinned Ultralytics honors YOLO_OFFLINE before DNS lookup and appends Ultralytics to
    YOLO_CONFIG_DIR; the existing venv parent is writable without creating anything here.
    Constraints: no library/algorithm patch, default user Roaming directory or implicit install.
    """
    return {
        "YOLO_OFFLINE": "1",
        "YOLO_AUTOINSTALL": "0",
        "YOLO_CONFIG_DIR": str(ROOT / ".venv-inference"),
    }


def inspect_environment(python, packages, *, cuda=False):
    """Functionality: Verify Python/direct pins and optionally actual CUDA availability.
    Inputs: isolated interpreter, package map and CUDA flag. Outputs: safe runtime metadata.
    Logic: check explicit TensorRT pins when selected, then probe native packages/CUDA once.
    Constraints: no pip/install, model loading, downloads, secret reads or version substitution.
    """
    if cuda:
        from realtime.depth_config import read_depth_backend

        if read_depth_backend(ROOT).backend == "tensorrt":
            packages = {**packages, "tensorrt-cu12": "10.13.3.9"}
    probe = """
import importlib.metadata as metadata
import json
import sys
expected = json.loads(sys.argv[1])
versions = {}
for name in expected:
    try:
        versions[name] = metadata.version(name)
    except metadata.PackageNotFoundError:
        versions[name] = None
result = {"python": list(sys.version_info[:2]), "packages": versions}
if sys.argv[2] == "cuda" and all(
    versions[name] and versions[name].split("+", 1)[0] == version
    for name, version in expected.items()
):
    import torch
    result["cuda_available"] = torch.cuda.is_available()
    result["cuda_runtime"] = torch.version.cuda
    if result["cuda_available"]:
        result["device"] = torch.cuda.get_device_name(0)
print(json.dumps(result))
"""
    environment = os.environ.copy()
    if cuda:
        environment.update(inference_settings())
    try:
        result = subprocess.run(
            [str(python), "-B", "-c", probe, json.dumps(packages), "cuda" if cuda else "metadata"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=environment,
        )
        if result.returncode:
            raise StartupError(f"Runtime probe failed for {python}; inspect that environment.")
        runtime = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        raise StartupError(
            f"Runtime probe unavailable for {python}: {type(error).__name__}."
        ) from None
    if runtime.get("python") != [3, 12]:
        raise StartupError(f"{python} must use Python 3.12.")
    mismatches = [
        f"{name}={runtime['packages'].get(name) or 'missing'} (required {version})"
        for name, version in packages.items()
        if (runtime["packages"].get(name) or "").split("+", 1)[0] != version
    ]
    if mismatches:
        raise StartupError(f"Incomplete environment {python}: " + "; ".join(mismatches))
    if cuda and not runtime.get("cuda_available"):
        raise StartupError("Inference environment has no usable CUDA device; no CPU fallback.")
    return runtime


def verify_models():
    """Functionality: Verify selected trusted local checkpoints before starting any service.
    Inputs: selected detection profile/manifest and fixed depth path. Outputs: None or safe failure.
    Logic: Compare SHA256 before Torch deserialization; Constraints: no download/replacement.
    """
    from core.detection_model import model_files, validate_manifest
    from realtime.distance_config import read_distance_config

    model_dir = ROOT / "models"
    try:
        weights, manifest_path = model_files()
        manifest = validate_manifest(json.loads(manifest_path.read_text(encoding="utf-8")))
        expected = {weights.name: manifest["source_sha256"]}
        if read_distance_config().distance_mode != "geometry":
            expected["yolo26n-depth.pt"] = DEPTH_SHA256
        for name, digest in expected.items():
            path = model_dir / name
            if not path.is_file():
                raise StartupError(f"Missing local checkpoint: {path}; provision it explicitly.")
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise StartupError(f"Checkpoint SHA256 mismatch: {path}; service was not started.")
    except (OSError, ValueError, KeyError) as error:
        raise StartupError(f"Unable to verify model manifest: {type(error).__name__}.") from None


def read_or_create_key(path):
    """Functionality: Prepare only the dedicated Git-ignored local MVP Django key.
    Inputs: private .env.local-mvp Path. Outputs: validated key without printing its value.
    Logic: Exclusive creation preserves an existing file; read only the one expected key.
    Constraints: never read/overwrite .env, copy external credentials or emit private contents.
    """
    if not path.exists():
        key = secrets.token_urlsafe(48)
        try:
            with path.open("x", encoding="utf-8") as output:
                output.write("DJANGO_SECRET_KEY=" + key + "\n")
        except FileExistsError:
            pass
        else:
            if os.name != "nt":
                path.chmod(0o600)
    values = [
        line.partition("=")[2].strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("DJANGO_SECRET_KEY=")
    ]
    if len(values) != 1 or len(values[0]) < 40 or any(char.isspace() for char in values[0]):
        raise StartupError("Invalid .env.local-mvp Django key; correct that dedicated local file.")
    return values[0]


def read_ors_key(path=None):
    """Functionality: Read only the explicitly authorized ORS credential for assist mode.
    Inputs: optional dotenv Path, otherwise process ORS_API_KEY. Outputs: key or unconfigured empty.
    Logic: bounded UTF-8 dotenv parsing without interpolation/execution; select exactly one ORS key.
    Constraints: no parent mutation, automatic .env discovery, other credentials or key diagnostics.
    """
    if path is None:
        value = os.environ.get("ORS_API_KEY", "")
    else:
        from dotenv.parser import parse_stream

        try:
            with path.open("rb") as source:
                raw = source.read(65537)
            if len(raw) > 65536:
                raise StartupError("Explicit ORS environment file exceeds 64 KiB.")
            bindings = list(parse_stream(io.StringIO(raw.decode("utf-8-sig"))))
        except (OSError, UnicodeError):
            raise StartupError("Explicit ORS environment file cannot be read as UTF-8.") from None
        values = [binding.value for binding in bindings if binding.key == "ORS_API_KEY"]
        if any(binding.error for binding in bindings) or len(values) != 1 or not values[0]:
            raise StartupError("Explicit ORS environment file must contain one valid ORS_API_KEY.")
        value = values[0]
    value = value.strip()
    if path is not None and not value:
        raise StartupError("Explicit ORS environment file contains an empty ORS_API_KEY.")
    if len(value) > 8192 or any(character.isspace() for character in value):
        raise StartupError("ORS_API_KEY must be one bounded non-whitespace credential.")
    return value


def child_environment(key, web_port, inference_port, *, web, assist=False, ors_key=""):
    """Functionality: Construct explicit role-specific child settings without parent mutation.
    Inputs: private Django key, loopback ports, role, assist selection and ORS key. Outputs: env.
    Logic: Empty known external credentials take precedence over config.base's .env loading;
    web uses SQLite/outputs MCP; only assist web receives ORS; worker has no Django/ORS key.
    Constraints: HF/DashScope stay disabled; no wildcard Origin or inherited production/mode policy.
    """
    environment = os.environ.copy()
    environment.update(
        {
            name: ""
            for name in ("HF_TOKEN", "HF_INFERENCE_TOKEN", "ORS_API_KEY", "DASHSCOPE_API_KEY")
        }
    )
    environment.update(
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONUNBUFFERED="1",
        PYTHONUTF8="1",
        OPENBLAS_NUM_THREADS="1",
        LOCAL_WEB_ORIGIN=f"http://127.0.0.1:{web_port}",
        LOCAL_INFERENCE_URL=f"ws://127.0.0.1:{inference_port}/ws/infer/",
        LOCAL_MVP_MODE="assist" if assist else "offline",
    )
    if web:
        environment.update(
            DJANGO_SECRET_KEY=key,
            DJANGO_SETTINGS_MODULE="config.settings",
            PRS_MCP_PROFILE="outputs",
            ORS_API_KEY=ors_key if assist else "",
        )
    else:
        for name in ("DJANGO_SECRET_KEY", "DJANGO_SETTINGS_MODULE", "PRS_MCP_PROFILE"):
            environment.pop(name, None)
        environment.update(inference_settings())
    return environment


def verify_ports(web_port, inference_port):
    """Functionality: Reject invalid, duplicate or occupied loopback ports before startup.
    Inputs: explicit integer ports. Outputs: None or safe failure.
    Logic: Bind probes use 127.0.0.1 only; Constraints: no port reassignment or process termination.
    """
    if web_port == inference_port or any(
        not 1024 <= port <= 65535 for port in (web_port, inference_port)
    ):
        raise StartupError("Choose distinct web/inference ports between 1024 and 65535.")
    for port in (web_port, inference_port):
        try:
            with socket.socket() as probe:
                if os.name == "nt":
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                probe.bind(("127.0.0.1", port))
        except OSError:
            raise StartupError(
                f"Loopback port {port} is unavailable; no process was stopped."
            ) from None


def launch(python, module, port, environment, log_handle=None):
    """Functionality: Start one owned ASGI child once with the unchanged transport bounds.
    Inputs: interpreter/module/port/env and optional log file. Outputs: owned Popen handle.
    Logic: Fixed loopback, one worker, lifespan on, no WS compression; hide Windows child windows.
    Constraints: no shell, pip, downloads, detached restart policy or global PID lookup.
    """
    command = [
        str(python),
        "-B",
        "-m",
        "uvicorn",
        module,
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--workers",
        "1",
        "--lifespan",
        "on",
        "--ws",
        "websockets-sansio",
        "--ws-max-size",
        "1310728",
        "--ws-max-queue",
        "5",
        "--ws-per-message-deflate",
        "false",
        "--no-proxy-headers",
    ]
    return subprocess.Popen(
        command,
        cwd=ROOT,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=log_handle,
        stderr=subprocess.STDOUT if log_handle else None,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def wait_listener(child, port):
    """Functionality: Observe one child listener within the existing 55-second cold budget.
    Inputs: owned child handle and loopback port. Outputs: None when listening or safe failure.
    Logic: Bounded socket observation while checking child liveness; Constraints: never restart
    a process, send frames, load models, probe external services or hide startup errors.
    """
    deadline = time.monotonic() + 55
    while not STOP_REQUESTED.is_set():
        if child.poll() is not None:
            raise StartupError(
                f"Child exited before port {port} was ready (exit {child.returncode})."
            )
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return
        except OSError:
            if time.monotonic() >= deadline:
                raise StartupError(f"No listener on port {port} within 55 seconds.") from None
            STOP_REQUESTED.wait(0.1)
    raise StartupError("Local startup was cancelled.")


def read_health(port, path):
    """Functionality: Read one bounded local health response without following redirects.
    Inputs: approved loopback port and health path. Outputs: health object or StartupError.
    Logic: One HTTP GET after listener readiness; Constraints: no retries, GPU frames or secret
    output; loaded=false is valid cold service availability and is never reported as model ready.
    """
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        raw = response.read(65537)
        if response.status != 200 or len(raw) > 65536:
            raise StartupError(f"Local health {port}{path} failed with HTTP {response.status}.")
        health = json.loads(raw)
        if not isinstance(health, dict) or health.get("status") != "ok":
            raise StartupError(f"Local health {port}{path} did not report status=ok.")
        return health
    except (OSError, http.client.HTTPException, json.JSONDecodeError) as error:
        raise StartupError(
            f"Local health {port}{path} unavailable: {type(error).__name__}."
        ) from None
    finally:
        connection.close()


def stop_children(children):
    """Functionality: Release only the supervisor's child process handles.
    Inputs: owned Popen list. Outputs: None. Logic: Reverse startup order; request termination,
    wait up to 10 seconds, then kill that same handle if necessary.
    Constraints: no taskkill, PID files, name matching, process-tree deletion or unrelated services.
    """
    for child in reversed(children):
        if child.poll() is not None:
            continue
        child.terminate()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=10)


def handle_signal(signum, frame):
    """Functionality: Request foreground supervisor shutdown from SIGINT/SIGTERM.
    Inputs: signal number/frame. Outputs: None. Logic: Set the shared stop event only.
    Constraints: process cleanup runs in the supervisor's finally block, not in the signal handler.
    """
    STOP_REQUESTED.set()


def run(args):
    """Functionality: Check prerequisites or supervise the two independent local services.
    Inputs: CLI flags, optional explicit inference interpreter and assist/ORS file.
    Outputs: normal stop or failure.
    Logic: Check all prerequisites before private-key creation; start GPU then web once; observe
    health and wait for user stop or child failure; finally stop only owned children.
    Constraints: --check never writes configuration or starts services; no install/download/GPU
    inference, restart, migration or deployment; --assist alone permits explicit ORS routes.
    """
    assist = getattr(args, "assist", False)
    ors_env = getattr(args, "ors_env", None)
    if ors_env is not None and not assist:
        raise StartupError("--ors-env requires the explicit --assist mode.")
    ors_key = read_ors_key(ors_env) if assist else ""
    web_python = python_path(".venv")
    selected_python = getattr(args, "inference_python", None)
    inference_python = (Path(selected_python).resolve() if selected_python is not None
                        else python_path(".venv-inference"))
    if selected_python is not None and not inference_python.is_file():
        raise StartupError(f"Missing explicit inference interpreter: {inference_python}")
    inspect_environment(web_python, WEB_PACKAGES)
    runtime = inspect_environment(inference_python, INFERENCE_PACKAGES, cuda=True)
    verify_models()
    verify_ports(args.web_port, args.inference_port)
    print(
        f"Local prerequisites verified: Python 3.12, {runtime['device']}, "
        f"CUDA {runtime['cuda_runtime']}, selected checkpoint SHA256 identities.",
        flush=True,
    )
    if args.check:
        return
    key = read_or_create_key(ROOT / ".env.local-mvp")
    children, log_handles = [], []
    STOP_REQUESTED.clear()
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    log_dir = None
    if args.log_dir:
        parent = args.log_dir.resolve()
        if not parent.is_relative_to(ROOT):
            raise StartupError("--log-dir must stay inside this backend workspace.")
        log_dir = parent / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8])
        log_dir.mkdir(parents=True)
    try:
        for name, python, module, port, web, path in (
            (
                "inference",
                inference_python,
                "integration.inference.asgi:application",
                args.inference_port,
                False,
                "/health/",
            ),
            (
                "web",
                web_python,
                "integration.assist_asgi:application" if assist else "integration.asgi:application",
                args.web_port,
                True,
                "/integration/status/",
            ),
        ):
            output = (log_dir / f"{name}.log").open("ab") if log_dir else None
            if output:
                log_handles.append(output)
            child = launch(
                python,
                module,
                port,
                child_environment(key, args.web_port, args.inference_port, web=web,
                                  assist=assist, ors_key=ors_key),
                output,
            )
            children.append(child)
            wait_listener(child, port)
            health = read_health(port, path)
            if "session_seconds" not in health or (
                    health["session_seconds"] is not None if assist else
                    type(health["session_seconds"]) is not int or health["session_seconds"] != 55):
                raise StartupError("Web/worker session policy does not match the selected mode.")
            print(
                f"{name} listener/health available on 127.0.0.1:{port}; "
                f"model_loaded={health.get('loaded', False)}; "
                "first-frame inference remains to be verified.",
                flush=True,
            )
        print(
            f"Open http://127.0.0.1:{args.web_port}/{'maps/' if assist else 'live/'} ; "
            "Ctrl+C stops only these two child processes.",
            flush=True,
        )
        if log_dir:
            print(f"Child logs: {log_dir}", flush=True)
        while not STOP_REQUESTED.wait(0.25):
            for child in children:
                if child.poll() is not None:
                    raise StartupError(
                        f"Owned service exited (exit {child.returncode}); "
                        "no automatic restart was attempted."
                    )
    finally:
        stop_children(children)
        for output in log_handles:
            output.close()


def main():
    """Functionality: Parse the explicit local supervisor command and report safe failures.
    Inputs: CLI --check/ports/log directory/--assist/--ors-env/--inference-python.
    Outputs: exit status.
    Logic: Default runs until Ctrl+C; errors return 1 without private values or child log dumps.
    Constraints: no background PID registry, implicit installation or remote actions.
    """
    parser = argparse.ArgumentParser(
        description="Run the isolated local PRS MVP without external APIs."
    )
    parser.add_argument(
        "--check", action="store_true", help="Check prerequisites only; no writes/start"
    )
    parser.add_argument("--web-port", type=int, default=8766)
    parser.add_argument("--inference-port", type=int, default=8767)
    parser.add_argument("--inference-python", type=Path,
                        help="Explicit prepared inference interpreter; default is .venv-inference")
    parser.add_argument("--log-dir", type=Path, help="Optional log parent directory inside backend")
    parser.add_argument("--assist", action="store_true",
                        help="Enable same-page maps with local continuous perception; HF stays off")
    parser.add_argument("--ors-env", type=Path,
                        help="Explicit UTF-8 dotenv file: import only ORS_API_KEY with --assist")
    args = parser.parse_args()
    try:
        run(args)
    except (StartupError, OSError) as error:
        print(f"Local MVP startup failed: {error}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    if __package__ in (None, ""):
        sys.path.insert(0, str(ROOT))
    sys.exit(main())
