"""Responsibilities: Maintain an explicitly started loopback SSH reverse tunnel to PRS.
Implementation: Trusted known_hosts, interactive existing password, fixed ports, no reconnect.
Related Modules: core.laptop_services; config.laptop_worker; backend/LAPTOP.md.
Declaration Index: forward; main.
Variable Index: HOST/PORT — inspected SSH server and loopback inference port; logger — safe states.
"""

import getpass
import logging
import select
import socket
from pathlib import Path
from threading import Thread

import paramiko

HOST = "47.239.50.129"
PORT = 18767
logger = logging.getLogger(__name__)


def forward(channel):
    """Functionality: Copy one SSH channel to the local worker. Inputs: accepted SSH channel.
    Outputs: socket traffic and closure; Logic: select/partial-safe sendall in both directions.
    Constraints: fixed 127.0.0.1 target, no image logging/storage; failures close, never reconnect.
    """
    local = socket.socket()
    try:
        local.settimeout(8)
        local.connect(("127.0.0.1", PORT))
        local.settimeout(None)
        while True:
            ready, _, _ = select.select([channel, local], [], [], 10)
            for source in ready:
                data = source.recv(65536)
                if not data:
                    return
                (local if source is channel else channel).sendall(data)
    except (OSError, EOFError) as error:
        logger.warning("Tunnel channel failed exception_type=%s", type(error).__name__)
    finally:
        local.close()
        channel.close()


def main():
    """Functionality: Start manual tunnel. Inputs: known_hosts, interactive SSH password.
    Outputs: server loopback listener forwarding to laptop loopback; Logic: inspect bind before use.
    Constraints: no password file/argument, global SSH settings, firewall change or automatic retry.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    logging.getLogger("paramiko").setLevel(logging.WARNING)
    client = paramiko.SSHClient()
    client.load_host_keys(str(Path.home() / ".ssh/known_hosts"))
    password = getpass.getpass("SSH password (not saved): ")
    try:
        client.connect(
            HOST,
            username="root",
            password=password,
            look_for_keys=False,
            allow_agent=False,
            timeout=20,
        )
        del password
        _, stdout, stderr = client.exec_command(f"ss -H -ltn 'sport = :{PORT}'", timeout=10)
        occupied = stdout.read()
        if stdout.channel.recv_exit_status() or stderr.read() or occupied:
            raise RuntimeError("Remote port is occupied or its state cannot be verified")
        transport = client.get_transport()
        transport.set_keepalive(30)
        transport.request_port_forward("127.0.0.1", PORT)
        _, stdout, stderr = client.exec_command(f"ss -H -ltn 'sport = :{PORT}'", timeout=10)
        binding = stdout.read().decode()
        if stdout.channel.recv_exit_status() or stderr.read() or f"127.0.0.1:{PORT}" not in binding:
            raise RuntimeError("Reverse tunnel loopback listener not verified")
        if any(address in binding for address in (f"0.0.0.0:{PORT}", f"*:{PORT}", f"[::]:{PORT}")):
            raise RuntimeError("Reverse tunnel unexpectedly exposed beyond loopback")
        logger.info("Tunnel ready server=127.0.0.1:%d local=127.0.0.1:%d", PORT, PORT)
        while transport.is_active():
            channel = transport.accept(timeout=1)
            if channel is not None:
                Thread(target=forward, args=(channel,), daemon=True).start()
        raise RuntimeError("SSH disconnected; check connectivity and start the tunnel manually")
    finally:
        client.close()


if __name__ == "__main__":
    main()
