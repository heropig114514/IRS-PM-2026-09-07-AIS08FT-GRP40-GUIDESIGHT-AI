/* Responsibilities: Verify the isolated local navigation replay and native Chinese speech in Chrome.
 * Implementation: Explicit fixture load, two user-started sessions, original DOM/controller/output
 * events and source-bound utterance/rate checks; supplemental explicit response-delivery and
 * visibility fixtures cover cancellation boundaries; a separately labeled stopOwned failure
 * fixture tests explicit cleanup after one refused cancellation without replacing the native engine.
 * Related Modules: integration/navigation.html/navigation-mvp.js/navigation-replay.js/navigation-speech.js;
 * navigation.js, speech-output.js, SystemCode/backend/integration/services.py.
 * Declaration Index: observe; observe.recordNavigation; observe.recordReplay; observe.recordSpeech;
 * observe.recordDriver; capture; resetProbe; enableSpeech; verifySpeech; verifySpeech.identify;
 * verifySpeech.collect; verifySpeech.requireDriver; manualSession;
 * arrivalSession; verifyNavigation; deferred; waitForGate; setVisibility; assertStopped;
 * loadSampleExplicit; deferredReadSession; deferredReadSession.holdRead; hiddenNativeSession;
 * cancellationFailureSession; cancellationFailureSession.installFailure;
 * cancellationFailureSession.installFailure.stopOwnedFailure;
 * checkLayouts; run; run.requested; run.consoleFailed;
 * requireNew; main.
 * Variable Index: PRS_PLAYWRIGHT_PATH — explicit installed Playwright runtime; report — unfiltered
 * synthetic navigation/replay and body-free speech lifecycle evidence; localNavigationProbe owns
 * browser observations. Utterance bodies are inspected only in memory and never serialized.
 * Constraints: Synthetic route/GPS only; native engine events do not verify physical audibility,
 * real GPS, real streets, ORS, background OS behavior, video inference or cloud deployment.
 */
import assert from "node:assert/strict";
import {createRequire} from "node:module";
import {access, mkdir, writeFile} from "node:fs/promises";
import {resolve, dirname} from "node:path";
import {pathToFileURL} from "node:url";

/* Functionality: Observe original controllers and native output without replacing any boundary.
 * Inputs: Browser page implicit. Outputs: In-memory probe and controller references.
 * Logic: Subscribe before fixture loading; compare actual native-start utterance with its owned
 * source snapshot/formatter and exact submitted rate. Constraints: No activation, manufactured
 * fixes/events, retained speech body, or hardware-success assertion. */
async function observe() {
  const module = await import("/integration/assets/navigation-mvp.js");
  const {navigationSpeechText} = await import("/integration/assets/navigation-speech.js");
  const {speechOutput} = await import("/live/assets/speech-output.js");
  await module.navigationMvpReady;
  const adapter = module.navigationSpeech;
  if (!module.navigation || !module.replayGeolocation || !adapter || !speechOutput)
    throw new Error("Local navigation controllers are unavailable");
  window.localNavigationControllers = {navigation: module.navigation,
    replay: module.replayGeolocation, speech: adapter, driver: speechOutput};
  window.localNavigationProbe = {navigation: [], replay: [], speech: [], driver: [], utterances: []};
  /* Functionality: Preserve each original navigation snapshot.
   * Inputs: Production DOM event. Outputs: Detached state in probe.
   * Logic: Clone original detail. Constraints: Synthetic fixture state, no rewritten route or GPS. */
  function recordNavigation(event) { window.localNavigationProbe.navigation.push(structuredClone(event.detail)); }
  /* Functionality: Preserve each source scheduler lifecycle event.
   * Inputs: Production DOM event. Outputs: Metadata in original order.
   * Logic: Clone original detail. Constraints: No generated fix or source-time adjustment. */
  function recordReplay(event) { window.localNavigationProbe.replay.push(structuredClone(event.detail)); }
  /* Functionality: Verify actual native speech starts against request-owned source and settings.
   * Inputs: Adapter lifecycle event. Outputs: Raw body-free event and comparison booleans.
   * Logic: Use the active source snapshot, formatter and current native utterance at output_start.
   * Constraints: Never serialize spoken text, replace voices or infer hardware audibility. */
  function recordSpeech(event) {
    window.localNavigationProbe.speech.push(structuredClone(event));
    if (event.phase !== "output_start") return;
    const active = adapter.active, last = speechOutput.lastRequest, utterance = speechOutput.current;
    const source = active?.snapshot, userRate = Number(document.getElementById("speech-rate").value);
    const owned = last?.id === event.request_id && active?.requestId === event.request_id;
    window.localNavigationProbe.utterances.push({...event,
      source_state: source?.state ?? null, source_reason: source?.reason ?? null,
      source_step_index: source?.progress?.step_index ?? null,
      source_timestamp: source?.position?.timestamp ?? null,
      request_owned: owned,
      text_matches_policy: Boolean(owned && source && typeof utterance?.text === "string" &&
        utterance.text.length > 0 && utterance.text === navigationSpeechText(source, last.language)),
      local_chinese_voice: owned && last.language === "zh-CN" && last.local_service === true &&
        utterance?.voice?.localService === true && utterance.voice.lang.split("-")[0].toLowerCase() === "zh",
      configured_rate: last?.rate ?? null, engine_rate: utterance?.rate ?? null, user_rate: userRate,
      rate_matches_user_setting: Boolean(owned && Number.isFinite(utterance?.rate) &&
        Math.abs(utterance.rate - last.rate) <= 1e-6 && last.rate === userRate)});
  }
  /* Functionality: Preserve the original shared-driver native lifecycle events.
   * Inputs: Body-free driver event. Outputs: Probe event append.
   * Logic: Keep original request IDs/phases/timestamps. Constraints: No fake start/end or text. */
  function recordDriver(event) { window.localNavigationProbe.driver.push(structuredClone(event)); }
  document.addEventListener("prs-navigation-change", recordNavigation);
  document.addEventListener("prs-local-navigation-replay", recordReplay);
  adapter.subscribe(recordSpeech);
  speechOutput.subscribe(recordDriver);
}

/* Functionality: Read all observations and explicit controller ownership at one boundary.
 * Inputs: Browser page. Outputs: Detached report-safe evidence.
 * Logic: Preserve every raw event plus finite cleanup metadata.
 * Constraints: No native utterance, callback, timer object or spoken body is serialized. */
async function capture(page) {
  return page.evaluate(() => {
    const control = window.localNavigationControllers;
    return {...structuredClone(window.localNavigationProbe), cleanup: {
      navigation: control.navigation.describe(), replay: control.replay.describe(), speech: control.speech.describe(),
      gps_watch_cleared: control.navigation.watchId === null,
      gps_deadline_cleared: control.navigation.timer === null,
      replay_timer_count: control.replay.timers.size,
      owned_speech_cleared: control.speech.active === null,
      native_utterance_cleared: control.driver.current === null,
    }};
  });
}

/* Functionality: Begin a fresh evidence partition before an explicit user start.
 * Inputs: Page. Outputs: Empty probe arrays. Logic: Clear observations only.
 * Constraints: Does not change production controllers, clocks, input data or output settings. */
async function resetProbe(page) {
  await page.evaluate(() => {
    window.localNavigationProbe = {navigation: [], replay: [], speech: [], driver: [], utterances: []};
  });
}

/* Functionality: Explicitly select installed local Chinese speech and enable both user switches.
 * Inputs: Real page/native inventory. Outputs: Selected voice and unchanged default rate.
 * Logic: Wait once for inventory, refresh UI, select matching local voice, check switches.
 * Constraints: Missing Chinese/local voices fail; no language substitution, retries or rate edits. */
async function enableSpeech(page) {
  await page.waitForFunction(() => speechSynthesis.getVoices().length > 0, null, {timeout: 5000});
  assert.equal(await page.inputValue("#navigation-speech-language"), "zh-CN");
  await page.click("#navigation-voices-refresh");
  const voices = await page.locator("#navigation-speech-voice option").evaluateAll(options =>
    options.filter(option => option.value && !option.disabled).map(option => option.value));
  assert.ok(voices.length > 0, "No installed matching local Chinese voice; do not substitute another language");
  await page.selectOption("#navigation-speech-voice", voices[0]);
  await page.check("#speech-enabled");
  await page.check("#navigation-speech-enabled");
  const rate = Number(await page.inputValue("#speech-rate"));
  assert.equal(rate, 2, "Navigation speech default must be 2x without checker adjustment");
  return {enabled: true, language: "zh-CN", voice_id: voices[0], rate, hardware_verified: false};
}

/* Functionality: Correlate every native adapter start with original driver events and policy.
 * Inputs: One unfiltered session report. Outputs: Lifecycle counts plus assertion failures.
 * Logic: Match each submitted request to exactly one end or accepted cancel; starts also require
 * source, owner, local voice and rate binding. Queued cancellation without a start is valid.
 * Constraints: Synchronous start may precede the adapter's submission report; native completion
 * and accepted cancellation remain distinct, neither claiming physical audibility. */
export function verifySpeech(session) {
  /* Functionality: Validate one native request identity. Inputs: Lifecycle event. Outputs: ID.
   * Logic: Native sequence IDs are positive safe integers. Constraints: No coercion or inferred IDs. */
  function identify(event) {
    assert.ok(Number.isSafeInteger(event.request_id) && event.request_id > 0, "Invalid speech request identity");
    return event.request_id;
  }
  /* Functionality: Build a unique lifecycle-phase index. Inputs: Phase. Outputs: Map of original events.
   * Logic: Duplicate events fail before matching. Constraints: Evidence remains unfiltered in the report. */
  function collect(phase) {
    const result = new Map();
    for (const event of session.speech.filter(item => item.phase === phase)) {
      const id = identify(event);
      assert.equal(result.has(id), false, `Duplicate ${phase} for speech request ${id}`);
      result.set(id, event);
    }
    return result;
  }
  /* Functionality: Require one matching original driver phase. Inputs: Phase and request ID.
   * Outputs: Assertion. Logic: Match native lifecycle by exact identity, not aggregate counts.
   * Constraints: An adapter cancellation acknowledgement must retain the driver's original cancel. */
  function requireDriver(phase, id) {
    assert.equal(session.driver.filter(event => event.phase === phase && event.request_id === id).length, 1,
      `Missing or duplicate driver ${phase} for speech request ${id}`);
  }
  const submitted = collect("output_submitted"), started = collect("output_start"),
    completed = collect("output_end"), cancelled = collect("output_cancel");
  const starts = session.speech.filter(event => event.phase === "output_start");
  assert.equal(starts.length, session.utterances.length);
  const boundUtterances = new Set();
  for (const utterance of session.utterances) {
    const id = identify(utterance);
    assert.ok(started.has(id) && !boundUtterances.has(id), "Utterance must bind to one unique native start");
    boundUtterances.add(id);
    assert.ok(utterance.request_owned && utterance.text_matches_policy && utterance.local_chinese_voice &&
      utterance.rate_matches_user_setting, "Native utterance/source/voice/rate binding failed");
    requireDriver("start", id);
  }
  for (const [id, event] of started) {
    assert.ok(submitted.has(id), `Native start without submission for speech request ${id}`);
    assert.equal(event.actual_start, true, "Native starts must be explicitly observed");
  }
  for (const id of completed.keys()) {
    assert.ok(submitted.has(id) && started.has(id), `Completion without native start for speech request ${id}`);
    requireDriver("end", id);
  }
  for (const [id, event] of cancelled) {
    assert.ok(submitted.has(id), `Cancellation without submission for speech request ${id}`);
    assert.equal(event.accepted, true, `Unconfirmed cancellation for speech request ${id}`);
    requireDriver("cancel", id);
  }
  for (const id of submitted.keys()) {
    requireDriver("submitted", id);
    assert.equal(Number(completed.has(id)) + Number(cancelled.has(id)), 1,
      `Speech request ${id} must have exactly one completion or accepted cancellation`);
  }
  assert.equal(session.speech.some(event => event.phase === "paused" || event.phase === "output_error"), false,
    "Native speech failure requires explicit recovery and must fail this check");
  return {submitted: submitted.size, started: started.size, completed: completed.size,
    cancelled: cancelled.size, hardware_verified: false};
}

/* Functionality: Verify user stop cancels source watch, pending timers and adapter-owned output.
 * Inputs: Loaded/configured page. Outputs: Raw manual-stop session and cleanup assertions.
 * Logic: Start by real click, wait for first source fix, stop by click, then observe past next offset.
 * Constraints: No synthetic timing changes or output completion claim for interrupted speech. */
async function manualSession(page) {
  await resetProbe(page);
  await page.click("#navigation-replay-start");
  await page.waitForFunction(() => window.localNavigationProbe.navigation.some(state => state.state === "navigating"),
    null, {timeout: 10000});
  await page.click("#navigation-replay-stop");
  const before = await capture(page);
  const fixCount = before.replay.filter(event => event.phase === "fix").length;
  await page.waitForTimeout(4200);
  const result = await capture(page);
  result.end_mode = "manual_stop";
  assert.ok(fixCount > 0);
  assert.equal(result.replay.filter(event => event.phase === "fix").length, fixCount, "Late source fix after manual stop");
  assert.equal(result.cleanup.navigation.state, "stopped");
  assert.equal(result.cleanup.navigation.active, false);
  assert.equal(result.cleanup.replay.active, false);
  assert.equal(result.cleanup.replay_timer_count, 0);
  assert.equal(result.cleanup.gps_watch_cleared, true);
  assert.equal(result.cleanup.gps_deadline_cleared, true);
  assert.equal(result.cleanup.speech.run_id, null);
  assert.equal(result.cleanup.owned_speech_cleared, true);
  assert.equal(result.cleanup.native_utterance_cleared, true);
  result.speech_summary = verifySpeech(result);
  return result;
}

/* Functionality: Verify original navigation semantics for every source fix in the local fixture.
 * Inputs: Complete session and explicitly loaded synthetic fixture.
 * Outputs: State/order assertions. Logic: Match scheduled source offsets and snapshots, inspect
 * current/next step identities, and require quality loss, deviation, recovery and arrival.
 * Constraints: Controller thresholds are read unchanged; no inferred road or device correctness. */
function verifyNavigation(session, fixture) {
  const fixes = session.replay.filter(event => event.phase === "fix");
  assert.equal(fixes.length, fixture.fixes.length, "Every fixture fix must be retained");
  const anchor = fixes[0].scheduled_at;
  assert.deepEqual(fixes.map(event => event.scheduled_at - anchor), fixture.fixes.map(fix => fix.offset_ms));
  assert.deepEqual(fixes.map(event => event.index), fixture.fixes.map((_, index) => index + 1));
  for (const fix of fixes) {
    const matching = session.navigation.filter(state => state.position?.timestamp === fix.scheduled_at);
    assert.equal(matching.length, 1, "Each source fix must bind to exactly one original navigation snapshot");
  }
  for (const state of session.navigation) {
    const progress = state.progress;
    if (!progress?.current_step) continue;
    assert.deepEqual(progress.current_step, fixture.route.steps[progress.step_index]);
    assert.deepEqual(progress.next_step, fixture.route.steps[progress.step_index + 1] ?? null);
  }
  const states = session.navigation;
  for (const index of [0, 1, 2])
    assert.ok(states.some(state => state.state === "navigating" && state.progress?.step_index === index));
  const inaccurate = states.findIndex(state => state.state === "locating" && state.reason === "accuracy_insufficient");
  assert.ok(inaccurate >= 0);
  const recovered = states.findIndex((state, index) => index > inaccurate && state.state === "navigating");
  assert.ok(recovered > inaccurate);
  const deviation = states.findIndex((state, index) => index > recovered && state.state === "off_route");
  assert.ok(deviation > recovered);
  assert.ok(states[deviation].progress.cross_track_m > states[deviation].policy.off_route_m);
  assert.ok(states.some((state, index) => index > deviation && state.state === "navigating"));
  assert.equal(states.at(-1).state, "arrived");
  assert.equal(states.at(-1).progress.current_step.type, 10);
  assert.equal(states.at(-1).active, false);
}

/* Functionality: Verify a fresh explicit run reaches arrival and finishes its actual arrival speech.
 * Inputs: Loaded real page and local fixture. Outputs: Complete raw session and engine evidence.
 * Logic: Start via click; wait for controller arrival, then owned native arrival end within15s.
 * Constraints: No seek/loop/speed changes, automatic retry, manufactured completion or GPS calls. */
async function arrivalSession(page, fixture) {
  await resetProbe(page);
  await page.click("#navigation-replay-start");
  await page.waitForFunction(() => window.localNavigationProbe.navigation.some(state => ["arrived", "error"].includes(state.state)) ||
    window.localNavigationProbe.speech.some(event => event.phase === "paused"), null, {timeout: 50000});
  assert.equal((await capture(page)).cleanup.navigation.state, "arrived");
  await page.waitForFunction(() => window.localNavigationProbe.speech.some(event => event.phase === "output_end" && event.kind === "arrived"),
    null, {timeout: 15000});
  const result = await capture(page);
  result.end_mode = "arrival";
  verifyNavigation(result, fixture);
  result.speech_summary = verifySpeech(result);
  assert.ok(result.speech_summary.completed > 0);
  const arrivalStart = result.utterances.find(event => event.kind === "arrived");
  assert.ok(arrivalStart?.text_matches_policy, "Arrival must start through the native output driver");
  assert.ok(result.speech.some(event => event.phase === "output_end" && event.kind === "arrived" &&
    event.request_id === arrivalStart.request_id));
  assert.equal(result.cleanup.replay.active, false);
  assert.equal(result.cleanup.replay_timer_count, 0);
  assert.equal(result.cleanup.gps_watch_cleared, true);
  assert.equal(result.cleanup.gps_deadline_cleared, true);
  assert.equal(result.cleanup.owned_speech_cleared, true);
  assert.equal(result.cleanup.native_utterance_cleared, true);
  return result;
}

/* Functionality: Hold one explicitly controlled supplemental test boundary.
 * Inputs: None. Outputs: Promise and resolve function.
 * Logic: Only a test action releases the promise. Constraints: No automatic retries, source time
 * changes, unreported timeout success or production API replacement. */
function deferred() {
  let resolvePromise;
  const promise = new Promise(resolve => { resolvePromise = resolve; });
  return {promise, resolve: resolvePromise};
}

/* Functionality: Bound a supplemental test gate without retrying the underlying operation.
 * Inputs: Promise, diagnostic label and optional timeout/timer boundaries. Outputs: Original value or rejection.
 * Logic: Race a single deadline and always release its timer on either outcome.
 * Constraints: Timeout is a failed check, never success, cancellation evidence or a second request. */
export async function waitForGate(promise, label, {timeoutMs = 10000,
  setTimer = (callback, delay) => globalThis.setTimeout(callback, delay),
  clearTimer = handle => globalThis.clearTimeout(handle)} = {}) {
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new TypeError("Gate timeout must be positive and finite");
  let timer;
  try {
    return await Promise.race([promise, new Promise((resolveGate, reject) => {
      timer = setTimer(() => reject(new Error(`${label} timed out after ${timeoutMs}ms`)), timeoutMs);
    })]);
  } finally { if (timer !== undefined) clearTimer(timer); }
}

/* Functionality: Dispatch an explicit document-visibility fixture through actual page handlers.
 * Inputs: Page and hidden/visible fixture value. Outputs: Original lifecycle listener effects.
 * Logic: Override only the document visibility getter, then dispatch visibilitychange.
 * Constraints: Not an OS background, tab suspension, permission, GPS or speech-engine assertion. */
async function setVisibility(page, value) {
  await page.evaluate(visibility => {
    Object.defineProperty(document, "visibilityState", {configurable: true, get: () => visibility});
    document.dispatchEvent(new Event("visibilitychange"));
  }, value);
}

/* Functionality: Assert complete replay/run ownership cleanup at a lifecycle stop boundary.
 * Inputs: Captured original controller evidence. Outputs: Cleanup assertions.
 * Logic: Require canceled watches/deadlines/timers and no owned native utterance or error latch.
 * Constraints: Does not infer audible silence or require removal of the retained selected route. */
function assertStopped(result) {
  assert.equal(result.cleanup.navigation.active, false);
  assert.equal(result.cleanup.replay.active, false);
  assert.equal(result.cleanup.replay_timer_count, 0);
  assert.equal(result.cleanup.gps_watch_cleared, true);
  assert.equal(result.cleanup.gps_deadline_cleared, true);
  assert.equal(result.cleanup.speech.run_id, null);
  assert.equal(result.cleanup.speech.paused, false, "Intentional lifecycle cancellation must not latch an output error");
  assert.equal(result.cleanup.owned_speech_cleared, true);
  assert.equal(result.cleanup.native_utterance_cleared, true);
}

/* Functionality: Load the unchanged local fixture again only through an explicit UI action.
 * Inputs: Page with no supplemental response interceptor. Outputs: Ready route snapshot.
 * Logic: Observe the actual local response, click load and await original ready controls.
 * Constraints: No route installation through evaluate, fallback data or automatic recovery. */
async function loadSampleExplicit(page) {
  const response = page.waitForResponse(item => new URL(item.url()).pathname === "/integration/assets/navigation-sample.json");
  await page.click("#navigation-load-sample");
  assert.equal((await response).status(), 200);
  await page.waitForFunction(() => !document.getElementById("navigation-replay-start").disabled);
  const result = await capture(page);
  assert.equal(result.cleanup.navigation.state, "ready");
  assert.equal(result.cleanup.navigation.active, false);
  return result;
}

/* Functionality: Verify stopping or hiding during a late local read prevents route installation.
 * Inputs: Page, explicit stop/hidden mode and caller-owned evidence partition. Outputs: Raw boundary evidence.
 * Logic: Fetch the real same-origin fixture once, hold only response delivery, cancel through
 * actual handlers, release the unchanged response and verify it stays canceled; load again by UI.
 * Constraints: Supplemental network-delivery fixture only, clearly separate from native sessions;
 * no replacement payload, upstream retry, external request, GPS or speech substitute. */
async function deferredReadSession(page, endMode, evidence = {}) {
  await resetProbe(page);
  const target = new URL("/integration/assets/navigation-sample.json", page.url()).href;
  const held = deferred(), release = deferred(), delivered = deferred();
  /* Functionality: Retain the real local response until the explicit cancellation action finishes.
   * Inputs: Playwright route. Outputs: Original response delivered after gate release.
   * Logic: One route.fetch with redirects/retries disabled; fulfill from that unmodified response.
   * Constraints: Fixture changes delivery timing only, never route/GPS content or native speech. */
  async function holdRead(route) {
    try {
      const response = await route.fetch({maxRedirects: 0, maxRetries: 0});
      held.resolve({status: response.status()});
      await release.promise;
      await route.fulfill({response});
      delivered.resolve({status: response.status()});
    } catch (error) {
      held.resolve({error: error.message});
      delivered.resolve({error: error.message});
    }
  }
  await page.route(target, holdRead, {times: 1});
  Object.assign(evidence, {fixture: "Same-origin response-delivery hold; original local payload and native controllers unchanged",
    end_mode: `deferred_read_${endMode}`, os_background_verified: false});
  try {
    await page.click("#navigation-load-sample");
    const ready = await waitForGate(held.promise, "Local source response interception");
    assert.equal(ready.error, undefined); assert.equal(ready.status, 200);
    assert.equal(await page.isEnabled("#navigation-replay-stop"), true, "Loading must expose a user stop action");
    evidence.loading = await capture(page);
    assert.equal(evidence.loading.cleanup.navigation.route, null);
    assert.equal(await page.getAttribute("#navigation-source-status", "data-kind"), "loading");
    if (endMode === "hidden") await setVisibility(page, "hidden");
    else await page.click("#navigation-replay-stop");
    evidence.cancelled = await capture(page);
    assertStopped(evidence.cancelled);
    assert.equal(evidence.cancelled.cleanup.navigation.route, null);
    const response = page.waitForResponse(item => item.url() === target);
    release.resolve();
    const completion = await waitForGate(delivered.promise, "Local source response delivery");
    assert.equal(completion.error, undefined); assert.equal(completion.status, 200);
    assert.equal(await (await response).finished(), null);
    // Functionality: Let real fetch/text continuations settle; Inputs: local response completion;
    // Outputs: event-loop checkpoint; Logic: bounded100ms observation; Constraints: no source timer edits.
    await page.waitForTimeout(100);
    evidence.after_late_read = await capture(page);
    assertStopped(evidence.after_late_read);
    assert.equal(evidence.after_late_read.cleanup.navigation.route, null, "Canceled late read must not install a route");
    assert.equal(evidence.after_late_read.navigation.some(state => state.state === "ready"), false);
    assert.equal(await page.isDisabled("#navigation-replay-start"), true);
    assert.equal(await page.getAttribute("#navigation-source-status", "data-kind"), "idle");
    if (endMode === "hidden") {
      await setVisibility(page, "visible");
      evidence.visible = await capture(page);
      assertStopped(evidence.visible);
      assert.equal(evidence.visible.cleanup.navigation.route, null);
      assert.equal(await page.isDisabled("#navigation-replay-start"), true);
    }
  } catch (error) {
    evidence.failure = {name: error.name, message: error.message};
    throw error;
  } finally {
    release.resolve();
    await page.unroute(target, holdRead);
    if (endMode === "hidden") await setVisibility(page, "visible");
  }
  evidence.explicit_reload = await loadSampleExplicit(page);
  return evidence;
}

/* Functionality: Verify an intentional hidden-page stop during native speech does not latch error.
 * Inputs: Ready page with the selected real local Chinese voice. Outputs: Original two-run evidence.
 * Logic: Wait for actual native start, dispatch explicit hidden/visible fixture, assert no resume,
 * then restart by UI and require another native start without using the recovery button.
 * Constraints: Supplemental visibility getter fixture only, not OS background verification;
 * original speech/GPS timers and engine remain intact and unmodified. */
async function hiddenNativeSession(page) {
  await resetProbe(page);
  const evidence = {fixture: "Explicit document.visibilityState getter and visibilitychange dispatch",
    end_mode: "native_start_hidden_visible_restart", os_background_verified: false, resume_button_used: false};
  await page.click("#navigation-replay-start");
  await page.waitForFunction(() => window.localNavigationProbe.utterances.length > 0 &&
    window.localNavigationControllers.speech.active?.started && window.localNavigationControllers.driver.current,
  null, {timeout: 10000});
  evidence.before_hidden = await capture(page);
  assert.ok(evidence.before_hidden.utterances.length > 0);
  const interruptedId = evidence.before_hidden.cleanup.speech.active_request_id;
  assert.notEqual(interruptedId, null, "Visibility fixture must interrupt an active owned request");
  assert.equal(evidence.before_hidden.cleanup.speech.active_started, true);
  assert.equal(evidence.before_hidden.cleanup.native_utterance_cleared, false);
  await setVisibility(page, "hidden");
  evidence.hidden = await capture(page);
  assertStopped(evidence.hidden);
  assert.equal(evidence.hidden.cleanup.navigation.reason, "page_hidden");
  assert.ok(evidence.hidden.speech.some(event => event.phase === "output_cancel" &&
    event.request_id === interruptedId && event.accepted === true));
  const beforeRuns = evidence.hidden.speech.filter(event => event.phase === "run").length;
  const beforeFixes = evidence.hidden.replay.filter(event => event.phase === "fix").length;
  await setVisibility(page, "visible");
  await page.waitForTimeout(100);
  evidence.visible = await capture(page);
  assertStopped(evidence.visible);
  assert.equal(evidence.visible.speech.filter(event => event.phase === "run").length, beforeRuns);
  assert.equal(evidence.visible.replay.filter(event => event.phase === "fix").length, beforeFixes);
  assert.equal(await page.isDisabled("#navigation-speech-resume"), true);
  const startsBeforeRestart = evidence.visible.utterances.length;
  await page.click("#navigation-replay-start");
  await page.waitForFunction(count => window.localNavigationProbe.utterances.length > count ||
    window.localNavigationProbe.speech.some(event => event.phase === "paused"), startsBeforeRestart, {timeout: 10000});
  evidence.restarted = await capture(page);
  assert.ok(evidence.restarted.utterances.length > startsBeforeRestart, "Explicit restart must reach native speech without resume");
  assert.equal(evidence.restarted.cleanup.speech.paused, false);
  assert.equal(evidence.restarted.cleanup.navigation.active, true);
  await page.click("#navigation-replay-stop");
  evidence.stopped = await capture(page);
  assertStopped(evidence.stopped);
  evidence.speech_summary = verifySpeech(evidence.stopped);
  assert.equal(evidence.stopped.speech.filter(event => event.phase === "run").length, beforeRuns + 1);
  return evidence;
}

/* Functionality: Verify an unconfirmed owned cancellation retains an explicit user cleanup path.
 * Inputs: Ready page and caller-owned supplemental evidence partition. Outputs: Original events plus fault metadata.
 * Logic: Reach a real native start, refuse the first driver stopOwned only, then require exactly
 * one failed stop, no retry on resume, and successful cleanup through a second explicit stop.
 * Constraints: Labeled local-adapter fault injection only; source data, clocks, native utterance,
 * speech engine/start/end and normal replay sessions are unchanged. No automatic recovery. */
async function cancellationFailureSession(page, evidence = {}) {
  await resetProbe(page);
  Object.assign(evidence, {fixture: "One explicit local stopOwned cancellation refusal; original native engine and source unchanged",
    end_mode: "native_start_cancel_failure_explicit_cleanup", expected_failure: "cancel_failed",
    native_engine_replaced: false, source_replaced: false});
  try {
    await page.click("#navigation-replay-start");
    await page.waitForFunction(() => window.localNavigationProbe.utterances.length > 0 &&
      window.localNavigationControllers.speech.active?.started && window.localNavigationControllers.driver.current,
    null, {timeout: 10000});
    evidence.before_failure = await capture(page);
    const ownedId = evidence.before_failure.cleanup.speech.active_request_id;
    assert.notEqual(ownedId, null);
    /* Functionality: Install one documented adapter-boundary cancellation refusal.
     * Inputs: Existing page driver. Outputs: Fault counter and restorable method reference.
     * Logic: First call leaves the native request untouched; unexpected later calls use the real method.
     * Constraints: Does not fabricate speech, GPS or successful cancellation events. */
    await page.evaluate(function installFailure() {
      const driver = window.localNavigationControllers.driver;
      const original = driver.stopOwned;
      const fault = window.localNavigationCancellationFault = {original, attempts: []};
      /* Functionality: Refuse the first owned cancellation while preserving native output.
       * Inputs: Request ID. Outputs: Explicit cancel_failed once, then original method result.
       * Logic: Count calls without altering engine/current on the injected refusal.
       * Constraints: A second implicit call is observable and fails the checker. */
      driver.stopOwned = function stopOwnedFailure(requestId) {
        fault.attempts.push(requestId);
        return fault.attempts.length === 1 ? {accepted: false, reason: "cancel_failed"}
          : original.call(this, requestId);
      };
    });
    await page.click("#navigation-replay-stop");
    evidence.failed_stop = await capture(page);
    evidence.attempts_after_stop = await page.evaluate(() => [...window.localNavigationCancellationFault.attempts]);
    assert.deepEqual(evidence.attempts_after_stop, [ownedId]);
    assert.equal(evidence.failed_stop.cleanup.navigation.active, false);
    assert.equal(evidence.failed_stop.cleanup.replay.active, false);
    assert.equal(evidence.failed_stop.cleanup.replay_timer_count, 0);
    assert.equal(evidence.failed_stop.cleanup.gps_watch_cleared, true);
    assert.equal(evidence.failed_stop.cleanup.gps_deadline_cleared, true);
    assert.equal(evidence.failed_stop.cleanup.speech.paused, true);
    assert.equal(evidence.failed_stop.cleanup.speech.reason, "cancel_failed");
    assert.equal(evidence.failed_stop.cleanup.speech.active_request_id, ownedId);
    assert.equal(evidence.failed_stop.cleanup.native_utterance_cleared, false);
    assert.equal(await page.isEnabled("#navigation-replay-stop"), true, "Unconfirmed owned output needs an explicit stop control");
    await page.click("#navigation-speech-resume");
    evidence.refused_resume = await capture(page);
    evidence.attempts_after_resume = await page.evaluate(() => [...window.localNavigationCancellationFault.attempts]);
    assert.deepEqual(evidence.attempts_after_resume, [ownedId], "Resume must not retry an unconfirmed cancellation");
    assert.equal(evidence.refused_resume.cleanup.speech.paused, true);
    assert.equal(evidence.refused_resume.cleanup.speech.active_request_id, ownedId);
    await page.evaluate(() => {
      window.localNavigationControllers.driver.stopOwned = window.localNavigationCancellationFault.original;
    });
    await page.click("#navigation-replay-stop");
    evidence.cleaned = await capture(page);
    assert.equal(evidence.cleaned.cleanup.owned_speech_cleared, true);
    assert.equal(evidence.cleaned.cleanup.native_utterance_cleared, true);
    assert.equal(evidence.cleaned.cleanup.speech.paused, true, "Successful cleanup alone must not clear the output error latch");
    await page.click("#navigation-speech-resume");
    evidence.resumed = await capture(page);
    assertStopped(evidence.resumed);
    assert.equal(evidence.resumed.speech.filter(event => event.phase === "output_cancel" &&
      event.request_id === ownedId && event.accepted === false).length, 1);
    assert.equal(evidence.resumed.speech.filter(event => event.phase === "output_cancel" &&
      event.request_id === ownedId && event.accepted === true).length, 1);
    assert.equal(evidence.resumed.driver.filter(event => event.phase === "cancel" && event.request_id === ownedId).length, 1);
    const starts = evidence.resumed.utterances;
    assert.ok(starts.length > 0 && starts.every(event => event.request_owned && event.text_matches_policy &&
      event.local_chinese_voice && event.rate_matches_user_setting));
    assert.ok(evidence.resumed.driver.some(event => event.phase === "start" && event.request_id === ownedId));
  } catch (error) {
    evidence.failure = {name: error.name, message: error.message};
    throw error;
  } finally {
    await page.evaluate(() => {
      const fault = window.localNavigationCancellationFault;
      if (fault) {
        window.localNavigationControllers.driver.stopOwned = fault.original;
        delete window.localNavigationCancellationFault;
      }
    });
  }
  return evidence;
}

/* Functionality: Verify responsive layouts and save one fresh390px screenshot.
 * Inputs: Actual page and unused screenshot path. Outputs: Checked widths and screenshot.
 * Logic: Test document overflow at320/390/1280, return to390 and save bytes exclusively.
 * Constraints: No CSS injection, screenshot overwrite or claims about screen-reader behavior. */
async function checkLayouts(page, screenshot) {
  const widths = [320, 390, 1280];
  for (const width of widths) {
    await page.setViewportSize({width, height: 844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true,
      `Navigation page overflows at${width}px`);
  }
  await page.setViewportSize({width: 390, height: 844});
  await writeFile(screenshot, await page.screenshot({fullPage: true}), {flag: "wx"});
  return widths;
}

/* Functionality: Exercise actual local navigation, output and network/module isolation once.
 * Inputs: Verified loopback URL, Playwright runtime and unused screenshot path.
 * Outputs: Success/failure report with all raw event evidence.
 * Logic: Observe before explicit load, enable native output, run stop/arrival, then verify no
 * external/WS/inference/unrelated module requests and independent run IDs.
 * Constraints: Native sessions have no intercepts/API substitutes; separately labeled response
 * delivery/visibility and one stopOwned-refusal fixture retain original data/engines and do not
 * claim real-device fault coverage or OS background behavior. */
async function run(url, runtime, screenshot) {
  const report = {complete: false, scope: "Real Chrome/local replay/native Chinese speech; synthetic route and GPS",
    hardware_outputs_verified: false, real_gps_verified: false, ors_verified: false,
    requests: [], external_requests: [], forbidden_requests: [], websockets: [], failures: [], sessions: []};
  let browser, page;
  try {
    const {chromium} = createRequire(import.meta.url)(resolve(runtime));
    browser = await chromium.launch({channel: "chrome", headless: true});
    page = await browser.newPage({viewport: {width: 390, height: 844}});
    /* Functionality: Record actual resource boundaries without replacing requests.
     * Inputs: Native Playwright request. Outputs: Query-free request evidence.
     * Logic: Exact-origin check and forbidden inference/video/core UI path checks.
     * Constraints: No credential/query logging or request-body/payload alteration. */
    function requested(request) {
      const address = new URL(request.url()), path = address.pathname;
      const item = {method: request.method(), type: request.resourceType(), origin: address.origin, path};
      report.requests.push(item);
      if (address.origin !== new URL(url).origin) report.external_requests.push(item);
      if (/\/(?:ws|maps|scene)(?:\/|$)/.test(path) || /\/integration\/status\//.test(path) ||
          /\/(?:video[^/]*|camera\.js|guidance\.js|local-mvp\.js|maps-page\.js|navigation-ui\.js|live-detection-client\.js|frame-pipeline\.js|scene-client\.js)$/.test(path))
        report.forbidden_requests.push(item);
    }
    /* Functionality: Surface errors hidden behind production observer isolation.
     * Inputs: Browser console message. Outputs: Fixed diagnostic failure marker.
     * Logic: Inspect observer-failure messages only. Constraints: No unrelated console/body dump. */
    function consoleFailed(message) {
      if (message.type() === "error" && /observer failed/i.test(message.text()))
        report.failures.push("Production observer reported a failure");
    }
    page.on("request", requested);
    page.on("websocket", socket => {
      const address = new URL(socket.url()); report.websockets.push(address.origin + address.pathname);
    });
    page.on("console", consoleFailed);
    page.on("pageerror", error => report.failures.push(error.message));
    const response = await page.goto(url, {waitUntil: "networkidle"});
    assert.equal(response.status(), 200);
    report.permissions_policy = response.headers()["permissions-policy"];
    for (const feature of ["camera", "microphone", "geolocation"])
      assert.match(report.permissions_policy, new RegExp(`(?:^|,\\s*)${feature}=\\(\\)(?:,|$)`));
    await page.evaluate(observe);
    assert.equal(await page.isChecked("#speech-enabled"), false);
    assert.equal(await page.isChecked("#navigation-speech-enabled"), false);
    const fixtureResponse = page.waitForResponse(response => new URL(response.url()).pathname === "/integration/assets/navigation-sample.json");
    await page.click("#navigation-load-sample");
    const sample = await fixtureResponse;
    assert.equal(sample.status(), 200);
    const fixture = await sample.json();
    assert.equal(fixture.schema_version, 1); assert.equal(fixture.source, "synthetic");
    report.fixture = fixture;
    await page.waitForFunction(() => !document.getElementById("navigation-replay-start").disabled);
    assert.equal((await capture(page)).cleanup.navigation.state, "ready");
    report.speech_configuration = await enableSpeech(page);
    report.setup = await capture(page);
    report.sessions.push(await manualSession(page));
    report.sessions.push(await arrivalSession(page, fixture));
    const runs = report.sessions.map(session => session.speech.find(event => event.phase === "run")?.run_id);
    assert.ok(runs.every(Boolean)); assert.equal(new Set(runs).size, 2);
    report.layouts = await checkLayouts(page, screenshot);
    report.screenshot = screenshot;
    report.lifecycle_fixtures = [];
    for (const endMode of ["stop", "hidden"]) {
      const evidence = {};
      report.lifecycle_fixtures.push(evidence);
      await deferredReadSession(page, endMode, evidence);
    }
    report.lifecycle_fixtures.push(await hiddenNativeSession(page));
    const cancellationEvidence = {};
    report.lifecycle_fixtures.push(cancellationEvidence);
    await cancellationFailureSession(page, cancellationEvidence);
    assert.equal(report.external_requests.length, 0, "External resource request violates local-only entry");
    assert.equal(report.forbidden_requests.length, 0, "Unrelated inference/map/video/core UI module requested");
    assert.equal(report.websockets.length, 0, "Offline navigation must not open WebSockets");
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

/* Functionality: Reject an evidence destination that already exists.
 * Inputs: Absolute filename. Outputs: Resolved absence or explicit error.
 * Logic: Only ENOENT is accepted. Constraints: Final writes also use exclusive creation. */
async function requireNew(path) {
  try { await access(path); throw new Error("Choose new report and screenshot paths"); }
  catch (error) { if (error.code !== "ENOENT") throw error; }
}

/* Functionality: Validate endpoint/evidence inputs and run one complete native-browser check.
 * Inputs: CLI loopback navigation URL/new JSON path and PRS_PLAYWRIGHT_PATH.
 * Outputs: Fresh JSON/screenshot, concise summary and truthful process status.
 * Logic: Reject credentials/queries/unsafe path and existing evidence before launching Chrome.
 * Constraints: No download, service start, clock/source-data mutation, overwrite or retry. */
async function main() {
  const [url, output, ...extra] = process.argv.slice(2), runtime = process.env.PRS_PLAYWRIGHT_PATH;
  if (!url || !output || !runtime || extra.length) throw new Error("Use URL NEW_REPORT.json; set PRS_PLAYWRIGHT_PATH");
  const address = new URL(url);
  if (address.protocol !== "http:" || !["127.0.0.1", "localhost"].includes(address.hostname) ||
      address.pathname !== "/integration/navigation/" || address.username || address.password || address.search || address.hash)
    throw new Error("Use the loopback /integration/navigation/ entry");
  const reportPath = resolve(output);
  if (!reportPath.toLowerCase().endsWith(".json")) throw new Error("Report path must end with .json");
  const screenshot = reportPath.slice(0, -5) + ".png";
  await requireNew(reportPath); await requireNew(screenshot);
  await mkdir(dirname(reportPath), {recursive: true});
  const report = await run(url, runtime, screenshot);
  await writeFile(reportPath, JSON.stringify(report, null, 2) + "\n", {flag: "wx"});
  process.stdout.write(JSON.stringify({complete: report.complete,
    sessions: report.sessions.map(session => ({end_mode: session.end_mode, ...session.speech_summary})),
    failures: report.failures}) + "\n");
  if (!report.complete) process.exitCode = 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) await main();
