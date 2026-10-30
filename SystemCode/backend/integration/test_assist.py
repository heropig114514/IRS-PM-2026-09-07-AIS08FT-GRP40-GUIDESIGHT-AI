"""Responsibilities: Verify the explicit assist entry without external APIs or CUDA.
Implementation: Real ASGI packets/template/module closure; synthetic native lifecycle and env.
Related Modules: integration.assist/protocol/inference.services; integration.test_local;
SystemCode/frontend/integration/assist-*; original core.camera asset allowlist.
Declaration Index: http_call; AssistTests; AssistTests.setUp;
AssistTests.test_explicit_policy_and_worker_contract;
AssistTests.test_combined_page_and_permissions;
AssistTests.test_product_and_diagnostic_boundaries;
AssistTests.test_canonical_asset_closure; AssistTests.test_asset_and_route_isolation;
AssistTests.test_prepared_audio_resources_and_boundaries;
AssistTests.test_prepared_audio_manifest_and_file_failures;
AssistTests.test_prepared_audio_mobile_mount;
AssistTests.test_entry_redirect_and_incompatible_gpu_pages;
AssistTests.test_status_preserves_health_and_announces_maps;
AssistTests.test_continuous_session_stops_without_default_deadline;
AssistTests.test_continuous_disconnect_drains_native_work.
Variable Index: ORIGIN — fixed synthetic local page origin;
gate/started — synthetic thread controls, never real inference or performance evidence.
"""

import asyncio
import hashlib
import json
import re
import threading
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import urljoin, urlsplit

from asgiref.testing import ApplicationCommunicator
from django.test import SimpleTestCase

from core.camera import ASSETS as SOFTWARE_ASSETS
from core.test_frames import jpeg
from realtime.protocol import unpack_result

from .assist import ASSIST_ASSETS, AssistApplication, render_assist_page
from .errors import IntegrationError
from .inference.services import InferenceWorker
from .mobile import MobileApplication
from .protocol import local_session_seconds, model_contract, pack_request, validate_contract
from .services import FRONTEND, IntegrationApplication
from .test_local import MANIFEST, DetectorFixture, fixture_runtime, open_worker, terminal

ORIGIN = "http://127.0.0.1:8766"


async def http_call(app, path, method="GET", **changes):
    """Functionality: Exercise one bounded real ASGI HTTP operation in memory.
    Inputs: callable/path/method and explicit scope overrides. Outputs: start and body messages.
    Logic: send an empty request and wait for clean completion. Constraints: no network listener.
    """
    scope = {"type": "http", "path": path, "method": method,
             "headers": [(b"host", b"127.0.0.1:8766")], "query_string": b"", **changes}
    peer = ApplicationCommunicator(app, scope)
    await peer.send_input({"type": "http.request", "body": b""})
    start, body = await peer.receive_output(), await peer.receive_output()
    chunks = [body["body"]]
    while body.get("more_body"):
        body = await peer.receive_output()
        chunks.append(body["body"])
    await peer.wait()
    return start, b"".join(chunks)


class AssistTests(SimpleTestCase):
    """Functionality: Guard combined-entry policy, canonical dependencies and worker lifetime.
    Logic: explicit assist env plus in-memory ASGI and gated thread fixtures.
    Constraints: no real ORS/GPS/speech/camera/GPU or inference throughput claims.
    """

    def setUp(self):
        """Functionality: Select the explicit assist process policy for each isolated case.
        Inputs: test environment. Outputs: automatically restored mode.
        Logic: patch only LOCAL_MVP_MODE. Constraints: never modify project env files or secrets.
        """
        mode = patch.dict("os.environ", {"LOCAL_MVP_MODE": "assist"})
        mode.start()
        self.addCleanup(mode.stop)

    async def test_explicit_policy_and_worker_contract(self):
        """Functionality: Reject Web/worker lifetime mismatches and preserve offline admission.
        Inputs: both modes, real runtime contract and invalid mode. Outputs: explicit null/55 only.
        Logic: freeze runtime before later env change; validate against the receiving process mode.
        Constraints: constructing/checking health never loads CUDA or substitutes the algorithm.
        """
        runtime, _, factory = fixture_runtime()
        try:
            contract = runtime.contract("a" * 32)
            self.assertIsNone(contract["session_seconds"])
            self.assertEqual(validate_contract(contract), contract)
            with self.assertRaises(IntegrationError):
                validate_contract(model_contract("a" * 32, MANIFEST))
            with patch.dict("os.environ", {"LOCAL_MVP_MODE": "offline"}):
                self.assertEqual(local_session_seconds(), 55)
                self.assertIsNone(runtime.contract("b" * 32)["session_seconds"])
                with self.assertRaises(IntegrationError):
                    validate_contract(contract)
                with self.assertRaises(ValueError):
                    AssistApplication(Mock())
            with patch.dict("os.environ", {"LOCAL_MVP_MODE": "unknown"}):
                with self.assertRaises(IntegrationError):
                    local_session_seconds()
            factory.assert_not_called()
        finally:
            await runtime.close()

    async def test_combined_page_and_permissions(self):
        """Functionality: Preserve original controller controls on both explicitly owned views.
        Inputs: actual product/test templates and GET/HEAD/invalid host/method scopes.
        Outputs: stable controls, no explicit voice-alert module on the product page, same-origin
        microphone allowance for both voice-enabled views, media/GPS policy, and safe errors
        without original file edits.
        Logic: verify SRI and nonce continuity; independent manager scripts never enter this page.
        Constraints: hidden product bindings remain available to the controller; no actual browser
        permissions, CDN availability or audible output verification.
        """
        original = (FRONTEND / "maps.html").read_bytes()
        app = AssistApplication(AsyncMock())
        start, body = await http_call(app, "/maps/")
        headers = dict(start["headers"])
        self.assertEqual(start["status"], 200)
        self.assertIn(b'data-api-base="../"', body)
        self.assertIn(b'data-assist="true"', body)
        self.assertIn(b'id="navigation-speech-enabled"', body)
        self.assertIn(b'id="speech-enabled"', body)
        self.assertNotIn(b'class="product-card product-speech"', body)
        self.assertNotIn(b'id="product-speech-heading"', body)
        self.assertIn(b'id="product-driver-bindings" hidden', body)
        self.assertIn(b'id="assist-speech-start"', body)
        self.assertIn(b'/assets/integration/assist-mvp.js', body)
        self.assertNotIn(b'src="../assets/integration/maps-navigation-speech.js"', body)
        self.assertNotIn(b'src="../integration/assets/local-mvp.js"', body)
        self.assertEqual(headers[b"permissions-policy"],
                         b"geolocation=(self), camera=(self), microphone=(self)")
        self.assertIn(b'data-assist-view="product"', body)
        self.assertRegex(headers[b"content-security-policy"], rb"'nonce-[A-Za-z0-9_-]+'")
        self.assertIn(b"https://tile.openstreetmap.org", headers[b"content-security-policy"])
        self.assertIn(b"media-src 'self' blob:", headers[b"content-security-policy"])
        self.assertIn(b"worker-src 'self'", headers[b"content-security-policy"])
        self.assertIn(b"ws://127.0.0.1:8766", headers[b"content-security-policy"])
        self.assertEqual(body.count(b'integrity="sha256-'), 2)
        head, empty = await http_call(app, "/maps/", "HEAD")
        self.assertEqual(head["status"], 200)
        self.assertEqual(empty, b"")
        self.assertGreater(int(dict(head["headers"])[b"content-length"]), 0)
        self.assertEqual((await http_call(app, "/maps/", "POST"))[0]["status"], 405)
        self.assertEqual((await http_call(app, "/maps/", headers=[(b"host", b"evil.test")]))[0]
                         ["status"], 403)
        self.assertEqual((FRONTEND / "maps.html").read_bytes(), original)

    async def test_product_and_diagnostic_boundaries(self):
        """Functionality: Expose diagnostics only at the fixed sibling route with the same owner.
        Inputs: both rendered views, exact test path, invalid method/host/query and template drift.
        Outputs: explicit view markers, same-origin microphone access for both assistant views,
        working relative APIs and
        safe failures without fallback.
        Logic: no trailing slash keeps original config/route/asset URL resolution unchanged.
        Constraints: fixtures do not execute controls, acquire devices or alter the core template.
        """
        app = AssistApplication(AsyncMock())
        start, body = await http_call(app, "/maps/test")
        self.assertEqual(start["status"], 200)
        self.assertIn(b'data-assist-view="test"', body)
        self.assertIn(b'href="./"', body)
        self.assertIn(b'id="assist-video-file"', body)
        self.assertIn(b'id="speech-play"', body)
        self.assertEqual(dict(start["headers"])[b"permissions-policy"],
                         b"geolocation=(self), camera=(self), microphone=(self)")
        self.assertNotIn(b'src="../assets/integration/product-ui.js"', body)
        nonce = re.search(rb'<style nonce="([^"]+)"', body).group(1)
        self.assertIn(b"'nonce-" + nonce + b"'", dict(start["headers"])[b"content-security-policy"])
        head, empty = await http_call(app, "/maps/test", "HEAD")
        self.assertEqual(head["status"], 200)
        self.assertEqual(empty, b"")
        self.assertEqual(int(dict(head["headers"])[b"content-length"]), len(body))
        for path in ("/maps/", "/maps/test"):
            page = ORIGIN + path
            self.assertEqual(urljoin(page, "./config/"), ORIGIN + "/maps/config/")
            self.assertEqual(urljoin(page, "../assets/camera.js"), ORIGIN + "/assets/camera.js")
            self.assertEqual((await http_call(app, path, "POST"))[0]["status"], 405)
            self.assertEqual((await http_call(app, path, query_string=b"view=test"))[0]
                             ["status"], 403)
            self.assertEqual((await http_call(app, path, headers=[(b"host", b"evil.test")]))[0]
                             ["status"], 403)
        self.assertEqual((await http_call(app, "/maps/test/"))[0]["status"], 404)
        with self.assertRaises(ValueError):
            render_assist_page(view="unknown")
        for failure in (OSError("private fixture"), ValueError("private fixture")):
            with patch("integration.assist.render_assist_page", side_effect=failure):
                start, raw = await http_call(app, "/maps/")
            self.assertEqual(start["status"], 503)
            self.assertEqual(json.loads(raw), {"code": "assist_template_unavailable"})

    async def test_canonical_asset_closure(self):
        """Functionality: Check both views' static JS imports and shared live-camera stylesheet.
        Inputs: both views with live display disabled/enabled and canonical asset allowlists.
        Outputs: every local module maps to an existing /assets file; the shared style is served.
        Logic: render both feature settings, recursively resolve static imports using browser URL
        rules and inspect served bytes.
        Constraints: no legacy aliases, dynamic import, network or module execution.
        """
        todo = []
        for enabled in ("", "1"):
            with patch.dict("os.environ", {"PRS_LATENCY_PANEL": enabled,
                                           "PRS_RISK_TTL_MS": "2000"}):
                for view, path in (("product", "/maps/"), ("test", "/maps/test")):
                    html, _ = render_assist_page(view=view)
                    todo.extend(urljoin(ORIGIN + path, item) for item in
                                re.findall(r'<script[^>]+src="([^"]+)"', html.decode())
                                if not item.startswith("https://"))
        seen = set()
        app = AssistApplication(AsyncMock())
        imports = re.compile(r'(?:\bfrom\s+|\bimport\s*)["\']([^"\']+)["\']')
        while todo:
            url = todo.pop()
            if url in seen:
                continue
            seen.add(url)
            path = urlsplit(url).path
            self.assertTrue(path.startswith("/assets/"), path)
            name = path.removeprefix("/assets/")
            if name.startswith("integration/"):
                self.assertIn(name.removeprefix("integration/"), ASSIST_ASSETS)
                start, raw = await http_call(app, path)
                self.assertEqual(start["status"], 200, path)
                source = raw.decode()
            else:
                self.assertIn(name, SOFTWARE_ASSETS, path)
                source = (FRONTEND / PurePosixPath(name)).read_text(encoding="utf-8")
            for dependency in imports.findall(source):
                self.assertTrue(dependency.startswith(("./", "../", "/")), dependency)
                todo.append(urljoin(url, dependency))
        self.assertIn(ORIGIN + "/assets/speech-output.js", seen)
        self.assertIn(ORIGIN + "/assets/integration/risk-speech-cache.js", seen)
        for name in ("live-latency.js", "live-camera-overlay.js", "local-motion.js"):
            self.assertIn(ORIGIN + "/assets/integration/" + name, seen)
        style = "live-camera.css"
        self.assertEqual(ASSIST_ASSETS[style], "text/css")
        start, raw = await http_call(app, "/assets/integration/" + style)
        self.assertEqual(start["status"], 200)
        self.assertEqual(raw, (FRONTEND / "integration" / style).read_bytes())
        self.assertNotIn(ORIGIN + "/assets/integration/maps-navigation-speech.js", seen)

    async def test_asset_and_route_isolation(self):
        """Functionality: Delegate only original maps APIs/shared assets around the offline wrapper.
        Inputs: fixed allowed and forbidden paths, traversal, original scopes and disabled features.
        Outputs: exact delegation or 404/explicit disabled errors before provider execution.
        Logic: mock original callable to observe exact scope identity; local wrapper remains real.
        Constraints: no ORS, model loading, filesystem traversal or duplicate drivers.
        """
        software = AsyncMock()
        app = AssistApplication(software)
        for path in ("/maps/config/", "/maps/route/", "/assets/camera.js",
                     "/assets/speech-output.js"):
            scope = {"type": "http", "path": path}
            await app(scope, Mock(), Mock())
            self.assertIs(software.call_args.args[0], scope)
        self.assertEqual(software.await_count, 4)
        for path in ("/assets/integration/../assist.py", "/assets/../.env",
                     "/assets/integration/maps-navigation-speech.js",
                     "/assets/integration/raw.html"):
            self.assertEqual((await http_call(app, path))[0]["status"], 404)
        for path in ("/maps/unknown/", "/scene/describe/"):
            start, body = await http_call(app, path)
            self.assertEqual(start["status"], 403)
            self.assertEqual(json.loads(body)["code"], "local_only_external_disabled")
        self.assertEqual(software.await_count, 4)
        original = IntegrationApplication(software)
        self.assertEqual((await http_call(original, "/maps/config/"))[0]["status"], 403)

    async def test_prepared_audio_resources_and_boundaries(self):
        """Functionality: Serve the exact prepared manifest/source WAVs without broad file access.
        Inputs: Real pack, GET/HEAD, unknown paths, traversal, queries and unsupported methods.
        Outputs: Original bytes/hashes, correct MIME/length, and explicit refusals.
        Logic: Exercise every source resource through the actual streamed ASGI handler.
        Constraints: No TTS provider, runtime playback, local listener or audible-speech claim.
        """
        software = AsyncMock()
        app = AssistApplication(software)
        base = "/assets/risk-audio/"
        start, raw = await http_call(app, base + "manifest.json")
        self.assertEqual(start["status"], 200)
        self.assertEqual(dict(start["headers"])[b"content-type"], b"application/json")
        self.assertEqual(raw, (FRONTEND / "assets/risk-audio/manifest.json").read_bytes())
        manifest = json.loads(raw)
        for clip in manifest["clips"]:
            start, audio = await http_call(app, base + clip["path"])
            self.assertEqual(start["status"], 200)
            headers = dict(start["headers"])
            self.assertEqual(headers[b"content-type"], b"audio/wav")
            self.assertEqual(headers[b"x-content-type-options"], b"nosniff")
            self.assertEqual(int(headers[b"content-length"]), clip["byte_length"])
            self.assertEqual(len(audio), clip["byte_length"])
            self.assertEqual(hashlib.sha256(audio).hexdigest(), clip["sha256"])
        for name in ("manifest.json", manifest["clips"][0]["path"]):
            start, raw = await http_call(app, base + name, "HEAD")
            self.assertEqual(start["status"], 200)
            self.assertEqual(raw, b"")
            self.assertGreater(int(dict(start["headers"])[b"content-length"]), 0)
            self.assertEqual((await http_call(app, base + name, "POST"))[0]["status"], 405)
            self.assertEqual((await http_call(app, base + name, query_string=b"v=1"))[0]
                             ["status"], 404)
        for name in ("", "README.md", "demos/zh-CN-person-front-left-approaching.wav",
                     "../speech-output.js", "zh-CN/../../.env", "zh-CN\\file.wav",
                     "%2e%2e/.env", "manifest.json/", "zh-CN/en-US-prefix-" + "a" * 64 + ".wav",
                     "zh-CN/zh-CN-prefix-" + "a" * 64 + ".wav"):
            start, _ = await http_call(app, base + name)
            self.assertEqual(start["status"], 404, name)
        software.assert_not_awaited()

    async def test_prepared_audio_manifest_and_file_failures(self):
        """Functionality: Fail closed on missing, malformed or inconsistent resource packages.
        Inputs: Temporary owned pack with unsafe metadata, missing manifest or truncated source.
        Outputs: Fixed unavailable errors or refusal before any unrelated file access.
        Logic: Patch only the frontend test root; do not alter prepared repository artifacts.
        Constraints: No hidden synthesis fallback, service launch or publication of a partial WAV.
        """
        manifest = json.loads((FRONTEND / "assets/risk-audio/manifest.json").read_bytes())
        app = AssistApplication(AsyncMock())
        with TemporaryDirectory() as directory:
            frontend = Path(directory)
            root = frontend / "assets/risk-audio"
            root.mkdir(parents=True)
            with patch("integration.assist.FRONTEND", frontend):
                start, raw = await http_call(app, "/assets/risk-audio/manifest.json")
                self.assertEqual(start["status"], 503)
                self.assertEqual(json.loads(raw), {"code": "risk_audio_unavailable"})
                for value in (b"{", b"[]", b"x" * 262145):
                    (root / "manifest.json").write_bytes(value)
                    start, _ = await http_call(app, "/assets/risk-audio/manifest.json")
                    self.assertEqual(start["status"], 503)
                unsafe = json.loads(json.dumps(manifest))
                unsafe["clips"][0]["path"] = "../private.wav"
                (root / "manifest.json").write_text(json.dumps(unsafe), encoding="utf-8")
                self.assertEqual((await http_call(app, "/assets/risk-audio/manifest.json"))[0]
                                 ["status"], 503)
                (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
                clip = manifest["clips"][0]
                target = root / clip["path"]
                target.parent.mkdir()
                target.write_bytes(b"incomplete")
                start, raw = await http_call(app, "/assets/risk-audio/" + clip["path"])
                self.assertEqual(start["status"], 503)
                self.assertEqual(json.loads(raw), {"code": "risk_audio_unavailable"})

    async def test_prepared_audio_mobile_mount(self):
        """Functionality: Preserve prepared audio through the existing public mobile wrapper.
        Inputs: Canonical resource paths after proxy mount stripping and public relay headers.
        Outputs: Identical manifest/WAV bytes with mount-relative browser URL resolution.
        Logic: Delegate through MobileApplication's unchanged asset permission gate.
        Constraints: In-memory evidence does not establish remote deployment or TLS availability.
        """
        public = "https://47.239.50.129"
        app = MobileApplication(AssistApplication(AsyncMock()), public_origin=public)
        manifest = json.loads((FRONTEND / "assets/risk-audio/manifest.json").read_bytes())
        for name in ("manifest.json", manifest["clips"][0]["path"]):
            path = "/assets/risk-audio/" + name
            start, raw = await http_call(
                app, path, headers=[(b"host", b"47.239.50.129"), (b"x-prs-relay", b"1")],
                client=("127.0.0.1", 51000), scheme="http", root_path="")
            self.assertEqual(start["status"], 200)
            self.assertEqual(raw, (FRONTEND / "assets/risk-audio" / name).read_bytes())
            self.assertEqual(urljoin(public + "/mvp/maps/test", ".." + path),
                             public + "/mvp" + path)

    async def test_status_preserves_health_and_announces_maps(self):
        """Functionality: Expose actual worker health without falsely describing maps as disabled.
        Inputs: unloaded health fixture. Outputs: assist policy, null session and safe fields.
        Logic: preserve original status validation and underlying worker values.
        Constraints: no external health/model work or ORS credential exposure.
        """
        health = {"status": "ok", "provider": "local", "loaded": False, "busy": False,
                  "ready": False, "session_seconds": None}
        software = AsyncMock()
        local = IntegrationApplication(software, health_reader=Mock(return_value=health))
        start, raw = await http_call(AssistApplication(software, local), "/integration/status/")
        body = json.loads(raw)
        self.assertEqual(start["status"], 200)
        self.assertEqual(body["mode"], "assist")
        self.assertEqual(body["external_services"], "explicit_maps_only")
        self.assertIsNone(body["session_seconds"])
        self.assertFalse(body["loaded"])
        self.assertNotIn("ORS_API_KEY", raw.decode())

    async def test_entry_redirect_and_incompatible_gpu_pages(self):
        """Functionality: Keep continuous-mode users on the compatible combined GPU entry.
        Inputs: root/standalone page GET/HEAD/POST, navigation replay and external-feature paths.
        Outputs: fixed maps redirect, explicit 409, preserved replay and unchanged provider guards.
        Logic: intercept before the offline renderer or software handlers can advertise old clients.
        Constraints: no provider switching, reconnect, original-page changes or external API calls.
        """
        software = AsyncMock()
        app = AssistApplication(software)
        for method in ("GET", "HEAD"):
            start, body = await http_call(app, "/", method)
            self.assertEqual(start["status"], 302)
            self.assertEqual(dict(start["headers"])[b"location"], b"/maps/")
            self.assertEqual(body, b"")
        self.assertEqual((await http_call(app, "/", "POST"))[0]["status"], 405)
        for path in ("/live/", "/integration/video/"):
            start, raw = await http_call(app, path)
            self.assertEqual(start["status"], 409)
            self.assertEqual(json.loads(raw), {
                "code": "assist_entry_required",
                "detail": "统一模式请使用/maps/；独立55秒测试请用默认离线启动器",
            })
            head, empty = await http_call(app, path, "HEAD")
            self.assertEqual(head["status"], 409)
            self.assertEqual(empty, b"")
            self.assertEqual(dict(head["headers"])[b"content-length"], str(len(raw)).encode())
        self.assertEqual((await http_call(app, "/integration/navigation/"))[0]["status"], 200)
        start, raw = await http_call(app, "/scene/describe/", "POST")
        self.assertEqual(start["status"], 403)
        self.assertEqual(json.loads(raw)["code"], "local_only_external_disabled")
        software.assert_not_awaited()
        with patch.dict("os.environ", {"LOCAL_MVP_MODE": "offline"}):
            original = IntegrationApplication(software)
            self.assertEqual((await http_call(original, "/live/"))[0]["status"], 200)
            self.assertEqual((await http_call(original, "/integration/video/"))[0]["status"], 200)

    async def test_continuous_session_stops_without_default_deadline(self):
        """Functionality: Keep explicit continuous sessions alive beyond the old local lifetime.
        Inputs: short patched offline deadline and two real PRF2 fixture packets.
        Outputs: second ordered result after deadline, explicit stop and released admission.
        Logic: only the transport lifetime is patched; inference and source timestamps stay exact.
        Constraints: synthetic fixture latency is not GPU or long-session performance evidence.
        """
        runtime, _, _ = fixture_runtime()
        try:
            with patch("integration.inference.services.SESSION_SECONDS", 0.02):
                peer, hello = await open_worker(InferenceWorker(runtime))
                self.assertIsNone(hello["session_seconds"])
                await peer.send_input({"type": "websocket.receive",
                                       "bytes": pack_request(0, 11.125, jpeg())})
                first, _ = unpack_result((await peer.receive_output())["bytes"], 0)
                self.assertEqual(first["capture_time_s"], 11.125)
                await asyncio.sleep(0.05)
                self.assertFalse(peer.future.done())
                await peer.send_input({"type": "websocket.receive",
                                       "bytes": pack_request(1, 11.25, jpeg())})
                second, _ = unpack_result((await peer.receive_output())["bytes"], 1)
                self.assertEqual(second["capture_time_s"], 11.25)
                await peer.send_input({"type": "websocket.receive", "text": '{"kind":"stop"}'})
                self.assertEqual((await terminal(peer))["frames"], 2)
                self.assertFalse(runtime.busy)
        finally:
            await runtime.close()

    async def test_continuous_disconnect_drains_native_work(self):
        """Functionality: Preserve continuous-session admission until disconnected native work ends.
        Inputs: gated synthetic native frame followed by browser transport disconnect.
        Outputs: busy until actual completion and no automatic second session.
        Logic: worker cleanup signals the continuous wait, cancels siblings and joins the thread.
        Constraints: cannot prove CUDA fault recovery; no native cancellation or algorithm mutation.
        """
        gate, started = threading.Event(), threading.Event()
        runtime, _, _ = fixture_runtime(DetectorFixture(gate, started))
        try:
            peer, _ = await open_worker(InferenceWorker(runtime))
            await peer.send_input({"type": "websocket.receive",
                                   "bytes": pack_request(0, 9, jpeg())})
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            await peer.send_input({"type": "websocket.disconnect", "code": 1000})
            await asyncio.sleep(0.02)
            self.assertTrue(runtime.busy)
            self.assertFalse(peer.future.done())
            gate.set()
            await peer.wait(timeout=2)
            self.assertFalse(runtime.busy)
        finally:
            gate.set()
            await runtime.close()
