"""Responsibilities: Verify portable server-relay startup and explicit network boundaries.
Implementation: Mock transport/process boundaries and exercise real validation/CLI composition.
Related Modules: tools.local_demo; tools.run_phone_mvp; integration.test_phone_deploy.
Declaration Index: PhoneEntryTests; PhoneEntryTests.test_phone_command;
PhoneEntryTests.test_check_does_not_authenticate; PhoneEntryTests.test_invalid_routes;
PhoneEntryTests.test_public_https_proxy; PhoneEntryTests.test_prompted_ssh;
PhoneEntryTests.test_failed_ssh_closes_owned_resources;
PhoneEntryTests.test_proxy_requires_prompt; PhoneEntryTests.test_warmup_failure_cleans_worker;
PhoneEntryTests.test_session_ports_are_bounded; PhoneEntryTests.test_multi_check_has_no_services;
PhoneEntryTests.test_three_workers_have_distinct_processes_and_pool;
PhoneEntryTests.test_partial_multi_warmup_failure_cleans_every_child;
PhoneEntryTests.test_pool_readiness_refuses_partial_capacity;
PhoneEntryTests.test_remote_pool_capacity_is_verified.
Variable Index: None; all passwords and endpoints in these tests are synthetic.
"""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

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
        self.assertEqual(command[command.index("--sessions") + 1], "1")
        args = demo.parse_args(["phone", "--sessions", "3", "--check"])
        with patch.object(Path, "is_file", return_value=True):
            command, _ = demo.launch_command(args)
        self.assertEqual(command[command.index("--sessions") + 1], "3")

    def test_session_ports_are_bounded(self):
        """Functionality: Protect the primary listener and all adjacent worker port allocations.
        Inputs: Valid and invalid session/range selections. Outputs: Fixed ports or early rejection.
        Logic: Check numeric type and complete range before socket probing.
        Constraints: No listener, process or alternate-port selection occurs.
        """
        self.assertEqual(phone.inference_ports(8767), (8767,))
        self.assertEqual(phone.inference_ports(8767, 2), (8768, 8769))
        self.assertEqual(phone.inference_ports(8767, 3), (8768, 8769, 8770))
        for port, sessions in ((8767, 0), (8767, 4), (8767, True), (True, 1),
                               (1023, 3), (65533, 3), (65535, 2), ("8767", 1)):
            with self.subTest(port=port, sessions=sessions), self.assertRaises(phone.StartupError):
                phone.inference_ports(port, sessions)

    def test_multi_check_has_no_services(self):
        """Functionality: Validate every assigned port before multi-phone startup.
        Inputs: Three-session check and mocked prepared runtimes. Outputs: Four port checks only.
        Logic: Run actual preflight while authentication/process adapters are observable.
        Constraints: No CUDA model execution, SSH, private credential file or log directory write.
        """
        args = SimpleNamespace(check=True, sessions=3, inference_port=8767,
                               log_dir=phone.ROOT / ".venv/tmp")
        with (patch.object(phone, "python_path", return_value=Path("python")),
              patch.object(phone, "inspect_environment"), patch.object(phone, "verify_models"),
              patch.object(phone, "verify_inference_port") as port,
              patch.object(phone, "connect_server") as connect,
              patch.object(phone, "launch") as launch):
            phone.run(args)
        self.assertEqual(port.call_args_list, [call(value) for value in (8767, 8768, 8769, 8770)])
        connect.assert_not_called()
        launch.assert_not_called()

    def test_three_workers_have_distinct_processes_and_pool(self):
        """Functionality: Keep three phone trackers in separate native processes behind one hub.
        Inputs: Three-session selection and independent fake child/health boundaries.
        Outputs: Three warmed GPU workers, one Web-runtime pool and scoped ownership metadata.
        Logic: Assert stable ports, interpreter separation and immutable child environments.
        Constraints: No real processes, model sharing, GPU inference or tunnel is started.
        """
        ready = {"status": "ok", "provider": "local", "loaded": True, "ready": True,
                 "busy": False}
        pool = {**ready, "capacity": 3, "active_sessions": 0, "available_sessions": 3,
                "accept_available": True}
        children, handles, report = [], [], {}
        args = SimpleNamespace(sessions=3, inference_port=8767, warmup=False)
        environment = {"LOCAL_WEB_ORIGIN": "http://127.0.0.1:18766",
                       "PRS_DEPTH_BACKEND": "torch"}
        with tempfile.TemporaryDirectory() as temporary:
            try:
                with (patch.object(phone, "launch", side_effect=[Mock() for _ in range(4)])
                      as launch, patch.object(phone, "wait_listener") as wait,
                      patch.object(phone, "read_health", side_effect=[ready, ready, ready, pool]),
                      patch("tools.run_public_mvp.warmup", new_callable=AsyncMock,
                            return_value=[{"server_ms": 1}] * 8) as warmup):
                    phone.start_inference(args, Path("web"), Path("gpu"), environment,
                                          Path(temporary), children, handles, report)
                self.assertEqual(len(children), 4)
                self.assertEqual([item.args[2] for item in launch.call_args_list],
                                 [8768, 8769, 8770, 8767])
                self.assertEqual([item.args[0] for item in launch.call_args_list],
                                 [Path("gpu")] * 3 + [Path("web")])
                self.assertEqual([item.args[1] for item in launch.call_args_list],
                                 ["integration.inference.asgi:application"] * 3
                                 + ["integration.inference.pool_asgi:application"])
                self.assertEqual(launch.call_args_list[-1].args[3]["PRS_INFERENCE_POOL_PORTS"],
                                 "8768,8769,8770")
                self.assertNotIn("PRS_INFERENCE_POOL_PORTS", environment)
                self.assertEqual(len({id(item.args[3]) for item in launch.call_args_list}), 4)
                self.assertEqual([item.args[1] for item in warmup.call_args_list],
                                 [8768, 8769, 8770])
                self.assertEqual(wait.call_count, 4)
                self.assertEqual(len(report["worker_warmups"]), 3)
                self.assertTrue(all(len(item["frames"]) == 8
                                    for item in report["worker_warmups"]))
                self.assertTrue(report["local_inference_health"]["model_loaded"])
            finally:
                for handle in handles:
                    handle.close()

    def test_partial_multi_warmup_failure_cleans_every_child(self):
        """Functionality: Refuse pool publication after one native worker fails warmup.
        Inputs: Successful first worker and failure while warming the second.
        Outputs: Both owned children cleaned, SSH closed and no pool or tunnel published.
        Logic: Exercise the supervisor transaction rather than an isolated cleanup helper.
        Constraints: No real CUDA, SSH, retries, unrelated process termination or partial service.
        """
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = SimpleNamespace(check=False, sessions=3, inference_port=8767, log_dir=root)
            client, first, second = Mock(), Mock(), Mock()
            health = {"status": "ok", "provider": "local", "loaded": True,
                      "ready": True, "busy": False}
            with (patch.object(phone, "ROOT", root),
                  patch.object(phone, "python_path", return_value=Path("python")),
                  patch.object(phone, "inspect_environment"), patch.object(phone, "verify_models"),
                  patch.object(phone, "verify_inference_port"),
                  patch.object(phone, "connect_server", return_value=client),
                  patch.object(phone, "check_web_service"),
                  patch.object(phone, "child_environment", return_value={}),
                  patch.object(phone, "launch", side_effect=[first, second]) as launch,
                  patch.object(phone, "wait_listener"),
                  patch.object(phone, "read_health", return_value=health),
                  patch("tools.run_public_mvp.warmup", new_callable=AsyncMock,
                        side_effect=[[{}] * 8, phone.StartupError("worker warmup failed")]),
                  patch.object(phone, "RelayForward") as relay,
                  patch.object(phone, "stop_children") as stop):
                with self.assertRaisesRegex(phone.StartupError, "warmup failed"):
                    phone.run(args)
            self.assertEqual(launch.call_count, 2)
            stop.assert_called_once_with([first, second])
            client.close.assert_called_once_with()
            relay.assert_not_called()
            state = json.loads(next(root.glob("*/runtime.json")).read_text())
            self.assertFalse(state["running"])
            self.assertEqual(state["session_capacity"], 3)
            self.assertEqual(state["worker_ports"], [8768, 8769, 8770])

    def test_pool_readiness_refuses_partial_capacity(self):
        """Functionality: Publish only the exact warmed and idle pool requested by the operator.
        Inputs: Valid health and one-field invalid variants. Outputs: Success or startup error.
        Logic: Do not accept booleans as integer counts or over/underclaimed capacity.
        Constraints: No health retry, model fallback or live readiness assertion from fixtures.
        """
        health = {"status": "ok", "provider": "local", "loaded": True, "ready": True,
                  "busy": False, "capacity": 3, "active_sessions": 0,
                  "available_sessions": 3, "accept_available": True}
        phone.verify_ready_capacity(health, 3)
        for changed in ({"ready": False}, {"loaded": False}, {"busy": True}, {"capacity": 2},
                        {"capacity": True}, {"active_sessions": 1}, {"active_sessions": False},
                        {"available_sessions": 2}, {"accept_available": False},
                        {"provider": "remote"}):
            with self.subTest(changed=changed), self.assertRaises(phone.StartupError):
                phone.verify_ready_capacity({**health, **changed}, 3)

    def test_remote_pool_capacity_is_verified(self):
        """Functionality: Confirm the server tunnel reaches the expected multi-session pool.
        Inputs: Synthetic remote health. Outputs: Ready metadata or mismatch failure.
        Logic: The single-session contract stays compatible while pools require exact capacity.
        Constraints: No actual SSH or frame transmission.
        """
        health = {"status": "ok", "provider": "local", "loaded": True, "ready": True,
                  "busy": False, "capacity": 3, "active_sessions": 0,
                  "available_sessions": 3, "accept_available": True}
        with patch.object(phone, "remote_command", return_value=(0, json.dumps(health).encode())):
            self.assertTrue(phone.check_remote_health(Mock(), 3)["model_ready"])
        active = {**health, "active_sessions": 1, "available_sessions": 2}
        with patch.object(phone, "remote_command", return_value=(0, json.dumps(active).encode())):
            self.assertTrue(phone.check_remote_health(Mock(), 3)["model_ready"])
        active = {**health, "active_sessions": 3, "available_sessions": 0,
                  "busy": True, "accept_available": False}
        with patch.object(phone, "remote_command", return_value=(0, json.dumps(active).encode())):
            self.assertTrue(phone.check_remote_health(Mock(), 3)["model_ready"])
        health["capacity"] = 2
        with patch.object(phone, "remote_command", return_value=(0, json.dumps(health).encode())):
            with self.assertRaisesRegex(phone.StartupError, "capacity"):
                phone.check_remote_health(Mock(), 3)

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
