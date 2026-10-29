"""Responsibilities: Verify portable server-relay startup and explicit network boundaries.
Implementation: Mock transport/process boundaries and exercise real validation/CLI composition.
Related Modules: tools.local_demo; tools.run_phone_mvp; integration.test_phone_deploy.
Declaration Index: PhoneEntryTests; PhoneEntryTests.test_phone_command;
PhoneEntryTests.test_check_does_not_authenticate; PhoneEntryTests.test_invalid_routes;
PhoneEntryTests.test_public_https_proxy; PhoneEntryTests.test_prompted_ssh;
PhoneEntryTests.test_failed_ssh_closes_owned_resources;
PhoneEntryTests.test_proxy_requires_prompt; PhoneEntryTests.test_warmup_failure_cleans_worker.
Variable Index: None; all passwords and endpoints in these tests are synthetic.
"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools import local_demo as demo
from tools import run_phone_mvp as phone


class PhoneEntryTests(unittest.TestCase):
    """Functionality: Guard teammate startup against routing ambiguity and resource leaks.
    Logic: Assert externally observable command, transport and cleanup contracts.
    Constraints: Mocks cannot establish real SSH, GPU speed or physical phone acceptance.
    """

    def test_phone_command(self):
        """Functionality: Keep GPU local and the phone Web application on the relay server.
        Inputs: Explicit public/proxy choices. Outputs: Single GPU/tunnel supervisor command.
        Logic: Execute profile composition. Constraints: No launch, secret or Web process.
        """
        args = demo.parse_args([
            "phone", "--public-origin", "https://entry.example", "--password-prompt",
            "--http-proxy", "http://127.0.0.1:7897", "--inference-port", "8877", "--check",
        ])
        with patch.object(Path, "is_file", return_value=True):
            command, environment = demo.launch_command(args)
        self.assertIn("tools.run_phone_mvp", command)
        for flag in ("--warmup", "--password-prompt", "--check"):
            self.assertIn(flag, command)
        self.assertEqual(command[command.index("--public-origin") + 1], "https://entry.example")
        self.assertEqual(command[command.index("--inference-port") + 1], "8877")
        self.assertNotIn("--web-port", command)
        self.assertNotIn("PRS_CAMERA_PROFILE", environment)
        self.assertEqual(environment["PRS_DEPTH_BACKEND"], "torch")
        self.assertEqual(environment["PRS_DEPTH_FUSION"], "host")

    def test_check_does_not_authenticate(self):
        """Functionality: Allow teammates to preflight without server credentials or side effects.
        Inputs: Check arguments and prepared synthetic dependencies. Outputs: No auth or launch.
        Logic: Run the real preflight transaction. Constraints: No GPU or private file access.
        """
        args = SimpleNamespace(check=True, inference_port=8877, log_dir=phone.ROOT / ".venv/tmp")
        with (
            patch.object(phone, "python_path", return_value=Path("python")),
            patch.object(phone, "inspect_environment"), patch.object(phone, "verify_models"),
            patch.object(phone, "verify_inference_port"),
            patch.object(phone, "connect_server") as connect,
            patch.object(phone, "connect_interactive") as prompted,
            patch.object(phone, "launch") as launch,
        ):
            phone.run(args)
        connect.assert_not_called()
        prompted.assert_not_called()
        launch.assert_not_called()

    def test_invalid_routes(self):
        """Functionality: Reject ambiguous or credential-bearing destinations before network I/O.
        Inputs: Invalid proxy/public origins. Outputs: Fixed validation errors.
        Logic: Exercise production validators. Constraints: No DNS request or credential logging.
        """
        for value in ("https://proxy:1", "http://user:secret@proxy:1", "http://proxy",
                      "http://proxy:0", "http://proxy:99999", "http://proxy:1/path",
                      "http://proxy:1#fragment", "http://proxy:1?query", "http://*:1"):
            with self.subTest(value=value), self.assertRaises(phone.StartupError):
                phone.proxy_endpoint(value)
        self.assertEqual(phone.proxy_endpoint("http://127.0.0.1:7897"), ("127.0.0.1", 7897))
        self.assertIsNone(phone.proxy_endpoint(None))
        with patch.object(phone.http.client, "HTTPSConnection") as factory:
            with self.assertRaises(ValueError):
                phone.public_ready("http://entry.example")
        factory.assert_not_called()

    def test_public_https_proxy(self):
        """Functionality: Validate the chosen public origin using CA-verified HTTPS over CONNECT.
        Inputs: Synthetic admitted page/policy. Outputs: One HTTPS tunnel to that origin.
        Logic: Inspect constructor and target. Constraints: No custom unverified TLS or fallback.
        """
        response = Mock(status=200)
        response.read.return_value = b'<html data-assist="true">'
        response.getheader.side_effect = lambda key, default: {
            "Content-Security-Policy": "connect-src 'self' wss://entry.example;",
            "Permissions-Policy": "camera=(self), geolocation=(self)",
        }.get(key, default)
        with patch.object(phone.http.client, "HTTPSConnection") as factory:
            factory.return_value.getresponse.return_value = response
            self.assertTrue(phone.public_ready("https://entry.example", ("127.0.0.1", 7897))[
                "https_verified"])
            factory.assert_called_once_with("127.0.0.1", 7897, timeout=15)
            factory.return_value.set_tunnel.assert_called_once_with("entry.example", 443)
            factory.return_value.close.assert_called_once()

    def test_prompted_ssh(self):
        """Functionality: Pin SSH identity while keeping prompted credentials out of argv/files.
        Inputs: Synthetic password and proxy socket. Outputs: One fixed-target SSH connection.
        Logic: Verify ownership transfer and disabled discovery. Constraints: No real credential.
        """
        stream = Mock()
        with (
            patch.object(phone.getpass, "getpass", return_value="synthetic"),
            patch.object(phone.paramiko, "SSHClient") as factory,
            patch.object(phone.http.client, "HTTPConnection") as proxy,
        ):
            proxy.return_value.sock = stream
            client = phone.connect_interactive(("127.0.0.1", 7897))
            self.assertIs(client, factory.return_value)
            proxy.return_value.set_tunnel.assert_called_once_with("47.239.50.129", 22)
            self.assertIsNone(proxy.return_value.sock)
            self.assertIsInstance(client.set_missing_host_key_policy.call_args.args[0],
                                  phone.PinnedHost)
            self.assertIs(client.connect.call_args.kwargs["sock"], stream)
            self.assertFalse(client.connect.call_args.kwargs["look_for_keys"])
            self.assertFalse(client.connect.call_args.kwargs["allow_agent"])
            client.close.assert_not_called()

    def test_failed_ssh_closes_owned_resources(self):
        """Functionality: Release a proxy socket when pinned SSH authentication fails.
        Inputs: Synthetic authentication failure. Outputs: Both owned resources closed once.
        Logic: Exercise the failure transaction. Constraints: No reconnect or alternate route.
        """
        stream = Mock()
        with (
            patch.object(phone.getpass, "getpass", return_value="synthetic"),
            patch.object(phone.paramiko, "SSHClient") as factory,
            patch.object(phone.http.client, "HTTPConnection") as proxy,
        ):
            proxy.return_value.sock = stream
            factory.return_value.connect.side_effect = OSError("synthetic")
            with self.assertRaises(OSError):
                phone.connect_interactive(("127.0.0.1", 7897))
            factory.return_value.close.assert_called_once()
            factory.return_value.connect.assert_called_once()
            stream.close.assert_called_once()

    def test_proxy_requires_prompt(self):
        """Functionality: Reject an implicit credential path when a proxy is selected.
        Inputs: Proxy without prompt flag. Outputs: Early actionable error.
        Logic: Validation precedes model/SSH checks. Constraints: No credential file read.
        """
        with patch.object(phone, "connect_server") as connect:
            with self.assertRaisesRegex(phone.StartupError, "password-prompt"):
                phone.run(SimpleNamespace(http_proxy="http://127.0.0.1:7897"))
        connect.assert_not_called()

    def test_warmup_failure_cleans_worker(self):
        """Functionality: Prevent publishing a tunnel when real model prewarming fails.
        Inputs: Owned fake child and failed warmup. Outputs: Cleanup without remote binding.
        Logic: Execute startup until warmup. Constraints: No test inference or SSH traffic.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client, child = Mock(), Mock()
            args = SimpleNamespace(check=False, inference_port=8877, log_dir=root, warmup=True)
            with (
                patch.object(phone, "ROOT", root),
                patch.object(phone, "python_path", return_value=Path("python")),
                patch.object(phone, "inspect_environment"), patch.object(phone, "verify_models"),
                patch.object(phone, "verify_inference_port"),
                patch.object(phone, "connect_server", return_value=client),
                patch.object(phone, "check_web_service"),
                patch.object(phone, "child_environment", return_value={
                    "PRS_DEPTH_BACKEND": "torch",
                }),
                patch.object(phone, "launch", return_value=child),
                patch.object(phone, "wait_listener"),
                patch("tools.run_public_mvp.warmup", side_effect=phone.StartupError("warmup")),
                patch.object(phone, "RelayForward") as relay,
                patch.object(phone, "stop_children") as stop,
            ):
                with self.assertRaisesRegex(phone.StartupError, "warmup"):
                    phone.run(args)
                relay.assert_not_called()
                stop.assert_called_once_with([child])
                client.close.assert_called_once()
