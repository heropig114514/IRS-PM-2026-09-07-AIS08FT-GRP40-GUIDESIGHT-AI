/**
 * Responsibilities: 摄像头授权、原样串行与ZeroGPU有界视频流水线、阶段计时和生命周期。
 * Implementation: 最大边640、JPEG0.8；HF首帧单独完成后30FPS采集、12帧上限、顺序显示；任何错误停止。
 * Related Modules: index.html, echo-client.js, live-detection-client.js,
 * frame-pipeline.js；core.echo/hf_video_relay；
 * tools/benchmark_phone_browser.mjs 验证真实页面；测试图流不代表手机实拍。
 * Declaration Index: processingPath; encodeJpeg; encodeJpeg.encode;
 * encodeJpeg.encode.ready;
 * CameraApp; CameraApp.constructor; CameraApp.apiUrl; CameraApp.setStatus; CameraApp.handleDisconnect;
 * CameraApp.handleProcessingChange; CameraApp.start; CameraApp.capture; CameraApp.showFrame; CameraApp.testPing;
 * CameraApp.runPipeline; CameraApp.runPipeline.display;
 * CameraApp.runPipeline.frame; CameraApp.runPipeline.sent; CameraApp.runPipeline.active;
 * CameraApp.publishFrame; CameraApp.handleSessionEnd;
 * CameraApp.recordFrameTimings; CameraApp.reportFrameTimings; CameraApp.stop;
 * CameraApp.handleCameraEnded; CameraApp.handleVisibility; CameraApp.handlePageHide。
 * Variable Index: MAX_EDGE/JPEG_QUALITY 为采集参数；METRICS_LOG_INTERVAL_MS 为汇总日志间隔；
 * PROCESSING_PATHS 为唯一两个WS模式与固定路径；app 为页面控制器；实例 frameTimings 保存首帧和有限聚合统计，
 * pacingFrames/pacingLateTotalMs/pacingLateMaxMs及creditWaitTotalMs/creditWaitMaxMs/creditWaitFrames
 * 仅聚合带有流水线调度观测的稳态帧，不把串行Echo缺失的观测计为零；
 * cameraPhase 定位操作阶段，pipeline持有有界作业/取消信号；apiBase为同源API挂载点。
 */
import { EchoClient, MAX_FRAME_BYTES } from "./echo-client.js";
import { LiveDetectionClient } from "./live-detection-client.js";
import { FramePipeline } from "./frame-pipeline.js";

const MAX_EDGE = 640;
const JPEG_QUALITY = 0.8;
const METRICS_LOG_INTERVAL_MS = 5000;
const PROCESSING_PATHS = {echo: "./ws/echo/", hf: "./ws/detect/hf/"};

/** Functionality: Resolve one of the two supported processing modes.
 * Inputs: processing enum. Outputs: fixed relative WS path; unknown values throw.
 * Logic: explicit allowlist rejects retired modes before camera authorization.
 * Constraints: no HTTP detector, alternate provider or automatic mode substitution. */
export function processingPath(processing) {
  if (!Object.hasOwn(PROCESSING_PATHS, processing)) throw new Error("未知图像处理模式");
  return PROCESSING_PATHS[processing];
}

/** Functionality: 编码当前画布。Inputs: canvas。Outputs: JPEG Blob Promise。
 * Logic: 使用固定 JPEG_QUALITY；Constraints: 编码不支持时明确失败，不换格式。 */
function encodeJpeg(canvas) {
  /** Functionality: 绑定一次异步编码。Inputs: resolve/reject。Outputs: 无。
   * Logic: 浏览器 toBlob 处理当前画布；Constraints: Blob 为空即错误。 */
  function encode(resolve, reject) {
    /** Functionality: 发布编码结果。Inputs: blob。Outputs: resolve 或 reject。
     * Logic: 核对 MIME，禁止悄悄切换 PNG；Constraints: 不写本地文件。 */
    function ready(blob) {
      if (!blob || blob.type !== "image/jpeg")
        reject(new Error("浏览器无法编码 JPEG 图像"));
      else resolve(blob);
    }
    canvas.toBlob(ready, "image/jpeg", JPEG_QUALITY);
  }
  return new Promise(encode);
}

/** Functionality: 管理手机摄像头诊断工作台。
 * Logic: generation 隔离取消后的异步返回；mode 防止重复开始或并发 ping。
 * Constraints: 不录音、不保存帧；页面隐藏、设备结束、停止或故障均释放摄像头。 */
export class CameraApp {
  /** Functionality: 绑定控件。Inputs: 当前 document/window。Outputs: 实例及事件注册。
   * Logic: processing选择模式；保存媒体/客户端/计时；apiBase由页面相对同源挂载点解析；不自动申请权限。
   * Constraints: 运行时锁定模式，停止后才能切换。 */
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
    this.pipeline = null;
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
  /** Functionality: 显示第三方图像处理及等待预算。Inputs: processing 当前值。
   * Outputs: 提示可见性；Logic: 仅 hf 选择显示；Constraints: 不联网或申请权限。 */
  handleProcessingChange() {
    document.getElementById("hf-note").hidden = this.processing.value !== "hf";
  }
  /** Functionality: 更新状态与操作边界。Inputs: text、kind。Outputs: DOM 修改。
   * Logic: mode 决定控件可操作性；Constraints: text 仅用 textContent，不插入 HTML。 */
  setStatus(text, kind = "idle") {
    this.status.textContent = text;
    this.status.dataset.kind = kind;
    this.startButton.disabled = this.mode !== "idle";
    this.pingButton.disabled = this.mode !== "idle";
    this.camera.disabled = this.mode !== "idle";
    this.processing.disabled = this.mode !== "idle";
    this.stopButton.disabled = this.mode !== "camera";
  }
  /** Functionality: 处理连接失败。Inputs: Error。Outputs: 释放资源及可读提示。
   * Logic: 记录mode、摄像头阶段和已完成数量后停止并显示原因。
   * Constraints: 日志无帧数据/凭据，其他操作的cameraPhase可为idle，不推断远端故障原因。 */
  handleDisconnect(error) {
    console.error("Camera processing failed:", {
      message: error.message, mode: this.mode, cameraPhase: this.cameraPhase,
      completedFrames: this.frameTimings ? this.frameTimings.steadyFrames + 1 : 0,
    });
    this.stop();
    this.setStatus("连接失败", "error");
    this.message.textContent = error.message;
    this.message.dataset.kind = "error";
  }
  /** Functionality: 授权摄像头并使用显式所选模式。Inputs: facingMode、processing、浏览器媒体 API。
   * Outputs: 本地 video、返回 img、往返时间及首帧后的完成速率。
   * Logic: 原样保持单在途；HF使用同源持久WS与当前默认采样/8作业流水线；首帧单列。
   * Constraints: exact镜头；检测失败停止，不切换模式或自动重新授权；不丢弃已发送帧来制造速率；
   * 没有固定等待不保证达到10FPS；计时从 drawImage 前开始，不等于传感器曝光或屏幕实际绘制时间。 */
  async start() {
    if (this.mode !== "idle") return;
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
      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
        throw new Error("摄像头需要 HTTPS，请在手机系统浏览器中打开安全链接");
      }
      const stream = await navigator.mediaDevices.getUserMedia({
        video: {
          facingMode: { exact: this.camera.value },
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
      const client = processing === "hf"
        ? new LiveDetectionClient(url.href, this.handleDisconnect, this.handleSessionEnd)
        : new EchoClient(url.href, this.handleDisconnect);
      this.client = client;
      this.cameraPhase = "connect";
      await client.connect();
      if (token !== this.generation) return;
      this.setStatus(processing !== "echo" ? "正在检测" : "正在回传", "live");
      this.image.alt =
        processing !== "echo"
          ? "服务端 YOLO26 标注的摄像头图像"
          : "服务端原样返回的摄像头图像";
      document.getElementById("verified").textContent =
        processing !== "echo" ? "YOLO26 标注" : "字节一致 ✓";
      this.message.textContent =
        processing !== "echo"
          ? "YOLO26n 检测 80 类；Hugging Face ZeroGPU 返回标注图像。"
          : "每帧均经过服务端原样回传，字节校验通过后显示。";
      let sent = 0;
      let received = 0;
      this.frameTimings = null;
      document.getElementById("frames").textContent = "0 / 0";
      document.getElementById("fps").textContent = "—";
      if (processing === "hf") {
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
  /** Functionality: Run the selected HF session without serial capture/response/display waits.
   * Inputs: client, processing snapshot, generation token. Outputs: pipeline completion/exception.
   * Logic: sender admits before capture; display and HUD callbacks have generation guards;
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
      app.setStatus("正在检测", "live");
      app.publishFrame(processing, timing.inputBytes, result, timing, timing.sent, timing.received);
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
  /** Functionality: Release camera after an explicit bounded HF session end.
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
    this.stop();
    this.setStatus("本次视频会话已结束");
    this.message.textContent = "ZeroGPU 视频会话已结束并释放资源。再次点击开始可开启新会话。";
  }
  /** Functionality: 按原长宽比采集 JPEG。Inputs: video 当前帧。Outputs: ArrayBuffer。
   * Logic: 限制最大边 640；仅尺寸改变时重置画布，随后编码当前帧。
   * Constraints: 无可用画面或超过 1 MiB 时明确错误；比例、最大边及 JPEG 质量保持原条件。 */
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
    return blob.arrayBuffer();
  }
  /** Functionality: 显示当前模式校验后的服务端JPEG。Inputs: bytes、token、可选dimensions元数据。
   * Outputs: img解码及URL生命周期。Logic: HF还校验实际naturalWidth/Height；finally释放旧帧。
   * Constraints: 解码/尺寸失败终止；代际变化后不显示旧帧；不代表屏幕实际绘制已完成。 */
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
  /** Functionality: 聚合一帧的阶段计时并返回首帧之后的完成速率。
   * Inputs: processing模式；timing中capture/transport/renderQueue/decode、采集到结果/解码的毫秒数和completedAt；
   * 流水线可选pacingLateMs/creditWaitMs区分定时器晚醒与有界信用等待。
   * Outputs: 首帧或时钟尚无正间隔时返回 null，其余返回完成 FPS；首次及每5秒发布汇总日志。
   * Logic: 首帧保留完整耗时；后续只累计数量、总和、最大值，不保存历史图像或无限计时列表；
   * 完成速率为首帧完成至本帧完成的区间计数，包含后续停顿，未排除慢帧。
   * Constraints: 只记录成功完成的帧；调用者必须先检查 generation；日志不含图像、凭据或设备身份。
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
  /** Functionality: 输出有限聚合统计。Inputs: reason、实例 frameTimings。
   * Outputs: 一条 console.info；Logic: 首帧单列，稳态均值/最大延迟及完成 FPS 单列，更新日志时间；
   * 存在调度观测时输出有限均值/最大值和信用等待帧数，缺失时为null。
   * Constraints: 无帧时不输出；均值不是P95；解码完成不证明屏幕已绘制；不会重试或触发推理。
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
  /** Functionality: 单独验证 ping/pong。Inputs: 当前页面同源地址。Outputs: 延迟与状态。
   * Logic: 建立临时连接、校验标识后关闭；Constraints: 不开启摄像头，不能与采集并发。 */
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
  /** Functionality: 停止操作并释放媒体资源。Inputs: 当前流、客户端、Blob URL。
   * Outputs: 清空预览和回显，保留页面统计并发布本次汇总；Logic: 递增 generation，取消旧异步操作。
   * Constraints: 可重复调用；不自动恢复，需用户再次点击开始。 */
  stop() {
    this.generation += 1;
    this.reportFrameTimings("stop");
    this.frameTimings = null;
    this.cameraPhase = "idle";
    if (this.pipeline) this.pipeline.stop();
    this.pipeline = null;
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
  /** Functionality: 报告设备中断。Inputs: 媒体轨道 ended 事件。Outputs: 停止及错误提示。
   * Logic: 不等待网络超时；Constraints: 不替换为其他摄像头。 */
  handleCameraEnded() {
    this.handleDisconnect(
      new Error("摄像头已中断，请检查权限或设备后重新开始"),
    );
  }
  /** Functionality: 页面转入后台时释放资源。Inputs: document.visibilityState。
   * Outputs: stop；Logic: 隐藏状态取消当前操作；Constraints: 回到前台不自动恢复采集。 */
  handleVisibility() {
    if (document.visibilityState === "hidden" && this.mode !== "idle")
      this.stop();
  }
  /** Functionality: 页面离开时清理。Inputs: pagehide 事件。Outputs: stop。
   * Logic: 统一释放；Constraints: 不依赖浏览器卸载等待异步网络。 */
  handlePageHide() {
    this.stop();
  }
}

const app = new CameraApp();
