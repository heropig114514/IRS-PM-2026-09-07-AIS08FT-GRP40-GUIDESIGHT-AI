"""Responsibilities: Prepare and launch a reproducible teammate local inference demo.
Implementation: Explicit venv/package/model preparation, fixed profiles and existing supervisors;
standard-library bootstrap, no system package installation or server relay dependency.
Related Modules: deploy/local-demo.toml; tools.run_local_mvp/run_lan_mvp; LOCAL-QUICKSTART.md.
Declaration Index: LocalDemoError; settings; interpreter; execute; ensure_environment;
file_digest; provision_models; prepare; local_environment; profile_environment;
launch_command; parse_args; main.
Variable Index: ROOT is this checkout's backend; CONFIG holds nonsecret versioned profiles;
MAX_MODEL_BYTES bounds each explicit download; CUDA_INDEXES admits official Torch wheel sources.
"""

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "deploy/local-demo.toml"
MAX_MODEL_BYTES = 64 * 1024 * 1024
CUDA_INDEXES = {name: f"https://download.pytorch.org/whl/{name}" for name in ("cu126", "cu128")}


class LocalDemoError(Exception):
    """Functionality: Report an actionable preparation or selection failure.
    Logic: stop the requested operation. Constraints: no automatic install, retry or fallback.
    """


def settings():
    """Functionality: Load the versioned nonsecret local deployment configuration.
    Inputs: fixed TOML path. Outputs: configuration mapping.
    Logic: only the repository-owned file is accepted. Constraints: never loads private dotenv.
    """
    with CONFIG.open("rb") as source:
        return tomllib.load(source)


def interpreter(directory):
    """Functionality: Select a standard venv executable on Windows or Linux.
    Inputs: explicit environment directory. Outputs: absolute interpreter path.
    Logic: platform layout only. Constraints: no PATH/global interpreter fallback.
    """
    return directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def execute(command):
    """Functionality: Run one explicit preparation command in this backend.
    Inputs: argument list. Outputs: successful completion or fixed failure.
    Logic: no shell interpolation; stream package diagnostics and preserve nonzero exits.
    Constraints: caller selects only venv-local package operations; no automatic retries.
    """
    result = subprocess.run([str(value) for value in command], cwd=ROOT, check=False)
    if result.returncode:
        raise LocalDemoError(f"Preparation command failed (exit {result.returncode}).")


def ensure_environment(directory):
    """Functionality: Create an isolated environment or verify an existing owned venv.
    Inputs: fixed backend environment directory. Outputs: Python3.12 interpreter.
    Logic: refuse unrelated/symlink directories; check Python before any package changes.
    Constraints: preparation is explicit; never deletes a venv or installs into global Python.
    """
    if directory.is_symlink() or not directory.resolve().is_relative_to(ROOT.resolve()):
        raise LocalDemoError("Environment must be an owned directory inside this backend.")
    if not directory.exists():
        execute([sys.executable, "-m", "venv", directory])
    python = interpreter(directory)
    if not (directory / "pyvenv.cfg").is_file() or not python.is_file():
        raise LocalDemoError(f"Not a prepared venv: {directory}; preserve and resolve it manually.")
    execute([python, "-c", "import sys;sys.exit(0 if sys.version_info[:2]==(3,12) "
             "and sys.prefix!=sys.base_prefix else 1)"])
    return python


def file_digest(path):
    """Functionality: Hash a complete checkpoint before any deserialization.
    Inputs: file path. Outputs: SHA256. Logic: bounded streaming.
    Constraints: never imports Torch, loads model objects or logs payload bytes.
    """
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def provision_models():
    """Functionality: Explicitly download missing official checkpoints with fixed hashes.
    Inputs: versioned manifest and backend/models. Outputs: verified files.
    Logic: preserve valid existing files, reject differing ones, stream to an owned temporary
    file, and publish only after full hash verification. Remove only the owned incomplete temp.
    Constraints: HTTPS only, at most64MiB per model, one request, no replace/retry/auto-export.
    """
    directory = ROOT / "models"
    if directory.is_symlink() or not directory.resolve().is_relative_to(ROOT.resolve()):
        raise LocalDemoError("Model directory must stay inside this backend.")
    directory.mkdir(exist_ok=True)
    for entry in settings()["models"].values():
        name, expected = entry["filename"], entry["sha256"]
        if Path(name).name != name or entry["url"] != (
            "https://github.com/ultralytics/assets/releases/download/v8.4.0/" + name
        ):
            raise LocalDemoError("Checkpoint source is outside the explicit release contract.")
        target = directory / name
        if target.is_symlink():
            raise LocalDemoError(f"Refuse model symlink: {name}")
        if target.exists():
            if file_digest(target) != expected:
                raise LocalDemoError(
                    f"Existing {name} hash differs; preserve and resolve manually."
                )
            print(f"Checkpoint verified: {name}", flush=True)
            continue
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=directory, suffix=".tmp", delete=False) as output:
                temporary = Path(output.name)
                with urllib.request.urlopen(entry["url"], timeout=60) as response:
                    if not response.geturl().startswith("https://"):
                        raise LocalDemoError("Checkpoint download must remain HTTPS.")
                    total = 0
                    while chunk := response.read(1024 * 1024):
                        total += len(chunk)
                        if total > MAX_MODEL_BYTES:
                            raise LocalDemoError("Checkpoint exceeds the explicit size bound.")
                        output.write(chunk)
            if file_digest(temporary) != expected:
                raise LocalDemoError(f"Downloaded {name} hash differs; file was not installed.")
            # Exclusive publication protects a checkpoint installed concurrently by another user.
            os.link(temporary, target)
            print(f"Checkpoint installed and verified: {name}", flush=True)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def prepare(args):
    """Functionality: Install exact Web/GPU prerequisites only on explicit preparation.
    Inputs: CUDA wheel choice and optional TensorRT export dependency selection.
    Outputs: two checked venvs and original SHA-verified weights.
    Logic: official CUDA Torch wheels first, fixed requirements next, then pip dependency checks.
    Constraints: may reuse/update only project venvs; no GPU inference, driver changes or engines.
    """
    web = ensure_environment(ROOT / ".venv")
    gpu = ensure_environment(ROOT / ".venv-inference")
    pip = ["-m", "pip", "--disable-pip-version-check", "install", "--retries", "0",
           "--timeout", "60", "--no-input"]
    execute([web, *pip, "-r", "requirements-local-demo.txt"])
    execute([gpu, *pip, f"torch==2.8.0+{args.cuda}", f"torchvision==0.23.0+{args.cuda}",
             "--index-url", CUDA_INDEXES[args.cuda]])
    execute([gpu, *pip, "-r", "requirements-inference.txt"])
    if args.with_tensorrt:
        execute([gpu, *pip, "-r", "requirements-inference-tensorrt.txt",
                 "-r", "requirements-depth-export.txt"])
    for python in (web, gpu):
        execute([python, "-m", "pip", "--disable-pip-version-check", "check"])
    provision_models()
    print("Preparation complete. Run 'check' before starting inference.", flush=True)


def local_environment():
    """Functionality: Isolate local test strategy and optional API credentials.
    Inputs: parent process environment. Outputs: independent mapping.
    Logic: clear inherited depth/distance/LAN overrides and blank external API keys.
    Constraints: preserve ordinary driver/PATH state; never mutate the calling shell.
    """
    environment = os.environ.copy()
    for key in tuple(environment):
        if key.startswith(("PRS_DEPTH_", "PRS_DISTANCE_", "PRS_LAN_")):
            environment.pop(key)
    for key in ("HF_TOKEN", "HF_INFERENCE_TOKEN", "ORS_API_KEY", "DASHSCOPE_API_KEY",
                "PRS_AGENT_API_KEY"):
        environment[key] = ""
    return environment


def profile_environment(args):
    """Functionality: Freeze explicit model/backend/queue selections for one invocation.
    Inputs: selected profile, detector and optional target-built engine manifest.
    Outputs: child environment and safe selection summary. Logic: isolate inherited settings,
    then assign exact profile values. Constraints: no backend fallback or engine discovery.
    """
    profile = settings()["profiles"][args.profile]
    manifest = args.engine_manifest
    if (profile["backend"] == "tensorrt") != (manifest is not None):
        raise LocalDemoError("Only the tensorrt profile requires --engine-manifest.")
    if manifest is not None:
        manifest = (ROOT / manifest).resolve()
        if not manifest.is_file() or manifest.suffix != ".json":
            raise LocalDemoError("Provide an existing target-built JSON engine manifest.")
    environment = local_environment()
    selected = {
        "PRS_DETECTION_MODEL": args.detector, "PRS_DISTANCE_MODE": "depth",
        "PRS_DEPTH_INTERVAL": "1", "PRS_DEPTH_BACKEND": profile["backend"],
        "PRS_DEPTH_FUSION": profile["fusion"], "PRS_CAMERA_PROFILE": profile["camera"],
        "PRS_DEPTH_ENGINE_MANIFEST": str(manifest) if manifest is not None else "",
        "YOLO_OFFLINE": "1", "YOLO_AUTOINSTALL": "0",
    }
    environment.update(selected)
    return environment, selected


def launch_command(args):
    """Functionality: Build an explicit existing-supervisor command for the selected entry.
    Inputs: check/run/LAN CLI arguments. Outputs: argv and isolated child environment.
    Logic: all relative paths are backend-relative; align LAN Origin with the local Web port.
    Constraints: no implicit certificate trust, public tunnel, alternate GPU or optional API.
    """
    if args.command == "lan":
        environment = local_environment()
        selected = {"PRS_CAMERA_PROFILE": args.camera_profile}
        environment.update(selected)
    else:
        environment, selected = profile_environment(args)
    web = interpreter(ROOT / ".venv")
    if not web.is_file():
        raise LocalDemoError("Missing Web venv; run the explicit prepare command first.")
    if args.command == "lan":
        command = [str(web), "-B", "-m", "tools.run_lan_mvp", "--address", args.address,
                   "--subnet", args.subnet, "--state-dir", str((ROOT / args.state_dir).resolve()),
                   "--tls-dir", str((ROOT / args.tls_dir).resolve()),
                   "--canonical-web-port", str(args.web_port),
                   "--inference-port", str(args.inference_port),
                   "--camera-profile", selected["PRS_CAMERA_PROFILE"]]
        if args.prepare_certificate:
            command.append("--prepare-certificate")
        if args.check:
            command.append("--check")
    else:
        command = [str(web), "-B", "-m", "tools.run_local_mvp", "--web-port", str(args.web_port),
                   "--inference-port", str(args.inference_port)]
        if args.inference_python:
            command += ["--inference-python", str((ROOT / args.inference_python).resolve())]
        if args.mode == "assist":
            command.append("--assist")
        if args.command == "check":
            command.append("--check")
        else:
            command += ["--log-dir", str(ROOT / ".venv/tmp/local-demo")]
    print(json.dumps({"profile": getattr(args, "profile", None), "settings": selected,
                      "command": args.command, "local_only": True}), flush=True)
    return command, environment


def parse_args(argv=None):
    """Functionality: Parse portable preparation and explicit startup operations.
    Inputs: optional argument list. Outputs: namespace.
    Logic: low-latency Torch is the visible default; baseline/TensorRT remain explicit choices.
    Constraints: LAN requires a chosen address/subnet and fresh owned state path.
    """
    root_parser = argparse.ArgumentParser(
        description="Prepare and run local GPU tests without a relay."
    )
    commands = root_parser.add_subparsers(dest="command", required=True)
    installation = commands.add_parser(
        "prepare", help="Install venv dependencies and missing models"
    )
    installation.add_argument("--cuda", choices=sorted(CUDA_INDEXES), default="cu128")
    installation.add_argument("--with-tensorrt", action="store_true")
    commands.add_parser("models", help="Explicitly provision only missing hash-verified weights")
    for name in ("check", "run", "lan"):
        parser = commands.add_parser(name)
        parser.add_argument("--web-port", type=int, default=8766)
        parser.add_argument("--inference-port", type=int, default=8767)
        if name == "lan":
            parser.add_argument("--camera-profile", choices=("standard", "bounded-2", "bounded-3"),
                                default="bounded-2")
            parser.add_argument("--address", required=True)
            parser.add_argument("--subnet", required=True)
            parser.add_argument("--state-dir", type=Path, required=True)
            parser.add_argument("--tls-dir", type=Path, default=Path(".venv/tmp/lan-demo/tls"))
            operation = parser.add_mutually_exclusive_group()
            operation.add_argument("--prepare-certificate", action="store_true")
            operation.add_argument("--check", action="store_true")
        else:
            parser.add_argument("--profile", choices=("baseline", "low-latency", "tensorrt"),
                                default="low-latency")
            parser.add_argument("--detector", choices=("yolo26n", "yolo26n-walk"),
                                default="yolo26n")
            parser.add_argument("--engine-manifest", type=Path)
            parser.add_argument("--mode", choices=("assist", "video"), default="assist")
            parser.add_argument("--inference-python", type=Path)
    return root_parser.parse_args(argv)


def main(argv=None):
    """Functionality: Dispatch one user-selected preparation/check/foreground service operation.
    Inputs: CLI arguments. Outputs: exit code or replacement supervisor process.
    Logic: require Python3.12 and Windows/Linux; preserve supervisor signal/process ownership by
    executing it directly. Read-only check waits for its bounded preflight subprocess.
    Constraints: no preparation during run/check, GPU benchmarks, cloud upload or port fallback.
    """
    args = parse_args(argv)
    try:
        if sys.version_info[:2] != (3, 12) or platform.system() not in {"Windows", "Linux"}:
            raise LocalDemoError("Use Python 3.12 on Windows/Linux with a usable NVIDIA CUDA GPU.")
        if args.command == "prepare":
            prepare(args)
        elif args.command == "models":
            provision_models()
        else:
            command, environment = launch_command(args)
            if args.command == "check" or args.command == "lan" and args.check:
                return subprocess.run(command, cwd=ROOT, env=environment, check=False).returncode
            os.chdir(ROOT)
            os.execve(command[0], command, environment)
    except (LocalDemoError, OSError, urllib.error.URLError, tomllib.TOMLDecodeError) as error:
        # urllib errors can contain local proxy details; retain only the actionable error category.
        detail = str(error) if isinstance(error, LocalDemoError) else type(error).__name__
        print(f"Local demo failed: {detail}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
