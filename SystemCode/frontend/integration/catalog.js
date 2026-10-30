/** Responsibilities: Local MVP language catalog and rule-derived spoken text.
 * Implementation: Explicit zh-CN/en-US text and bounded passive display snapshots; preserve algorithm
 * labels, distances and risk fields independently of speech configuration or source lifecycle.
 * Related Modules: guidance.js, local-mvp.js, assist-mvp.js; existing i18n.js controls static page text.
 * Declaration Index: languageOf; text; riskText; resultText; AssistPresentation;
 * AssistPresentation.constructor; AssistPresentation.setText; AssistPresentation.setResults;
 * AssistPresentation.renderText; AssistPresentation.renderResults; AssistPresentation.refresh.
 * Variable Index: CATALOG contains local-only UI; DIRECTIONS/LEVELS map server enums to spoken labels;
 * texts retains one bilingual state per fixed binding; results retains at most 50 display-only rows.
 */
const CATALOG = {
  title: ["本地中台 MVP", "Local integration MVP"],
  intro: ["本机 CUDA 感知与独立引导。场景/地图/Hugging Face 外部操作已阻断。", "Local CUDA perception and independent guidance. External scene/maps/Hugging Face actions are blocked."],
  camera: ["明确选择摄像头", "Select a camera explicitly"],
  refresh: ["读取摄像头列表", "Read camera list"],
  authorizeCamera: ["授权并读取摄像头列表", "Authorize camera and read devices"],
  permissionNeeded: ["摄像头尚未授权；请点击授权按钮，然后明确选择设备。", "Camera permission is needed; click Authorize, then select a device explicitly."],
  permissionFailed: ["摄像头授权失败；未启动推理，请检查浏览器权限后手动再试。", "Camera authorization failed; inference was not started. Check browser permissions and try manually."],
  chooseCamera: ["请选择摄像头", "Choose a camera"],
  enable: ["启用本地引导（默认关闭）", "Enable local guidance (off by default)"],
  voice: ["引导语音（只允许本地声音）", "Guidance voice (local voices only)"],
  chooseVoice: ["请选择本地语音", "Choose a local voice"],
  readVoices: ["读取本地声音", "Read local voices"],
  resumeSpeech: ["手动恢复语音通道", "Resume speech manually"],
  resumeVibration: ["手动恢复震动通道", "Resume vibration manually"],
  demo: ["回放示例导航事件（demo）", "Replay demo navigation event"],
  demoText: ["示例导航：继续向前，前方路口右转。", "Demo navigation: continue ahead, then turn right at the junction."],
  demoNote: ["demo 只是引导调度输入，不是 GPS/地图路线；最多保留一条，10秒过期。", "Demo is a guidance input, not GPS/map routing; one pending instruction expires after 10 seconds."],
  outputNote: ["还需启用原语音/震动开关。语音必须明确选择匹配语言的本地声音；错误后手动恢复。浏览器接受不证明实际响/震。", "Also enable the existing speech/vibration switches. Speech requires an explicitly selected local voice in the matching language; recover errors manually. Browser acceptance does not prove audible sound or physical vibration."],
  waiting: ["等待本地检测结果", "Waiting for local results"],
  empty: ["未检出目标不代表道路安全。", "No detections does not establish road safety."],
  stale: ["结果已过期：仅显示和计时，不触发提醒。", "Stale result: displayed and measured, without alerts."],
  fresh: ["采集后1秒内，允许引导消费。", "Within 1 second of capture; eligible for guidance."],
  stopped: ["摄像头会话已停止，引导自有输出和导航已清理。", "Camera session stopped; guidance-owned outputs and pending navigation cleared."],
  disabled: ["本地引导已关闭。", "Local guidance disabled."],
  enabled: ["本地引导已启用；通道须单独启用。", "Local guidance enabled; output channels must be enabled separately."],
  paused: ["通道暂停，等待用户手动恢复", "Channel paused; waiting for manual recovery"],
  ready: ["准备就绪", "Ready"],
  noCamera: ["未发现摄像头；检查设备权限后手动读取。", "No camera found; check device permissions and read the list manually."],
  cameraError: ["无法读取摄像头列表；请检查权限后手动重试。", "Cannot read camera list; check permissions and retry manually."],
  noVoice: ["没有匹配语言的本地声音；语音不启用，不选远程替代。", "No matching local voice; speech remains unavailable without a remote replacement."],
  noDistance: ["距离未知", "Distance unknown"],
  localMode: ["本地 CUDA · 80 类", "Local CUDA · 80 classes"],
};
const DIRECTIONS = {front: ["前方", "Ahead"], front_left: ["左前方", "Ahead left"],
  front_right: ["右前方", "Ahead right"], left: ["左侧", "Left"], right: ["右侧", "Right"],
  behind: ["后方", "Behind"], unknown: ["方向未知", "Direction unknown"]};
const LEVELS = {unknown: ["风险未知", "Unknown risk"], low: ["低风险", "Low risk"],
  medium: ["中风险", "Medium risk"], high: ["高风险", "High risk"], critical: ["极高风险", "Critical risk"]};

/** Functionality: Normalize page language. Inputs: language string. Outputs: supported speech language.
 * Logic: zh prefix selects Chinese, others English; Constraints: does not replace a selected voice. */
export function languageOf(language) { return /^zh(?:-|$)/i.test(language || "") ? "zh-CN" : "en-US"; }
/** Functionality: Resolve UI catalog text. Inputs: key/language. Outputs: string.
 * Logic: fixed local dictionary; Constraints: missing keys are implementation errors, not hidden fallback. */
export function text(key, language) { return CATALOG[key][languageOf(language) === "zh-CN" ? 0 : 1]; }
/** Functionality: Describe the server's selected alert without a VLM. Inputs: box/language. Outputs: spoken text.
 * Logic: preserve risk/direction/distance, request a pause and check; Constraints: no new risk scoring or safe-route claim. */
export function riskText(box, language) {
  const zh = languageOf(language) === "zh-CN", index = zh ? 0 : 1;
  const direction = (DIRECTIONS[box.direction] || DIRECTIONS.unknown)[index];
  const level = (LEVELS[box.risk_level] || LEVELS.unknown)[index];
  const distance = Number.isFinite(box.distance_m) ? box.distance_m.toFixed(1) : null;
  return zh ? `${direction}存在${level}目标${distance ? `，距离约${distance}米` : "，距离未知"}。请停下确认周围情况。`
    : `${direction}: ${level.toLowerCase()} object${distance ? `, about ${distance} meters away` : ", distance unknown"}. Stop and check your surroundings.`;
}
/** Functionality: Render one canonical result. Inputs: box/language. Outputs: diagnostic text.
 * Logic: retain original class and track ID, metric distance and risk enum; Constraints: unknown is not safe. */
export function resultText(box, language) {
  const index = languageOf(language) === "zh-CN" ? 0 : 1;
  const distance = Number.isFinite(box.distance_m) ? `${box.distance_m.toFixed(1)} m` : text("noDistance", language);
  return `${box.label} · ID ${box.track_id ?? "?"} · ${distance} · ${(DIRECTIONS[box.direction] || DIRECTIONS.unknown)[index]} · ${(LEVELS[box.risk_level] || LEVELS.unknown)[index]}${Number.isFinite(box.ttc_sec) ? ` · TTC ${box.ttc_sec.toFixed(1)} s` : ""}`;
}

/** Functionality: Keep current integration display text in the interface language.
 * Logic: Retain bilingual strings and scalar result rows so stopped results can be translated again.
 * Constraints: No speech, risk selection, capture time, media, network, or controller ownership. */
export class AssistPresentation {
  /** Functionality: Bind a page's passive presentation state. Inputs: Document. Outputs: Empty maps.
   * Logic: Read interface language at render time. Constraints: No listener or device acquisition. */
  constructor(doc) { this.doc = doc; this.texts = new Map(); this.results = new Map(); }

  /** Functionality: Replace one current localized status. Inputs: Fixed ID and bilingual text.
   * Outputs: Current-language DOM text. Logic: Retain both variants, replacing the previous state.
   * Constraints: Strings only; a static product label cannot overwrite an owned dynamic status. */
  setText(id, chinese, english = chinese) {
    if (typeof chinese !== "string" || typeof english !== "string") throw new TypeError("Display text must be a string");
    const target = this.doc.getElementById(id);
    target.removeAttribute("data-product-copy"); target.setAttribute("data-language-original", "");
    this.texts.set(id, {chinese, english}); this.renderText(id);
  }

  /** Functionality: Retain only the last bounded result-list presentation. Inputs: Fixed ID and boxes.
   * Outputs: At most 50 localized rows. Logic: Copy only scalar fields used by resultText.
   * Constraints: Never retain image data, source timestamps, algorithm objects or speech eligibility. */
  setResults(id, boxes) {
    this.results.set(id, boxes.slice(0, 50).map(box => ({label: box.label, track_id: box.track_id,
      distance_m: box.distance_m, direction: box.direction, risk_level: box.risk_level, ttc_sec: box.ttc_sec})));
    this.doc.getElementById(id).setAttribute("data-language-original", ""); this.renderResults(id);
  }

  /** Functionality: Render one retained status without replacing another controller's update.
   * Inputs: Fixed ID, retained strings and document language. Outputs: Changed text only.
   * Logic: Drop ownership when another writer changes the text. Constraints: No lifecycle replay. */
  renderText(id) {
    const record = this.texts.get(id), target = this.doc.getElementById(id);
    if (record.rendered !== undefined && target.textContent !== record.rendered) {
      this.texts.delete(id); return;
    }
    record.rendered = languageOf(this.doc.documentElement.lang) === "zh-CN" ? record.chinese : record.english;
    if (target.textContent !== record.rendered) target.textContent = record.rendered;
  }

  /** Functionality: Translate retained result rows using the interface language. Inputs: Fixed list ID.
   * Outputs: Text-only list. Logic: Rebuild from display scalars, including after perception stopped.
   * Constraints: Speech language and queued calibration cannot influence display language. */
  renderResults(id) {
    const list = this.doc.getElementById(id); list.replaceChildren();
    for (const box of this.results.get(id)) {
      const item = this.doc.createElement("li"); item.textContent = resultText(box, this.doc.documentElement.lang); list.append(item);
    }
  }

  /** Functionality: Apply a language change to the current display only. Inputs: Document language.
   * Outputs: Repainted statuses and retained rows. Logic: Current records replace old translations.
   * Constraints: No source restart, new alert, timestamp refresh or speech action. */
  refresh() {
    for (const id of this.texts.keys()) this.renderText(id);
    for (const id of this.results.keys()) this.renderResults(id);
  }
}
