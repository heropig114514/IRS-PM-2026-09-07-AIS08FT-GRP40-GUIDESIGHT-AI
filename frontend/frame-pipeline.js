/** Responsibilities: Overlap capture/transmission and ordered display without a stale-frame backlog.
 * Implementation: Planned cadence with stall resets, first frame alone, max12 working/awaiting-display frames;
 * admission precedes capture; errors/stop wake waits; sent frames are never intentionally skipped.
 * Related Modules: camera.js, live-detection-client.js; tests/frame-pipeline.test.mjs.
 * Declaration Index: clockNow; FramePipeline; FramePipeline.constructor;
 * FramePipeline.constructor.registerStop; FramePipeline.constructor.registerCancel;
 * FramePipeline.active; FramePipeline.pause;
 * FramePipeline.pause.register; FramePipeline.pause.finished; FramePipeline.run;
 * FramePipeline.dispatch; FramePipeline.dispatch.received; FramePipeline.dispatch.received.render;
 * FramePipeline.dispatch.failed; FramePipeline.dispatch.finished; FramePipeline.drain;
 * FramePipeline.finish; FramePipeline.fail; FramePipeline.stop.
 * Variable Index: INPUT_FPS —30 default input cadence; instance pending/renderTail bound work/display;
 * stopPromise/wakeStop stop admission; cancelPromise/wakeCancel cancel draining;
 * pauseTimer cancels pacing, finishing prevents new captures; failure retains the original visible error;
 * sent/received/maxInFlight track all submitted/completed frames, including slow responses.
 * maxPending is the explicit capture/display admission bound, defaulting to MAX_INFLIGHT;
 * controlled comparisons can use a smaller window within the same advertised transport ceiling.
 * Per-frame scheduledCaptureAt/pacingRequestedMs/pacingWaitMs/pacingLateMs distinguish cadence
 * timer delay; creditWaitMs/pendingBeforeAdmission expose bounded admission without media logs.
 */
import {MAX_INFLIGHT} from "./live-detection-client.js";

export const INPUT_FPS = 30;

/** Functionality: Read monotonic time. Inputs: browser performance. Outputs: milliseconds.
 * Logic: single clock for capture/result/display. Constraints: not sensor exposure or paint time. */
function clockNow() { return performance.now(); }

/** Functionality: Manage a bounded live capture/transport/display pipeline.
 * Logic: pending includes network and render work; renderTail serializes display independently of capture.
 * Constraints: at most twelve total frames including capture; callbacks must use generation guards;
 * no model/codec changes, retries, auto-restart, captured-frame drops or hidden latency filtering. */
export class FramePipeline {
  /** Functionality: Bind frame operations and optional explicit benchmark cadence/count.
   * Inputs: client/capture/display/onFrame/onSent/isActive/onFailure, rate, frameLimit, now, maxPending.
   * Outputs: idle instance. Logic: store dependencies, initialize counters and cancellation signal.
   * Constraints: default30FPS is an input ceiling, not an achieved-output claim; finite frameLimit is
   * for controlled tests, null runs until stop/provider completion; maxPending must be1–12;
   * using five/eight is an explicit historical-window comparison, never an automatic downgrade. No I/O here. */
  constructor({client, capture, display, onFrame, onSent, isActive, onFailure,
    rate = INPUT_FPS, frameLimit = null, now = clockNow, maxPending = MAX_INFLIGHT}) {
    if (!Number.isFinite(rate) || rate < 1 || rate > 60 ||
        (frameLimit !== null && (!Number.isInteger(frameLimit) || frameLimit < 1)) ||
        !Number.isInteger(maxPending) || maxPending < 1 || maxPending > MAX_INFLIGHT)
      throw new Error("视频流水线采样配置无效。");
    this.client = client;
    this.capture = capture;
    this.display = display;
    this.onFrame = onFrame;
    this.onSent = onSent;
    this.isActive = isActive;
    this.onFailure = onFailure;
    this.now = now;
    this.rate = rate;
    this.frameLimit = frameLimit;
    this.maxPending = maxPending;
    this.pending = new Set();
    this.renderTail = Promise.resolve();
    this.sent = this.received = this.maxInFlight = 0;
    this.stopped = false;
    this.finishing = false;
    this.failure = null;
    this.pauseTimer = null;
    const pipeline = this;
    /** Functionality: Register the stop signal. Inputs: resolve. Outputs: wakeStop state.
     * Logic: one signal wakes pacing/admission/drain waits. Constraints: never rejects unobserved. */
    function registerStop(resolve) { pipeline.wakeStop = resolve; }
    this.stopPromise = new Promise(registerStop);
    /** Functionality: Register force-cancel signal. Inputs: resolve. Outputs: wakeCancel.
     * Logic: ordinary provider end drains display, explicit stop/error cancels immediately.
     * Constraints: signal never rejects or implies external GPU cancellation. */
    function registerCancel(resolve) { pipeline.wakeCancel = resolve; }
    this.cancelPromise = new Promise(registerCancel);
  }
  /** Functionality: Check this session still owns media/UI. Inputs: stopped/failure/isActive callback.
   * Outputs: boolean. Logic: caller generation is authoritative. Constraints: no auto-resume. */
  active() { return !this.stopped && !this.failure && this.isActive(); }
  /** Functionality: Wait only for the remaining start-to-start cadence.
   * Inputs: milliseconds. Outputs: Promise raced with stop.
   * Logic: delay is subtracted from work already elapsed; clear timer on completion/stop.
   * Constraints: does not add a fixed delay after every returned result. */
  async pause(milliseconds) {
    if (milliseconds <= 0 || !this.active()) return;
    const pipeline = this;
    /** Functionality: Complete pacing wait. Inputs: closure resolver/timer. Outputs: resolved Promise.
     * Logic: release timer reference. Constraints: no new frame is captured here. */
    function finished(resolve) { pipeline.pauseTimer = null; resolve(); }
    /** Functionality: Register short cadence timer. Inputs: resolve. Outputs: pauseTimer.
     * Logic: single outstanding timer. Constraints: stop clears it and wakes the race. */
    function register(resolve) {
      pipeline.pauseTimer = setTimeout(finished, milliseconds, resolve);
    }
    await Promise.race([new Promise(register), this.stopPromise]);
  }
  /** Functionality: Capture only when a slot and cadence permit, then independently render results.
   * Inputs: stored operations/rate/frameLimit. Outputs: counters or original failure.
   * Logic: first frame completes alone; then permit maxPending total jobs. Keep the planned timeline
   * across sub-interval timer lateness, so ordinary timer oversleep does not accumulate every frame.
   * Reanchor after credit waits or lateness of at least one interval; never replay missed slots.
   * Finite tests drain every submitted frame; capture/transport/display retain their existing limits.
   * Record requested/actual pacing and credit waits separately before capture; diagnostics add no
   * additional waits and do not alter admission, measured latency or the existing acceptance rules.
   * Constraints: stopping returns without waiting for an uncancellable browser decode; all late
   * callbacks test active() before UI; failure is retained/reported, not changed into success. */
  async run() {
    let nextCaptureAt = this.now();
    try {
      while (this.active() && !this.finishing && (this.frameLimit === null || this.sent < this.frameLimit)) {
        const scheduledCaptureAt = nextCaptureAt, pacingStartedAt = this.now();
        const pacingRequestedMs = Math.max(0, scheduledCaptureAt - pacingStartedAt);
        await this.pause(pacingRequestedMs);
        const pacingEndedAt = this.now(), pendingBeforeAdmission = this.pending.size;
        if (this.pending.size >= this.maxPending)
          await Promise.race([...this.pending, this.stopPromise]);
        if (!this.active() || this.finishing) break;
        const capturedAt = this.now();
        const bytes = await this.capture();
        const encodedAt = this.now();
        if (!this.active() || this.finishing) break;
        this.sent += 1;
        this.onSent(this.sent, this.received);
        const task = this.dispatch(bytes, {ordinal: this.sent, capturedAt, encodedAt,
          inputBytes: bytes.byteLength, scheduledCaptureAt, pacingRequestedMs,
          pacingWaitMs: pacingEndedAt - pacingStartedAt,
          pacingLateMs: Math.max(0, pacingEndedAt - scheduledCaptureAt),
          creditWaitMs: pendingBeforeAdmission >= this.maxPending ? capturedAt - pacingEndedAt : 0,
          pendingBeforeAdmission});
        const interval = 1000 / this.rate;
        // Preserve phase for ordinary timer lateness; capacity stalls start a new timeline rather
        // than scheduling a sequence of overdue frames. No acquired credit is released early.
        nextCaptureAt = pendingBeforeAdmission >= this.maxPending || capturedAt - scheduledCaptureAt >= interval
          ? capturedAt + interval : scheduledCaptureAt + interval;
        if (this.sent === 1) {
          await Promise.race([task, this.stopPromise]);
          nextCaptureAt = this.now();
        }
      }
      if (this.active()) await this.drain();
    } catch (error) { this.fail(error); }
    if (this.failure) throw this.failure;
    return {sent: this.sent, received: this.received, maxInFlight: this.maxInFlight};
  }
  /** Functionality: Submit one frame and append its returned result to the display chain.
   * Inputs: original bytes and ordinal/capture/encode/inputBytes context.
   * Outputs: observed task. Logic: result time is recorded before render queue waiting; completion
   * credit is released only after display, so decoding/queued results remain within maxPending.
   * Constraints: no lost rejection or out-of-order display; stopped frames cannot update UI.
   */
  dispatch(bytes, context) {
    const pipeline = this;
    /** Functionality: Record result arrival and queue ordered display. Inputs: result.
     * Outputs: display task. Logic: pipeline's receiver contract preserves request order.
     * Constraints: result latency includes full validation; queued decode time is measured separately. */
    function received(result) {
      const receivedAt = pipeline.now();
      /** Functionality: Display one validated result and publish its complete timing.
       * Inputs: result/context and active state. Outputs: callback/counter update.
       * Logic: generation checked around decode; strict ordinal, then include queue wait in full latency.
       * Constraints: no renderer concurrency, stale UI update, skipped frame or latency clipping. */
      async function render() {
        if (!pipeline.active()) return;
        if (context.ordinal !== pipeline.received + 1) throw new Error("视频结果顺序与采集不一致。");
        const renderStartedAt = pipeline.now();
        await pipeline.display(result.bytes, result.metadata);
        const decodedAt = pipeline.now();
        if (!pipeline.active()) return;
        pipeline.received += 1;
        pipeline.onFrame(result, {...context, receivedAt, renderStartedAt, decodedAt,
          captureMs: context.encodedAt - context.capturedAt,
          transportMs: receivedAt - context.encodedAt,
          renderQueueMs: renderStartedAt - receivedAt,
          decodeMs: decodedAt - renderStartedAt,
          captureToResultMs: receivedAt - context.capturedAt,
          captureToDecodedMs: decodedAt - context.capturedAt,
          completedAt: decodedAt, sent: pipeline.sent, received: pipeline.received});
      }
      const rendering = pipeline.renderTail.then(render);
      pipeline.renderTail = rendering.catch(failed);
      return rendering;
    }
    /** Functionality: Preserve/report a pipeline error. Inputs: Error. Outputs: stopped session.
     * Logic: fail() deduplicates render/transport rejection observations. Constraints: never retries. */
    function failed(error) {
      if (pipeline.finishing && error.name === "VideoSessionEnded") return;
      pipeline.fail(error);
    }
    let observed;
    /** Functionality: Release one working slot. Inputs: captured observed task. Outputs: Set removal.
     * Logic: only after display/failure. Constraints: no capture is retained in a waiting queue. */
    function finished() { pipeline.pending.delete(observed); }
    observed = this.client.sendFrame(bytes).then(received).catch(failed).finally(finished);
    this.pending.add(observed);
    this.maxInFlight = Math.max(this.maxInFlight, this.pending.size);
    return observed;
  }
  /** Functionality: Complete already-returned/queued display or honour explicit cancellation.
   * Inputs: current pending tasks. Outputs: Promise. Logic: provider end is distinct from force-stop.
   * Constraints: unresolved session-end frames reject explicitly and are not counted as displayed. */
  async drain() { await Promise.race([Promise.all([...this.pending]), this.cancelPromise]); }
  /** Functionality: Stop admission and drain valid results on a normal bounded session end.
   * Inputs: pacing state. Outputs: finishing state/wake. Logic: render active state remains true.
   * Constraints: no new capture or replacement GPU session, no lost already-validated result. */
  finish() {
    this.finishing = true;
    clearTimeout(this.pauseTimer);
    this.pauseTimer = null;
    this.wakeStop();
  }
  /** Functionality: Record original error and stop capture/transport.
   * Inputs: Error. Outputs: failure, cancelled waits/client, onFailure notification.
   * Logic: first error wins. Constraints: no hidden recovery, discarded error or double notification. */
  fail(error) {
    if (this.failure || this.stopped) return;
    this.failure = error;
    this.stop();
    this.client.stop();
    this.onFailure(error);
  }
  /** Functionality: Cancel pacing/admission/drain promptly.
   * Inputs: timer and stop signal. Outputs: stopped state and wake.
   * Logic: UI/client resource release remains the controller's responsibility.
   * Constraints: no new capture, decoding or display after active() becomes false. */
  stop() {
    if (this.stopped) return;
    this.stopped = true;
    clearTimeout(this.pauseTimer);
    this.pauseTimer = null;
    this.wakeStop();
    this.wakeCancel();
  }
}
