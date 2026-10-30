/** Responsibilities: Verify shared speech API inputs, lifecycle, voice selection, and MCP dispatch boundaries.
 * Implementation: Drive native events/device APIs with EventTarget fixtures; also verify reentrant event snapshots and observer isolation; play no real audio and make no network requests.
 * Related Modules: speech-output.js, ai-control.js, index.html, translations.js, i18n.js.
 * Declaration Index: Control; Control.constructor; Control.replaceChildren; Control.append;
 * SpeechDocument; SpeechDocument.constructor; SpeechDocument.getElementById;
 * SpeechDocument.querySelectorAll; SpeechDocument.createElement; Utterance; Utterance.constructor;
 * Engine; Engine.constructor; Engine.getVoices; Engine.speak; Engine.cancel; Engine.emit;
 * fixture; enable; testPrerequisites; testLifecycle; testLimits; testVoices;
 * testVoiceQuery; testCancellation; testNativeErrors; testVisibility; testMcp; testTranslations;
 * testGuidanceEvents; testLocalVoiceOnly; testReentrantBroadcast.
 * Variable Index: VOICES — explicit local/remote test identities; other variables store only this test's controls, requests, and diagnostics.
 * Constraints: Fixtures do not prove real MCP networking, TTS engine behavior, or speaker output; browser regression is separate.
 */
import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {SpeechOutput, MAX_TEXT_LENGTH} from "../speech-output.js";
import {performSpeech} from "../ai-control.js";
import {translate} from "../i18n.js";

const VOICES = [
  {voiceURI: "local-zh", name: "Fixture Chinese", lang: "zh-CN", localService: true, default: true},
  {voiceURI: "remote-en", name: "Fixture English", lang: "en-US", localService: false, default: false},
];

/** Functionality: Verify real driver's request lifecycle/ownership seam. Inputs: native event fixture.
 * Outputs: submitted/start/end and safe refusal to cancel another request ID.
 * Logic: engine event determines actual start, stale events remain isolated; Constraints: no audible hardware assertion. */
test("output subscriptions expose native lifecycle and preserve request ownership", function testGuidanceEvents() {
  const {engine, output} = fixture(), events = []; enable(output);
  const unsubscribe = output.subscribe(event => events.push(event));
  const first = output.speak("first", {voice_id: "local-zh"});
  assert.equal(events[0].phase, "submitted"); assert.equal(events[0].request_id, first.last_request.id);
  assert.equal(output.stopOwned(999).reason, "not_owned"); assert.equal(engine.cancels, 0);
  engine.emit(engine.requests[0], "start"); engine.emit(engine.requests[0], "end");
  assert.deepEqual(events.map(event => event.phase), ["submitted", "start", "end"]);
  const second = output.speak("second", {voice_id: "local-zh"});
  assert.equal(output.stopOwned(first.last_request.id).reason, "not_owned");
  assert.equal(output.stopOwned(second.last_request.id).accepted, true);
  assert.equal(events.at(-1).phase, "cancel"); unsubscribe();
});
/** Functionality: Verify explicit local-MVP gate without weakening original voice selection.
 * Inputs: local-only controller, default/remote/explicit-local choices. Outputs: no remote/default submission.
 * Logic: invalid local voices never replace themselves; Constraints: synthetic voice inventory. */
test("local-MVP marker rejects remote and browser-default speech", function testLocalVoiceOnly() {
  const {output, engine} = fixture(); output.localOnly = true; enable(output);
  assert.equal(output.speak("sample").reason, "local_voice_required");
  assert.equal(output.speak("sample", {language: "en-US", voice_id: "remote-en"}).reason, "local_voice_required");
  assert.equal(engine.requests.length, 0);
  assert.equal(output.speak("sample", {voice_id: "local-zh"}).accepted, true); output.stop();
});

/** Functionality: Preserve a terminal event when its first observer immediately submits new output.
 * Inputs: Real driver end/cancel, explicit second request and observer-local event mutation.
 * Outputs: Old request identity/time for every terminal observer plus distinct nested submitted event.
 * Logic: Exercise actual driver dispatch and native-fixture completion rather than calling notify directly.
 * Constraints: No native audio, substituted clock or assertions swallowed inside driver observers. */
test("reentrant end and cancel broadcasts retain their original request and timestamp", function testReentrantBroadcast() {
  for (const phase of ["end", "cancel"]) {
    const {engine, output} = fixture(); enable(output);
    let firstId = null, original = null, firstReference = null, nested = null;
    const later = [];
    const unsubscribeFirst = output.subscribe(event => {
      if (event.phase !== phase || event.request_id !== firstId) return;
      original = {...event}; firstReference = event;
      event.request_id = -1; event.phase = "observer_mutation"; event.at = -1;
      event.channel = "observer_mutation"; event.reason = "observer_mutation";
      nested = output.speak("second request", {voice_id: "local-zh"});
    });
    const unsubscribeLater = output.subscribe(event => later.push(event));
    const first = output.speak("first request", {voice_id: "local-zh"}); firstId = first.last_request.id;
    const oldUtterance = engine.requests[0];
    if (phase === "end") engine.emit(oldUtterance, "end");
    else output.stop();
    assert.equal(nested?.accepted, true);
    const terminal = later.filter(event => event.phase === phase);
    assert.equal(terminal.length, 1);
    assert.deepEqual(terminal[0], original);
    assert.notEqual(terminal[0], firstReference);
    assert.equal(terminal[0].request_id, firstId);
    assert.equal(terminal[0].at, original.at);
    assert.ok(later.some(event => event.phase === "submitted" && event.request_id === nested.last_request.id));
    assert.notEqual(nested.last_request.id, firstId);
    assert.equal(output.current, engine.requests[1]);
    unsubscribeFirst(); unsubscribeLater(); output.stop();
  }
});

/** Functionality: Provide a simple control with real event dispatch.
 * Logic: Store values, state, and options without parsing HTML.
 * Constraints: Does not simulate layout or disabled-event interception.
 */
class Control extends EventTarget {
  /** Functionality: Initialize a node. Inputs: Optional initial value.
   * Outputs: Control object. Logic: Start unchecked with an empty child list.
   * Constraints: No browser environment.
   */
  constructor(value = "") {
    super();
    Object.assign(this, {value, checked: false, disabled: false, textContent: "", dataset: {}, children: []});
  }
  /** Functionality: Replace options. Inputs: nodes. Outputs: Updated children.
   * Logic: Replace the list directly. Constraints: Do not implicitly replace value, so the controller's selection preservation is explicit.
   */
  replaceChildren(...nodes) { this.children = nodes; }
  /** Functionality: Add an option. Inputs: node. Outputs: Appended child.
   * Logic: Store the reference. Constraints: Does not implement general DOM rendering.
   */
  append(node) { this.children.push(node); }
}

/** Functionality: Provide fixed speech UI nodes and visibility state.
 * Logic: EventTarget dispatches lifecycle events. Constraints: No page, permissions, text persistence, or network.
 */
class SpeechDocument extends EventTarget {
  /** Functionality: Create nodes required by the complete controller.
   * Inputs: None. Outputs: Node map and visible state.
   * Logic: Initialize rate/volume to 1 and language to zh-CN.
   * Constraints: Does not read real HTML; actual page tests cover that separately.
   */
  constructor() {
    super();
    this.visibilityState = "visible";
    const ids = ["enabled", "text", "language", "voice", "rate", "volume", "play", "stop",
      "status", "message", "request", "rate-value", "volume-value"];
    this.nodes = new Map(ids.map(id => ["speech-" + id, new Control()]));
    this.getElementById("speech-rate").value = "1";
    this.getElementById("speech-volume").value = "1";
    this.getElementById("speech-language").value = "zh-CN";
  }
  /** Functionality: Find a fixed node. Inputs: id. Outputs: Control or undefined.
   * Logic: Read from the map. Constraints: Do not create missing nodes.
   */
  getElementById(id) { return this.nodes.get(id); }
  /** Functionality: Provide the sample-button collection. Inputs: selector.
   * Outputs: Empty collection. Logic: This test group does not simulate sample buttons; real UI regression covers them.
   * Constraints: Accept only the controller's fixed selector.
   */
  querySelectorAll(selector) { assert.equal(selector, "[data-speech-sample]"); return []; }
  /** Functionality: Create a voice-option node. Inputs: tag.
   * Outputs: Control. Logic: Support only option. Constraints: Does not implement other DOM types.
   */
  createElement(tag) { assert.equal(tag, "option"); return new Control(); }
}

/** Functionality: Represent a speech request that can receive start/end/error events.
 * Logic: EventTarget provides real event identity. Constraints: Does not synthesize or play audio.
 */
class Utterance extends EventTarget {
  /** Functionality: Store original text. Inputs: text.
   * Outputs: Test utterance. Logic: Do not rewrite content. Constraints: In-memory test object only.
   */
  constructor(text) { super(); this.text = text; }
}

/** Functionality: Explicitly simulate the speech engine, voice-list loading, and failures.
 * Logic: Record requests only; the test advances events explicitly.
 * Constraints: No real system voice or speaker.
 */
class Engine extends EventTarget {
  /** Functionality: Initialize native state.
   * Inputs: None. Outputs: Fixed voices, request list, counters, and configurable failures.
   * Logic: Do not trigger events automatically. Constraints: Each test uses an independent instance.
   */
  constructor() {
    super();
    Object.assign(this, {voices: [...VOICES], requests: [], cancels: 0, speaking: false,
      pending: false, paused: false, failSpeak: false, failCancel: false, failVoices: false});
  }
  /** Functionality: Return the current voice list or fail explicitly.
   * Inputs: voices/failVoices. Outputs: List or Error.
   * Logic: Tests can change the list and dispatch voiceschanged. Constraints: Does not call operating-system services.
   */
  getVoices() { if (this.failVoices) throw new Error("Fixture voice lookup failure"); return this.voices; }
  /** Functionality: Accept one speech request.
   * Inputs: utterance. Outputs: void or Error; increments request count.
   * Logic: Simulate queueing without automatically starting. Constraints: API return does not mean playback occurred.
   */
  speak(utterance) {
    this.requests.push(utterance);
    if (this.failSpeak) throw new Error("Fixture native failure");
    this.pending = true;
  }
  /** Functionality: Accept one cancellation request.
   * Inputs: Engine state/failCancel. Outputs: void or Error.
   * Logic: On success, simulate a synchronous canceled event to check controller isolation.
   * Constraints: Does not prove real browser cancellation timing or actual audio stop.
   */
  cancel() {
    this.cancels++;
    if (this.failCancel) throw new Error("Fixture cancellation failure");
    this.pending = this.speaking = false;
    if (this.requests.length) this.emit(this.requests.at(-1), "error", "canceled");
  }
  /** Functionality: Advance a standard event for a current or stale request.
   * Inputs: utterance/type and optional error. Outputs: Event dispatch and engine-state update.
   * Logic: Preserve the actual event.target object identity. Constraints: Explicitly controlled timing does not represent real audio.
   */
  emit(utterance, type, error) {
    if (type === "start") { this.pending = false; this.speaking = true; }
    if (type === "end" || type === "error") { this.pending = false; this.speaking = false; }
    utterance.dispatchEvent(Object.assign(new Event(type), {error}));
  }
}

/** Functionality: Create an isolated instance of the real controller.
 * Inputs: supported determines whether native APIs exist. Outputs: doc/win/engine/output/logs.
 * Logic: Simulate device dependencies only; controller and MCP dispatch use real source code.
 * Constraints: Console receivers record test data only and do not verify a real logging service.
 */
function fixture(supported = true) {
  const doc = new SpeechDocument(), win = new EventTarget(), engine = new Engine(), logs = [];
  Object.assign(win, {navigator: {userActivation: {hasBeenActive: true}},
    speechSynthesis: supported ? engine : undefined, SpeechSynthesisUtterance: supported ? Utterance : undefined,
    console: {info: (...args) => logs.push(args), error: (...args) => logs.push(args)}});
  return {doc, win, engine, logs, output: new SpeechOutput(doc, win)};
}

/** Functionality: Dispatch an explicit enable choice for the test.
 * Inputs: output. Outputs: Toggle change event. Logic: Do not call speak directly.
 * Constraints: Trusted user activation is controlled explicitly by test state; do not impersonate operating-system interaction.
 */
function enable(output) {
  output.enabled.checked = true;
  output.enabled.dispatchEvent(new Event("change"));
}

/** Functionality: Verify default-off state, foreground/activation gates, and unsupported result.
 * Inputs: Independent device fixtures and explicit visibility/activation. Outputs: Zero native calls and exact reasons.
 * Logic: Querying and enabling do not speak; do not submit before prerequisites are met.
 * Constraints: Real browser activation and MCP transport have separate regression coverage.
 */
test("Default-off, hidden, and inactive requests do not submit speech", function testPrerequisites() {
  const {doc, win, engine, output} = fixture();
  assert.equal(output.describe().enabled, false);
  assert.equal(output.speak("text").reason, "disabled");
  enable(output);
  win.navigator.userActivation.hasBeenActive = false;
  assert.equal(output.speak("text").reason, "activation_required");
  win.navigator.userActivation.hasBeenActive = true;
  doc.visibilityState = "hidden";
  assert.equal(output.speak("text").reason, "page_hidden");
  assert.deepEqual(engine.requests, []);
  const unavailable = fixture(false);
  assert.equal(unavailable.output.speak("text").reason, "unsupported");
  assert.equal(unavailable.output.stop().reason, "unsupported");
});

/** Functionality: Verify submitted, started, and ended are distinct states and busy output does not queue another request.
 * Inputs: Explicit text/options, controlled native events, and paused state.
 * Outputs: Request, phase, control, and privacy-log assertions.
 * Logic: Exercise the real speak method and event handlers. Constraints: completed does not prove speaker output.
 */
test("Submission differs from start/completion and does not queue automatically", function testLifecycle() {
  const {engine, output, logs} = fixture();
  enable(output);
  const submitted = output.speak(" private fixture text ", {rate: 1.3, volume: 0.4});
  assert.equal(submitted.accepted, true);
  assert.equal(submitted.state, "queued");
  assert.equal(submitted.audio_output_verified, false);
  const first = engine.requests[0];
  assert.equal(first.text, " private fixture text ");
  assert.equal(first.rate, 1.3);
  assert.equal(first.volume, 0.4);
  assert.equal(output.speak("second").reason, "busy");
  assert.equal(engine.requests.length, 1);
  engine.emit(first, "start");
  assert.equal(output.describe().state, "speaking");
  engine.emit(first, "end");
  assert.equal(output.describe().state, "completed");
  assert.equal(output.describe().active_request, false);
  assert.equal(output.playButton.disabled, false);
  engine.paused = true;
  assert.equal(output.speak("paused engine").reason, "busy");
  assert.equal(output.describe().engine_paused, true);
  assert.equal(engine.requests.length, 1);
  assert.doesNotMatch(JSON.stringify(logs), /private fixture text/);
  assert.doesNotMatch(JSON.stringify(output.describe()), /private fixture text/);
});

/** Functionality: Verify Unicode code-point limits and reject whitespace, non-finite, or out-of-range options before device access.
 * Inputs: Invalid text/options and the 1,000 astral-character boundary. Outputs: Explicit rejection and preserved original text.
 * Logic: Use established product boundaries without changing thresholds to force a pass.
 * Constraints: Does not verify engine synthesis for long text or a particular language.
 */
test("Text and numeric boundaries reject explicitly with consistent Unicode counting", function testLimits() {
  const {engine, output} = fixture();
  enable(output);
  for (const text of ["", "  ", 123, "x".repeat(MAX_TEXT_LENGTH + 1)])
    assert.equal(output.speak(text).reason, "invalid_text");
  for (const options of [{rate: NaN}, {rate: Infinity}, {rate: 2.1}, {volume: -0.1},
    {volume: 1.1}, {rate: "1"}, {voice_id: 3}, {language: "unknown"}])
    assert.equal(output.speak("text", options).reason, "invalid_parameters");
  assert.equal(engine.requests.length, 0);
  const result = output.speak("😀".repeat(MAX_TEXT_LENGTH), {volume: 0});
  assert.equal(result.accepted, true);
  assert.equal(result.last_request.text_length, MAX_TEXT_LENGTH);
  assert.equal(engine.requests[0].volume, 0);
});

/** Functionality: Verify voice identity, asynchronous list updates, missing options, and language matching.
 * Inputs: Fixed local/remote voices and explicit voice_id. Outputs: Exact voice object or explicit rejection.
 * Logic: Do not switch a missing voice to the default; preserve an invalid selection when the list changes.
 * Constraints: Local/remote are fixture properties and do not prove real offline capability or network behavior.
 */
test("Missing or ambiguous explicit voices do not fall back to the default", function testVoices() {
  const {engine, output} = fixture();
  enable(output);
  const listed = output.voiceList();
  assert.equal(listed.voices[1].local_service, false);
  output.voice.value = "local-zh";
  engine.voices = [];
  engine.dispatchEvent(new Event("voiceschanged"));
  assert.equal(output.voice.value, "local-zh");
  assert.equal(output.voice.children.at(-1).disabled, true);
  assert.equal(output.speak("text", {voice_id: "local-zh"}).reason, "voice_unavailable");
  engine.voices = [VOICES[0], VOICES[0]];
  assert.equal(output.speak("text", {voice_id: "local-zh"}).reason, "voice_ambiguous");
  engine.voices = [...VOICES];
  assert.equal(output.speak("text", {voice_id: "remote-en"}).reason, "voice_language_mismatch");
  const submitted = output.speak("text", {voice_id: "remote-en", language: "en-US"});
  assert.equal(submitted.accepted, true);
  assert.equal(engine.requests.length, 1);
  assert.equal(engine.requests[0].voice, VOICES[1]);
});

/** Functionality: Verify that list-read failure does not present a cache as the current list.
 * Inputs: getVoices failure after an initial list load. Outputs: Structured error, empty result, and zero speak calls.
 * Logic: Reject explicitly when voice selection cannot be verified. Constraints: No automatic retry or default-voice fallback.
 */
test("Voice-list lookup failure remains diagnosable and does not replace the voice", function testVoiceQuery() {
  const {engine, output} = fixture();
  enable(output);
  engine.failVoices = true;
  assert.equal(output.voiceList().reason, "voice_query_failed");
  assert.deepEqual(output.voiceList().voices, []);
  assert.equal(output.speak("text", {voice_id: "local-zh"}).reason, "voice_query_failed");
  assert.equal(engine.requests.length, 0);
});

/** Functionality: Verify cancellation failure preserves the request and successful cancellation isolates stale events from new requests.
 * Inputs: Controlled cancel exception/synchronous cancellation event/stale event. Outputs: Cancellation count and exact phases.
 * Logic: Do not retry cancellation; after an explicit later stop, stale callbacks cannot affect a new request.
 * Constraints: Does not prove audio actually stopped; checks application state and call boundaries only.
 */
test("Cancellation failure preserves state and stale events cannot end a new request", function testCancellation() {
  const {engine, output} = fixture();
  enable(output);
  output.speak("first");
  const first = engine.requests[0];
  engine.failCancel = true;
  assert.equal(output.stop().reason, "cancel_failed");
  assert.equal(output.describe().active_request, true);
  assert.equal(engine.cancels, 1);
  engine.failCancel = false;
  assert.equal(output.stop().accepted, true);
  assert.equal(output.describe().state, "stopped");
  output.speak("second");
  const second = engine.requests[1];
  engine.emit(first, "end");
  assert.equal(output.describe().state, "queued");
  assert.equal(output.current, second);
  engine.emit(second, "start");
  assert.equal(output.describe().state, "speaking");
});

/** Functionality: Verify synchronous speak exceptions and asynchronous errors fail explicitly without retrying or echoing external messages.
 * Inputs: Synthetic native exception/event error. Outputs: Single request, released resources, and safe diagnostics.
 * Logic: The controller records request identity first; errors log only standard codes, not utterance content.
 * Constraints: Does not verify a real speech service network or speaker failure.
 */
test("Synchronous and asynchronous speech errors do not retry silently or leak text", function testNativeErrors() {
  const {engine, output, logs} = fixture();
  enable(output);
  engine.failSpeak = true;
  assert.equal(output.speak("private text").reason, "native_error");
  assert.equal(output.describe().last_request.id, 1);
  assert.equal(output.describe().active_request, false);
  engine.failSpeak = false;
  output.speak("other private text");
  engine.emit(engine.requests[1], "error", "not-allowed");
  assert.equal(output.describe().last_error, "not-allowed");
  assert.equal(output.describe().state, "error");
  output.speak("third private text");
  engine.emit(engine.requests[2], "error", "private arbitrary external diagnostic");
  assert.equal(output.describe().last_error, "unknown");
  assert.equal(engine.requests.length, 3);
  assert.doesNotMatch(JSON.stringify(logs), /private/);
});

/** Functionality: Verify cancellation on disable/hide/page exit and no speech resume on foreground return.
 * Inputs: Three independent user/lifecycle events. Outputs: One cancel per event and no new speak.
 * Logic: Use the real registered callbacks. Constraints: Not a background-audio or phone-lock test.
 */
test("Disable, hide, or page exit stops speech without automatic foreground resume", function testVisibility() {
  for (const kind of ["disabled", "hidden", "pagehide"]) {
    const {doc, win, engine, output} = fixture();
    enable(output);
    output.speak("text");
    if (kind === "disabled") {
      output.enabled.checked = false;
      output.enabled.dispatchEvent(new Event("change"));
    } else if (kind === "hidden") {
      doc.visibilityState = "hidden";
      doc.dispatchEvent(new Event("visibilitychange"));
    } else win.dispatchEvent(new Event("pagehide"));
    doc.visibilityState = "visible";
    doc.dispatchEvent(new Event("visibilitychange"));
    win.dispatchEvent(new Event("pagehide"));
    assert.equal(engine.cancels, 1);
    assert.equal(engine.requests.length, 1);
    assert.equal(output.describe().state, "stopped");
  }
});

/** Functionality: Verify MCP and manual interfaces share one native call and explicit state.
 * Inputs: Actual performSpeech and controller; device dependencies are fixtures.
 * Outputs: Status/list queries do not play, default-off is not bypassed, parameters are passed exactly, and stop is shared.
 * Logic: Delegate to the common interface instead of using DOM buttons to simulate completion.
 * Constraints: Real HTTP/WS/MCP transport has separate browser regression coverage.
 */
test("MCP speech does not auto-enable and shares the UI execution path", function testMcp() {
  assert.equal(performSpeech("speech_status", {}, null).reason, "page_unavailable");
  const {engine, output} = fixture();
  assert.equal(performSpeech("speech_status", {}, output).state, "idle");
  assert.equal(performSpeech("speech_voices", {}, output).voices.length, 2);
  assert.equal(performSpeech("speech_speak", {text: "text"}, output).reason, "disabled");
  enable(output);
  assert.equal(performSpeech("speech_speak", {text: "text", rate: null}, output).reason, "invalid_parameters");
  const submitted = performSpeech("speech_speak", {text: "text", language: "en-US",
    voice_id: "remote-en", rate: 1.5, volume: 0.5}, output);
  assert.equal(submitted.accepted, true);
  assert.equal(engine.requests[0].rate, 1.5);
  assert.equal(engine.requests[0].voice, VOICES[1]);
  assert.equal(performSpeech("speech_stop", {}, output).state, "stopped");
  assert.equal(engine.requests.length, 1);
  assert.throws(() => performSpeech("speech_loop", {}, output), /Unknown speech command/);
});

/** Functionality: Verify bilingual dictionary coverage for static UI and dynamic feedback.
 * Inputs: Actual module/HTML source; test utterances remain unchanged as user content.
 * Outputs: All UI strings are translatable; dynamic request summaries and native error codes retain their parameters.
 * Logic: Exclude explicit sample utterances; browser checks protect the text box and native voice names.
 * Constraints: Does not replace real-DOM translation, layout, or audio verification.
 */
test("Speech UI and dynamic state are translated while utterance text stays outside the UI dictionary", async function testTranslations() {
  const source = await readFile(new URL("../speech-output.js", import.meta.url), "utf8");
  const html = await readFile(new URL("../index.html", import.meta.url), "utf8");
  const section = html.match(/<section id="speech-test"[\s\S]*?<\/section>/)[0];
  const protectedText = "这是 PRS 语音输出测试。";
  const strings = [...source.matchAll(/"([^"\n]*\p{Script=Han}[^"\n]*)"/gu),
    ...section.replace(/<textarea[\s\S]*?<\/textarea>/g, "").matchAll(/>([^<>]*\p{Script=Han}[^<>]*)</gu),
    ...section.matchAll(/aria-label="([^"]+)"/g)].map(match => match[1].trim());
  for (const text of strings) if (text !== protectedText)
    assert.doesNotMatch(translate(text, "en"), /\p{Script=Han}/u, text);
  assert.equal(translate("请求 1 · 4 个字符 · en-US · 语速 1.5× · 音量 50%", "en"),
    "Request 1 · 4 characters · en-US · Rate 1.5× · Volume 50%");
});
