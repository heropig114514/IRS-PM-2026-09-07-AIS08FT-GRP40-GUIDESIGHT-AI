/** Responsibilities: Replay an explicitly selected local video into the existing local detection transport.
 * Implementation: rVFC presentation sampling, original640/JPEG0.8,30FPS ceiling/12 total jobs,
 * first frame exclusive, encode-order dispatch, EOF drain and immediate explicit cancellation.
 * Related Modules: video-mvp.js; existing FramePipeline.dispatch/drain and LiveDetectionClient PRF2/PRS1;
 * guidance.js consumes the same structured session/frame lifecycle without cameraApp.
 * Declaration Index: VideoReplay; VideoReplay.constructor; VideoReplay.subscribe; VideoReplay.emit;
 * VideoReplay.active; VideoReplay.validatePlayback; VideoReplay.loadFile; VideoReplay.loadFile.loaded;
 * VideoReplay.loadFile.failed; VideoReplay.loadFile.cleanup; VideoReplay.loadFile.cancelLoad; VideoReplay.start;
 * VideoReplay.schedule; VideoReplay.presented; VideoReplay.encode; VideoReplay.dispatchEncoded;
 * VideoReplay.display; VideoReplay.completed; VideoReplay.finish; VideoReplay.fail; VideoReplay.stop;
 * VideoReplay.dispose; VideoReplay.stats.
 * Variable Index: MAX_EDGE/JPEG_QUALITY/MAX_FRAME_BYTES/INPUT_FPS/MAX_PENDING keep original input contract;
 * dependencies inject native video/canvas/transport/pipeline for tests; generation/session isolate late callbacks;
 * objectUrl/resultUrl own Blob lifetime; loading/loadSequence cancel metadata listeners by exact owner;
 * disposed prevents off-page loading/restart; session encoding/jobs/sendTail are bounded, not a frame FIFO;
 * counters measure presented/cadence/credit/first-window skips separately from sent/delivered frames.
 */
import {EventBus} from "./events.js";
const MAX_EDGE = 640, JPEG_QUALITY = 0.8, MAX_FRAME_BYTES = 1048576, INPUT_FPS = 30, MAX_PENDING = 12;

/** Functionality: Control local-file decode and explicit inference sessions independently of cameras.
 * Logic: capture only after cadence/admission; actual browser draw time is the PRF2 source clock,
 * mediaTime/presentedFrames remain observations rather than substituted motion timestamps.
 * Constraints: no getUserMedia, seek, loop, speed correction, retries, original-file upload or source frame duplication. */
export class VideoReplay {
  /** Functionality: Bind explicit adapters. Inputs: video/canvas/image, clientFactory/pipelineFactory and native dependencies.
   * Outputs: idle controller with media fault listeners. Logic: no file/permission/network opened.
   * Constraints: timer/native dependencies retain their global receiver; factories do not substitute providers. */
  constructor({video, canvas, image, clientFactory, pipelineFactory, href = globalThis.location?.href,
    now = () => performance.now(), createUrl = blob => URL.createObjectURL(blob), revokeUrl = url => URL.revokeObjectURL(url)}) {
    Object.assign(this, {video, canvas, image, clientFactory, pipelineFactory, href, now, createUrl, revokeUrl});
    this.events = new EventBus(); this.generation = 0; this.mode = "idle"; this.session = null;
    this.file = null; this.objectUrl = this.resultUrl = null; this.client = this.pipeline = null; this.callbackId = null;
    this.lastStats = null; this.loading = null; this.loadSequence = 0; this.disposed = false;
    video.addEventListener("ended", () => { if (this.active()) void this.finish("eof"); });
    video.addEventListener("seeking", () => { if (this.mode !== "loading" && this.file) this.fail(new Error("Video seeking is not allowed.")); });
    video.addEventListener("ratechange", () => { if (this.file) { try { this.validatePlayback(); } catch (error) { this.fail(error); } } });
    video.addEventListener("error", () => { if (this.mode !== "loading" && this.file) this.fail(new Error("Local video decoding failed.")); });
  }
  /** Functionality: Observe explicit state. Inputs: listener. Outputs: unsubscribe.
   * Logic: in-memory event bus; Constraints: no body/pixel archive or DOM polling. */
  subscribe(listener) { return this.events.subscribe(listener); }
  /** Functionality: Publish replay state. Inputs: type/details. Outputs: callbacks.
   * Logic: source generation frozen at publication; Constraints: observer errors are explicit and do not change source timing. */
  emit(type, details = {}) {
    try { this.events.publish({type, generation: this.generation, ...details}); }
    catch (error) { console.error("Video observer failed", {error_type: error.name}); }
  }
  /** Functionality: Check active ownership. Inputs: optional session. Outputs: boolean.
   * Logic: generation/cancellation guard includes EOF drain; Constraints: no automatic restart. */
  active(session = this.session) { return !!session && session === this.session && !session.cancelled; }
  /** Functionality: Enforce original media conditions. Inputs: native video. Outputs: void/error.
   * Logic: validate1x/default1x/no loop/no seek without repairing any value.
   * Constraints: user/programmatic mutations stop rather than silently correcting experimental conditions. */
  validatePlayback() {
    if (this.video.playbackRate !== 1 || this.video.defaultPlaybackRate !== 1 || this.video.loop || this.video.seeking)
      throw new Error("Video must remain at 1x without seeking or looping.");
  }
  /** Functionality: Load a user-selected local Blob/File. Inputs: file. Outputs: metadata-ready source.
   * Logic: replace/revoke previous Blob, native loadedmetadata/error wait; selection is explicit.
   * Constraints: no playback/permissions/inference, default file, source URL fetch or automatic seek. */
  async loadFile(file) {
    if (this.disposed) throw new Error("Video page has been disposed.");
    if (this.mode !== "idle") throw new Error("Stop before selecting another video.");
    if (!(file instanceof Blob) || !file.size) throw new Error("Choose a nonempty local video file.");
    if (this.objectUrl) this.revokeUrl(this.objectUrl);
    const load = {sequence: ++this.loadSequence, url: this.createUrl(file), cancelled: false, cancel: null};
    this.loading = load; this.file = file; this.objectUrl = load.url; this.mode = "loading";
    try {
      await new Promise((resolve, reject) => {
        /** Functionality: Resolve metadata loading. Inputs: native event. Outputs: detached wait.
         * Logic: single current waiter; Constraints: ownership is checked again after awaiting. */
        const loaded = () => { cleanup(); resolve(); };
        /** Functionality: Reject decoder metadata fault. Inputs: native event. Outputs: detached failure.
         * Logic: no retry/codec substitution; Constraints: original file is never sent. */
        const failed = () => { cleanup(); reject(new Error("Local video metadata could not be decoded.")); };
        /** Functionality: Detach metadata callbacks. Inputs: closure callbacks. Outputs: listener cleanup.
         * Logic: shared resolve/reject/cancel cleanup; Constraints: never affects another load token. */
        const cleanup = () => { this.video.removeEventListener("loadedmetadata", loaded); this.video.removeEventListener("error", failed); };
        /** Functionality: Settle explicitly cancelled loading. Inputs: stop/dispose. Outputs: cancelled rejection.
         * Logic: stop owns URL/source cleanup; Constraints: already-resolved wait still has cancelled ownership flag. */
        const cancelLoad = () => {
          load.cancelled = true; cleanup();
          reject(Object.assign(new Error("Local video loading cancelled."), {name: "VideoLoadCancelled"}));
        };
        load.cancel = cancelLoad;
        this.video.addEventListener("loadedmetadata", loaded); this.video.addEventListener("error", failed);
        this.video.src = load.url; this.video.load();
      });
      if (load.cancelled || this.loading !== load || this.disposed)
        throw Object.assign(new Error("Local video loading cancelled."), {name: "VideoLoadCancelled"});
      this.validatePlayback();
      if (!this.video.videoWidth || !this.video.videoHeight) throw new Error("Video has no decoded dimensions.");
      this.loading = null;
      this.mode = "idle"; this.emit("loaded", {source: "local_video", name: file.name || "Local video", duration: this.video.duration});
    } catch (error) {
      if (this.loading !== load) throw error;
      this.loading = null;
      this.mode = "idle"; this.file = null;
      this.revokeUrl(load.url);
      this.objectUrl = null; this.video.removeAttribute("src"); this.video.load();
      this.fail(error); throw error;
    }
  }
  /** Functionality: Start one explicitly requested local session and1x replay. Inputs: loaded file.
   * Outputs: session admission/playback or explicit failure. Logic: no camera module, connect same-origin before playing.
   * Constraints: ended source must be reselected manually; provider ends after55s without looping/reconnecting. */
  async start() {
    if (this.disposed) throw new Error("Video page has been disposed.");
    if (this.mode !== "idle") return;
    if (!this.file || this.video.ended) throw new Error("Select a playable local video before starting.");
    if (typeof this.video.requestVideoFrameCallback !== "function") throw new Error("This browser lacks video presentation callbacks.");
    this.validatePlayback();
    const url = new URL("../../ws/detect/local/", this.href);
    if (url.origin !== new URL(this.href).origin) throw new Error("Video detection must use the same origin.");
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    const session = {generation: ++this.generation, cancelled: false, finishing: false, sendClosed: false, encoding: 0,
      jobs: new Set(), sendTail: Promise.resolve(), firstComplete: false, lastMediaTime: null, lastPresented: null,
      nextCaptureAt: this.now(), stats: {source: "local_video", sourceCallbacks: 0, observedPresentedFrames: 0,
        callbackGaps: 0, skippedCadence: 0, skippedCredit: 0, skippedFirstFrame: 0, admitted: 0,
        encoded: 0, sent: 0, delivered: 0, cancelledBeforeSend: 0, maxInFlight: 0,
        firstSourceMediaTime: null, lastSourceMediaTime: null, lastPresentedFrames: null}};
    this.session = session; this.pipeline = null; this.mode = "connecting";
    this.client = session.client = this.clientFactory(url.href, error => { if (this.active(session)) this.fail(error); },
      () => { if (this.active(session)) void this.finish("provider_ended"); });
    try {
      const contract = await this.client.connect();
      if (!this.active(session)) return;
      session.id = contract.session_id; session.contract = contract;
      this.pipeline = session.pipeline = this.pipelineFactory({client: session.client, capture: null, now: this.now,
        display: (bytes, metadata) => this.display(bytes, metadata, session),
        onFrame: (result, timing) => this.completed(result, timing, session), onSent: () => {},
        isActive: () => this.active(session), onFailure: error => this.fail(error)});
      this.emit("session", {sessionId: session.id, provider: "local", source: "local_video", contract});
      await this.video.play();
      if (!this.active(session)) return;
      this.validatePlayback(); this.mode = "playing"; this.schedule(session);
    } catch (error) { if (this.active(session)) this.fail(error); }
  }
  /** Functionality: Await the next native presentation. Inputs: session. Outputs: one registered callback.
   * Logic: continuously observe source presentations even while sampling/admission skips frames.
   * Constraints: no timer-generated repeated image or capture before credit. */
  schedule(session) {
    if (!this.active(session) || session.finishing) return;
    this.callbackId = this.video.requestVideoFrameCallback((callbackNow, metadata) => {
      this.callbackId = null;
      if (!this.active(session) || session.finishing) return;
      try { this.presented(callbackNow, metadata, session); }
      catch (error) { this.fail(error); return; }
      this.schedule(session);
    });
  }
  /** Functionality: Observe a genuinely new presented frame and admit sampling when permitted.
   * Inputs: native callback time/metadata/session. Outputs: observation or bounded encode job.
   * Logic:1x source PTS/presented count strictly increase; cadence maintains timeline without catch-up;
   * first result is exclusive, then total encoding+pending display jobs stays<=12.
   * Constraints: source mediaTime is never used as PRF2 motion timestamp; skips precede canvas capture. */
  presented(callbackNow, metadata, session) {
    this.validatePlayback();
    const admissionAt = this.now(), mediaTime = metadata.mediaTime, presentedFrames = metadata.presentedFrames;
    if (!Number.isFinite(admissionAt) || admissionAt <= 0 || !Number.isFinite(callbackNow) ||
        !Number.isFinite(mediaTime) || mediaTime < 0 || !Number.isSafeInteger(presentedFrames) || presentedFrames < 1 ||
        session.lastMediaTime !== null && mediaTime <= session.lastMediaTime ||
        session.lastPresented !== null && presentedFrames <= session.lastPresented)
      throw new Error("Video presentation time/count must increase without duplicated source PTS.");
    const delta = session.lastPresented === null ? 1 : presentedFrames - session.lastPresented;
    session.stats.sourceCallbacks++; session.stats.observedPresentedFrames += delta; session.stats.callbackGaps += delta - 1;
    session.stats.firstSourceMediaTime ??= mediaTime;
    session.stats.lastSourceMediaTime = mediaTime; session.stats.lastPresentedFrames = presentedFrames;
    session.lastMediaTime = mediaTime; session.lastPresented = presentedFrames;
    const inflight = session.encoding + this.pipeline.pending.size;
    if (!session.firstComplete && inflight) { session.stats.skippedFirstFrame++; return; }
    if (inflight >= MAX_PENDING) { session.stats.skippedCredit++; return; }
    if (admissionAt < session.nextCaptureAt) { session.stats.skippedCadence++; return; }
    const interval = 1000 / INPUT_FPS;
    session.nextCaptureAt = admissionAt - session.nextCaptureAt >= interval ? admissionAt + interval : session.nextCaptureAt + interval;
    session.encoding++; session.stats.admitted++;
    session.stats.maxInFlight = Math.max(session.stats.maxInFlight, session.encoding + this.pipeline.pending.size);
    const context = {admissionAt, sourceMediaTime: mediaTime, presentedFrames, presentationCallbackAt: callbackNow,
      scheduledCaptureAt: session.nextCaptureAt - interval};
    const encoded = this.encode(context).then(bytes => ({bytes}), error => ({error}));
    this.dispatchEncoded(encoded, context, session);
  }
  /** Functionality: Encode current source pixels under the unchanged frame contract.
   * Inputs: native video dimensions/canvas and mutable admitted context. Outputs: JPEG ArrayBuffer and actual draw clock.
   * Logic: scale max edge640, fixed0.8; native toBlob snapshots the canvas per admitted frame.
   * Constraints: capture time is read immediately before draw; empty/oversize/non-JPEG fails without parameter fallback. */
  async encode(context) {
    const width = this.video.videoWidth, height = this.video.videoHeight;
    if (!width || !height) throw new Error("Video has no current pixels.");
    const scale = Math.min(1, MAX_EDGE / Math.max(width, height));
    const w = Math.max(1, Math.round(width * scale)), h = Math.max(1, Math.round(height * scale));
    if (this.canvas.width !== w) this.canvas.width = w;
    if (this.canvas.height !== h) this.canvas.height = h;
    const drawing = this.canvas.getContext("2d");
    context.capturedAt = this.now();
    drawing.drawImage(this.video, 0, 0, w, h);
    const blob = await new Promise((resolve, reject) => this.canvas.toBlob(value => {
      if (!value || value.type !== "image/jpeg" || value.size > MAX_FRAME_BYTES) reject(new Error("Video JPEG encoding failed or exceeded1MiB."));
      else resolve(value);
    }, "image/jpeg", JPEG_QUALITY));
    return blob.arrayBuffer();
  }
  /** Functionality: Preserve encode-admission order without serializing network/display.
   * Inputs: observed encode outcome/context/session. Outputs: bounded queued encoding task then independent pipeline dispatch.
   * Logic: sendTail waits only for encoding/order; pipeline.pending owns result/display lifetime.
   * Constraints: late canceled encodes never send, faults stop the session, no captured-frame FIFO beyond12 jobs. */
  dispatchEncoded(encoded, context, session) {
    const queued = session.sendTail.then(async () => {
      const outcome = await encoded;
      session.encoding--;
      if (!this.active(session) || session.sendClosed) { session.stats.cancelledBeforeSend++; return; }
      if (outcome.error) throw outcome.error;
      session.stats.encoded++; session.stats.sent = ++this.pipeline.sent;
      this.pipeline.dispatch(outcome.bytes, {...context, ordinal: this.pipeline.sent, encodedAt: this.now(), inputBytes: outcome.bytes.byteLength});
      this.emit("submitted", {sessionId: session.id, source: "local_video", ...context, stats: {...session.stats}});
    }).catch(error => { if (this.active(session)) this.fail(error); });
    session.sendTail = queued;
    session.jobs.add(queued); void queued.finally(() => session.jobs.delete(queued));
  }
  /** Functionality: Decode the next validated annotation. Inputs: bytes/metadata/session.
   * Outputs: image display and Blob URL lifecycle. Logic: dimensions match server metadata, old callbacks cannot update UI.
   * Constraints: decoding is not proof of final physical paint; original video is never uploaded. */
  async display(bytes, metadata, session) {
    const previous = this.resultUrl;
    const url = this.createUrl(new Blob([bytes], {type: "image/jpeg"})); this.resultUrl = url; this.image.src = url;
    try {
      await this.image.decode();
      if (!this.active(session)) return;
      if (this.image.naturalWidth !== metadata.width || this.image.naturalHeight !== metadata.height)
        throw new Error("Video annotation dimensions do not match metadata.");
      this.image.hidden = false;
    } finally {
      if (previous) this.revokeUrl(previous);
      if (!this.active(session)) { this.revokeUrl(url); if (this.resultUrl === url) this.resultUrl = null; }
    }
  }
  /** Functionality: Publish one ordered fully decoded result. Inputs: result/timing/session.
   * Outputs: delivery counters and camera-compatible structured event. Logic: source context retained through dispatch.
   * Constraints: no synthetic repeat frames, source-time reset, hidden latency clipping or automatic output enablement. */
  completed(result, timing, session) {
    if (!this.active(session)) return;
    session.firstComplete = true; session.stats.delivered = this.pipeline.received;
    this.emit("frame", {sessionId: session.id, provider: "local", source: "local_video", result,
      capturedAt: timing.capturedAt, sourceMediaTime: timing.sourceMediaTime, presentedFrames: timing.presentedFrames,
      timing, stats: {...session.stats}});
  }
  /** Functionality: End input and drain on EOF/provider completion. Inputs: reason.
   * Outputs: terminal event, stopped source/client and preserved delivery stats.
   * Logic: EOF sends every admitted JPEG before drain; provider end refuses unsent work explicitly.
   * Constraints: no looping/seeking/restart or claiming unresolved jobs delivered. */
  async finish(reason = "video_end") {
    const session = this.session;
    if (!this.active(session)) return;
    if (reason === "provider_ended") {
      session.sendClosed = true; session.pipeline?.finish(); session.endReason = reason;
    }
    if (session.finishing) return;
    session.endReason ??= reason;
    session.finishing = true; this.mode = "draining";
    if (this.callbackId !== null) this.video.cancelVideoFrameCallback(this.callbackId);
    this.callbackId = null; this.video.pause();
    await Promise.all([...session.jobs]);
    if (!this.active(session)) return;
    await session.pipeline?.drain();
    if (!this.active(session)) return;
    this.lastStats = {...session.stats}; session.client?.stop(); session.pipeline?.stop();
    session.cancelled = true; this.mode = "idle";
    this.emit("ended", {reason: session.endReason, sessionId: session.id, source: "local_video", stats: this.lastStats});
  }
  /** Functionality: Diagnose and release a replay fault. Inputs: Error.
   * Outputs: stop/error events. Logic: source/transport faults remain explicit.
   * Constraints: logs contain type only, no video body, local path, retries or parameter correction. */
  fail(error) {
    const sessionId = this.session?.id ?? null;
    this.stop("error"); this.emit("error", {sessionId, errorName: error.name, message: error.message, source: "local_video"});
  }
  /** Functionality: Cancel active/queued work immediately. Inputs: reason.
   * Outputs: paused source, stopped transport/pipeline and owned terminal event.
   * Logic: generation guards prevent late encode/decode; original local File stays selected for explicit continuation.
   * Constraints: no provider reconnect, media seek/loop, automatic timer restart or camera permission use. */
  stop(reason = "user_stop") {
    const load = this.loading;
    if (load) {
      this.loading = null; load.cancel(); this.revokeUrl(load.url);
      if (this.objectUrl === load.url) { this.objectUrl = null; this.file = null; this.video.removeAttribute("src"); this.video.load(); }
    }
    const session = this.session;
    if (session) { session.cancelled = true; session.sendClosed = true; this.lastStats = {...session.stats}; }
    if (this.callbackId !== null) this.video.cancelVideoFrameCallback(this.callbackId);
    this.callbackId = null; this.video.pause(); this.pipeline?.stop(); this.client?.stop(); this.mode = "idle";
    this.emit("stop", {reason, sessionId: session?.id ?? null, source: "local_video", stats: this.lastStats});
  }
  /** Functionality: Release Blob ownership on leave. Inputs: controller. Outputs: stopped work/revoked URLs.
   * Logic: no source retained after disposal; Constraints: cannot imply external GPU task instantaneous cancellation. */
  dispose() {
    this.disposed = true; this.stop("pagehide");
    if (this.objectUrl) this.revokeUrl(this.objectUrl);
    if (this.resultUrl) this.revokeUrl(this.resultUrl);
    this.objectUrl = this.resultUrl = this.file = null;
    this.video.removeAttribute("src"); this.video.load();
  }
  /** Functionality: Read bounded source/transport counts. Inputs: session/last stats. Outputs: copy.
   * Logic: include encoding+pending jobs to expose admission window; Constraints: counts are not safety/accuracy evidence. */
  get stats() {
    const stats = this.session?.stats || this.lastStats;
    return {...stats, uncapturedObservedFrames: stats ? stats.callbackGaps + stats.skippedCadence + stats.skippedCredit + stats.skippedFirstFrame : 0,
      currentInFlight: this.session ? this.session.encoding + (this.pipeline?.pending.size || 0) : 0};
  }
}
