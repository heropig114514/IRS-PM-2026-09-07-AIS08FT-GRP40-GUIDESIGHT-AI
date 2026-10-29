"""Responsibilities: Run the accepted phone MVP from one checkout over temporary HTTPS.
Implementation: Explicit FP32 TensorRT profile, checksum-pinned connector, loopback children,
bounded readiness observations, real fixture warmup and owned-process cleanup.
Related Modules: tools.run_local_mvp/local_demo; integration.tunnel_asgi/inference.asgi;
MVP-RELEASE.md. No application-server SSH or automatic model/engine provisioning.
Declaration Index: parse_args; selected_environment; connector_environment; preflight;
read_json; warmup; run; run.spawn; run.request_stop; main.
Variable Index: ROOT is this checkout; PROXY_KEYS isolates the connector from shell proxies;
children/logs are owned resources; state contains public URL and safe health/timing metadata.
"""

import argparse
import asyncio
import hashlib
import http.client
import json
import os
import re
import secrets
import signal
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

from tools.local_demo import local_environment
from tools.run_local_mvp import (
    INFERENCE_PACKAGES,
    ROOT,
    WEB_PACKAGES,
    StartupError,
    child_environment,
    inspect_environment,
    python_path,
    stop_children,
    verify_models,
    verify_ports,
    wait_listener,
)

PROXY_KEYS = {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "TUNNEL_TOKEN", "TUNNEL_ORIGIN_CERT"}


def parse_args(argv=None):
    """Functionality: Select explicit prerequisites and owned listener ports.
    Inputs: CLI argv. Outputs: namespace. Logic: require a target-built engine and trusted binary.
    Constraints: check never creates a tunnel; run is an explicit publication action.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-manifest", required=True, type=Path)
    parser.add_argument("--cloudflared", required=True, type=Path)
    parser.add_argument("--cloudflared-sha256", required=True)
    parser.add_argument("--inference-python", type=Path)
    parser.add_argument("--web-port", type=int, default=8766)
    parser.add_argument("--inference-port", type=int, default=8767)
    parser.add_argument("--metrics-port", type=int, default=18772)
    parser.add_argument("--state-dir", type=Path, default=Path(".venv/tmp/public-mvp"))
    parser.add_argument("--tts-upstream", help="Optional owned HTTPS speech service; no default")
    parser.add_argument("--check", action="store_true")
    return parser.parse_args(argv)


def selected_environment(args):
    """Functionality: Freeze the exact accepted model, transport and voice policy.
    Inputs: explicit manifest and optional owned TTS origin. Outputs: isolated environment.
    Logic: override inherited profiles and disable optional provider keys.
    Constraints: global model default remains independent; no precision/backend fallback.
    """
    from integration.mobile import mobile_origin

    environment = local_environment()
    environment.update(
        PRS_DETECTION_MODEL="yolo26n", PRS_DISTANCE_MODE="depth", PRS_DEPTH_INTERVAL="1",
        PRS_DEPTH_BACKEND="tensorrt", PRS_DEPTH_FUSION="host",
        PRS_DEPTH_ENGINE_MANIFEST=str((ROOT / args.engine_manifest).resolve()),
        PRS_CAMERA_PROFILE="low-latency-2", PRS_LATENCY_PANEL="1",
        PRS_PREPARED_SPEECH="1", PRS_RISK_TTL_MS="2000",
        PRS_TTS_UPSTREAM_ORIGIN=mobile_origin(args.tts_upstream) if args.tts_upstream else "",
        YOLO_OFFLINE="1", YOLO_AUTOINSTALL="0", PYTHONUTF8="1",
    )
    return environment


def connector_environment(environment):
    """Functionality: Isolate a new quick tunnel from inherited proxy and named-tunnel secrets.
    Inputs: selected environment. Outputs: independent mapping. Logic: case-insensitive removal.
    Constraints: does not change system VPN/routing or guarantee a particular Cloudflare edge.
    """
    return {key: value for key, value in environment.items() if key.upper() not in PROXY_KEYS}


def preflight(args):
    """Functionality: Check prerequisites before opening listeners or publishing traffic.
    Inputs: explicit CLI selections and selected process environment. Outputs: worker path/report.
    Logic: hash the supplied executable, verify local weights, package pins and CUDA.
    Constraints: no install, SSH, model execution or engine rebuild; warmup checks engines.
    """
    binary = (ROOT / args.cloudflared).resolve()
    if not re.fullmatch(r"[a-fA-F0-9]{64}", args.cloudflared_sha256):
        raise StartupError("Provide the independently verified cloudflared SHA256.")
    if hashlib.sha256(binary.read_bytes()).hexdigest() != args.cloudflared_sha256.lower():
        raise StartupError("cloudflared does not match the selected checksum.")
    if not (ROOT / args.engine_manifest).is_file():
        raise StartupError("Missing target-built depth engine manifest.")
    if args.metrics_port in {args.web_port, args.inference_port}:
        raise StartupError("Metrics, web and inference ports must be distinct.")
    verify_ports(args.web_port, args.inference_port)
    verify_ports(args.metrics_port, args.inference_port)
    worker = ((ROOT / args.inference_python).resolve() if args.inference_python
              else python_path(".venv-inference"))
    if not worker.is_file():
        raise StartupError("Selected inference interpreter is missing.")
    inspect_environment(Path(sys.executable), WEB_PACKAGES)
    gpu = inspect_environment(worker, INFERENCE_PACKAGES, cuda=True)
    verify_models()
    return worker, {"gpu": gpu, "detector": "yolo26n", "depth": "tensorrt-fp32",
                    "camera_profile": "low-latency-2", "risk_max_age_ms": 2000}


def read_json(port, path, host=None):
    """Functionality: Read one local status without proxies or redirects.
    Inputs: port/path and optional exact public Host. Outputs: parsed JSON.
    Logic: bounded local HTTP request. Constraints: no retry or response body in errors.
    """
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request("GET", path, headers={"Host": host} if host else {})
        response = connection.getresponse()
        if response.status != 200:
            raise StartupError(f"Local health failed with HTTP {response.status}.")
        return json.loads(response.read(65536))
    finally:
        connection.close()


async def warmup(web_port, inference_port):
    """Functionality: Verify real FP32 engine inference before advertising the phone URL.
    Inputs: owned ports and repository fixture. Outputs: eight timing records.
    Logic: one PRF2 session validates frame/session identities and returned JPEG hashes.
    Constraints: warms fixture shapes only; no phone or physical-speaker verification claim.
    """
    from websockets.asyncio.client import connect

    frame = (ROOT.parent / "frontend/tools/fixtures/detection-sample.jpg").read_bytes()
    records = []
    async with connect(f"ws://127.0.0.1:{inference_port}/ws/infer/",
                       origin=f"http://127.0.0.1:{web_port}", proxy=None,
                       compression=None, open_timeout=10) as ws:
        hello = json.loads(await asyncio.wait_for(ws.recv(), 55))
        for index in range(8):
            started = time.perf_counter()
            await ws.send(struct.pack("!4sId", b"PRF2", index, started) + frame)
            raw = await asyncio.wait_for(ws.recv(), 55 if index == 0 else 10)
            if not isinstance(raw, bytes) or raw[:4] != b"PRS1":
                raise StartupError("Warmup did not return an inference frame.")
            size = struct.unpack("!I", raw[4:8])[0]
            meta = json.loads(raw[8:8 + size])
            if (meta["index"] != index or meta["session_id"] != hello["session_id"]
                    or meta["jpeg_sha256"] != hashlib.sha256(raw[8 + size:]).hexdigest()
                    or meta["depth_backend"] != "tensorrt"):
                raise StartupError("Warmup frame identity/backend validation failed.")
            records.append({"rtt_ms": (time.perf_counter() - started) * 1000,
                            **{key: meta.get(key) for key in
                               ("server_ms", "inference_ms", "depth_ms", "execution")}})
        await ws.send('{"kind":"stop"}')
    return records


def run(args):
    """Functionality: Own one foreground phone session and its three local child processes.
    Inputs: explicit arguments. Outputs: URL/state or failure; always cleans owned children.
    Logic: preflight, fresh logs, warm GPU, acquire temporary hostname, launch exact-origin gate.
    Constraints: no process restart, existing-session takeover, firewall change or external keys.
    """
    os.environ.clear()
    os.environ.update(args.environment)
    worker, state = preflight(args)
    if args.check:
        print(json.dumps({"preflight": "passed", **state}), flush=True)
        return
    directory = (ROOT / args.state_dir).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    children, logs = [], []
    stopped = threading.Event()

    def request_stop(*_):
        """Functionality: Request shutdown. Inputs: signal arguments. Outputs: event set.
        Logic: leave cleanup to finally. Constraints: only this invocation's children.
        """
        stopped.set()

    def spawn(command, environment, name):
        """Functionality: Start one owned child. Inputs: argv/env/log name. Outputs: Popen.
        Logic: same checkout and hidden Windows window. Constraints: no shell interpolation.
        """
        log = (directory / (name + ".log")).open("xb")
        logs.append(log)
        child = subprocess.Popen([str(value) for value in command], cwd=ROOT, env=environment,
                                 stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                 creationflags=(subprocess.CREATE_NO_WINDOW
                                                if os.name == "nt" else 0))
        children.append(child)
        return child

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    state.update(running=False, supervisor_pid=os.getpid())
    try:
        common = ["-B", "-m", "uvicorn"]
        limits = ["--host", "127.0.0.1", "--workers", "1", "--no-proxy-headers", "--ws",
                  "websockets-sansio", "--ws-max-size", "1310728", "--ws-max-queue", "1",
                  "--ws-per-message-deflate", "false", "--no-access-log"]
        gpu_env = child_environment("", args.web_port, args.inference_port, web=False, assist=True)
        gpu_env.update(YOLO_CONFIG_DIR=str(directory), MPLCONFIGDIR=str(directory / "matplotlib"))
        gpu = spawn([worker, *common, "integration.inference.asgi:application", "--port",
                     args.inference_port, *limits], gpu_env, "inference")
        wait_listener(gpu, args.inference_port)
        read_json(args.inference_port, "/health/")
        state["warmup"] = asyncio.run(warmup(args.web_port, args.inference_port))
        connector = spawn([(ROOT / args.cloudflared).resolve(), "tunnel", "--url",
                           f"http://127.0.0.1:{args.web_port}", "--protocol", "http2",
                           "--edge-ip-version", "4", "--no-autoupdate", "--metrics",
                           f"127.0.0.1:{args.metrics_port}"],
                          connector_environment(os.environ), "connector")
        deadline, origin = time.monotonic() + 60, None
        while not stopped.is_set() and time.monotonic() < deadline:
            if connector.poll() is not None:
                raise StartupError("Cloudflare connector exited before registration.")
            found = re.findall(r"https://[a-z0-9-]+\.trycloudflare\.com",
                               (directory / "connector.log").read_text(errors="replace"))
            if found:
                origin = found[-1]
                break
            stopped.wait(.3)
        if not origin:
            raise StartupError("No temporary HTTPS hostname received within 60 seconds.")
        environment = child_environment(secrets.token_urlsafe(48), args.web_port,
                                        args.inference_port, web=True, assist=True)
        environment["MOBILE_PUBLIC_ORIGIN"] = origin
        web = spawn([sys.executable, *common, "integration.tunnel_asgi:application", "--port",
                     args.web_port, *limits], environment, "web")
        wait_listener(web, args.web_port)
        state["health"] = read_json(args.web_port, "/mvp/integration/status/", origin[8:])
        state.update(running=True, origin=origin, url=origin + "/mvp/maps/",
                     children=[child.pid for child in children])
        (directory / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
        print(json.dumps({"url": state["url"], "state_dir": str(directory)}), flush=True)
        while not stopped.wait(.5) and not (directory / "stop.request").exists():
            if any(child.poll() is not None for child in children):
                raise StartupError("An owned service exited; see its local log.")
    finally:
        stop_children(children)
        for log in logs:
            log.close()
        state["running"] = False
        (directory / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")


def main(argv=None):
    """Functionality: Dispatch explicit check/start. Inputs: argv. Outputs: process exit code.
    Logic: select isolated environment before preflight. Constraints: no shell environment change.
    """
    args = parse_args(argv)
    try:
        args.environment = selected_environment(args)
        run(args)
    except (StartupError, OSError, ValueError) as error:
        detail = str(error) if isinstance(error, StartupError) else type(error).__name__
        print(f"Public MVP failed: {detail}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
