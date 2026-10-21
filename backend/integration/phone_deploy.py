"""Responsibilities: Provision only the dedicated HTTPS relay and its private access settings.
Implementation: Pinned server, bounded SFTP reads, bcrypt-bounded ASCII Basic auth and one additive
Nginx include; syntax-check before reload and preserve existing app/service identity.
Related Modules: tools.run_phone_mvp; tools.deploy_phone_live; deploy/phone-relay.locations.conf.
Declaration Index: PhoneDeploymentError; private_access; read_remote; write_remote;
remote_command; connect_server; read_ors; prepare_vhost; verify_relay; install_relay.
Variable Index: SERVER/PUBLIC_ORIGIN/REMOTE_PORT/MOUNT are explicit inspected deployment targets;
VHOST/INCLUDE/AUTH/ANCHOR/INCLUDE_LINE select only relay-owned files and one additive include;
ROOT is backend; access/password/ORS values must never appear in logs or exception messages.
"""

import hashlib
import io
import json
import os
import re
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
REMOTE_PORT = 18766
MOUNT = "/mvp"
VHOST = "/etc/nginx/sites-enabled/ai-interviewer"
INCLUDE = "/etc/nginx/prs-phone-relay.locations.conf"
AUTH = "/etc/nginx/prs-phone-relay.htpasswd"
ANCHOR = "include /etc/nginx/prs-camera.locations.conf;"
INCLUDE_LINE = "include " + INCLUDE + ";"


class PhoneDeploymentError(Exception):
    """Functionality: Carry a fixed non-secret deployment diagnostic.
    Logic: Fail without retry or changes to unrelated services. Constraints: No raw remote output.
    """


def private_access(path, *, create=False):
    """Functionality: Load or exclusively create the dedicated phone access credential.
    Inputs: Explicit dotenv Path and create flag. Outputs: username/password tuple.
    Logic: Generate a random password once; validate unambiguous fields without expansion;
    require 20–72 ASCII password characters to respect bcrypt's 72-byte limit without truncation.
    Constraints: Creation stays under backend; no SSH/ORS persistence, overwrite or secret logging.
    """
    path = Path(path).resolve()
    if create and not path.exists():
        if not path.is_relative_to(ROOT):
            raise PhoneDeploymentError("Access-file creation must stay inside backend.")
        try:
            with path.open("x", encoding="utf-8") as output:
                output.write(
                    "PHONE_ACCESS_USER=phone\nPHONE_ACCESS_PASSWORD="
                    + secrets.token_urlsafe(24)
                    + "\n"
                )
            if os.name != "nt":
                path.chmod(0o600)
        except FileExistsError:
            pass
    try:
        with path.open("rb") as source:
            raw = source.read(8193)
        if len(raw) > 8192:
            raise ValueError
        bindings = list(parse_stream(io.StringIO(raw.decode("utf-8-sig"))))
        if any(item.error for item in bindings):
            raise ValueError
        values = {}
        for name in ("PHONE_ACCESS_USER", "PHONE_ACCESS_PASSWORD"):
            found = [item.value for item in bindings if item.key == name]
            if len(found) != 1:
                raise ValueError
            values[name] = found[0]
        user, password = values["PHONE_ACCESS_USER"], values["PHONE_ACCESS_PASSWORD"]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", user or "") or not re.fullmatch(
            r"[A-Za-z0-9_-]{20,72}", password or ""
        ):
            raise ValueError
        return user, password
    except (OSError, UnicodeError, ValueError):
        raise PhoneDeploymentError("Invalid dedicated phone access file.") from None


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
    """Functionality: Read only the existing server ORS key for the local Web child.
    Inputs: Authenticated SSH client. Outputs: Credential string in memory.
    Logic: Read one bounded config; select exactly one valid ORS assignment without interpolation.
    Constraints: No remote writes, key printing, copying other external keys or local persistence.
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


def verify_relay(client, user, password):
    """Functionality: Verify the installed relay template and password hash without changes.
    Inputs: Pinned SSH client and local access credential. Outputs: Safe verification metadata.
    Logic: Compare exact owned template, one include and bcrypt password identity.
    Constraints: Does not reload Nginx, rotate credentials, read private keys or contact the GPU.
    """
    import bcrypt

    expected = (ROOT / "deploy/phone-relay.locations.conf").read_bytes()
    try:
        with client.open_sftp() as sftp:
            sftp.get_channel().settimeout(25)
            actual = read_remote(sftp, INCLUDE)
            vhost = read_remote(sftp, VHOST)
            auth = read_remote(sftp, AUTH).strip()
        name, separator, digest = auth.partition(b":")
        if (
            actual != expected
            or vhost.count(INCLUDE_LINE.encode()) != 1
            or not separator
            or name.decode() != user
            or not bcrypt.checkpw(password.encode(), digest)
        ):
            raise ValueError
    except (OSError, ValueError, UnicodeError):
        raise PhoneDeploymentError(
            "Relay is not installed with this configuration; use --install-relay."
        ) from None
    return {"template_sha256": hashlib.sha256(actual).hexdigest(), "access_hash_matches": True}


def install_relay(client, user, password):
    """Functionality: Install the explicitly requested additive Nginx relay configuration.
    Inputs: Pinned client/access settings. Outputs: Backup path and checked existing-app identity.
    Logic: Preserve originals, compare before activation, syntax-check and reload once; any error
    during mutation or reload restores all selected prior files before reporting failure.
    Constraints: No PRS restart, TLS changes, code uploads, migrations or automatic restart/retry.
    """
    import bcrypt

    _, pid_before = remote_command(client, "systemctl show prs-camera --property=MainPID --value")
    template = (ROOT / "deploy/phone-relay.locations.conf").read_bytes()
    digest = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12))
    # Verify crypt support before touching Nginx: only the hash and password travel over SSH stdin.
    verify = (
        "import crypt,hmac,json,sys;d=json.load(sys.stdin);"
        "print(hmac.compare_digest(crypt.crypt(d['password'],d['hash']),d['hash']))"
    )
    _, compatible = remote_command(
        client,
        "python3 -W ignore -c " + shlex.quote(verify),
        body=json.dumps({"password": password, "hash": digest.decode()}),
    )
    if compatible.strip() != b"True":
        raise PhoneDeploymentError(
            "Existing Nginx host does not support the selected password hash."
        )
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
        for name in (INCLUDE, AUTH):
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
            write_remote(sftp, AUTH, user.encode() + b":" + digest + b"\n", 0o640)
            remote_command(client, "chown root:www-data " + shlex.quote(AUTH))
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
                        write_remote(sftp, name, raw, 0o640 if name == AUTH else 0o644)
                        if name == AUTH:
                            remote_command(client, "chown root:www-data " + shlex.quote(AUTH))
                except Exception:
                    restored = False
            detail = ("Previous files restored; reload completion is unconfirmed."
                      if restored else "File restoration incomplete; inspect the relay backup.")
            raise PhoneDeploymentError("Relay installation failed. " + detail) from None
    _, pid_after = remote_command(client, "systemctl show prs-camera --property=MainPID --value")
    if pid_before != pid_after:
        raise PhoneDeploymentError(
            "Existing PRS service identity changed during relay installation."
        )
    verified = verify_relay(client, user, password)
    return {
        "backup": backup,
        "nginx_syntax_valid": True,
        "nginx_reloaded": True,
        "existing_prs_pid_unchanged": True,
        **verified,
    }
