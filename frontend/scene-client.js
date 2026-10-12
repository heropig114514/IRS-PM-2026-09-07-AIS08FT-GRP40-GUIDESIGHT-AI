/** Responsibilities: 摄像头页“描述场景”：取最近帧与检测结果，请求 POST /scene/describe/，显示并按需朗读。
 * Implementation: 复用 CameraApp 已编码 JPEG(640/0.8)与最近 HF 检测框，映射为契约 objects(<=50，按
 * alert_priority/距离/置信度排序)；同源 fetch，单次在途，无重试/降级；错误按后端 code 本地化；
 * 仅当语音输出开关已启用时调用共享 SpeechOutput.speak。
 * Related Modules: index.html, camera.js(latestJpeg/latestBoxes), speech-output.js, i18n.js,
 * translations.js; backend core.scene/core.vlm 与 core.camera.ASSETS；tests/scene-client.test.mjs。
 * Declaration Index: SceneError; mapBox; mapObjects; encodeBase64; sceneLanguage; errorMessage; resolveSceneUrl;
 * SceneClient; SceneClient.constructor; SceneClient.setBusy; SceneClient.show; SceneClient.fail;
 * SceneClient.buildRequest; SceneClient.describe; SceneClient.speak。
 * Variable Index: MAX_OBJECTS=50/MAX_QUESTION_CHARS=200 为契约上限；REQUEST_TIMEOUT_MS 为浏览器等待上限
 * （大于后端20s上游超时）；ERROR_MESSAGES 为后端错误码到中文界面键(由 translations.js 译英)；
 * sceneClient 为摄像头页实例，其他页/测试为 null。实例 busy 为单次在途标记。
 * 不保存图像、问题或回答，日志只含 code/request_id/对象数。
 */
export const MAX_OBJECTS = 50;
export const MAX_QUESTION_CHARS = 200;
export const REQUEST_TIMEOUT_MS = 30000;

/** Functionality: 携带已本地化界面文案的可预期错误。Logic: message 必须是 translations.js 中的键。
 * Constraints: 只有此类错误的 message 会直接展示；其他异常只显示通用失败文案。 */
export class SceneError extends Error {}
const DIRECTIONS = ["front", "front_left", "front_right", "left", "right", "behind", "unknown"];
const RISK_LEVELS = ["low", "medium", "high", "critical", "unknown"];
const MOTION_STATES = ["approaching", "moving_away", "stationary", "unknown"];
export const ERROR_MESSAGES = {
  origin_rejected: "请求来源被拒绝，请从本站页面访问。",
  invalid_content_type: "请求格式无效，请刷新页面后重试。",
  request_too_large: "图像过大，请重试。",
  invalid_image: "图像无效，请重新采集后重试。",
  invalid_scene_input: "提问或检测数据无效，请检查后重试。",
  vlm_not_configured: "场景描述服务尚未配置。",
  vlm_rate_limit: "场景描述请求过于频繁，请稍后再试。",
  vlm_capacity: "场景描述服务繁忙，请稍后再试。",
  vlm_authentication_failed: "场景描述服务认证失败，请联系管理员。",
  vlm_quota_exceeded: "场景描述额度已用尽，请联系管理员。",
  vlm_upstream_error: "场景描述上游服务出错，请稍后再试。",
  vlm_timeout: "场景描述超时，请稍后再试。",
  vlm_invalid_response: "场景描述响应无效，请稍后再试。",
  vlm_response_too_large: "场景描述响应无效，请稍后再试。",
};

/** Functionality: 把一个检测框映射为契约 object。Inputs: HF box。Outputs: object 或 null。
 * Logic: label/confidence 必需；其余字段仅在值合法时携带，缺失即省略（不编造距离/方向）。
 * Constraints: distance_m 只用原始深度值，不用平滑值替代；非法 label 的框被丢弃。 */
export function mapBox(box) {
  if (!box || typeof box.label !== "string" || !box.label || box.label.length > 40) return null;
  const confidence = Number(box.score);
  if (!Number.isFinite(confidence) || confidence < 0 || confidence > 1) return null;
  const item = {label: box.label, confidence};
  if (Number.isFinite(box.distance_m) && box.distance_m > 0) item.distance_m = box.distance_m;
  if (DIRECTIONS.includes(box.direction)) item.direction = box.direction;
  if (RISK_LEVELS.includes(box.risk_level)) item.risk_level = box.risk_level;
  if (MOTION_STATES.includes(box.motion_state)) item.motion_state = box.motion_state;
  if (Number.isSafeInteger(box.track_id) && box.track_id >= 0) item.track_id = box.track_id;
  return item;
}

/** Functionality: 选取并映射最重要的 <=50 个对象。Inputs: boxes 数组。Outputs: objects 数组。
 * Logic: 先按 alert_priority 降序，再按距离升序(无距离靠后)，再按置信度降序；稳定排序后截断。
 * Constraints: 不修改输入；非数组视为空。 */
export function mapObjects(boxes) {
  if (!Array.isArray(boxes)) return [];
  const ranked = boxes.map((box, index) => ({box, index})).filter(entry => mapBox(entry.box));
  ranked.sort((a, b) => {
    const priority = (b.box.alert_priority || 0) - (a.box.alert_priority || 0);
    if (priority) return priority;
    const da = Number.isFinite(a.box.distance_m) ? a.box.distance_m : Infinity;
    const db = Number.isFinite(b.box.distance_m) ? b.box.distance_m : Infinity;
    if (da !== db) return da < db ? -1 : 1;
    return (b.box.score - a.box.score) || (a.index - b.index);
  });
  return ranked.slice(0, MAX_OBJECTS).map(entry => mapBox(entry.box));
}

/** Functionality: ArrayBuffer 转标准 base64。Inputs: ArrayBuffer/Uint8Array。Outputs: 字符串。
 * Logic: 分块 fromCharCode 后 btoa，避免大数组展开栈溢出。Constraints: 只在内存中转换。 */
export function encodeBase64(buffer) {
  const bytes = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer);
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 0x8000)
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  return btoa(binary);
}

/** Functionality: 页面语言到契约 language。Inputs: document.documentElement.lang。
 * Outputs: "zh-CN" | "en-US"。Logic: i18n.js 只会写 zh-CN 或 en。Constraints: 其余一律英文。 */
export function sceneLanguage(lang) { return /^zh(?:-|$)/i.test(lang || "") ? "zh-CN" : "en-US"; }

/** Functionality: 后端错误码到界面键。Inputs: code, requestId。Outputs: 文本。
 * Logic: 已知码用固定键，未知码用通用键；request_id 以全角括号附在末尾供 i18n 保留。
 * Constraints: 不显示 detail 原文（可能含上游信息）。 */
export function errorMessage(code, requestId) {
  const base = Object.hasOwn(ERROR_MESSAGES, code) ? ERROR_MESSAGES[code]
    : typeof code === "string" && /^[a-z_]{1,48}$/.test(code) ? `场景描述失败：${code}` : "场景描述失败。";
  return typeof requestId === "string" && /^[\w-]{1,64}$/.test(requestId) ? `${base}（${requestId}）` : base;
}

/** Functionality: 解析同源 API 地址。Inputs: apiBase, path, pageHref。Outputs: URL。
 * Logic: 同 CameraApp.apiUrl 的 data-api-base 约定；Constraints: 跨源抛错，无回退。 */
export function resolveSceneUrl(apiBase, path, pageHref) {
  const page = new URL(pageHref), base = new URL(apiBase, page), url = new URL(path, base);
  if (base.origin !== page.origin || url.origin !== page.origin) throw new SceneError("场景描述接口必须与页面同源。");
  return url;
}

/** Functionality: 管理场景描述区块。
 * Logic: source 提供 latestJpeg()/latestBoxes()；speech 为共享 SpeechOutput(或 null)；busy 保证单次在途。
 * Constraints: 不自动触发、不重试、不降级；失败后按钮恢复，由用户再次点击。 */
export class SceneClient {
  /** Functionality: 绑定控件与依赖。Inputs: {doc, win, fetchImpl, source, speech}。
   * Outputs: 实例及 click 注册。Logic: 依赖可注入以便测试。Constraints: 不请求权限或网络。 */
  constructor({doc = document, win = window, fetchImpl = (...args) => fetch(...args), source, speech = null}) {
    this.doc = doc;
    this.win = win;
    this.fetchImpl = fetchImpl;
    this.source = source;
    this.speech = speech;
    this.button = doc.getElementById("scene-describe");
    this.question = doc.getElementById("scene-question");
    this.result = doc.getElementById("scene-result");
    this.status = doc.getElementById("scene-status");
    this.message = doc.getElementById("scene-message");
    this.apiBase = doc.documentElement?.dataset?.apiBase || "./";
    this.busy = false;
    this.describe = this.describe.bind(this);
    this.button.addEventListener("click", this.describe);
  }
  /** Functionality: 切换忙碌态。Inputs: busy, statusText。Outputs: 按钮 disabled 与状态文字。
   * Constraints: 状态仅用 textContent。 */
  setBusy(busy, statusText, kind = "idle") {
    this.busy = busy;
    this.button.disabled = busy;
    this.status.textContent = statusText;
    this.status.dataset.kind = kind;
  }
  /** Functionality: 显示成功文本。Inputs: text。Outputs: 结果区。Constraints: textContent，不插 HTML。 */
  show(text) {
    this.result.textContent = text;
    this.message.textContent = "";
    this.message.dataset.kind = "idle";
  }
  /** Functionality: 显示失败。Inputs: 已本地化键文本。Outputs: 状态与消息；清空旧结果以免误读。
   * Constraints: 不记录用户内容。 */
  fail(text) {
    this.result.textContent = "";
    this.message.textContent = text;
    this.message.dataset.kind = "error";
    this.setBusy(false, "失败", "error");
  }
  /** Functionality: 组装请求体。Inputs: 取帧结果 buffer。Outputs: 契约 JSON 对象。
   * Logic: question strip 后为空则省略；objects 为空则省略。
   * Constraints: 问题超过200个码点抛出本地错误（不截断）。 */
  buildRequest(buffer) {
    const request = {image_jpeg_base64: encodeBase64(buffer),
      language: sceneLanguage(this.doc.documentElement?.lang)};
    const question = String(this.question.value || "").trim();
    if ([...question].length > MAX_QUESTION_CHARS) throw new SceneError("问题最多200个字符。");
    if (question) request.question = question;
    const objects = mapObjects(this.source.latestBoxes());
    if (objects.length) request.objects = objects;
    return request;
  }
  /** Functionality: 执行一次描述请求。Inputs: 当前帧、检测框、问题、语言。
   * Outputs: 结果文本/本地化错误；可能触发朗读。
   * Logic: busy 则忽略；取帧→POST(超时 AbortController)→校验 text→显示→按需朗读。
   * Constraints: 无摄像头帧明确提示；不重试；日志仅 code/request_id/对象数。 */
  async describe() {
    if (this.busy) return;
    this.setBusy(true, "正在描述场景");
    this.message.textContent = "";
    let timer = null;
    try {
      let buffer;
      try { buffer = await this.source.latestJpeg(); }
      catch (error) {
        this.win.console.warn("[PRS scene] Frame capture failed", {error_name: error?.name});
        throw new SceneError("无法获取摄像头画面，请稍后重试。");
      }
      if (!buffer) return this.fail("请先开启摄像头再描述场景。");
      const request = this.buildRequest(buffer);
      const url = resolveSceneUrl(this.apiBase, "scene/describe/", this.win.location.href);
      const controller = new AbortController();
      timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
      let response;
      try {
        response = await this.fetchImpl(url.href, {method: "POST", cache: "no-store",
          headers: {"Content-Type": "application/json"}, body: JSON.stringify(request),
          signal: controller.signal});
      } catch (error) {
        return this.fail(error?.name === "AbortError" ? "场景描述请求超时，请重试。"
          : "场景描述请求失败，请检查网络后重试。");
      }
      let body = null;
      try { body = await response.json(); } catch { body = null; }
      if (!response.ok) {
        this.win.console.warn("[PRS scene] Request failed", {status: response.status,
          code: body?.code, request_id: body?.request_id, objects: request.objects?.length || 0});
        return this.fail(errorMessage(body?.code, body?.request_id));
      }
      if (!body || typeof body.text !== "string" || !body.text.trim())
        return this.fail("场景描述响应格式无效。");
      this.win.console.info("[PRS scene] Request done", {request_id: body.request_id,
        elapsed_ms: body.elapsed_ms, objects: request.objects?.length || 0});
      this.show(body.text);
      this.setBusy(false, "已完成", "live");
      this.speak(body.text, request.language);
    } catch (error) {
      if (error instanceof SceneError) this.fail(error.message);
    else {
      this.win.console.warn("[PRS scene] Unexpected failure", {error_name: error?.name});
      this.fail("场景描述失败。");
    }
    } finally {
      clearTimeout(timer);
      if (this.busy) this.setBusy(false, "失败", "error");
    }
  }
  /** Functionality: 语音输出已启用时朗读结果。Inputs: text, language。Outputs: 无。
   * Logic: 读取共享 SpeechOutput 的开关；未启用或无该控件则只显示。
   * Constraints: 朗读被拒绝时由 SpeechOutput 自己在语音区块反馈，这里不重试、不改参数。 */
  speak(text, language) {
    if (!this.speech?.enabled?.checked) return;
    this.speech.speak(text, {language});
  }
}

export let sceneClient = null;
if (typeof document !== "undefined" && document.getElementById("scene-describe")) {
  // 动态导入避免非页面环境(测试)实例化 CameraApp；与 index.html 的 camera.js 为同一模块实例。
  const [{cameraApp}, {speechOutput}] = await Promise.all([import("./camera.js"), import("./speech-output.js")]);
  sceneClient = new SceneClient({source: cameraApp, speech: speechOutput});
}
