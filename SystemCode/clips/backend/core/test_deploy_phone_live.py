"""Responsibilities: Verify page dependency closure and configuration-preserving scoped deployments.
Implementation: Traverse real HTML asset references and static relative JS imports,
then compare the closure with the literal allowlist; mock SSH for scoped staging/activation.
Related Modules: tools.deploy_phone_live; SystemCode/clips/frontend/index.html,
maps.html, device outputs; i18n.js supplies shared language behavior.
Declaration Index: DeploymentAssetTests; DeploymentAssetTests.check_dependency_closure;
DeploymentAssetTests.test_maps_dependency_closure;
DeploymentAssetTests.test_navigation_speech_profiles;
DeploymentAssetTests.test_camera_dependency_closure;
DeploymentAssetTests.test_scene_files_published.
DeploymentAssetTests.test_agent_bundle; DeploymentAssetTests.test_agent_overlay_refusal.
DeploymentAssetTests.test_agent_environment; DeploymentAssetTests.test_full_outputs_activation;
DeploymentAssetTests.test_navigation_scope — navigation closure and preserved camera/config paths;
DeploymentAssetTests.test_overlay_profile_stage;
DeploymentAssetTests.test_overlay_profile_stage.command;
DeploymentAssetTests.test_overlay_profile_stage.upload;
DeploymentAssetTests.test_overlay_profile_stage.open_file;
DeploymentAssetTests.test_overlay_profile_activation.
DeploymentAssetTests.test_outputs_scope; DeploymentAssetTests.test_outputs_activation;
DeploymentAssetTests.test_outputs_activation.open_file.
DeploymentAssetTests.test_shared_asset_route_contract;
DeploymentAssetTests.test_shared_asset_route_precondition;
DeploymentAssetTests.test_shared_asset_route_precondition.open_file.
Variable Index: None; required/pending are test-local file-name sets, with no credentials.
"""

import ast
import hashlib
import io
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

    def check_dependency_closure(
        self, page_name, asset_prefix, published_name="FRONTEND", inherited=(), entrypoints=None
    ):
        """Functionality: Require every locally referenced page asset in one release.
        Inputs: page_name, asset_prefix, published_name, inherited assets and optional entrypoints.
        Outputs: Missing dependency assertion with file names; local source reads only.
        Logic: Read allowlist via AST; seed from HTML or entries; traverse JS once;
        resolve nested HTML and ./ or ../ imports within frontend before rejecting escapes.
        No publisher import, so no Paramiko credential/runtime requirement.
        Constraints: Covers static imports only; not dynamic imports or CDN availability;
        inherited names explicitly denote protected assets preserved by copying current.
        """
        backend = Path(__file__).resolve().parents[1]
        publisher = ast.parse((backend / "tools/deploy_phone_live.py").read_text(encoding="utf-8"))
        declarations = [
            ast.literal_eval(node.value) for node in publisher.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == published_name
                    for target in node.targets)
        ]
        self.assertEqual(len(declarations), 1, "Publisher must have one literal selected allowlist")
        published = set(declarations[0])
        frontend = (backend.parent / "frontend").resolve()
        html = (frontend / page_name).read_text(encoding="utf-8")
        required = {page_name}
        pending = [frontend / name for name in (entrypoints or re.findall(
            r'(?:src|href)="' + re.escape(asset_prefix) + r'([^"?#]+)"', html
        ))]
        self.assertTrue(pending, f"No local assets found in {page_name}")
        while pending:
            path = pending.pop().resolve()
            self.assertTrue(path.is_relative_to(frontend), str(path))
            name = path.relative_to(frontend).as_posix()
            if name in required:
                continue
            required.add(name)
            if path.suffix == ".js":
                source = path.read_text(encoding="utf-8")
                imports = re.findall(r"(?:\bfrom\s*|\bimport\s*)['\"](\.\.?/[^'\"]+)['\"]", source)
                pending.extend(path.parent / item for item in imports)
        self.assertFalse(
            required - published - set(inherited),
            f"Unpublished {page_name} assets: {required - published - set(inherited)}",
        )

    def test_maps_dependency_closure(self):
        """Functionality: Preserve complete map and language asset publication.
        Inputs: Actual maps.html and ../assets/ references. Outputs: Dependency assertions.
        Logic: Traverse the common static closure. Constraints: No remote deployment or CDN check.
        """
        self.check_dependency_closure("maps.html", "../assets/")

    def test_navigation_speech_profiles(self):
        """Functionality: Keep shared maps speech deployable in each profile that publishes maps.
        Inputs: Real nested entry/imports and full/navigation/outputs literal lists.
        Outputs: Complete runtime closure and explicit bridge test/checker presence.
        Logic: Full release checks current HTML; legacy scopes check the shared speech entry.
        test_agent_overlay_refusal requires legacy scopes to reject current agent HTML.
        Constraints: No routing upgrade, SSH or actual deployment is performed.
        """
        from tools import deploy_phone_live as publisher

        required = {
            "integration/maps-navigation-speech.js", "integration/maps-navigation-bridge.js",
            "integration/maps-navigation-speech.css", "integration/navigation-speech.js",
            "integration/events.js", "integration/catalog.js",
            "tests/integration-maps-navigation-bridge.test.mjs",
            "tools/check_maps_navigation_speech_browser.mjs",
            "tools/check_local_navigation_browser.mjs",
        }
        for name in ("FRONTEND", "NAVIGATION_FRONTEND", "OUTPUT_FRONTEND"):
            with self.subTest(profile=name):
                self.check_dependency_closure(
                    "maps.html", "../assets/", name,
                    inherited=("camera.css",) if name == "NAVIGATION_FRONTEND" else (),
                    entrypoints=None if name == "FRONTEND" else
                    ("integration/maps-navigation-speech.js",),
                )
                self.assertLessEqual(required, set(getattr(publisher, name)))

    def test_camera_dependency_closure(self):
        """Functionality: Include the camera page's manual vibration module in publication.
        Inputs: Actual index.html and ./assets/ references. Outputs: Dependency assertions.
        Logic: Traverse all static dependencies from the page.
        Constraints: No SSH or hardware calls.
        """
        self.check_dependency_closure("index.html", "./assets/")

    def test_scene_files_published(self):
        """Functionality: Require scene-description runtime files in the full release lists.
        Inputs: Literal BACKEND/FRONTEND declarations read via AST (no Paramiko import).
        Outputs: Missing-name assertion; Logic: config/urls.py imports core.scene/core.vlm, so a
        release copying only listed files over an older tree would fail to start without them.
        Constraints: Static list check only; overlay profiles are intentionally not extended.
        """
        backend = Path(__file__).resolve().parents[1]
        tree = ast.parse((backend / "tools/deploy_phone_live.py").read_text(encoding="utf-8"))
        lists = {
            target.id: set(ast.literal_eval(node.value))
            for node in tree.body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name) and target.id in {"BACKEND", "FRONTEND"}
        }
        self.assertLessEqual(
            {"core/scene.py", "core/vlm.py", "core/test_scene.py", "VLM.md", "config/urls.py"},
            lists["BACKEND"],
        )
        self.assertLessEqual(
            {"scene-client.js", "tests/scene-client.test.mjs"}, lists["FRONTEND"]
        )

    def test_agent_bundle(self):
        """Functionality: Include dynamic worklet/runtime/skills in a full publication.
        Inputs: actual allowlists and agent package; Outputs: complete-set assertions.
        Logic: Worklet, Markdown workflows and JSON scopes need explicit publication coverage.
        Constraints: Reads local files only; does not validate a remote deployment.
        """
        from tools import deploy_phone_live as publisher

        backend = Path(__file__).resolve().parents[1]
        required = {
            str(path.relative_to(backend)).replace("\\", "/")
            for root, pattern in ((backend / "core/agent", "*.py"),
                                  (backend / "agent_skills", "*.md"),
                                  (backend / "agent_skills", "*.json"))
            for path in root.rglob(pattern)
        }
        self.assertLessEqual(required, set(publisher.BACKEND))
        self.assertLessEqual({"voice-worklet.js", "tools/check_agent_extended.mjs"},
                             set(publisher.FRONTEND))

    def test_agent_overlay_refusal(self):
        """Functionality: Reject partial deployment before any remote operation.
        Inputs: current agent-enabled source, explicit valid runtime, SSH mocks.
        Outputs: refusal and zero execute calls; Logic: exercise source dependency guard.
        Constraints: No upload, credentials, remote mutation or full-deployment assertion.
        """
        from tools import deploy_phone_live as publisher

        for profile in ("vibration", "outputs", "navigation"):
            with self.subTest(profile=profile), patch.object(publisher, "execute") as command:
                with self.assertRaisesRegex(ValueError, "pre-agent source snapshot"):
                    publisher.stage(Mock(), Mock(), profile,
                                    runtime_path=publisher.PREFIX + "/runtimes/test-agent")
                command.assert_not_called()

    def test_agent_environment(self):
        """Functionality: Keep secret updates scoped and reject invalid configuration.
        Inputs: temporary synthetic dotenv and private bytes; Outputs: value/byte assertions.
        Logic: Actual whitelist, region validation and serialization; no real credentials are read.
        Constraints: Does not connect to SSH or a model; unrelated fields remain identical.
        """
        from tools import deploy_phone_live as publisher

        values = {"PRS_AGENT_API_KEY": "test-only-secret", "PRS_AGENT_BASE_URL":
                  "https://dashscope.aliyuncs.com/compatible-mode/v1",
                  "PRS_AGENT_MODEL": "qwen3.6-plus", "PRS_ASR_MODEL": "qwen3-asr-flash",
                  "PRS_AGENT_SEARCH_COUNTRY": "SGP"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("\n".join(f"{k}={v}" for k, v in values.items()) +
                            "\nDJANGO_SECRET_KEY=must-not-copy\n", encoding="utf-8")
            selected = publisher.load_agent_environment(path)
            path.write_text(path.read_text(encoding="utf-8").replace(
                "PRS_AGENT_SEARCH_COUNTRY=SGP", "PRS_AGENT_SEARCH_COUNTRY=Singapore"
            ), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "alpha-3"):
                publisher.load_agent_environment(path)
        self.assertEqual(selected, values)
        original = b"HF_INFERENCE_TOKEN='fixture'\r\nORS_API_KEY=fixture\nPRS_MCP_PROFILE=outputs\n"
        updated = publisher.merge_agent_environment(original, selected)
        self.assertTrue(updated.startswith(original))
        self.assertEqual(updated.count(b"PRS_AGENT_MODEL="), 1)
        with self.assertRaisesRegex(ValueError, "Unrelated"):
            publisher.merge_agent_environment(original, {"HF_INFERENCE_TOKEN": "change"})

    def test_full_outputs_activation(self):
        """Functionality: Preserve restricted registry and HF/ORS on a full agent release.
        Inputs: empty hash manifest and explicit mocked secret loader/SSH dispatcher.
        Outputs: shared outputs activator receives settings; Logic: actual activation routing.
        Constraints: Mocks cover external boundaries; no service/config mutation is performed.
        """
        from tools import deploy_phone_live as publisher

        manifest = {"profile": "full", "mcp_profile": "outputs", "files": {},
                    "path": publisher.PREFIX + "/releases/test-agent", "release": "test-agent",
                    "previous": publisher.PREFIX + "/releases/previous"}
        values = {"PRS_AGENT_API_KEY": "fixture-only"}
        with patch.object(publisher, "execute", return_value=manifest["previous"]), \
                patch.object(publisher, "load_agent_environment", return_value=values), \
                patch.object(publisher, "activate_outputs") as activate:
            publisher.activate(Mock(), Mock(), manifest, Path("explicit-test-env"))
        activate.assert_called_once()
        self.assertEqual(activate.call_args.kwargs, {"agent_values": values})

    def test_overlay_profile_stage(self):
        """Functionality: Verify scoped staging and reject model drift before mutation.
        Inputs: Actual qualified plan, repository-shaped temporary source, and explicit SSH/SFTP
        substitutes.
        Outputs: Exact overlay set, no retired/template files, recorded commit and early failure.
        Logic: Run the real profile selector and hash checks; mocks implement remote bytes only.
        Constraints: No real SSH, credentials, GPU, dependency installation or remote readiness.
        """
        from tools import deploy_phone_live as publisher

        backend = Path(__file__).resolve().parents[1]
        for profile, mismatch in (("vibration", False), ("vibration", True),
                                  ("live-tuning", False), ("live-tuning", True),
                                  ("navigation", False), ("navigation", True)):
            with self.subTest(profile=profile, mismatch=mismatch), \
                    tempfile.TemporaryDirectory() as directory:
                source = Path(directory)
                remote = {}
                scripts = []
                current = publisher.PREFIX + "/releases/test-current"
                remote[current + "/backend/config/urls.py"] = (
                    backend / "config/urls.py"
                ).read_bytes()
                backend_source = source / "SystemCode" / "clips" / "backend"
                frontend_source = source / "SystemCode" / "clips" / "frontend"
                for name in ("models/inference-plan.json", "models/yolo26n.json"):
                    target = backend_source / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes((backend / name).read_bytes())
                    remote[current + "/backend/" + name] = target.read_bytes()
                if mismatch:
                    remote[current + "/backend/models/inference-plan.json"] = b"changed"
                frontend = {"live-tuning": publisher.LIVE_FRONTEND,
                            "vibration": publisher.VIBRATION_FRONTEND,
                            "navigation": publisher.NAVIGATION_FRONTEND}[profile]
                backend_names = {"live-tuning": publisher.LIVE_BACKEND,
                                 "vibration": publisher.VIBRATION_BACKEND,
                                 "navigation": publisher.NAVIGATION_BACKEND}[profile]
                names = ["frontend/" + name for name in frontend]
                names += ["backend/" + name for name in backend_names]
                for name in names:
                    category, relative_name = name.split("/", 1)
                    target_root = backend_source if category == "backend" else frontend_source
                    target = target_root / relative_name
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
                with patch.object(publisher, "ROOT", backend_source), \
                        patch.object(publisher, "execute", side_effect=command):
                    if mismatch:
                        with self.assertRaisesRegex(ValueError, "active inference identity"):
                            publisher.stage(Mock(), sftp, profile, source, "a" * 40,
                                            publisher.PREFIX + "/runtimes/test-existing")
                        self.assertEqual(len(scripts), 1)
                        sftp.putfo.assert_not_called()
                    else:
                        result = publisher.stage(Mock(), sftp, profile, source, "a" * 40,
                                            publisher.PREFIX + "/runtimes/test-existing")
                        self.assertEqual(set(result["files"]), set(names))
                        self.assertEqual(result["removed"], [])
                        self.assertEqual(result["git_commit"], "a" * 40)
                        self.assertEqual(result["profile"], profile)
                        self.assertEqual(result["generated"], {})
                        self.assertFalse(any("export PRS_MCP_PROFILE=" in item for item in scripts))
                        self.assertFalse(any("nginx" in name for name in result["files"]))
                        self.assertFalse(any("names=json.loads" in script for script in scripts))

    def test_overlay_profile_activation(self):
        """Functionality: Verify activation preserves configuration and failure semantics.
        Inputs: Explicit manifest and mocked shell/readiness boundaries, success or failure.
        Outputs: One service restart, no config/token/proxy mutation or retry, truthful state.
        Logic: Run the shared source-overlay activator and inspect all issued commands.
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
                            publisher.activate_overlay(Mock(), manifest)
                        self.assertNotIn("activated", manifest)
                    else:
                        publisher.activate_overlay(Mock(), manifest)
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
        self.assertNotIn("config/urls.py", publisher.OUTPUT_BACKEND)
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

    def test_navigation_scope(self):
        """Functionality: Keep navigation publication independent of camera and service upgrades.
        Inputs: Actual navigation allowlists and explicit mocked SSH boundary.
        Outputs: Required map/MCP dependencies, absence of camera/config/model files, early refusal
        without a runtime, and source-only activation. Logic: Assert the selected release contract.
        Constraints: No network, real systemd restart, credential read or external-service proof.
        """
        from tools import deploy_phone_live as publisher

        self.assertLessEqual({"maps.html", "maps-page.js", "navigation.js", "navigation-ui.js",
                              "maps-client.js", "ai-control.js", "i18n.js", "translations.js",
                              "vibration.js", "speech-output.js"},
                             set(publisher.NAVIGATION_FRONTEND))
        self.assertFalse({"index.html", "camera.js", "camera.css", "frame-pipeline.js",
                          "live-detection-client.js"} & set(publisher.NAVIGATION_FRONTEND))
        self.assertFalse({"config/asgi.py", "config/urls.py", "core/hf_video_relay.py",
                          "models/inference-plan.json", "deploy/prs-camera.service",
                          "deploy/nginx.locations.conf"} & set(publisher.NAVIGATION_BACKEND))
        with patch.object(publisher, "execute") as command:
            with self.assertRaisesRegex(ValueError, "explicit existing runtime"):
                publisher.stage(Mock(), Mock(), "navigation")
            command.assert_not_called()
        manifest = {"profile": "navigation", "path": publisher.PREFIX + "/releases/test-nav",
                    "previous": publisher.PREFIX + "/releases/test-previous", "release": "test-nav",
                    "files": {}}
        with patch.object(publisher, "execute", return_value=manifest["previous"]), \
                patch.object(publisher, "activate_overlay") as activate:
            publisher.activate(Mock(), Mock(), manifest)
            activate.assert_called_once()
            self.assertIs(activate.call_args.args[1], manifest)

    def test_shared_asset_route_contract(self):
        """Functionality: Verify route capability from active source without executing it.
        Inputs: Valid and incompatible source bytes. Outputs: Verified hash or explicit refusal.
        Logic: Only a direct urlpatterns shared path to camera_asset qualifies; old routes,
        comments, unused declarations, malformed source and oversized data cannot bypass it.
        Constraints: No Django route patch, module execution, SSH or active configuration change.
        """
        from tools import deploy_phone_live as publisher

        valid = b'urlpatterns = [path("assets/<path:name>", camera_asset, name="shared-asset")]'
        self.assertEqual(
            publisher.require_shared_asset_route(valid), hashlib.sha256(valid).hexdigest()
        )
        for raw in (
            b'urlpatterns = [path("assets/<str:name>", camera_asset)]',
            b'# path("assets/<path:name>", camera_asset)\nurlpatterns=[]',
            b'example=path("assets/<path:name>", camera_asset)\nurlpatterns=[]',
            b'urlpatterns=[path("assets/<path:name>", other_asset)]',
            b'urlpatterns=[path("live/assets/<path:name>", camera_asset)]',
            b'urlpatterns=[', b'\xff', b' ' * 262145,
        ):
            with self.subTest(source_prefix=raw[:64]):
                with self.assertRaisesRegex(ValueError, "upgrade the shared asset route"):
                    publisher.require_shared_asset_route(raw)

    def test_shared_asset_route_precondition(self):
        """Functionality: Refuse incompatible navigation/output scopes before staging any files.
        Inputs: Real local model metadata, mocked active source and read-only remote boundaries.
        Outputs: Explicit prerequisite error, only a readlink command and no uploads/chmods.
        Logic: Both scoped profiles check active urls without changing it or retrying.
        Constraints: No network, remote mutation, source bundle writes or actual service checks.
        """
        from tools import deploy_phone_live as publisher

        backend = Path(__file__).resolve().parents[1]
        current = publisher.PREFIX + "/releases/test-current"
        for profile in ("navigation", "outputs"):
            with self.subTest(profile=profile):
                remote = {current + "/backend/" + name: (backend / name).read_bytes()
                          for name in ("models/inference-plan.json", "models/yolo26n.json")}
                remote[current + "/backend/config/urls.py"] = (
                    b'urlpatterns=[path("assets/<str:name>", camera_asset)]'
                )

                def open_file(path, mode="r", remote=remote):
                    """Functionality: Expose only selected active bytes through a mock read.
                    Inputs: Path/mode and private fixture dictionary. Outputs: In-memory stream.
                    Logic: Reject writes and unknown paths.
                    Constraints: No real SFTP or credentials.
                    """
                    self.assertEqual(mode, "rb")
                    return io.BytesIO(remote[path])

                sftp = Mock()
                sftp.open.side_effect = open_file
                # Functionality: Exercise the route prerequisite using a pre-agent source boundary.
                # Logic: Only plan/model files are needed before the route check.
                # Constraints: Current agent HTML refusal is independently tested above.
                with tempfile.TemporaryDirectory() as directory, \
                        patch.object(publisher, "execute", return_value=current) as command:
                    source = Path(directory)
                    for name in ("models/inference-plan.json", "models/yolo26n.json"):
                        target = source / "SystemCode" / "clips" / "backend" / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes((backend / name).read_bytes())
                    with self.assertRaisesRegex(ValueError, "upgrade the shared asset route"):
                        publisher.stage(Mock(), sftp, profile,
                                        source_root=source,
                                        runtime_path=publisher.PREFIX + "/runtimes/test-existing")
                command.assert_called_once()
                self.assertTrue(command.call_args.args[1].startswith("readlink"))
                sftp.putfo.assert_not_called()
                sftp.chmod.assert_not_called()
                self.assertEqual(sftp.open.call_count, 3)

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
