/* Responsibilities: Verify original maps navigation, MCP control and integrated native Chinese speech.
 * Implementation: Real Chrome/Leaflet/UI/HTTP/WebSocket/TTS; explicitly synthetic ORS/config/GPS
 * boundaries, source-bound native utterance checks and per-run ownership cleanup.
 * Related Modules: maps.html, maps-page.js, navigation-ui.js, ai-control.js, speech-output.js;
 * integration/maps-navigation-speech.js/maps-navigation-bridge.js/navigation-speech.js;
 * check_local_navigation_browser.mjs exports the shared verifySpeech assertion.
 * Declaration Index: installGPS; installGPS.fixture.fix; installGPS.fixture.describe;
 * installGPS.geo.watchPosition; installGPS.geo.clearWatch; installGPS.geo.getCurrentPosition;
 * observe; observe.recordNavigation; observe.recordSpeech; observe.recordDriver;
 * capture; resetProbe; runTool; enableSpeech; planRoute; deliverFix; completeFix;
 * assertStopped; fullSession; setVisibility; stoppedSession; checkLayouts;
 * run; run.requested; run.responded; run.consoleFailed; run.fixtureRoute;
 * requireNew; main.
 * Variable Index: MCP_SCRIPT is fixed official-client source; ROUTE is the explicit synthetic
 * route; mapsSpeechProbe stores original synthetic navigation and body-free output events;
 * mapsSpeechControllers references original canonical singletons; report records dependency
 * requests, explicit fixture scope, complete observations and native output ownership.
 * Constraints: No real GPS, ORS authentication/planning, metric accuracy, physical audibility,
 * video inference or deployment validation. Leaflet CDN and OSM remain real dependencies;
 * missing native Chinese speech or CDN resources fail without substitution or retries.
 */
import assert from "node:assert/strict";
import {createRequire} from "node:module";
import {execFile} from "node:child_process";
import {promisify} from "node:util";
import {access, mkdir, writeFile} from "node:fs/promises";
import {resolve, dirname} from "node:path";
import {pathToFileURL} from "node:url";
import {verifySpeech, waitForGate} from "./check_local_navigation_browser.mjs";

const MCP_SCRIPT = `
import asyncio,json,sys
from mcp.client import Client
async def run():
    # Functionality: Use the official MCP HTTP client for one explicit navigation command.
    # Inputs: endpoint/session/tool argv. Outputs: JSON result. Logic: discover then call once.
    # Constraints: No database/output mutations, browser replacement, retries or service start.
    async with Client(sys.argv[1]) as client:
        names={tool.name for tool in (await client.list_tools()).tools}
        assert {"navigation_start","navigation_stop","navigation_status"} <= names
        result=await client.call_tool(sys.argv[3], {"session_id":sys.argv[2]})
        assert not result.is_error, result.content
        print(json.dumps(result.structured_content))
asyncio.run(run())
`;
const ROUTE = Object.freeze({provider: "openrouteservice", profile: "foot-walking",
  attribution: "Synthetic route fixture: original maps UI and navigation speech test",
  geometry: {type: "LineString", coordinates: [[103.7766, 1.2971], [103.7776, 1.2971],
    [103.7776, 1.2981], [103.7786, 1.2981]]},
  summary: {distance_m: 333, duration_s: 270}, steps: [
    {type: 11, instruction: "Head east (synthetic)", distance_m: 111, duration_s: 90, way_points: [0, 1]},
    {type: 0, instruction: "Turn left (synthetic)", distance_m: 111, duration_s: 90, way_points: [1, 2]},
    {type: 1, instruction: "Turn right (synthetic)", distance_m: 111, duration_s: 90, way_points: [2, 3]},
    {type: 10, instruction: "Arrive (synthetic)", distance_m: 0, duration_s: 0, way_points: [3, 3]},
  ]});

/* Functionality: Replace only the browser sensor boundary before original modules initialize.
 * Inputs: Browser window implicit. Outputs: Explicit GPS fixture with watch/clear/fix evidence.
 * Logic: Count active watches, retain old callbacks for deliberate late-delivery checks and use
 * actual Date.now timestamps. Constraints: No real permission/GPS, fake clock or generated core state. */
function installGPS() {
  const watches = [], cleared = [], fixes = [];
  const fixture = {watches, cleared, fixes, max_active: 0, one_shot_requests: 0,
    /* Functionality: Deliver one explicitly requested synthetic sensor observation.
     * Inputs: Longitude/latitude/accuracy and optional retained watch ID. Outputs: Native-style callback.
     * Logic: Use the real browser clock and original registered callback, including cleared handles.
     * Constraints: No core method call or timestamp adjustment; callers must separate consecutive fixes. */
    fix(longitude, latitude, accuracy = 5, id = watches.length - 1) {
      const watch = watches[id];
      if (!watch) throw new Error("No registered synthetic watch");
      const timestamp = Date.now();
      fixes.push({watch_id: id, active_at_delivery: watch.active, longitude, latitude, accuracy, timestamp});
      watch.success({coords: {longitude, latitude, accuracy}, timestamp});
      return timestamp;
    },
    /* Functionality: Return detached sensor registration evidence.
     * Inputs: Fixture state. Outputs: Metadata without callbacks.
     * Logic: Count original active handles and preserve all cleanup/source observations.
     * Constraints: Evidence describes synthetic registration, not physical sensor resources. */
    describe() {
      return {watch_count: watches.length, active_watch_count: watches.filter(watch => watch.active).length,
        max_active_watch_count: fixture.max_active, cleared: [...cleared], one_shot_requests: fixture.one_shot_requests,
        watches: watches.map(({id, active, options}) => ({id, active, options})), fixes: structuredClone(fixes)};
    }};
  const geo = {
    /* Functionality: Retain the original controller's single watch registration.
     * Inputs: Success/error callbacks and options. Outputs: Unique handle.
     * Logic: Count active ownership; a duplicate active watch is a hard test failure.
     * Constraints: Never delivers an implicit fix or grants browser permissions. */
    watchPosition(success, error, options) {
      const id = watches.length;
      watches.push({id, active: true, success, error, options: structuredClone(options)});
      fixture.max_active = Math.max(fixture.max_active, watches.filter(watch => watch.active).length);
      if (fixture.max_active > 1) throw new Error("Multiple GPS owners registered active watches");
      return id;
    },
    /* Functionality: Observe original cleanup without deleting retained callback evidence.
     * Inputs: Watch handle. Outputs: Inactive fixture handle and cleanup record.
     * Logic: Reject unknown/duplicate cleanup so an incorrect release cannot look successful.
     * Constraints: No actual native GPS watch exists. */
    clearWatch(id) {
      if (!watches[id]?.active) throw new Error("Unknown or already cleared synthetic GPS watch");
      watches[id].active = false; cleared.push(id);
    },
    /* Functionality: Fail unexpected one-shot location acquisition.
     * Inputs: None used. Outputs: Explicit failure and invocation count.
     * Logic: The test chooses coordinates by original example/form controls only.
     * Constraints: No hidden sensor fallback, permission request or fixture-derived start location. */
    getCurrentPosition() { fixture.one_shot_requests++; throw new Error("Unexpected one-shot GPS acquisition"); },
  };
  window.__mapsSpeechGPS = fixture;
  Object.defineProperty(navigator, "geolocation", {configurable: true, value: geo});
}

/* Functionality: Subscribe to canonical production navigation and native speech after page initialization.
 * Inputs: Browser document implicit. Outputs: Detached evidence stream and controller references.
 * Logic: Assert shared driver/adapter identity and inspect actual native-start text in memory.
 * Constraints: No controller replacement, output activation, synthetic speech events or retained utterance bodies. */
async function observe() {
  const glue = await import("/assets/integration/maps-navigation-speech.js");
  const {navigation} = await import("/assets/navigation-ui.js");
  const {speechOutput: driver} = await import("/assets/speech-output.js");
  const {navigationSpeechText} = await import("/assets/integration/navigation-speech.js");
  await glue.mapsNavigationSpeechReady;
  const adapter = glue.navigationSpeech, bridge = glue.navigationBridge;
  if (!navigation || !driver || !adapter || !bridge || adapter.speech !== driver || bridge.adapter !== adapter ||
      glue.navigation !== navigation)
    throw new Error("Maps speech does not share canonical navigation/driver/adapter instances");
  window.mapsSpeechControllers = {navigation, adapter, bridge, driver};
  window.mapsSpeechProbe = {navigation: [], speech: [], driver: [], utterances: []};
  /* Functionality: Preserve each original navigation snapshot.
   * Inputs: Original DOM event. Outputs: Detached source snapshot.
   * Logic: Clone original detail. Constraints: Synthetic source coordinates are labeled in the outer report. */
  function recordNavigation(event) { window.mapsSpeechProbe.navigation.push(structuredClone(event.detail)); }
  /* Functionality: Bind actual native output starts to the adapter's frozen source and selected voice/rate.
   * Inputs: Original adapter lifecycle event. Outputs: Body-free event and truth-valued binding evidence.
   * Logic: Compare actual current utterance with the existing formatter only while memory is live.
   * Constraints: No utterance body serialization, fake native lifecycle or physical-audibility assertion. */
  function recordSpeech(event) {
    window.mapsSpeechProbe.speech.push(structuredClone(event));
    if (event.phase !== "output_start") return;
    const active = adapter.active, source = active?.snapshot, last = driver.lastRequest, utterance = driver.current;
    const owned = active?.requestId === event.request_id && last?.id === event.request_id;
    const userRate = Number(document.getElementById("speech-rate").value);
    window.mapsSpeechProbe.utterances.push({...event, source_state: source?.state ?? null,
      source_reason: source?.reason ?? null, source_step_index: source?.progress?.step_index ?? null,
      source_timestamp: source?.position?.timestamp ?? null,
      source_next_is_endpoint: source?.progress?.next_step?.type === 10,
      claims_arrival: typeof utterance?.text === "string" && /已到达|have reached/i.test(utterance.text),
      request_owned: owned,
      text_matches_policy: Boolean(owned && source && typeof utterance?.text === "string" &&
        utterance.text.length > 0 && utterance.text === navigationSpeechText(source, last.language)),
      local_chinese_voice: Boolean(owned && last.language === "zh-CN" && last.local_service === true &&
        utterance?.voice?.localService === true && utterance.voice.lang.split("-")[0].toLowerCase() === "zh"),
      configured_rate: last?.rate ?? null, engine_rate: utterance?.rate ?? null, user_rate: userRate,
      rate_matches_user_setting: Boolean(owned && Number.isFinite(utterance?.rate) &&
        Math.abs(utterance.rate - last.rate) <= 1e-6 && last.rate === userRate)});
  }
  /* Functionality: Preserve the existing driver's native lifecycle independently of adapter claims.
   * Inputs: Original body-free event. Outputs: Detached event.
   * Logic: Preserve every phase and request ID. Constraints: Never manufactures start or completion. */
  function recordDriver(event) { window.mapsSpeechProbe.driver.push(structuredClone(event)); }
  document.addEventListener("prs-navigation-change", recordNavigation);
  adapter.subscribe(recordSpeech); driver.subscribe(recordDriver);
  return {shared_driver: adapter.speech === driver, shared_bridge_adapter: bridge.adapter === adapter,
    shared_navigation_export: glue.navigation === navigation, navigation_export_available: glue.navigation !== undefined};
}

/* Functionality: Capture source, output and explicit ownership state without native objects.
 * Inputs: Browser page. Outputs: JSON-compatible complete observations and cleanup metadata.
 * Logic: Preserve all probe events and finite controller/fixture state.
 * Constraints: No callbacks, handles, utterance text, actual location or hardware-success claim. */
async function capture(page) {
  return page.evaluate(() => {
    const {navigation, adapter, bridge, driver} = window.mapsSpeechControllers;
    return {...structuredClone(window.mapsSpeechProbe), cleanup: {navigation: navigation.describe(),
      speech: adapter.describe(), bridge: bridge.describe(), gps: __mapsSpeechGPS.describe(),
      gps_watch_cleared: navigation.watchId === null, gps_deadline_cleared: navigation.timer === null,
      speech_deadline_cleared: adapter.sourceTimer === null, owned_speech_cleared: adapter.active === null,
      native_utterance_cleared: driver.current === null}};
  });
}

/* Functionality: Partition evidence before a new explicit navigation run.
 * Inputs: Page. Outputs: Empty observation arrays only.
 * Logic: Preserve all production state and cumulative GPS registration evidence.
 * Constraints: Never resets a source clock, route, controller, native request or user setting. */
async function resetProbe(page) {
  await page.evaluate(() => { window.mapsSpeechProbe = {navigation: [], speech: [], driver: [], utterances: []}; });
}

/* Functionality: Call one original navigation tool through real MCP HTTP and the page's real control socket.
 * Inputs: Maps URL, session ID and fixed navigation tool name. Outputs: Structured MCP result.
 * Logic: Run fixed official-client source with argument array and hidden Windows subprocess.
 * Constraints: No shell interpolation, retry, service start, database call or native output mutation. */
async function runTool(url, sessionId, name) {
  assert.ok(["navigation_start", "navigation_stop", "navigation_status"].includes(name));
  const {stdout} = await promisify(execFile)(process.env.PYTHON,
    ["-c", MCP_SCRIPT, new URL("../mcp", url).href, sessionId, name], {windowsHide: true, timeout: 20000});
  return JSON.parse(stdout.trim());
}

/* Functionality: Select installed local Chinese speech using original controls and two explicit switches.
 * Inputs: Browser page. Outputs: Voice/rate configuration evidence.
 * Logic: Read native inventory once and preserve the page's default rate.
 * Constraints: No substituted language/voice, automatic enablement, retry or test-written speech rate. */
async function enableSpeech(page) {
  await page.waitForFunction(() => speechSynthesis.getVoices().length > 0, null, {timeout: 5000});
  assert.equal(await page.inputValue("#navigation-speech-language"), "zh-CN");
  await page.click("#navigation-voices-refresh");
  const voices = await page.locator("#navigation-speech-voice option").evaluateAll(options =>
    options.filter(option => option.value && !option.disabled).map(option => option.value));
  assert.ok(voices.length, "No installed matching local Chinese voice; no substitution permitted");
  await page.selectOption("#navigation-speech-voice", voices[0]);
  await page.check("#speech-enabled"); await page.check("#navigation-speech-enabled");
  const rate = Number(await page.inputValue("#speech-rate"));
  assert.equal(rate, 2, "Page must supply the 2x default without checker adjustment");
  const state = await capture(page);
  assert.equal(state.cleanup.gps.watch_count, 0, "Output configuration must not acquire GPS");
  assert.equal(state.speech.some(event => event.phase === "output_submitted"), false);
  return {language: "zh-CN", voice_id: voices[0], rate, hardware_verified: false};
}

/* Functionality: Plan the explicit synthetic route through unchanged original map example/form controls.
 * Inputs: Page. Outputs: Ready original controller route.
 * Logic: Choose the example, enter the fixture endpoint, submit and await the original start button.
 * Constraints: Only the HTTP response boundary is replaced; no direct route installation or automatic navigation. */
async function planRoute(page) {
  await page.click("#example");
  await page.locator("#end-longitude").fill("103.7786");
  await page.locator("#end-latitude").fill("1.2981");
  await page.click("#plan");
  await page.waitForFunction(() => !document.getElementById("navigation-start").disabled &&
    window.mapsSpeechControllers.navigation.describe().state === "ready");
  const state = await capture(page);
  assert.equal(state.cleanup.navigation.active, false);
  assert.equal(state.cleanup.gps.active_watch_count, 0);
  return state.cleanup.navigation.route;
}

/* Functionality: Deliver one explicit sensor fixture through the original registered native callback.
 * Inputs: Page, coordinates, accuracy and optional retained watch ID. Outputs: Original core snapshot.
 * Logic: Separate consecutive observations using actual wall time before reading Date.now in the browser.
 * Constraints: No timestamp fabrication, test clock, controller method replacement or hidden continuous replay. */
async function deliverFix(page, longitude, latitude, accuracy = 5, id = null) {
  await page.waitForTimeout(10);
  return page.evaluate(({longitude, latitude, accuracy, id}) => {
    __mapsSpeechGPS.fix(longitude, latitude, accuracy, id ?? undefined);
    return window.mapsSpeechControllers.navigation.describe();
  }, {longitude, latitude, accuracy, id});
}

/* Functionality: Observe one semantic state through real native Chinese speech completion.
 * Inputs: Page, explicit synthetic coordinates, expected core state, output kind and optional step.
 * Outputs: Source snapshot and its completed native request metadata.
 * Logic: Bound one native completion by 15 seconds, retaining every event; errors fail immediately.
 * Constraints: No fabricated completion, speech retry, clock mutation or changing core freshness policy. */
async function completeFix(page, {longitude, latitude, accuracy = 5, state, kind, step = null, reason = null}) {
  const offset = await page.evaluate(() => window.mapsSpeechProbe.speech.length);
  const snapshot = await deliverFix(page, longitude, latitude, accuracy);
  assert.equal(snapshot.state, state);
  if (reason !== null) assert.equal(snapshot.reason, reason);
  if (step !== null) assert.equal(snapshot.progress?.step_index, step);
  await page.waitForFunction(({offset, kind, step}) => window.mapsSpeechProbe.speech.slice(offset).some(event =>
    event.phase === "paused" || event.phase === "output_error" ||
    event.phase === "output_end" && event.kind === kind && (kind !== "turn" || step === null || event.step_index === step)),
  {offset, kind, step}, {timeout: 15000});
  const result = await capture(page), events = result.speech.slice(offset);
  assert.equal(events.some(event => ["paused", "output_error"].includes(event.phase)), false,
    "Native speech failed during an explicit semantic transition");
  const end = events.find(event => event.phase === "output_end" && event.kind === kind &&
    (kind !== "turn" || step === null || event.step_index === step));
  assert.ok(end, "Expected native utterance did not complete");
  assert.ok(result.utterances.some(event => event.request_id === end.request_id && event.text_matches_policy));
  return {source: snapshot, output: end};
}

/* Functionality: Assert original GPS and adapter-owned work are fully closed after an intentional stop.
 * Inputs: Complete capture. Outputs: Assertions.
 * Logic: Require no active watch/deadlines/run/pending/native ownership and no automatic failure latch.
 * Constraints: Does not require deleting a retained planned route or infer audible silence. */
function assertStopped(result) {
  assert.equal(result.cleanup.navigation.active, false);
  assert.equal(result.cleanup.gps.active_watch_count, 0);
  assert.equal(result.cleanup.gps.max_active_watch_count, 1);
  assert.equal(result.cleanup.gps.one_shot_requests, 0);
  for (const field of ["gps_watch_cleared", "gps_deadline_cleared", "speech_deadline_cleared",
    "owned_speech_cleared", "native_utterance_cleared"]) assert.equal(result.cleanup[field], true, field);
  assert.equal(result.cleanup.speech.run_id, null);
  assert.equal(result.cleanup.speech.pending, false);
  assert.equal(result.cleanup.speech.paused, false);
  assert.equal(result.cleanup.bridge.run_id, null);
}

/* Functionality: Exercise original route progress, quality notices, recovery and arrival with actual speech.
 * Inputs: Page, original maps URL and registered MCP session. Outputs: Full closed-run evidence.
 * Logic: UI start and MCP status/duplicate-start share one watch; each semantic transition must
 * complete through the native engine before the next fixture fix; repeat GPS cannot repeat speech.
 * Constraints: No true physical motion, real GPS, new distance trigger, speech event mock or route fallback. */
async function fullSession(page, url, sessionId) {
  await resetProbe(page);
  await page.click("#navigation-start");
  const started = await runTool(url, sessionId, "navigation_status");
  assert.equal(started.state, "locating"); assert.equal(started.active, true);
  const duplicate = await runTool(url, sessionId, "navigation_start");
  assert.equal(duplicate.accepted, false); assert.equal(duplicate.reason, "already_active");
  assert.equal((await capture(page)).cleanup.gps.active_watch_count, 1);
  const transitions = [];
  transitions.push(await completeFix(page, {longitude: 103.7771, latitude: 1.2971,
    state: "navigating", kind: "turn", step: 0}));
  const beforeDuplicate = (await capture(page)).speech.filter(event => event.phase === "output_submitted").length;
  await deliverFix(page, 103.77715, 1.2971); await deliverFix(page, 103.7772, 1.2971);
  await page.waitForTimeout(350);
  assert.equal((await capture(page)).speech.filter(event => event.phase === "output_submitted").length, beforeDuplicate,
    "Repeated original GPS updates within one step must not repeat speech");
  transitions.push(await completeFix(page, {longitude: 103.7776, latitude: 1.2976,
    state: "navigating", kind: "turn", step: 1}));
  transitions.push(await completeFix(page, {longitude: 103.7776, latitude: 1.2976, accuracy: 80,
    state: "locating", reason: "accuracy_insufficient", kind: "accuracy_insufficient"}));
  transitions.push(await completeFix(page, {longitude: 103.7776, latitude: 1.29765,
    state: "navigating", kind: "turn", step: 1}));
  transitions.push(await completeFix(page, {longitude: 103.7800, latitude: 1.2981,
    state: "off_route", kind: "off_route"}));
  transitions.push(await completeFix(page, {longitude: 103.7776, latitude: 1.2978,
    state: "navigating", kind: "turn", step: 1}));
  transitions.push(await completeFix(page, {longitude: 103.7781, latitude: 1.2981,
    state: "navigating", kind: "turn", step: 2}));
  const beforeArrival = await capture(page);
  assert.ok(beforeArrival.utterances.some(event => event.source_next_is_endpoint && !event.claims_arrival));
  assert.equal(beforeArrival.utterances.some(event => event.claims_arrival || event.kind === "arrived"), false,
    "A terminal next instruction must not announce actual arrival");
  transitions.push(await completeFix(page, {longitude: 103.7786, latitude: 1.2981,
    state: "arrived", kind: "arrived", step: 3}));
  const arrival = await capture(page);
  assert.equal(arrival.cleanup.navigation.active, false);
  assert.equal(arrival.cleanup.gps.active_watch_count, 0);
  const status = await runTool(url, sessionId, "navigation_status");
  assert.equal(status.state, "arrived"); assert.equal(status.progress.step_index, 3);
  assert.ok(arrival.utterances.some(event => event.kind === "arrived" && event.claims_arrival));
  assert.equal((await runTool(url, sessionId, "navigation_stop")).accepted, true);
  const result = await capture(page);
  result.end_mode = "arrival_then_explicit_mcp_stop";
  result.transitions = transitions; result.arrival_before_explicit_stop = arrival.cleanup;
  result.mcp = {started, duplicate, arrived_status: status};
  result.speech_summary = verifySpeech(result); assertStopped(result);
  assert.ok(result.speech_summary.completed >= 8);
  return result;
}

/* Functionality: Apply one explicitly labeled visibility boundary to original page lifecycle handlers.
 * Inputs: Browser page and hidden/visible value. Outputs: Original listener effects.
 * Logic: Replace the document visibility getter and dispatch its event.
 * Constraints: Test-only visibility fixture, not real OS background execution or permission evidence. */
async function setVisibility(page, value) {
  await page.evaluate(visibility => {
    Object.defineProperty(document, "visibilityState", {configurable: true, get: () => visibility});
    document.dispatchEvent(new Event("visibilitychange"));
  }, value);
}

/* Functionality: Verify user/MCP/form/hidden stops invalidate native speech and original GPS ownership.
 * Inputs: Page, maps URL, session ID and explicit boundary name. Outputs: Closed-run raw evidence.
 * Logic: Exercise both UI/MCP start-stop directions, wait for real output start, apply the chosen
 * original boundary and deliberately deliver a retained old callback afterward.
 * Constraints: No core methods replaced; visibility is explicitly synthetic and no recovery is automatic. */
async function stoppedSession(page, url, sessionId, endMode) {
  if (!(await capture(page)).cleanup.navigation.route) await planRoute(page);
  await resetProbe(page);
  if (endMode === "ui_stop_after_mcp_start") {
    const start = await runTool(url, sessionId, "navigation_start"); assert.equal(start.accepted, true);
  } else await page.click("#navigation-start");
  const active = await capture(page), watch = active.cleanup.gps.watch_count - 1;
  assert.equal(active.cleanup.gps.active_watch_count, 1);
  await deliverFix(page, 103.7771, 1.2971);
  await page.waitForFunction(() => window.mapsSpeechControllers.adapter.active?.started === true &&
    window.mapsSpeechControllers.driver.current !== null || window.mapsSpeechProbe.speech.some(event => event.phase === "paused"),
  null, {timeout: 15000, polling: "raf"});
  const before = await capture(page);
  assert.equal(before.cleanup.speech.paused, false);
  assert.ok(before.utterances.length > 0);
  if (endMode === "mcp_stop_after_ui_start") {
    assert.equal((await runTool(url, sessionId, "navigation_stop")).accepted, true);
  } else if (endMode === "ui_stop_after_mcp_start") await page.click("#navigation-stop");
  else if (endMode === "route_input_change") await page.locator("#start-longitude").fill("103.7767");
  else if (endMode === "hidden") await setVisibility(page, "hidden");
  else throw new Error("Unknown explicit stop boundary");
  await page.waitForTimeout(150);
  const stopped = await capture(page); assertStopped(stopped);
  assert.ok(stopped.cleanup.gps.cleared.includes(watch));
  if (endMode === "route_input_change") {
    assert.equal(stopped.cleanup.navigation.route, null);
    assert.equal((await runTool(url, sessionId, "navigation_start")).reason, "route_required");
  }
  const count = stopped.navigation.length, submitted = stopped.speech.filter(event => event.phase === "output_submitted").length;
  await deliverFix(page, 103.7786, 1.2981, 5, watch);
  await page.waitForTimeout(250);
  const late = await capture(page); assertStopped(late);
  assert.equal(late.navigation.length, count, "A cleared watch's late callback must not publish a new state");
  assert.equal(late.speech.filter(event => event.phase === "output_submitted").length, submitted);
  if (endMode === "hidden") {
    assert.equal(late.cleanup.navigation.reason, "page_hidden");
    assert.equal((await runTool(url, sessionId, "navigation_start")).reason, "page_hidden");
    await setVisibility(page, "visible"); await page.waitForTimeout(350);
    assertStopped(await capture(page));
  }
  const result = await capture(page);
  result.end_mode = endMode; result.before_stop = before.cleanup;
  result.after_stop_before_late_callback = stopped.cleanup;
  result.speech_summary = verifySpeech(result);
  return result;
}

/* Functionality: Check original maps and integrated speech controls at supported narrow/wide widths.
 * Inputs: Browser page and unused screenshot path. Outputs: Checked widths and actual screenshot.
 * Logic: Inspect horizontal overflow, restore 390px and write a fresh full-page image.
 * Constraints: No CSS injection, browser-device accessibility claim or screenshot overwrite. */
async function checkLayouts(page, screenshot) {
  const widths = [320, 390, 1280];
  for (const width of widths) {
    await page.setViewportSize({width, height: 844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true,
      `Maps speech page overflows at ${width}px`);
  }
  await page.setViewportSize({width: 390, height: 844});
  await writeFile(screenshot, await page.screenshot({fullPage: true}), {flag: "wx"});
  return widths;
}

/* Functionality: Run one original maps integration check with explicit synthetic route/sensor boundaries.
 * Inputs: Loopback /maps URL, installed Playwright runtime and unused screenshot filename.
 * Outputs: Complete report with all observations on success or failure.
 * Logic: Real original UI and one original control socket; fail forbidden module/network paths,
 * missing native speech, incorrect singleton/watch ownership or unclosed native output.
 * Constraints: No service start, implicit fallback/retry, offline CDN replacement or inference invocation. */
async function run(url, runtime, screenshot) {
  const report = {complete: false,
    scope: "Real original maps UI/Chrome/Leaflet CDN/MCP HTTP+WS/native Chinese TTS; synthetic ORS config/route and GPS",
    hardware_outputs_verified: false, real_gps_verified: false, real_ors_verified: false,
    fixtures: {config: "Explicit configured ORS response fixture", route: ROUTE,
      gps: "Explicit watchPosition callbacks, actual browser Date.now timestamps",
      visibility: "Explicit document getter/event fixture; not real OS background behavior"},
    dependency_mode: "Original real Leaflet 1.9.4 CDN/SRI and OSM network requests; no offline fallback",
    requests: [], external_requests: [], unexpected_requests: [], forbidden_requests: [],
    dependency_responses: [], dependency_failures: [], websockets: [], failures: [], sessions: []};
  let browser, page, routeCalls = 0, resolveWelcome;
  const welcome = new Promise(resolvePromise => { resolveWelcome = resolvePromise; });
  try {
    const {chromium} = createRequire(import.meta.url)(resolve(runtime));
    browser = await chromium.launch({channel: "chrome", headless: true});
    const context = await browser.newContext({viewport: {width: 390, height: 844}});
    await context.addInitScript(installGPS);
    await context.route(new URL("config/", url).href, route => route.fulfill({json: {
      provider: "openrouteservice", profile: "foot-walking", configured: true, code: "synthetic_fixture"}}));
    /* Functionality: Fulfill one explicitly submitted original UI route request with labeled synthetic data.
     * Inputs: Intercepted same-origin route request. Outputs: ORS-compatible response fixture.
     * Logic: Check original POST/body endpoints and increment a new response identity.
     * Constraints: Never contacts ORS or substitutes a response after an upstream failure. */
    function fixtureRoute(route) {
      const request = route.request();
      try {
        assert.equal(request.method(), "POST");
        assert.deepEqual(request.postDataJSON(), {start: ROUTE.geometry.coordinates[0], end: ROUTE.geometry.coordinates.at(-1)});
      } catch {
        report.failures.push("Original route request does not match the explicitly selected synthetic endpoints");
        return route.fulfill({status: 400, json: {code: "test_fixture_input_rejected",
          message: "Original route request did not match explicit test fixture inputs"}});
      }
      routeCalls++;
      return route.fulfill({json: {...ROUTE, request_id: `maps-speech-fixture-${routeCalls}`}});
    }
    await context.route(new URL("route/", url).href, fixtureRoute);
    page = await context.newPage();
    /* Functionality: Record exact map dependencies and reject unrelated network/module chains.
     * Inputs: Original browser request. Outputs: Body-free URL classifications.
     * Logic: Permit same-origin assets plus exact original Leaflet and OSM tile dependencies.
     * Constraints: Does not intercept dependencies, hide failures or contact ORS intentionally. */
    function requested(request) {
      const address = new URL(request.url());
      if (!["http:", "https:"].includes(address.protocol)) return;
      const item = {origin: address.origin, path: address.pathname}; report.requests.push(item);
      if (address.origin !== new URL(url).origin) {
        report.external_requests.push(item);
        const leaflet = address.origin === "https://unpkg.com" &&
          ["/leaflet@1.9.4/dist/leaflet.js", "/leaflet@1.9.4/dist/leaflet.css"].includes(address.pathname);
        const tile = address.origin === "https://tile.openstreetmap.org" && /^\/\d+\/\d+\/\d+\.png$/.test(address.pathname);
        if (!leaflet && !tile) report.unexpected_requests.push(item);
      }
      if (/\/(?:video[^/]*|camera\.js|guidance\.js|local-mvp\.js|navigation-mvp\.js|video-replay\.js|live-detection-client\.js|frame-pipeline\.js|scene-client\.js)$/.test(address.pathname) ||
          /\/(?:inference|infer|detect)(?:\/|$)/.test(address.pathname)) report.forbidden_requests.push(item);
    }
    /* Functionality: Preserve actual external dependency response evidence.
     * Inputs: Browser response. Outputs: Body-free origin/path/status.
     * Logic: Record original CDN/tile responses separately from synthetic route boundaries.
     * Constraints: HTTP success is not a hardware or route-planning assertion. */
    function responded(response) {
      const address = new URL(response.url());
      if (["https://unpkg.com", "https://tile.openstreetmap.org"].includes(address.origin))
        report.dependency_responses.push({origin: address.origin, path: address.pathname, status: response.status()});
    }
    /* Functionality: Surface production observer failures without logging spoken or input bodies.
     * Inputs: Native console message. Outputs: Finite failure marker.
     * Logic: Inspect fixed observer-failure diagnostics only.
     * Constraints: Unrelated source text is never copied to the report. */
    function consoleFailed(message) {
      if (message.type() === "error" && /observer failed/i.test(message.text()))
        report.failures.push("Production observer reported a failure");
    }
    page.on("request", requested); page.on("response", responded); page.on("console", consoleFailed);
    page.on("pageerror", error => report.failures.push(error.message));
    page.on("requestfailed", request => {
      const address = new URL(request.url());
      if (address.origin !== new URL(url).origin) report.dependency_failures.push({origin: address.origin,
        path: address.pathname, error: request.failure()?.errorText ?? null});
    });
    const controlURL = new URL("../ws/control/", url); controlURL.protocol = "ws:";
    page.on("websocket", socket => {
      const item = {url: socket.url(), closed: false}; report.websockets.push(item);
      socket.on("close", () => { item.closed = true; });
      socket.on("framereceived", frame => {
        try {
          const data = JSON.parse(String(frame.payload));
          if (socket.url() === controlURL.href && data.type === "welcome") resolveWelcome(data.session_id);
        } catch { /* Non-registration frames are not retained or interpreted by this checker. */ }
      });
    });
    const response = await page.goto(url, {waitUntil: "domcontentloaded"});
    assert.equal(response.status(), 200, "Run the ordinary config.asgi /maps/ entry, not the local-only wrapper");
    report.permissions_policy = response.headers()["permissions-policy"];
    assert.match(report.permissions_policy, /geolocation=\(self\)/);
    for (const feature of ["camera", "microphone"])
      assert.match(report.permissions_policy, new RegExp(`${feature}=\\(\\)`));
    report.identity = await page.evaluate(observe);
    await page.waitForFunction(() => !document.getElementById("example").disabled);
    assert.equal(await page.evaluate(() => globalThis.L?.version), "1.9.4");
    const sessionId = await page.evaluate(() => sessionStorage.getItem("prs.ai.session"));
    assert.equal(await waitForGate(welcome, "Original maps control registration"), sessionId);
    assert.equal(await page.isChecked("#speech-enabled"), false);
    assert.equal(await page.isChecked("#navigation-speech-enabled"), false);
    report.before = await runTool(url, sessionId, "navigation_status");
    assert.equal(report.before.state, "idle");
    assert.equal((await runTool(url, sessionId, "navigation_start")).reason, "route_required");
    report.planned_route = await planRoute(page);
    report.speech_configuration = await enableSpeech(page);
    report.sessions.push(await fullSession(page, url, sessionId));
    for (const boundary of ["mcp_stop_after_ui_start", "ui_stop_after_mcp_start", "route_input_change", "hidden"])
      report.sessions.push(await stoppedSession(page, url, sessionId, boundary));
    const runs = report.sessions.map(session => session.speech.find(event => event.phase === "run")?.run_id);
    assert.ok(runs.every(Boolean)); assert.equal(new Set(runs).size, report.sessions.length);
    report.layouts = await checkLayouts(page, screenshot); report.screenshot = screenshot;
    report.final = await capture(page); assertStopped(report.final);
    report.route_calls = routeCalls; assert.equal(routeCalls, 2, "Navigation must never replan by itself");
    assert.equal(report.websockets.length, 1, "Only the original ai-control WebSocket may connect");
    assert.equal(report.websockets[0].url, controlURL.href); assert.equal(report.websockets[0].closed, false);
    report.control_open_at_verification = true;
    assert.deepEqual(report.unexpected_requests, []); assert.deepEqual(report.forbidden_requests, []);
    for (const path of ["/leaflet@1.9.4/dist/leaflet.js", "/leaflet@1.9.4/dist/leaflet.css"])
      assert.ok(report.dependency_responses.some(item => item.origin === "https://unpkg.com" &&
        item.path === path && item.status >= 200 && item.status < 300), `Original Leaflet dependency unavailable: ${path}`);
    assert.deepEqual(report.failures, []);
    report.complete = true;
  } catch (error) {
    report.failures.push(error.message);
    if (page) {
      try { report.interrupted_session = await capture(page); }
      catch (inspectionError) { report.inspection_error_type = inspectionError.name; }
    }
  } finally { if (browser) await browser.close(); }
  return report;
}

/* Functionality: Reject an existing report/screenshot destination before browser work.
 * Inputs: Absolute path. Outputs: Confirmed absence or explicit failure.
 * Logic: Only ENOENT is accepted. Constraints: Final writes also use exclusive creation. */
async function requireNew(path) {
  try { await access(path); throw new Error("Choose new report and screenshot paths"); }
  catch (error) { if (error.code !== "ENOENT") throw error; }
}

/* Functionality: Validate explicit development endpoints/runtimes and preserve one complete browser report.
 * Inputs: CLI loopback /maps/ URL and new JSON path; PYTHON/PRS_PLAYWRIGHT_PATH environment.
 * Outputs: New JSON/screenshot and truthful exit status.
 * Logic: Reject credentials/query/hash, extra arguments and reused evidence before launching once.
 * Constraints: No service start, download, CDN fallback, output overwrite, Git mutation or retry. */
async function main() {
  const [url, output, ...extra] = process.argv.slice(2), runtime = process.env.PRS_PLAYWRIGHT_PATH;
  if (!url || !output || !runtime || !process.env.PYTHON || extra.length)
    throw new Error("Use MAPS_URL NEW_REPORT.json; set PYTHON and PRS_PLAYWRIGHT_PATH");
  const address = new URL(url);
  if (address.protocol !== "http:" || !["127.0.0.1", "localhost"].includes(address.hostname) ||
      address.pathname !== "/maps/" || address.username || address.password || address.search || address.hash)
    throw new Error("Use the loopback ordinary config.asgi /maps/ entry");
  const path = resolve(output);
  if (!path.toLowerCase().endsWith(".json")) throw new Error("Report path must end with .json");
  const screenshot = path.slice(0, -5) + ".png";
  await requireNew(path); await requireNew(screenshot); await mkdir(dirname(path), {recursive: true});
  const report = await run(url, runtime, screenshot);
  await writeFile(path, JSON.stringify(report, null, 2) + "\n", {flag: "wx"});
  process.stdout.write(JSON.stringify({complete: report.complete, route_calls: report.route_calls ?? null,
    sessions: report.sessions.map(session => ({end_mode: session.end_mode, ...session.speech_summary})),
    failures: report.failures}) + "\n");
  if (!report.complete) process.exitCode = 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) await main();
