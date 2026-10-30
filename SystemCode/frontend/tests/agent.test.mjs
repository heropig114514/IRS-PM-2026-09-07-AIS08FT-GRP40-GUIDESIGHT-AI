/* Responsibilities: Verify audio encoding, capture lifecycle, live/replayed reply notification, device evidence,
 * intent activity and page refusals.
 * Implementation: Node tests with explicit controller fixtures, no browser hardware.
 * Related Modules: voice-input.js, agent-client.js, agent-device.js, integration/assist-mvp.js;
 * check_agent_browser.mjs adds real UI.
 * Declaration Index: testLiveReplyNotifications; testLiveReplyNotifications.FixtureCustomEvent;
 * testProductRecordingToggle; testProductRecordingRender;
 * testAgentReconnect; testAgentReconnect.FixtureCustomEvent; testAgentReconnect.FakeWebSocket.
 * Variable Index: None; fixtures and request state are local to each test. */
import test from "node:test";
import assert from "node:assert/strict";
import {encodeWav, VoiceInput} from "../voice-input.js";
import {AgentPanel} from "../agent-client.js";
import {cameraFailureReason, cameraState, observe, performDevice,
  productNavigationSpeechState} from "../agent-device.js";

/* Functionality: Verify product recording uses a click toggle and dispatches speech activation synchronously.
 * Inputs: Minimal panel state and EventTarget fixture. Outputs: start/finish calls and activation count.
 * Logic: Exercise the production record and button-handler methods in both idle and recording states.
 * Constraints: No DOM layout, browser permission prompt, microphone, network or actual speech playback. */
test("product recording control toggles capture on clicks", async function testProductRecordingToggle(t) {
  const previous = Object.getOwnPropertyDescriptor(globalThis, "window");
  const target = new EventTarget();
  let activations = 0;
  const timeline = [];
  target.addEventListener("prs-assist-voice-gesture", () => { activations++; timeline.push("activate"); });
  Object.defineProperty(globalThis, "window", {configurable: true, value: target});
  t.after(() => {
    if (previous) Object.defineProperty(globalThis, "window", previous);
    else delete globalThis.window;
  });

  const actions = [];
  const owner = {productMode: true, busy: false, connected: true, recording: false, stage: "ready",
    startRecording() { actions.push("start"); timeline.push("start"); },
    finishRecording() { actions.push("finish"); timeline.push("finish"); }};
  owner.record = AgentPanel.prototype.record;
  AgentPanel.prototype.handleRecordClick.call(owner);
  await Promise.resolve();
  assert.deepEqual(actions, ["start"]);
  assert.deepEqual(timeline, ["start"]);
  assert.equal(activations, 0, "speech calibration does not play over a new recording");

  owner.recording = true;
  AgentPanel.prototype.handleRecordClick.call(owner);
  await Promise.resolve();
  assert.deepEqual(actions, ["start", "finish"]);
  assert.deepEqual(timeline, ["start", "finish", "activate"]);
  assert.equal(activations, 1, "finishing the recording activates speech within the click gesture");

  owner.productMode = false;
  owner.recording = false;
  AgentPanel.prototype.handleRecordClick.call(owner);
  await Promise.resolve();
  assert.deepEqual(actions, ["start", "finish", "start"]);
  assert.deepEqual(timeline, ["start", "finish", "activate", "start"]);
  assert.equal(activations, 1, "diagnostic clicks do not signal product speech activation");
});

/* Functionality: Render accessible labels and cancellation states for the product recording toggle.
 * Inputs: Minimal language-aware panel controls. Outputs: text, disabled state and active-recording marker.
 * Logic: Exercise production rendering in ready, permission-pending and recording states.
 * Constraints: No browser layout, hardware prompt, microphone, network or playback verification. */
test("product recording toggle exposes start, permission, and finish states", function testProductRecordingRender(t) {
  const previous = Object.getOwnPropertyDescriptor(globalThis, "document");
  Object.defineProperty(globalThis, "document", {configurable: true, value: {
    documentElement: {lang: "en"},
  }});
  t.after(() => {
    if (previous) Object.defineProperty(globalThis, "document", previous);
    else delete globalThis.document;
  });

  const controls = {
    '[data-copy="help"]': {textContent: ""}, "#agent-send": {disabled: false},
    "#agent-record": {textContent: "", disabled: false, setAttribute() {}},
    "#agent-cancel": {disabled: true}, "#agent-clear": {disabled: true},
  };
  const owner = {productMode: true, connected: true, busy: false, recording: false, stage: "ready",
    notice: null, input: {disabled: false}, status: {textContent: ""}, root: {
      dataset: {}, querySelectorAll: () => [], querySelector: selector => controls[selector],
    }};
  const record = controls["#agent-record"];
  AgentPanel.prototype.render.call(owner);
  assert.equal(record.textContent, "Start recording");
  assert.equal(record.disabled, false);

  owner.stage = "permission";
  AgentPanel.prototype.render.call(owner);
  assert.equal(record.textContent, "Waiting for microphone permission…");
  assert.equal(record.disabled, true);
  assert.equal(controls["#agent-cancel"].disabled, false);

  owner.recording = true;
  owner.stage = "recording";
  AgentPanel.prototype.render.call(owner);
  assert.equal(record.textContent, "Finish & send");
  assert.equal(owner.status.textContent, "Recording. Tap again to finish and send within 20 seconds.");
  assert.equal(owner.root.dataset.recording, "true");
  assert.equal(record.disabled, false);
});

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
  assert.equal(status.textContent, "The model service rejected the request. Check model access, region or request settings.");

  AgentPanel.prototype.receive.call(owner, {type: "error", seq: 3, code: "context_limit"});
  assert.deepEqual(dialogue, []);
  assert.equal(status.textContent, "The conversation reached its context limit. Clear the conversation and retry.");
  document.documentElement.lang = "zh-CN";
  AgentPanel.prototype.receive.call(owner, {type: "error", seq: 4, code: "context_limit"});
  assert.equal(status.textContent, "对话上下文已达到上限，请清空对话后重试。");
  AgentPanel.prototype.receive.call(owner, {type: "error", seq: 5, code: "invalid_tool_call"});
  assert.equal(status.textContent, "助手未能确定有效的下一步操作。请检查导航状态后再试。");
  document.documentElement.lang = "en-US";
  AgentPanel.prototype.receive.call(owner, {type: "error", seq: 6, code: "qwen_request_too_large"});
  assert.equal(status.textContent, "The model service rejected the request as too large. Clear the conversation or shorten the request.");
  AgentPanel.prototype.receive.call(owner, {type: "error", seq: 7, code: "qwen_rate_limit"});
  assert.equal(status.textContent, "The model service is rate-limiting requests. Wait briefly before trying again.");

  document.documentElement.lang = "zh-CN";
  AgentPanel.prototype.receive.call(owner, {
    type: "rejected", seq: 8, code: "agent_capacity",
    detail: "Untrusted backend text must not become dialogue or UI copy.",
  });
  assert.deepEqual(dialogue, []);
  assert.equal(status.textContent, "语音助手会话已达到服务容量，请稍后刷新页面。");
  document.documentElement.lang = "en-US";
  AgentPanel.prototype.receive.call(owner, {
    type: "rejected", seq: 9, code: "agent_capacity", detail: "Untrusted backend text.",
  });
  assert.equal(status.textContent, "The assistant session capacity is reached. Refresh the page later.");
});

/* Functionality: Notify the speech integration for live replies but never replayed history.
 * Inputs: Ordered reply/ready events and an EventTarget fixture. Outputs: One body-bearing event per live reply.
 * Logic: Observe the production receive method, including ready-event replay and sequence deduplication.
 * Constraints: No WebSocket, provider request, speech engine or audible-output claim is involved. */
test("agent reply notifications include live responses and exclude replayed history", function testLiveReplyNotifications(t) {
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

/* Functionality: Restore an in-flight request after a mobile transport handoff.
 * Inputs: Fake WebSockets, session fixtures and deterministic timers. Outputs: one replayed reply notification.
 * Logic: Reconnect performs hello only, matches the pending request ID, and rejects old socket callbacks.
 * Constraints: No real network, backend work, microphone capture or speech playback is exercised. */
test("agent reconnect replays its pending reply once without resubmitting", function testAgentReconnect(t) {
  const names = ["WebSocket", "document", "location", "window", "CustomEvent", "sessionStorage", "crypto",
    "setTimeout", "clearTimeout", "setInterval", "clearInterval"];
  const previous = new Map(names.map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  t.after(() => { for (const [name, descriptor] of previous) {
    if (descriptor) Object.defineProperty(globalThis, name, descriptor); else delete globalThis[name];
  }});
  const timers = new Map(), sockets = [], replies = []; let timerSequence = 0, cancelled = 0;
  Object.defineProperty(globalThis, "setTimeout", {configurable: true, value: (callback, delay) => {
    const id = ++timerSequence; timers.set(id, {callback, delay}); return id;
  }});
  Object.defineProperty(globalThis, "clearTimeout", {configurable: true, value: id => timers.delete(id)});
  Object.defineProperty(globalThis, "setInterval", {configurable: true, value: (callback, delay) => {
    const id = ++timerSequence; timers.set(id, {callback, delay, interval: true}); return id;
  }});
  Object.defineProperty(globalThis, "clearInterval", {configurable: true, value: id => timers.delete(id)});
  class FixtureCustomEvent extends Event {
    constructor(type, options = {}) { super(type); this.detail = options.detail; }
  }
  const target = new EventTarget();
  target.addEventListener("prs-agent-reply", event => replies.push(event.detail.text));
  Object.defineProperty(globalThis, "window", {configurable: true, value: target});
  Object.defineProperty(globalThis, "CustomEvent", {configurable: true, value: FixtureCustomEvent});
  Object.defineProperty(globalThis, "document", {configurable: true, value: {
    hidden: false, documentElement: {lang: "en", dataset: {}}, createElement: () => ({textContent: ""}),
  }});
  Object.defineProperty(globalThis, "location", {configurable: true, value: {href: "http://localhost/live/", protocol: "http:"}});
  Object.defineProperty(globalThis, "sessionStorage", {configurable: true, value: {getItem: () => "fixture"}});
  Object.defineProperty(globalThis, "crypto", {configurable: true, value: {randomUUID: () => "request-1"}});
  function FakeWebSocket() {
    const socket = new EventTarget(); socket.readyState = 0; socket.sent = [];
    socket.send = message => socket.sent.push(JSON.parse(message));
    socket.open = () => { socket.readyState = 1; socket.dispatchEvent(new Event("open")); };
    socket.message = event => socket.dispatchEvent(Object.assign(new Event("message"), {data: JSON.stringify(event)}));
    socket.close = () => { socket.readyState = 3; socket.dispatchEvent(new Event("close")); };
    sockets.push(socket); return socket;
  }
  FakeWebSocket.CONNECTING = 0; FakeWebSocket.OPEN = 1;
  Object.defineProperty(globalThis, "WebSocket", {configurable: true, value: FakeWebSocket});
  let rendered = 0, failures = 0;
  const owner = {recording: true, connected: true,
    root: {dataset: {recording: "true"}}, stage: "ready", busy: false,
    socket: null, heartbeat: null, heartbeatTimeout: null, pingPending: false,
    connectionTimer: null, reconnectTimer: null, reconnectAttempt: 0,
    connectionGeneration: 0, leaving: false, pendingRequestId: null, pendingReplyNotified: false,
    voice: {cancel: async () => { cancelled++; }}, render() {
      rendered++; this.root.dataset.recording = String(this.recording);
    }, fail() { failures++; }, append() {},
    connect() { return AgentPanel.prototype.connect.call(this); },
    handleDisconnect(socket, generation) { return AgentPanel.prototype.handleDisconnect.call(this, socket, generation); },
    send(message) { return AgentPanel.prototype.send.call(this, message); },
    submit(input) { return AgentPanel.prototype.submit.call(this, input); },
    scheduleReconnect() { return AgentPanel.prototype.scheduleReconnect.call(this); },
    receive(event, replayed = false) { return AgentPanel.prototype.receive.call(this, event, replayed); }};

  AgentPanel.prototype.connect.call(owner);
  const first = sockets[0]; first.open();
  first.message({type: "ready", busy: false, events: []});
  owner.submit({text: "check status"});
  first.close();
  assert.equal(cancelled, 1);
  assert.equal(owner.recording, false);
  assert.equal(owner.connected, false);
  assert.equal(owner.root.dataset.recording, "false");
  assert.equal(owner.stage, "offline");
  assert.equal(rendered >= 3, true);

  const retryEntry = [...timers].find(([, item]) => item.delay === 1000);
  assert.ok(retryEntry, `disconnect schedules the first bounded retry: ${JSON.stringify([...timers])}`);
  const [retryId, retry] = retryEntry;
  timers.delete(retryId); retry.callback();
  const second = sockets[1]; second.open();
  second.message({type: "ready", busy: false, events: [
    {type: "reply", seq: 1, request_id: "other-request", text: "old reply"},
    {type: "reply", seq: 2, request_id: "request-1", text: "pending reply"},
    {type: "idle", seq: 3, request_id: "request-1"},
  ]});
  second.message({type: "reply", seq: 2, request_id: "request-1", text: "duplicate replay"});
  first.close();

  assert.equal(owner.connected, true, "stale close from the prior socket cannot override the new connection");
  assert.deepEqual(first.sent.map(message => message.type), ["hello", "submit"]);
  assert.deepEqual(second.sent.map(message => message.type), ["hello"], "reconnect never resubmits the user command");
  assert.deepEqual(replies, ["pending reply"]);
  assert.equal(owner.pendingRequestId, null);
  second.send = () => { throw Object.assign(new Error("fixture transport failure"), {name: "NetworkError"}); };
  assert.equal(owner.send({type: "cancel"}), false);
  assert.equal(owner.connected, false, "a synchronous send failure enters the same disconnect path");
  assert.equal(owner.stage, "offline");
  assert.equal(failures, 1);
  clearTimeout(owner.reconnectTimer); owner.reconnectTimer = null;
  assert.equal(timers.size, 0);
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
