/** Responsibilities: Describe a camera-page scene by reusing the latest frame/detections, calling POST /scene/describe/, displaying, and optionally speaking the result.
 * Implementation: Export describeScene/describeCurrentScene for one-call use from any same-origin page (failures throw SceneApiError).
 * SceneClient.describe reuses describeScene; CameraApp supplies encoded JPEG (640/0.8) and recent HF boxes, mapped to contract objects (<=50, sorted by
 * alert_priority/distance/confidence). Use same-origin fetch with one in-flight request and no retry/fallback; localize errors by backend code.
 * Call shared SpeechOutput.speak only when speech output is enabled.
 * Related Modules: index.html, camera.js(latestJpeg/latestBoxes), speech-output.js, i18n.js,
 * translations.js; backend core.scene/core.vlm and core.camera.ASSETS; tests/scene-client.test.mjs.
 * Declaration Index: SceneError; mapBox; mapObjects; encodeBase64; sceneLanguage; errorMessage; resolveSceneUrl;
 * SceneApiError; SceneApiError.constructor; normalizeJpeg; describeScene; describeCurrentScene;
 * SceneClient; SceneClient.constructor; SceneClient.setBusy; SceneClient.show; SceneClient.fail;
 * SceneClient.describe; SceneClient.speak.
 * Variable Index: MAX_OBJECTS=50/MAX_QUESTION_CHARS=200/MAX_JPEG_BYTES=1MiB — contract limits; REQUEST_TIMEOUT_MS — browser wait limit
 * (longer than the backend's 20-second upstream timeout); ERROR_MESSAGES — backend error codes mapped to translation keys in translations.js;
 * SceneApiError.code/status/requestId — error fields; sceneClient — camera-page instance, null on other pages/tests; instance busy — single in-flight marker.
 * Do not store images/questions/answers; logs contain only code/request_id/object count.
 */
export const MAX_OBJECTS = 50;
export const MAX_QUESTION_CHARS = 200;
export const REQUEST_TIMEOUT_MS = 30000;
export const MAX_JPEG_BYTES = 1024 * 1024;

/** Functionality: Carry an expected error with localized UI text. Logic: message must be a key from translations.js.
 * Constraints: Only this error's message is displayed directly; other exceptions use generic failure text. */
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

/** Functionality: Map one detection box to the API object contract. Inputs: HF box. Outputs: object or null.
 * Logic: Match backend boxes_to_objects to prevent one bad box from rejecting the whole batch: trim label, require 1..40 characters without
 * Unicode control-category characters (\p{C}), and send the trimmed value; accept score only as a finite number in [0,1].
 * Include distance_m only when finite/positive and still >0 after rounding to millimeters; include other fields only when valid and omit missing values.
 * Constraints: Use raw depth for distance_m, not a smoothed substitute; discard invalid label/score without type coercion. */
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

/** Functionality: Select and map the most important <=50 objects. Inputs: boxes array. Outputs: objects array.
 * Logic: Sort by alert_priority descending, distance ascending (missing values last), then confidence descending; stable-sort and truncate.
 * Constraints: Do not mutate input; treat non-arrays as empty. */
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

/** Functionality: Convert ArrayBuffer to standard base64. Inputs: ArrayBuffer/Uint8Array. Outputs: String.
 * Logic: Convert in chunks with fromCharCode before btoa to avoid stack overflow from spreading large arrays. Constraints: Keep conversion in memory. */
export function encodeBase64(buffer) {
  const bytes = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer);
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 0x8000)
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  return btoa(binary);
}

/** Functionality: Map page language to the API language. Inputs: document.documentElement.lang.
 * Outputs: "zh-CN" | "en-US". Logic: i18n.js writes only zh-CN or en. Constraints: Treat all other values as English. */
export function sceneLanguage(lang) { return /^zh(?:-|$)/i.test(lang || "") ? "zh-CN" : "en-US"; }

/** Functionality: Map backend error codes to UI keys. Inputs: code and requestId. Outputs: Text.
 * Logic: Use fixed keys for known codes and a generic key for unknown ones; append request_id in full-width parentheses for i18n preservation.
 * Constraints: Do not display raw detail, which may contain upstream information. */
export function errorMessage(code, requestId) {
  const base = Object.hasOwn(ERROR_MESSAGES, code) ? ERROR_MESSAGES[code]
    : typeof code === "string" && /^[a-z_]{1,48}$/.test(code) ? `场景描述失败：${code}` : "场景描述失败。";
  return typeof requestId === "string" && /^[\w-]{1,64}$/.test(requestId) ? `${base}（${requestId}）` : base;
}

/** Functionality: Resolve a same-origin API URL. Inputs: apiBase, path, pageHref. Outputs: URL.
 * Logic: Follow CameraApp.apiUrl's data-api-base convention; Constraints: Throw for cross-origin URLs without fallback. */
export function resolveSceneUrl(apiBase, path, pageHref) {
  const page = new URL(pageHref), base = new URL(apiBase, page), url = new URL(path, base);
  if (base.origin !== page.origin || url.origin !== page.origin) throw new SceneError("场景描述接口必须与页面同源。");
  return url;
}

/** Functionality: Expected error thrown by describeScene/describeCurrentScene.
 * Logic: message is a localized UI key (optionally with request_id suffix); code is a backend or local code
 * (invalid_image/invalid_question/invalid_api_base/camera_unavailable/network_error/timeout/aborted/invalid_response/http_error);
 * status is HTTP status (null without a response); requestId is null when absent.
 * Constraints: Do not include raw detail, image, or question content. */
export class SceneApiError extends Error {
  /** Functionality: Construct an error. Inputs: code, message, {status, requestId}. Outputs: Instance.
   * Logic: Assign fields only; Constraints: No side effects. */
  constructor(code, message, {status = null, requestId = null} = {}) {
    super(message);
    this.name = "SceneApiError";
    this.code = code;
    this.status = status;
    this.requestId = requestId;
  }
}

/** Functionality: Normalize and validate JPEG input as Uint8Array. Inputs: ArrayBuffer/view/Blob/other.
 * Outputs: Uint8Array. Logic: Accept only non-empty bytes <=1 MiB that start with FF D8.
 * Constraints: Invalid input throws SceneApiError(invalid_image); do not read resources beyond the supplied Blob. */
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

/** Functionality: Submit one scene-description request from any same-origin page. Inputs: jpeg(ArrayBuffer|Uint8Array|Blob), boxes (HF
 * metadata.boxes unchanged), optional question (trimmed; blank means omitted), and options {language, apiBase, fetchImpl,
 * timeoutMs=30000, signal, pageHref, logger}. pageHref/logger support tests and SceneClient injection; defaults are
 * globalThis.location.href / console.
 * Outputs: Success object {text, model, language, elapsed_ms, usage, request_id}; failures throw SceneApiError.
 * Logic: Treat AbortError as timeout unless caused by the external signal (matching the original page); validate image/question without a request, mapObjects, then same-origin POST with AbortController timeout and classify HTTP/body response.
 * language defaults to document.documentElement.lang; apiBase defaults to data-api-base or "./".
 * Constraints: No retry, fallback, or question truncation; caller enforces one in-flight request; logs contain only code/request_id/object count. */
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

/** Functionality: Describe the current scene using the camera page's latest frame and boxes. Inputs: question and options (same as describeScene, plus optional
 * injected source={latestJpeg, latestBoxes} for tests). Outputs: Same as describeScene.
 * Logic: Dynamically import camera.js inside the function to avoid side effects on non-camera pages; capture failure/no frame throws camera_unavailable.
 * Constraints: Do not send when the camera is off; do not retry; the page must have loaded the DOM required by camera.js or camera_unavailable is raised. */
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

/** Functionality: Manage the scene-description panel.
 * Logic: source supplies latestJpeg()/latestBoxes(); speech is shared SpeechOutput or null; busy enforces one in-flight request.
 * Constraints: No automatic trigger, retry, or fallback; after failure the button is restored for a manual attempt. */
export class SceneClient {
  /** Functionality: Bind controls and dependencies. Inputs: {doc, win, fetchImpl, source, speech}.
   * Outputs: Instance and click handler. Logic: Dependencies are injectable for tests. Constraints: No permission or network request. */
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
  /** Functionality: Change busy state. Inputs: busy, statusText. Outputs: Button disabled state and status text.
   * Constraints: Render status with textContent only. */
  setBusy(busy, statusText, kind = "idle") {
    this.busy = busy;
    this.button.disabled = busy;
    this.status.textContent = statusText;
    this.status.dataset.kind = kind;
  }
  /** Functionality: Display successful text. Inputs: text. Outputs: Result area. Constraints: Use textContent, never HTML insertion. */
  show(text) {
    this.result.textContent = text;
    this.message.textContent = "";
    this.message.dataset.kind = "idle";
  }
  /** Functionality: Display failure. Inputs: Localized UI-key text. Outputs: Status and message; clear stale results to prevent misinterpretation.
   * Constraints: Do not log user content. */
  fail(text) {
    this.result.textContent = "";
    this.message.textContent = text;
    this.message.dataset.kind = "error";
    this.setBusy(false, "失败", "error");
  }
  /** Functionality: Run one description request. Inputs: Current frame, boxes, question, and language from the page/injected frame source.
   * Outputs: Result text/localized error; may trigger speech.
   * Logic: Ignore while busy; capture, call describeScene, display, and optionally speak. SceneApiError/SceneError messages
   * are already localized UI keys and can be displayed directly.
   * Constraints: Explain missing camera frame; do not retry; logs contain only code/request_id/object count. */
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
  /** Functionality: Speak the result when speech output is enabled. Inputs: text and language. Outputs: None.
   * Logic: Read the shared SpeechOutput toggle; if disabled or absent, display text only.
   * Constraints: SpeechOutput reports rejected speech in its own panel; do not retry or change parameters here. */
  speak(text, language) {
    if (!this.speech?.enabled?.checked) return;
    this.speech.speak(text, {language});
  }
}

export let sceneClient = null;
if (typeof document !== "undefined" && document.getElementById("scene-describe")) {
  // Dynamic import avoids instantiating CameraApp outside the page (for tests); index.html and this module share the same camera.js instance.
  const [{cameraApp}, {speechOutput}] = await Promise.all([import("./camera.js"), import("./speech-output.js")]);
  sceneClient = new SceneClient({source: cameraApp, speech: speechOutput});
}
