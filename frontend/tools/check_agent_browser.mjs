/* Responsibilities: Evaluate real Qwen→MCP→browser workflows with explicit device/ORS fixtures.
 * Implementation: Dedicated Chrome, actual app/ASGI/WS, natural-language requests and UI assertions.
 * Related Modules: backend/core/agent, agent-client/device, maps/camera controllers.
 * Declaration Index: installDevices; installDevices.fix; installDevices.denied;
 * installDevices.geo.getCurrentPosition/watchPosition/clearWatch;
 * installDevices.geo.watchPosition.report; ask; checkAudio; main.
 * Variable Index: None; PRS_PLAYWRIGHT_PATH is required; PRS_TLS_SPKI optionally pins a verified
 * deployment certificate key; args/report/context belong to main.
 * Constraints: --live-qwen explicitly authorizes paid Qwen calls. GPS/ORS/camera are synthetic;
 * no GPU, deployment, physical device, real-route accuracy or speaker verification. */
import assert from "node:assert/strict";
import {createRequire} from "node:module";
import {mkdir, writeFile} from "node:fs/promises";
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

/* Functionality: Submit a real natural-language turn and collect its UI evidence.
 * Inputs: page, text and report; Outputs: completed turn evidence.
 * Logic: Wait for a new user message and final idle across page handoffs.
 * Constraints: No model retries or response rewriting; fails visibly at the 185s outer limit. */
export async function ask(page, text, report) {
  const started = Date.now();
  await page.locator("#agent-panel[data-connected=true][data-busy=false]").waitFor();
  const count = await page.locator(".agent-message.you").count();
  await page.locator("#agent-input").fill(text);
  await page.locator("#agent-send").click();
  await page.waitForFunction(previous => document.querySelectorAll(".agent-message.you").length > previous, count, {timeout: 185000});
  await page.locator("#agent-panel[data-connected=true][data-busy=false]").waitFor({timeout: 185000});
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
 * Logic: User starts Echo through the real button, records 4s, then ASR/agent must stop it.
 * Constraints: Synthetic Chinese voice/camera; no physical microphone or recognition-rate claim. */
export async function checkAudio(page, report) {
  await page.locator("#agent-panel[data-connected=true][data-busy=false]").waitFor();
  await page.locator("#processing").selectOption("echo");
  await page.locator("#start").click();
  await page.waitForFunction(async () => {
    const {cameraApp} = await import(new URL("./assets/camera.js", location.href).href);
    return cameraApp.stream?.active && cameraApp.frameTimings;
  });
  report.toolEvents.length = 0;
  await page.locator("#agent-record").click();
  await page.waitForFunction(() => /Finish|结束/.test(document.querySelector("#agent-record").textContent));
  await page.waitForTimeout(4000);
  await page.locator("#agent-record").click();
  await page.locator("#agent-panel[data-connected=true][data-busy=false]").waitFor({timeout: 185000});
  const transcript = await page.locator(".agent-message.you").last().innerText();
  assert.match(transcript, /关闭|摄像头/);
  assert.ok(report.toolEvents.some(tool => tool.name === "camera_stop"));
  assert.equal(await page.evaluate(async () => (await import(new URL("./assets/camera.js", location.href).href)).cameraApp.stream), null);
  report.audio = {transcript, reply: await page.locator(".agent-message.assistant").last().innerText(),
    tools: report.toolEvents.splice(0), camera_released: true,
    source: "Windows synthetic Chinese speech via Chrome fake microphone; actual running Echo camera released"};
  report.checks.push("microphone_worklet_wav_qwen_asr_camera_stop");
}

/* Functionality: Run multi-turn/cross-page/device/UI scenarios with a real Qwen backend.
 * Inputs: URL OUTPUT --live-qwen, optional --audio=absolute WAV; Playwright package/TLS pin env.
 * Outputs: JSON report/screenshots; Logic: assert visible state independently of model reply.
 * Constraints: Explicit synthetic device/ORS boundaries; no retries after failed assertions. */
async function main() {
  const [url, output, mode, audioArg] = process.argv.slice(2);
  if (!url || !output || mode !== "--live-qwen" || !process.env.PRS_PLAYWRIGHT_PATH)
    throw new Error("Supply URL OUTPUT --live-qwen and PRS_PLAYWRIGHT_PATH");
  const {chromium} = createRequire(import.meta.url)(process.env.PRS_PLAYWRIGHT_PATH);
  const args = ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"];
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
    await ask(page, "开启导航", report);
    assert.match(page.url(), /\/maps\/$/);
    assert.equal(routeCalls, 0);
    assert.ok(!report.turns.at(-1).tools.some(tool => tool.name === "places_search"), "No invented destination searches");
    assert.match(report.turns.at(-1).reply, /哪|终点|目的地/);
    report.checks.push("missing_destination_question_and_page_handoff");
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
    await ask(page, "打开摄像头，使用原样回传模式。", report);
    assert.match(page.url(), /\/live\/$/);
    const media = await page.evaluate(async () => {
      const {cameraApp} = await import(new URL("./assets/camera.js", location.href).href);
      return {active: cameraApp.stream?.active, frames: cameraApp.frameTimings?.steadyFrames};
    });
    assert.equal(media.active, true); assert.ok(media.frames >= 0);
    report.checks.push("camera_echo_live_frames_and_page_handoff");
    await ask(page, "关闭摄像头", report);
    assert.equal(await page.evaluate(async () => (await import(new URL("./assets/camera.js", location.href).href)).cameraApp.stream), null);
    report.checks.push("camera_stop_releases_track");
    await ask(page, "把界面切换成英文", report);
    assert.equal(await page.locator("#language").inputValue(), "en");
    assert.equal(await page.locator("#agent-send").innerText(), "Send");
    report.checks.push("language_switch");
    await ask(page, "让手机短震一下", report);
    assert.equal(await page.locator("#vibration-enabled").isChecked(), false);
    report.checks.push("vibration_respects_user_enable");
    await ask(page, "朗读你好", report);
    assert.equal(await page.locator("#speech-enabled").isChecked(), false);
    report.checks.push("speech_respects_user_enable");
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
