/** Responsibilities: 摄像头页“描述场景”：取最近帧与检测结果，请求 POST /scene/describe/，显示并按需朗读。
 * Implementation: 导出 describeScene/describeCurrentScene 供任意同源页面一行调用(失败抛 SceneApiError)，
 * SceneClient.describe 复用 describeScene；复用 CameraApp 已编码 JPEG(640/0.8)与最近 HF 检测框，映射为契约 objects(<=50，按
 * alert_priority/距离/置信度排序)；同源 fetch，单次在途，无重试/降级；错误按后端 code 本地化；
 * 仅当语音输出开关已启用时调用共享 SpeechOutput.speak。
 * Related Modules: index.html, camera.js(latestJpeg/latestBoxes), speech-output.js, i18n.js,
 * translations.js; backend core.scene/core.vlm 与 core.camera.ASSETS；tests/scene-client.test.mjs。
 * Declaration Index: SceneError; mapBox; mapObjects; encodeBase64; sceneLanguage; errorMessage; resolveSceneUrl;
 * SceneApiError; SceneApiError.constructor; normalizeJpeg; describeScene; describeCurrentScene;
 * SceneClient; SceneClient.constructor; SceneClient.setBusy; SceneClient.show; SceneClient.fail;
 * SceneClient.describe; SceneClient.speak。
 * Variable Index: MAX_OBJECTS=50/MAX_QUESTION_CHARS=200/MAX_JPEG_BYTES=1MiB 为契约上限；REQUEST_TIMEOUT_MS 为浏览器等待上限
 * （大于后端20s上游超时）；ERROR_MESSAGES 为后端错误码到中文界面键(由 translations.js 译英)；
 * SceneApiError.code/status/requestId 为错误字段；sceneClient 为摄像头页实例，其他页/测试为 null。实例 busy 为单次在途标记。
 * 不保存图像、问题或回答，日志只含 code/request_id/对象数。
 */
export const MAX_OBJECTS = 50;
export const MAX_QUESTION_CHARS = 200;
export const REQUEST_TIMEOUT_MS = 30000;
export const MAX_JPEG_BYTES = 1024 * 1024;

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
 * Logic: 与后端 boxes_to_objects 对齐以免一个坏框使整批 400：label 先 trim，须为 1..40 字符且不含
 * Unicode 控制类字符(\p{C})，发送 trim 后的值；score 只接受 typeof number 且有限、在 [0,1]；
 * distance_m 为有限正数且四舍五入到毫米后仍 >0 才携带；其余字段仅在值合法时携带，缺失即省略。
 * Constraints: distance_m 只用原始深度值，不用平滑值替代；非法 label/score 的框被丢弃，不做类型转换。 */
export function mapBox(box) {
  if (!box || typeof box.label !== "string") return null;
  const label = box.label.trim();
  if (!label || label.length > 40 || /\p{C}/u.test(label)) return null;
  const confidence = box.score;
  if (typeof confidence !== "number" || !Number.isFinite(confidence) || confidence < 0 || confidence > 1) return null;
  const item = {label, confidence};
  if (Number.isFinite(box.distance_m) && Math.round(box.distance_m * 1000) / 1000 > 0) item.distance_m = box.distance_m;
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

/** Functionality: describeScene/describeCurrentScene 抛出的可预期错误。
 * Logic: message 为已本地化界面键(含 request_id 后缀)；code 为后端错误码或本地码（invalid_image/
 * invalid_question/invalid_api_base/camera_unavailable/network_error/timeout/aborted/invalid_response/
 * http_error）；status 为 HTTP 状态(无响应时为 null)；requestId 无则为 null。
 * Constraints: 不携带 detail 原文、图像或问题内容。 */
export class SceneApiError extends Error {
  /** Functionality: 构造错误。Inputs: code, message, {status, requestId}。Outputs: 实例。
   * Logic: 字段只读语义，仅赋值。Constraints: 无副作用。 */
  constructor(code, message, {status = null, requestId = null} = {}) {
    super(message);
    this.name = "SceneApiError";
    this.code = code;
    this.status = status;
    this.requestId = requestId;
  }
}

/** Functionality: 把 jpeg 输入规整为 Uint8Array 并校验。Inputs: ArrayBuffer/视图/Blob/其他。
 * Outputs: Uint8Array。Logic: 仅接受非空、<=1 MiB、以 FF D8 开头的字节。
 * Constraints: 不合格抛 SceneApiError(invalid_image)；不读取 Blob 之外的资源。 */
async function normalizeJpeg(jpeg) {
  const bad = () => new SceneApiError("invalid_image", errorMessage("invalid_image"));
  let bytes = null;
  if (jpeg instanceof Uint8Array) bytes = jpeg;
  else if (ArrayBuffer.isView(jpeg)) bytes = new Uint8Array(jpeg.buffer, jpeg.byteOffset, jpeg.byteLength);
  else if (jpeg instanceof ArrayBuffer) bytes = new Uint8Array(jpeg);
  else if (typeof Blob !== "undefined" && jpeg instanceof Blob) bytes = new Uint8Array(await jpeg.arrayBuffer());
  if (!bytes || !bytes.length || bytes.length > MAX_JPEG_BYTES || bytes[0] !== 0xff || bytes[1] !== 0xd8) throw bad();
  return bytes;
}

/** Functionality: 对任意同源页面发起一次场景描述。Inputs: jpeg(ArrayBuffer|Uint8Array|Blob), boxes(HF
 * metadata.boxes 原样), question(可选, trim 后空视为不传), options{language, apiBase, fetchImpl,
 * timeoutMs=30000, signal, pageHref, logger}。pageHref/logger 供测试与 SceneClient 注入，缺省取
 * globalThis.location.href / console。
 * Outputs: 成功响应对象 {text, model, language, elapsed_ms, usage, request_id}；失败抛 SceneApiError。
 * Logic: 非外部 signal 引起的 AbortError 一律按 timeout 处理（与原页面行为一致）；校验图像与问题(不发请求)→mapObjects→同源 POST(AbortController 超时)→按 HTTP/body 分类；
 * language 缺省取 document.documentElement.lang，apiBase 缺省取 data-api-base 或 "./"。
 * Constraints: 不重试、不降级、不截断问题；调用方自行保证单次在途；日志仅 code/request_id/对象数。 */
export async function describeScene(jpeg, boxes = [], question = null, options = {}) {
  const {timeoutMs = REQUEST_TIMEOUT_MS, signal = null} = options;
  const doc = typeof document !== "undefined" ? document : null;
  const logger = options.logger || console;
  const fetchImpl = options.fetchImpl || ((...args) => fetch(...args));
  const bytes = await normalizeJpeg(jpeg);
  const text = question == null ? "" : String(question).trim();
  if ([...text].length > MAX_QUESTION_CHARS)
    throw new SceneApiError("invalid_question", "问题最多200个字符。");
  const request = {image_jpeg_base64: encodeBase64(bytes),
    language: options.language || sceneLanguage(doc?.documentElement?.lang)};
  if (text) request.question = text;
  const objects = mapObjects(boxes);
  if (objects.length) request.objects = objects;
  let url;
  try {
    url = resolveSceneUrl(options.apiBase ?? (doc?.documentElement?.dataset?.apiBase || "./"),
      "scene/describe/", options.pageHref || globalThis.location?.href);
  } catch (error) {
    if (error instanceof SceneError) throw new SceneApiError("invalid_api_base", error.message);
    throw new SceneApiError("invalid_api_base", "场景描述失败。");
  }
  const controller = new AbortController();
  let timedOut = false;
  const onAbort = () => controller.abort();
  if (signal?.aborted) controller.abort();
  else signal?.addEventListener?.("abort", onAbort, {once: true});
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
  let response, body = null;
  try {
    response = await fetchImpl(url.href, {method: "POST", cache: "no-store",
      headers: {"Content-Type": "application/json"}, body: JSON.stringify(request), signal: controller.signal});
    try { body = await response.json(); } catch (error) {
      if (error?.name === "AbortError") throw error;
      body = null;
    }
  } catch (error) {
    const aborted = error?.name === "AbortError";
    const code = aborted ? (signal?.aborted && !timedOut ? "aborted" : "timeout") : "network_error";
    logger.warn("[PRS scene] Request not completed", {code});
    throw new SceneApiError(code, aborted ? (code === "timeout" ? "场景描述请求超时，请重试。" : "场景描述请求已取消。")
      : "场景描述请求失败，请检查网络后重试。");
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener?.("abort", onAbort);
  }
  const requestId = typeof body?.request_id === "string" ? body.request_id : null;
  if (!response.ok) {
    logger.warn("[PRS scene] Request failed", {status: response.status, code: body?.code,
      request_id: requestId, objects: objects.length});
    const code = typeof body?.code === "string" && body.code ? body.code : "http_error";
    throw new SceneApiError(code, errorMessage(body?.code, body?.request_id), {status: response.status, requestId});
  }
  if (!body || typeof body.text !== "string" || !body.text.trim())
    throw new SceneApiError("invalid_response", "场景描述响应格式无效。", {status: response.status, requestId});
  logger.info("[PRS scene] Request done", {request_id: requestId, elapsed_ms: body.elapsed_ms, objects: objects.length});
  return body;
}

/** Functionality: 用摄像头页最近帧与检测框描述当前场景。Inputs: question, options(同 describeScene，另可
 * 注入 source={latestJpeg, latestBoxes} 供测试)。Outputs: 同 describeScene。
 * Logic: 在函数内动态 import camera.js（避免非摄像头页加载副作用）；取帧失败或无帧抛 camera_unavailable。
 * Constraints: 摄像头未开不发请求；不重试；本页需已加载 camera.js 所需 DOM，否则按 camera_unavailable 处理。 */
export async function describeCurrentScene(question = null, options = {}) {
  const unavailable = () => new SceneApiError("camera_unavailable", "请先开启摄像头再描述场景。");
  let source = options.source;
  if (!source) {
    try { source = (await import("./camera.js")).cameraApp; } catch { throw unavailable(); }
  }
  let jpeg;
  try { jpeg = await source?.latestJpeg?.(); } catch { throw unavailable(); }
  if (!jpeg) throw unavailable();
  return describeScene(jpeg, source.latestBoxes(), question, options);
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
  /** Functionality: 执行一次描述请求。Inputs: 当前帧、检测框、问题、语言（均来自页面与注入的取帧源）。
   * Outputs: 结果文本/本地化错误；可能触发朗读。
   * Logic: busy 则忽略；取帧→调用 describeScene→显示→按需朗读；SceneApiError/SceneError 的 message
   * 已是本地化界面键，直接展示。
   * Constraints: 无摄像头帧明确提示；不重试；日志仅 code/request_id/对象数。 */
  async describe() {
    if (this.busy) return;
    this.setBusy(true, "正在描述场景");
    this.message.textContent = "";
    try {
      let buffer;
      try { buffer = await this.source.latestJpeg(); }
      catch (error) {
        this.win.console.warn("[PRS scene] Frame capture failed", {error_name: error?.name});
        throw new SceneError("无法获取摄像头画面，请稍后重试。");
      }
      if (!buffer) return this.fail("请先开启摄像头再描述场景。");
      const language = sceneLanguage(this.doc.documentElement?.lang);
      const result = await describeScene(buffer, this.source.latestBoxes(), this.question.value, {
        language, apiBase: this.apiBase, fetchImpl: this.fetchImpl,
        pageHref: this.win.location.href, logger: this.win.console});
      this.show(result.text);
      this.setBusy(false, "已完成", "live");
      this.speak(result.text, language);
    } catch (error) {
      if (error instanceof SceneError || error instanceof SceneApiError) this.fail(error.message);
      else {
        this.win.console.warn("[PRS scene] Unexpected failure", {error_name: error?.name});
        this.fail("场景描述失败。");
      }
    } finally {
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
