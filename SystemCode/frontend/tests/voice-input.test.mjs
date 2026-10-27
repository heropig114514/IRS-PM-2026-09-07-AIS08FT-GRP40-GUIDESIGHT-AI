/* Responsibilities: Verify explicit voice capture, PCM encoding, worklet boundaries and cleanup.
 * Implementation: Install deterministic browser-audio fixtures and exercise real recorder modules.
 * Related Modules: ../voice-input.js, ../voice-worklet.js, agent.test.mjs.
 * Declaration Index: browserFixture — install and restore fake media/WebAudio APIs;
 * deferred — expose one manually settled asynchronous operation;
 * loadWorklet — load the production worklet under host-owned audio globals;
 * encodeWavBoundaries — check sample conversion and input limits;
 * secureAndCapabilityGates — reject insecure or unsupported capture;
 * capturesAndFlushes — verify a complete recording and resource release;
 * permissionAndContextFailures — preserve failures and release partial resources;
 * stalePermissionRejection — ignore a denial after cancellation;
 * cancelDuringWorkletLoad — prevent stale graph construction;
 * finishTimeoutCleansUp — release a recording when its flush handshake expires;
 * releaseRespectsClosedContext — avoid closing an already closed context;
 * audioWorkletFlushesBatchesAndStopsAtLimit — check batching, stop, silence and the duration cap.
 * Variable Index: None; fixture counters and fake browser objects are local to each test.
 * Constraints: Tests use synthetic samples and fake browser APIs; no real microphone or ASR claim. */
import assert from "node:assert/strict";
import test from "node:test";
import {encodeWav, VoiceInput} from "../voice-input.js";

/* Functionality: Install one explicit browser media and audio graph fixture.
 * Inputs: Test cleanup owner and optional API behaviors. Outputs: Recorder and observable fake resources.
 * Logic: Replace only named browser globals and restore their original descriptors after the test.
 * Constraints: Media is synthetic; the fixture never opens a device or creates an audible output path. */
function browserFixture(t, options = {}) {
  const names = ["isSecureContext", "AudioContext", "AudioWorkletNode", "navigator"];
  const previous = new Map(names.map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  t.after(() => {
    for (const [name, descriptor] of previous) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  });

  const state = {permissionCalls: 0, contexts: 0, stoppedTracks: 0, closedContexts: 0,
    moduleCalls: 0, constructedNodes: 0, sourceConnections: 0, sourceDisconnections: 0,
    nodeConnections: 0, nodeDisconnections: 0, actions: [], messages: []};
  const track = {stop: () => { state.stoppedTracks++; }};
  const stream = {getTracks: () => [track]};
  const source = {connect: () => { state.sourceConnections++; },
    disconnect: () => { state.sourceDisconnections++; }};
  let workletNode;
  const port = {onmessage: null, postMessage: message => {
    state.messages.push(message);
    if (message.type === "stop" && options.autoFlush)
      port.onmessage?.({data: {type: "stopped"}});
  }};
  workletNode = {port, connect: () => { state.nodeConnections++; },
    disconnect: () => { state.nodeDisconnections++; }};
  const context = {
    sampleRate: options.sampleRate ?? 48000,
    state: "running",
    destination: {},
    resume: () => { state.actions.push("resume"); return options.resume?.() ?? Promise.resolve(); },
    close: async () => {
      state.closedContexts++;
      context.state = "closed";
      if (options.closeError) throw options.closeError;
    },
    audioWorklet: {addModule: url => {
      state.moduleCalls++;
      state.workletUrl = url.href;
      return options.addModule?.(url) ?? Promise.resolve();
    }},
    createMediaStreamSource: value => {
      assert.equal(value, stream);
      return source;
    },
  };
  const getUserMedia = options.getUserMedia ?? (() => {
    state.actions.push("permission");
    return Promise.resolve(stream);
  });
  const mediaDevices = options.mediaDevices === false ? undefined : {
    getUserMedia: request => {
      state.permissionCalls++;
      state.request = request;
      return getUserMedia(request);
    },
  };
  Object.defineProperty(globalThis, "isSecureContext", {configurable: true, value: options.secure !== false});
  Object.defineProperty(globalThis, "navigator", {configurable: true, value: {mediaDevices}});
  Object.defineProperty(globalThis, "AudioContext", {configurable: true,
    value: options.audioContext === false ? undefined : function () { state.contexts++; return context; }});
  Object.defineProperty(globalThis, "AudioWorkletNode", {configurable: true,
    value: function (actualContext, name) {
      assert.equal(actualContext, context);
      assert.equal(name, "prs-voice-capture");
      state.constructedNodes++;
      return workletNode;
    }});
  return {voice: new VoiceInput(() => { state.limitSignals = (state.limitSignals ?? 0) + 1; }),
    state, context, stream, source, node: workletNode, port};
}

/* Functionality: Create a promise whose resolution is controlled by its caller.
 * Inputs: None. Outputs: Promise and resolve/reject functions.
 * Logic: Retain the native settlement callbacks for deterministic race tests.
 * Constraints: The helper does not add timeout or retry semantics. */
function deferred() {
  let resolve, reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return {promise, resolve, reject};
}

/* Functionality: Load the production audio worklet with a synthetic browser host.
 * Inputs: Test cleanup owner. Outputs: Registered processor constructor and emitted messages.
 * Logic: Set AudioWorklet globals before importing the side-effect registration module.
 * Constraints: A unique module URL isolates this fixture from other test imports. */
async function loadWorklet(t) {
  const names = ["AudioWorkletProcessor", "registerProcessor", "sampleRate"];
  const previous = new Map(names.map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  const state = {registeredName: null, Processor: null, messages: []};
  Object.defineProperty(globalThis, "sampleRate", {configurable: true, value: 16000});
  Object.defineProperty(globalThis, "AudioWorkletProcessor", {configurable: true,
    value: class { constructor() { this.port = {onmessage: null, postMessage: message => state.messages.push(message)}; } }});
  Object.defineProperty(globalThis, "registerProcessor", {configurable: true, value: (name, Processor) => {
    state.registeredName = name;
    state.Processor = Processor;
  }});
  t.after(() => {
    for (const [name, descriptor] of previous) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  });
  const moduleUrl = new URL(`../voice-worklet.js?voice-input-test=${Date.now()}`, import.meta.url);
  await import(moduleUrl.href);
  assert.equal(state.registeredName, "prs-voice-capture");
  return state;
}

/* Functionality: Check resampling, clipping, WAV metadata and invalid input limits.
 * Inputs: None. Outputs: Assertions over the production encoder.
 * Logic: Decode the WAV bytes and verify little-endian PCM fields and boundary failures.
 * Constraints: Sample data is generated in memory and does not represent recognized speech. */
function encodeWavBoundaries() {
  const input = new Float32Array(4410);
  input.fill(2);
  input[0] = input[1] = -2;
  const bytes = Buffer.from(encodeWav(input, 44100), "base64");
  assert.equal(bytes.toString("ascii", 0, 4), "RIFF");
  assert.equal(bytes.toString("ascii", 8, 12), "WAVE");
  assert.equal(bytes.readUInt32LE(24), 16000);
  assert.equal(bytes.readUInt32LE(28), 32000);
  assert.equal(bytes.readUInt32LE(40), 3200);
  assert.equal(bytes.readInt16LE(44), -32768);
  assert.equal(bytes.readInt16LE(46), 32767);
  assert.throws(() => encodeWav(new Float32Array(1600), Number.NaN));
  assert.throws(() => encodeWav(new Float32Array(1600), 15999));
}

/* Functionality: Reject capture before opening a microphone when the page/API is unavailable.
 * Inputs: None. Outputs: Explicit SecurityError and NotSupportedError assertions.
 * Logic: Exercise secure-context, media-device and Web Audio capability guards.
 * Constraints: Browser APIs are fixture values; no permission prompt is generated. */
async function secureAndCapabilityGates(t) {
  const insecure = browserFixture(t, {secure: false});
  await assert.rejects(insecure.voice.start(), error => error.name === "SecurityError");
  assert.equal(insecure.state.permissionCalls, 0);
  assert.equal(insecure.state.contexts, 0);

  const noMedia = browserFixture(t, {mediaDevices: false});
  await assert.rejects(noMedia.voice.start(), error => error.name === "NotSupportedError");
  assert.equal(noMedia.state.contexts, 0);

  const noAudioContext = browserFixture(t, {audioContext: false});
  await assert.rejects(noAudioContext.voice.start(), error => error.name === "NotSupportedError");
  assert.equal(noAudioContext.state.permissionCalls, 0);
}

/* Functionality: Capture samples, answer the worklet duration signal, flush and release resources.
 * Inputs: Synthetic 48kHz samples. Outputs: 16kHz PCM WAV and lifecycle assertions.
 * Logic: Start through the real recorder, deliver worklet messages, and finish through its stop handshake.
 * Constraints: Audio graph and media track are fake; this does not verify physical playback or ASR. */
async function capturesAndFlushes(t) {
  const fixture = browserFixture(t, {autoFlush: true});
  assert.equal(await fixture.voice.start(), true);
  assert.deepEqual(fixture.state.actions, ["permission", "resume"]);
  assert.deepEqual(fixture.state.request, {video: false,
    audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true}});
  assert.match(fixture.state.workletUrl, /voice-worklet\.js$/);
  assert.equal(fixture.state.sourceConnections, 1);
  assert.equal(fixture.state.nodeConnections, 1);
  fixture.port.onmessage({data: {type: "ignored"}});
  fixture.port.onmessage({data: {type: "samples", samples: new Float32Array(4800).fill(0.5)}});
  fixture.port.onmessage({data: {type: "limit"}});
  fixture.port.onmessage({data: {type: "stopped"}});
  assert.equal(fixture.state.limitSignals, 1);
  const wav = Buffer.from(await fixture.voice.finish(), "base64");
  assert.equal(wav.toString("ascii", 0, 4), "RIFF");
  assert.equal(wav.readUInt32LE(24), 16000);
  assert.equal(wav.length, 44 + 1600 * 2);
  assert.equal(wav.readInt16LE(44), 16384);
  assert.deepEqual(fixture.state.messages, [{type: "stop"}]);
  assert.equal(fixture.state.stoppedTracks, 1);
  assert.equal(fixture.state.sourceDisconnections, 1);
  assert.equal(fixture.state.nodeDisconnections, 1);
  assert.equal(fixture.state.closedContexts, 1);
  assert.equal(fixture.voice.stream, null);
}

/* Functionality: Preserve permission, context and worklet failures while releasing acquired resources.
 * Inputs: Three deterministic failure modes. Outputs: Original or named error plus cleanup assertions.
 * Logic: Exercise failures before and after the browser grants a media stream.
 * Constraints: No synthetic success fallback, retry or real permission request is used. */
async function permissionAndContextFailures(t) {
  const denied = new Error("permission denied fixture");
  const permission = browserFixture(t, {getUserMedia: () => Promise.reject(denied)});
  await assert.rejects(permission.voice.start(), error => error === denied);
  assert.equal(permission.state.closedContexts, 1);
  assert.equal(permission.state.stoppedTracks, 0);

  const resumeFailure = new Error("resume rejected fixture");
  const context = browserFixture(t, {resume: () => Promise.reject(resumeFailure)});
  await assert.rejects(context.voice.start(), error => error.name === "AudioContextError");
  assert.equal(context.state.stoppedTracks, 1);
  assert.equal(context.state.closedContexts, 1);
  assert.equal(context.state.constructedNodes, 0);

  const moduleFailure = new Error("worklet load fixture");
  const worklet = browserFixture(t, {addModule: () => Promise.reject(moduleFailure)});
  await assert.rejects(worklet.voice.start(), error => error === moduleFailure);
  assert.equal(worklet.state.stoppedTracks, 1);
  assert.equal(worklet.state.closedContexts, 1);
}

/* Functionality: Ignore a media denial that arrives after the caller canceled startup.
 * Inputs: Deferred microphone permission. Outputs: False startup result and closed context.
 * Logic: Invalidate the generation before rejecting the permission request.
 * Constraints: No operating-system microphone permission is involved. */
async function stalePermissionRejection(t) {
  const media = deferred();
  const fixture = browserFixture(t, {getUserMedia: () => media.promise});
  const starting = fixture.voice.start();
  await Promise.resolve();
  await fixture.voice.cancel();
  media.reject(new Error("late permission denial"));
  assert.equal(await starting, false);
  assert.equal(fixture.state.closedContexts, 1);
}

/* Functionality: Stop graph construction when cancellation occurs during worklet loading.
 * Inputs: Deferred worklet registration. Outputs: False startup result and no node construction.
 * Logic: Cancel after media is active but before the module load resolves.
 * Constraints: Uses controlled promises and synthetic tracks only. */
async function cancelDuringWorkletLoad(t) {
  const module = deferred(), started = deferred();
  const fixture = browserFixture(t, {addModule: () => { started.resolve(); return module.promise; }});
  const starting = fixture.voice.start();
  await started.promise;
  await fixture.voice.cancel();
  module.resolve();
  assert.equal(await starting, false);
  assert.equal(fixture.state.stoppedTracks, 1);
  assert.equal(fixture.state.closedContexts, 1);
  assert.equal(fixture.state.constructedNodes, 0);
}

/* Functionality: Reject an unfinished worklet flush and still release recording resources.
 * Inputs: Running fake recorder with no stopped acknowledgment. Outputs: timeout rejection and cleanup.
 * Logic: Exercise finish's one-second protocol deadline without shortening it.
 * Constraints: Real timers are used to verify the configured production deadline. */
async function finishTimeoutCleansUp(t) {
  const fixture = browserFixture(t);
  await fixture.voice.start();
  await assert.rejects(fixture.voice.finish(), /录音结束超时/);
  assert.equal(fixture.state.stoppedTracks, 1);
  assert.equal(fixture.state.nodeDisconnections, 1);
  assert.equal(fixture.state.closedContexts, 1);
  await assert.rejects(new VoiceInput(() => {}).finish(), /录音尚未开始/);
}

/* Functionality: Avoid closing a context whose browser state already reports closed.
 * Inputs: Recorder state with an already closed context. Outputs: No second close call.
 * Logic: Release follows the resource guard directly and leaves the recorder empty.
 * Constraints: The state fixture is not a real browser AudioContext. */
async function releaseRespectsClosedContext(t) {
  const fixture = browserFixture(t);
  fixture.context.state = "closed";
  fixture.voice.context = fixture.context;
  await fixture.voice.release();
  assert.equal(fixture.state.closedContexts, 0);
  assert.equal(fixture.voice.context, null);
}

/* Functionality: Verify the real worklet's batch, stop, silence and hard-duration boundaries.
 * Inputs: Synthetic PCM blocks and the host sample-rate fixture. Outputs: Worklet message sequence.
 * Logic: Fill one full batch, acknowledge stop, then process exactly 20 seconds to trigger the cap.
 * Constraints: This checks buffer protocol only; no real microphone or recognition is involved. */
async function audioWorkletFlushesBatchesAndStopsAtLimit(t) {
  const state = await loadWorklet(t);
  const processor = new state.Processor();
  processor.flush();
  assert.equal(processor.process([]), true);
  assert.equal(processor.process([[new Float32Array(2048).fill(0.25)]]), true);
  assert.equal(state.messages.length, 1);
  assert.equal(state.messages[0].type, "samples");
  assert.equal(state.messages[0].samples.length, 2048);
  processor.port.onmessage({data: {type: "stop"}});
  assert.deepEqual(state.messages.map(message => message.type), ["samples", "stopped"]);
  const count = state.messages.length;
  assert.equal(processor.process([[new Float32Array(8).fill(0.5)]]), true);
  assert.equal(state.messages.length, count);

  const bounded = new state.Processor();
  assert.equal(bounded.process([[new Float32Array(16000 * 20).fill(0.1)]]), true);
  assert.equal(state.messages.at(-2).type, "samples");
  assert.equal(state.messages.at(-2).samples.length, 16000 * 20 % 2048);
  assert.deepEqual(state.messages.at(-1), {type: "limit"});
  assert.equal(bounded.stopped, true);
}

test("WAV encoding covers clipping, resampling and invalid boundaries", encodeWavBoundaries);
test("capture enforces secure origin and browser capability", secureAndCapabilityGates);
test("recorder captures, flushes, encodes and releases the audio graph", capturesAndFlushes);
test("permission, context resume and worklet failures release resources", permissionAndContextFailures);
test("late permission rejection after cancellation stays stale", stalePermissionRejection);
test("cancel during worklet load prevents stale node construction", cancelDuringWorkletLoad);
test("flush timeout and inactive finish fail with resource cleanup", finishTimeoutCleansUp);
test("release respects an already closed AudioContext", releaseRespectsClosedContext);
test("AudioWorklet batches samples, stops silently and enforces its duration cap",
  audioWorkletFlushesBatchesAndStopsAtLimit);
