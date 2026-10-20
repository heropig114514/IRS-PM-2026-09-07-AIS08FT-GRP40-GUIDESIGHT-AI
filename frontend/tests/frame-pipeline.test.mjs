/** Responsibilities: Verify bounded capture admission, ordered display and honest latency accounting.
 * Implementation: Deferred transport/display fixtures and an explicit monotonic test clock.
 * Related Modules: frame-pipeline.js; actual socket/GPU benchmarks are separate.
 * Declaration Index: deferred; deferred.register; flush; fixture; fixture.capture;
 * fixture.display; fixture.frame; fixture.sent; fixture.active; fixture.failed;
 * fixture.now; fixture.client.sendFrame; fixture.client.stop;
 * pacingFixture; pacingFixture.capture; pacingFixture.display; pacingFixture.frame;
 * pacingFixture.sent; pacingFixture.active; pacingFixture.failed; pacingFixture.now;
 * pacingFixture.pause; pacingFixture.client.sendFrame; pacingFixture.client.stop;
 * testBound; testLatency; testFailure; testEnd; testCaptureStop; testPacing; testPacingStall;
 * testCreditPacing; testTwelveBound; testThirtyPacing; testCaptureTimestamp.
 * Variable Index: None. Fixture state is local to each test; no camera/network/GPU is used.
 */
import assert from "node:assert/strict";
import test from "node:test";
import {setImmediate} from "node:timers/promises";
import {FramePipeline} from "../frame-pipeline.js";

/** Functionality: Expose one controlled completion. Inputs: None. Outputs: promise/resolve/reject.
 * Logic: caller controls timing. Constraints: never substitutes a real external operation. */
function deferred() {
  let resolve, reject;
  /** Functionality: Store resolvers. Inputs: yes/no. Outputs: closure state.
   * Logic: no automatic completion. Constraints: observed by the pipeline/test. */
  function register(yes, no) { resolve = yes; reject = no; }
  return {promise: new Promise(register), resolve, reject};
}
/** Functionality: Complete queued microtasks. Inputs: None. Outputs: next event-loop turn.
 * Logic: immediate yield. Constraints: not a real network timing measurement. */
async function flush() { await setImmediate(); }

/** Functionality: Inject explicit clock, capture, display and deferred transport.
 * Inputs: optional frameLimit and maxPending (five for the original-window control).
 * Outputs: pipeline/state. Logic: explicitly retain12FPS for the old-cadence tests; capture advances100ms,
 * exceeding default cadence so these admission tests need no synthetic pacing timer.
 * Constraints: marker bytes and deferred display do not exercise JPEG/model/browser APIs. */
function fixture(frameLimit = null, maxPending = 5) {
  const state = {time: 0, captures: 0, requests: [], frames: [], errors: [], stops: 0, gate: null};
  /** Functionality: Simulate capture. Inputs: test state. Outputs: unique bytes.
   * Logic: advance100ms. Constraints: no physical sensor or encoding. */
  async function capture() { state.captures++; state.time += 100; return new ArrayBuffer(5); }
  /** Functionality: Simulate serial display. Inputs: fixture bytes. Outputs: optional gate.
   * Logic: advance15ms after gate. Constraints: no image decode/paint. */
  async function display() { if (state.gate) await state.gate.promise; state.time += 15; }
  /** Functionality: Record every completed timing. Inputs: result/timing. Outputs: frame list.
   * Logic: retain slow frames. Constraints: test history only, production uses bounded totals. */
  function frame(result, timing) { state.frames.push(timing); }
  /** Functionality: Observe send notification. Inputs: counters. Outputs: None.
   * Logic: actual request fixture records sends. Constraints: no assertions hidden here. */
  function sent() {}
  /** Functionality: Keep session generation active. Inputs: None. Outputs: true.
   * Logic: explicit pipeline stop governs this fixture. Constraints: no UI generation test. */
  function active() { return true; }
  /** Functionality: Record failure. Inputs: error. Outputs: errors list.
   * Logic: no restart. Constraints: no actual logging/network. */
  function failed(error) { state.errors.push(error); }
  /** Functionality: Read test clock. Inputs: state. Outputs: milliseconds.
   * Logic: explicit elapsed time. Constraints: not wall-clock benchmarking. */
  function now() { return state.time; }
  const client = {
    /** Functionality: Record one deferred request. Inputs: bytes. Outputs: result promise.
     * Logic: caller completes it. Constraints: no response skipping/retry. */
    sendFrame(bytes, options) { const request = deferred(); request.options = options; state.requests.push(request); return request.promise; },
    /** Functionality: Record transport stop. Inputs: None. Outputs: stop count.
     * Logic: no implicit completion. Constraints: resource release tested over real sockets elsewhere. */
    stop() { state.stops++; },
  };
  return {state, pipeline: new FramePipeline({client, capture, display, onFrame: frame,
    onSent: sent, isActive: active, onFailure: failed, now, frameLimit, maxPending, rate: 12})};
}

/** Functionality: Verify transport receives the source timestamp before asynchronous JPEG encoding.
 * Inputs: controlled100ms encode cost and actual pipeline. Outputs:0ms source versus100ms encode-complete.
 * Logic: timestamp travels through the explicit sendFrame option; Constraints: not a sensor-exposure measurement. */
test("local transport receives capture time before encoding completes", async function testCaptureTimestamp() {
  const {pipeline, state} = fixture(1);
  const running = pipeline.run(); await flush();
  assert.equal(state.requests[0].options.capturedAt, 0); assert.equal(state.time, 100);
  state.requests[0].resolve({bytes: new ArrayBuffer(5)}); await running;
  assert.equal(state.frames[0].capturedAt, 0); assert.equal(state.frames[0].encodedAt, 100);
});

/** Functionality: Simulate ordinary timer oversleep or a blocked credit using an explicit clock.
 * Inputs: frameLimit, oversleep list (last value repeats), holdSteady transport flag,
 * rate (explicit12FPS default retains the historical scheduling controls).
 * Outputs: pipeline/state with capture starts, all timings and deferred requests.
 * Logic: default12FPS preserves original drift-test conditions; an explicit rate permits30FPS;
 * capture takes8ms;
 * pause advances requested time plus injected lateness; the first frame
 * returns immediately and later results can be held for a controlled five-credit boundary.
 * Constraints: tests scheduling invariants, not real timer/network/GPU performance; no real sleeps. */
function pacingFixture(frameLimit, oversleep = [10], holdSteady = false, rate = 12) {
  const state = {time: 0, starts: [], frames: [], requests: [], waits: 0};
  /** Functionality: Record a capture start. Inputs: clock state. Outputs: marker bytes.
   * Logic: add8ms encode cost. Constraints: no real codec. */
  async function capture() { state.starts.push(state.time); state.time += 8; return new ArrayBuffer(5); }
  /** Functionality: Complete synthetic display. Inputs: None. Outputs: resolved Promise.
   * Logic: no additional clock delay. Constraints: not a decoder measurement. */
  async function display() {}
  /** Functionality: Retain completed timings. Inputs: result/timing. Outputs: frame history.
   * Logic: no filtering. Constraints: only synthetic scheduling observations. */
  function frame(result, timing) { state.frames.push(timing); }
  /** Functionality: Accept submission notifications. Inputs: None. Outputs: None.
   * Logic: requests/starts are recorded elsewhere. Constraints: no hidden side effects. */
  function sent() {}
  /** Functionality: Keep the fixture active. Inputs: None. Outputs: true.
   * Logic: explicit finite count/stop bounds execution. Constraints: no real media generation. */
  function active() { return true; }
  /** Functionality: Leave failure to run's rejected Promise. Inputs: error. Outputs: None.
   * Logic: no retry or recovery. Constraints: a test rejection remains visible. */
  function failed(error) {}
  /** Functionality: Read the controlled clock. Inputs: state. Outputs: milliseconds.
   * Logic: timer/capture advances are explicit. Constraints: not wall-clock timing. */
  function now() { return state.time; }
  const client = {
    /** Functionality: Return the first result or hold subsequent transport completions.
     * Inputs: bytes. Outputs: result Promise. Logic: deferred requests are explicitly released.
     * Constraints: no actual socket, retries or dropped results. */
    sendFrame(bytes) {
      if (!holdSteady || state.starts.length === 1) return Promise.resolve({bytes});
      const request = deferred(); state.requests.push(request); return request.promise;
    },
    /** Functionality: Represent the client stop boundary. Inputs: None. Outputs: None.
     * Logic: fixture has no external resources. Constraints: no hidden completion. */
    stop() {},
  };
  const pipeline = new FramePipeline({client, capture, display, onFrame: frame, onSent: sent,
    isActive: active, onFailure: failed, now, frameLimit, maxPending: 5, rate});
  /** Functionality: Inject deterministic timer oversleep. Inputs: milliseconds.
   * Outputs: advanced clock. Logic: a positive wait advances time and yields an event-loop turn,
   * allowing already-resolved transport/display microtasks to release their credits as a timer does.
   * Constraints: overrides pacing's timer boundary only; admission/render code remains real. */
  async function pause(milliseconds) {
    if (milliseconds > 0) {
      state.time += milliseconds + oversleep[Math.min(state.waits++, oversleep.length - 1)];
      await flush();
    }
  }
  pipeline.pause = pause;
  return {pipeline, state};
}

/** Functionality: Verify the new default ceiling still includes queued display and blocks capture.
 * Inputs: thirteen controlled results and an explicit twelve-job fixture; Outputs: bound/order counts.
 * Logic: cold first frame alone, then twelve accepted jobs; no thirteenth outstanding capture.
 * Constraints: synthetic clock/transport, no throughput/GPU claim; old five-window tests retained. */
test("twelve-credit ceiling includes all queued display work", async function testTwelveBound() {
  const {pipeline, state} = fixture(13, 12), running = pipeline.run();
  await flush(); assert.equal(state.captures, 1);
  state.requests[0].resolve({bytes: new ArrayBuffer(5)}); await flush();
  assert.equal(state.captures, 13); assert.equal(pipeline.pending.size, 12);
  state.gate = deferred();
  for (const request of state.requests.slice(1)) request.resolve({bytes: new ArrayBuffer(5)});
  await flush(); assert.equal(pipeline.pending.size, 12); assert.equal(pipeline.received, 1);
  state.gate.resolve();
  assert.deepEqual(await running, {sent: 13, received: 13, maxInFlight: 12});
  assert.deepEqual(state.frames.map(row => row.ordinal), Array.from({length: 13}, (_, index) => index + 1));
  assert.throws(() => new FramePipeline({maxPending: 13}), /采样配置/);
  assert.equal(new FramePipeline({}).rate, 30);
  assert.equal(new FramePipeline({}).maxPending, 12);
});

/** Functionality: Verify30FPS cadence preserves phase despite ordinary timer oversleep.
 * Inputs:31 captures,8ms encode cost and1ms timer oversleep in a controlled clock.
 * Outputs: complete ordered results and approximately33.33ms capture intervals.
 * Logic: measure after the independent first frame; fixed12FPS historical controls remain intact.
 * Constraints: verifies scheduling arithmetic, not camera throughput or real HF performance. */
test("30FPS cadence absorbs ordinary timer oversleep", async function testThirtyPacing() {
  const {pipeline, state} = pacingFixture(31, [1], false, 30);
  assert.deepEqual(await pipeline.run(), {sent: 31, received: 31, maxInFlight: 1});
  const starts = state.starts.slice(1), interval = (starts.at(-1) - starts[0]) / (starts.length - 1);
  assert.ok(Math.abs(interval - 1000 / 30) < 0.1);
  assert.equal(state.frames.length, 31);
});

/** Functionality: Prevent ordinary timer oversleep from accumulating over many frames.
 * Inputs:61 frames,8ms capture,10ms timer oversleep. Outputs: planned average cadence and delivery.
 * Logic: retain startup and first lateness; later starts stay one interval apart.
 * Constraints: deterministic timer control; does not prove real browser FPS. */
test("ordinary timer oversleep does not accumulate across steady captures", async function testPacing() {
  const {pipeline, state} = pacingFixture(61);
  const summary = await pipeline.run();
  assert.equal(summary.received, 61);
  const elapsed = state.starts.at(-1) - state.starts[2];
  assert.ok(Math.abs(elapsed - 58 * 1000 / 12) < 1e-6);
  assert.ok(state.frames.slice(2).every(row => Math.abs(row.pacingLateMs - 10) < 1e-6));
  assert.ok(state.frames.every(row => row.creditWaitMs === 0));
});

/** Functionality: Reanchor after a missed whole interval without replaying overdue frames.
 * Inputs: one100ms oversleep followed by10ms ordinary lateness. Outputs: no catch-up burst.
 * Logic: next capture waits a full interval after the stall, then normal cadence resumes.
 * Constraints: all frames remain counted; no slow-frame exclusion or throughput claim. */
test("a full-interval timer stall resets cadence without a catch-up burst", async function testPacingStall() {
  const {pipeline, state} = pacingFixture(8, [100, 10]);
  await pipeline.run();
  assert.ok(Math.abs(state.starts[3] - state.starts[2] - (1000 / 12 + 10)) < 1e-6);
  for (let index = 4; index < state.starts.length; index++)
    assert.ok(Math.abs(state.starts[index] - state.starts[index - 1] - 1000 / 12) < 1e-6);
  assert.equal(state.frames.length, 8);
});

/** Functionality: Preserve cadence reset even for a credit wait shorter than one interval.
 * Inputs: five held steady results and a20ms admission stall. Outputs: bound/order/full next wait.
 * Logic: release one display credit explicitly, verify the next deadline starts at actual capture.
 * Constraints: explicit five-window control under the twelve-credit ceiling; ordered display unchanged. */
test("credit stalls reset cadence even below one interval", async function testCreditPacing() {
  const {pipeline, state} = pacingFixture(8, [0], true), running = pipeline.run();
  while (state.starts.length < 6 || pipeline.pending.size < 5) await flush();
  await flush(); assert.equal(state.starts.length, 6); assert.equal(pipeline.pending.size, 5);
  state.time += 20; state.requests[0].resolve({bytes: new ArrayBuffer(5)});
  await flush(); assert.equal(state.starts.length, 7);
  for (const request of state.requests) request.resolve({bytes: new ArrayBuffer(5)});
  await flush();
  for (const request of state.requests) request.resolve({bytes: new ArrayBuffer(5)});
  assert.equal((await running).received, 8);
  assert.ok(Math.abs(state.starts[7] - state.starts[6] - 1000 / 12) < 1e-6);
  assert.ok(state.frames[6].creditWaitMs >= 20);
  assert.deepEqual(state.frames.map(row => row.ordinal), [1, 2, 3, 4, 5, 6, 7, 8]);
});

/** Functionality: Ensure no cold-start backlog and no capture without a display credit.
 * Inputs: six controlled results and blocked display. Outputs: capture/order/bound assertions.
 * Logic: first alone; five later jobs held until display releases credit.
 * Constraints: synthetic timing, no throughput claim. */
test("cold first frame and five credits include queued display", async function testBound() {
  const {pipeline, state} = fixture(6), running = pipeline.run();
  await flush(); assert.equal(state.captures, 1);
  state.requests[0].resolve({bytes: new ArrayBuffer(5)}); await flush();
  assert.equal(state.captures, 6); assert.equal(pipeline.pending.size, 5);
  state.gate = deferred();
  for (const request of state.requests.slice(1)) request.resolve({bytes: new ArrayBuffer(5)});
  await flush(); assert.equal(pipeline.pending.size, 5); assert.equal(pipeline.received, 1);
  state.gate.resolve();
  const summary = await running;
  assert.deepEqual(summary, {sent: 6, received: 6, maxInFlight: 5});
  assert.deepEqual(state.frames.map(frame => frame.ordinal), [1, 2, 3, 4, 5, 6]);
});

/** Functionality: Retain a600ms steady stall and serial-render queue time.
 * Inputs: explicit clock/network/display waits. Outputs: full latency exceeds500ms.
 * Logic: latency starts before capture; queue delay belongs to decoded latency.
 * Constraints: negative control for accounting, not measured network performance. */
test("steady stalls and render queue are included in latency", async function testLatency() {
  const {pipeline, state} = fixture(3), running = pipeline.run();
  await flush(); state.requests[0].resolve({bytes: new ArrayBuffer(5)}); await flush();
  state.time += 600; state.gate = deferred();
  for (const request of state.requests.slice(1)) request.resolve({bytes: new ArrayBuffer(5)});
  await flush(); state.time += 80; state.gate.resolve(); await running;
  assert.equal(state.frames.length, 3);
  assert.ok(state.frames[1].captureToResultMs >= 700);
  assert.ok(state.frames[2].captureToDecodedMs > 500);
  assert.ok(state.frames[2].renderQueueMs >= 95);
});

/** Functionality: Preserve original failure and prevent extra captures/retries.
 * Inputs: explicit transport rejection. Outputs: one error/stop and rejected run.
 * Logic: first error wins. Constraints: mocked transport failure only. */
test("transport failure stops admission and is reported once", async function testFailure() {
  const {pipeline, state} = fixture(), running = pipeline.run();
  const rejected = assert.rejects(running, /fixture failure/);
  await flush(); state.requests[0].reject(new Error("fixture failure")); await rejected;
  assert.equal(state.errors.length, 1); assert.equal(state.stops, 1); assert.equal(state.captures, 1);
});

/** Functionality: Drain accepted display on normal end while retaining incomplete counters.
 * Inputs: one returned and one unfinished steady frame. Outputs: sent3/received2, no failure.
 * Logic: finish prevents new captures; only local explicit session-end cancellation is benign.
 * Constraints: does not claim an unfinished frame was delivered or a GPU task was cancelled. */
test("normal end drains valid frames without counting unresolved input", async function testEnd() {
  const {pipeline, state} = fixture(3), running = pipeline.run();
  await flush(); state.requests[0].resolve({bytes: new ArrayBuffer(5)}); await flush();
  state.gate = deferred(); state.requests[1].resolve({bytes: new ArrayBuffer(5)}); await flush();
  pipeline.finish();
  const ended = new Error("ended"); ended.name = "VideoSessionEnded";
  state.requests[2].reject(ended); await flush(); assert.equal(pipeline.received, 1);
  state.gate.resolve(); assert.deepEqual(await running, {sent: 3, received: 2, maxInFlight: 2});
  assert.deepEqual(state.errors, []);
});

/** Functionality: Suppress a late capture after explicit stop.
 * Inputs: deferred capture. Outputs: no send or frame, stopped result.
 * Logic: capture cannot be cancelled, so generation is checked when it returns.
 * Constraints: stop's UI/resource release is the controller's responsibility. */
test("stop during capture never submits the late frame", async function testCaptureStop() {
  const {pipeline, state} = fixture(), capture = deferred();
  pipeline.capture = () => capture.promise;
  const running = pipeline.run(); await flush(); pipeline.stop();
  capture.resolve(new ArrayBuffer(5)); assert.equal((await running).sent, 0);
  assert.equal(state.requests.length, 0);
});
