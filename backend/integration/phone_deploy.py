"""Responsibilities: Manage the isolated public guest relay and pinned app-server SSH operations.
Implementation: Bound remote reads/commands, install only the additive Nginx include when requested,
remove its retired access hash, and preserve the independent application-service identity.
Related Modules: tools.run_phone_mvp; tools.deploy_phone_live; deploy/phone-relay.locations.conf.
Declaration Index: PhoneDeploymentError; read_remote; write_remote;
remote_command; connect_server; read_ors; prepare_vhost; verify_relay; install_relay.
Variable Index: SERVER/PUBLIC_ORIGIN/WEB_PORT/INFERENCE_REMOTE_PORT/MOUNT are explicit deployment
targets; WEB_PORT is the app-server service and INFERENCE_REMOTE_PORT is the laptop reverse tunnel;
VHOST/INCLUDE/LEGACY_AUTH/ANCHOR/INCLUDE_LINE select only relay-owned Nginx files; ROOT is backend.
SSH/ORS values must never appear in logs or exception messages.
"""

import hashlib
import io
import secrets
import shlex
import time
from pathlib import Path
from uuid import uuid4

import paramiko
from dotenv.parser import parse_stream

from tools.deploy_phone_live import FINGERPRINT, PinnedHost

ROOT = Path(__file__).resolve().parents[1]
SERVER = "47.239.50.129"
PUBLIC_ORIGIN = "https://" + SERVER
WEB_PORT = 18766
INFERENCE_REMOTE_PORT = 18767
MOUNT = "/mvp"
VHOST = "/etc/nginx/sites-enabled/ai-interviewer"
INCLUDE = "/etc/nginx/prs-phone-relay.locations.conf"
LEGACY_AUTH = "/etc/nginx/prs-phone-relay.htpasswd"
ANCHOR = "include /etc/nginx/prs-camera.locations.conf;"
INCLUDE_LINE = "include " + INCLUDE + ";"


class PhoneDeploymentError(Exception):
    """Functionality: Carry a fixed non-secret deployment diagnostic.
    Logic: Fail without retry or changes to unrelated services. Constraints: No raw remote output.
    """


def read_remote(sftp, path, limit=65536):
    """Functionality: Read one named remote file with a strict size boundary.
    Inputs: SFTP owner/path/limit. Outputs: bytes. Logic: Bound before decoding/configuration.
    Constraints: No arbitrary directory enumeration, private-key access or content logging.
    """
    with sftp.open(path, "rb") as source:
        raw = source.read(limit + 1)
    if len(raw) > limit:
        raise PhoneDeploymentError("Remote file exceeds the deployment boundary.")
    return raw


def write_remote(sftp, path, raw, mode):
    """Functionality: Write a selected relay-owned file and explicit permissions.
    Inputs: SFTP owner/path/bytes/mode. Outputs: None. Logic: Close before permission changes.
    Constraints: Caller owns backup and transactional validation; credentials are never logged.
    """
    with sftp.open(path, "wb") as output:
        output.write(raw)
    sftp.chmod(path, mode)


def remote_command(client, command, *, body=None, check=True):
    """Functionality: Run one bounded, caller-selected server inspection/configuration operation.
    Inputs: Pinned client, fixed command, optional stdin. Outputs: status and bounded stdout.
    Logic: Secret material uses SSH stdin only, never arguments; refuse oversized output.
    Constraints: No retries, password/raw stderr logging or shell-built untrusted values.
    """
    stdin, stdout, stderr = client.exec_command(command, timeout=25)
    if body is not None:
        stdin.write(body)
        stdin.flush()
    stdin.channel.shutdown_write()
    raw, diagnostic = stdout.read(32769), stderr.read(32769)
    deadline = time.monotonic() + 25
    while not stdout.channel.exit_status_ready():
        if time.monotonic() >= deadline:
            stdout.channel.close()
            raise PhoneDeploymentError("Remote relay status exceeded its deadline.")
        time.sleep(0.05)
    status = stdout.channel.recv_exit_status()
    if len(raw) > 32768 or len(diagnostic) > 32768 or check and status:
        raise PhoneDeploymentError("Remote relay operation failed; inspect its named status.")
    return status, raw


def connect_server(password):
    """Functionality: Authenticate once to the inspected SSH server and fixed host identity.
    Inputs: Password supplied interactively in memory. Outputs: Connected SSHClient.
    Logic: No system host/key discovery; also verify the actual transport key before use.
    Constraints: No password persistence, retries, alternate server or identity acceptance prompt.
    """
    import base64

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(PinnedHost())
    try:
        client.connect(
            SERVER,
            port=22,
            username="root",
            password=password,
            allow_agent=False,
            look_for_keys=False,
            timeout=12,
            auth_timeout=12,
            banner_timeout=12,
        )
        key = client.get_transport().get_remote_server_key()
        digest = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode()
        if not secrets.compare_digest(digest, FINGERPRINT):
            raise PhoneDeploymentError("SSH identity does not match the inspected server.")
        client.get_transport().set_keepalive(20)
        return client
    except Exception:
        client.close()
        raise


def read_ors(client):
    """Functionality: Read the existing ORS key for an isolated app-server MVP environment.
    Inputs: Authenticated SSH client. Outputs: Credential string in caller memory.
    Logic: Read one bounded config; select exactly one valid ORS assignment without interpolation.
    Constraints: No remote writes, key printing, laptop transfer, other credentials or local
    storage.
    """
    with client.open_sftp() as sftp:
        sftp.get_channel().settimeout(25)
        raw = read_remote(sftp, "/etc/prs-camera/app.env")
    bindings = list(parse_stream(io.StringIO(raw.decode("utf-8-sig"))))
    found = [item.value for item in bindings if item.key == "ORS_API_KEY"]
    if (
        len(found) != 1
        or not found[0]
        or len(found[0]) > 8192
        or any(character.isspace() for character in found[0])
    ):
        raise PhoneDeploymentError("Existing server ORS configuration is missing or invalid.")
    return found[0]


def prepare_vhost(raw):
    """Functionality: Add exactly one relay include beside the existing PRS include.
    Inputs: Inspected virtual-host bytes. Outputs: Candidate bytes, preserving unrelated content.
    Logic: Refuse missing/duplicate markers; an already present additive include is idempotent.
    Constraints: No whole virtual-host replacement, certificate changes or other application edits.
    """
    anchor, own = ANCHOR.encode(), INCLUDE_LINE.encode()
    if raw.count(anchor) != 1 or raw.count(own) > 1:
        raise PhoneDeploymentError("HTTPS virtual-host include contract changed.")
    return raw if own in raw else raw.replace(anchor, anchor + b"\n    " + own, 1)


def verify_relay(client):
    """Functionality: Verify the public guest relay and absence of its retired password hash.
    Inputs: Pinned SSH client. Outputs: Safe template and guest-access metadata.
    Logic: Compare exact owned template and one include; fail while the legacy hash remains.
    Constraints: Does not reload Nginx, read private keys or contact the GPU.
    """
    expected = (ROOT / "deploy/phone-relay.locations.conf").read_bytes()
    try:
        with client.open_sftp() as sftp:
            sftp.get_channel().settimeout(25)
            actual = read_remote(sftp, INCLUDE)
            vhost = read_remote(sftp, VHOST)
            try:
                read_remote(sftp, LEGACY_AUTH)
            except FileNotFoundError:
                legacy_auth_present = False
            else:
                legacy_auth_present = True
        if actual != expected or vhost.count(INCLUDE_LINE.encode()) != 1 or legacy_auth_present:
            raise ValueError
    except (OSError, ValueError, UnicodeError):
        raise PhoneDeploymentError(
            "Relay is not installed with this configuration; use --install-relay."
        ) from None
    return {"template_sha256": hashlib.sha256(actual).hexdigest(), "guest_access": True,
            "legacy_access_hash_removed": True}


def install_relay(client):
    """Functionality: Install the explicitly requested additive public guest relay configuration.
    Inputs: Pinned SSH client. Outputs: Backup path and checked existing-app identity.
    Logic: Preserve originals, compare before activation, syntax-check and reload once; any error
    before successful reload restores selected files. Remove the legacy hash after Nginx serves the
    guest configuration.
    Constraints: No PRS restart, TLS changes, code uploads, migrations or automatic restart/retry.
    """
    _, pid_before = remote_command(client, "systemctl show prs-camera --property=MainPID --value")
    template = (ROOT / "deploy/phone-relay.locations.conf").read_bytes()
    tag = uuid4().hex
    backup = "/etc/nginx/prs-phone-relay-backups/" + tag
    with client.open_sftp() as sftp:
        sftp.get_channel().settimeout(25)
        target = sftp.normalize(VHOST)
        if target not in {VHOST, "/etc/nginx/sites-available/ai-interviewer"}:
            raise PhoneDeploymentError("HTTPS virtual-host target changed.")
        original = read_remote(sftp, target)
        candidate = prepare_vhost(original)
        previous = {}
        for name in (INCLUDE, LEGACY_AUTH):
            try:
                previous[name] = read_remote(sftp, name)
            except FileNotFoundError:
                previous[name] = None
        remote_command(client, "install -d -m 700 " + shlex.quote(backup))
        write_remote(sftp, backup + "/vhost.conf", original, 0o600)
        for name, raw in previous.items():
            if raw is not None:
                write_remote(sftp, backup + "/" + Path(name).name, raw, 0o600)
        if read_remote(sftp, target) != original:
            raise PhoneDeploymentError("HTTPS virtual-host changed during preparation.")
        stage = target + ".phone-next-" + tag
        try:
            write_remote(sftp, INCLUDE, template, 0o644)
            write_remote(sftp, stage, candidate, 0o644)
            sftp.posix_rename(stage, target)
            code, _ = remote_command(client, "nginx -t", check=False)
            if code:
                raise PhoneDeploymentError("Relay candidate failed nginx -t.")
            remote_command(client, "systemctl reload nginx")
        except Exception:
            # Restore disk state even if SFTP/control failed rather than returning a syntax code.
            # A failed reload can have uncertain remote completion; do not issue an implicit retry.
            restored = True
            try:
                write_remote(sftp, stage, original, 0o644)
                sftp.posix_rename(stage, target)
            except Exception:
                restored = False
            for name, raw in previous.items():
                try:
                    if raw is None:
                        try:
                            sftp.remove(name)
                        except FileNotFoundError:
                            pass
                    else:
                        write_remote(sftp, name, raw, 0o600 if name == LEGACY_AUTH else 0o644)
                except Exception:
                    restored = False
            detail = ("Previous files restored; reload completion is unconfirmed."
                      if restored else "File restoration incomplete; inspect the relay backup.")
            raise PhoneDeploymentError("Relay installation failed. " + detail) from None
        try:
            remote_command(client, "rm -f -- " + shlex.quote(LEGACY_AUTH))
        except Exception:
            raise PhoneDeploymentError(
                "Guest relay is active, but password-hash cleanup could not be confirmed; "
                "inspect the server file and relay backup."
            ) from None
    _, pid_after = remote_command(client, "systemctl show prs-camera --property=MainPID --value")
    if pid_before != pid_after:
        raise PhoneDeploymentError(
            "Existing PRS service identity changed during relay installation."
        )
    verified = verify_relay(client)
    return {
        "backup": backup,
        "nginx_syntax_valid": True,
        "nginx_reloaded": True,
        "existing_prs_pid_unchanged": True,
        **verified,
    }
