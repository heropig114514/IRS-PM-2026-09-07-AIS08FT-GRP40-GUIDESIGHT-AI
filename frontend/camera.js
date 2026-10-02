/**
 * Responsibilities: 摄像头授权、串行采集、原样/CPU/laptop YOLO 回显和生命周期。
 * Implementation: 最大边 640 像素、JPEG 0.8、每次回显后等待 200 ms；任何错误停止。
 * Related Modules: index.html, echo-client.js, detection-client.js；core.echo/vision/laptop_vision。
 * Declaration Index: detectionUrl; delay; delay.wait; encodeJpeg; encodeJpeg.encode;
 * encodeJpeg.encode.ready;
 * CameraApp; CameraApp.constructor; CameraApp.setStatus; CameraApp.handleDisconnect;
 * CameraApp.start; CameraApp.capture; CameraApp.showFrame; CameraApp.testPing;
 * CameraApp.testDetection; CameraApp.stop;
 * CameraApp.handleCameraEnded; CameraApp.handleVisibility; CameraApp.handlePageHide。
 * Variable Index: FRAME_INTERVAL_MS/MAX_EDGE/JPEG_QUALITY 为明确采集参数；app 为页面控制器。
 */
import { EchoClient, MAX_FRAME_BYTES } from "./echo-client.js";
import { DetectionClient } from "./detection-client.js";

const FRAME_INTERVAL_MS = 200;
const MAX_EDGE = 640;
const JPEG_QUALITY = 0.8;

/** Functionality: 映射显式检测模式。Inputs: processing、href。Outputs: 同源 API URL。
 * Logic: laptop 只指向 laptop 转发，其余检测指向 CPU。
 * Constraints: 不接受任意推理主机，不自动探测或切换后端。 */
export function detectionUrl(processing, href) {
  if (!["echo", "detect", "laptop"].includes(processing))
    throw new Error("未知图像处理模式");
  return new URL(processing === "laptop" ? "./vision/laptop/" : "./vision/", href);
}

/** Functionality: 控制串行发送间隔。Inputs: milliseconds。Outputs: Promise。
 * Logic: 单次计时，不追赶遗漏帧；Constraints: 停止由调用者的 generation 检查保证。 */
function delay(milliseconds) {
  /** Functionality: 注册计时完成。Inputs: resolve。Outputs: 无。
   * Logic: 固定延迟后完成 Promise；Constraints: 不执行重试或额外采集。 */
  function wait(resolve) {
    setTimeout(resolve, milliseconds);
  }
  return new Promise(wait);
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
   * Logic: processing 选择原样/检测；保存单个媒体流、客户端和 Blob URL；不自动申请权限。
   * Constraints: 运行时锁定模式，停止后才能切换。 */
  constructor() {
    this.video = document.getElementById("preview");
    this.image = document.getElementById("echo");
    this.startButton = document.getElementById("start");
    this.stopButton = document.getElementById("stop");
    this.pingButton = document.getElementById("ping");
    this.sampleButton = document.getElementById("sample");
    this.sampleImage = document.getElementById("sample-preview");
    this.sampleAbort = null;
    this.camera = document.getElementById("camera");
    this.processing = document.getElementById("processing");
    this.status = document.getElementById("status");
    this.message = document.getElementById("message");
    this.canvas = document.createElement("canvas");
    this.stream = null;
    this.client = null;
    this.objectUrl = null;
    this.generation = 0;
    this.mode = "idle";
    this.start = this.start.bind(this);
    this.stop = this.stop.bind(this);
    this.testPing = this.testPing.bind(this);
    this.testDetection = this.testDetection.bind(this);
    this.handleDisconnect = this.handleDisconnect.bind(this);
    this.handleCameraEnded = this.handleCameraEnded.bind(this);
    this.handleVisibility = this.handleVisibility.bind(this);
    this.handlePageHide = this.handlePageHide.bind(this);
    this.startButton.addEventListener("click", this.start);
    this.stopButton.addEventListener("click", this.stop);
    this.pingButton.addEventListener("click", this.testPing);
    this.sampleButton.addEventListener("click", this.testDetection);
    document.addEventListener("visibilitychange", this.handleVisibility);
    window.addEventListener("pagehide", this.handlePageHide);
  }
  /** Functionality: 更新状态与操作边界。Inputs: text、kind。Outputs: DOM 修改。
   * Logic: mode 决定控件可操作性；Constraints: text 仅用 textContent，不插入 HTML。 */
  setStatus(text, kind = "idle") {
    this.status.textContent = text;
    this.status.dataset.kind = kind;
    this.startButton.disabled = this.mode !== "idle";
    this.pingButton.disabled = this.mode !== "idle";
    this.sampleButton.disabled = this.mode !== "idle";
    this.camera.disabled = this.mode !== "idle";
    this.processing.disabled = this.mode !== "idle";
    this.stopButton.disabled = !["camera", "sample"].includes(this.mode);
  }
  /** Functionality: 处理连接失败。Inputs: Error。Outputs: 释放资源及可读提示。
   * Logic: 主动停止后显示原因；Constraints: 控制台只记录错误，绝不记录帧数据。 */
  handleDisconnect(error) {
    console.error("Camera processing failed:", error.message);
    this.stop();
    this.setStatus("连接失败", "error");
    this.message.textContent = error.message;
    this.message.dataset.kind = "error";
  }
  /** Functionality: 授权摄像头并串行处理。Inputs: facingMode、processing、浏览器媒体 API。
   * Outputs: 本地 video、返回 img 和统计；Logic: 模式快照决定明确的客户端和结果说明。
   * Constraints: exact 镜头；检测失败停止，不切换模式或自动重新授权；采集参数不变。 */
  async start() {
    if (this.mode !== "idle") return;
    const token = ++this.generation;
    this.mode = "camera";
    this.sampleImage.hidden = true;
    this.video.hidden = false;
    document.getElementById("capture-source").textContent = "01 / 本地镜头";
    this.setStatus("等待摄像头授权");
    this.message.dataset.kind = "idle";
    try {
      const processing = this.processing.value;
      if (!["echo", "detect", "laptop"].includes(processing))
        throw new Error("未知图像处理模式");
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
      await this.video.play();
      if (token !== this.generation) return;
      document.getElementById("local-placeholder").hidden = true;
      this.setStatus("正在连接服务端");
      const url = new URL(
        processing !== "echo" ? detectionUrl(processing, window.location.href).href : "./ws/echo/",
        window.location.href,
      );
      if (processing === "echo")
        url.protocol = location.protocol === "https:" ? "wss:" : "ws:";
      const client =
        processing !== "echo"
          ? new DetectionClient(url.href, {backend: processing === "laptop" ? "TensorRT" : null})
          : new EchoClient(url.href, this.handleDisconnect);
      this.client = client;
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
          ? `YOLO26n 检测 80 类；${processing === "laptop" ? "Laptop GPU" : "服务器 CPU"} 返回标注图像。`
          : "每帧均经过服务端原样回传，字节校验通过后显示。";
      let sent = 0;
      let received = 0;
      const started = performance.now();
      document.getElementById("frames").textContent = "0 / 0";
      while (token === this.generation) {
        const bytes = await this.capture();
        if (token !== this.generation) break;
        sent += 1;
        document.getElementById("frames").textContent = `${sent} / ${received}`;
        const result = await client.sendFrame(bytes);
        if (token !== this.generation) break;
        await this.showFrame(result.bytes, token);
        if (token !== this.generation) break;
        received += 1;
        if (processing !== "echo") {
          document.getElementById("verified").textContent =
            `YOLO26 · ${result.detections} 个目标`;
          this.message.textContent = `本帧 ${result.detections} 个目标 · 模型推理 ${result.inferenceMs.toFixed(0)} ms。未检出目标不代表道路安全。`;
        }
        document.getElementById("frames").textContent = `${sent} / ${received}`;
        document.getElementById("latency").textContent =
          result.rttMs.toFixed(0);
        document.getElementById("size").textContent = (
          bytes.byteLength / 1024
        ).toFixed(1);
        document.getElementById("fps").textContent = (
          (received * 1000) /
          (performance.now() - started)
        ).toFixed(1);
        await delay(FRAME_INTERVAL_MS);
      }
    } catch (error) {
      if (token === this.generation) this.handleDisconnect(error);
    }
  }
  /** Functionality: 按原长宽比采集 JPEG。Inputs: video 当前帧。Outputs: ArrayBuffer。
   * Logic: 限制最大边 640 再编码；Constraints: 无可用画面或超过 1 MiB 时明确错误。 */
  async capture() {
    const width = this.video.videoWidth;
    const height = this.video.videoHeight;
    if (!width || !height) throw new Error("摄像头尚无可用画面");
    const scale = Math.min(1, MAX_EDGE / Math.max(width, height));
    this.canvas.width = Math.max(1, Math.round(width * scale));
    this.canvas.height = Math.max(1, Math.round(height * scale));
    this.canvas
      .getContext("2d")
      .drawImage(this.video, 0, 0, this.canvas.width, this.canvas.height);
    const blob = await encodeJpeg(this.canvas);
    if (blob.size > MAX_FRAME_BYTES)
      throw new Error("当前图像超过 1 MiB，请停止并检查采集配置");
    return blob.arrayBuffer();
  }
  /** Functionality: 显示当前模式校验后的服务端 JPEG。Inputs: bytes、token。Outputs: img 解码及 URL 生命周期。
   * Logic: 新 Blob URL 成为当前帧，finally 释放旧帧；Constraints: 解码失败不显示成功标记。 */
  async showFrame(bytes, token) {
    const previous = this.objectUrl;
    this.objectUrl = URL.createObjectURL(
      new Blob([bytes], { type: "image/jpeg" }),
    );
    this.image.src = this.objectUrl;
    try {
      await this.image.decode();
      if (token !== this.generation) return;
      this.image.hidden = false;
      document.getElementById("echo-placeholder").hidden = true;
      document.getElementById("verified").hidden = false;
    } finally {
      if (previous) URL.revokeObjectURL(previous);
    }
  }
  /** Functionality: 单独验证 ping/pong。Inputs: 当前页面同源地址。Outputs: 延迟与状态。
   * Logic: 建立临时连接、校验标识后关闭；Constraints: 不开启摄像头，不能与采集并发。 */
  async testPing() {
    if (this.mode !== "idle") return;
    const token = ++this.generation;
    this.mode = "ping";
    this.setStatus("正在测试连接");
    try {
      const url = new URL("./ws/echo/", location.href);
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
  /** Functionality: 用公开样例验证当前选择的检测后端。Inputs: processing、同源 JPEG、API。
   * Outputs: 输入/标注图、目标数和往返时间；Logic: 只发送一帧，generation 隔离取消。
   * Constraints: 原样模式的样例仍测试 CPU；laptop 模式只测试 GPU，不训练/存帧/重试。 */
  async testDetection() {
    if (this.mode !== "idle") return;
    const token = ++this.generation;
    const processing = this.processing.value;
    this.mode = "sample";
    this.setStatus("正在测试 YOLO26");
    this.message.dataset.kind = "idle";
    const controller = new AbortController();
    this.sampleAbort = controller;
    const timer = setTimeout(() => controller.abort(), 10000);
    try {
      const sampleUrl = new URL("./assets/detection-sample.jpg", location.href);
      const response = await fetch(sampleUrl, {
        signal: controller.signal,
        credentials: "omit",
        cache: "no-store",
      });
      if (!response.ok)
        throw new Error(`样例读取失败：HTTP ${response.status}`);
      const bytes = await response.arrayBuffer();
      clearTimeout(timer);
      this.sampleAbort = null;
      if (token !== this.generation) return;
      this.video.hidden = true;
      this.sampleImage.src = sampleUrl.href;
      this.sampleImage.hidden = false;
      document.getElementById("capture-source").textContent =
        "01 / 公开样例（非摄像头）";
      document.getElementById("local-placeholder").hidden = true;
      this.image.alt = "服务端 YOLO26 标注的公开测试图";
      const client = new DetectionClient(
        detectionUrl(processing, location.href).href,
        {backend: processing === "laptop" ? "TensorRT" : null},
      );
      this.client = client;
      await client.connect();
      if (token !== this.generation) return;
      document.getElementById("frames").textContent = "1 / 0";
      const result = await client.sendFrame(bytes);
      if (token !== this.generation) return;
      await this.showFrame(result.bytes, token);
      if (token !== this.generation) return;
      document.getElementById("verified").textContent =
        `YOLO26 · ${result.detections} 个目标`;
      document.getElementById("frames").textContent = "1 / 1";
      document.getElementById("latency").textContent = result.rttMs.toFixed(0);
      document.getElementById("size").textContent = (
        bytes.byteLength / 1024
      ).toFixed(1);
      document.getElementById("fps").textContent = "—";
      client.stop();
      this.client = null;
      this.mode = "idle";
      this.setStatus("示例已标注", "live");
      this.message.textContent = `公开样例检出 ${result.detections} 个目标 · ${processing === "laptop" ? "Laptop TensorRT" : "服务器 CPU"} 推理 ${result.inferenceMs.toFixed(0)} ms。此图不是实时摄像头画面。`;
    } catch (error) {
      if (token === this.generation) this.handleDisconnect(error);
    } finally {
      clearTimeout(timer);
      if (this.sampleAbort === controller) this.sampleAbort = null;
    }
  }
  /** Functionality: 停止操作并释放媒体资源。Inputs: 当前流、客户端、样例请求、Blob URL。
   * Outputs: 清空预览和回显，保留统计；Logic: 递增 generation，取消旧异步操作。
   * Constraints: 可重复调用；不自动恢复，需用户再次点击开始。 */
  stop() {
    this.generation += 1;
    this.mode = "idle";
    if (this.sampleAbort) this.sampleAbort.abort();
    this.sampleAbort = null;
    this.sampleImage.hidden = true;
    this.sampleImage.removeAttribute("src");
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
