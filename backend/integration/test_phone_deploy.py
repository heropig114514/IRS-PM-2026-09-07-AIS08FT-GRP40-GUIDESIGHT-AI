"""Responsibilities: Guard public guest phone relay configuration and its deployment boundary.
Implementation: Exact synthetic templates and mock remote boundary;
no existing virtualenv directories, private configuration, server or GPU required.
Related Modules: integration.phone_deploy; tools.run_phone_mvp; deploy/phone-relay.locations.conf.
Declaration Index: PhoneDeployTests;
PhoneDeployTests.test_guest_relay_verification_requires_no_hash;
PhoneDeployTests.test_guest_relay_verification_requires_no_hash.read;
PhoneDeployTests.test_guest_relay_verification_rejects_legacy_hash;
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
Variable Index: None; relay hashes, when present in fixtures, are synthetic recovery fixtures only.
"""

import threading
from unittest.mock import MagicMock, Mock, patch

from django.test import SimpleTestCase

from tools.run_local_mvp import StartupError
from tools.run_phone_mvp import check_remote_binding, public_ready, supervise

from .phone_deploy import (
    ANCHOR,
    INCLUDE,
    INCLUDE_LINE,
    LEGACY_AUTH,
    ROOT,
    VHOST,
    PhoneDeploymentError,
    install_relay,
    prepare_vhost,
    remote_command,
    verify_relay,
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

    def test_guest_relay_verification_requires_no_hash(self):
        """Functionality: Verify a public guest relay without an access credential.
        Inputs: Exact template/include fixture and one absent legacy-hash path.
        Outputs: Safe guest-access metadata. Logic: compare only owned public configuration.
        Constraints: No private access file, remote server or browser request.
        """
        client = MagicMock()
        template = (ROOT / "deploy/phone-relay.locations.conf").read_bytes()
        files = {INCLUDE: template, VHOST: (ANCHOR + "\n    " + INCLUDE_LINE).encode()}

        def read(_sftp, path, limit=65536):
            """Functionality: Model a relay with public configuration and no retired credential.
            Inputs: unused SFTP owner, path and byte limit. Outputs: fixture bytes or missing file.
            Logic: only the legacy credential path is absent. Constraints: no filesystem or SSH.
            """
            if path == LEGACY_AUTH:
                raise FileNotFoundError
            return files[path]

        with patch("integration.phone_deploy.read_remote", side_effect=read):
            result = verify_relay(client)
        self.assertTrue(result["guest_access"])
        self.assertTrue(result["legacy_access_hash_removed"])
        self.assertNotIn("password", str(result))
        client.open_sftp.return_value.__exit__.assert_called_once()

    def test_guest_relay_verification_rejects_legacy_hash(self):
        """Functionality: Refuse an installation that still enforces or retains the old login.
        Inputs: Exact current template plus a synthetic legacy hash file.
        Outputs: Fixed install-required error. Logic: fail before local service startup.
        Constraints: Fixture hash is synthetic; no authentication attempt or secret disclosure.
        """
        client = MagicMock()
        files = {
            INCLUDE: (ROOT / "deploy/phone-relay.locations.conf").read_bytes(),
            VHOST: (ANCHOR + "\n    " + INCLUDE_LINE).encode(),
            LEGACY_AUTH: b"phone:synthetic-hash",
        }

        with patch(
            "integration.phone_deploy.read_remote",
            side_effect=lambda _owner, path, limit=65536: files[path],
        ):
            with self.assertRaises(PhoneDeploymentError):
                verify_relay(client)
        client.open_sftp.return_value.__exit__.assert_called_once()

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
        good = b"LISTEN 0 128 127.0.0.1:18767 0.0.0.0:*\n"
        with patch("tools.run_phone_mvp.remote_command", return_value=(0, good)):
            check_remote_binding(Mock())
        for bad in [b"", good + good, good.replace(b"127.0.0.1:18767", b"0.0.0.0:18767")]:
            with patch("tools.run_phone_mvp.remote_command", return_value=(0, bad)):
                with self.assertRaises(StartupError):
                    check_remote_binding(Mock())

    def test_public_ready_verifies_tls_and_bounded_media_policy(self):
        """Functionality: Require a real HTTPS client contract and explicit public media policy.
        Inputs: Synthetic HTTP response metadata. Outputs: Safe readiness, then rejected bad page.
        Logic: Inspect the unauthenticated HTTPS adapter while keeping response content outside
        logs.
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
            result = public_ready()
            self.assertTrue(result["https_verified"])
            self.assertNotIn("password", str(result))
            self.assertEqual(factory.call_args.args, ("47.239.50.129",))
            factory.return_value.request.assert_called_once_with("GET", "/mvp/maps/")
            response.read.return_value = b"unrelated page"
            with self.assertRaises(StartupError):
                public_ready()

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
            for point in ("include", "rename", "syntax", "status", "reload"):
                with self.subTest(existing=existing, failure=point):
                    original = b"server {\n " + ANCHOR.encode() + b"\n}\n"
                    if existing:
                        original = prepare_vhost(original)
                    files = {VHOST: original, "/etc/nginx/unrelated.conf": b"unchanged"}
                    if existing:
                        files.update({INCLUDE: b"old relay", LEGACY_AUTH: b"old access hash"})
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
                        if not failed and point == "include" and path == INCLUDE:
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
                        selected = ((point == "syntax" and value == "nginx -t")
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
                          self.assertRaises(PhoneDeploymentError) as caught):
                        install_relay(client)
                    self.assertIn("Previous files restored", str(caught.exception))
                    for path in (VHOST, INCLUDE, LEGACY_AUTH, "/etc/nginx/unrelated.conf"):
                        self.assertEqual(files.get(path), before.get(path), path)
                    self.assertLessEqual(commands.count("systemctl reload nginx"), 1)
                    self.assertFalse(any("restart" in value for value in commands))
