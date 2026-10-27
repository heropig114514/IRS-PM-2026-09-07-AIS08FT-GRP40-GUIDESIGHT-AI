/* Responsibilities: Verify backend bilingual speech playback and the formal/diagnostic page contract.
 * Implementation: Drive SpeechOutput with deterministic WebSocket/Web Audio fixtures and inspect
 * the server-composed page templates; no network, provider, browser voice inventory, or real audio.
 * Related Modules: ../speech-output.js, ../integration/assist-product.html, ../maps.html,
 * ../integration/assist-panel.html, SystemCode/backend/core/agent/tts.py.
 * Declaration Index: Control; Control.constructor; FakeBufferSource; FakeBufferSource.start;
 * FakeBufferSource.stop; FakeBufferSource.disconnect; FakeGain; FakeGain.connect; FakeGain.disconnect;
 * FakeAudioContext; FakeAudioContext.resume; FakeAudioContext.createBuffer;
 * FakeAudioContext.createGain; FakeAudioContext.createBufferSource; FakeWebSocket;
 * FakeWebSocket.constructor; FakeWebSocket.open; FakeWebSocket.send; FakeWebSocket.message;
 * FakeWebSocket.close; AssistDocument; AssistDocument.constructor; AssistDocument.getElementById;
 * AssistDocument.querySelectorAll; AssistDocument.createElement; backendFixture; enable;
 * flushMicrotasks; pcmMessage; testPageContracts; testStreamingBilingualPlayback; testStopAndStaleFrames;
 * testBackendErrors.
 * Variable Index: FakeWebSocket.instances and FakeAudioContext.instances — isolated transport/audio evidence;
 * PCM_RATE — protocol fixture sample rate; all other mutable state belongs to one test fixture.
 * Constraints: Fixtures verify application contracts and scheduled audio data, not provider availability,
 * speech quality, browser activation enforcement, or physical speaker audibility.
 */
import assert from "node:assert/strict";
import test from "node:test";
import {readFile} from "node:fs/promises";
import {SpeechOutput} from "../speech-output.js";

const PCM_RATE = 24000;

/** Functionality: Model one mutable DOM control. Inputs: Optional initial value.
 * Outputs: event-capable node. Logic: retain only properties SpeechOutput reads/writes.
 * Constraints: no HTML parsing, layout, or real trusted user gesture. */
class Control extends EventTarget {
  constructor(value = "") {
    super();
    Object.assign(this, {value, checked: false, disabled: false, textContent: "", dataset: {}, children: []});
  }
  replaceChildren(...children) { this.children = children; }
  append(child) { this.children.push(child); }
}

/** Functionality: Represent one Web Audio source with controlled completion.
 * Inputs: none. Outputs: start/stop/ended state for one scheduled PCM block.
 * Logic: tests explicitly dispatch ended; Constraints: no audio device access. */
class FakeBufferSource extends EventTarget {
  constructor() { super(); this.stops = 0; this.connections = []; }
  connect(target) { this.connections.push(target); }
  start(when) { this.when = when; }
  stop() { this.stops++; }
  disconnect() { this.disconnected = true; }
  finish() { this.dispatchEvent(new Event("ended")); }
}

/** Functionality: Record gain routing and volume. Inputs: none. Outputs: audio graph terminal.
 * Logic: only connect/disconnect identity is modeled; Constraints: no signal amplification occurs. */
class FakeGain {
  constructor() { this.gain = {value: 1}; this.connections = []; }
  connect(target) { this.connections.push(target); }
  disconnect() { this.disconnected = true; }
}

/** Functionality: Provide the Web Audio methods required by the PCM player.
 * Inputs: none. Outputs: sample buffers, source nodes and gain nodes.
 * Logic: resume is synchronous in state and promise-shaped; Constraints: no rendered audio. */
class FakeAudioContext {
  static instances = [];
  constructor() {
    this.state = "suspended"; this.currentTime = 1; this.destination = {};
    this.sources = []; this.gains = []; FakeAudioContext.instances.push(this);
  }
  resume() { this.state = "running"; return Promise.resolve(); }
  createBuffer(channels, length, sampleRate) {
    assert.equal(channels, 1);
    return {duration: length / sampleRate, sampleRate, channel: new Float32Array(length),
      getChannelData(index) { assert.equal(index, 0); return this.channel; }};
  }
  createGain() { const gain = new FakeGain(); this.gains.push(gain); return gain; }
  createBufferSource() { const source = new FakeBufferSource(); this.sources.push(source); return source; }
}

/** Functionality: Capture one same-origin TTS WebSocket and provide controlled server frames.
 * Inputs: resolved WebSocket URL. Outputs: send/close records and dispatched protocol events.
 * Logic: event identity follows EventTarget; Constraints: no socket or server communication. */
class FakeWebSocket extends EventTarget {
  static OPEN = 1;
  static instances = [];
  constructor(url) {
    super(); this.url = String(url); this.readyState = 0; this.binaryType = "";
    this.sent = []; this.closeCalls = []; FakeWebSocket.instances.push(this);
  }
  open() { this.readyState = FakeWebSocket.OPEN; this.dispatchEvent(new Event("open")); }
  send(data) { this.sent.push(data); }
  message(data) { this.dispatchEvent(Object.assign(new Event("message"), {data})); }
  close(code, reason) {
    this.closeCalls.push([code, reason]); this.readyState = 3;
    this.dispatchEvent(new Event("close"));
  }
}

/** Functionality: Provide controller nodes and explicit assist page identity.
 * Inputs: product/test view. Outputs: page-shaped document fixture.
 * Logic: use the same datasets and selected Cherry voice on both entries; Constraints: no rendered HTML. */
class AssistDocument extends EventTarget {
  constructor(view) {
    super(); this.visibilityState = "visible";
    this.documentElement = {lang: "zh-CN", dataset: {assist: "true", assistView: view, apiBase: "../"}};
    const ids = ["enabled", "text", "language", "voice", "rate", "volume", "play", "stop",
      "status", "message", "request", "rate-value", "volume-value"];
    this.nodes = new Map(ids.map(id => ["speech-" + id, new Control()]));
    this.getElementById("speech-voice").value = "Cherry";
    this.getElementById("speech-rate").value = "2";
    this.getElementById("speech-volume").value = "1";
    this.getElementById("speech-language").value = "auto";
  }
  getElementById(id) { return this.nodes.get(id); }
  querySelectorAll(selector) { assert.equal(selector, "[data-speech-sample]"); return []; }
  createElement(tag) { assert.equal(tag, "option"); return new Control(); }
}

/** Functionality: Construct a product or diagnostic controller while making native TTS access fail.
 * Inputs: exact page view. Outputs: controller and per-test dependencies.
 * Logic: getters catch accidental SpeechSynthesis usage; Constraints: browser gates are separately tested. */
function backendFixture(view) {
  FakeWebSocket.instances.length = 0; FakeAudioContext.instances.length = 0;
  const doc = new AssistDocument(view), win = new EventTarget(), logs = [];
  Object.assign(win, {navigator: {userActivation: {hasBeenActive: true}}, location: {href: "https://47.239.50.129/mvp/maps/"},
    crypto: {randomUUID: () => "12345678-1234-4234-9234-123456789abc"}, AudioContext: FakeAudioContext,
    WebSocket: FakeWebSocket, console: {info: (...args) => logs.push(args), error: (...args) => logs.push(args)}});
  Object.defineProperty(win, "speechSynthesis", {get() { throw new Error("native speech must not be accessed"); }});
  Object.defineProperty(win, "SpeechSynthesisUtterance", {get() { throw new Error("native speech must not be accessed"); }});
  return {doc, win, logs, output: new SpeechOutput(doc, win)};
}

/** Functionality: Enable speech through the page's explicit toggle. Inputs: controller.
 * Outputs: handled change event. Logic: invoke the real registered listener; Constraints: synthetic activation only. */
function enable(output) {
  output.enabled.checked = true; output.enabled.dispatchEvent(new Event("change"));
}

/** Functionality: Drain async audio-resume and WebSocket-open continuations.
 * Inputs: current event loop. Outputs: resolved after queued microtasks and one timer turn.
 * Logic: wait once without controlling controller state; Constraints: test scheduling only. */
function flushMicrotasks() { return new Promise(resolve => setTimeout(resolve, 0)); }

/** Functionality: Encode a fixed mono PCM16 test packet. Inputs: signed fixture samples.
 * Outputs: ArrayBuffer matching the server binary message; Logic: little endian.
 * Constraints: fixture amplitudes are not speech and are not played. */
function pcmMessage(...samples) {
  const buffer = new ArrayBuffer(samples.length * 2), view = new DataView(buffer);
  samples.forEach((sample, index) => view.setInt16(index * 2, sample, true));
  return buffer;
}

/** Functionality: Ensure both assembled views expose the same backend speech contract.
 * Inputs: source page/panel markup. Outputs: no legacy local voice dependency in either assist view.
 * Logic: inspect actual source fragments combined by the Django renderer; Constraints: no HTTP server. */
test("formal and diagnostic pages select backend bilingual speech", async function testPageContracts() {
  const product = await readFile(new URL("../integration/assist-product.html", import.meta.url), "utf8");
  const map = await readFile(new URL("../maps.html", import.meta.url), "utf8");
  const panel = await readFile(new URL("../integration/assist-panel.html", import.meta.url), "utf8");
  const renderer = await readFile(new URL("../../backend/integration/assist.py", import.meta.url), "utf8");
  assert.match(product, /Backend bilingual TTS/);
  assert.match(product, /id="speech-language"[^>]*>[\s\S]*?value="auto"/);
  assert.match(map, /id="speech-voice"[^>]*>[\s\S]*?value="Cherry"/);
  assert.match(map, /id="speech-language"[^>]*>[\s\S]*?value="auto"/);
  assert.match(panel, /语音通过后端中英文合成服务生成音频，前端负责流式播放/);
  assert.match(renderer, /data-assist-view="\{view\}" data-api-base="\.\.\/"/);
  for (const view of ["product", "test"]) {
    const {output} = backendFixture(view);
    assert.equal(output.backendOnly, true, `${view} uses backend TTS mode`);
    assert.equal(output.engine, null, `${view} never binds native SpeechSynthesis`);
    assert.deepEqual(output.voiceList().voices.map(voice => voice.id), ["Cherry"]);
  }
});

/** Functionality: Verify request mapping, streamed PCM scheduling, and completion on both assist views.
 * Inputs: Chinese/English utterances and one binary PCM chunk. Outputs: exact WebSocket payload and audio-buffer samples.
 * Logic: require completion metadata plus source-ended event before reporting complete; Constraints: fixture audio only. */
test("Chinese and English backend audio streams play on formal and diagnostic pages", async function testStreamingBilingualPlayback() {
  for (const view of ["product", "test"]) {
    const {output, logs} = backendFixture(view); enable(output);
    const language = view === "product" ? "auto" : "en-US";
    const text = view === "product" ? "Turn left at the next street." : "前方路口左转。";
    const accepted = output.speak(text, {language, voice_id: "Cherry", rate: 1.5, volume: 0.4});
    assert.equal(accepted.accepted, true);
    assert.equal(accepted.speech_service, "backend");
    const socket = FakeWebSocket.instances[0];
    assert.equal(socket.url, "wss://47.239.50.129/mvp/ws/tts/");
    socket.open();
    await flushMicrotasks();
    const command = JSON.parse(socket.sent[0]);
    assert.deepEqual(command, {type: "synthesize", request_id: "12345678-1234-4234-9234-123456789abc",
      text, language, rate: 1.5});
    socket.message(JSON.stringify({type: "audio_config", request_id: command.request_id,
      sample_rate: PCM_RATE, format: "pcm_s16le_mono"}));
    socket.message(pcmMessage(-32768, 16384));
    const context = FakeAudioContext.instances[0], source = context.sources[0];
    assert.equal(output.describe().state, "speaking");
    assert.deepEqual([...source.buffer.channel], [-1, 0.5]);
    assert.equal(context.gains[0].gain.value, 0.4);
    socket.message(JSON.stringify({type: "complete", request_id: command.request_id, audio_bytes: 4}));
    assert.equal(output.describe().state, "speaking", "scheduled PCM must finish before end notification");
    source.finish();
    assert.equal(output.describe().state, "completed");
    assert.equal(output.describe().last_request.language, language);
    assert.equal(output.describe().last_request.voice_id, "Cherry");
    assert.doesNotMatch(JSON.stringify(logs), new RegExp(text));
  }
});

/** Functionality: Verify stop closes only the current stream and stale server data cannot restart playback.
 * Inputs: active socket and scheduled PCM source. Outputs: stopped state, exact close, and unchanged request count.
 * Logic: dispatch late binary/config messages after ownership is cleared; Constraints: no actual server cancellation. */
test("stopping backend speech cancels sources and ignores stale frames", async function testStopAndStaleFrames() {
  const {output} = backendFixture("test"); enable(output);
  output.speak("停止测试", {language: "zh-CN", voice_id: "Cherry"});
  const socket = FakeWebSocket.instances[0]; socket.open();
  await flushMicrotasks();
  const requestId = JSON.parse(socket.sent[0]).request_id;
  socket.message(JSON.stringify({type: "audio_config", request_id: requestId, sample_rate: PCM_RATE,
    format: "pcm_s16le_mono"}));
  socket.message(pcmMessage(123));
  const source = FakeAudioContext.instances[0].sources[0];
  assert.equal(output.stop().accepted, true);
  assert.equal(output.describe().state, "stopped");
  assert.deepEqual(socket.closeCalls, [[1000, "user_stop"]]);
  assert.equal(source.stops, 1);
  socket.message(pcmMessage(456)); source.finish();
  assert.equal(output.describe().state, "stopped");
  assert.equal(FakeAudioContext.instances[0].sources.length, 1);
});

/** Functionality: Map backend errors to a safe terminal state without disclosing spoken text.
 * Inputs: provider-independent error code and private fixture text. Outputs: released request and sanitized diagnostics.
 * Logic: only the enumerated error code reaches UI/log metadata; Constraints: no real provider failure exercised. */
test("backend synthesis failures are visible and do not log utterance text", async function testBackendErrors() {
  const {output, logs} = backendFixture("product"); enable(output);
  const text = "private bilingual test text";
  output.speak(text, {language: "auto", voice_id: "Cherry"});
  const socket = FakeWebSocket.instances[0]; socket.open();
  await flushMicrotasks();
  socket.message(JSON.stringify({type: "error", request_id: JSON.parse(socket.sent[0]).request_id,
    code: "tts_upstream_unavailable", detail: text}));
  assert.equal(output.describe().state, "error");
  assert.equal(output.describe().last_error, "tts_upstream_unavailable");
  assert.equal(output.describe().active_request, false);
  assert.doesNotMatch(JSON.stringify(logs), /private bilingual test text/);
  assert.match(output.message.textContent, /tts upstream unavailable/);
});

/** Functionality: Keep synchronous request-construction failures distinct from audio-context failures.
 * Inputs: UUID and WebSocket send exceptions. Outputs: transport_failed terminal state.
 * Logic: inject each exception after the real audio-resume promise resolves; Constraints: no provider connection. */
test("backend command construction and send failures are classified as transport errors", async function testTransportSetupErrors() {
  for (const failure of ["uuid", "send"]) {
    const {output, win} = backendFixture("test"); enable(output);
    if (failure === "uuid") win.crypto.randomUUID = () => { throw new Error("fixture UUID failure"); };
    output.speak("private transport sample", {language: "en-US", voice_id: "Cherry"});
    const socket = FakeWebSocket.instances[0];
    if (failure === "send") socket.send = () => { throw new Error("fixture send failure"); };
    socket.open(); await flushMicrotasks();
    assert.equal(output.describe().state, "error", `${failure} fails visibly`);
    assert.equal(output.describe().last_error, "transport_failed", `${failure} is not an audio-context failure`);
    assert.equal(output.describe().active_request, false);
  }
});
