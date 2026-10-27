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
LocalLauncherTests.test_health_is_one_request_without_redirect_following;
AssistLauncherTests; AssistLauncherTests.test_explicit_ors_is_web_only;
AssistLauncherTests.test_dotenv_imports_only_ors_without_interpolation;
AssistLauncherTests.test_invalid_ors_files_are_rejected_safely;
AssistLauncherTests.test_ors_file_requires_assist_before_preflight;
AssistLauncherTests.test_assist_check_has_no_writes_or_children;
AssistLauncherTests.test_assist_launch_selects_entry_and_session_policy;
AssistLauncherTests.test_policy_mismatch_stops_owned_children.
Variable Index: None.
"""

import argparse
import contextlib
import io
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


class AssistLauncherTests(unittest.TestCase):
    """Functionality: Verify opt-in route credentials and combined-entry process configuration.
    Logic: synthetic credentials, file contents and process handles only.
    Constraints: no real secret discovery, subprocess, port, external API or CUDA operations.
    """

    def test_explicit_ors_is_web_only(self):
        """Functionality: Pass authorized ORS only to the assist web process.
        Inputs: synthetic parent keys/mode and explicit selected ORS key. Outputs: isolated envs.
        Logic: inference retains no route key; default mode ignores inherited assist/ORS settings.
        Constraints: no parent mutation or accidental HF/DashScope activation.
        """
        parent = {"ORS_API_KEY": "parent-private", "HF_TOKEN": "external-private",
                  "DASHSCOPE_API_KEY": "other-private", "LOCAL_MVP_MODE": "assist"}
        with patch.dict(launcher.os.environ, parent, clear=True):
            selected = launcher.read_ors_key()
            web = launcher.child_environment("synthetic", 8766, 8767, web=True,
                                             assist=True, ors_key=selected)
            worker = launcher.child_environment("synthetic", 8766, 8767, web=False,
                                                assist=True, ors_key=selected)
            offline = launcher.child_environment("synthetic", 8766, 8767, web=True)
            self.assertEqual(dict(launcher.os.environ), parent)
        self.assertEqual(web["ORS_API_KEY"], "parent-private")
        self.assertEqual(worker["ORS_API_KEY"], "")
        self.assertEqual(offline["ORS_API_KEY"], "")
        self.assertEqual(offline["LOCAL_MVP_MODE"], "offline")
        for environment in (web, worker):
            self.assertEqual(environment["LOCAL_MVP_MODE"], "assist")
            self.assertEqual(environment["HF_TOKEN"], "")
            self.assertEqual(environment["DASHSCOPE_API_KEY"], "")

    def test_dotenv_imports_only_ors_without_interpolation(self):
        """Functionality: Read an explicit dotenv without importing arbitrary values or expansion.
        Inputs: synthetic file with comments/quoted ORS and unrelated secret-shaped assignments.
        Outputs: literal ORS string and unchanged process. Logic: safe parser, no load_dotenv call.
        Constraints: no automatic .env discovery, shell expansion or private value diagnostics.
        """
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "explicit.env"
            path.write_text('HF_TOKEN=do-not-copy\nDJANGO_SECRET_KEY=do-not-copy\n'
                            'export ORS_API_KEY="${NOT_EXPANDED}-literal" # comment\n',
                            encoding="utf-8")
            with patch.dict(launcher.os.environ, {"NOT_EXPANDED": "private"}, clear=True):
                before = dict(launcher.os.environ)
                self.assertEqual(launcher.read_ors_key(path), "${NOT_EXPANDED}-literal")
                self.assertEqual(dict(launcher.os.environ), before)

    def test_invalid_ors_files_are_rejected_safely(self):
        """Functionality: Reject duplicate/malformed/oversized/unreadable credential files.
        Inputs: explicit synthetic invalid files. Outputs: safe StartupError without key content.
        Logic: one ORS binding, 64KiB and UTF-8 bounds. Constraints: never repair or replace files.
        """
        contents = (b"ORS_API_KEY=private-one\nORS_API_KEY=private-two\n",
                    b"ORS_API_KEY='private-broken\n", b"HF_TOKEN=private-only\n",
                    b"ORS_API_KEY=private-" + b"a" * 65536, b"ORS_API_KEY=\xff")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "explicit.env"
            for content in contents:
                path.write_bytes(content)
                with self.assertRaises(launcher.StartupError) as caught:
                    launcher.read_ors_key(path)
                self.assertNotIn("private-", str(caught.exception))
                self.assertEqual(path.read_bytes(), content)

    def test_ors_file_requires_assist_before_preflight(self):
        """Functionality: Keep default offline mode from reading or using an explicit ORS file.
        Inputs: offline CLI arguments with a synthetic credential path. Outputs: early failure.
        Logic: reject incompatible options before runtime probes or credential reads.
        Constraints: no file access, key creation, GPU query or subprocess.
        """
        args = argparse.Namespace(assist=False, ors_env=Path("not-read.env"))
        with patch.object(launcher, "read_ors_key") as reader, \
                patch.object(launcher, "python_path") as python:
            with self.assertRaises(launcher.StartupError):
                launcher.run(args)
            reader.assert_not_called()
            python.assert_not_called()

    def test_assist_check_has_no_writes_or_children(self):
        """Functionality: Keep successful --assist --check read-only after explicit config parsing.
        Inputs: synthetic prerequisite results. Outputs: no configuration creation or child launch.
        Logic: inspect prerequisites before mutation. Constraints: no real GPU or sockets.
        """
        args = argparse.Namespace(assist=True, ors_env=None, check=True,
                                  web_port=8766, inference_port=8767, log_dir=None)
        with (
            patch.object(launcher, "read_ors_key", return_value="private-check"),
            patch.object(launcher, "python_path", return_value=Path("synthetic")),
            patch.object(launcher, "inspect_environment", return_value={
                "device": "synthetic", "cuda_runtime": "synthetic"}),
            patch.object(launcher, "verify_models"), patch.object(launcher, "verify_ports"),
            patch.object(launcher, "read_or_create_key") as key,
            patch.object(launcher, "launch") as launch,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            launcher.run(args)
            key.assert_not_called()
            launch.assert_not_called()
        self.assertNotIn("private-check", output.getvalue())

    def test_assist_launch_selects_entry_and_session_policy(self):
        """Functionality: Select two explicit process modes and advertise the combined page.
        Inputs: mock listeners/health and immediate user stop. Outputs: exact modules/envs/cleanup.
        Logic: assist owns web ORS only and both processes agree on unbounded session policy.
        Constraints: all processes, signals and waits are substitutes; no running server or GPU.
        """
        args = argparse.Namespace(assist=True, ors_env=None, check=False,
                                  web_port=8766, inference_port=8767, log_dir=None)
        children = [Mock(), Mock()]
        with (
            patch.object(launcher, "read_ors_key", return_value="private-selected"),
            patch.object(launcher, "python_path", return_value=Path("synthetic")),
            patch.object(launcher, "inspect_environment", return_value={
                "device": "synthetic", "cuda_runtime": "synthetic"}),
            patch.object(launcher, "verify_models"), patch.object(launcher, "verify_ports"),
            patch.object(launcher, "read_or_create_key", return_value="private-django"),
            patch.object(launcher.signal, "signal"),
            patch.object(launcher.STOP_REQUESTED, "wait", return_value=True),
            patch.object(launcher, "launch", side_effect=children) as launch,
            patch.object(launcher, "wait_listener"),
            patch.object(launcher, "read_health", return_value={"session_seconds": None}),
            patch.object(launcher, "stop_children") as stop,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            launcher.run(args)
            self.assertEqual(launch.call_args_list[0].args[1],
                             "integration.inference.asgi:application")
            self.assertEqual(launch.call_args_list[1].args[1],
                             "integration.assist_asgi:application")
            self.assertEqual(launch.call_args_list[0].args[3]["ORS_API_KEY"], "")
            self.assertEqual(launch.call_args_list[1].args[3]["ORS_API_KEY"], "private-selected")
            stop.assert_called_once_with(children)
        self.assertIn("http://127.0.0.1:8766/maps/", output.getvalue())
        self.assertNotIn("private-", output.getvalue())

    def test_policy_mismatch_stops_owned_children(self):
        """Functionality: Reject an old worker's 55-second policy during continuous startup.
        Inputs: assist selection but incompatible worker health. Outputs: failure and owned cleanup.
        Logic: never start web or claim availability under a mismatched transport contract.
        Constraints: no restart, automatic policy adjustment or actual subprocess.
        """
        args = argparse.Namespace(assist=True, ors_env=None, check=False,
                                  web_port=8766, inference_port=8767, log_dir=None)
        child = Mock()
        with (
            patch.object(launcher, "read_ors_key", return_value=""),
            patch.object(launcher, "python_path", return_value=Path("synthetic")),
            patch.object(launcher, "inspect_environment", return_value={
                "device": "synthetic", "cuda_runtime": "synthetic"}),
            patch.object(launcher, "verify_models"), patch.object(launcher, "verify_ports"),
            patch.object(launcher, "read_or_create_key", return_value="private-django"),
            patch.object(launcher.signal, "signal"),
            patch.object(launcher, "launch", return_value=child) as launch,
            patch.object(launcher, "wait_listener"),
            patch.object(launcher, "read_health", return_value={"session_seconds": 55}),
            patch.object(launcher, "stop_children") as stop,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            with self.assertRaises(launcher.StartupError):
                launcher.run(args)
            launch.assert_called_once()
            stop.assert_called_once_with([child])
