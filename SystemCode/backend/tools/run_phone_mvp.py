"""Responsibilities: Run only the laptop-hosted CUDA worker behind an app-server tunnel.
Implementation: Verify pinned local dependencies/checkpoints, confirm the separate app-server Web
service, start the inference ASGI process, then reverse-forward its loopback port for on-demand use.
Related Modules: integration.inference.asgi; integration.phone_deploy/relay;
tools.deploy_phone_live.connect_server; tools.run_local_mvp; deploy/prs-mvp-web.service;
PHONE-MVP.md.
Declaration Index: public_ready; check_remote_binding; check_remote_health; check_web_service;
verify_inference_port; supervise; run; main.
Variable Index: ROOT/STOP_REQUESTED and child helpers are reused from the local launcher;
WEB_PORT/INFERENCE_REMOTE_PORT separate the permanent app-server Web listener from the laptop
inference tunnel; client/relay/children/log_handles own only this run's resources;
tools.deploy_phone_live.connect_server reads server_info.txt credentials without printing them.
"""

import argparse
import http.client
import json
import signal
import socket
import sys
import time
from pathlib import Path
from uuid import uuid4

from integration.phone_deploy import (
    INFERENCE_REMOTE_PORT,
    MOUNT,
    PUBLIC_ORIGIN,
    SERVER,
    WEB_PORT,
    PhoneDeploymentError,
    remote_command,
)
from integration.relay import RelayError, RelayForward
from tools.deploy_phone_live import connect_server
from tools.run_local_mvp import (
    INFERENCE_PACKAGES,
    ROOT,
    STOP_REQUESTED,
    WEB_PACKAGES,
    StartupError,
    child_environment,
    handle_signal,
    inspect_environment,
    launch,
    python_path,
    read_health,
    stop_children,
    verify_models,
    wait_listener,
)


def public_ready():
    """Functionality: Verify the public product page on the independent app-server service.
    Inputs: Fixed public HTTPS target. Outputs: Nonsecret status and permission metadata.
    Logic: One CA-verified unauthenticated GET checks page identity, WSS policy and device policy.
    Constraints: No redirects, retries, TLS bypass, device requests or page-body logging; this
    check does not exercise the inference worker.
    """
    connection = http.client.HTTPSConnection(SERVER, timeout=15)
    try:
        connection.request("GET", MOUNT + "/maps/")
        response = connection.getresponse()
        raw = response.read(1048577)
        csp = response.getheader("Content-Security-Policy", "")
        policy = response.getheader("Permissions-Policy", "")
        if (
            response.status != 200
            or len(raw) > 1048576
            or b'data-assist="true"' not in raw
            or "wss://" + SERVER not in csp
            or "camera=(self)" not in policy
            or "geolocation=(self)" not in policy
        ):
            raise StartupError("The application-server page failed HTTPS readiness checks.")
        return {
            "https_verified": True,
            "public_page_status": response.status,
            "camera_geolocation_policy": True,
        }
    finally:
        connection.close()


def check_remote_binding(client):
    """Functionality: Confirm the inference tunnel binds only to server loopback.
    Inputs: Pinned SSH client. Outputs: None or a fixed binding failure.
    Logic: Inspect the one assigned SSH reverse-forward port.
    Constraints: No firewall changes, broad process enumeration or remote process termination.
    """
    _, raw = remote_command(client, f"ss -H -ltn 'sport = :{INFERENCE_REMOTE_PORT}'")
    rows = [line.split() for line in raw.decode("ascii").splitlines() if line.strip()]
    if (len(rows) != 1 or len(rows[0]) < 4
            or rows[0][3] != f"127.0.0.1:{INFERENCE_REMOTE_PORT}"):
        raise StartupError("The inference tunnel is not bound to its dedicated loopback port.")


def check_remote_health(client):
    """Functionality: Validate one inference health response through the live SSH tunnel.
    Inputs: Pinned SSH client and fixed loopback health path. Outputs: Safe worker state summary.
    Logic: Make one bounded remote curl request, then validate provider and boolean state fields.
    Constraints: Health may report an unloaded model; no frame is submitted and no load is claimed.
    """
    status, raw = remote_command(
        client,
        f"curl --fail --silent --show-error --max-time 5 "
        f"http://127.0.0.1:{INFERENCE_REMOTE_PORT}/health/",
        check=False,
    )
    if status:
        raise StartupError("The application server could not read inference health through SSH.")
    try:
        health = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise StartupError("The tunneled inference health response is invalid.") from None
    if (
        not isinstance(health, dict)
        or health.get("status") != "ok"
        or health.get("provider") != "local"
        or any(type(health.get(name)) is not bool for name in ("loaded", "ready", "busy"))
    ):
        raise StartupError(
            "The tunneled endpoint does not satisfy the local inference health contract."
        )
    return {"health_status": "ok", "model_loaded": health["loaded"], "model_ready": health["ready"]}


def check_web_service(client):
    """Functionality: Require the application-server service before starting local inference.
    Inputs: Pinned SSH client. Outputs: None for an active dedicated Web unit.
    Logic: Check only the named prs-mvp-web unit state.
    Constraints: Does not restart, modify or inspect the independent prs-camera unit.
    """
    status, _ = remote_command(client, "systemctl is-active --quiet prs-mvp-web", check=False)
    if status:
        raise StartupError("The application-server prs-mvp-web service is not active.")


def verify_inference_port(port):
    """Functionality: Reject invalid or occupied local inference ports before startup.
    Inputs: Explicit local TCP port. Outputs: None or a safe startup error.
    Logic: Bind a short-lived IPv4 loopback probe without selecting an alternative.
    Constraints: No process termination, wildcard listener or automatic port fallback.
    """
    if type(port) is not int or not 1024 <= port <= 65535:
        raise StartupError("Choose an inference port between 1024 and 65535.")
    try:
        with socket.socket() as probe:
            if sys.platform == "win32":
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind(("127.0.0.1", port))
    except OSError:
        raise StartupError(
            f"Loopback inference port {port} is unavailable; no process was stopped."
        ) from None


def supervise(relay, children):
    """Functionality: Observe only the local inference child and its owned SSH tunnel.
    Inputs: Started relay and child handles. Outputs: None for manual stop or fixed failure.
    Logic: Check tunnel state while polling owned processes.
    Constraints: No retry, process restart or SSH reconnect; cancellation remains explicit.
    """
    while not STOP_REQUESTED.wait(0.25):
        relay.check_active()
        if any(child.poll() is not None for child in children):
            raise StartupError(
                "The owned inference process exited; no automatic restart was attempted."
            )
    relay.check_active()


def run(args):
    """Functionality: Preflight or supervise laptop inference through the app-server tunnel.
    Inputs: Local check flag, inference port, log path and the ignored server_info.txt credential
    file consumed by the pinned SSH helper. Outputs: bounded readiness then service until explicit
    stop, child exit or SSH failure.
    Logic: Verify the Web unit first, launch only the CUDA worker, bind remote loopback 18767, and
    validate tunneled health plus the already-running public page; the password remains in memory.
    Constraints: No interactive password prompt, local Web process, Nginx changes, ORS transfer,
    GPU fallback, retry or restart.
    """
    web_python = python_path(".venv")
    selected_python = getattr(args, "inference_python", None)
    inference_python = (Path(selected_python).resolve() if selected_python is not None
                        else python_path(".venv-inference"))
    if selected_python is not None and not inference_python.is_file():
        raise StartupError(f"Missing explicit inference interpreter: {inference_python}")
    inspect_environment(web_python, {**WEB_PACKAGES, "paramiko": "4.0.0"})
    inspect_environment(inference_python, INFERENCE_PACKAGES, cuda=True)
    verify_models()
    verify_inference_port(args.inference_port)
    directory_root = args.log_dir.resolve()
    if not directory_root.is_relative_to(ROOT):
        raise StartupError("Inference logs must stay inside backend.")
    if args.check:
        print(
            "Laptop inference prerequisites verified; no services or SSH were started.", flush=True
        )
        return

    directory = directory_root / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8])
    directory.mkdir(parents=True)
    client = relay = None
    children, log_handles = [], []
    report = {
        "running": False,
        "product_url": PUBLIC_ORIGIN + MOUNT + "/maps/",
        "test_url": PUBLIC_ORIGIN + MOUNT + "/maps/test",
        "remote_inference_binding": f"127.0.0.1:{INFERENCE_REMOTE_PORT}",
        "web_service_on_application_server": True,
        "credential_saved": False,
    }
    STOP_REQUESTED.clear()
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    try:
        client = connect_server()
        client.get_transport().set_keepalive(20)
        check_web_service(client)
        environment = child_environment(
            "", WEB_PORT, args.inference_port, web=False, assist=True, ors_key=""
        )
        log_handle = (directory / "inference.log").open("ab")
        log_handles.append(log_handle)
        child = launch(
            inference_python,
            "integration.inference.asgi:application",
            args.inference_port,
            environment,
            log_handle,
        )
        children.append(child)
        wait_listener(child, args.inference_port)
        health = read_health(args.inference_port, "/health/")
        report["local_inference_health"] = {
            "status": health["status"],
            "model_loaded": bool(health.get("loaded", False)),
        }
        relay = RelayForward(
            client,
            local_port=args.inference_port,
            remote_port=INFERENCE_REMOTE_PORT,
            stop_event=STOP_REQUESTED,
        ).start()
        check_remote_binding(client)
        report["remote_inference_health"] = check_remote_health(client)
        report.update(public_ready())
        report["running"] = True
        (directory / "runtime.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("Product URL: " + report["product_url"], flush=True)
        print("Test URL: " + report["test_url"], flush=True)
        print(
            "The application-server pages remain available independently of this laptop.",
            flush=True,
        )
        print("Ctrl+C stops only this inference process and its SSH tunnel.", flush=True)
        print("Runtime report: " + str(directory / "runtime.json"), flush=True)
        supervise(relay, children)
    except RelayError as error:
        report["failure_code"] = error.code
        raise
    finally:
        if relay is not None:
            try:
                relay.stop()
            except Exception:
                report["tunnel_cleanup_confirmed"] = False
        if client is not None:
            client.close()
        stop_children(children)
        for output in log_handles:
            output.close()
        report["running"] = False
        (directory / "runtime.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


def main():
    """Functionality: Parse the laptop inference supervisor CLI and report safe failures.
    Inputs: Explicit local flags; normal startup reads credentials through the pinned helper.
    Outputs: process exit status. Logic: check mode performs preflight only; normal mode owns one
    worker and one tunnel. Constraints: No password argument or prompt, Nginx installer, public Web
    process or automatic restart.
    """
    parser = argparse.ArgumentParser(
        description="Run laptop CUDA inference through the application's loopback SSH tunnel."
    )
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--inference-port", type=int, default=8767)
    parser.add_argument("--inference-python", type=Path,
                        help="Explicit prepared inference interpreter; default is .venv-inference")
    parser.add_argument("--log-dir", type=Path, default=ROOT / ".venv/tmp/phone-mvp")
    args = parser.parse_args()
    try:
        run(args)
    except (StartupError, PhoneDeploymentError, RelayError) as error:
        print("Phone inference startup failed: " + str(error), file=sys.stderr, flush=True)
        return 1
    except Exception as error:
        print(
            "Phone inference startup failed: " + type(error).__name__,
            file=sys.stderr,
            flush=True,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
