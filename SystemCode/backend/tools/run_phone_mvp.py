"""Responsibilities: Run isolated laptop CUDA sessions behind one app-server tunnel.
Implementation: Verify pinned local dependencies/checkpoints, confirm the separate app-server Web
service, start one native worker per admitted phone and an optional loopback-only pool, then
reverse-forward the stable inference port. Multi-session startup prewarms every worker explicitly.
Related Modules: integration.inference.asgi/pool_asgi; integration.phone_deploy/relay;
tools.deploy_phone_live.connect_server; tools.run_local_mvp; deploy/prs-mvp-web.service;
PHONE-MVP.md.
Declaration Index: proxy_endpoint; connect_interactive; public_ready; check_remote_binding;
check_remote_health; check_web_service;
inference_ports; verify_inference_port; verify_ready_capacity; start_inference;
supervise; run; main.
Variable Index: ROOT/STOP_REQUESTED and child helpers are reused from the local launcher;
WEB_PORT/INFERENCE_REMOTE_PORT separate the permanent app-server Web listener from the laptop
inference tunnel; client/relay/children/log_handles own only this run's resources;
sessions/worker_ports select one to three process-isolated native pipelines without shared trackers;
tools.deploy_phone_live.connect_server reads server_info.txt credentials without printing them.
"""

import argparse
import asyncio
import getpass
import http.client
import json
import os
import signal
import socket
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import paramiko

from integration.mobile import mobile_origin
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
from tools.deploy_phone_live import PinnedHost, connect_server
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


def proxy_endpoint(value):
    """Functionality: Validate an explicit HTTP CONNECT proxy without exposing credentials.
    Inputs: Optional proxy origin. Outputs: hostname/port pair or None.
    Logic: Accept only a canonical HTTP authority with an explicit port.
    Constraints: No proxy discovery, credentials, paths, redirects or implicit fallback.
    """
    if not value:
        return None
    try:
        parsed = urlsplit(value)
        if (not value.isascii() or any(char.isspace() for char in value)
                or parsed.scheme != "http" or not parsed.hostname or not parsed.port
                or parsed.username is not None or parsed.password is not None
                or parsed.path or parsed.query or parsed.fragment
                or any(char in parsed.netloc for char in "*\\")
                or value != "http://" + parsed.netloc):
            raise ValueError
        return parsed.hostname, parsed.port
    except (ValueError, TypeError):
        raise StartupError(
            "Use an HTTP proxy origin with a port and no credentials or path."
        ) from None


def connect_interactive(proxy=None):
    """Functionality: Open a pinned SSH session using a hidden operator password prompt.
    Inputs: Optional validated HTTP CONNECT proxy. Outputs: Authenticated SSH client.
    Logic: Tunnel only the fixed server SSH port, validate its host identity, then authenticate.
    Constraints: No password argument/file, discovery, retries or automatic alternate route.
    """
    password = getpass.getpass(f"SSH password for root@{SERVER}: ")
    if not password:
        raise StartupError("An SSH password is required.")
    client, connection, stream = paramiko.SSHClient(), None, None
    try:
        client.load_system_host_keys()
        client.set_missing_host_key_policy(PinnedHost())
        if proxy:
            connection = http.client.HTTPConnection(*proxy, timeout=15)
            connection.set_tunnel(SERVER, 22)
            connection.connect()
            stream, connection.sock = connection.sock, None
            stream.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        client.connect(SERVER, port=22, username="root", password=password,
                       allow_agent=False, look_for_keys=False, timeout=15,
                       banner_timeout=15, auth_timeout=15, sock=stream)
        return client
    except Exception:
        client.close()
        if stream is not None:
            stream.close()
        raise
    finally:
        password = None
        if connection is not None:
            connection.close()


def public_ready(origin=PUBLIC_ORIGIN, proxy=None):
    """Functionality: Verify the public product page on the independent app-server service.
    Inputs: Explicit HTTPS origin and optional CONNECT proxy. Outputs: Safe permission metadata.
    Logic: One CA-verified unauthenticated GET checks page identity, WSS policy and device policy.
    Constraints: No redirects, retries, TLS bypass, device requests or page-body logging; this
    check does not exercise the inference worker.
    """
    target = urlsplit(mobile_origin(origin))
    if proxy:
        connection = http.client.HTTPSConnection(*proxy, timeout=15)
        connection.set_tunnel(target.hostname, target.port or 443)
    else:
        connection = http.client.HTTPSConnection(target.netloc, timeout=15)
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
            or "wss://" + target.netloc not in csp
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


def check_remote_health(client, capacity=1):
    """Functionality: Validate one inference health response through the live SSH tunnel.
    Inputs: Pinned SSH client, expected capacity and fixed health path.
    Outputs: Safe worker summary.
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
    if capacity > 1:
        verify_ready_capacity(health, capacity, idle=False)
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


def inference_ports(port, sessions=1):
    """Functionality: Allocate fixed native-worker ports for one explicit session capacity.
    Inputs: Primary loopback port and one, two or three sessions. Outputs: Native port tuple.
    Logic: Single-session keeps its original port; pooled workers use the following ports.
    Constraints: Validate the entire range before any socket probe; never select an alternate port.
    """
    if type(sessions) is not int or sessions not in {1, 2, 3}:
        raise StartupError("Choose one, two or three inference sessions.")
    if type(port) is not int or not 1024 <= port <= 65535:
        raise StartupError("Choose an inference port between 1024 and 65535.")
    ports = (port,) if sessions == 1 else tuple(range(port + 1, port + sessions + 1))
    if ports[-1] > 65535:
        raise StartupError("The inference worker ports exceed 65535; choose a lower primary port.")
    return ports


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


def verify_ready_capacity(health, capacity, *, idle=True):
    """Functionality: Refuse an incompletely warmed or mismatched multi-session pool.
    Inputs: Local/tunneled health, requested capacity and prepublication-idle requirement.
    Outputs: None or fixed failure. Logic: require ready native workers and valid pooled counters;
    require full availability before publication and permit phone activity afterward.
    Constraints: No health retries, lazy readiness claim or worker substitution.
    """
    if (health.get("status") != "ok" or health.get("provider") != "local"
            or health.get("loaded") is not True or health.get("ready") is not True
            or type(health.get("busy")) is not bool or idle and health["busy"]):
        raise StartupError(
            "Every native inference worker must be ready before publishing the pool."
        )
    if capacity > 1:
        active, available = health.get("active_sessions"), health.get("available_sessions")
        if (type(health.get("capacity")) is not int or health["capacity"] != capacity
                or type(active) is not int or not 0 <= active <= capacity
                or type(available) is not int or not 0 <= available <= capacity
                or active + available != capacity
                or health["busy"] is not (available == 0)
                or health.get("accept_available") is not (available > 0)
                or idle and (active != 0 or available != capacity)):
            raise StartupError(
                "The inference pool does not expose the requested session capacity."
            )


def start_inference(args, web_python, inference_python, environment, directory,
                    children, log_handles, report):
    """Functionality: Start and validate owned native workers and the optional local pool.
    Inputs: Explicit startup selections, interpreters, environment and ownership/report lists.
    Outputs: Child handles retained by the supervisor and bounded safe startup evidence.
    Logic: Allocate independent native processes, warm each, then expose the Web-runtime pool.
    Constraints: No local product Web process, shared tracker, automatic fallback or partial-ready
    publication; the caller's finally block cleans every child on any failure.
    """
    sessions = getattr(args, "sessions", 1)
    ports = inference_ports(args.inference_port, sessions)
    worker_warmups = []
    for port in ports:
        name = "inference.log" if sessions == 1 else f"inference-{port}.log"
        log_handle = (directory / name).open("ab")
        log_handles.append(log_handle)
        child = launch(inference_python, "integration.inference.asgi:application",
                       port, environment.copy(), log_handle)
        children.append(child)
        wait_listener(child, port)
        if getattr(args, "warmup", False) or sessions > 1:
            from tools.run_public_mvp import warmup

            records = asyncio.run(warmup(
                WEB_PORT, port, expected_backend=environment.get("PRS_DEPTH_BACKEND", "torch"),
            ))
            if sessions == 1:
                report["warmup"] = records
            else:
                worker_warmups.append({"port": port, "frames": records})
        if sessions > 1:
            verify_ready_capacity(read_health(port, "/health/"), 1)
    if sessions > 1:
        report["worker_warmups"] = worker_warmups
        pool_environment = environment.copy()
        pool_environment["PRS_INFERENCE_POOL_PORTS"] = ",".join(str(port) for port in ports)
        log_handle = (directory / "inference-pool.log").open("ab")
        log_handles.append(log_handle)
        child = launch(web_python, "integration.inference.pool_asgi:application",
                       args.inference_port, pool_environment, log_handle)
        children.append(child)
        wait_listener(child, args.inference_port)
    health = read_health(args.inference_port, "/health/")
    if sessions > 1:
        verify_ready_capacity(health, sessions)
    report["local_inference_health"] = {
        "status": health["status"], "model_loaded": bool(health.get("loaded", False)),
    }


def supervise(relay, children):
    """Functionality: Observe all owned inference children and their shared SSH tunnel.
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
    file or hidden prompt consumed by the pinned SSH helper. Outputs: readiness then service until
    stop, child exit or SSH failure.
    Logic: Verify the Web unit, launch isolated CUDA workers and any local pool, bind 18767, and
    validate tunneled health plus the already-running public page; the password remains in memory.
    Constraints: No local product Web process, Nginx changes, ORS transfer,
    GPU fallback, retry or restart.
    """
    origin = mobile_origin(getattr(args, "public_origin", PUBLIC_ORIGIN))
    proxy = proxy_endpoint(getattr(args, "http_proxy", None))
    interactive = getattr(args, "password_prompt", False)
    if proxy and not interactive:
        raise StartupError("An explicit proxy requires --password-prompt for SSH authentication.")
    sessions = getattr(args, "sessions", 1)
    worker_ports = inference_ports(args.inference_port, sessions)
    selected_web = getattr(args, "web_python", None)
    web_python = Path(selected_web).resolve() if selected_web else python_path(".venv")
    if selected_web and not web_python.is_file():
        raise StartupError("The explicit Web/relay interpreter does not exist.")
    selected_python = getattr(args, "inference_python", None)
    inference_python = (Path(selected_python).resolve() if selected_python is not None
                        else python_path(".venv-inference"))
    if selected_python is not None and not inference_python.is_file():
        raise StartupError(f"Missing explicit inference interpreter: {inference_python}")
    inspect_environment(web_python, {**WEB_PACKAGES, "paramiko": "4.0.0"})
    inspect_environment(inference_python, INFERENCE_PACKAGES, cuda=True)
    verify_models()
    for port in dict.fromkeys((args.inference_port, *worker_ports)):
        verify_inference_port(port)
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
        "product_url": origin + MOUNT + "/maps/",
        "test_url": origin + MOUNT + "/maps/test",
        "explicit_proxy": bool(proxy),
        "remote_inference_binding": f"127.0.0.1:{INFERENCE_REMOTE_PORT}",
        "web_service_on_application_server": True,
        "credential_saved": False,
        "session_capacity": sessions,
        "worker_ports": list(worker_ports),
    }
    STOP_REQUESTED.clear()
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    try:
        client = connect_interactive(proxy) if interactive else connect_server()
        client.get_transport().set_keepalive(20)
        check_web_service(client)
        environment = child_environment(
            "", WEB_PORT, args.inference_port, web=False, assist=True, ors_key=""
        )
        start_inference(args, web_python, inference_python, environment, directory,
                        children, log_handles, report)
        relay = RelayForward(
            client,
            local_port=args.inference_port,
            remote_port=INFERENCE_REMOTE_PORT,
            stop_event=STOP_REQUESTED,
        ).start()
        check_remote_binding(client)
        report["remote_inference_health"] = (check_remote_health(client, sessions) if sessions > 1
                                             else check_remote_health(client))
        report.update(public_ready(origin, proxy))
        report["inference_profile"] = {name: os.environ.get(name, "") for name in (
            "PRS_DETECTION_MODEL", "PRS_DEPTH_BACKEND", "PRS_DEPTH_FUSION",
            "PRS_DISTANCE_MODE", "PRS_DEPTH_INTERVAL",
        )}
        report["running"] = True
        (directory / "runtime.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("Product URL: " + report["product_url"], flush=True)
        print("Test URL: " + report["test_url"], flush=True)
        print(
            "The application-server pages remain available independently of this laptop.",
            flush=True,
        )
        print("Ctrl+C stops only this run's inference processes and SSH tunnel.", flush=True)
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
    tunnel and one to three isolated workers. Constraints: No password argument, Nginx installer,
    public Web process or automatic restart.
    """
    parser = argparse.ArgumentParser(
        description="Run laptop CUDA inference through the application's loopback SSH tunnel."
    )
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--inference-port", type=int, default=8767)
    parser.add_argument("--sessions", type=int, choices=(1, 2, 3), default=1,
                        help="Independent phone sessions; two or three always prewarm every worker")
    parser.add_argument("--inference-python", type=Path,
                        help="Explicit prepared inference interpreter; default is .venv-inference")
    parser.add_argument("--web-python", type=Path,
                        help="Explicit prepared Web/relay interpreter; default is .venv")
    parser.add_argument("--public-origin", default=PUBLIC_ORIGIN)
    parser.add_argument("--http-proxy", help="Explicit HTTP CONNECT proxy for SSH and HTTPS checks")
    parser.add_argument("--password-prompt", action="store_true")
    parser.add_argument("--warmup", action="store_true",
                        help="Verify eight real frames before ready")
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
