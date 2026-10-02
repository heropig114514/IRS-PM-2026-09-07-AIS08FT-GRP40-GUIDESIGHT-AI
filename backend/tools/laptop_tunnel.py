"""Responsibilities: Own a configured SSH reverse tunnel within the combined worker lifetime.
Implementation: Trusted host keys, interactive authentication, bounded loopback channel forwarding.
Related Modules: tools.run_laptop owns startup/shutdown; tools.laptop_config defines boundaries.
Declaration Index: ReverseTunnel; ReverseTunnel.__init__; ReverseTunnel.connect;
ReverseTunnel.listener; ReverseTunnel.activate; ReverseTunnel.forward; ReverseTunnel.serve;
ReverseTunnel.close.
Variable Index: logger — safe lifecycle diagnostics; MAX_CHANNELS — forwarding thread bound.
"""

import getpass
import logging
import select
import socket
from threading import BoundedSemaphore, Thread

import paramiko

logger = logging.getLogger(__name__)
MAX_CHANNELS = 4


class ReverseTunnel:
    """Functionality: Own one authenticated SSH connection and its private reverse listener.
    Logic: connect, verify vacant port, activate after worker readiness, close on any fatal exit.
    Constraints: no reconnect, password persistence, public bind or changes to remote SSH settings.
    """

    def __init__(self, config):
        """Functionality: Prepare ownership. Inputs: self and validated LaptopConfig.
        Outputs: client, transport and bounded channel state; Logic: reject unknown host keys.
        Constraints: does not connect or acquire listeners until explicitly requested.
        """
        self.config = config
        self.client = paramiko.SSHClient()
        self.client.load_host_keys(str(config.known_hosts))
        self.client.set_missing_host_key_policy(paramiko.RejectPolicy())
        self.transport = None
        self.slots = BoundedSemaphore(MAX_CHANNELS)
        self.error = None

    def connect(self):
        """Functionality: Authenticate and reserve connection ownership. Inputs: self/config/TTY.
        Outputs: active transport or exception; Logic: explicit private key or one password prompt.
        Constraints: no authentication fallback; secrets stay in process memory, no logging.
        """
        credentials = {}
        try:
            if self.config.key_path:
                try:
                    credentials["pkey"] = paramiko.PKey.from_path(self.config.key_path)
                except (paramiko.PasswordRequiredException, TypeError):
                    # cryptography's encrypted OpenSSH loader raises TypeError without a password.
                    passphrase = getpass.getpass("SSH key passphrase (not saved): ")
                    credentials["pkey"] = paramiko.PKey.from_path(
                        self.config.key_path, passphrase=passphrase.encode()
                    )
                    del passphrase
            else:
                credentials["password"] = getpass.getpass("SSH password (not saved): ")
            self.client.connect(
                self.config.host,
                port=self.config.ssh_port,
                username=self.config.user,
                look_for_keys=False,
                allow_agent=False,
                timeout=20,
                auth_timeout=20,
                banner_timeout=20,
                **credentials,
            )
        finally:
            credentials.clear()
        self.transport = self.client.get_transport()
        self.transport.set_keepalive(30)
        if self.listener():
            raise RuntimeError("Remote inference port is occupied; choose a vacant port")
        logger.info("SSH authenticated; remote inference port is vacant")

    def listener(self):
        """Functionality: Inspect remote port. Inputs: self/config's validated numeric port.
        Outputs: ss rows; Logic: require successful command and empty stderr.
        Constraints: read-only Linux ss command; cannot kill or replace another listener.
        """
        _, stdout, stderr = self.client.exec_command(
            f"ss -H -ltn 'sport = :{self.config.remote_port}'", timeout=10
        )
        rows = stdout.read().decode().splitlines()
        if stdout.channel.recv_exit_status() or stderr.read():
            raise RuntimeError("Cannot verify the remote inference port with ss")
        return rows

    def activate(self):
        """Functionality: Expose only private server loopback. Inputs: self's active transport.
        Outputs: SSH listener or exception; Logic: request bind then inspect exact local address.
        Constraints: called only after GPU worker is ready; failures require owner cleanup.
        """
        self.transport.request_port_forward("127.0.0.1", self.config.remote_port)
        rows = self.listener()
        if (
            len(rows) != 1
            or len(rows[0].split()) < 4
            or rows[0].split()[3] != f"127.0.0.1:{self.config.remote_port}"
        ):
            raise RuntimeError("SSH reverse listener is not exclusively IPv4 loopback")
        logger.info(
            "Tunnel ready cloud=127.0.0.1:%d laptop=127.0.0.1:%d",
            self.config.remote_port,
            self.config.local_port,
        )

    def forward(self, channel):
        """Functionality: Forward one accepted channel. Inputs: self, channel with acquired slot.
        Outputs: copied bytes and resource release; Logic: bidirectional select/sendall until EOF.
        Constraints: fixed local loopback; no media logging, replay or connection retry.
        """
        local = socket.socket()
        try:
            local.settimeout(8)
            local.connect(("127.0.0.1", self.config.local_port))
            local.settimeout(None)
            while self.transport.is_active():
                ready, _, _ = select.select([channel, local], [], [], 1)
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
            self.slots.release()

    def serve(self, stop):
        """Functionality: Accept bounded channels. Inputs: self's transport and shutdown Event.
        Outputs: forwarding threads or self.error on fatal disconnect; Logic: check SSH each cycle.
        Constraints: no reconnect; owner observes thread termination and stops the GPU worker.
        """
        try:
            while not stop.is_set():
                if not self.transport.is_active():
                    raise RuntimeError("SSH disconnected; restart the combined command manually")
                channel = self.transport.accept(timeout=0.5)
                if channel is not None:
                    if self.slots.acquire(blocking=False):
                        Thread(target=self.forward, args=(channel,), daemon=True).start()
                    else:
                        logger.warning("Tunnel channel rejected: forwarding capacity reached")
                        channel.close()
        except Exception as error:
            self.error = error
            logger.error(
                "Tunnel stopped exception_type=%s; check SSH connectivity", type(error).__name__
            )

    def close(self):
        """Functionality: Release this connection's listener/channels. Inputs: self's client.
        Outputs: SSH closure; Logic: client owns transport and all forwarded channels.
        Constraints: does not stop unrelated SSH sessions or cloud applications.
        """
        self.client.close()
