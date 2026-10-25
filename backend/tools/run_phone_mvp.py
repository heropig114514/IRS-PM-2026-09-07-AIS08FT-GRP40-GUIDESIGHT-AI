"""Responsibilities: Run local GPU/Web services behind the public HTTPS phone relay.
Implementation: One pinned interactive SSH connection, one reverse loopback bind, explicit optional
Nginx guest-relay install, existing isolated child processes and honest readiness/failure supervision.
Related Modules: integration.phone_deploy/mobile_asgi/relay; tools.run_local_mvp; PHONE-MVP.md.
Declaration Index: public_ready; check_remote_binding; supervise; run; main.
Variable Index: ROOT/STOP_REQUESTED and local child helpers are reused from the existing launcher;
client/relay/children/log_handles own only this run's resources; private SSH/ORS values never enter
arguments, reports or logs. runtime.json is ignored, finite operational evidence.
"""

import argparse
import getpass
import http.client
import json
import signal
import sys
import time
from pathlib import Path
from uuid import uuid4

from integration.phone_deploy import (
    MOUNT,
    PUBLIC_ORIGIN,
    REMOTE_PORT,
    SERVER,
    PhoneDeploymentError,
    connect_server,
    install_relay,
    read_ors,
    remote_command,
    verify_relay,
)
from integration.relay import RelayError, RelayForward
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
    read_or_create_key,
    stop_children,
    verify_models,
    verify_ports,
    wait_listener,
)


def public_ready():
    """Functionality: Verify one trusted HTTPS request across the actual server relay.
    Inputs: Fixed public relay target. Outputs: Public guest-readiness metadata.
    Logic: Native CA/IP verification, unauthenticated request, bounded HTML and explicit CSP/media gates.
    Constraints: No redirects, retries, TLS bypass, device requests or page-body logs.
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
            raise StartupError("Public phone relay did not pass HTTPS/page/permission readiness.")
        return {
            "https_verified": True,
            "public_page_status": response.status,
            "camera_geolocation_policy": True,
        }
    finally:
        connection.close()


def check_remote_binding(client):
    """Functionality: Confirm the reverse listener is server-loopback only.
    Inputs: Pinned SSH client. Outputs: None or fixed failure.
    Logic: Inspect one selected listening port; refuse wildcard, IPv6 or duplicate bindings.
    Constraints: No firewall change, broad listener enumeration or remote process termination.
    """
    _, raw = remote_command(client, "ss -H -ltn 'sport = :18766'")
    rows = [line.split() for line in raw.decode("ascii").splitlines() if line.strip()]
    if len(rows) != 1 or len(rows[0]) < 4 or rows[0][3] != "127.0.0.1:18766":
        raise StartupError("Remote relay listener is not the dedicated loopback binding.")


def supervise(relay, children):
    """Functionality: Observe owned services until explicit stop or one fatal relay failure.
    Inputs: Started relay and owned children. Outputs: None for manual stop or a fixed failure.
    Logic: Check the relay after the shared stop event wakes, since a fatal relay sets that event.
    Constraints: No retry, process restart or SSH reconnect; manual cancellation remains successful.
    """
    while not STOP_REQUESTED.wait(0.25):
        relay.check_active()
        if any(child.poll() is not None for child in children):
            raise StartupError("An owned local service exited; no automatic restart.")
    relay.check_active()


def run(args):
    """Functionality: Check prerequisites or supervise one explicitly requested phone deployment.
    Inputs: Check/install flag, local ports and log path. Outputs: Live service
    until explicit stop, child exit or SSH failure.
    Logic: Preflight once; optional additive server install; launch GPU/Web, bind reverse SSH and
    validate actual HTTPS. Finally release only owned forwarding/channels/processes/client.
    Constraints: No reconnect/restart, external GPU, PRS restart, SSH password or ORS persistence.
    """
    if args.check and args.install_relay:
        raise StartupError("--check and --install-relay cannot be combined.")
    web_python, inference_python = python_path(".venv"), python_path(".venv-inference")
    inspect_environment(web_python, {**WEB_PACKAGES, "paramiko": "4.0.0"})
    inspect_environment(inference_python, INFERENCE_PACKAGES, cuda=True)
    verify_models()
    verify_ports(args.web_port, args.inference_port)
    parent = args.log_dir.resolve()
    if not parent.is_relative_to(ROOT):
        raise StartupError("Phone logs must stay inside backend.")
    if args.check:
        print(
            "Phone prerequisites verified; no services, SSH or configuration changes.", flush=True
        )
        return
    client = relay = None
    children, log_handles = [], []
    report = {
        "running": False,
        "url": PUBLIC_ORIGIN + MOUNT + "/maps/",
        "remote_binding": "127.0.0.1:" + str(REMOTE_PORT),
        "cloud_inference": False,
        "ssh_password_saved": False,
        "ors_key_saved": False,
        "committed": False,
    }
    directory = parent / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8])
    directory.mkdir(parents=True)
    STOP_REQUESTED.clear()
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    try:
        password = getpass.getpass("SSH password (not saved): ")
        try:
            client = connect_server(password)
        finally:
            password = None
        if args.install_relay:
            report["relay_install"] = install_relay(client)
        else:
            report["relay_configuration"] = verify_relay(client)
        ors_key = read_ors(client)
        key = read_or_create_key(ROOT / ".env.local-mvp")
        for name, python, module, port, web, health_path in (
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
                "integration.mobile_asgi:application",
                args.web_port,
                True,
                "/integration/status/",
            ),
        ):
            environment = child_environment(
                key, args.web_port, args.inference_port, web=web, assist=True, ors_key=ors_key
            )
            if web:
                environment["MOBILE_PUBLIC_ORIGIN"] = PUBLIC_ORIGIN
            output = (directory / (name + ".log")).open("ab")
            log_handles.append(output)
            child = launch(python, module, port, environment, output)
            children.append(child)
            wait_listener(child, port)
            health = read_health(port, health_path)
            report[name] = {"listener_ready": True, "model_loaded": health.get("loaded", False)}
            print(name + " loopback listener/health ready.", flush=True)
        key = ors_key = None
        relay = RelayForward(
            client, local_port=args.web_port, remote_port=REMOTE_PORT, stop_event=STOP_REQUESTED
        ).start()
        check_remote_binding(client)
        report.update(public_ready())
        report["running"] = True
        (directory / "runtime.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("Phone URL: " + report["url"], flush=True)
        print("Website access: public guest mode; no username or password required.", flush=True)
        print("Runtime report: " + str(directory / "runtime.json"), flush=True)
        print(
            "Keep this computer running; Ctrl+C stops only this relay and its two children.",
            flush=True,
        )
        supervise(relay, children)
    except RelayError as error:
        report["failure_code"] = error.code
        raise
    finally:
        if relay is not None:
            try:
                relay.stop()
            except Exception:
                report["relay_cleanup_confirmed"] = False
        if client is not None:
            client.close()
        stop_children(children)
        for output in log_handles:
            output.close()
        report["running"] = False
        (directory / "runtime.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


def main():
    """Functionality: Parse the dedicated phone-relay CLI and report safe failures.
    Inputs: Local CLI flags; interactive SSH password only when running. Outputs: Exit status.
    Logic: Explicit installation is separate from normal startup; errors disclose type only.
    Constraints: No password argument, credential stdout, automatic port changes or restart.
    """
    parser = argparse.ArgumentParser(
        description="Serve the local assist MVP through the owned HTTPS server."
    )
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--install-relay",
        action="store_true",
        help="Explicitly install the public guest relay and remove its legacy access hash",
    )
    parser.add_argument("--web-port", type=int, default=8766)
    parser.add_argument("--inference-port", type=int, default=8767)
    parser.add_argument("--log-dir", type=Path, default=ROOT / ".venv/tmp/phone-mvp")
    args = parser.parse_args()
    try:
        run(args)
    except (StartupError, PhoneDeploymentError, RelayError) as error:
        print("Phone startup failed: " + str(error), file=sys.stderr, flush=True)
        return 1
    except Exception as error:
        print("Phone startup failed: " + type(error).__name__, file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
