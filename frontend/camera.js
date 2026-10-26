/**
 * Responsibilities: Manage camera permission, explicit Echo/HF/local-CUDA transport, phase timing, and observable lifecycle.
 * Implementation: 640-pixel maximum edge and JPEG 0.8; after the first HF/local frame completes, capture at up to 30 FPS with a 12-frame limit and ordered display; any error stops processing and retains its browser error name for semantic status reporting.
 * Related Modules: index.html, echo-client.js, live-detection-client.js,
 * frame-pipeline.js, scene-client.js, integration/local-mvp.js；core.echo/hf_video_relay/integration.asgi；
 * tools/benchmark_phone_browser.mjs validates the real page; fixture input is not physical phone-camera evidence.
 * Declaration Index: SCENE_MAX_AGE_MS; cameraApp; processingPath; encodeJpeg; encodeJpeg.encode;
 * encodeJpeg.encode.ready;
 * CameraApp; CameraApp.constructor; CameraApp.apiUrl; CameraApp.setStatus; CameraApp.handleDisconnect;
 * CameraApp.handleProcessingChange; CameraApp.start; CameraApp.capture; CameraApp.showFrame; CameraApp.testPing;
 * CameraApp.runPipeline; CameraApp.runPipeline.display;
 * CameraApp.runPipeline.frame; CameraApp.runPipeline.sent; CameraApp.runPipeline.active;
 * CameraApp.publishFrame; CameraApp.handleSessionEnd;
 * CameraApp.latestJpeg; CameraApp.latestBoxes; CameraApp.subscribe; CameraApp.emit;
 * CameraApp.recordFrameTimings; CameraApp.reportFrameTimings; CameraApp.stop;
 * CameraApp.handleCameraEnded; CameraApp.handleVisibility; CameraApp.handlePageHide。
 * Variable Index: MAX_EDGE/JPEG_QUALITY — capture settings; METRICS_LOG_INTERVAL_MS — summary-log interval;
 * PROCESSING_PATHS — the three explicit WebSocket modes; app — page controller; frameTimings — first-frame and bounded aggregate statistics;
 * pacingFrames/pacingLateTotalMs/pacingLateMaxMs and creditWaitTotalMs/creditWaitMaxMs/creditWaitFrames
 * aggregate only steady-state frames with pipeline scheduling observations; serial Echo frames without observations are not counted as zero;
 * SCENE_MAX_AGE_MS — maximum age for scene-description frame/detection reuse; lastFrame/lastDetection retain only the latest
 * encoded JPEG and detection metadata in memory for scene-client.js, then clear on stop without persistence;
 * cameraApp — exported reference to the page controller; lastErrorType — latest browser error name, cleared at the next explicit start;
 * cameraPhase — lifecycle phase; pipeline — bounded cancellation signal; apiBase — same-origin API mount; listeners — observer set;
 * localDeviceId — device explicitly selected in the local UI; localFacingMode — null by default, with the integrated entry optionally requesting the rear camera using environment;
 * if both are set, explicit deviceId takes precedence; invalid non-null facing modes are rejected. localOnly prevents the local MVP from starting HF accidentally; sessionId identifies result lifecycle;
 * localSessionSeconds defaults to 55; the separate integration entry may set null for a continuous local session; HF does not accept this option.
 */
import { EchoClient, MAX_FRAME_BYTES } from "./echo-client.js";
import { LiveDetectionClient } from "./live-detection-client.js";
import { FramePipeline } from "./frame-pipeline.js";

const MAX_EDGE = 640;
const JPEG_QUALITY = 0.8;
const METRICS_LOG_INTERVAL_MS = 5000;
export const SCENE_MAX_AGE_MS = 1000;
const PROCESSING_PATHS = {echo: "./ws/echo/", hf: "./ws/detect/hf/", local: "./ws/detect/local/"};

/** Functionality: Resolve one of the three explicitly supported processing modes.
 * Inputs: processing enum. Outputs: fixed relative WS path; unknown values throw.
 * Logic: explicit allowlist rejects retired modes before camera authorization.
 * Constraints: no HTTP detector or automatic provider substitution. */
export function processingPath(processing) {
  if (!Object.hasOwn(PROCESSING_PATHS, processing)) throw new Error("未知图像处理模式");
  return PROCESSING_PATHS[processing];
}

/** Functionality: Encode the current canvas. Inputs: canvas. Outputs: JPEG Blob Promise.
 * Logic: Use fixed JPEG_QUALITY; Constraints: report unsupported encoding explicitly and do not change formats. */
function encodeJpeg(canvas) {
  /** Functionality: Complete one asynchronous encode. Inputs: resolve/reject. Outputs: None.
   * Logic: Let browser toBlob encode the current canvas; Constraints: an empty Blob is an error. */
  function encode(resolve, reject) {
    /** Functionality: Publish the encoded result. Inputs: blob. Outputs: resolve or reject.
     * Logic: Verify MIME type and prevent a silent PNG switch; Constraints: do not write local files. */
    function ready(blob) {
      if (!blob || blob.type !== "image/jpeg")
        reject(new Error("浏览器无法编码 JPEG 图像"));
      else resolve(blob);
    }
    canvas.toBlob(ready, "image/jpeg", JPEG_QUALITY);
  }
  return new Promise(encode);
}

/** Functionality: Manage the mobile-camera diagnostic workspace.
 * Logic: generation isolates late async results after cancellation; mode prevents duplicate starts or concurrent pings.
 * Constraints: Do not record audio or save frames; release the camera on page hide, device end, stop, or failure. */
export class CameraApp {
  /** Functionality: Bind controls. Inputs: Current document/window. Outputs: Instance and event handlers.
   * Logic: processing selects the mode; retain media/client/timing state; local sessions default to 55 seconds; resolve apiBase from the page-relative same-origin mount; do not request permission automatically.
   * Constraints: Lock mode during runtime; switching requires stopping first. */
  constructor() {
    this.video = document.getElementById("preview");
    this.image = document.getElementById("echo");
    this.startButton = document.getElementById("start");
    this.stopButton = document.getElementById("stop");
    this.pingButton = document.getElementById("ping");
    this.camera = document.getElementById("camera");
    this.processing = document.getElementById("processing");
    this.status = document.getElementById("status");
    this.message = document.getElementById("message");
    this.canvas = document.createElement("canvas");
    this.stream = null;
    this.client = null;
    this.objectUrl = null;
    this.frameTimings = null;
    this.cameraPhase = "idle";
    this.lastErrorType = null;
    this.pipeline = null;
    this.lastFrame = null;
    this.lastDetection = null;
    this.listeners = new Set();
    this.localDeviceId = "";
    this.localFacingMode = null;
    this.localOnly = document.documentElement?.dataset?.localMvp === "true";
    this.localSessionSeconds = 55;
    this.sessionId = null;
    this.apiBase = document.documentElement?.dataset?.apiBase || "./";
    this.generation = 0;
    this.mode = "idle";
    this.start = this.start.bind(this);
    this.stop = this.stop.bind(this);
    this.testPing = this.testPing.bind(this);
    this.handleDisconnect = this.handleDisconnect.bind(this);
    this.handleCameraEnded = this.handleCameraEnded.bind(this);
    this.handleVisibility = this.handleVisibility.bind(this);
    this.handlePageHide = this.handlePageHide.bind(this);
    this.handleProcessingChange = this.handleProcessingChange.bind(this);
    this.handleSessionEnd = this.handleSessionEnd.bind(this);
    this.processing.addEventListener("change", this.handleProcessingChange);
    this.handleProcessingChange();
    this.startButton.addEventListener("click", this.start);
    this.stopButton.addEventListener("click", this.stop);
    this.pingButton.addEventListener("click", this.testPing);
    document.addEventListener("visibilitychange", this.handleVisibility);
    window.addEventListener("pagehide", this.handlePageHide);
  }
  /** Functionality: Subscribe to camera events. Inputs: listener function. Outputs: unsubscribe callback.
   * Logic: identity-based Set; events include source capture times and generation.
   * Constraints: no DOM polling, frame copying, automatic network or persistent history. */
  subscribe(listener) { this.listeners.add(listener); return () => this.listeners.delete(listener); }
  /** Functionality: Publish lifecycle/results. Inputs: event object. Outputs: observer callbacks.
   * Logic: include controller generation; diagnose observer failures without changing inference behavior.
   * Constraints: observers own their business rules; exception logs omit frame bodies. */
  emit(event) {
    const generation = this.generation;
    for (const listener of this.listeners) {
      try { listener({...event, generation}); }
      catch (error) { console.error("Camera observer failed", {error_type: error.name}); }
    }
  }
  /** Functionality: Resolve an API path against the page's explicit same-origin mount.
   * Inputs: relative path, apiBase and browser location. Outputs: validated URL.
   * Logic: canonical /live/ page declares ../; the root entry redirects to it.
   * Constraints: cross-origin mounts fail; no arbitrary inference host, query routing or fallback. */
  apiUrl(path) {
    const base = new URL(this.apiBase, window.location.href), url = new URL(path, base);
    if (base.origin !== new URL(window.location.href).origin || url.origin !== base.origin)
      throw new Error("检测接口必须与页面同源。");
    return url;
  }
  /** Functionality: Show third-party image-processing and wait-budget notice. Inputs: current processing mode.
   * Outputs: Notice visibility; Logic: visible only for hf; Constraints: no network or permission request. */
  handleProcessingChange() {
    document.getElementById("hf-note").hidden = this.processing.value !== "hf";
  }
  /** Functionality: Update status and action boundaries. Inputs: text and kind. Outputs: DOM updates.
   * Logic: mode determines control availability; Constraints: render textContent only, never insert HTML. */
  setStatus(text, kind = "idle") {
    this.status.textContent = text;
    this.status.dataset.kind = kind;
    this.startButton.disabled = this.mode !== "idle";
    this.pingButton.disabled = this.mode !== "idle";
    this.camera.disabled = this.mode !== "idle";
    this.processing.disabled = this.mode !== "idle";
    this.stopButton.disabled = this.mode !== "camera";
  }
  /** Functionality: Handle connection failure. Inputs: Error. Outputs: Resource cleanup and readable status.
   * Logic: Retain the native error name, log mode/phase/count, then stop and display the reason.
   * Constraints: Logs contain no frame data or credentials; cameraPhase may be idle for other operations; do not infer remote failure causes. */
  handleDisconnect(error) {
    this.lastErrorType = error.name;
    this.emit({type: "error", sessionId: this.sessionId, errorType: error.name});
    console.error("Camera processing failed:", {
      message: error.message, mode: this.mode, cameraPhase: this.cameraPhase,
      completedFrames: this.frameTimings ? this.frameTimings.steadyFrames + 1 : 0,
    });
    this.stop();
    this.setStatus("连接失败", "error");
    this.message.textContent = error.message;
    this.message.dataset.kind = "error";
  }
  /** Functionality: Request camera access and use the explicitly selected mode. Inputs: localDeviceId, localFacingMode, facingMode, processing, and browser media APIs.
   * Outputs: Local video, returned image, round-trip timings, and post-first-frame completion rate.
   * Logic: Clear prior failure state, preserve one in-flight job, and use the selected processing mode. HF/local modes share the 30 FPS/12-job pipeline. For local mode, explicit deviceId wins; otherwise only an entry that explicitly sets environment may request the rear camera.
   * Check facingMode constraint support before requesting rear-camera permission; pass localSessionSeconds locally while HF retains exact-camera behavior.
   * Constraints: Require exact camera selection; fail if unsupported or no rear camera exists, with no front/default-camera fallback.
   * Detection failure stops processing without mode switching or automatic permission retry; never discard sent frames to inflate rate.
   * Without a fixed delay there is no 10 FPS guarantee; timing begins before drawImage and is not sensor exposure or final screen-render time. */
  async start() {
    if (this.mode !== "idle") return;
    this.lastErrorType = null;
    const token = ++this.generation;
    this.mode = "camera";
    this.cameraPhase = "permission";
    this.video.hidden = false;
    document.getElementById("capture-source").textContent = "01 / 本地镜头";
    this.setStatus("等待摄像头授权");
    this.message.dataset.kind = "idle";
    try {
      const processing = this.processing.value;
      const path = processingPath(processing);
      if (this.localOnly && processing === "hf") throw new Error("本地 MVP 不允许调用 Hugging Face。");
      if (processing === "local" && this.localFacingMode !== null && this.localFacingMode !== "environment")
        throw new Error("本地摄像头方向配置无效，仅支持明确指定后置摄像头。");
      if (processing === "local" && !this.localDeviceId && this.localFacingMode === null)
        throw new Error("请先明确选择本地摄像头。");
      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
        throw new Error("摄像头需要 HTTPS，请在手机系统浏览器中打开安全链接");
      }
      if (processing === "local" && !this.localDeviceId &&
          navigator.mediaDevices.getSupportedConstraints?.().facingMode !== true)
        throw new Error("当前浏览器不支持明确选择后置摄像头，请使用手机系统浏览器。");
      const stream = await navigator.mediaDevices.getUserMedia({
        video: {
          ...(processing === "local" ? this.localDeviceId
            ? {deviceId: {exact: this.localDeviceId}} : {facingMode: {exact: this.localFacingMode}}
            : {facingMode: {exact: this.camera.value}}),
          width: { ideal: 640 },
          height: { ideal: 480 },
        },
        audio: false,
      });
      if (token !== this.generation) {
        for (const track of stream.getTracks()) track.stop();
        return;
      }
      this.stream = stream;
      for (const track of stream.getTracks())
        track.addEventListener("ended", this.handleCameraEnded);
      this.video.srcObject = stream;
      this.cameraPhase = "video_start";
      await this.video.play();
      if (token !== this.generation) return;
      document.getElementById("local-placeholder").hidden = true;
      this.setStatus("正在连接服务端");
      const url = this.apiUrl(path);
      url.protocol = location.protocol === "https:" ? "wss:" : "ws:";
      const client = processing !== "echo"
        ? new LiveDetectionClient(url.href, this.handleDisconnect, this.handleSessionEnd,
          {provider: processing, ...(processing === "local" ? {localSessionSeconds: this.localSessionSeconds} : {})})
        : new EchoClient(url.href, this.handleDisconnect);
      this.client = client;
      this.cameraPhase = "connect";
      const contract = await client.connect();
      if (token !== this.generation) return;
      this.sessionId = contract?.session_id || `camera-${token}`;
      this.emit({type: "session", sessionId: this.sessionId, provider: processing, contract});
      this.setStatus(processing !== "echo" ? "正在检测" : "正在回传", "live");
      this.image.alt =
        processing !== "echo"
          ? "服务端 YOLO26 标注的摄像头图像"
          : "服务端原样返回的摄像头图像";
      document.getElementById("verified").textContent =
        processing !== "echo" ? "YOLO26 标注" : "字节一致 ✓";
      this.message.textContent =
        processing !== "echo"
          ? processing === "local" ? "YOLO26n 本地 CUDA 检测 80 类；图像仅发送本机服务。"
            : "YOLO26n 检测 80 类；Hugging Face ZeroGPU 返回标注图像。"
          : "每帧均经过服务端原样回传，字节校验通过后显示。";
      let sent = 0;
      let received = 0;
      this.frameTimings = null;
      document.getElementById("frames").textContent = "0 / 0";
      document.getElementById("fps").textContent = "—";
      if (processing !== "echo") {
        this.setStatus("等待首帧检测");
        await this.runPipeline(client, processing, token);
        return;
      }
      while (token === this.generation) {
        this.cameraPhase = "capture";
        const capturedAt = performance.now();
        const bytes = await this.capture();
        const encodedAt = performance.now();
        if (token !== this.generation) break;
        sent += 1;
        document.getElementById("frames").textContent = `${sent} / ${received}`;
        this.cameraPhase = "transport";
        const result = await client.sendFrame(bytes);
        const receivedAt = performance.now();
        if (token !== this.generation) break;
        this.cameraPhase = "decode";
        await this.showFrame(result.bytes, token);
        const decodedAt = performance.now();
        if (token !== this.generation) break;
        this.cameraPhase = "displayed";
        received += 1;
        this.publishFrame(processing, bytes.byteLength, result, {
          captureMs: encodedAt - capturedAt,
          transportMs: receivedAt - encodedAt,
          renderQueueMs: 0,
          decodeMs: decodedAt - receivedAt,
          captureToResultMs: receivedAt - capturedAt,
          captureToDecodedMs: decodedAt - capturedAt,
          completedAt: decodedAt,
        }, sent, received);
      }
    } catch (error) {
      if (token === this.generation) this.handleDisconnect(error);
    }
  }
  /** Functionality: Run the selected HF/local session without serial capture/response/display waits.
   * Inputs: client, processing snapshot, generation token. Outputs: pipeline completion/exception.
   * Logic: sender admits before capture; result subscribers/HUD retain original capture time and generation guards;
   * controller stop cancels the instance, and LiveDetectionClient owns provider end/error semantics.
   * Constraints: twelve total jobs, first frame alone,30FPS input ceiling; no retry or automatic next session. */
  async runPipeline(client, processing, token) {
    const app = this;
    /** Functionality: Decode the next ordered annotation. Inputs: bytes, metadata.
     * Outputs: showFrame Promise. Logic: validate actual decoded dimensions.
     * Constraints: does not block independent capture until the bounded jobs fill. */
    function display(bytes, metadata) { return app.showFrame(bytes, token, metadata); }
    /** Functionality: Publish one completed frame. Inputs: result, timing/context.
     * Outputs: HUD/state. Logic: generation guards and bounded timing aggregation.
     * Constraints: queued/late/cancelled frames are not counted as displayed. */
    function frame(result, timing) {
      if (token !== app.generation) return;
      app.lastDetection = {metadata: result.metadata, at: timing.capturedAt};
      app.setStatus("正在检测", "live");
      app.publishFrame(processing, timing.inputBytes, result, timing, timing.sent, timing.received);
      app.emit({type: "frame", sessionId: app.sessionId, provider: processing,
        result, capturedAt: timing.capturedAt, timing});
    }
    /** Functionality: Publish submitted/displayed counters. Inputs: sentCount, receivedCount.
     * Outputs: DOM. Logic: sender owns sent and renderer owns received.
     * Constraints: no success mark for a pending result. */
    function sent(sentCount, receivedCount) {
      if (token === app.generation)
        document.getElementById("frames").textContent = `${sentCount} / ${receivedCount}`;
    }
    /** Functionality: Check ownership. Inputs: generation token. Outputs: boolean.
     * Logic: stopped/older operations cannot update this camera session. Constraints: no auto-resume. */
    function active() { return token === app.generation; }
    this.pipeline = new FramePipeline({client, capture: this.capture.bind(this), display,
      onFrame: frame, onSent: sent, isActive: active, onFailure: this.handleDisconnect});
    this.cameraPhase = "pipeline";
    await this.pipeline.run();
  }
  /** Functionality: Publish frame metrics shared by serial and pipelined modes.
   * Inputs: processing, inputBytes count, result, timing, sent/received counts.
   * Outputs: HUD and bounded timing state. Logic: count only after successful decode; existing RTT
   * label stays request-to-validated-result, full capture/queue/decode timing is logged separately.
   * Constraints: no media persistence; first frame excluded explicitly from steady completion FPS. */
  publishFrame(processing, inputBytes, result, timing, sent, received) {
    const steadyFps = this.recordFrameTimings(processing, timing);
    if (processing !== "echo") {
      document.getElementById("verified").textContent = `YOLO26 · ${result.detections} 个目标`;
      this.message.textContent = `本帧 ${result.detections} 个目标 · 模型推理 ${result.inferenceMs.toFixed(0)} ms。未检出目标不代表道路安全。`;
    }
    document.getElementById("frames").textContent = `${sent} / ${received}`;
    document.getElementById("latency").textContent = result.rttMs.toFixed(0);
    document.getElementById("size").textContent = (inputBytes / 1024).toFixed(1);
    document.getElementById("fps").textContent = steadyFps === null ? "—" : steadyFps.toFixed(1);
  }
  /** Functionality: Release camera after an explicit bounded detector session end.
   * Inputs: active controller/client state. Outputs: stopped media and visible completed-session state.
   * Logic: stop admission, drain already-validated display, then release; user starts another session.
   * Constraints: forced stop/error still cancels promptly; no automatic GPU allocation. */
  async handleSessionEnd() {
    if (this.mode !== "camera") return;
    const token = this.generation;
    if (this.pipeline) {
      this.pipeline.finish();
      await this.pipeline.drain();
    }
    if (token !== this.generation) return;
    this.emit({type: "ended", sessionId: this.sessionId});
    this.stop();
    this.setStatus("本次视频会话已结束");
    this.message.textContent = this.processing.value === "local"
      ? "本地视频会话已结束并释放资源。再次点击开始可开启新会话。"
      : "ZeroGPU 视频会话已结束并释放资源。再次点击开始可开启新会话。";
  }
  /** Functionality: Capture JPEG while preserving aspect ratio. Inputs: Current video frame. Outputs: ArrayBuffer.
   * Logic: Limit the maximum edge to 640 pixels; resize the canvas only when dimensions change, then encode the frame.
   * Constraints: Report unavailable frames and payloads over 1 MiB; preserve the original aspect ratio, edge limit, and JPEG quality. */
  async capture() {
    const width = this.video.videoWidth;
    const height = this.video.videoHeight;
    if (!width || !height) throw new Error("摄像头尚无可用画面");
    const scale = Math.min(1, MAX_EDGE / Math.max(width, height));
    const scaledWidth = Math.max(1, Math.round(width * scale));
    const scaledHeight = Math.max(1, Math.round(height * scale));
    if (this.canvas.width !== scaledWidth) this.canvas.width = scaledWidth;
    if (this.canvas.height !== scaledHeight) this.canvas.height = scaledHeight;
    this.canvas
      .getContext("2d")
      .drawImage(this.video, 0, 0, this.canvas.width, this.canvas.height);
    const blob = await encodeJpeg(this.canvas);
    if (blob.size > MAX_FRAME_BYTES)
      throw new Error("当前图像超过 1 MiB，请停止并检查采集配置");
    const bytes = await blob.arrayBuffer();
    this.lastFrame = {bytes, at: performance.now()};
    return bytes;
  }
  /** Functionality: Provide the current JPEG for scene description. Inputs: maxAgeMs, lastFrame, and mode.
   * Outputs: ArrayBuffer; return null when the camera is off.
   * Logic: Reuse the latest encoded streaming frame when no older than maxAgeMs; otherwise call capture() under the same 640/JPEG 0.8 contract.
   * Constraints: Read without changing the stream; capture shares the pipeline canvas and draws the current video frame without a new protocol.
   * Do not request permission when the camera is off; propagate the original capture error. */
  async latestJpeg(maxAgeMs = SCENE_MAX_AGE_MS) {
    if (this.mode !== "camera") return null;
    const frame = this.lastFrame;
    if (frame && performance.now() - frame.at <= maxAgeMs) return frame.bytes;
    return this.capture();
  }
  /** Functionality: Provide the latest detection boxes for scene description. Inputs: maxAgeMs and lastDetection.
   * Outputs: boxes array, possibly empty. Logic: Calculate result age from original capture time so delayed frames are not treated as fresh.
   * Constraints: Return data without mutating metadata; return an empty array when metadata/boxes is missing or non-array, or in Echo/no-detection mode. Never fabricate boxes. */
  latestBoxes(maxAgeMs = SCENE_MAX_AGE_MS) {
    const detection = this.lastDetection;
    if (this.mode !== "camera" || !detection || performance.now() - detection.at > maxAgeMs) return [];
    const boxes = detection.metadata?.boxes;
    return Array.isArray(boxes) ? boxes : [];
  }
  /** Functionality: Display a server JPEG validated for the current mode. Inputs: bytes, token, optional dimensions metadata.
   * Outputs: Image decoding and URL lifecycle. Logic: HF also verifies actual naturalWidth/Height; finally release the prior frame.
   * Constraints: Decode/dimension failure stops processing; never display stale frames after generation changes; this does not prove final screen rendering. */
  async showFrame(bytes, token, dimensions = null) {
    const previous = this.objectUrl;
    this.objectUrl = URL.createObjectURL(
      new Blob([bytes], { type: "image/jpeg" }),
    );
    this.image.src = this.objectUrl;
    try {
      await this.image.decode();
      if (token !== this.generation) return;
      if (dimensions && (this.image.naturalWidth !== dimensions.width ||
          this.image.naturalHeight !== dimensions.height))
        throw new Error("视频图像尺寸与检测结果不匹配。");
      this.image.hidden = false;
      document.getElementById("echo-placeholder").hidden = true;
      document.getElementById("verified").hidden = false;
    } finally {
      if (previous) URL.revokeObjectURL(previous);
    }
  }
  /** Functionality: Aggregate one frame's phase timings and return completion rate after the first frame.
   * Inputs: processing mode; timing.capture/transport/renderQueue/decode, capture-to-result/decode milliseconds, and completedAt;
   * the pipeline may supply pacingLateMs/creditWaitMs to distinguish late timers from bounded credit waits.
   * Outputs: null for the first frame or a non-positive clock interval; otherwise completion FPS. Emit summaries initially and every five seconds.
   * Logic: Retain full first-frame timing; later frames update only count, sum, and maximum, not image history or an unbounded timing list.
   * Completion FPS counts frames from first-frame completion through the current frame, includes later pauses, and does not exclude slow frames.
   * Constraints: Record successful frames only; caller must check generation first; logs contain no image, credential, or device identity.
   */
  recordFrameTimings(processing, timing) {
    if (!this.frameTimings) {
      this.frameTimings = {
        processing, first: timing, steadyFrames: 0,
        lastCompletedAt: timing.completedAt, lastReportAt: timing.completedAt,
        captureTotalMs: 0, transportTotalMs: 0, renderQueueTotalMs: 0, decodeTotalMs: 0,
        captureToResultMaxMs: 0, captureToDecodedMaxMs: 0,
        pacingFrames: 0, pacingLateTotalMs: 0, pacingLateMaxMs: 0,
        creditWaitTotalMs: 0, creditWaitMaxMs: 0, creditWaitFrames: 0,
      };
      this.reportFrameTimings("first_frame");
      return null;
    }
    const metrics = this.frameTimings;
    metrics.steadyFrames += 1;
    metrics.lastCompletedAt = timing.completedAt;
    metrics.captureTotalMs += timing.captureMs;
    metrics.transportTotalMs += timing.transportMs;
    metrics.renderQueueTotalMs += timing.renderQueueMs;
    metrics.decodeTotalMs += timing.decodeMs;
    metrics.captureToResultMaxMs = Math.max(metrics.captureToResultMaxMs, timing.captureToResultMs);
    metrics.captureToDecodedMaxMs = Math.max(metrics.captureToDecodedMaxMs, timing.captureToDecodedMs);
    if (Number.isFinite(timing.pacingLateMs) && Number.isFinite(timing.creditWaitMs)) {
      metrics.pacingFrames += 1;
      metrics.pacingLateTotalMs += timing.pacingLateMs;
      metrics.pacingLateMaxMs = Math.max(metrics.pacingLateMaxMs, timing.pacingLateMs);
      metrics.creditWaitTotalMs += timing.creditWaitMs;
      metrics.creditWaitMaxMs = Math.max(metrics.creditWaitMaxMs, timing.creditWaitMs);
      if (timing.creditWaitMs > 0) metrics.creditWaitFrames += 1;
    }
    if (timing.completedAt - metrics.lastReportAt >= METRICS_LOG_INTERVAL_MS)
      this.reportFrameTimings("periodic");
    const elapsed = timing.completedAt - metrics.first.completedAt;
    return elapsed > 0 ? metrics.steadyFrames * 1000 / elapsed : null;
  }
  /** Functionality: Emit bounded aggregate statistics. Inputs: reason and instance frameTimings.
   * Outputs: One console.info entry. Logic: Report first frame separately from steady-state mean/max latency and completion FPS, then update log time;
   * when scheduling observations exist, report bounded mean/max and credit-wait frame count; otherwise use null.
   * Constraints: Emit nothing without frames; mean is not P95; decode completion does not prove screen presentation; do not retry or trigger inference.
   */
  reportFrameTimings(reason) {
    const metrics = this.frameTimings;
    if (!metrics) return;
    const count = metrics.steadyFrames;
    const elapsed = metrics.lastCompletedAt - metrics.first.completedAt;
    console.info("Camera frame timings", {
      reason, processing: metrics.processing, completedFrames: count + 1,
      firstFrameMs: {
        capture: metrics.first.captureMs, transport: metrics.first.transportMs,
        renderQueue: metrics.first.renderQueueMs, decode: metrics.first.decodeMs,
        captureToResult: metrics.first.captureToResultMs,
        captureToDecoded: metrics.first.captureToDecodedMs,
      },
      steadyFrames: count, steadyFps: count && elapsed > 0 ? count * 1000 / elapsed : null,
      steadyMeanMs: count ? {
        capture: metrics.captureTotalMs / count, transport: metrics.transportTotalMs / count,
        renderQueue: metrics.renderQueueTotalMs / count, decode: metrics.decodeTotalMs / count,
      } : null,
      steadyMaxMs: count ? {
        captureToResult: metrics.captureToResultMaxMs, captureToDecoded: metrics.captureToDecodedMaxMs,
      } : null,
      steadyScheduling: metrics.pacingFrames ? {
        frames: metrics.pacingFrames, pacingLateMeanMs: metrics.pacingLateTotalMs / metrics.pacingFrames,
        pacingLateMaxMs: metrics.pacingLateMaxMs, creditWaitFrames: metrics.creditWaitFrames,
        creditWaitMeanMs: metrics.creditWaitTotalMs / metrics.pacingFrames,
        creditWaitMaxMs: metrics.creditWaitMaxMs,
      } : null,
    });
    metrics.lastReportAt = metrics.lastCompletedAt;
  }
  /** Functionality: Validate ping/pong independently. Inputs: Current same-origin page URL. Outputs: Latency and status.
   * Logic: Open a temporary connection, verify the ID, then close it; Constraints: Do not open the camera or run concurrently with capture. */
  async testPing() {
    if (this.mode !== "idle") return;
    const token = ++this.generation;
    this.mode = "ping";
    this.setStatus("正在测试连接");
    try {
      const url = this.apiUrl("./ws/echo/");
      url.protocol = location.protocol === "https:" ? "wss:" : "ws:";
      const client = new EchoClient(url.href, this.handleDisconnect);
      this.client = client;
      await client.connect();
      const result = await client.ping();
      if (token !== this.generation) return;
      this.stop();
      document.getElementById("latency").textContent = result.rttMs.toFixed(0);
      this.setStatus("连接正常", "live");
      this.message.dataset.kind = "idle";
      this.message.textContent = "ping/pong 标识已匹配，可以开启摄像头回传。";
    } catch (error) {
      if (token === this.generation) this.handleDisconnect(error);
    }
  }
  /** Functionality: Stop work and release media resources. Inputs: Current stream, client, and Blob URL.
   * Outputs: Clear preview/echo, retain page statistics, and publish this run's summary; Logic: increment generation and cancel old async work.
   * Constraints: Safe to call repeatedly; do not resume automatically, require another user start. */
  stop() {
    this.emit({type: "stop", sessionId: this.sessionId});
    this.sessionId = null;
    this.generation += 1;
    this.reportFrameTimings("stop");
    this.frameTimings = null;
    this.cameraPhase = "idle";
    if (this.pipeline) this.pipeline.stop();
    this.pipeline = null;
    this.lastFrame = null;
    this.lastDetection = null;
    this.mode = "idle";
    this.video.hidden = false;
    if (this.client) this.client.stop();
    this.client = null;
    if (this.stream) {
      for (const track of this.stream.getTracks()) track.stop();
    }
    this.stream = null;
    this.video.srcObject = null;
    if (this.objectUrl) URL.revokeObjectURL(this.objectUrl);
    this.objectUrl = null;
    this.image.removeAttribute("src");
    this.image.hidden = true;
    document.getElementById("local-placeholder").hidden = false;
    document.getElementById("echo-placeholder").hidden = false;
    document.getElementById("verified").hidden = true;
    this.setStatus("已停止");
    this.message.dataset.kind = "idle";
    this.message.textContent = "摄像头和连接已释放。再次点击开始即可重新采集。";
  }
  /** Functionality: Report device interruption. Inputs: Media-track ended event. Outputs: Stop and error status.
   * Logic: Do not wait for network timeout; Constraints: Do not switch cameras. */
  handleCameraEnded() {
    this.handleDisconnect(
      new Error("摄像头已中断，请检查权限或设备后重新开始"),
    );
  }
  /** Functionality: Release resources when the page is backgrounded. Inputs: document.visibilityState.
   * Outputs: stop; Logic: Cancel current work while hidden; Constraints: Do not resume capture automatically on foreground return. */
  handleVisibility() {
    if (document.visibilityState === "hidden" && this.mode !== "idle")
      this.stop();
  }
  /** Functionality: Clean up when the page is left. Inputs: pagehide event. Outputs: stop.
   * Logic: Use common resource release; Constraints: Do not rely on asynchronous network work during browser unload. */
  handlePageHide() {
    this.stop();
  }
}

const app = new CameraApp();
export const cameraApp = app;
