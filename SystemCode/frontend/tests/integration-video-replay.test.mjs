/** Responsibilities: Verify continuous-source admission, clock identity, EOF drain and explicit cancellation.
 * Implementation: Native-receiver-sensitive media/canvas/image fixtures, deferred encoding/transport/decode,
 * actual FramePipeline.dispatch/drain; no camera, network, GPU or hardware output.
 * Related Modules: integration/video-replay.js/video-mvp.js; root check_local_video_browser verifies actual MP4/native decoder.
 * Declaration Index: deferred; flush; Media; Media.constructor; Media.load; Media.play; Media.pause;
 * Media.removeAttribute; Media.requestVideoFrameCallback; Media.cancelVideoFrameCallback; Media.present;
 * Canvas; Canvas.constructor; Canvas.getContext; Canvas.getContext.drawImage; Canvas.toBlob; Image; Image.constructor; Image.decode;
 * fixture; fixture.now; fixture.createUrl; fixture.revokeUrl; fixture.clientFactory; fixture.pipelineFactory;
 * fixture.clientFactory.connect; fixture.clientFactory.sendFrame; fixture.clientFactory.stop;
 * load; response; testFileOnly; testDrawClock; testFirstExclusive; testTotalWindow; testEof;
 * testStopEncode; testStopDecode; testProviderDuringEof; testProviderPending; testPts; testMutation;
 * testPlayFailure; testLoadCancel; testLoadDispose; testDecodeFailure; testOldSession;
 * testNativeEof; testCadence; testEncodeFailure;
 * Variable Index: None; each fixture has independent state and explicit synthetic JPEG markers.
 */
import test from "node:test";
import assert from "node:assert/strict";
import {setImmediate} from "node:timers/promises";
import {FramePipeline} from "../frame-pipeline.js";
import {VideoReplay} from "../integration/video-replay.js";

/** Functionality: Create one controlled completion. Inputs: None. Outputs: promise/resolve/reject.
 * Logic: explicit test control; Constraints: not a real decoder or transport. */
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return {promise, resolve, reject}; }
/** Functionality: Flush observed asynchronous work. Inputs: None. Outputs: event-loop yield.
 * Logic: Blob.arrayBuffer and promise chains settle; Constraints: not a latency measurement. */
async function flush() { await setImmediate(); await setImmediate(); }
/** Functionality: Provide media events and receiver-sensitive native-style methods.
 * Logic: explicit presentations only; Constraints: no pixels, real codec, camera or automatic playback callback. */
class Media extends EventTarget {
  /** Functionality: Initialize media state. Inputs: None. Outputs: isolated controls/callbacks.
   * Logic:1x/no loop; Constraints: source is synthetic. */
  constructor() { super(); Object.assign(this, {playbackRate: 1, defaultPlaybackRate: 1, loop: false, seeking: false,
    videoWidth: 1280, videoHeight: 720, duration: 10, ended: false, callbacks: new Map(), counter: 0, plays: 0, pauses: 0, loads: 0}); }
  /** Functionality: Record explicit source loading. Inputs: receiver. Outputs: count.
   * Logic: require Media receiver; Constraints: test fires metadata independently. */
  load() { assert.ok(this instanceof Media); this.loads++; this.ended = false; }
  /** Functionality: Record requested playback. Inputs: receiver. Outputs: optional rejected native promise.
   * Logic: no source correction; Constraints: no real playback. */
  async play() { assert.ok(this instanceof Media); this.plays++; if (this.playError) throw this.playError; }
  /** Functionality: Record pause. Inputs: receiver. Outputs: count.
   * Logic: require proper native receiver; Constraints: no real device. */
  pause() { assert.ok(this instanceof Media); this.pauses++; }
  /** Functionality: Release source attribute. Inputs: name. Outputs: empty src.
   * Logic: source cleanup assertion; Constraints: only src supported. */
  removeAttribute(name) { assert.ok(this instanceof Media); assert.equal(name, "src"); this.src = ""; }
  /** Functionality: Register native-style callback. Inputs: callback. Outputs: ID.
   * Logic: one pending registration observed; Constraints: no timer-generated frames. */
  requestVideoFrameCallback(callback) { assert.ok(this instanceof Media); const id = ++this.counter; this.callbacks.set(id, callback); return id; }
  /** Functionality: Cancel presentation callback. Inputs: ID. Outputs: removal.
   * Logic: require receiver; Constraints: no implicit resume. */
  cancelVideoFrameCallback(id) { assert.ok(this instanceof Media); this.callbacks.delete(id); }
  /** Functionality: Present one explicit frame. Inputs: monotonic callback time/mediaPTS/count. Outputs: registered callback effects.
   * Logic: clear registration before calling; Constraints: frames are source observations, not genuine video pixels. */
  present(now, mediaTime, presentedFrames) { const [id, callback] = [...this.callbacks.entries()][0] || []; assert.ok(callback); this.callbacks.delete(id); callback(now, {mediaTime, presentedFrames}); }
}
/** Functionality: Track original dimensions/quality with deferred encoding.
 * Logic: context acquisition may cost time; Constraints: Blob is a synthetic JPEG marker, not codec validation. */
class Canvas {
  /** Functionality: Initialize encoder fixture. Inputs: state. Outputs: dimensions/jobs/draws.
   * Logic: native method receiver tracked; Constraints: no pixels allocated. */
  constructor(state) { Object.assign(this, {state, width: 0, height: 0, jobs: [], draws: []}); }
  /** Functionality: Acquire native-style drawing context. Inputs: kind. Outputs: context.
   * Logic: explicit acquisition cost before actual capture timestamp; Constraints: no real drawing. */
  getContext(kind) {
    assert.ok(this instanceof Canvas); assert.equal(kind, "2d"); this.state.time += this.state.contextCost || 0;
    const canvas = this;
    return {
      /** Functionality: Record drawing with correct context receiver. Inputs: source/rectangle. Outputs: capture record.
       * Logic: independent actual draw clock; Constraints: no video pixels decoded. */
      drawImage(video, x, y, w, h) { assert.ok(this.drawImage); canvas.draws.push({video, x, y, w, h, at: canvas.state.time}); }};
  }
  /** Functionality: Register deferred JPEG callback. Inputs: callback/type/quality. Outputs: encoding job.
   * Logic: preserve exact caller parameters; Constraints: no native toBlob scheduling claims. */
  toBlob(callback, type, quality) {
    assert.ok(this instanceof Canvas); assert.equal(type, "image/jpeg"); assert.equal(quality, 0.8);
    this.jobs.push({complete: () => callback(new Blob([Uint8Array.of(255, 216, 1, 255, 217)], {type})), fail: () => callback(null)});
  }
}
/** Functionality: Provide receiver-sensitive deferred annotation decode.
 * Logic: decodes are explicit; Constraints: not physical paint. */
class Image {
  /** Functionality: Initialize dimensions/decode jobs. Inputs: None. Outputs: isolated state.
   * Logic: dimensions match the original scaled frame; Constraints: no JPEG parsing. */
  constructor() { this.naturalWidth = 640; this.naturalHeight = 360; this.jobs = []; this.hidden = true; }
  /** Functionality: Await controlled decode. Inputs: receiver. Outputs: deferred promise.
   * Logic: proper native receiver required; Constraints: not browser codec performance. */
  decode() { assert.ok(this instanceof Image); const job = deferred(); this.jobs.push(job); return job.promise; }
}
/** Functionality: Construct one replay with real pipeline but explicit native/transport fixtures.
 * Inputs: None. Outputs: controller/source/state. Logic: count factory calls and keep per-session errors/results.
 * Constraints: no getUserMedia surface is supplied, so accidental camera use cannot pass. */
function fixture() {
  const state = {time: 100, urls: [], revoked: [], events: [], clients: [], contextCost: 0};
  const video = new Media(), canvas = new Canvas(state), image = new Image();
  /** Functionality: Read test capture clock. Inputs: state. Outputs: milliseconds.
   * Logic: independent of source mediaPTS; Constraints: only explicitly advanced. */
  function now() { return state.time; }
  /** Functionality: Create owned Blob identity. Inputs: Blob. Outputs: synthetic URL.
   * Logic: unique IDs; Constraints: never reads/uploads the original Blob. */
  function createUrl(blob) { assert.ok(blob instanceof Blob); const url = `blob:fixture-${state.urls.length}`; state.urls.push(url); return url; }
  /** Functionality: Record URL release. Inputs: URL. Outputs: release log.
   * Logic: exact owner asserted in tests; Constraints: no browser resource measurement. */
  function revokeUrl(url) { state.revoked.push(url); }
  /** Functionality: Provide one deferred local transport. Inputs: URL/fault/end callbacks. Outputs: client.
   * Logic: session/source timestamp recorded; Constraints: no WS/GPU/protocol substitutes advertised as real. */
  function clientFactory(url, failed, ended) {
    assert.equal(url, "ws://localhost:8766/ws/detect/local/");
    const client = {requests: [], stops: 0, failed, ended,
      /** Functionality: Accept one local session fixture. Inputs: None. Outputs: unique ID.
       * Logic: no handshake model validation in this unit; Constraints: protocol validation has separate tests. */
      async connect() { return {session_id: `session-${state.clients.length}`, provider: "local"}; },
      /** Functionality: Keep one deferred request. Inputs: bytes/options. Outputs: result promise.
       * Logic: explicit source timestamp; Constraints: no inferred mediaPTS or response skipping. */
      sendFrame(bytes, options) { const request = deferred(); request.bytes = bytes; request.options = options; this.requests.push(request); return request.promise; },
      /** Functionality: Record explicit transport cleanup. Inputs: None. Outputs: count.
       * Logic: no automatic reconnect; Constraints: rejected network work is controlled by tests. */
      stop() { this.stops++; }};
    state.clients.push(client); return client;
  }
  /** Functionality: Reuse actual dispatch/drain pipeline. Inputs: adapters. Outputs: real FramePipeline.
   * Logic: run() is never called; Constraints: no camera capture/rate modification. */
  function pipelineFactory(options) { return new FramePipeline(options); }
  const replay = new VideoReplay({video, canvas, image, clientFactory, pipelineFactory, href: "http://localhost:8766/integration/video/", now, createUrl, revokeUrl});
  replay.subscribe(event => state.events.push(event));
  return {state, video, canvas, image, replay};
}
/** Functionality: Explicitly select metadata-ready fixture file. Inputs: fixture. Outputs: loaded controller.
 * Logic: metadata event follows selection; Constraints: no playback is started. */
async function load(f) { const operation = f.replay.loadFile(new Blob(["video"], {type: "video/mp4"})); f.video.dispatchEvent(new Event("loadedmetadata")); await operation; }
/** Functionality: Complete transport then annotation decode. Inputs: fixture/index. Outputs: ordered result event.
 * Logic: the actual pipeline handles dispatch/render; Constraints: metadata is minimal synthetic unit input. */
async function response(f, index) {
  f.state.clients.at(-1).requests[index].resolve({bytes: new ArrayBuffer(5), metadata: {width: 640, height: 360, index, boxes: []}});
  await flush(); f.image.jobs.at(-1).resolve(); await flush();
}
/** Functionality: Verify selection is local decode only. Inputs: fixture Blob. Outputs: no transport/playback.
 * Logic: factory called only on start; Constraints: real MP4 codec verified separately. */
test("file metadata selection opens no camera, playback or inference", async function testFileOnly() {
  const f = fixture(); await load(f);
  assert.equal(f.video.plays, 0); assert.equal(f.state.clients.length, 0); assert.equal(f.video.callbacks.size, 0);
  assert.equal(f.state.events[0].type, "loaded"); assert.equal(f.state.events[0].source, "local_video"); f.replay.dispose();
});
/** Functionality: Verify native receivers and actual draw timestamp versus PTS/encode completion.
 * Inputs:7ms context cost then delayed encoder. Outputs:107ms capture versus9s sourcePTS/900ms send.
 * Logic: timestamp is bound before draw; Constraints: marker codec is synthetic. */
test("capture timestamp is actual draw clock, not media PTS or encode completion", async function testDrawClock() {
  const f = fixture(); await load(f); await f.replay.start(); f.state.contextCost = 7;
  f.video.present(100, 9, 1); assert.equal(f.canvas.draws[0].at, 107); assert.equal(f.canvas.width, 640); assert.equal(f.canvas.height, 360);
  f.state.time = 900; f.canvas.jobs[0].complete(); await flush();
  assert.equal(f.replay.client.requests[0].options.capturedAt, 107);
  await response(f, 0);
  const frame = f.state.events.find(event => event.type === "frame"); assert.equal(frame.capturedAt, 107); assert.equal(frame.sourceMediaTime, 9);
  assert.equal(frame.timing.captureMs, 793); f.replay.stop();
});
/** Functionality: Verify cold first response holds source admission only.
 * Inputs: advancing mediaPTS/count while first response waits. Outputs: one capture, explicit uncaptured counts.
 * Logic: source continues rather than repeating held image; Constraints: not actual decoder timing. */
test("first frame is exclusive while source PTS keeps advancing", async function testFirstExclusive() {
  const f = fixture(); await load(f); await f.replay.start(); f.video.present(100, 0, 1);
  f.canvas.jobs[0].complete(); await flush(); f.state.time = 2000; f.video.present(2000, 1.9, 58);
  assert.equal(f.canvas.jobs.length, 1); assert.equal(f.replay.stats.skippedFirstFrame, 1); assert.equal(f.replay.stats.callbackGaps, 56);
  await response(f, 0); f.state.time = 2034; f.video.present(2034, 1.934, 59);
  assert.equal(f.canvas.jobs.length, 2); assert.equal(f.replay.stats.firstSourceMediaTime, 0); f.replay.stop(); f.canvas.jobs[1].complete(); await flush();
});
/** Functionality: Verify12-window includes unencoded work and preserves encoded admission order.
 * Inputs: stalled encoders, out-of-order completions. Outputs: max12, no thirteenth capture and ordered send.
 * Logic: credit precedes canvas draw; Constraints: synthetic transport remains deferred. */
test("total window includes JPEG encoding and dispatch remains in capture order", async function testTotalWindow() {
  const f = fixture(); await load(f); await f.replay.start(); f.video.present(100, 0, 1); f.canvas.jobs[0].complete(); await flush(); await response(f, 0);
  for (let n = 1; n <= 13; n++) { f.state.time = 100 + n * 34; f.video.present(f.state.time, n / 30, n + 1); }
  assert.equal(f.canvas.jobs.length, 13); assert.equal(f.replay.stats.currentInFlight, 12); assert.equal(f.replay.stats.maxInFlight, 12); assert.equal(f.replay.stats.skippedCredit, 1);
  f.canvas.jobs[2].complete(); await flush(); assert.equal(f.replay.client.requests.length, 1);
  f.canvas.jobs[1].complete(); await flush(); assert.equal(f.replay.client.requests.length, 3);
  assert.ok(f.replay.client.requests[1].options.capturedAt < f.replay.client.requests[2].options.capturedAt);
  f.replay.stop(); for (const job of f.canvas.jobs.slice(3)) job.complete(); await flush();
});
/** Functionality: Verify natural EOF drains every admitted encoding/response/decode.
 * Inputs: EOF while encoder waits. Outputs: stop only after delivered completion.
 * Logic: no new presentation admission; Constraints: no auto-loop/reconnect. */
test("EOF sends already admitted JPEG and waits for full ordered delivery before stop", async function testEof() {
  const f = fixture(); await load(f); await f.replay.start(); f.video.present(100, 0, 1);
  const ending = f.replay.finish(); assert.equal(f.replay.mode, "draining"); assert.equal(f.video.callbacks.size, 0); assert.equal(f.replay.client.stops, 0);
  f.canvas.jobs[0].complete(); await flush(); assert.equal(f.replay.client.requests.length, 1); assert.equal(f.replay.client.stops, 0);
  await response(f, 0); await ending;
  assert.equal(f.replay.mode, "idle"); assert.equal(f.replay.stats.sent, 1); assert.equal(f.replay.stats.delivered, 1);
  assert.equal(f.state.events.at(-1).type, "ended"); assert.equal(f.state.events.at(-1).reason, "video_end"); assert.equal(f.state.clients.length, 1);
});
/** Functionality: Verify immediate user stop cancels queued encoder before sending.
 * Inputs: pending toBlob. Outputs: no late send/frame, cancelled count and callback cleanup.
 * Logic: active generation guard; Constraints: native encoder itself cannot be forcibly aborted. */
test("explicit stop does not send a JPEG that finishes encoding later", async function testStopEncode() {
  const f = fixture(); await load(f); await f.replay.start(); f.video.present(100, 0, 1); f.replay.stop("hidden");
  assert.equal(f.video.callbacks.size, 0); assert.equal(f.replay.mode, "idle"); f.canvas.jobs[0].complete(); await flush();
  assert.equal(f.replay.client.requests.length, 0); assert.equal(f.replay.stats.cancelledBeforeSend, 1); assert.equal(f.replay.stats.currentInFlight, 0);
  assert.equal(f.state.events.filter(event => event.type === "frame").length, 0);
});
/** Functionality: Verify late native decode cannot publish after stop.
 * Inputs: pending annotation decode. Outputs: no result lifecycle, released result URL.
 * Logic: real pipeline tests active around decode; Constraints: not physical paint cancellation. */
test("explicit stop isolates a late annotation decode", async function testStopDecode() {
  const f = fixture(); await load(f); await f.replay.start(); f.video.present(100, 0, 1); f.canvas.jobs[0].complete(); await flush();
  f.replay.client.requests[0].resolve({bytes: new ArrayBuffer(5), metadata: {width: 640, height: 360, index: 0}}); await flush();
  f.replay.stop(); f.image.jobs[0].resolve(); await flush();
  assert.equal(f.replay.stats.delivered, 0); assert.equal(f.state.events.filter(event => event.type === "frame").length, 0); assert.ok(f.state.revoked.includes("blob:fixture-1"));
});
/** Functionality: Verify provider-ended callback can seal an already-running EOF drain.
 * Inputs: encoder unresolved during EOF, then normal provider end. Outputs: canceled unsent frame without error.
 * Logic: end handling happens before duplicate-finish guard; Constraints: unresolved frames are not counted delivered. */
test("provider completion during EOF drain seals unsent encodes without changing normal end to error", async function testProviderDuringEof() {
  const f = fixture(); await load(f); await f.replay.start(); f.video.present(100, 0, 1);
  const ending = f.replay.finish("eof"); f.replay.client.ended(); f.canvas.jobs[0].complete(); await ending;
  assert.equal(f.replay.stats.sent, 0); assert.equal(f.replay.stats.cancelledBeforeSend, 1);
  assert.equal(f.state.events.at(-1).reason, "provider_ended"); assert.equal(f.state.events.filter(event => event.type === "error").length, 0);
});
/** Functionality: Verify normal budget end records pending delivery honestly.
 * Inputs: typed session-end rejection. Outputs: normal end, sent1/delivered0.
 * Logic: pipeline.finish precedes rejection observation; Constraints: no automatic provider restart. */
test("normal provider end drains valid work while unresolved session-ended frames stay undelivered", async function testProviderPending() {
  const f = fixture(); await load(f); await f.replay.start(); f.video.present(100, 0, 1); f.canvas.jobs[0].complete(); await flush();
  f.replay.client.ended(); f.replay.client.requests[0].reject(Object.assign(new Error("ended"), {name: "VideoSessionEnded"})); await flush();
  assert.equal(f.replay.mode, "idle"); assert.equal(f.replay.stats.sent, 1); assert.equal(f.replay.stats.delivered, 0);
  assert.equal(f.state.events.filter(event => event.type === "error").length, 0);
});
/** Functionality: Verify duplicated/backward PTS cannot become a new inference input.
 * Inputs: second callback with same mediaPTS. Outputs: explicit stop, no second draw.
 * Logic: source observation checked even without credit; Constraints: no replacement timestamp/repeated-frame fallback. */
test("duplicate source mediaPTS stops explicitly instead of replaying the same frame", async function testPts() {
  const f = fixture(); await load(f); await f.replay.start(); f.video.present(100, 0, 1); f.state.time = 140; f.video.present(140, 0, 2);
  assert.equal(f.replay.mode, "idle"); assert.equal(f.canvas.jobs.length, 1); assert.match(f.state.events.at(-1).message, /duplicated/);
  f.canvas.jobs[0].complete(); await flush();
});
/** Functionality: Verify prohibited native media mutations stop without repair.
 * Inputs: seeking/ratechange/loop changes. Outputs: source/transport stop and unchanged mutation.
 * Logic: explicit fault, no correction or retry; Constraints: not browser event sequencing verification. */
test("seeking, speed changes and loop mutations each stop without automatic correction", async function testMutation() {
  for (const kind of ["seek", "rate", "defaultRate", "loop"]) {
    const f = fixture(); await load(f); await f.replay.start();
    if (kind === "seek") { f.video.seeking = true; f.video.dispatchEvent(new Event("seeking")); }
    else if (kind === "rate") { f.video.playbackRate = 2; f.video.dispatchEvent(new Event("ratechange")); }
    else if (kind === "defaultRate") { f.video.defaultPlaybackRate = 2; f.video.dispatchEvent(new Event("ratechange")); }
    else { f.video.loop = true; f.video.present(100, 0, 1); }
    assert.equal(f.replay.mode, "idle"); assert.equal(f.state.events.at(-1).type, "error"); assert.equal(f.state.clients.length, 1); assert.equal(f.canvas.jobs.length, 0);
    if (kind === "rate") assert.equal(f.video.playbackRate, 2); if (kind === "loop") assert.equal(f.video.loop, true);
  }
});
/** Functionality: Verify native play failure is explicit and releases the accepted transport.
 * Inputs: rejected play promise. Outputs: no callback/inference retry.
 * Logic: connect admission does not imply decode/play success; Constraints: native codec separately tested. */
test("native playback rejection stops the accepted session without retry or fallback", async function testPlayFailure() {
  const f = fixture(); await load(f); f.video.playError = new Error("decode/play failed"); await f.replay.start();
  assert.equal(f.replay.mode, "idle"); assert.equal(f.replay.client.stops, 1); assert.equal(f.video.callbacks.size, 0); assert.equal(f.state.events.at(-1).type, "error");
});
/** Functionality: Verify explicit stop settles loadA before loadB and lateA cannot clearB.
 * Inputs: cancelled metadata wait then new selection. Outputs: exact-owner URL cleanup and onlyB loaded.
 * Logic: token+detached listener guard; Constraints: no Promise timeout used to hide an unresolved load. */
test("cancelled metadata loading settles and cannot mutate a new file selection", async function testLoadCancel() {
  const f = fixture(), a = f.replay.loadFile(new Blob(["A"])); const rejected = assert.rejects(a, {name: "VideoLoadCancelled"});
  f.replay.stop(); const b = f.replay.loadFile(new Blob(["B"])); await rejected;
  assert.equal(f.replay.objectUrl, "blob:fixture-1"); assert.equal(f.replay.file.size, 1); assert.deepEqual(f.state.revoked, ["blob:fixture-0"]);
  f.video.dispatchEvent(new Event("loadedmetadata")); await b;
  assert.equal(f.state.events.filter(event => event.type === "loaded").length, 1); assert.equal(f.replay.mode, "idle"); f.replay.dispose();
});
/** Functionality: Verify off-page loading is cancelled rather than resurrected.
 * Inputs: dispose before metadata. Outputs: settled waiter/revoked source/no later loaded or restart.
 * Logic: disposed is terminal; Constraints: no off-page task or automatic reopen. */
test("dispose during metadata wait detaches and settles without late loaded event", async function testLoadDispose() {
  const f = fixture(), operation = f.replay.loadFile(new Blob(["video"])); const rejected = assert.rejects(operation, {name: "VideoLoadCancelled"});
  f.replay.dispose(); await rejected; f.video.dispatchEvent(new Event("loadedmetadata")); await flush();
  assert.equal(f.replay.objectUrl, null); assert.equal(f.replay.file, null); assert.equal(f.state.events.filter(event => event.type === "loaded").length, 0);
  assert.deepEqual(f.state.revoked, ["blob:fixture-0"]); await assert.rejects(f.replay.start(), /disposed/); await assert.rejects(f.replay.loadFile(new Blob(["B"])), /disposed/);
});
/** Functionality: Verify decoder metadata faults release source resources.
 * Inputs: native error event. Outputs: explicit error, no loaded/client/codec fallback.
 * Logic: matching load owns cleanup; Constraints: real codec success is independent. */
test("native metadata decode error stays explicit and releases its own Blob URL", async function testDecodeFailure() {
  const f = fixture(), operation = f.replay.loadFile(new Blob(["invalid"])); const rejected = assert.rejects(operation, /metadata/);
  f.video.dispatchEvent(new Event("error")); await rejected;
  assert.equal(f.replay.file, null); assert.equal(f.replay.objectUrl, null); assert.equal(f.video.src, ""); assert.equal(f.state.clients.length, 0); assert.equal(f.state.events.at(-1).type, "error");
});
/** Functionality: Verify old transport observer cannot stop a new explicit session.
 * Inputs: restart then old disconnection. Outputs: new session stays active.
 * Logic: per-session observer closure guards generation; Constraints: manual continuation does not seek/replay old mediaPTS. */
test("late disconnect from a stopped session cannot cancel a new explicit session", async function testOldSession() {
  const f = fixture(); await load(f); await f.replay.start(); const old = f.replay.client; f.replay.stop(); await f.replay.start();
  old.failed(new Error("old disconnect")); assert.equal(f.replay.mode, "playing"); assert.equal(f.state.clients.length, 2); assert.equal(f.replay.active(), true); f.replay.stop();
});
/** Functionality: Verify actual ended-event wiring follows the EOF drain path.
 * Inputs: native-style ended event. Outputs: no callback admission and eof reason after delivery.
 * Logic: event listener delegates finish, never force-cancels admitted work; Constraints: decoder is still synthetic. */
test("native video ended event drains admitted work before reporting EOF", async function testNativeEof() {
  const f = fixture(); await load(f); await f.replay.start(); f.video.present(100, 0, 1);
  f.video.ended = true; f.video.dispatchEvent(new Event("ended")); assert.equal(f.replay.mode, "draining");
  f.canvas.jobs[0].complete(); await flush(); await response(f, 0); await flush();
  assert.equal(f.state.events.at(-1).reason, "eof"); assert.equal(f.replay.stats.sent, 1); assert.equal(f.replay.stats.delivered, 1);
  assert.equal(f.video.plays, 1); assert.equal(f.video.callbacks.size, 0);
});
/** Functionality: Verify native presentations faster than30FPS are observed without overcapture.
 * Inputs:60FPS presentation clock after first completion. Outputs: cadence skips preceding canvas work.
 * Logic: the fixed input ceiling uses browser clock rather than mediaPTS as a scheduling clock.
 * Constraints:30FPS is admission maximum, not achieved detection throughput. */
test("presentation sampling keeps the original30FPS ceiling before capture", async function testCadence() {
  const f = fixture(); await load(f); await f.replay.start(); f.video.present(100, 0, 1); f.canvas.jobs[0].complete(); await flush(); await response(f, 0);
  f.state.time = 116; f.video.present(116, 1 / 60, 2); f.state.time = 134; f.video.present(134, 2 / 60, 3);
  assert.equal(f.replay.stats.sourceCallbacks, 3); assert.equal(f.replay.stats.admitted, 2); assert.equal(f.replay.stats.skippedCadence, 1);
  assert.equal(f.replay.stats.observedPresentedFrames, f.replay.stats.admitted + f.replay.stats.uncapturedObservedFrames);
  f.replay.stop(); f.canvas.jobs[1].complete(); await flush();
});
/** Functionality: Verify admitted JPEG failure does not send or retry.
 * Inputs: null native toBlob result. Outputs: released encoding count and explicit error.
 * Logic: no alternative codec/source fallback; Constraints: this is a fault fixture, not real encoder validation. */
test("JPEG encoding failure releases the session without retry or inferred delivery", async function testEncodeFailure() {
  const f = fixture(); await load(f); await f.replay.start(); f.video.present(100, 0, 1); f.canvas.jobs[0].fail(); await flush();
  assert.equal(f.replay.mode, "idle"); assert.equal(f.replay.stats.currentInFlight, 0); assert.equal(f.replay.stats.sent, 0);
  assert.equal(f.state.clients.length, 1); assert.equal(f.state.events.at(-1).type, "error"); assert.match(f.state.events.at(-1).message, /encoding/);
});
