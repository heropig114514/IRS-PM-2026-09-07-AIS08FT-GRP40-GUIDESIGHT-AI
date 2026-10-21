"""Responsibilities: Guard private phone access configuration and additive relay configuration.
Implementation: Fresh temporary backend roots, real bcrypt limits and mock remote boundary;
no existing virtualenv directories, private configuration, server or GPU required.
Related Modules: integration.phone_deploy; tools.run_phone_mvp; deploy/phone-relay.locations.conf.
Declaration Index: PhoneDeployTests; PhoneDeployTests.test_access_creation_preserves_existing;
PhoneDeployTests.test_access_rejects_ambiguity_without_secret_errors;
PhoneDeployTests.test_vhost_is_additive_idempotent_and_rejects_changed_anchor;
PhoneDeployTests.test_remote_binding_requires_exact_loopback;
PhoneDeployTests.test_public_ready_verifies_tls_and_bounded_media_policy;
PhoneDeployTests.test_supervisor_reports_fatal_event_but_accepts_manual_stop;
PhoneDeployTests.test_remote_status_deadline_closes_only_its_channel;
PhoneDeployTests.test_install_failure_restores_all_selected_files;
PhoneDeployTests.test_install_failure_restores_all_selected_files.read;
PhoneDeployTests.test_install_failure_restores_all_selected_files.write;
PhoneDeployTests.test_install_failure_restores_all_selected_files.remove;
PhoneDeployTests.test_install_failure_restores_all_selected_files.rename;
PhoneDeployTests.test_install_failure_restores_all_selected_files.command.
Variable Index: None; fixture credentials are synthetic and never production credentials.
"""

import tempfile
import threading
from pathlib import Path
from unittest.mock import Mock, patch

import bcrypt
from django.test import SimpleTestCase

from tools.run_local_mvp import StartupError
from tools.run_phone_mvp import check_remote_binding, public_ready, supervise

from .phone_deploy import (
    ANCHOR,
    AUTH,
    INCLUDE,
    INCLUDE_LINE,
    VHOST,
    PhoneDeploymentError,
    install_relay,
    prepare_vhost,
    private_access,
    remote_command,
)
from .relay import RelayError


class PhoneDeployTests(SimpleTestCase):
    """Functionality: Protect exact deployment boundaries and secret handling.
    Logic: Use temporary files and synthetic remote replies. Constraints: No actual SSH/TLS/GPU.
    """

    def test_remote_status_deadline_closes_only_its_channel(self):
        """Functionality: Refuse a hung SSH exit status after bounded output reads.
        Inputs: Synthetic ready output but missing exit status. Outputs: Fixed failure.
        Logic: Advance the deadline without sleeping, require closure of the owned channel.
        Constraints: No real SSH, retry, client closure or private exception contents.
        """
        client, stdin, stdout, stderr = Mock(), Mock(), Mock(), Mock()
        client.exec_command.return_value = stdin, stdout, stderr
        stdout.read.return_value = stderr.read.return_value = b""
        stdout.channel.exit_status_ready.return_value = False
        with (patch("integration.phone_deploy.time.monotonic", side_effect=[0, 26]),
              self.assertRaises(PhoneDeploymentError) as caught):
            remote_command(client, "nginx -t")
        self.assertIn("deadline", str(caught.exception))
        stdout.channel.close.assert_called_once_with()
        stdout.channel.recv_exit_status.assert_not_called()
        client.close.assert_not_called()

    def test_access_creation_preserves_existing(self):
        """Functionality: Verify exclusive creation, stable credentials and the bcrypt boundary.
        Inputs: Fresh temporary backend root and a synthetic 72-byte password. Outputs: Preserved
        identity, a usable boundary hash and rejection of creation outside the allowed root.
        Logic: Patch only the configured root; use real filesystem operations and bcrypt.
        Constraints: No existing virtualenv or private files; fast hash cost is test-only.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "backend"
            root.mkdir()
            with patch("integration.phone_deploy.ROOT", root):
                path = root / "phone.env"
                first = private_access(path, create=True)
                original = path.read_bytes()
                self.assertEqual(first[0], "phone")
                self.assertGreaterEqual(len(first[1]), 20)
                self.assertEqual(private_access(path, create=True), first)
                self.assertEqual(path.read_bytes(), original)
                boundary = "x" * 72
                path.write_text(
                    f"PHONE_ACCESS_USER=phone\nPHONE_ACCESS_PASSWORD={boundary}\n", encoding="utf-8"
                )
                self.assertEqual(private_access(path), ("phone", boundary))
                digest = bcrypt.hashpw(boundary.encode(), bcrypt.gensalt(rounds=4))
                self.assertTrue(bcrypt.checkpw(boundary.encode(), digest))
                outside = root.parent / "outside.env"
                with self.assertRaises(PhoneDeploymentError):
                    private_access(outside, create=True)
                self.assertFalse(outside.exists())

    def test_access_rejects_ambiguity_without_secret_errors(self):
        """Functionality: Refuse duplicate/unsafe credentials without echoing contents.
        Inputs: Synthetic malformed files, including a 73-byte password. Outputs: Fixed errors.
        Logic: Reject bcrypt-incompatible lengths before hashing.
        Constraints: No production password material.
        """
        secret = "synthetic-private-credential-value"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "phone.env"
            for raw in [
                f"PHONE_ACCESS_USER=phone\nPHONE_ACCESS_PASSWORD={secret}\n"
                f"PHONE_ACCESS_PASSWORD={secret}\n",
                f"PHONE_ACCESS_USER=phone:bad\nPHONE_ACCESS_PASSWORD={secret}\n",
                "PHONE_ACCESS_USER=phone\nPHONE_ACCESS_PASSWORD=short\n",
                "PHONE_ACCESS_USER=phone\nPHONE_ACCESS_PASSWORD=" + "x" * 73 + "\n",
            ]:
                path.write_text(raw, encoding="utf-8")
                with self.assertRaises(PhoneDeploymentError) as caught:
                    private_access(path)
                self.assertNotIn(secret, str(caught.exception))
                self.assertEqual(str(caught.exception), "Invalid dedicated phone access file.")

    def test_vhost_is_additive_idempotent_and_rejects_changed_anchor(self):
        """Functionality: Preserve unrelated server bytes and insert exactly once.
        Inputs: Synthetic virtual host. Outputs: Additive candidate or explicit refusal.
        Logic: Compare byte preservation around the single owned marker.
        Constraints: No whole-config generation or alternate insertion heuristic.
        """
        original = b"server {\n listen 443 ssl;\n " + ANCHOR.encode() + b"\n # unrelated\n}\n"
        candidate = prepare_vhost(original)
        self.assertEqual(candidate.count(INCLUDE_LINE.encode()), 1)
        self.assertIn(b"# unrelated", candidate)
        self.assertEqual(prepare_vhost(candidate), candidate)
        self.assertEqual(candidate.replace(b"\n    " + INCLUDE_LINE.encode(), b""), original)
        for bad in [b"server {}", original + original]:
            with self.assertRaises(PhoneDeploymentError):
                prepare_vhost(bad)

    def test_remote_binding_requires_exact_loopback(self):
        """Functionality: Reject wildcard/duplicate reverse listeners.
        Inputs: Explicit ss replies. Outputs: Success or fixed startup failure.
        Logic: Inspect actual parsed local-address field, not the wildcard peer field.
        Constraints: No real listener or SSH commands.
        """
        good = b"LISTEN 0 128 127.0.0.1:18766 0.0.0.0:*\n"
        with patch("tools.run_phone_mvp.remote_command", return_value=(0, good)):
            check_remote_binding(Mock())
        for bad in [b"", good + good, good.replace(b"127.0.0.1:18766", b"0.0.0.0:18766")]:
            with patch("tools.run_phone_mvp.remote_command", return_value=(0, bad)):
                with self.assertRaises(StartupError):
                    check_remote_binding(Mock())

    def test_public_ready_verifies_tls_and_bounded_media_policy(self):
        """Functionality: Require a real HTTPS client contract and explicit public media policy.
        Inputs: Synthetic HTTP response metadata. Outputs: Safe readiness, then rejected bad page.
        Logic: Inspect adapter boundary while keeping credential outside result/errors.
        Constraints: Does not claim certificate or browser verification from a mock.
        """
        response = Mock(status=200)
        response.read.return_value = b'<html data-assist="true">'
        response.getheader.side_effect = lambda name, default="": {
            "Content-Security-Policy": "connect-src 'self' wss://47.239.50.129;",
            "Permissions-Policy": "camera=(self), geolocation=(self), microphone=()",
        }.get(name, default)
        with patch("tools.run_phone_mvp.http.client.HTTPSConnection") as factory:
            factory.return_value.getresponse.return_value = response
            result = public_ready("phone", "synthetic-private-password")
            self.assertTrue(result["https_verified"])
            self.assertNotIn("password", str(result))
            self.assertEqual(factory.call_args.args, ("47.239.50.129",))
            response.read.return_value = b"unrelated page"
            with self.assertRaises(StartupError):
                public_ready("phone", "synthetic-private-password")

    def test_supervisor_reports_fatal_event_but_accepts_manual_stop(self):
        """Functionality: Distinguish a fatal relay wakeup from manual cancellation.
        Inputs: Shared stop event and explicit relay observations. Outputs: Fatal error or None.
        Logic: A fatal relay sets the same event as Ctrl+C before the supervisor enters its loop.
        Constraints: No threads, children, SSH connection or real signal are started.
        """
        stopped = threading.Event()
        stopped.set()
        relay = Mock()
        with patch("tools.run_phone_mvp.STOP_REQUESTED", stopped):
            relay.check_active.side_effect = RelayError("ssh_disconnected")
            with self.assertRaisesRegex(RelayError, "ssh_disconnected"):
                supervise(relay, [])
            relay.check_active.assert_called_once_with()
            relay.reset_mock()
            relay.check_active.side_effect = None
            relay.check_active.return_value = False
            self.assertIsNone(supervise(relay, []))
            relay.check_active.assert_called_once_with()

    def test_install_failure_restores_all_selected_files(self):
        """Functionality: Restore every selected disk file after partial installation failure.
        Inputs: Synthetic SFTP and failures after writes, rename, syntax operation or reload.
        Outputs: Original bytes/absence, safe error and at most one reload operation.
        Logic: Inject failure once after its possible side effect for new/existing relay files.
        Constraints: No remote operations, credentials, reload retry or unrelated-file mutation.
        """
        for existing in (False, True):
            for point in ("include", "auth", "chown", "rename", "syntax", "status", "reload"):
                with self.subTest(existing=existing, failure=point):
                    original = b"server {\n " + ANCHOR.encode() + b"\n}\n"
                    if existing:
                        original = prepare_vhost(original)
                    files = {VHOST: original, "/etc/nginx/unrelated.conf": b"unchanged"}
                    if existing:
                        files.update({INCLUDE: b"old relay", AUTH: b"old access hash"})
                    before = dict(files)
                    failed, commands = [], []
                    client, sftp = Mock(), Mock()
                    client.open_sftp.return_value.__enter__ = Mock(return_value=sftp)
                    client.open_sftp.return_value.__exit__ = Mock(return_value=False)
                    sftp.normalize.return_value = VHOST

                    def read(owner, path, limit=65536, *, files=files):
                        """Functionality: Read finite fixture bytes. Inputs: path/limit/owner.
                        Outputs: Bytes or absence. Logic: Exact mapping lookup.
                        Constraints: No remote or filesystem I/O; no fabricated missing-file bytes.
                        """
                        if path not in files:
                            raise FileNotFoundError
                        return files[path]

                    def write(owner, path, raw, mode, *, files=files, point=point, failed=failed):
                        """Functionality: Model a write that may partially succeed before failure.
                        Inputs: Explicit selected fixture path/bytes/mode. Outputs: Mutation/error.
                        Logic: Fail once after the selected live write.
                        Constraints: Backup and restoration writes remain independently observable.
                        """
                        files[path] = raw
                        if not failed and ((point == "include" and path == INCLUDE)
                                           or (point == "auth" and path == AUTH)):
                            failed.append(True)
                            raise OSError("synthetic-private-error")

                    def remove(path, *, files=files):
                        """Functionality: Remove one fixture file. Inputs: path. Outputs: Absence.
                        Logic: Missing paths raise FileNotFoundError.
                        Constraints: No real file deletion or broad directory operation.
                        """
                        if path not in files:
                            raise FileNotFoundError
                        del files[path]

                    def rename(source, target, *, files=files, point=point, failed=failed):
                        """Functionality: Model atomic replacement and uncertain completion.
                        Inputs: Fixture source/target. Outputs: Replaced bytes or injected failure.
                        Logic: Rename first, fail once so rollback cannot assume no side effect.
                        Constraints: Restoration rename is permitted without replaying the failure.
                        """
                        files[target] = files.pop(source)
                        if point == "rename" and not failed:
                            failed.append(True)
                            raise OSError("synthetic-private-error")

                    def command(owner, value, *, body=None, check=True, point=point,
                                failed=failed, commands=commands):
                        """Functionality: Model bounded inspected remote control responses.
                        Inputs: Exact command and optional synthetic stdin. Outputs: Status/bytes.
                        Logic: Record commands and inject failure once at the selected operation.
                        Constraints: No shell, network, real password or automatic command retry.
                        """
                        commands.append(value)
                        selected = ((point == "chown" and value.startswith("chown "))
                                    or (point == "syntax" and value == "nginx -t")
                                    or (point == "reload" and value == "systemctl reload nginx"))
                        if selected and not failed:
                            failed.append(True)
                            raise OSError("synthetic-private-error")
                        if value == "nginx -t" and point == "status":
                            return 1, b""
                        return 0, b"True\n" if value.startswith("python3 ") else b"321\n"

                    sftp.remove.side_effect = remove
                    sftp.posix_rename.side_effect = rename
                    with (patch("integration.phone_deploy.read_remote", side_effect=read),
                          patch("integration.phone_deploy.write_remote", side_effect=write),
                          patch("integration.phone_deploy.remote_command", side_effect=command),
                          patch("bcrypt.hashpw", return_value=b"synthetic-hash"),
                          self.assertRaises(PhoneDeploymentError) as caught):
                        install_relay(client, "phone", "synthetic-private-password")
                    self.assertIn("Previous files restored", str(caught.exception))
                    self.assertNotIn("synthetic-private", str(caught.exception))
                    for path in (VHOST, INCLUDE, AUTH, "/etc/nginx/unrelated.conf"):
                        self.assertEqual(files.get(path), before.get(path), path)
                    self.assertLessEqual(commands.count("systemctl reload nginx"), 1)
                    self.assertFalse(any("restart" in value for value in commands))
