"""Responsibilities: Supervise a direct HTTPS LAN demo using an already-owned GPU worker.
Implementation: Explicit private interface/subnet, dedicated seven-day TLS assets, fixed Web
and enrollment children, verified TLS health, state report and owned-only cleanup.
Related Modules: integration.lan_asgi/lan_setup_asgi; tools.lan_certificate/run_local_mvp.
Declaration Index: owned_directory; verify_tls; wait_bound; run; main.
Variable Index: ROOT is backend; STOP_REQUESTED is the shared supervisor cancellation event.
"""

import argparse
import http.client
import json
import os
import signal
import socket
import ssl
import subprocess
import time
from pathlib import Path

from integration.assist import CAMERA_PROFILES
from integration.lan import lan_address
from tools.lan_certificate import create_certificate
from tools.run_local_mvp import (
    ROOT,
    STOP_REQUESTED,
    StartupError,
    child_environment,
    handle_signal,
    read_health,
    read_or_create_key,
    stop_children,
)


def owned_directory(value):
    """Functionality: Bound generated TLS/control files to ignored backend runtime storage.
    Inputs: explicit path. Outputs: absolute path or error.
    Logic: resolve before requiring a descendant of backend/.venv/tmp.
    Constraints: no writes outside the workspace or to the runtime root itself.
    """
    directory, parent = Path(value).resolve(), (ROOT / ".venv/tmp").resolve()
    if directory == parent or not directory.is_relative_to(parent):
        raise StartupError("LAN runtime directories must be below backend/.venv/tmp")
    return directory


def verify_tls(address, port, directory):
    """Functionality: Verify certificate trust, IP identity and the routed live GPU health.
    Inputs: explicit listener and dedicated CA directory. Outputs: actual health dictionary.
    Logic: one bounded standard TLS request with the exact CA, without redirect following.
    Constraints: no insecure HTTPS mode, inference submission, camera claim or retry.
    """
    context = ssl.create_default_context(cafile=str(directory / "demo-ca.crt"))
    peer = http.client.HTTPSConnection(address, port, timeout=8, context=context)
    try:
        peer.request("GET", "/mvp/integration/status/")
        response = peer.getresponse()
        if response.status != 200:
            raise StartupError("LAN routed health did not return HTTP 200")
        body = json.loads(response.read(65537))
        if not body.get("ready") or body.get("session_seconds") is not None:
            raise StartupError("LAN demo requires the ready continuous local worker")
        return body
    finally:
        peer.close()


def wait_bound(child, address, port):
    """Functionality: Wait for one owned listener's startup, with no process restart.
    Inputs: owned Popen, address and port. Outputs: None or error.
    Logic: bounded TCP readiness observation while detecting child exit.
    Constraints: observation only; TLS and routed health are verified separately.
    """
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise StartupError("LAN Web child exited; inspect its named log")
        try:
            with socket.create_connection((address, port), timeout=0.25):
                return
        except OSError:
            time.sleep(0.1)
    raise StartupError("LAN listener did not become ready within 20 seconds")


def run(args):
    """Functionality: Prepare TLS or serve the explicitly configured local phone demo.
    Inputs: validated CLI address/subnet/ports/profile/runtime directories.
    Outputs: private runtime state, public enrollment instructions and owned Web children.
    Logic: preflight ports and existing GPU, load TLS, start once, verify HTTPS, then supervise.
    Constraints: never starts/stops the GPU or public tunnel, changes firewall/system trust,
    copies external keys, retries, reconnects, or automatically selects another network/profile.
    """
    address, _ = lan_address(args.address, args.subnet)
    tls, state = owned_directory(args.tls_dir), owned_directory(args.state_dir)
    if args.prepare_certificate:
        return print(json.dumps(create_certificate(tls, str(address))), flush=True)
    if not tls.is_dir():
        raise StartupError("Prepare the dedicated LAN certificate explicitly first")
    ports = (args.https_port, args.setup_port)
    if len(set(ports)) != 2 or any(not 1024 <= port <= 65535 for port in ports):
        raise StartupError("Choose two distinct LAN ports between 1024 and 65535")
    for port in ports:
        with socket.socket() as peer:
            if os.name == "nt":
                peer.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            peer.bind((str(address), port))
    health = read_health(args.inference_port, "/health/")
    if not health.get("ready") or health.get("session_seconds") is not None:
        raise StartupError("Start the selected continuous GPU worker before this Web-only launcher")
    server_tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_tls.load_cert_chain(tls / "server.crt", tls / "server.key")
    origin = f"https://{address}:{args.https_port}"
    if args.check:
        return print(
            json.dumps(
                {"preflight": "passed", "origin": origin, "camera_profile": args.camera_profile}
            ),
            flush=True,
        )
    state.mkdir(parents=True, exist_ok=False)
    environment = child_environment(
        read_or_create_key(ROOT / ".env.local-mvp"),
        args.canonical_web_port,
        args.inference_port,
        web=True,
        assist=True,
        ors_key="",
    )
    environment.update(
        PRS_LAN_ADDRESS=str(address),
        PRS_LAN_SUBNET=args.subnet,
        PRS_LAN_ORIGIN=origin,
        PRS_LAN_SETUP_PORT=str(args.setup_port),
        PRS_LAN_TLS_DIR=str(tls),
        PRS_CAMERA_PROFILE=args.camera_profile,
    )
    report = {
        "running": False,
        "pid": os.getpid(),
        "gpu_owned": False,
        "public_tunnel_changed": False,
        "camera_profile": args.camera_profile,
        "setup_url": f"http://{address}:{args.setup_port}/",
        "product_url": origin + "/mvp/maps/",
        "test_url": origin + "/mvp/maps/test",
        "subnet": args.subnet,
        "certificate": json.loads((tls / "certificate.json").read_text(encoding="utf-8")),
        "phone_trust_verified": False,
        "physical_phone_verified": False,
    }
    children, logs = [], []
    STOP_REQUESTED.clear()
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    try:
        for module, port, secure in (
            ("integration.lan_asgi:application", args.https_port, True),
            ("integration.lan_setup_asgi:application", args.setup_port, False),
        ):
            log = (state / ("https.log" if secure else "setup.log")).open("ab")
            logs.append(log)
            command = [
                str(ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")),
                "-B",
                "-m",
                "uvicorn",
                module,
                "--host",
                str(address),
                "--port",
                str(port),
                "--workers",
                "1",
                "--lifespan",
                "on",
                "--no-proxy-headers",
                "--ws",
                "websockets-sansio",
                "--ws-max-size",
                "1310728",
                "--ws-max-queue",
                "12",
                "--ws-per-message-deflate",
                "false",
            ]
            if secure:
                command += [
                    "--ssl-certfile",
                    str(tls / "server.crt"),
                    "--ssl-keyfile",
                    str(tls / "server.key"),
                ]
            child = subprocess.Popen(
                command,
                cwd=ROOT,
                env=environment,
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            children.append(child)
            wait_bound(child, str(address), port)
        report["health"] = verify_tls(str(address), args.https_port, tls)
        report.update(running=True, tls_verified=True, children=[child.pid for child in children])
        (state / "state.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(
            json.dumps(
                {
                    key: report[key]
                    for key in (
                        "running",
                        "setup_url",
                        "test_url",
                        "camera_profile",
                        "tls_verified",
                    )
                }
            ),
            flush=True,
        )
        while not STOP_REQUESTED.wait(0.5):
            if (state / "stop.request").exists():
                break
            if any(child.poll() is not None for child in children):
                raise StartupError("Owned LAN Web child exited")
    except Exception as error:
        report["failure_type"] = type(error).__name__
        raise
    finally:
        stop_children(children)
        for log in logs:
            log.close()
        report["running"] = False
        (state / "state.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def main():
    """Functionality: Parse explicit LAN deployment choices and run one supervisor.
    Inputs: CLI arguments. Outputs: prepared certificate, preflight or running demo.
    Logic: retain original camera policy unless explicitly selected by the operator.
    Constraints: no network discovery, automatic certificate replacement or implicit GPU owner.
    """
    parser = argparse.ArgumentParser(
        description="Serve the existing local GPU over dedicated LAN HTTPS"
    )
    parser.add_argument("--address", required=True)
    parser.add_argument("--subnet", required=True)
    parser.add_argument("--https-port", type=int, default=8443)
    parser.add_argument("--setup-port", type=int, default=8080)
    parser.add_argument("--inference-port", type=int, default=8767)
    parser.add_argument("--canonical-web-port", type=int, default=18766)
    parser.add_argument("--camera-profile", choices=sorted(CAMERA_PROFILES), default="standard")
    parser.add_argument("--tls-dir", default=str(ROOT / ".venv/tmp/lan-demo/tls"))
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--prepare-certificate", action="store_true")
    parser.add_argument("--check", action="store_true")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
