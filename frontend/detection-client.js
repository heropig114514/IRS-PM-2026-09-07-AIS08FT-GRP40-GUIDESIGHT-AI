/* Responsibilities: 同源 YOLO 配置和 JPEG 标注请求的客户端契约。
 * Implementation: 单个在途请求、默认10秒（显式ZeroGPU60秒）、主动取消；不重试。
 * Related Modules: camera.js, echo-client.js；backend/core/vision.py/hf_vision.py。
 * Declaration Index: DetectionClient; DetectionClient.constructor; DetectionClient.request;
 * DetectionClient.connect; DetectionClient.sendFrame; DetectionClient.stop。
 * Variable Index: None；实例持有 url/fetchFn/timeoutMs/backend/controller/stopped。
 */
import { MAX_FRAME_BYTES, TIMEOUT_MS } from "./echo-client.js";

/* Functionality: 管理一个串行 HTTP 检测会话。
 * Logic: controller 代表唯一在途请求；停止后拒绝后续调用。
 * Constraints: 输出图像按 JPEG/模型/计数契约校验，不要求与输入字节相等。
 */
export class DetectionClient {
  /* Functionality: 初始化依赖。Inputs: url、可注入 fetchFn/timeoutMs/backend；Outputs: 会话实例。
   * Logic: 原生 fetch 绑定全局接收者，保存基础地址与取消状态；不立即联网或申请摄像头。
   * Constraints: 注入的 fetchFn 保留其测试语义；不得以类实例调用未绑定的 Window.fetch。
   */
  constructor(
    url,
    { fetchFn = globalThis.fetch.bind(globalThis), timeoutMs = TIMEOUT_MS, backend = null } = {},
  ) {
    this.url = url;
    this.fetchFn = fetchFn;
    this.timeoutMs = timeoutMs;
    this.backend = backend;
    this.controller = null;
    this.stopped = false;
  }

  /* Functionality: 执行一次可取消请求。Inputs: path、请求 options；Outputs: 已读取 JSON 或帧结果。
   * Logic: 定时器覆盖响应正文读取；同源、无 Cookie、拒绝重定向；finally 释放操作状态。
   * Constraints: 错误正文仅显示安全诊断，网络错误/超时均终止，不自动重试。
   */
  async request(path, options = {}) {
    if (this.stopped || this.controller)
      throw new Error("检测会话已停止或已有图像等待返回");
    const controller = new AbortController();
    this.controller = controller;
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    const started = performance.now();
    try {
      const response = await this.fetchFn(new URL(path, this.url).href, {
        ...options,
        mode: "same-origin",
        credentials: "omit",
        redirect: "error",
        cache: "no-store",
        signal: controller.signal,
      });
      if (!response.ok) {
        let data;
        try {
          data = await response.json();
        } catch {
          throw new Error(
            `检测接口返回错误（HTTP ${response.status}），请检查服务端。`,
          );
        }
        throw new Error(
          `${data.detail || "检测请求失败"}（${data.code || response.status}${data.request_id ? " / " + data.request_id : ""}）`,
        );
      }
      if (path === "config/") {
        const config = await response.json();
        if (this.stopped) throw new Error("已停止");
        return config;
      }
      if (
        response.headers.get("Content-Type")?.split(";")[0] !== "image/jpeg" ||
        response.headers.get("X-Model") !== "yolo26n" ||
        (this.backend && (
          response.headers.get("X-Inference-Backend") !== this.backend ||
          response.headers.get("X-Inference-Precision") !== "FP32"
        ))
      )
        throw new Error("服务端检测图像格式或模型不匹配");
      const count = response.headers.get("X-Detection-Count");
      const time = response.headers.get("X-Inference-Ms");
      const detections = Number(count),
        inferenceMs = Number(time);
      if (
        count === null ||
        time === null ||
        !Number.isInteger(detections) ||
        detections < 0 ||
        detections > 300 ||
        !Number.isFinite(inferenceMs) ||
        inferenceMs < 0
      )
        throw new Error("服务端检测统计格式无效");
      const bytes = await response.arrayBuffer();
      const jpeg = new Uint8Array(bytes);
      if (
        !jpeg.length ||
        jpeg.length > MAX_FRAME_BYTES ||
        jpeg[0] !== 255 ||
        jpeg[1] !== 216 ||
        jpeg.at(-2) !== 255 ||
        jpeg.at(-1) !== 217
      )
        throw new Error("返回图像不是有效大小的 JPEG");
      if (this.stopped) throw new Error("已停止");
      return {
        bytes,
        detections,
        inferenceMs,
        rttMs: performance.now() - started,
      };
    } catch (error) {
      if (this.stopped) throw new Error("已停止");
      if (controller.signal.aborted)
        throw new Error(`检测请求超过 ${this.timeoutMs / 1000} 秒，请检查服务端后手动开始`);
      if (error instanceof TypeError)
        throw new Error("检测网络请求失败，请检查连接");
      throw error;
    } finally {
      clearTimeout(timer);
      this.controller = null;
    }
  }

  /* Functionality: 确认服务端模型已就绪。Inputs: 实例 url；Outputs: config Promise 或错误。
   * Logic: 检查模型/80 类；指定 backend 时还检查所选后端/FP32，禁止串用响应。
   * Constraints: 配置成功不证明当前画面一定有目标。
   */
  async connect() {
    const config = await this.request("config/");
    if (
      config.ready !== true ||
      config.model !== "yolo26n" ||
      config.classes !== 80 ||
      (this.backend && (config.backend !== this.backend || config.precision !== "FP32"))
    )
      throw new Error("YOLO26 检测配置无效");
    return config;
  }

  /* Functionality: 提交 JPEG 并接收服务端标注图。Inputs: buffer ArrayBuffer；Outputs: 帧结果 Promise。
   * Logic: 校验现有 1 MiB 边界后 POST；Constraints: 不保存、重放或返回原始帧冒充标注。
   */
  sendFrame(buffer) {
    if (
      !(buffer instanceof ArrayBuffer) ||
      !buffer.byteLength ||
      buffer.byteLength > MAX_FRAME_BYTES
    )
      throw new Error("图像帧为空或超过 1 MiB 限制");
    return this.request("frame/", {
      method: "POST",
      headers: { "Content-Type": "image/jpeg" },
      body: buffer,
    });
  }

  /* Functionality: 取消会话。Inputs: 当前 controller；Outputs: abort 和 stopped 状态。
   * Logic: 浏览器丢弃响应，后端已提交的推理工作可能继续完成；Constraints: 不再发送新帧。
   */
  stop() {
    this.stopped = true;
    this.controller?.abort();
  }
}
