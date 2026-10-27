/* Responsibilities: Evaluate clarification, failure, cancellation and existing output/VLM operations.
 * Implementation: Real Qwen/geocoder/VLM and browser controls; explicit GPS/route/media/output fixtures.
 * Related Modules: check_agent_browser.mjs, agent skills and existing scene/output controllers.
 * Declaration Index: installOutputs; installOutputs.Utterance; installOutputs.Utterance.constructor;
 * installOutputs.Engine; installOutputs.Engine.constructor; installOutputs.Engine.getVoices;
 * installOutputs.Engine.speak; installOutputs.Engine.cancel; main.
 * Variable Index: None; PRS_PLAYWRIGHT_PATH required; PRS_TLS_SPKI optionally pins the verified
 * deployment certificate key; report/context are local evaluation state.
 * Constraints: --live-qwen required; no real GPS, motor/speaker, GPU or public deployment verified. */
import assert from "node:assert/strict";
import {createRequire} from "node:module";
import {mkdir, writeFile} from "node:fs/promises";
import {dirname} from "node:path";
import {installDevices, ask} from "./check_agent_browser.mjs";

/* Functionality: Provide explicit deterministic output hardware boundaries.
 * Inputs: browser globals; Outputs: fake native speech/vibration plus evidence counters.
 * Logic: Real controller still validates user activation and switch state.
 * Constraints: Does not produce physical sound/vibration; not a device compatibility claim. */
function installOutputs() {
  window.agentOutputs = {vibrations: [], spoken: [], cancels: 0};
  Object.defineProperty(navigator, "vibrate", {configurable: true, value: pattern => {
    window.agentOutputs.vibrations.push(pattern); return true;
  }});
  // Functionality: Model native utterance events; Logic: real EventTarget.
  // Constraints: Synthetic test object, never synthesized into audio.
  class Utterance extends EventTarget {
    // Functionality: Store requested text; Inputs: text; Outputs: utterance.
    // Logic: normal property; Constraints: retained only in test memory.
    constructor(text) { super(); this.text = text; }
  }
  // Functionality: Model a bounded output engine; Logic: asynchronous start/end events.
  // Constraints: Explicit test fixture with no real speech service.
  class Engine extends EventTarget {
    // Functionality: Initialize engine state; Inputs: none; Outputs: idle fixture.
    // Logic: no audio resources; Constraints: only test memory.
    constructor() { super(); this.speaking = false; this.pending = false; this.paused = false; }
    // Functionality: Return voices; Inputs: none; Outputs: one test voice.
    // Logic: fixed inventory; Constraints: cannot verify installed OS voices.
    getVoices() { return [{voiceURI: "fixture-zh", name: "Fixture Chinese", lang: "zh-CN", localService: true, default: true}]; }
    // Functionality: Record and deliver lifecycle events; Inputs: utterance; Outputs: event sequence.
    // Logic: generation identity guards cancellation; Constraints: no audible sound.
    speak(utterance) {
      window.agentOutputs.spoken.push(utterance.text); this.current = utterance; this.pending = true;
      setTimeout(() => {
        if (this.current !== utterance) return;
        this.pending = false; this.speaking = true; utterance.dispatchEvent(new Event("start"));
        setTimeout(() => {
          if (this.current !== utterance) return;
          this.speaking = false; this.current = null; utterance.dispatchEvent(new Event("end"));
        }, 100);
      }, 20);
    }
    // Functionality: Cancel fixture request; Inputs: current state; Outputs: cleared engine.
    // Logic: invalidate callbacks; Constraints: no claim about a physical speaker.
    cancel() { window.agentOutputs.cancels++; this.current = null; this.speaking = this.pending = false; }
  }
  Object.defineProperty(window, "SpeechSynthesisUtterance", {configurable: true, value: Utterance});
  Object.defineProperty(window, "speechSynthesis", {configurable: true, value: new Engine()});
}

/* Functionality: Run real model scenarios beyond the basic happy path.
 * Inputs: URL OUTPUT --live-qwen, package/TLS pin env; Outputs: JSON evidence/screenshots.
 * Logic: Assert independent page/controller state, retain failures, never replay failed turns.
 * Constraints: Real Qwen/ORS geocoder/VLM charges; synthetic route/device boundaries noted. */
async function main() {
  const [url, output, flag] = process.argv.slice(2);
  if (!url || !output || flag !== "--live-qwen" || !process.env.PRS_PLAYWRIGHT_PATH)
    throw new Error("Supply URL OUTPUT --live-qwen and PRS_PLAYWRIGHT_PATH");
  const {chromium} = createRequire(import.meta.url)(process.env.PRS_PLAYWRIGHT_PATH);
  const args = ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"];
  if (process.env.PRS_TLS_SPKI) {
    if (!/^[A-Za-z0-9+/]{43}=$/.test(process.env.PRS_TLS_SPKI)) throw new Error("Invalid verified TLS key pin");
    args.push("--ignore-certificate-errors-spki-list=" + process.env.PRS_TLS_SPKI);
  }
  const browser = await chromium.launch({channel: "chrome", headless: true, args});
  const report = {complete: false, scope: "Real Qwen/ORS geocoder/VLM/MCP/UI; synthetic GPS/route/camera/output engine", turns: [], checks: [], errors: [], toolEvents: []};
  await mkdir(dirname(output), {recursive: true});
  try {
    const context = await browser.newContext({viewport: {width: 390, height: 844}, permissions: ["camera"]});
    await context.addInitScript(installDevices);
    await context.addInitScript(installOutputs);
    await context.route("**/maps/config/", route => route.fulfill({json: {configured: true, provider: "openrouteservice", profile: "foot-walking"}}));
    let routeCalls = 0;
    await context.route("**/maps/route/", route => {
      routeCalls++;
      const {start, end} = route.request().postDataJSON();
      route.fulfill({json: {provider: "openrouteservice", profile: "foot-walking", request_id: "explicit-agent-fixture",
        geometry: {type: "LineString", coordinates: [start, end]}, summary: {distance_m: 400, duration_s: 300},
        steps: [{type: 11, instruction: "Synthetic route", distance_m: 400, duration_s: 300, way_points: [0, 1]},
          {type: 10, instruction: "Synthetic arrival", distance_m: 0, duration_s: 0, way_points: [1, 1]}], attribution: "Synthetic ORS route"}});
    });
    const page = await context.newPage();
    page.on("pageerror", error => report.errors.push(error.message));
    page.on("websocket", socket => {
      if (!socket.url().endsWith("/ws/agent/")) return;
      socket.on("framereceived", ({payload}) => {
        const event = JSON.parse(String(payload));
        if (event.type === "tool_start") report.toolEvents.push({name: event.name, arguments: event.arguments});
      });
    });
    await page.goto(url);
    await ask(page, "我要去 NUS Central Library，请先搜索候选让我选择，暂时不要规划路线或开始导航。", report);
    assert.ok(report.turns.at(-1).tools.some(tool => tool.name === "places_search"));
    assert.equal(routeCalls, 0);
    assert.match(report.turns.at(-1).reply, /Library|图书馆/);
    report.checks.push("real_geocoder_candidates_and_clarification");
    await ask(page, "选择 Yale-NUS College Library，规划路线，但先不要开始导航。", report);
    assert.equal(routeCalls, 1);
    const map = await page.evaluate(async () => (await import(new URL("../assets/maps-page.js", location.href).href)).describeMap());
    assert.ok(map.navigation.route);
    assert.equal(map.navigation.active, false);
    assert.ok(Math.abs(map.destination[0] - 103.772556) < .001);
    report.checks.push("candidate_selection_retained_across_turns");
    await page.evaluate(() => { window.agentFixture.denyLocation = true; });
    await ask(page, "重新获取我的当前位置，导航到经度103.7735、纬度1.2991。", report);
    assert.equal(routeCalls, 1);
    assert.match(report.turns.at(-1).reply, /权限|拒绝|定位/);
    report.checks.push("location_denied_no_fake_origin_or_retry");
    await page.locator("#agent-input").fill("开始导航");
    await page.locator("#agent-send").click();
    await page.locator("#agent-cancel").click();
    await page.locator("#agent-panel[data-busy=false]").waitFor({timeout: 10000});
    assert.match(await page.locator(".agent-message.assistant").last().innerText(), /取消/);
    report.checks.push("cancel_inflight_turn");
    await page.goto(new URL("../live/", page.url()).href);
    await page.locator("#agent-panel[data-connected=true][data-busy=false]").waitFor();
    await page.locator("#agent-clear").click();
    await page.waitForFunction(() => document.querySelectorAll(".agent-message").length === 0);
    await page.locator("#vibration-enabled").check();
    await ask(page, "请短震一下", report);
    assert.deepEqual(await page.evaluate(() => window.agentOutputs.vibrations), [[200]]);
    report.checks.push("enabled_vibration_one_native_request");
    await ask(page, "停止震动", report);
    assert.equal(await page.evaluate(() => window.agentOutputs.vibrations.at(-1)), 0);
    report.checks.push("vibration_stop");
    await page.locator("#speech-enabled").check();
    await ask(page, "请用中文朗读：测试成功。", report);
    assert.ok((await page.evaluate(() => window.agentOutputs.spoken)).some(text => text.includes("测试成功")));
    report.checks.push("enabled_speech_engine_request");
    await ask(page, "停止朗读", report);
    assert.ok(await page.evaluate(() => window.agentOutputs.cancels > 0));
    report.checks.push("speech_stop");
    await page.locator("#speech-enabled").uncheck();
    await ask(page, "开启摄像头使用原样回传，然后描述当前场景。", report);
    const scene = await page.locator("#scene-result").innerText();
    assert.ok(scene.length > 0, "Actual configured VLM must produce a visible scene description");
    report.scene = scene;
    report.checks.push("real_vlm_scene_description_via_existing_ui");
    await ask(page, "关闭摄像头", report);
    assert.equal(await page.evaluate(async () => (await import(new URL("./assets/camera.js", location.href).href)).cameraApp.stream), null);
    await page.locator("#agent-panel").screenshot({path: output + ".png"});
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
await main();
