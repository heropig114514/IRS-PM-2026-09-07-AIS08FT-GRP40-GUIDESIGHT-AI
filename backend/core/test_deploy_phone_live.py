"""Responsibilities: Prevent deployment from omitting camera/maps pages or their local dependencies.
Implementation: Traverse real HTML asset references and static relative JS imports,
then compare the closure with the publisher's literal allowlist parsed without importing it.
Related Modules: tools.deploy_phone_live; frontend/index.html, maps.html, device outputs;
i18n.js supplies shared language behavior.
Declaration Index: DeploymentAssetTests; DeploymentAssetTests.check_dependency_closure;
DeploymentAssetTests.test_maps_dependency_closure;
DeploymentAssetTests.test_camera_dependency_closure.
DeploymentAssetTests.test_vibration_profile_stage;
DeploymentAssetTests.test_vibration_profile_stage.command;
DeploymentAssetTests.test_vibration_profile_stage.upload;
DeploymentAssetTests.test_vibration_profile_stage.open_file;
DeploymentAssetTests.test_vibration_profile_activation.
DeploymentAssetTests.test_outputs_scope; DeploymentAssetTests.test_outputs_activation;
DeploymentAssetTests.test_outputs_activation.open_file.
Variable Index: None; required/pending are test-local file-name sets, with no credentials.
"""

import ast
import re
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from django.test import SimpleTestCase


class DeploymentAssetTests(SimpleTestCase):
    """Functionality: Verify source/deployment integration for camera and map pages.
    Logic: Inspect actual local source and the production publisher's allowlist.
    Constraints: No SSH, uploads, deployment-only imports or external-service assertions.
    """

    def check_dependency_closure(self, page_name, asset_prefix):
        """Functionality: Require every locally referenced page asset in one release.
        Inputs: page_name and HTML asset_prefix, static imports and publisher FRONTEND declaration.
        Outputs: Missing dependency assertion with file names; local source reads only.
        Logic: Read the literal allowlist via AST, seed from HTML, traverse static JS once,
        reject path escapes. No publisher import, so no Paramiko credential/runtime requirement.
        Constraints: Covers static imports only; not dynamic imports or CDN availability.
        """
        backend = Path(__file__).resolve().parents[1]
        publisher = ast.parse((backend / "tools/deploy_phone_live.py").read_text(encoding="utf-8"))
        declarations = [
            ast.literal_eval(node.value) for node in publisher.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "FRONTEND"
                    for target in node.targets)
        ]
        self.assertEqual(len(declarations), 1, "Publisher must have one literal FRONTEND allowlist")
        published = set(declarations[0])
        frontend = backend.parent / "frontend"
        html = (frontend / page_name).read_text(encoding="utf-8")
        required = {page_name}
        pending = re.findall(r'(?:src|href)="' + re.escape(asset_prefix) + r'([^"/]+)"', html)
        self.assertTrue(pending, f"No local assets found in {page_name}")
        while pending:
            name = pending.pop()
            if name in required:
                continue
            path = (frontend / name).resolve()
            self.assertTrue(path.is_relative_to(frontend.resolve()), name)
            required.add(name)
            if path.suffix == ".js":
                source = path.read_text(encoding="utf-8")
                imports = re.findall(r"(?:\bfrom\s*|\bimport\s*)['\"](\./[^'\"]+)['\"]", source)
                pending.extend(
                    str(path.parent.joinpath(item).relative_to(frontend)).replace("\\", "/")
                    for item in imports
                )
        self.assertFalse(
            required - published, f"Unpublished {page_name} assets: {required - published}"
        )

    def test_maps_dependency_closure(self):
        """Functionality: Preserve complete map and language asset publication.
        Inputs: Actual maps.html and ../assets/ references. Outputs: Dependency assertions.
        Logic: Traverse the common static closure. Constraints: No remote deployment or CDN check.
        """
        self.check_dependency_closure("maps.html", "../assets/")

    def test_camera_dependency_closure(self):
        """Functionality: Include the camera page's manual vibration module in publication.
        Inputs: Actual index.html and ./assets/ references. Outputs: Dependency assertions.
        Logic: Traverse all static dependencies from the page.
        Constraints: No SSH or hardware calls.
        """
        self.check_dependency_closure("index.html", "./assets/")

    def test_vibration_profile_stage(self):
        """Functionality: Verify UI-only staging and fail before mutation on plan mismatch.
        Inputs: Actual qualified plan, temporary overlay files and explicit SSH/SFTP substitutes.
        Outputs: Exact overlay set, no retired/template files, recorded commit and early failure.
        Logic: Run the real profile selector and hash checks; mocks implement remote bytes only.
        Constraints: No real SSH, credentials, GPU, dependency installation or remote readiness.
        """
        from tools import deploy_phone_live as publisher

        backend = Path(__file__).resolve().parents[1]
        for mismatch in (False, True):
            with self.subTest(mismatch=mismatch), tempfile.TemporaryDirectory() as directory:
                source = Path(directory)
                remote = {}
                scripts = []
                current = publisher.PREFIX + "/releases/test-current"
                for name in ("models/inference-plan.json", "models/yolo26n.json"):
                    target = source / "backend" / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes((backend / name).read_bytes())
                    remote[current + "/backend/" + name] = target.read_bytes()
                if mismatch:
                    remote[current + "/backend/models/inference-plan.json"] = b"changed"
                names = ["frontend/" + name for name in publisher.VIBRATION_FRONTEND]
                names += ["backend/" + name for name in publisher.VIBRATION_BACKEND]
                for name in names:
                    target = source / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(b"explicit overlay fixture")

                def command(client, script, scripts=scripts, current=current):
                    """Functionality: Capture remote scripts and return fixed current/readiness.
                    Inputs: client/script and bound scripts/current fixtures.
                    Outputs: current path or synthetic successful check.
                    Logic: Only the readlink probe has data. Constraints: Never executes a shell.
                    """
                    scripts.append(script)
                    return current if script.startswith("readlink") else "fixture checks passed"

                def upload(stream, path, remote=remote):
                    """Functionality: Record uploaded source bytes.
                    Inputs: stream/path and bound remote byte map. Outputs: map update.
                    Logic: Read once. Constraints: No actual SFTP or filesystem access.
                    """
                    remote[path] = stream.read()

                def open_file(path, mode="r", remote=remote):
                    """Functionality: Return a remote read/write context substitute.
                    Inputs: path/mode and bound remote byte map. Outputs: Mock file.
                    Logic: Upload verification sees recorded bytes; writable manifest is a mock.
                    Constraints: No credentials or real remote stream lifecycle verification.
                    """
                    stream = Mock()
                    stream.read.return_value = remote.get(path, b"")
                    stream.__enter__ = Mock(return_value=stream)
                    stream.__exit__ = Mock(return_value=False)
                    return stream

                sftp = Mock()
                sftp.putfo.side_effect = upload
                sftp.open.side_effect = open_file
                with patch.object(publisher, "ROOT", source / "backend"), \
                        patch.object(publisher, "execute", side_effect=command):
                    if mismatch:
                        with self.assertRaisesRegex(ValueError, "active inference identity"):
                            publisher.stage(Mock(), sftp, "vibration", source, "a" * 40)
                        self.assertEqual(len(scripts), 1)
                        sftp.putfo.assert_not_called()
                    else:
                        result = publisher.stage(Mock(), sftp, "vibration", source, "a" * 40)
                        self.assertEqual(set(result["files"]), set(names))
                        self.assertEqual(result["removed"], [])
                        self.assertEqual(result["git_commit"], "a" * 40)
                        self.assertEqual(result["profile"], "vibration")
                        self.assertFalse(any("nginx" in name for name in result["files"]))
                        self.assertFalse(any("names=json.loads" in script for script in scripts))

    def test_vibration_profile_activation(self):
        """Functionality: Verify activation preserves configuration and failure semantics.
        Inputs: Explicit manifest and mocked shell/readiness boundaries, success or failure.
        Outputs: One service restart, no config/token/proxy mutation or retry, truthful state.
        Logic: Run the actual dedicated activator and inspect all issued commands.
        Constraints: Mock success is not real service or health verification; no automatic rollback.
        """
        from tools import deploy_phone_live as publisher

        for fails in (False, True):
            with self.subTest(fails=fails):
                manifest = {"release": "test-vibration", "previous": "previous",
                            "path": publisher.PREFIX + "/releases/test-vibration"}
                with patch.object(publisher, "execute", return_value="active") as execute, \
                        patch.object(publisher, "wait_ready", return_value="healthy") as ready:
                    if fails:
                        ready.side_effect = RuntimeError("Explicit readiness failure")
                        with self.assertRaisesRegex(RuntimeError, "readiness failure"):
                            publisher.activate_vibration(Mock(), manifest)
                        self.assertNotIn("activated", manifest)
                    else:
                        publisher.activate_vibration(Mock(), manifest)
                        self.assertTrue(manifest["activated"])
                    self.assertEqual(execute.call_count, 1)
                    self.assertEqual(ready.call_count, 1)
                    script = execute.call_args.args[1]
                    self.assertEqual(script.count("systemctl restart prs-camera"), 1)
                    forbidden_commands = ("nginx", "app.env", "install -m",
                                          "daemon-reload", "rollback")
                    for forbidden in forbidden_commands:
                        self.assertNotIn(forbidden, script)

    def test_outputs_scope(self):
        """Functionality: Require output deployment to exclude inference/weight replacement.
        Inputs: Actual publisher constants and temporary runtime configuration.
        Outputs: Source closure, protected-file exclusions and invalid-runtime rejection.
        Logic: Inspect the explicit overlays; reject missing/escaped runtime before any SSH call.
        Constraints: No network, device, model execution or transitive dependency verification.
        """
        from tools import deploy_phone_live as publisher

        protected = {"core/hf_video_relay.py", "zerogpu/live_transport.py",
                     "models/inference-plan.json", "models/yolo26n.json"}
        self.assertFalse(protected & set(publisher.OUTPUT_BACKEND))
        self.assertFalse({"camera.js", "frame-pipeline.js", "live-detection-client.js"} &
                         set(publisher.OUTPUT_FRONTEND))
        self.assertTrue({"vibration.js", "speech-output.js", "ai-control.js", "maps.html"} <=
                        set(publisher.OUTPUT_FRONTEND))
        with patch.object(publisher, "execute") as command:
            with self.assertRaisesRegex(ValueError, "explicit isolated runtime"):
                publisher.stage(Mock(), Mock(), "outputs")
            with self.assertRaisesRegex(ValueError, "Unexpected runtime path"):
                publisher.stage(Mock(), Mock(), "outputs", runtime_path="/tmp/unowned")
            with self.assertRaisesRegex(ValueError, "isolated runtime"):
                publisher.stage(Mock(), Mock(), "outputs",
                                runtime_path=publisher.PREFIX + "/runtime-venv")
            command.assert_not_called()

    def test_outputs_activation(self):
        """Functionality: Preserve credentials and isolate deployment to the owned service.
        Inputs: Explicit output manifest, SSH/SFTP/readiness mocks and synthetic private env bytes.
        Outputs: Profile-only env change, root0600, one restart, no token substitution or retry.
        Logic: Run the actual activator and compare written environment bytes without printing them.
        Constraints: Synthetic values never access real services; not evidence of actual TLS/health.
        """
        from tools import deploy_phone_live as publisher

        original = b"HF_INFERENCE_TOKEN=fixture-only\nORS_API_KEY=fixture-only\n"
        written = []

        def open_file(path, mode="r"):
            """Functionality: Provide explicit read/write file boundaries for private config.
            Inputs: path/mode. Outputs: Mock stream; writes captured for byte comparison.
            Logic: Known old env returned only for read. Constraints: No actual credentials or SSH.
            """
            stream = Mock()
            stream.read.return_value = original
            stream.write.side_effect = written.append
            stream.__enter__ = Mock(return_value=stream)
            stream.__exit__ = Mock(return_value=False)
            return stream

        manifest = {"path": publisher.PREFIX + "/releases/test-outputs", "release": "test-outputs",
                    "runtime": publisher.PREFIX + "/runtimes/test-outputs"}
        sftp = Mock()
        sftp.open.side_effect = open_file
        with patch.object(publisher, "execute", return_value="checked") as command, \
                patch.object(publisher, "wait_ready", return_value="healthy"):
            publisher.activate_outputs(Mock(), sftp, manifest)
        self.assertEqual(written, [original + b"PRS_MCP_PROFILE=outputs\n"])
        sftp.chmod.assert_called_once_with("/etc/prs-camera/app.env.outputs-next", 0o600)
        script = "\n".join(call.args[1] for call in command.call_args_list)
        self.assertEqual(script.count("systemctl restart prs-camera"), 1)
        self.assertNotIn("HF_INFERENCE_TOKEN=", script)
        self.assertNotIn("ORS_API_KEY=", script)
        self.assertNotIn("rollback", script)
        self.assertTrue(manifest["activated"])
