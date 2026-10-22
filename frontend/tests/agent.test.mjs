/* Responsibilities: Verify audio encoding, capture lifecycle, device evidence, intent activity
 * and page refusals.
 * Implementation: Node tests with explicit controller fixtures, no browser hardware.
 * Related Modules: voice-input.js, agent-device.js; check_agent_browser.mjs adds real UI.
 * Declaration Index: None (test callbacks are anonymous).
 * Variable Index: None; fixture state is local to each test. */
import test from "node:test";
import assert from "node:assert/strict";
import {encodeWav, VoiceInput} from "../voice-input.js";
import {AgentPanel} from "../agent-client.js";
import {cameraState, observe, performDevice} from "../agent-device.js";

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
  assert.equal(cameraState(app).state, "error");
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

// Functionality: Release microphone on transport loss without submitting or reconnecting.
// Inputs: explicit EventTarget socket and recorder spies; Outputs: lifecycle assertions.
// Logic: Exercise actual connect close handler; Constraints: no network or native media API.
test("agent socket disconnect cancels capture and preserves offline UI", t => {
  const socket = new EventTarget();
  t.mock.method(globalThis, "WebSocket", function () { return socket; });
  Object.defineProperty(globalThis, "document", {configurable: true,
    value: {documentElement: {dataset: {}}}});
  Object.defineProperty(globalThis, "location", {configurable: true,
    value: {href: "http://localhost/live/", protocol: "http:"}});
  t.after(() => { delete globalThis.document; delete globalThis.location; });
  let cancelled = 0, rendered = 0;
  const owner = {recording: true, connected: true, voice: {cancel: async () => { cancelled++; }},
    render: () => { rendered++; }};
  AgentPanel.prototype.connect.call(owner);
  socket.dispatchEvent(new Event("close"));
  assert.equal(cancelled, 1);
  assert.equal(owner.recording, false);
  assert.equal(owner.connected, false);
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
