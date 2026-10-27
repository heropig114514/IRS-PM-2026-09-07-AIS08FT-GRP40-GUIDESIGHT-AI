/** Responsibilities: Verify speech test UI, lifecycle and real MCP HTTP/WS routing in Chrome.
 * Implementation: Native engine/utterance fixtures cover controlled events and errors; a separate
 * explicit local English/Chinese attempts use the real browser engine without replacing speech APIs.
 * Related Modules: speech-output.js, ai-control.js, i18n.js; SystemCode/clips/backend/tools/check_speech_mcp.py.
 * Declaration Index: installFixture; installFixture.Utterance; installFixture.Utterance.constructor;
 * installFixture.Engine; installFixture.Engine.constructor; installFixture.Engine.getVoices;
 * installFixture.Engine.speak; installFixture.Engine.cancel; installFixture.Engine.emit;
 * installFixture.denyCamera; runClient; runClient.completed; runClient.completed.exited;
 * noGesture; collectUntranslated; layout; nativeCheck; main.
 * Variable Index: None; PRS_PLAYWRIGHT_PATH/PYTHON are explicit development runtimes;
 * main/nativeCheck own disposable contexts and reports. Pre-interaction CDP uses userGesture=false.
 * Constraints: No database writes/GPU/GPS, TLS bypass, permission grant or retry. Native engine
 * start/end is not evidence of physical sound; audio_output_verified remains false.
 */
import assert from "node:assert/strict";
import {createRequire} from "node:module";
import {spawn} from "node:child_process";
import {mkdir, readFile, writeFile} from "node:fs/promises";
import {dirname} from "node:path";

/** Functionality: Install explicit native speech/media fixtures while preserving real activation.
 * Inputs: supported flag; browser Window/EventTarget. Outputs: Controlled engine and utterance.
 * Logic: Requests do not auto-complete; the driver emits events through the actual handlers.
 * Constraints: No physical synthesis, recording or camera permission; installed only in test contexts.
 */
function installFixture(supported) {
  /** Functionality: Hold text and actual event-target identity for a synthetic utterance.
   * Logic: EventTarget dispatches controlled engine callbacks. Constraints: No audio generation.
   */
  class Utterance extends EventTarget {
    /** Functionality: Store test text without rewriting it. Inputs: text. Outputs: Fixture object.
     * Logic: Plain in-memory property. Constraints: Not an OS utterance or actual sensor output.
     */
    constructor(text) { super(); this.text = text; }
  }
  /** Functionality: Represent a deterministic single-request native engine boundary.
   * Logic: Preserve request/options and expose explicit events/failures. Constraints: No sound.
   */
  class Engine extends EventTarget {
    /** Functionality: Initialize two voice identities, state and counters.
     * Inputs: None. Outputs: Engine fixture. Logic: One local Chinese and one remote English voice.
     * Constraints: localService is synthetic and does not verify real offline service behavior.
     */
    constructor() {
      super();
      this.voices = [
        {voiceURI: "fixture-zh", name: "Fixture Chinese", lang: "zh-CN", localService: true, default: true},
        {voiceURI: "fixture-en", name: "Fixture English", lang: "en-US", localService: false, default: false},
      ];
      Object.assign(this, {calls: [], cancels: 0, current: null, speaking: false,
        pending: false, paused: false, failSpeak: false});
    }
    /** Functionality: Read the current fixture inventory. Inputs: this.voices. Outputs: Voice list.
     * Logic: Return controlled objects. Constraints: No remote or operating-system request.
     */
    getVoices() { return this.voices; }
    /** Functionality: Capture one request and queue it without automatic events.
     * Inputs: utterance and failSpeak. Outputs: void or explicit exception, recorded metadata.
     * Logic: Record sample text only in this test fixture; product logs/status do not expose it.
     * Constraints: No actual audio or retry.
     */
    speak(utterance) {
      this.calls.push({text: utterance.text, language: utterance.lang, voice_id: utterance.voice?.voiceURI ?? null,
        rate: utterance.rate, volume: utterance.volume});
      if (this.failSpeak) throw new DOMException("Explicit fixture failure", "NotAllowedError");
      this.current = utterance;
      this.pending = true;
    }
    /** Functionality: Cancel the synthetic current request with a synchronous cancellation event.
     * Inputs: current. Outputs: Incremented count and cleared native flags.
     * Logic: Emit canceled only when a request exists. Constraints: No motor/speaker confirmation.
     */
    cancel() { this.cancels++; this.emit("error", "canceled"); }
    /** Functionality: Deliver an explicit engine lifecycle event with current object identity.
     * Inputs: type and optional error. Outputs: State changes and actual EventTarget dispatch.
     * Logic: start sets speaking; end/error clear flags and the fixture current request.
     * Constraints: The event is synthetic; not proof of actual TTS synthesis.
     */
    emit(type, error) {
      if (!this.current) return;
      if (type === "start") { this.pending = false; this.speaking = true; }
      if (type === "end" || type === "error") { this.pending = this.speaking = false; }
      const utterance = this.current;
      if (type !== "start") this.current = null;
      utterance.dispatchEvent(Object.assign(new Event(type), {error}));
    }
  }
  const engine = new Engine();
  window.__speechFixture = {engine, cameraRequests: 0};
  Object.defineProperty(window, "speechSynthesis", {configurable: true, value: supported ? engine : undefined});
  Object.defineProperty(window, "SpeechSynthesisUtterance", {configurable: true, value: supported ? Utterance : undefined});
  /** Functionality: Detect accidental sensor use without authorizing it.
   * Inputs: Implicit getUserMedia. Outputs: Counter and explicit exception.
   * Logic: Reject once. Constraints: No microphone/camera recording or permission validation.
   */
  function denyCamera() {
    window.__speechFixture.cameraRequests++;
    throw new Error("Speech test unexpectedly requested media");
  }
  if (navigator.mediaDevices) Object.defineProperty(navigator.mediaDevices, "getUserMedia", {
    configurable: true, value: denyCamera,
  });
}

/** Functionality: Run one official MCP client scenario against an explicit browser session.
 * Inputs: camera URL, report prefix, session/scenario, optional exact native voice/language.
 * Outputs: Parsed report or explicit process failure. Logic: Fixed argument array, no shell.
 * Constraints: No implicit mutation replay or browser selection; child windows remain hidden.
 */
async function runClient(url, output, session, scenario, voice = null, language = "en-US") {
  const path = output + "." + scenario + ".json";
  const args = ["-m", "tools.check_speech_mcp", "--url", new URL("../mcp", url).href,
    "--session", session, "--scenario", scenario, "--output", path];
  if (voice) args.push("--voice", voice);
  if (scenario === "native") args.push("--language", language);
  const child = spawn(process.env.PYTHON, args,
    {cwd: new URL("../../backend/", import.meta.url), stdio: "inherit", windowsHide: true});
  /** Functionality: Observe one child completion. Inputs: resolve/reject. Outputs: Promise settlement.
   * Logic: Register launch error/exit once. Constraints: No relaunch on failure.
   */
  function completed(resolve, reject) {
    child.once("error", reject);
    /** Functionality: Check checker exit code. Inputs: code. Outputs: resolve/error.
     * Logic: Only zero passes. Constraints: Never interprets a passed fixture as actual sound.
     */
    function exited(code) { if (code === 0) resolve(); else reject(new Error("Speech MCP checker failed: " + code)); }
    child.once("exit", exited);
  }
  await new Promise(completed);
  const report = JSON.parse(await readFile(path, "utf8"));
  assert.equal(report.complete, true);
  return report;
}

/** Functionality: Evaluate test observation/setup without granting browser user activation.
 * Inputs: CDP session and explicit expression. Outputs: Serialized return value or failure.
 * Logic: userGesture=false preserves the activation prerequisite; check CDP exceptions.
 * Constraints: No page evaluation before trusted-click scenarios, permission bypass or retry.
 */
async function noGesture(cdp, expression) {
  const result = await cdp.send("Runtime.evaluate", {expression, returnByValue: true, userGesture: false});
  assert.equal(result.exceptionDetails, undefined);
  return result.result.value;
}

/** Functionality: Inspect authored English UI while protecting input/native voice identities.
 * Inputs: Actual speech-test DOM. Outputs: Untranslated authored strings.
 * Logic: Exclude textarea contents and marked native option names; check aria-labels.
 * Constraints: Does not translate or inspect audio data, entered text or native provider identity.
 */
function collectUntranslated() {
  const root = document.getElementById("speech-test"), missing = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) if (!walker.currentNode.parentElement.closest("textarea,[data-language-original]") &&
      /\p{Script=Han}/u.test(walker.currentNode.nodeValue)) missing.push(walker.currentNode.nodeValue.trim());
  for (const node of root.querySelectorAll("[aria-label]"))
    if (/\p{Script=Han}/u.test(node.getAttribute("aria-label"))) missing.push(node.getAttribute("aria-label"));
  return missing;
}

/** Functionality: Check responsive page/card controls using actual layout.
 * Inputs: page/current viewport. Outputs: Width and touch-target assertions.
 * Logic: Require no horizontal overflow and at least44px action buttons.
 * Constraints: Browser pixels are not physical phone touch verification.
 */
async function layout(page) {
  const value = await page.evaluate(() => ({width: innerWidth, content: document.documentElement.scrollWidth,
    heights: [...document.querySelectorAll(".speech-actions button")].map(node => node.getBoundingClientRect().height)}));
  assert.ok(value.content <= value.width, JSON.stringify(value));
  assert.ok(value.heights.every(height => height >= 44));
}

/** Functionality: Attempt one real local voice in the explicit language, separate from fixtures.
 * Inputs: browser/url/output and language(default en-US).
 * Outputs: Actual engine state or explicit unavailable/timeout evidence.
 * Logic: Observe the target local-language voice for up to5s as inventory loads asynchronously;
 * explicitly choose that identity, submit once
 * after trusted enable; observe engine completion/error and explicitly clean up the test request.
 * Constraints: No default/remote substitution, repeated speech or physical sound verification.
 * Headless/native service limitations are reported, not converted to successful fixture evidence.
 */
async function nativeCheck(browser, url, output, language = "en-US") {
  const context = await browser.newContext({locale: "en-US"});
  const evidence = {attempted: false, language, audio_output_verified: false, fixture: false};
  try {
    const page = await context.newPage();
    assert.equal((await page.goto(url, {waitUntil: "networkidle"})).status(), 200);
    try {
      await page.waitForFunction(language => window.speechSynthesis?.getVoices()
        .some(voice => voice.localService && voice.lang.split("-")[0].toLowerCase() === language.split("-")[0].toLowerCase()),
      language, {timeout: 5000});
    } catch (error) {
      if (error.name !== "TimeoutError") throw error;
      const inventory = await page.evaluate(() => speechSynthesis?.getVoices().map(voice => ({
        language: voice.lang, local_service: voice.localService,
      })) ?? []);
      return {...evidence, reason: "native_local_voice_unavailable_within_5s", inventory};
    }
    const identity = await page.evaluate(language => ({session: sessionStorage.getItem("prs.ai.session"),
      voice: speechSynthesis.getVoices().filter(voice => voice.localService &&
        voice.lang.split("-")[0].toLowerCase() === language.split("-")[0].toLowerCase())
        .map(voice => ({id: voice.voiceURI, language: voice.lang}))[0] ?? null}), language);
    if (!identity.voice) return {...evidence, reason: "native_local_voice_unavailable"};
    await page.locator("#speech-enabled").check();
    evidence.attempted = true;
    evidence.voice = identity.voice;
    evidence.submission = await runClient(url, output, identity.session, "native", identity.voice.id, language);
    try {
      await page.waitForFunction(() => ["Engine finished", "Speech failed"]
        .includes(document.getElementById("speech-status").textContent.trim()), null, {timeout: 15000});
      evidence.final_state = await page.locator("#speech-status").textContent();
      evidence.engine_completed = evidence.final_state === "Engine finished";
    } catch (error) {
      if (error.name !== "TimeoutError") throw error;
      evidence.reason = "native_engine_terminal_event_timeout";
      evidence.final_state = await page.locator("#speech-status").textContent();
    }
    await page.locator("#speech-stop").click();
    return evidence;
  } finally { await context.close(); }
}

/** Functionality: Verify UI/MCP fixture scenarios and optionally observe the real native engine.
 * Inputs: CLI CAMERA_URL/OUTPUT, optional --native-chinese-only, development runtimes and ASGI.
 * Outputs: Evidence JSON, screenshots and exact submission/cancellation/media counters.
 * Logic: Preserve real activation, drive synthetic start/end/error through native event handlers,
 * compare UI and MCP parameters, language/layout, explicit failures and unsupported context.
 * Constraints: No database/GPU/GPS/sensor calls, native retries or deployment; test text is public
 * sample text. Always close this browser; retain report on failure and never assert audible output.
 */
async function main() {
  const [url, output, mode] = process.argv.slice(2);
  if (mode && mode !== "--native-chinese-only") throw new Error("Unknown check mode");
  if (!url || !output || !process.env.PRS_PLAYWRIGHT_PATH || !process.env.PYTHON)
    throw new Error("Use CAMERA_URL OUTPUT and set PRS_PLAYWRIGHT_PATH/PYTHON");
  const parsed = new URL(url);
  if (parsed.username || parsed.password || parsed.search || parsed.hash ||
      !(parsed.protocol === "https:" || parsed.protocol === "http:" && ["localhost", "127.0.0.1"].includes(parsed.hostname)))
    throw new Error("Use a credential-free HTTPS or explicit loopback URL");
  await mkdir(dirname(output), {recursive: true});
  const report = {complete: false, pageErrors: [], scenarios: {}, audio_output_verified: false};
  const {chromium} = createRequire(import.meta.url)(process.env.PRS_PLAYWRIGHT_PATH);
  const browser = await chromium.launch({channel: "chrome", headless: true});
  if (mode === "--native-chinese-only") {
    try {
      report.native_chinese = await nativeCheck(browser, url, output, "zh-CN");
      report.complete = true;
    } catch (error) {
      report.error = {name: error.name, message: error.message};
      throw error;
    } finally {
      await writeFile(output, JSON.stringify(report, null, 2) + "\n");
      await browser.close();
    }
    console.info(JSON.stringify(report));
    return;
  }
  try {
    const context = await browser.newContext({locale: "en-US", viewport: {width: 390, height: 844}});
    await context.addInitScript(installFixture, true);
    const page = await context.newPage();
    page.on("pageerror", error => report.pageErrors.push(error.message));
    assert.equal((await page.goto(url, {waitUntil: "networkidle"})).status(), 200);
    const cdp = await context.newCDPSession(page);
    const identity = JSON.parse(await noGesture(cdp,
      'JSON.stringify({session: sessionStorage.getItem("prs.ai.session"), activated: navigator.userActivation.hasBeenActive})'));
    assert.equal(identity.activated, false);
    report.scenarios.disabled = await runClient(url, output, identity.session, "disabled");
    await noGesture(cdp, 'document.getElementById("speech-enabled").checked=true; document.getElementById("speech-enabled").dispatchEvent(new Event("change",{bubbles:true}))');
    report.scenarios.activation = await runClient(url, output, identity.session, "activation");
    assert.equal(await noGesture(cdp, "window.__speechFixture.engine.calls.length"), 0);
    await page.locator("#speech-enabled").uncheck();
    await page.locator("#speech-enabled").check();
    report.scenarios.submit = await runClient(url, output, identity.session, "submit");
    await noGesture(cdp, 'window.__speechFixture.engine.emit("start")');
    report.scenarios.speaking = await runClient(url, output, identity.session, "speaking");
    await noGesture(cdp, 'window.__speechFixture.engine.emit("end")');
    report.scenarios.completed = await runClient(url, output, identity.session, "completed");
    await page.waitForFunction(() => document.getElementById("speech-status").textContent === "Engine finished");
    assert.deepEqual(await page.evaluate(collectUntranslated), []);
    await page.locator('[data-speech-sample="zh-CN"]').click();
    await page.locator("#speech-voice").selectOption("fixture-zh");
    await page.locator("#speech-rate").fill("1.6");
    await page.locator("#speech-volume").fill("0.7");
    await page.locator("#speech-play").click();
    report.scenarios.ui_queued = await runClient(url, output + ".ui", identity.session, "queued");
    const calls = JSON.parse(await noGesture(cdp, "JSON.stringify(window.__speechFixture.engine.calls)"));
    assert.deepEqual(calls[0], {text: "This is a PRS speech output test.", language: "en-US",
      voice_id: "fixture-en", rate: 1.4, volume: 0.6});
    assert.deepEqual(calls[1], {text: "这是 PRS 语音输出测试。", language: "zh-CN",
      voice_id: "fixture-zh", rate: 1.6, volume: 0.7});
    report.scenarios.stop = await runClient(url, output, identity.session, "stop");
    report.scenarios.voice_missing = await runClient(url, output, identity.session, "voice_missing");
    await noGesture(cdp, "window.__speechFixture.engine.failSpeak=true");
    report.scenarios.native_error = await runClient(url, output, identity.session, "native_error");
    await noGesture(cdp, "window.__speechFixture.engine.failSpeak=false");
    report.scenarios.second_submit = await runClient(url, output + ".second", identity.session, "submit");
    await noGesture(cdp, 'window.__speechFixture.engine.emit("error","not-allowed")');
    report.scenarios.engine_error = await runClient(url, output, identity.session, "engine_error");
    await page.waitForFunction(() => document.getElementById("speech-message").textContent.includes("not-allowed"));
    assert.deepEqual(await page.evaluate(collectUntranslated), []);
    await layout(page);
    await page.locator("#speech-test").screenshot({path: output + ".mobile-en.png"});
    await page.locator("#language").selectOption("zh-CN");
    await page.waitForFunction(() => document.getElementById("speech-title").textContent === "语音输出测试");
    assert.equal(await page.locator("#speech-text").inputValue(), "这是 PRS 语音输出测试。");
    await page.setViewportSize({width: 320, height: 740});
    await layout(page);
    await page.locator("#speech-test").screenshot({path: output + ".mobile-zh.png"});
    await page.setViewportSize({width: 1280, height: 900});
    await layout(page);
    const state = JSON.parse(await noGesture(cdp,
      "JSON.stringify({calls:window.__speechFixture.engine.calls,cancels:window.__speechFixture.engine.cancels,media:window.__speechFixture.cameraRequests})"));
    assert.equal(state.calls.length, 4);
    assert.equal(state.media, 0);
    report.fixture = {submissions: state.calls.length, cancellations: state.cancels,
      media_requests: 0, layouts: [390, 320, 1280], languages: ["en", "zh-CN"]};
    await context.close();
    const unavailable = await browser.newContext({locale: "en-US"});
    await unavailable.addInitScript(installFixture, false);
    const unsupported = await unavailable.newPage();
    unsupported.on("pageerror", error => report.pageErrors.push(error.message));
    assert.equal((await unsupported.goto(url, {waitUntil: "networkidle"})).status(), 200);
    const otherSession = await unsupported.evaluate(() => sessionStorage.getItem("prs.ai.session"));
    report.scenarios.unsupported = await runClient(url, output, otherSession, "unsupported");
    assert.equal(await unsupported.locator("#speech-enabled").isDisabled(), true);
    assert.equal(await unsupported.locator("#speech-play").isDisabled(), true);
    assert.equal(await unsupported.locator("#speech-stop").isDisabled(), true);
    await unavailable.close();
    assert.deepEqual(report.pageErrors, []);
    report.native = await nativeCheck(browser, url, output);
    report.complete = true;
  } catch (error) {
    report.error = {name: error.name, message: error.message};
    throw error;
  } finally {
    await writeFile(output, JSON.stringify(report, null, 2) + "\n");
    await browser.close();
  }
  console.info(JSON.stringify({complete: report.complete, fixture: report.fixture,
    native: report.native, audio_output_verified: false}));
}

await main();
