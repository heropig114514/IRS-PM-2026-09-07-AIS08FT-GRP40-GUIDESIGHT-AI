"""Responsibilities: Verify portable preparation, profile isolation and local supervisor wiring.
Implementation: Temporary model/venv files, explicit download streams and mocked process boundary.
Related Modules: tools.local_demo; deploy/local-demo.toml; tools.run_local_mvp.
Declaration Index: LocalDemoTests; LocalDemoTests.test_versioned_checkpoints_match_runtime;
LocalDemoTests.test_profile_isolation_preserves_parent; LocalDemoTests.test_backend_selection;
LocalDemoTests.test_commands_bind_only_local_supervisors; LocalDemoTests.test_lan_origin_and_trust;
LocalDemoTests.test_check_never_prepares_or_executes; LocalDemoTests.test_run_replaces_supervisor;
LocalDemoTests.test_prepare_uses_only_project_environments; LocalDemoTests.test_environment_refusal;
LocalDemoTests.test_model_publication; LocalDemoTests.test_model_failures_preserve_existing;
LocalDemoTests.test_model_size_bound; LocalDemoTests.test_network_failure_does_not_retry;
LocalDemoTests.test_process_failure_does_not_continue.
Variable Index: None; tests create no real network/GPU service, package installation or certificate.
"""

import hashlib
import io
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

from tools import local_demo as demo
from tools.run_local_mvp import DEPTH_SHA256


class LocalDemoTests(unittest.TestCase):
    """Functionality: Guard reproducible local setup and startup contracts.
    Logic: explicit files and adapters exercise actual command/environment/provisioning code.
    Constraints: fake downloaded bytes and mocked processes do not prove GPU or WAN performance.
    """

    def test_versioned_checkpoints_match_runtime(self):
        """Functionality: Prevent bootstrap manifests drifting from runtime model identities.
        Inputs: tracked deployment/model files. Outputs: exact matching hashes and filenames.
        Logic: compare independent configuration owners. Constraints: no model deserialization.
        """
        import json

        configuration = demo.settings()
        original = json.loads((demo.ROOT / "models/yolo26n.json").read_text())
        self.assertEqual(configuration["models"]["yolo26n"]["sha256"], original["source_sha256"])
        self.assertEqual(configuration["models"]["depth"]["sha256"], DEPTH_SHA256)
        self.assertEqual(configuration["models"]["depth"]["filename"], "yolo26n-depth.pt")

    def test_profile_isolation_preserves_parent(self):
        """Functionality: Prevent inherited strategies/credentials changing a comparison.
        Inputs: conflicting synthetic environment. Outputs: fixed model/policy and empty API keys.
        Logic: read actual low-latency profile, retaining only ordinary driver/process variables.
        Constraints: no mutation of the calling shell or secret diagnostics.
        """
        parent = {"PRS_DISTANCE_CONFIG": "other.json", "PRS_DEPTH_BACKEND": "tensorrt",
                  "PRS_DEPTH_ENGINE_MANIFEST": "other-engine.json", "PRS_DEPTH_INTERVAL": "3",
                  "PRS_LAN_ORIGIN": "https://other.invalid", "PRS_AGENT_API_KEY": "synthetic",
                  "ORS_API_KEY": "synthetic", "PATH": "fixture-path"}
        args = demo.parse_args(["check"])
        with patch.dict(demo.os.environ, parent, clear=True):
            environment, selected = demo.profile_environment(args)
            self.assertEqual(dict(demo.os.environ), parent)
        self.assertNotIn("PRS_DISTANCE_CONFIG", environment)
        self.assertNotIn("PRS_LAN_ORIGIN", environment)
        self.assertEqual(environment["PATH"], "fixture-path")
        self.assertEqual(environment["PRS_AGENT_API_KEY"], "")
        self.assertEqual(environment["ORS_API_KEY"], "")
        self.assertEqual(selected["PRS_DETECTION_MODEL"], "yolo26n")
        self.assertEqual(selected["PRS_DEPTH_BACKEND"], "torch")
        self.assertEqual(selected["PRS_DEPTH_FUSION"], "host")
        self.assertEqual(selected["PRS_DEPTH_INTERVAL"], "1")
        self.assertEqual(selected["PRS_CAMERA_PROFILE"], "bounded-2")

    def test_backend_selection(self):
        """Functionality: Require a manifest only for explicit TensorRT selection.
        Inputs: missing/extra/existing manifest and walk detector. Outputs: error or config.
        Logic: no engine discovery or fallback. Constraints: runtime still checks engine identity.
        """
        for args in (["check", "--profile", "tensorrt"],
                     ["check", "--engine-manifest", "missing.json"]):
            with self.subTest(args=args), self.assertRaises(demo.LocalDemoError):
                demo.profile_environment(demo.parse_args(args))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text("{}")
            _, selected = demo.profile_environment(demo.parse_args([
                "check", "--profile", "tensorrt", "--engine-manifest", str(path),
                "--detector", "yolo26n-walk",
            ]))
            self.assertEqual(selected["PRS_DEPTH_ENGINE_MANIFEST"], str(path.resolve()))
            self.assertEqual(selected["PRS_DETECTION_MODEL"], "yolo26n-walk")
        _, baseline = demo.profile_environment(demo.parse_args(["check", "--profile", "baseline"]))
        self.assertEqual(baseline["PRS_DEPTH_FUSION"], "gpu")
        self.assertEqual(baseline["PRS_CAMERA_PROFILE"], "standard")

    def test_commands_bind_only_local_supervisors(self):
        """Functionality: Build correctly scoped commands for camera and independent video modes.
        Inputs: explicit alternate ports/interpreter. Outputs: local module and exact arguments.
        Logic: all paths are checkout-relative, not caller-CWD or author-specific.
        Constraints: no cloud SSH launcher or secret argument.
        """
        with patch.object(Path, "is_file", return_value=True):
            command, environment = demo.launch_command(demo.parse_args([
                "check", "--web-port", "8776", "--inference-port", "8777",
                "--inference-python", "custom/bin/python",
            ]))
            self.assertIn("tools.run_local_mvp", command)
            self.assertIn("--assist", command)
            self.assertIn("--check", command)
            self.assertIn(str((demo.ROOT / "custom/bin/python").resolve()), command)
            self.assertEqual(environment["PRS_CAMERA_PROFILE"], "bounded-2")
            video, _ = demo.launch_command(demo.parse_args(["run", "--mode", "video"]))
            self.assertNotIn("--assist", video)
            self.assertIn("--log-dir", video)

    def test_lan_origin_and_trust(self):
        """Functionality: Align the LAN gateway with the separately started local worker Origin.
        Inputs: chosen subnet and alternate local Web port. Outputs: exact canonical origin port.
        Logic: certificate preparation/check are explicit. Constraints: no trust installation.
        """
        args = demo.parse_args(["lan", "--address", "192.168.1.10", "--subnet", "192.168.1.0/24",
                                "--state-dir", ".venv/tmp/lan-next", "--web-port", "8776",
                                "--check"])
        with patch.object(Path, "is_file", return_value=True):
            command, _ = demo.launch_command(args)
        self.assertIn("tools.run_lan_mvp", command)
        self.assertEqual(command[command.index("--canonical-web-port") + 1], "8776")
        self.assertIn("--check", command)
        self.assertNotIn("--prepare-certificate", command)
        self.assertNotIn("tools.run_phone_mvp", command)

    def test_check_never_prepares_or_executes(self):
        """Functionality: Keep read-only check separate from installs and long-running services.
        Inputs: mocked preflight returning7. Outputs: unchanged failure code with no prepare/exec.
        Logic: preserve native launcher's bounded validation. Constraints: no real CUDA check here.
        """
        with (patch.object(demo, "launch_command", return_value=(["python", "check"], {})),
              patch.object(demo, "prepare") as prepare,
              patch.object(demo, "provision_models") as models,
              patch.object(demo.os, "execve") as execute,
              patch.object(demo.subprocess, "run", return_value=Mock(returncode=7))):
            self.assertEqual(demo.main(["check"]), 7)
        prepare.assert_not_called()
        models.assert_not_called()
        execute.assert_not_called()

    def test_run_replaces_supervisor(self):
        """Functionality: Preserve existing foreground supervisor process/signal ownership.
        Inputs: constructed command and synthetic environment. Outputs: exact exec call.
        Logic: no nested waiting supervisor or forced process kill. Constraints: no actual exec.
        """
        command, environment = ["explicit-python", "-m", "tools.run_local_mvp"], {"KEY": "fixture"}
        with (patch.object(demo, "launch_command", return_value=(command, environment)),
              patch.object(demo.os, "chdir") as directory,
              patch.object(demo.os, "execve") as execute):
            self.assertEqual(demo.main(["run"]), 0)
        directory.assert_called_once_with(demo.ROOT)
        execute.assert_called_once_with(command[0], command, environment)

    def test_prepare_uses_only_project_environments(self):
        """Functionality: Install CUDA wheels explicitly before native requirements.
        Inputs: cu126 plus optional TensorRT export dependencies. Outputs: scoped pip operations.
        Logic: every install has retries0; dependencies checked before checkpoint publication.
        Constraints: mocked package processes do not establish public wheel availability.
        """
        with (patch.object(demo, "ensure_environment", side_effect=[Path("web"), Path("gpu")]),
              patch.object(demo, "execute") as execute,
              patch.object(demo, "provision_models") as models):
            demo.prepare(demo.parse_args(["prepare", "--cuda", "cu126", "--with-tensorrt"]))
        commands = [call.args[0] for call in execute.call_args_list]
        self.assertIn("torch==2.8.0+cu126", commands[1])
        self.assertIn("https://download.pytorch.org/whl/cu126", commands[1])
        self.assertIn("requirements-inference-tensorrt.txt", commands[3])
        self.assertIn("requirements-depth-export.txt", commands[3])
        for command in commands[:4]:
            self.assertEqual(command[command.index("--retries") + 1], "0")
        self.assertEqual(commands[-1][-1], "check")
        models.assert_called_once()

    def test_environment_refusal(self):
        """Functionality: Refuse an unrelated directory instead of deleting or adopting it.
        Inputs: existing non-venv inside a temporary backend. Outputs: fixed error, original bytes.
        Logic: validate venv marker/interpreter. Constraints: no process or package execution.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = root / ".venv"
            environment.mkdir()
            (environment / "keep.txt").write_text("owned elsewhere")
            with patch.object(demo, "ROOT", root), patch.object(demo, "execute") as execute:
                with self.assertRaisesRegex(demo.LocalDemoError, "Not a prepared venv"):
                    demo.ensure_environment(environment)
            execute.assert_not_called()
            self.assertEqual((environment / "keep.txt").read_text(), "owned elsewhere")

    def test_model_publication(self):
        """Functionality: Publish only complete hash-verified bytes and reuse them without network.
        Inputs: explicit synthetic HTTPS response. Outputs: exact file and no leftover temporary.
        Logic: actual streamed file/hash/exclusive link operations. Constraints: no model execution.
        """
        content = b"synthetic checkpoint"
        entry = {"filename": "yolo26n.pt", "sha256": hashlib.sha256(content).hexdigest(),
                 "url": "https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26n.pt"}
        response = io.BytesIO(content)
        response.geturl = Mock(return_value=entry["url"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (patch.object(demo, "ROOT", root),
                  patch.object(demo, "settings", return_value={"models": {"test": entry}}),
                  patch.object(demo.urllib.request, "urlopen", return_value=response) as download):
                demo.provision_models()
                demo.provision_models()
            download.assert_called_once()
            self.assertEqual((root / "models/yolo26n.pt").read_bytes(), content)
            self.assertEqual(len(list((root / "models").iterdir())), 1)

    def test_model_failures_preserve_existing(self):
        """Functionality: Reject mismatched new/existing weights without replacement.
        Inputs: corrupt transfer followed by a different user-owned checkpoint.
        Outputs: no published bad bytes, no temp leak, preserved user file and one network call.
        Logic: fail before deserialization or overwrite. Constraints: no alternate weight source.
        """
        entry = {"filename": "yolo26n.pt", "sha256": "0" * 64,
                 "url": "https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26n.pt"}
        response = io.BytesIO(b"corrupt")
        response.geturl = Mock(return_value=entry["url"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (patch.object(demo, "ROOT", root),
                  patch.object(demo, "settings", return_value={"models": {"test": entry}}),
                  patch.object(demo.urllib.request, "urlopen", return_value=response) as download):
                with self.assertRaisesRegex(demo.LocalDemoError, "Downloaded"):
                    demo.provision_models()
                self.assertEqual(list((root / "models").iterdir()), [])
                path = root / "models/yolo26n.pt"
                path.write_bytes(b"user checkpoint")
                with self.assertRaisesRegex(demo.LocalDemoError, "Existing"):
                    demo.provision_models()
            download.assert_called_once()
            self.assertEqual(path.read_bytes(), b"user checkpoint")

    def test_model_size_bound(self):
        """Functionality: Bound streamed checkpoint bytes before publishing.
        Inputs: oversized synthetic response and a4-byte test cap. Outputs: visible failure/cleanup.
        Logic: actual streaming loop aborts immediately. Constraints: no unbounded download.
        """
        entry = {"filename": "yolo26n.pt", "sha256": "0" * 64,
                 "url": "https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26n.pt"}
        response = io.BytesIO(b"oversized")
        response.geturl = Mock(return_value=entry["url"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (patch.object(demo, "ROOT", root), patch.object(demo, "MAX_MODEL_BYTES", 4),
                  patch.object(demo, "settings", return_value={"models": {"test": entry}}),
                  patch.object(demo.urllib.request, "urlopen", return_value=response),
                  self.assertRaisesRegex(demo.LocalDemoError, "size bound")):
                demo.provision_models()
            self.assertEqual(list((root / "models").iterdir()), [])

    def test_network_failure_does_not_retry(self):
        """Functionality: Preserve download failure without logging proxy details or retrying.
        Inputs: private sentinel in a synthetic urllib exception. Outputs: safe category and exit1.
        Logic: main only exposes nonsecret diagnostics. Constraints: no real network request.
        """
        with (patch.object(demo, "provision_models", side_effect=urllib.error.URLError("private"))
              as models, patch("sys.stderr", new_callable=io.StringIO) as output):
            self.assertEqual(demo.main(["models"]), 1)
        self.assertNotIn("private", output.getvalue())
        self.assertIn("URLError", output.getvalue())
        models.assert_called_once()

    def test_process_failure_does_not_continue(self):
        """Functionality: Surface a failed preparation subprocess once.
        Inputs: exit9. Outputs: explicit exception with no second invocation.
        Logic: preserve argument boundaries and backend CWD. Constraints: no shell substitution.
        """
        with patch.object(demo.subprocess, "run", return_value=Mock(returncode=9)) as run:
            with self.assertRaisesRegex(demo.LocalDemoError, "exit 9"):
                demo.execute(["python path", "-m", "pip", "check"])
        run.assert_called_once_with(
            ["python path", "-m", "pip", "check"], cwd=demo.ROOT, check=False
        )
