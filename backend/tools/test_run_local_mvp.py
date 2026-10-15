"""Responsibilities: Verify local launcher isolation, prerequisite failure and child ownership.
Implementation: Mock process/runtime probes and temporary private files; no GPU/server/API calls.
Related Modules: tools.run_local_mvp; Django test discovery also includes this unittest module.
Declaration Index: LocalLauncherTests;
LocalLauncherTests.test_external_credentials_are_empty_without_mutating_parent;
LocalLauncherTests.test_existing_private_key_is_preserved;
LocalLauncherTests.test_invalid_existing_key_is_not_replaced;
LocalLauncherTests.test_failed_check_does_not_prepare_key_or_launch;
LocalLauncherTests.test_duplicate_ports_fail_before_binding;
LocalLauncherTests.test_cleanup_terminates_only_owned_live_children;
LocalLauncherTests.test_cuda_local_version_suffix_is_accepted;
LocalLauncherTests.test_readonly_probe_uses_project_offline_settings;
LocalLauncherTests.test_health_is_one_request_without_redirect_following.
Variable Index: None.
"""

import argparse
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tools import run_local_mvp as launcher


class LocalLauncherTests(unittest.TestCase):
    """Functionality: Guard offline configuration and supervisor ownership boundaries.
    Logic: Exercise launcher behavior with deterministic substitutes and temporary files.
    Constraints: no environment installation, child launch, network or model execution.
    """

    def test_external_credentials_are_empty_without_mutating_parent(self):
        """Functionality: Prevent inherited API keys from activating external providers.
        Inputs: Synthetic parent env. Outputs: isolated child env assertions.
        Logic: Confirm explicit empties and role-specific Django key; Constraints: no real keys.
        """
        parent = {
            "HF_INFERENCE_TOKEN": "synthetic",
            "ORS_API_KEY": "synthetic",
            "DASHSCOPE_API_KEY": "synthetic",
            "DJANGO_SECRET_KEY": "parent-synthetic",
            "YOLO_OFFLINE": "0",
            "YOLO_CONFIG_DIR": "synthetic-parent-config",
        }
        with patch.dict(launcher.os.environ, parent, clear=True):
            web = launcher.child_environment("local-synthetic", 8776, 8777, web=True)
            worker = launcher.child_environment("local-synthetic", 8776, 8777, web=False)
            self.assertEqual(dict(launcher.os.environ), parent)
        for environment in (web, worker):
            for key in ("HF_TOKEN", "HF_INFERENCE_TOKEN", "ORS_API_KEY", "DASHSCOPE_API_KEY"):
                self.assertEqual(environment[key], "")
            self.assertEqual(environment["LOCAL_WEB_ORIGIN"], "http://127.0.0.1:8776")
            self.assertEqual(environment["LOCAL_INFERENCE_URL"], "ws://127.0.0.1:8777/ws/infer/")
        self.assertEqual(web["DJANGO_SECRET_KEY"], "local-synthetic")
        self.assertEqual(web["PRS_MCP_PROFILE"], "outputs")
        self.assertNotIn("DJANGO_SECRET_KEY", worker)
        self.assertEqual(worker["YOLO_OFFLINE"], "1")
        self.assertEqual(worker["YOLO_AUTOINSTALL"], "0")
        self.assertEqual(worker["YOLO_CONFIG_DIR"], str(launcher.ROOT / ".venv-inference"))

    def test_existing_private_key_is_preserved(self):
        """Functionality: Preserve a previously prepared dedicated local configuration.
        Inputs: Temporary key file. Outputs: same key/file bytes.
        Logic: Read an existing key without regeneration; Constraints: temporary synthetic values.
        """
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env.local-mvp"
            original = "DJANGO_SECRET_KEY=" + "k" * 48 + "\n"
            path.write_text(original, encoding="utf-8")
            self.assertEqual(launcher.read_or_create_key(path), "k" * 48)
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_invalid_existing_key_is_not_replaced(self):
        """Functionality: Reject invalid configuration without destroying user-owned contents.
        Inputs: Temporary malformed file. Outputs: failure and unchanged bytes.
        Logic: Explicit failure keeps the correction reviewable; Constraints: no automatic repair.
        """
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env.local-mvp"
            path.write_text("DJANGO_SECRET_KEY=short\n", encoding="utf-8")
            with self.assertRaises(launcher.StartupError):
                launcher.read_or_create_key(path)
            self.assertEqual(path.read_text(encoding="utf-8"), "DJANGO_SECRET_KEY=short\n")

    def test_failed_check_does_not_prepare_key_or_launch(self):
        """Functionality: Keep prerequisite checks read-only and fail before partial service start.
        Inputs: Mock unavailable runtime. Outputs: no key creation or launch.
        Logic: Check mode cannot repair an environment; Constraints: no real process.
        """
        args = argparse.Namespace(check=True, web_port=8766, inference_port=8767, log_dir=None)
        with (
            patch.object(launcher, "python_path", return_value=Path("synthetic-python")),
            patch.object(
                launcher, "inspect_environment", side_effect=launcher.StartupError("missing")
            ),
            patch.object(launcher, "read_or_create_key") as key,
            patch.object(launcher, "launch") as launch,
        ):
            with self.assertRaises(launcher.StartupError):
                launcher.run(args)
            key.assert_not_called()
            launch.assert_not_called()

    def test_duplicate_ports_fail_before_binding(self):
        """Functionality: Reject ambiguous process routing before probing sockets.
        Inputs: Duplicate local ports. Outputs: failure/no socket created.
        Logic: Distinct addresses are required; Constraints: no actual listener.
        """
        with patch.object(launcher.socket, "socket") as socket:
            with self.assertRaises(launcher.StartupError):
                launcher.verify_ports(8766, 8766)
            socket.assert_not_called()

    def test_cleanup_terminates_only_owned_live_children(self):
        """Functionality: Bound cleanup to the supplied child handles.
        Inputs: Exited, live and stalled child substitutes. Outputs: ownership/timeout assertions.
        Logic: Skip exited child, terminate live child, kill stalled handle only after timeout.
        Constraints: no global process inspection or actual termination.
        """
        exited, live, stalled = Mock(), Mock(), Mock()
        exited.poll.return_value = 0
        live.poll.return_value = stalled.poll.return_value = None
        stalled.wait.side_effect = [subprocess.TimeoutExpired("synthetic", 10), 0]
        launcher.stop_children([exited, live, stalled])
        exited.terminate.assert_not_called()
        live.terminate.assert_called_once()
        live.kill.assert_not_called()
        stalled.terminate.assert_called_once()
        stalled.kill.assert_called_once()

    def test_cuda_local_version_suffix_is_accepted(self):
        """Functionality: Accept official CUDA wheel local-version labels without loosening pins.
        Inputs: Synthetic Torch 2.8.0+cu128 runtime. Outputs: successful exact-base validation.
        Logic: CUDA labels remain runtime evidence; Constraints: no actual Torch import.
        """
        result = Mock(
            returncode=0,
            stdout=json.dumps(
                {
                    "python": [3, 12],
                    "packages": {"torch": "2.8.0+cu128"},
                    "cuda_available": True,
                    "cuda_runtime": "12.8",
                    "device": "synthetic",
                }
            ),
        )
        with patch.object(launcher.subprocess, "run", return_value=result):
            runtime = launcher.inspect_environment(Path("synthetic"), {"torch": "2.8.0"}, cuda=True)
        self.assertTrue(runtime["cuda_available"])

    def test_readonly_probe_uses_project_offline_settings(self):
        """Functionality: Keep CUDA preflight from inheriting external Ultralytics settings.
        Inputs: synthetic runtime and parent library configuration. Outputs: probe env/no writes.
        Logic: same offline settings as worker, but only pass variables to the metadata/Torch probe.
        Constraints: no import of Ultralytics or creation of config directories during --check.
        """
        result = Mock(
            returncode=0,
            stdout=json.dumps(
                {
                    "python": [3, 12],
                    "packages": {"torch": "2.8.0+cu128"},
                    "cuda_available": True,
                    "cuda_runtime": "12.8",
                    "device": "synthetic",
                }
            ),
        )
        with (
            patch.dict(
                launcher.os.environ,
                {"YOLO_OFFLINE": "0", "YOLO_CONFIG_DIR": "synthetic-parent-config"},
            ),
            patch.object(launcher.subprocess, "run", return_value=result) as probe,
            patch.object(launcher.Path, "mkdir") as mkdir,
        ):
            launcher.inspect_environment(Path("synthetic"), {"torch": "2.8.0"}, cuda=True)
            self.assertEqual(launcher.os.environ["YOLO_OFFLINE"], "0")
            self.assertEqual(launcher.os.environ["YOLO_CONFIG_DIR"], "synthetic-parent-config")
        environment = probe.call_args.kwargs["env"]
        self.assertEqual(environment["YOLO_OFFLINE"], "1")
        self.assertEqual(environment["YOLO_AUTOINSTALL"], "0")
        self.assertEqual(environment["YOLO_CONFIG_DIR"], str(launcher.ROOT / ".venv-inference"))
        self.assertNotIn("import ultralytics", probe.call_args.args[0][3].lower())
        mkdir.assert_not_called()

    def test_health_is_one_request_without_redirect_following(self):
        """Functionality: Reject redirected health checks instead of reaching another provider.
        Inputs: Synthetic HTTP 302. Outputs: failure/one request/closed connection.
        Logic: HTTPConnection does not follow redirects; Constraints: no real request.
        """
        connection = Mock()
        response = connection.getresponse.return_value
        response.status, response.read.return_value = 302, b"{}"
        with patch.object(launcher.http.client, "HTTPConnection", return_value=connection):
            with self.assertRaises(launcher.StartupError):
                launcher.read_health(8767, "/health/")
        connection.request.assert_called_once_with("GET", "/health/")
        connection.close.assert_called_once()
