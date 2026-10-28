/* Responsibilities: Verify backend bilingual speech playback and the formal/diagnostic page contract.
 * Implementation: Drive SpeechOutput with deterministic WebSocket/Web Audio fixtures and inspect
 * the server-composed page templates; risk-only cache selection retains the original playback guards.
 * Prepared-resource cases read real WAV files into a synthetic audio graph; no network, provider,
 * browser voice inventory, or physical audio output is used.
 * Related Modules: ../speech-output.js, ../integration/assist-guidance.js, ../integration/risk-speech-cache.js,
 * ../integration/assist-product.html, ../maps.html,
 * ../integration/assist-panel.html, SystemCode/backend/core/agent/tts.py.
 * Declaration Index: Control; Control.constructor; FakeBufferSource; FakeBufferSource.start;
 * FakeBufferSource.stop; FakeBufferSource.disconnect; FakeGain; FakeGain.connect; FakeGain.disconnect;
 * FakeAudioContext; FakeAudioContext.resume; FakeAudioContext.createBuffer;
 * FakeAudioContext.createGain; FakeAudioContext.createBufferSource; FakeWebSocket;
 * FakeWebSocket.constructor; FakeWebSocket.open; FakeWebSocket.send; FakeWebSocket.message;
 * FakeWebSocket.close; AssistDocument; AssistDocument.constructor; AssistDocument.getElementById;
 * AssistDocument.querySelectorAll; AssistDocument.createElement; backendFixture; enable;
 * flushMicrotasks; pcmMessage; testPageContracts; testStreamingBilingualPlayback; testBackendWatchdog; testStopAndStaleFrames;
 * testBackendErrors; testBeforePlaybackCancellation; testRiskTransportIsolation;
 * testRiskTransportUnavailable; testCachedRiskPlayback; testPreparedRiskPlayback;
 * testPreparedRiskPlayback.fetchResource; testTransportSetupErrors.
 * Variable Index: FakeWebSocket.instances and FakeAudioContext.instances — isolated transport/audio evidence;
 * PCM_RATE — protocol fixture sample rate; per-test timer maps force connection, stream and playback deadlines without wall-clock waits.
 * Constraints: Fixtures verify application contracts and scheduled audio data, not provider availability,
 * speech quality, browser activation enforcement, or physical speaker audibility.
 */
import assert from "node:assert/strict";
import test from "node:test";
import {readFile} from "node:fs/promises";
import {webcrypto} from "node:crypto";
import {SpeechOutput} from "../speech-output.js";
import {AssistGuidanceManager} from "../integration/assist-guidance.js";
import {RiskSpeechCache} from "../integration/risk-speech-cache.js";
import {RiskAudioResources} from "../integration/risk-audio-resources.js";

const PCM_RATE = 24000;

/** Functionality: Reject stale guidance before its first PCM block reaches the audio graph.
 * Inputs: Direct/cached transports and an owner withdrawing its queued request at before_start,
 * with successful or failed socket closure.
 * Outputs: No scheduled source or start event. Logic: Exercise the real pre-playback notification and ownership guard.
 * Constraints: Synthetic transport/audio only; failed cancellation requires an explicit successful cleanup. */
test("withdrawn guidance never schedules its first audio block", async function testBeforePlaybackCancellation() {
  for (const [cached, failedClose] of [[false, false], [false, true], [true, false], [true, true]]) {
    const {output} = backendFixture("product"); enable(output);
    if (cached) output.riskSocketFactory = address => new FakeWebSocket(address);
    const phases = [];
    output.subscribe(event => {
      phases.push(event.phase);
      if (event.phase === "before_start") output.stopOwned(event.request_id);
    });
    output.speak("警告，左前方有行人靠近。", {language: "zh-CN", voice_id: "Cherry",
      ...(cached ? {purpose: "risk"} : {})});
    const socket = FakeWebSocket.instances[0]; socket.open();
    await flushMicrotasks();
    const close = socket.close.bind(socket);
    if (failedClose) socket.close = () => { throw new Error("synthetic close failure"); };
    socket.message(JSON.stringify({type: "audio_config", sample_rate: PCM_RATE, format: "pcm_s16le_mono"}));
    socket.message(pcmMessage(16384));
    assert.equal(FakeAudioContext.instances[0].sources.length, 0);
    assert.equal(phases.filter(phase => phase === "before_start").length, 1);
    assert.equal(phases.includes("start"), false);
    if (failedClose) assert.equal(output.describe().last_error, "cancel_failed");
    socket.close = close;
    output.stop();
  }
});

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

/** Functionality: Route risk speech through the explicit adapter without changing other utterances or protocol.
 * Inputs: Bound cache transport, risk phrase and a later ordinary phrase.
 * Outputs: One cache selection, identical five-field wire command, normal PCM start/completion and a direct manual socket.
 * Logic: Exercise the real speak entry and shared player rather than bypassing the pre-playback event.
 * Constraints: The socket/audio fixtures do not verify a real cache, provider, browser gesture or speaker. */
test("only explicit risk speech selects the cache transport and keeps the original wire protocol", async function testRiskTransportIsolation() {
  const {output} = backendFixture("test"); enable(output);
  const addresses = [], phases = [];
  output.riskSocketFactory = address => { addresses.push(String(address)); return new FakeWebSocket(address); };
  output.subscribe(event => phases.push(event.phase));
  const result = output.speak("危险，右前方有行人。", {language: "zh-CN", voice_id: "Cherry",
    rate: 2, volume: 0.7, purpose: "risk"});
  assert.equal(result.accepted, true);
  assert.equal(result.last_request.audio_transport, "risk_cache");
  assert.deepEqual(addresses, ["wss://47.239.50.129/mvp/ws/tts/"]);
  const socket = FakeWebSocket.instances[0]; socket.open(); await flushMicrotasks();
  const command = JSON.parse(socket.sent[0]);
  assert.deepEqual(command, {type: "synthesize", request_id: "12345678-1234-4234-9234-123456789abc",
    text: "危险，右前方有行人。", language: "zh-CN", rate: 2});
  socket.message(JSON.stringify({type: "audio_config", request_id: command.request_id,
    sample_rate: PCM_RATE, format: "pcm_s16le_mono"}));
  socket.message(pcmMessage(16384));
  assert.equal(output.describe().state, "speaking");
  assert.ok(phases.indexOf("before_start") < phases.indexOf("start"));
  socket.message(JSON.stringify({type: "complete", request_id: command.request_id, audio_bytes: 2}));
  FakeAudioContext.instances[0].sources[0].finish();
  assert.equal(output.describe().state, "completed");
  const ordinary = output.speak("这是独立语音测试。", {language: "zh-CN", voice_id: "Cherry"});
  assert.equal(ordinary.accepted, true);
  assert.equal(ordinary.last_request.audio_transport, "websocket");
  assert.equal(addresses.length, 1, "ordinary speech never consults the risk cache binding");
  assert.equal(FakeWebSocket.instances.length, 2);
  output.stop();
});

/** Functionality: Fail explicit cache selection without silently using a direct or local provider.
 * Inputs: Missing/throwing factory and an invalid purpose marker.
 * Outputs: Explicit rejection or transport setup error, with no native WebSocket fallback.
 * Logic: Test both configuration admission and transport construction boundaries.
 * Constraints: No retries or real external service failures are induced. */
test("unavailable risk transport fails explicitly without falling back", function testRiskTransportUnavailable() {
  const {output} = backendFixture("product"); enable(output);
  const options = {language: "zh-CN", voice_id: "Cherry", purpose: "risk"};
  const missing = output.speak("危险，前方有行人。", options);
  assert.equal(missing.accepted, false);
  assert.equal(missing.reason, "risk_audio_unavailable");
  assert.equal(FakeWebSocket.instances.length, 0);
  assert.equal(FakeAudioContext.instances.length, 0);
  assert.equal(output.speak("危险，前方有行人。", {...options, purpose: "unknown"}).reason, "invalid_parameters");
  output.riskSocketFactory = () => { throw new Error("synthetic cache construction failure"); };
  const failed = output.speak("危险，前方有行人。", options);
  assert.equal(failed.accepted, false);
  assert.equal(failed.reason, "transport_unavailable");
  assert.equal(output.describe().last_error, "transport_unavailable");
  assert.equal(FakeWebSocket.instances.length, 0);
});

/** Functionality: Preserve a prepared risk phrase through cancellation and admit only a new fresh warning to playback.
 * Inputs: Production manager, SpeechOutput and risk cache with synthetic backend PCM, source clocks and audio graph.
 * Outputs: Cold preparation has no stale start; a fresh matching request reuses its bytes without a second synthesis.
 * Logic: Change the grade before first PCM, complete the old preparation, then restore the warning on a fresh frame;
 * verify request identity, immutable source attribution, buffer content and complete playback lifecycle together.
 * Constraints: No real provider, browser permission, GPU or physical audibility is established by these fixtures. */
test("fresh risk speech reuses canceled preparation without playing stale PCM", async function testCachedRiskPlayback() {
  const {output, win} = backendFixture("test"); enable(output);
  let clock = 100, identity = 0;
  win.crypto.randomUUID = () => `12345678-1234-4234-9234-${String(++identity).padStart(12, "0")}`;
  const cache = new RiskSpeechCache({WebSocket: FakeWebSocket, origin: "https://47.239.50.129", now: () => clock});
  output.riskSocketFactory = address => cache.createSocket(address);
  const manager = new AssistGuidanceManager({speech: output, now: () => clock,
    setTimer: () => null, clearTimer: () => {}}), events = [];
  const high = {track_id: 7, label: "person", direction: "front_left", alert_required: true,
    alert_priority: 3, risk_level: "high", motion_state: "stationary", relative_speed_mps: 0,
    risk_reasons: [], distance_m: 2, ttc_sec: 3};
  try {
    manager.subscribe(event => events.push(event));
    manager.configure({voiceId: "Cherry", language: "zh-CN"}); manager.setRiskEnabled(true); manager.beginRisk("cached-risk-test");
    manager.ingestRisk({sessionId: "cached-risk-test", capturedAt: clock,
      metadata: {index: 1, tracking_epoch: 1, boxes: [high]}});
    await flushMicrotasks();
    const upstream = FakeWebSocket.instances[0]; upstream.open(); await flushMicrotasks();
    const originalCommand = JSON.parse(upstream.sent[0]);
    clock = 4100;
    manager.ingestRisk({sessionId: "cached-risk-test", capturedAt: clock,
      metadata: {index: 2, tracking_epoch: 1, boxes: [{...high, alert_priority: 2, risk_level: "medium"}]}});
    upstream.message(JSON.stringify({type: "audio_config", request_id: originalCommand.request_id,
      sample_rate: PCM_RATE, format: "pcm_s16le_mono"}));
    upstream.message(pcmMessage(16384));
    assert.equal(FakeAudioContext.instances[0].sources.length, 0, "incomplete audio is preparation only");
    upstream.message(JSON.stringify({type: "complete", request_id: originalCommand.request_id, audio_bytes: 2}));
    await flushMicrotasks();
    assert.equal(FakeAudioContext.instances[0].sources.length, 0, "grade-mismatched first PCM never starts");
    assert.ok(events.some(event => event.phase === "output_cancel" && event.request_id === 1 &&
      event.reason === "risk_changed_before_start"));
    assert.equal(cache.describe().completed, 1);
    clock = 4200;
    manager.ingestRisk({sessionId: "cached-risk-test", capturedAt: clock,
      metadata: {index: 3, tracking_epoch: 1, boxes: [high]}});
    await flushMicrotasks();
    const current = output.describe();
    assert.equal(current.last_request.id, 3, "restoring a warning creates a distinct current output request");
    assert.equal(current.last_request.audio_transport, "risk_cache");
    assert.equal(current.last_error, null, "cached controls are rebound to the current UUID");
    assert.equal(current.state, "speaking");
    const source = FakeAudioContext.instances[0].sources[0];
    assert.deepEqual([...source.buffer.channel], [0.5]);
    assert.equal(FakeWebSocket.instances.length, 2, "high phrase is synthesized once; only medium preparation opens another socket");
    assert.equal(upstream.sent.length, 1);
    assert.equal(identity, 3);
    const starts = events.filter(event => event.phase === "output_start" && event.channel === "speech");
    assert.equal(starts.length, 1); assert.equal(starts[0].request_id, 3);
    assert.equal(starts[0].frame_index, 3); assert.equal(starts[0].captured_at_ms, 4200);
    assert.equal(starts[0].confirmed_frame_index, 3); assert.equal(starts[0].confirmed_track_id, 7);
    assert.equal(starts[0].capture_to_start_ms, 0, "the new request retains its own source instead of relabeling the cold request");
    source.finish();
    assert.equal(output.describe().state, "completed");
    assert.ok(events.some(event => event.phase === "output_end" && event.request_id === 3));
    assert.equal(cache.describe().hits, 1);
  } finally {
    manager.dispose(); cache.dispose(); output.stop();
  }
});

/** Functionality: Exercise the actual prepared pack through the shared manager/cache/player.
 * Inputs: Real Chinese/English WAVs, same-target escalation and current source clocks.
 * Outputs: Exact composed sample buffers, no risk TTS sockets, complete first phrase and explicit stop.
 * Logic: Load original assets, deliver current warnings and compare every scheduled PCM sample.
 * Constraints: Web Audio is synthetic; this does not establish physical audibility or model accuracy. */
test("prepared bilingual warnings reach playback intact and preserve same-target completion", async function testPreparedRiskPlayback() {
  const origin = "https://47.239.50.129", baseUrl = origin + "/mvp/assets/risk-audio/";
  const assetRoot = new URL("../assets/risk-audio/", import.meta.url);
  const manifest = JSON.parse(await readFile(new URL("manifest.json", assetRoot), "utf8"));
  /* Functionality: Read only the requested repository resource for this same-origin fixture.
   * Inputs: Exact test URL. Outputs: Browser-shaped response. Logic: Validate prefix before mapping.
   * Constraints: No HTTP request, local service, upload or alternate audio content. */
  async function fetchResource(address) {
    assert.ok(address.startsWith(baseUrl));
    const bytes = await readFile(new URL(address.slice(baseUrl.length), assetRoot));
    return new Response(bytes, {headers: {"content-length": String(bytes.length)}});
  }
  const resources = new RiskAudioResources({fetch: fetchResource, crypto: webcrypto, baseUrl, origin});
  try {
    await resources.load();
    for (const language of ["zh-CN", "en-US"]) {
      const {output, win} = backendFixture("test"); enable(output);
      let clock = 100, identity = 0;
      win.crypto.randomUUID = () => `12345678-1234-4234-9234-${String(++identity).padStart(12, "0")}`;
      const cache = new RiskSpeechCache({WebSocket: FakeWebSocket, origin, resources, now: () => clock});
      output.riskSocketFactory = address => cache.createSocket(address);
      const manager = new AssistGuidanceManager({speech: output, now: () => clock,
        setTimer: () => null, clearTimer: () => {}}), events = [];
      const target = {track_id: 17, label: "person", direction: "front_left", alert_required: true,
        alert_priority: 3, risk_level: "high", motion_state: "approaching", relative_speed_mps: -3,
        risk_reasons: ["approaching", "closing_mps_ge_2"], distance_m: 8, ttc_sec: 2};
      try {
        manager.subscribe(event => events.push(event));
        manager.configure({voiceId: "Cherry", language}); manager.setRiskEnabled(true); manager.beginRisk("prepared-risk-test");
        manager.ingestRisk({sessionId: "prepared-risk-test", capturedAt: clock,
          metadata: {index: 1, tracking_epoch: 1, boxes: [target]}});
        await flushMicrotasks();
        assert.equal(output.describe().state, "speaking");
        const first = FakeAudioContext.instances[0].sources[0];
        const composition = manifest.compositions.find(item => item.language === language &&
          item.label === "person" && item.direction === "front_left" && item.risk_level === "high" && item.approaching);
        const pcm = Buffer.concat(await Promise.all(composition.clip_ids.map(async id => {
          const clip = manifest.clips.find(item => item.id === id);
          return (await readFile(new URL(clip.path, assetRoot))).subarray(44);
        })));
        const expected = new Float32Array(pcm.length / 2);
        for (let index = 0; index < expected.length; index++) expected[index] = pcm.readInt16LE(index * 2) / 32768;
        assert.deepEqual(first.buffer.channel, expected);
        assert.equal(first.buffer.sampleRate, 24000, "2x was synthesized already, with no playback-rate multiplication");
        clock = 200;
        manager.ingestRisk({sessionId: "prepared-risk-test", capturedAt: clock,
          metadata: {index: 2, tracking_epoch: 1, boxes: [{...target, risk_level: "critical", alert_priority: 4}]}});
        await flushMicrotasks();
        assert.equal(first.stops, 0, "same target escalation does not cut off the started phrase");
        assert.equal(FakeAudioContext.instances[0].sources.length, 1);
        first.finish(); await flushMicrotasks();
        assert.ok(events.some(event => event.phase === "output_end" && event.request_id === 1));
        assert.equal(FakeWebSocket.instances.length, 0, "covered risk phrases never open backend TTS");
        assert.ok(cache.describe().prepared_compositions >= 1);
        assert.equal(cache.describe().native_preparations, 0);
        manager.stopRisk("user"); cache.cancelPending("user");
        const scheduled = FakeAudioContext.instances[0].sources.length;
        for (const source of FakeAudioContext.instances[0].sources) source.finish();
        await flushMicrotasks();
        assert.equal(FakeAudioContext.instances[0].sources.length, scheduled, "stop never replays pending audio");
      } finally { manager.dispose(); cache.dispose(); output.stop(); }
    }
  } finally { resources.dispose(); }
});

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

/** Functionality: End a backend request that remains connecting or never produces PCM.
 * Inputs: controllable SpeechOutput timers and fake sockets. Outputs: one visible terminal error and released ownership.
 * Logic: invoke each client watchdog directly without waiting for wall-clock deadlines; Constraints: no provider or audio device. */
test("backend speech watchdog releases stalled connection, stream and playback phases", async function testBackendWatchdog() {
  for (const phase of ["connect", "first_audio", "audio_idle", "playback"]) {
    const {output} = backendFixture("test"), timers = new Map(); let timerId = 0;
    output.setTimer = (callback, delay) => { const id = ++timerId; timers.set(id, {callback, delay}); return id; };
    output.clearTimer = id => timers.delete(id);
    enable(output);
    output.speak("watchdog test", {language: "en-US", voice_id: "Cherry"});
    const socket = FakeWebSocket.instances[0];
    if (phase !== "connect") {
      socket.open(); await flushMicrotasks();
      const requestId = JSON.parse(socket.sent[0]).request_id;
      if (phase === "audio_idle" || phase === "playback") {
        socket.message(JSON.stringify({type: "audio_config", request_id: requestId, sample_rate: PCM_RATE,
          format: "pcm_s16le_mono"}));
      }
      if (phase === "playback") {
        socket.message(pcmMessage(1));
        socket.message(JSON.stringify({type: "complete", request_id: requestId, audio_bytes: 2}));
      }
    }
    assert.equal(timers.size, 1, `${phase} watchdog is armed`);
    const [deadlineId, deadline] = timers.entries().next().value; timers.delete(deadlineId); deadline.callback();
    assert.equal(output.describe().active_request, false);
    assert.equal(output.describe().state, "error");
    const expected = {connect: "connection_timeout", first_audio: "first_audio_timeout",
      audio_idle: "audio_idle_timeout", playback: "playback_timeout"};
    assert.equal(output.describe().last_error, expected[phase]);
    assert.equal(timers.size, 0, `${phase} watchdog is cleared after terminal error`);
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
