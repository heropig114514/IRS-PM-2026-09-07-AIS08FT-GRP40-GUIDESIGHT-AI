/* Responsibilities: Evaluate real Qwen→MCP→browser workflows with explicit device/ORS/media fixtures.
 * Implementation: Dedicated Chrome, actual app/ASGI/WS, natural-language requests, diagnostic route
 * coverage, canonical product camera user-gesture gating and user-started camera stop, explicit
 * test-page speech activation, automatic
 * backend reply speech, and UI assertions.
 * Related Modules: backend/core/agent, agent-client/device, maps/camera controllers.
 * Declaration Index: installDevices — install explicit native-device fixtures;
 * installDevices.fix — return one synthetic NUS position; installDevices.denied — deliver a synthetic denial;
 * installDevices.geo.getCurrentPosition — deliver a fixture fix;
 * installDevices.geo.watchPosition — own a periodic synthetic watch;
 * installDevices.geo.clearWatch — release that watch; installDevices.geo.watchPosition.report — emit one active fix;
 * ask — submit one live turn, require a new assistant reply, and collect safe evidence;
 * checkAudio — verify synthetic recorded speech stops a real product camera stream;
 * main — run live-Qwen diagnostic and product interaction acceptance.
 * Variable Index: agentSpeechEvidence — body-free playback and guidance request lifecycle metadata;
 * PRS_PLAYWRIGHT_PATH/PRS_FAKE_CAMERA_VIDEO are required runtime/scene inputs;
 * PRS_TLS_SPKI optionally pins a verified
 * deployment certificate key; args/report/context belong to main.
 * Constraints: --live-qwen explicitly authorizes paid Qwen calls; assistant speech requires the page's
 * explicit activation gesture. GPS/ORS/camera frames are synthetic,
 * but inference uses the actual public local-GPU relay; no deployment, physical device, real-route
 * accuracy or speaker verification. */
import assert from "node:assert/strict";
import {createRequire} from "node:module";
import {mkdir, stat, writeFile} from "node:fs/promises";
import {dirname, resolve} from "node:path";
import {pathToFileURL} from "node:url";

/* Functionality: Install explicitly synthetic GPS and rear-camera adapter.
 * Inputs: native Chrome fake media, window; Outputs: deterministic GPS/permission controls.
 * Logic: Remove only unsupported fake facingMode, keep all actual capture/transport code.
 * Constraints: Test-only injection; never shipped in application or interpreted as real GPS. */
export function installDevices() {
  const native = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
  navigator.mediaDevices.getUserMedia = constraints => {
    if (constraints.video && typeof constraints.video === "object") {
      const video = {...constraints.video}; delete video.facingMode;
      return native({...constraints, video});
    }
    return native(constraints);
  };
  const watches = new Map(); let index = 0;
  window.agentFixture = {denyLocation: false, locateCalls: 0};
  // Functionality: Create GPS fix; Inputs: clock; Outputs: explicit NUS fixture.
  // Logic: 5m synthetic accuracy; Constraints: never claim real device position.
  function fix() { return {coords: {longitude: 103.7766, latitude: 1.2971, accuracy: 5}, timestamp: Date.now()}; }
  // Functionality: Deliver fake denial; Inputs: callback; Outputs: browser-shaped error.
  // Logic: explicit fixture flag; Constraints: not browser permission UI verification.
  function denied(error) { error({code: 1}); }
  const geo = {
    // Functionality: Return one fix. Inputs: callbacks; Outputs: fixture data/counter.
    // Logic: explicit denial option; Constraints: no external GPS.
    getCurrentPosition(success, error) {
      window.agentFixture.locateCalls++;
      setTimeout(() => window.agentFixture.denyLocation ? denied(error) : success(fix()), 50);
    },
    // Functionality: Maintain synthetic GPS. Inputs: callbacks; Outputs: handle.
    // Logic: periodic fresh fixes; Constraints: only test interval, no actual sensor.
    watchPosition(success, error) {
      const id = ++index;
      const report = () => window.agentFixture.denyLocation ? denied(error) : success(fix());
      watches.set(id, setInterval(report, 1000)); setTimeout(report, 50); return id;
    },
    // Functionality: Cancel fixture watch. Inputs: id; Outputs: timer removal.
    // Logic: explicit cleanup; Constraints: unrelated watches unchanged.
    clearWatch(id) { clearInterval(watches.get(id)); watches.delete(id); },
  };
  Object.defineProperty(navigator, "geolocation", {configurable: true, value: geo});
}

/* Functionality: Submit one natural-language turn and collect its corresponding UI evidence.
 * Inputs: page, text and report; Outputs: completed turn evidence with the new assistant response.
 * Logic: Record prior user/assistant counts, then require a new user message, idle state and a new reply.
 * Constraints: No model retries or response rewriting; prevents stale prior replies from passing. */
export async function ask(page, text, report) {
  const started = Date.now();
  await page.locator("#agent-panel[data-connected=true][data-busy=false]").waitFor();
  const userCount = await page.locator(".agent-message.you").count();
  const assistantCount = await page.locator(".agent-message.assistant").count();
  await page.locator("#agent-input").fill(text);
  await page.locator("#agent-send").click();
  await page.waitForFunction(previous => document.querySelectorAll(".agent-message.you").length > previous,
    userCount, {timeout: 185000});
  await page.locator("#agent-panel[data-connected=true][data-busy=false]").waitFor({timeout: 185000});
  const currentAssistantCount = await page.locator(".agent-message.assistant").count();
  assert.ok(currentAssistantCount > assistantCount,
    `Turn completed without a new assistant reply: ${await page.locator("#agent-status").textContent()}`);
  const entry = {input: text, elapsed_ms: Date.now() - started, url: page.url(),
    reply: await page.locator(".agent-message.assistant").last().innerText(),
    activity: await page.locator("#agent-activity").textContent(),
    tools: report.toolEvents.splice(0),
    diagnostic: await page.locator("#maps-message, #message").allTextContents()};
  report.turns.push(entry);
  console.log(JSON.stringify(entry));
  return entry;
}

/* Functionality: Verify recorded speech actually closes a running camera.
 * Inputs: connected page, evidence report, Chrome configured with explicit fake audio.
 * Outputs: transcript, tool evidence and independent released-stream assertion.
 * Logic: User starts the existing product camera through its real button, holds the actual pointer for
 * four seconds while recording, then ASR/agent must stop it.
 * Constraints: Synthetic Chinese voice and fake camera; actual local inference; no physical microphone or recognition-rate claim. */
export async function checkAudio(page, report) {
  await page.locator("#agent-panel[data-connected=true][data-busy=false]").waitFor();
  await page.locator("#product-camera-start").click();
  await page.waitForFunction(async () => {
    const {cameraApp} = await import(new URL("../assets/camera.js", location.href).href);
    return cameraApp.stream?.active && cameraApp.frameTimings;
  });
  report.toolEvents.length = 0;
  const recordButton = page.locator("#agent-record");
  await recordButton.scrollIntoViewIfNeeded();
  const bounds = await recordButton.boundingBox();
  assert.ok(bounds, "Voice button must be visible before recording");
  await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
  await page.mouse.down();
  try {
    await page.waitForFunction(() => /Release to send|松开发送|Finish.*send|结束并发送/.test(
      document.querySelector("#agent-record").textContent));
    await page.waitForTimeout(4000);
  } finally { await page.mouse.up(); }
  await page.locator("#agent-panel[data-connected=true][data-busy=false]").waitFor({timeout: 185000});
  const transcript = await page.locator(".agent-message.you").last().innerText();
  assert.match(transcript, /关闭|摄像头/);
  assert.ok(report.toolEvents.some(tool => tool.name === "camera_stop"));
  assert.equal(await page.evaluate(async () => (await import(new URL("../assets/camera.js", location.href).href)).cameraApp.stream), null);
  report.audio = {transcript, reply: await page.locator(".agent-message.assistant").last().innerText(),
    tools: report.toolEvents.splice(0), camera_released: true,
    source: "Windows synthetic Chinese speech via Chrome fake microphone; actual product local camera stream released"};
  report.checks.push("microphone_worklet_wav_qwen_asr_camera_stop");
}

/* Functionality: Run multi-turn product-page/device/UI scenarios with a real Qwen backend.
 * Inputs: URL OUTPUT --live-qwen, optional --audio=absolute WAV; Playwright package/Y4M scene/TLS pin env.
 * Outputs: JSON report/screenshots; Logic: assert visible state independently of model reply, verify
 * backend playback of a live assistant reply, confirm the camera user-gesture gate, and verify cleanup.
 * Constraints: Explicit synthetic device/ORS boundaries; no route handoff to legacy /live/; no retries. */
async function main() {
  const [url, output, mode, audioArg] = process.argv.slice(2);
  const cameraFile = process.env.PRS_FAKE_CAMERA_VIDEO;
  if (!url || !output || mode !== "--live-qwen" || !process.env.PRS_PLAYWRIGHT_PATH || !cameraFile)
    throw new Error("Supply URL OUTPUT --live-qwen and set PRS_PLAYWRIGHT_PATH/PRS_FAKE_CAMERA_VIDEO");
  if (!cameraFile.toLowerCase().endsWith(".y4m") || !(await stat(cameraFile)).isFile())
    throw new Error("PRS_FAKE_CAMERA_VIDEO must be an existing Y4M scene file");
  const {chromium} = createRequire(import.meta.url)(process.env.PRS_PLAYWRIGHT_PATH);
  const args = ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
    `--use-file-for-fake-video-capture=${cameraFile}`];
  if (process.env.PRS_TLS_SPKI) {
    if (!/^[A-Za-z0-9+/]{43}=$/.test(process.env.PRS_TLS_SPKI)) throw new Error("Invalid verified TLS key pin");
    args.push("--ignore-certificate-errors-spki-list=" + process.env.PRS_TLS_SPKI);
  }
  if (audioArg) {
    if (!audioArg.startsWith("--audio=")) throw new Error("Invalid audio option");
    args.push("--use-file-for-fake-audio-capture=" + audioArg.slice(8));
  }
  const browser = await chromium.launch({channel: "chrome", headless: true, args});
  const report = {complete: false, scope: "Real Qwen/MCP/ASGI/WS/Chrome; synthetic GPS/ORS/rear camera", turns: [], checks: [], errors: [], toolEvents: []};
  await mkdir(dirname(output), {recursive: true});
  try {
    const context = await browser.newContext({viewport: {width: 390, height: 844}, permissions: ["camera", "microphone"]});
    await context.addInitScript(installDevices);
    await context.route("**/maps/config/", route => route.fulfill({json: {configured: true, provider: "openrouteservice", profile: "foot-walking"}}));
    let routeCalls = 0;
    await context.route("**/maps/route/", route => {
      routeCalls++;
      const {start, end} = route.request().postDataJSON();
      assert.deepEqual(start, [103.7766, 1.2971]);
      route.fulfill({json: {provider: "openrouteservice", profile: "foot-walking", request_id: "explicit-agent-fixture",
        geometry: {type: "LineString", coordinates: [start, end]},
        summary: {distance_m: 400, duration_s: 300},
        steps: [{type: 11, instruction: "Synthetic test route", distance_m: 400, duration_s: 300, way_points: [0, 1]},
          {type: 10, instruction: "Synthetic arrival", distance_m: 0, duration_s: 0, way_points: [1, 1]}],
        attribution: "Synthetic ORS fixture"}});
    });
    const page = await context.newPage();
    page.on("websocket", socket => {
      if (!socket.url().endsWith("/ws/agent/")) return;
      socket.on("framereceived", ({payload}) => {
        const event = JSON.parse(String(payload));
        if (event.type === "tool_start") report.toolEvents.push({name: event.name, arguments: event.arguments});
      });
    });
    page.on("pageerror", error => report.errors.push(error.message));
    await page.goto(url);
    await page.evaluate(async () => {
      const asset = new URL("../assets/", location.href);
      const {guidanceManager, assistReady} = await import(new URL("integration/assist-mvp.js", asset));
      await assistReady;
      const {speechOutput} = await import(new URL("speech-output.js", asset));
      const evidence = window.agentSpeechEvidence = {requests: [], driver: [], guidance: []};
      speechOutput.subscribe(event => {
        if (event.phase === "submitted") {
          const request = speechOutput.lastRequest;
          evidence.requests.push({request_id: request.id, text_length: request.text_length,
            language: request.language, voice_id: request.voice_id, service: request.service});
        }
        evidence.driver.push({phase: event.phase, request_id: event.request_id, reason: event.reason});
      });
      guidanceManager.subscribe(event => evidence.guidance.push({phase: event.phase, channel: event.channel,
        kind: event.kind, request_id: event.request_id, reason: event.reason}));
    });
    await page.locator("#assist-speech-start:not([disabled])").waitFor({timeout: 15000});
    await page.locator("#assist-speech-start").click();
    await page.waitForFunction(async () => {
      const {speechActivation} = await import(new URL("../assets/integration/assist-mvp.js", location.href).href);
      return speechActivation.describe().ready;
    }, null, {timeout: 45000});
    report.checks.push("explicit_user_gesture_activates_test_page_speech");
    await ask(page, "开启导航", report);
    await page.waitForFunction(() => window.agentSpeechEvidence.guidance.some(event =>
      event.phase === "output_end" && event.channel === "speech" && event.kind === "assistant"),
    null, {timeout: 45000});
    const assistantSpeech = await page.evaluate(() => {
      const events = window.agentSpeechEvidence, start = events.guidance.find(event =>
        event.phase === "output_start" && event.channel === "speech" && event.kind === "assistant");
      const end = events.guidance.find(event => event.phase === "output_end" &&
        event.channel === "speech" && event.kind === "assistant" && event.request_id === start?.request_id);
      const request = events.requests.find(event => event.request_id === start?.request_id);
      const audio = events.driver.filter(event => event.request_id === start?.request_id).map(event => event.phase);
      return {start: Boolean(start), end: Boolean(end), request, audio_start: audio.includes("start"), audio_end: audio.includes("end")};
    });
    assert.equal(assistantSpeech.start, true); assert.equal(assistantSpeech.end, true);
    assert.equal(assistantSpeech.request?.service, "backend"); assert.equal(assistantSpeech.request?.voice_id, "Cherry");
    assert.equal(assistantSpeech.audio_start, true); assert.equal(assistantSpeech.audio_end, true);
    report.checks.push("live_assistant_reply_auto_spoken_by_backend_stream");
    assert.equal(new URL(page.url()).pathname, "/mvp/maps/test");
    assert.equal(routeCalls, 0);
    assert.ok(!report.turns.at(-1).tools.some(tool => tool.name === "places_search"), "No invented destination searches");
    assert.match(report.turns.at(-1).reply, /哪|终点|目的地/);
    report.checks.push("missing_destination_question_without_search_or_route");
    await ask(page, "终点是经度103.7735、纬度1.2991，从我的当前位置出发，请规划路线并开始导航。", report);
    assert.equal(routeCalls, 1);
    assert.ok(await page.evaluate(() => window.agentFixture.locateCalls > 0));
    assert.equal(await page.evaluate(async () => (await import(new URL("../assets/navigation-ui.js", location.href).href)).navigation.describe().state), "navigating");
    report.checks.push("device_origin_route_plan_navigation_verified");
    await ask(page, "现在还有多远？", report);
    assert.equal(routeCalls, 1);
    report.checks.push("read_progress_without_replanning");
    await ask(page, "停止导航", report);
    assert.equal(await page.evaluate(async () => (await import(new URL("../assets/navigation-ui.js", location.href).href)).navigation.describe().active), false);
    report.checks.push("stop_navigation");
    await page.locator("#agent-panel").screenshot({path: output + ".maps.png"});
    /* Functionality: Scope camera gesture checks to the canonical public product page.
     * Inputs: Current same-origin maps test URL. Outputs: Product entry with the same browser session.
     * Logic: Open the user-facing route explicitly; this check does not infer page handoff from a camera request.
     * Constraints: no permissions or camera stream are acquired by navigation. */
    const productUrl = new URL("./", page.url()).href;
    await page.goto(productUrl);
    await page.locator("#agent-panel[data-connected=true][data-busy=false]").waitFor({timeout: 15000});
    await ask(page, "开启环境感知。", report);
    assert.match(page.url(), /\/mvp\/maps\/$/);
    assert.match(report.turns.at(-1).reply, /点击|Tap|click|Start surroundings/i);
    assert.ok(!report.turns.at(-1).tools.some(tool => tool.name === "camera_start"),
      "Product camera start must remain a direct browser gesture");
    const beforeCameraTap = await page.evaluate(async () => {
      const {cameraApp} = await import(new URL("../assets/camera.js", location.href).href);
      return {active: Boolean(cameraApp.stream?.active), mode: cameraApp.mode};
    });
    assert.deepEqual(beforeCameraTap, {active: false, mode: "idle"});
    report.checks.push("assistant_preserves_product_camera_user_gesture_gate");
    /* Functionality: Test assistant stop against a camera stream started through the required direct gesture.
     * Inputs: Current product page, synthetic Chrome camera and real public inference relay.
     * Outputs: Active camera state followed by a tool-confirmed idle state with no retained stream.
     * Logic: Start once through the visible control, then issue one natural-language stop request.
     * Constraints: The captured frames are synthetic; this does not test a physical camera. */
    await page.locator("#product-camera-start").click();
    await page.waitForFunction(async () => {
      const {cameraApp} = await import(new URL("../assets/camera.js", location.href).href);
      return cameraApp.stream?.active && cameraApp.frameTimings;
    }, null, {timeout: 30000});
    assert.equal(await page.evaluate(async () => {
      const {cameraApp} = await import(new URL("../assets/camera.js", location.href).href);
      return cameraApp.stream?.active === true;
    }), true, "Direct camera gesture must own an active stream before assistant stop");
    await ask(page, "关闭摄像头", report);
    assert.ok(report.turns.at(-1).tools.some(tool => tool.name === "camera_stop"));
    assert.deepEqual(await page.evaluate(async () => {
      const {cameraApp} = await import(new URL("../assets/camera.js", location.href).href);
      return {active: Boolean(cameraApp.stream?.active), mode: cameraApp.mode, stream_cleared: cameraApp.stream === null};
    }), {active: false, mode: "idle", stream_cleared: true});
    report.checks.push("natural_language_camera_stop_preserves_idle_resource_state");
    await ask(page, "把界面切换成英文", report);
    assert.equal(await page.locator("#language").inputValue(), "en");
    assert.equal(await page.locator("#agent-send").innerText(), "Send");
    report.checks.push("language_switch");
    const vibrationEnabled = await page.locator("#vibration-enabled").isChecked();
    await ask(page, "让手机短震一下", report);
    assert.equal(await page.locator("#vibration-enabled").isChecked(), vibrationEnabled);
    assert.ok(report.turns.at(-1).tools.some(tool => tool.name === "vibration_status"));
    assert.equal(report.turns.at(-1).tools.some(tool => tool.name === "vibration_test"), vibrationEnabled);
    report.checks.push("vibration_respects_user_enable");
    const speechEnabled = await page.locator("#speech-enabled").isChecked();
    await ask(page, "Please say the word Hello aloud in English.", report);
    assert.equal(await page.locator("#speech-enabled").isChecked(), speechEnabled);
    assert.ok(report.turns.at(-1).tools.some(tool => tool.name === "speech_status"));
    assert.equal(report.turns.at(-1).tools.some(tool => tool.name === "speech_speak"), speechEnabled);
    if (speechEnabled) assert.ok(report.turns.at(-1).tools.some(tool => tool.name === "speech_speak" &&
      tool.arguments.language === "en-US" && tool.arguments.text === "Hello"));
    report.checks.push("speech_request_respects_user_enable_and_matches_english");
    if (audioArg) await checkAudio(page, report);
    for (const width of [320, 390, 1280]) {
      await page.setViewportSize({width, height: 900});
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    }
    await page.setViewportSize({width: 390, height: 844});
    await page.locator("#agent-panel").screenshot({path: output + ".camera.png"});
    report.checks.push("responsive_320_390_1280");
    assert.deepEqual(report.errors, []);
    report.complete = true;
  } catch (error) {
    report.errors.push(error.stack);
    throw error;
  } finally {
    await writeFile(output, JSON.stringify(report, null, 2));
    await browser.close();
  }
}
if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) await main();
