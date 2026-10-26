/* Responsibilities: Verify audio encoding, capture lifecycle, live-reply notification, device evidence,
 * intent activity and page refusals.
 * Implementation: Node tests with explicit controller fixtures, no browser hardware.
 * Related Modules: voice-input.js, agent-client.js, agent-device.js, integration/assist-mvp.js;
 * check_agent_browser.mjs adds real UI.
 * Declaration Index: None (test callbacks are anonymous).
 * Variable Index: None; fixture state is local to each test. */
import test from "node:test";
import assert from "node:assert/strict";
import {encodeWav, VoiceInput} from "../voice-input.js";
import {AgentPanel} from "../agent-client.js";
import {cameraFailureReason, cameraState, observe, performDevice,
  productNavigationSpeechState} from "../agent-device.js";

// Functionality: Show routing decisions in the activity log before tool execution, once.
// Inputs: explicit bilingual intent/replay events and a minimal DOM fixture; Outputs: labels.
// Logic: Exercise receive directly; reject-mode general questions must display as unsupported.
// Constraints: No actual browser/network or semantic-classification quality assertion.
test("intent activity distinguishes mapped operations from rejected general questions", t => {
  Object.defineProperty(globalThis, "document", {configurable: true, value: {
    documentElement: {lang: "zh-CN"}, createElement: () => ({textContent: ""}),
  }});
  t.after(() => { delete globalThis.document; });
  const items = [];
  const owner = {seen: 0, root: {dataset: {}}, render() {},
    activity: {children: items, append(item) { items.push(item); }}};
  const event = {type: "intent", seq: 1, route: "system", labels: [{zh: "启动导航", en: "Start navigation"}]};
  AgentPanel.prototype.receive.call(owner, event);
  AgentPanel.prototype.receive.call(owner, event);
  assert.equal(items.length, 1);
  assert.equal(items[0].textContent, "已识别操作：启动导航");
  AgentPanel.prototype.receive.call(owner, {type: "intent", seq: 2, route: "general", unknown_policy: "reject"});
  assert.equal(items[1].textContent, "未命中已启用的能力");
  document.documentElement.lang = "en";
  AgentPanel.prototype.receive.call(owner, {type: "intent", seq: 3, route: "general", unknown_policy: "general"});
  assert.equal(items[2].textContent, "General question");
});

// Functionality: Keep backend failure details out of assistant-authored dialogue.
// Inputs: Ordered failure events, their untrusted detail field and the selected UI language.
// Outputs: Empty dialogue plus a localized, non-conversational status notice.
// Logic: Exercise the production receive path with a minimal panel owner.
// Constraints: Presentation only; no service call or model output is involved.
test("agent failures render as status and never as assistant messages", t => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, "document");
  Object.defineProperty(globalThis, "document", {configurable: true, value: {
    documentElement: {lang: "zh-CN"},
  }});
  t.after(() => {
    if (previous) Object.defineProperty(globalThis, "document", previous);
    else delete globalThis.document;
  });

  const dialogue = [], status = {textContent: ""};
  const owner = {
    seen: 0, busy: true, stage: "thinking", notice: null, root: {dataset: {}},
    status,
    append: (...entry) => dialogue.push(entry),
    render() { this.status.textContent = this.notice || "Ready"; },
  };
  AgentPanel.prototype.receive.call(owner, {
    type: "error", seq: 1, code: "agent_explanation_unavailable",
    detail: "A fixed backend message must not be presented as the agent's reply.",
  });
  assert.deepEqual(dialogue, []);
  assert.equal(status.textContent, "助手暂时无法生成故障说明，请稍后重试。");
  assert.equal(status.textContent.includes("fixed backend message"), false);

  document.documentElement.lang = "en-US";
  AgentPanel.prototype.receive.call(owner, {
    type: "error", seq: 2, code: "qwen_http_error",
    detail: "A second fixed backend message must also stay out of dialogue.",
  });
  assert.deepEqual(dialogue, []);
  assert.equal(status.textContent, "The assistant service is unavailable.");
});

/* Functionality: Notify the speech integration for live replies but never replayed history.
 * Inputs: Ordered reply/ready events and an EventTarget fixture. Outputs: One body-bearing event per live reply.
 * Logic: Observe the production receive method, including ready-event replay and sequence deduplication.
 * Constraints: No WebSocket, provider request, speech engine or audible-output claim is involved. */
test("agent reply notifications include live responses and exclude replayed history", t => {
  const previous = new Map(["window", "CustomEvent"].map(name =>
    [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  class FixtureCustomEvent extends Event {
    constructor(type, options = {}) { super(type); this.detail = options.detail; }
  }
  const target = new EventTarget(), replies = [];
  target.addEventListener("prs-agent-reply", event => replies.push(event.detail?.text));
  Object.defineProperty(globalThis, "window", {configurable: true, value: target});
  Object.defineProperty(globalThis, "CustomEvent", {configurable: true, value: FixtureCustomEvent});
  t.after(() => { for (const [name, descriptor] of previous) {
    if (descriptor) Object.defineProperty(globalThis, name, descriptor);
    else delete globalThis[name];
  }});

  const owner = {seen: 0, busy: false, stage: "ready", root: {dataset: {}}, append() {}, render() {},
    receive(event, replayed = false) { AgentPanel.prototype.receive.call(this, event, replayed); }};
  AgentPanel.prototype.receive.call(owner, {type: "reply", seq: 1, text: "刚收到的回答"});
  AgentPanel.prototype.receive.call(owner, {type: "ready", busy: false, events: [
    {type: "reply", seq: 2, text: "连接恢复时重放的历史回答"},
  ]});
  AgentPanel.prototype.receive.call(owner, {type: "reply", seq: 1, text: "重复的旧事件"});

  assert.deepEqual(replies, ["刚收到的回答"]);
});

// Functionality: Verify WAV metadata/duration/resampling at native rates.
// Inputs: synthetic constant PCM; Outputs: header/sample assertions; Logic: decode bytes.
// Constraints: Does not establish microphone or ASR recognition quality.
test("WAV contract is mono PCM16 16kHz with bounded duration", () => {
  for (const rate of [16000, 44100, 48000]) {
    const bytes = Buffer.from(encodeWav(new Float32Array(rate).fill(.5), rate), "base64");
    assert.equal(bytes.toString("ascii", 0, 4), "RIFF");
    assert.equal(bytes.readUInt32LE(24), 16000);
    assert.equal(bytes.readUInt16LE(22), 1);
    assert.equal(bytes.length, 32044);
    assert.equal(bytes.readInt16LE(44), 16384);
  }
  assert.throws(() => encodeWav(new Float32Array(100), 48000));
  assert.throws(() => encodeWav(new Float32Array(16000 * 21), 16000));
});

// Functionality: Reject false success from pending media or old counters.
// Inputs: controller fixture; Outputs: independent active/state assertions.
// Logic: native track and current session evidence; Constraints: no physical camera.
test("camera pending permission is not active and errors remain explicit", () => {
  const app = {stream: null, frameTimings: null, processing: {value: "echo"}, cameraPhase: "permission",
    mode: "camera", message: {dataset: {}, textContent: "waiting"}};
  assert.equal(cameraState(app).active, false);
  assert.equal(cameraState(app).state, "starting");
  app.stream = {getVideoTracks: () => [{readyState: "live"}]};
  app.frameTimings = {steadyFrames: 2};
  assert.equal(cameraState(app).state, "streaming");
  assert.equal(cameraState(app).frames_received, 3);
  app.message.dataset.kind = "error";
  app.lastErrorType = "NotFoundError";
  assert.equal(cameraState(app).state, "error");
  assert.equal(cameraState(app).reason, "camera_not_found");
  assert.equal(cameraFailureReason("NotAllowedError"), "camera_permission_denied");
  assert.equal(cameraFailureReason("UnknownError"), "camera_failed");
});

// Functionality: Refuse page-inapplicable tools and invalid observation budgets.
// Inputs: absent-controller document; Outputs: explicit refusals; Logic: no dynamic import.
// Constraints: Does not exercise an actual browser page.
test("semantic tools never construct controllers on the wrong page", async () => {
  const doc = {querySelector: () => null};
  for (const action of ["camera_start", "maps_locate", "agent_navigation_status"])
    assert.equal((await performDevice(action, {}, doc)).reason, "page_unavailable");
  await assert.rejects(observe(() => ({}), () => true, 13));
  assert.deepEqual(await observe(() => ({state: "ready"}), () => false, 1), {state: "ready"});
});

// Functionality: Preserve the unified product's navigation-output evidence in status snapshots.
// Inputs: Detached guidance-manager fixture; Outputs: product navigation speech metadata.
// Logic: Project only the live navigation channel state; Constraints: no speech body or audibility claim.
test("product navigation speech status is mapped from its existing output owner", () => {
  assert.deepEqual(productNavigationSpeechState({
    navigation_enabled: true,
    navigation_run_id: "navigation-4",
    paused: {speech: true, vibration: false},
    reasons: {speech: "risk_priority"},
    active: {speech: {request_id: "speech-7", started: true, text: "must not escape"}},
  }), {
    available: true,
    enabled: true,
    paused: true,
    reason: "risk_priority",
    run_id: "navigation-4",
    active_request_id: "speech-7",
    active_started: true,
    audio_output_verified: false,
  });
  assert.deepEqual(productNavigationSpeechState({}), {
    available: true, enabled: false, paused: false, reason: null, run_id: null,
    active_request_id: null, active_started: false, audio_output_verified: false,
  });
});

// Functionality: Release microphone on transport loss without submitting or reconnecting.
// Inputs: explicit EventTarget socket and recorder spies; Outputs: lifecycle assertions.
// Logic: Exercise actual connect close handler; Constraints: no network or native media API.
test("agent socket disconnect cancels capture and preserves offline UI", t => {
  const socket = new EventTarget();
  // Node builds need not expose this browser global; install and restore an explicit fixture.
  const previousSocket = Object.getOwnPropertyDescriptor(globalThis, "WebSocket");
  Object.defineProperty(globalThis, "WebSocket", {configurable: true, writable: true,
    value: function () { return socket; }});
  t.after(() => {
    if (previousSocket) Object.defineProperty(globalThis, "WebSocket", previousSocket);
    else delete globalThis.WebSocket;
  });
  Object.defineProperty(globalThis, "document", {configurable: true,
    value: {documentElement: {dataset: {}}}});
  Object.defineProperty(globalThis, "location", {configurable: true,
    value: {href: "http://localhost/live/", protocol: "http:"}});
  t.after(() => { delete globalThis.document; delete globalThis.location; });
  let cancelled = 0, rendered = 0;
  const owner = {recording: true, connected: true, holdActive: true, holdToken: 5,
    activePointerId: 7, root: {dataset: {pressing: "true"}},
    voice: {cancel: async () => { cancelled++; }},
    render: () => { rendered++; }};
  AgentPanel.prototype.connect.call(owner);
  socket.dispatchEvent(new Event("close"));
  assert.equal(cancelled, 1);
  assert.equal(owner.recording, false);
  assert.equal(owner.connected, false);
  assert.equal(owner.holdActive, false);
  assert.equal(owner.holdToken, 6);
  assert.equal(owner.activePointerId, null);
  assert.equal(owner.root.dataset.pressing, "false");
  assert.equal(owner.stage, "offline");
  assert.equal(rendered, 1);
});

// Functionality: Prevent late permission grants from restarting a cancelled recording.
// Inputs: deferred media promise and fake audio context; Outputs: stopped track/closed context.
// Logic: Real recorder generation cancellation; Constraints: no real microphone or worklet.
test("cancel before microphone permission resolves releases the late stream", async t => {
  const prior = new Map(["isSecureContext", "AudioContext", "navigator"].map(name =>
    [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  t.after(() => { for (const [name, descriptor] of prior) {
    if (descriptor) Object.defineProperty(globalThis, name, descriptor); else delete globalThis[name];
  }});
  let grant, stopped = 0, closed = 0;
  const pending = new Promise(resolve => { grant = resolve; });
  for (const [name, value] of Object.entries({isSecureContext: true,
    AudioContext: function () { return {resume: async () => {}, close: async () => { closed++; }}; },
    navigator: {mediaDevices: {getUserMedia: () => pending}}}))
    Object.defineProperty(globalThis, name, {configurable: true, value});
  const voice = new VoiceInput(() => assert.fail("No recording should start"));
  const starting = voice.start();
  await Promise.resolve();
  await voice.cancel();
  grant({getTracks: () => [{stop: () => { stopped++; }}]});
  assert.equal(await starting, false);
  assert.equal(stopped, 1);
  assert.equal(closed, 1);
  assert.equal(voice.stream, null);
});
