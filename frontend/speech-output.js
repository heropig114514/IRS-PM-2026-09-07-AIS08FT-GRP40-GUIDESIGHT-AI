/** Responsibilities: Provide shared browser speech output, test UI, lifecycle events, and explicit failure feedback.
 * Implementation: Off by default; manual UI and MCP share speak/stop while preserving foreground and user-activation requirements.
 * Submit one utterance, record engine start/end/error states, cancel on stop/background, and never retry or replace voices.
 * Snapshot request events before broadcasting and give each subscriber a copy to isolate synchronous reentry or mutation.
 * Related Modules: index.html, camera.css, translations.js, ai-control.js, integration/guidance.js; core.ai_mcp/core.camera。
 * Declaration Index: SpeechOutput; SpeechOutput.constructor; SpeechOutput.feedback;
 * SpeechOutput.render; SpeechOutput.refreshVoices; SpeechOutput.describe; SpeechOutput.voiceList;
 * SpeechOutput.result; SpeechOutput.speak; SpeechOutput.stop; SpeechOutput.handlePlay;
 * SpeechOutput.handleEnable; SpeechOutput.handleSample; SpeechOutput.handleStart;
 * SpeechOutput.handleEnd; SpeechOutput.handleError; SpeechOutput.handleVisibility;
 * SpeechOutput.handlePageHide; SpeechOutput.subscribe; SpeechOutput.notify; SpeechOutput.stopOwned。
 * Variable Index: MAX_TEXT_LENGTH — 1,000 Unicode code points; LANGUAGES — explicit Chinese/English languages;
 * SAMPLES — test text that is never played automatically; PHASE_LABELS — UI labels for engine states; ERROR_CODES — standard error allowlist;
 * speechOutput — shared camera-page instance, null on other pages. engine/doc/win — browser dependencies;
 * current retains the utterance and isolates stale events; canceling isolates events during cancellation; sequence identifies requests;
 * phase/lastRequest/lastError/nativeVoices/voiceReadError — bounded state; listeners observe lifecycle without utterance text;
 * notify's local event fixes phase/request_id/reason/at for the current event and gives each snapshotted listener a copy;
 * localOnly is explicitly set only by the standalone integration page, which rejects default/remote voices without changing the original page.
 */
export const MAX_TEXT_LENGTH = 1000;
const LANGUAGES = ["zh-CN", "en-US"];
const SAMPLES = {"zh-CN": "这是 PRS 语音输出测试。", "en-US": "This is a PRS speech output test."};
const PHASE_LABELS = {idle: "等待朗读", queued: "等待引擎开始", speaking: "引擎正在朗读",
  completed: "引擎报告完成", stopped: "已请求停止", error: "语音输出失败"};
const ERROR_CODES = ["canceled", "interrupted", "audio-busy", "audio-hardware", "network",
  "synthesis-unavailable", "synthesis-failed", "language-unavailable", "voice-unavailable",
  "text-too-long", "invalid-argument", "not-allowed"];

/** Functionality: Manage one browser speech request and separate manual/MCP output interfaces.
 * Logic: Use actual SpeechSynthesisUtterance events; a void return from speak does not mean playback completed.
 * Isolate late events from cancelled requests by object identity. Constraints: No recording, camera access, or text storage;
 * no automatic queueing, retry, voice replacement, or audio-file generation. Browser speech service determines whether a remote voice uses the network.
 */
export class SpeechOutput {
  /** Functionality: Bind speech UI and browser lifecycle, then read the initial voice list.
   * Inputs: doc/win, defaulting to current document/window; page must contain speech-test controls.
   * Outputs: Instance, status message, and event handlers; no automatic speech or sensor permission request.
   * Logic: Begin with no current request and output disabled; voiceschanged refreshes the async voice list while preserving explicit selection.
   * Constraints: Support status proves only API presence; voices may load later, and errors are recorded without automatic retry.
   */
  constructor(doc = document, win = window) {
    this.doc = doc;
    this.win = win;
    this.engine = win.speechSynthesis;
    this.supported = typeof win.SpeechSynthesisUtterance === "function" &&
      ["speak", "cancel", "getVoices", "addEventListener"].every(name => typeof this.engine?.[name] === "function");
    this.enabled = doc.getElementById("speech-enabled");
    this.text = doc.getElementById("speech-text");
    this.language = doc.getElementById("speech-language");
    this.voice = doc.getElementById("speech-voice");
    this.rate = doc.getElementById("speech-rate");
    this.volume = doc.getElementById("speech-volume");
    this.playButton = doc.getElementById("speech-play");
    this.stopButton = doc.getElementById("speech-stop");
    this.samples = [...doc.querySelectorAll("[data-speech-sample]")];
    this.status = doc.getElementById("speech-status");
    this.message = doc.getElementById("speech-message");
    this.nativeVoices = [];
    this.voiceReadError = false;
    this.current = null;
    this.canceling = false;
    this.phase = "idle";
    this.sequence = 0;
    this.lastRequest = null;
    this.lastError = null;
    this.listeners = new Set();
    this.localOnly = doc.documentElement?.dataset?.localMvp === "true";
    this.enabled.checked = false;
    for (const name of ["render", "refreshVoices", "handlePlay", "stop", "handleEnable",
      "handleSample", "handleStart", "handleEnd", "handleError", "handleVisibility", "handlePageHide"])
      this[name] = this[name].bind(this);
    this.enabled.addEventListener("change", this.handleEnable);
    this.playButton.addEventListener("click", this.handlePlay);
    this.stopButton.addEventListener("click", this.stop);
    for (const button of this.samples) button.addEventListener("click", this.handleSample);
    this.rate.addEventListener("input", this.render);
    this.volume.addEventListener("input", this.render);
    doc.addEventListener("visibilitychange", this.handleVisibility);
    win.addEventListener("pagehide", this.handlePageHide);
    this.feedback(this.supported ? "启用语音输出后，输入文本并点击朗读。" : "当前浏览器不提供语音输出接口。",
      this.supported ? "idle" : "error");
    if (this.supported) {
      this.engine.addEventListener("voiceschanged", this.refreshVoices);
      this.refreshVoices();
    }
    this.render();
    win.console.info("[PRS speech] Initialized", {supported: this.supported});
  }
  /** Functionality: Subscribe to non-text output events. Inputs: listener. Outputs: unsubscribe callback.
   * Logic: local Set; events carry request identity and actual browser engine phase.
   * Constraints: no polling, voice replacement, permission grant or hardware-success inference. */
  subscribe(listener) { this.listeners.add(listener); return () => this.listeners.delete(listener); }
  /** Functionality: Publish one request-bound lifecycle snapshot to independent observers.
   * Inputs: phase, optional reason and current request identity. Outputs: Independent event copies.
   * Logic: Capture identity/time and listener set before callbacks; nested new requests cannot
   * relabel the original event, and one observer's mutations cannot affect later observers.
   * Constraints: Body omitted; observer exceptions cannot alter native output semantics. */
  notify(phase, reason = null) {
    const event = Object.freeze({channel: "speech", phase, request_id: this.lastRequest?.id ?? null,
      reason, at: performance.now()});
    for (const listener of [...this.listeners]) {
      try { listener({...event}); }
      catch (error) { this.win.console.error("Speech observer failed", {error_type: error.name}); }
    }
  }
  /** Functionality: Cancel only the caller's current utterance. Inputs: requestId. Outputs: acknowledgement.
   * Logic: compare current request identity before delegating to the existing shared stop.
   * Constraints: never cancel a later manual/scene utterance or retry a failed cancellation. */
  stopOwned(requestId) {
    if (!this.current || this.lastRequest?.id !== requestId) return this.result("stop", false, "not_owned");
    return this.stop();
  }

  /** Functionality: Publish a translatable speech status message.
   * Inputs: Fixed UI source text and kind (idle/live/error). Outputs: textContent and visual state.
   * Logic: Existing i18n observer translates display; never show utterance text or external exception messages.
   * Constraints: Engine events and request acceptance do not prove physical speaker output.
   */
  feedback(text, kind = "idle") {
    this.message.textContent = text;
    this.message.dataset.kind = kind;
  }

  /** Functionality: Refresh controls from API support, user toggle, and current request.
   * Inputs: supported/enabled/current/phase, range values, and lastRequest.
   * Outputs: disabled states, status, value labels, and a summary of actual request metadata.
   * Logic: Allow one request at a time; keep stop available when supported and lock settings during a request.
   * Constraints: Do not translate/rewrite text input or infer program state from translated status text.
   */
  render() {
    const active = this.current !== null;
    this.enabled.disabled = !this.supported;
    this.text.disabled = !this.supported;
    for (const control of [this.language, this.voice, this.rate, this.volume, ...this.samples])
      control.disabled = !this.supported || active;
    this.playButton.disabled = !this.supported || !this.enabled.checked || active;
    this.stopButton.disabled = !this.supported;
    this.status.textContent = this.supported ? PHASE_LABELS[this.phase] : "接口不可用";
    this.status.dataset.kind = !this.supported || this.phase === "error" ? "error" :
      this.phase === "speaking" || this.phase === "completed" ? "live" : "idle";
    this.doc.getElementById("speech-rate-value").textContent = Number(this.rate.value).toFixed(1) + "×";
    this.doc.getElementById("speech-volume-value").textContent = Math.round(Number(this.volume.value) * 100) + "%";
    this.doc.getElementById("speech-request").textContent = this.lastRequest ?
      `请求 ${this.lastRequest.id} · ${this.lastRequest.text_length} 个字符 · ${this.lastRequest.language} · 语速 ${this.lastRequest.rate}× · 音量 ${Math.round(this.lastRequest.volume * 100)}%` :
      "尚无语音请求。";
  }

  /** Functionality: Read available voices and preserve explicit selection.
   * Inputs: engine.getVoices, voice.value, and doc. Outputs: Native list, options, and read-error state.
   * Logic: Register async list events and record a count only when values change. Preserve a disabled placeholder when the selected ID disappears;
   * never select another voice automatically. Keep native name/language/local/remote identity for exact MCP selection.
   * Constraints: Catch only native list-call errors; browser default is an explicit option, not a failure fallback.
   */
  refreshVoices() {
    if (!this.supported) return;
    let voices;
    try { voices = [...this.engine.getVoices()]; }
    catch (error) {
      this.voiceReadError = true;
      this.win.console.error("[PRS speech] Voice list failed", {error_name: error?.name});
      this.feedback("无法读取语音列表，请检查浏览器后手动再试。", "error");
      return;
    }
    const changed = JSON.stringify(voices.map(voice => [voice.voiceURI, voice.lang, voice.localService])) !==
      JSON.stringify(this.nativeVoices.map(voice => [voice.voiceURI, voice.lang, voice.localService]));
    this.nativeVoices = voices;
    this.voiceReadError = false;
    const selected = this.voice.value || "";
    const primary = this.doc.createElement("option");
    primary.value = "";
    primary.textContent = "浏览器默认语音";
    this.voice.replaceChildren(primary);
    for (const voice of voices) {
      const option = this.doc.createElement("option");
      option.value = voice.voiceURI;
      option.textContent = `${voice.name} (${voice.lang}) [${voice.localService ? "local" : "remote"}]`;
      option.dataset.languageOriginal = "";
      this.voice.append(option);
    }
    if (selected && !voices.some(voice => voice.voiceURI === selected)) {
      const missing = this.doc.createElement("option");
      missing.value = selected;
      missing.textContent = "已选语音当前不可用";
      missing.disabled = true;
      this.voice.append(missing);
    }
    this.voice.value = selected;
    this.render();
    if (changed) this.win.console.info("[PRS speech] Voices changed", {count: voices.length});
  }

  /** Functionality: Query state and non-text metadata for the latest request.
   * Inputs: Controls, current utterance, native state, and bounded request/error records. Outputs: JSON-compatible state.
   * Logic: Distinguish queued, started, completed, and cancelled; never read or return current text input.
   * Constraints: audio_output_verified is always false; speaking/pending/paused do not prove the device is audible.
   */
  describe() {
    return {available: true, supported: this.supported, enabled: this.enabled.checked,
      visible: this.doc.visibilityState === "visible",
      user_activated: this.win.navigator.userActivation?.hasBeenActive ?? null,
      state: this.phase, active_request: this.current !== null,
      engine_speaking: Boolean(this.engine?.speaking), engine_pending: Boolean(this.engine?.pending),
      engine_paused: Boolean(this.engine?.paused),
      voice_count: this.nativeVoices.length, voice_query_failed: this.voiceReadError,
      last_request: this.lastRequest ? {...this.lastRequest} : null, last_error: this.lastError,
      audio_output_verified: false};
  }

  /** Functionality: Return structured identities for voices available on this device.
   * Inputs: Current native speech service. Outputs: Status, full voice list, or explicit voice_query_failed.
   * Logic: Query once and expose URI/name/language/locality/default for explicit selection.
   * Constraints: Do not speak or enable output; on failure, do not report stale cache as current.
   */
  voiceList() {
    this.refreshVoices();
    return {...this.describe(), operation: "voices", accepted: this.supported && !this.voiceReadError,
      reason: !this.supported ? "unsupported" : this.voiceReadError ? "voice_query_failed" : null,
      voices: this.voiceReadError ? [] : this.nativeVoices.map(voice => ({id: voice.voiceURI,
        name: voice.name, language: voice.lang, local_service: voice.localService, default: voice.default}))};
  }

  /** Functionality: Build an operation response that distinguishes submission/cancellation from audible output.
   * Inputs: operation, accepted, and reason. Outputs: Current state and machine-readable result.
   * Logic: Return a bounded state snapshot; rejection records operation/reason only, never text.
   * Constraints: No retry, implicit enabling, or voice switch; the result does not guarantee start/end events.
   */
  result(operation, accepted, reason = null) {
    if (!accepted) this.win.console.info("[PRS speech] Request refused", {operation, reason});
    return {...this.describe(), operation, accepted, reason};
  }

  /** Functionality: Submit one controlled text-to-speech request.
   * Inputs: text; options.language (zh-CN/en-US), voice_id (null/empty selects the browser default),
   * rate (0.5..2, default 1), volume (0..1, default 1), plus user toggle, foreground, real activation, and engine state.
   * Outputs: Submission status and structured reason; accepted requests begin as queued and native events update the phase.
   * Logic: Validate Unicode length and options, then check prerequisites and busy state; an explicit voice must be unique and match the primary language.
   * Retain the utterance strongly, bind start/end/error handlers, and call speak exactly once.
   * Constraints: Do not queue or interrupt older requests automatically; do not retry native exceptions/events or rewrite/store text.
   * The main page lets the browser select its default voice for the explicit language; local MVP accepts only explicitly localService voices and never substitutes a missing voice.
   */
  speak(text, {language = "zh-CN", voice_id = null, rate = 1, volume = 1} = {}) {
    const length = typeof text === "string" ? [...text].length : 0;
    if (typeof text !== "string" || !text.trim() || length > MAX_TEXT_LENGTH) {
      this.feedback("请输入1至1000个字符的朗读文本。", "error");
      return this.result("speak", false, "invalid_text");
    }
    if (!LANGUAGES.includes(language) || !Number.isFinite(rate) || rate < 0.5 || rate > 2 ||
        !Number.isFinite(volume) || volume < 0 || volume > 1 ||
        voice_id !== null && (typeof voice_id !== "string" || voice_id.length > 1024)) {
      this.feedback("语音参数无效，请检查语言、语速、音量和语音选择。", "error");
      return this.result("speak", false, "invalid_parameters");
    }
    if (!this.supported || !this.enabled.checked) {
      this.feedback("请确认浏览器支持并启用语音输出。", "error");
      return this.result("speak", false, this.supported ? "disabled" : "unsupported");
    }
    if (this.doc.visibilityState !== "visible") {
      this.feedback("请在前台页面手动启用后再朗读。", "error");
      return this.result("speak", false, "page_hidden");
    }
    if (this.win.navigator.userActivation && !this.win.navigator.userActivation.hasBeenActive) {
      this.feedback("请先在页面真实点击并启用语音输出。", "error");
      return this.result("speak", false, "activation_required");
    }
    if (this.current || this.engine.speaking || this.engine.pending || this.engine.paused) {
      this.feedback("已有语音请求，请先停止或等待完成。", "error");
      return this.result("speak", false, "busy");
    }
    let voice = null;
    if (voice_id) {
      this.refreshVoices();
      const matches = this.nativeVoices.filter(candidate => candidate.voiceURI === voice_id);
      if (this.voiceReadError || matches.length !== 1) {
        this.feedback("指定语音不可用或不唯一，请重新选择。", "error");
        return this.result("speak", false, this.voiceReadError ? "voice_query_failed" :
          matches.length > 1 ? "voice_ambiguous" : "voice_unavailable");
      }
      voice = matches[0];
      if (voice.lang.split("-")[0].toLowerCase() !== language.split("-")[0].toLowerCase()) {
        this.feedback("所选语音与朗读语言不匹配，请检查设置。", "error");
        return this.result("speak", false, "voice_language_mismatch");
      }
    }
    if (this.localOnly && (!voice || voice.localService !== true)) {
      this.feedback("指定语音不可用或不唯一，请重新选择。", "error");
      return this.result("speak", false, "local_voice_required");
    }
    const requestId = ++this.sequence;
    this.lastRequest = {id: requestId, text_length: length, language, voice_id: voice_id || null,
      rate, volume, local_service: voice ? voice.localService : null};
    try {
      const utterance = new this.win.SpeechSynthesisUtterance(text);
      utterance.lang = language;
      utterance.voice = voice;
      utterance.rate = rate;
      utterance.volume = volume;
      utterance.addEventListener("start", this.handleStart);
      utterance.addEventListener("end", this.handleEnd);
      utterance.addEventListener("error", this.handleError);
      this.current = utterance;
      this.phase = "queued";
      this.lastError = null;
      this.feedback("请求已提交，等待语音引擎开始；请确认设备实际声音。");
      this.render();
      this.notify("submitted");
      this.engine.speak(utterance);
      this.win.console.info("[PRS speech] Request submitted", {request_id: requestId,
        text_length: length, language, rate, volume, local_service: this.lastRequest.local_service});
      return this.result("speak", true);
    } catch (error) {
      this.current = null;
      this.phase = "error";
      this.lastError = "native_error";
      this.win.console.error("[PRS speech] Native speak failed", {request_id: requestId,
        text_length: length, language, rate, volume, error_name: error?.name});
      this.feedback("语音调用异常，请检查浏览器后手动再试。", "error");
      this.render();
      this.notify("error", "native_error");
      return this.result("speak", false, "native_error");
    }
  }

  /** Functionality: Request cancellation of current or queued output from this page's speech engine.
   * Inputs: Current engine and request; does not require the toggle, foreground, or an active module-owned request.
   * Outputs: Whether the cancellation call returned; on success clear the current reference and mark stopped, while errors preserve the request record.
   * Logic: Ignore synchronous cancellation events while canceling and isolate later stale events by object identity.
   * Constraints: A void return from cancel does not prove playback stopped; retain observable state after failure for manual follow-up,
   * do not retry automatically, and manage the page's shared speech engine through this common interface.
   */
  stop() {
    if (!this.supported) return this.result("stop", false, "unsupported");
    this.canceling = true;
    try {
      this.engine.cancel();
      this.current = null;
      this.phase = "stopped";
      this.lastError = null;
      this.feedback("已提交停止请求；请确认设备实际声音。");
      this.render();
      this.win.console.info("[PRS speech] Cancel submitted", {request_id: this.lastRequest?.id ?? null});
      this.notify("cancel");
      return this.result("stop", true);
    } catch (error) {
      this.lastError = "cancel_failed";
      this.win.console.error("[PRS speech] Cancel failed", {request_id: this.lastRequest?.id ?? null,
        state: this.phase, error_name: error?.name});
      this.feedback("停止请求异常，输出状态尚未确认，请手动检查。", "error");
      this.notify("error", "cancel_failed");
      return this.result("stop", false, "cancel_failed");
    } finally { this.canceling = false; }
  }

  /** Functionality: Submit the test UI's explicit settings through the shared output entry point.
   * Inputs: User text and language/voice/rate/volume controls. Outputs: speak result and the same lifecycle events.
   * Logic: Convert range values to numbers only; preserve text. Constraints: Do not enable output or replace a voice automatically.
   */
  handlePlay() {
    return this.speak(this.text.value, {language: this.language.value, voice_id: this.voice.value || null,
      rate: Number(this.rate.value), volume: Number(this.volume.value)});
  }

  /** Functionality: Apply the user's enable/disable choice.
   * Inputs: enabled.checked and current. Outputs: Status and controls; disabling requests cancellation of current output.
   * Logic: Toggling does not speak; a cancellation failure after disabling is not overwritten by a success message.
   * Constraints: Do not persist the toggle; the MCP interface does not enable it automatically.
   */
  handleEnable() {
    if (!this.enabled.checked && this.current) this.stop();
    else this.feedback(this.enabled.checked ? "已启用语音输出，可以点击朗读。" : "语音输出已关闭。");
    this.render();
    this.win.console.info("[PRS speech] Setting changed", {enabled: this.enabled.checked});
  }

  /** Functionality: Populate an explicit Chinese or English sample without speaking it.
   * Inputs: event.currentTarget.dataset.speechSample. Outputs: Text and language controls.
   * Logic: Accept only fixed language keys and preserve the selected voice and rate for user review.
   * Constraints: Do not start speech implicitly or change the selected voice.
   */
  handleSample(event) {
    const language = event.currentTarget.dataset.speechSample;
    if (!Object.hasOwn(SAMPLES, language)) return;
    this.text.value = SAMPLES[language];
    this.language.value = language;
  }

  /** Functionality: Record the speech engine's start event.
   * Inputs: event.target, current, and canceling. Outputs: speaking phase and a log without utterance text.
   * Logic: Apply only to the current object. Constraints: A start event does not prove the user heard audio.
   */
  handleStart(event) {
    if (event.target !== this.current || this.canceling) return;
    this.phase = "speaking";
    this.feedback("语音引擎已开始朗读；请确认设备实际声音。", "live");
    this.render();
    this.win.console.info("[PRS speech] Engine started", {request_id: this.lastRequest.id});
    this.notify("start");
  }

  /** Functionality: Record the speech engine's end event and release the current reference.
   * Inputs: event.target, current, and canceling. Outputs: completed phase and restored controls.
   * Logic: Ignore events from stale objects or during cancellation. Constraints: Report engine completion only; do not verify audio hardware.
   */
  handleEnd(event) {
    if (event.target !== this.current || this.canceling) return;
    this.current = null;
    this.phase = "completed";
    this.feedback("语音引擎报告完成；实际声音仍需在设备上确认。", "live");
    this.render();
    this.win.console.info("[PRS speech] Engine ended", {request_id: this.lastRequest.id});
    this.notify("end");
  }

  /** Functionality: Report an asynchronous native error for the current request.
   * Inputs: event.target/error, current, and canceling. Outputs: error phase, allowlisted error code, and released resources.
   * Logic: Accept only the current object; do not echo unknown external error text, and log only the request ID and standard code.
   * Constraints: Isolate cancellation and stale events; do not retry, synthesize a substitute voice, or log the utterance.
   */
  handleError(event) {
    if (event.target !== this.current || this.canceling) return;
    this.lastError = ERROR_CODES.includes(event.error) ? event.error : "unknown";
    this.current = null;
    this.phase = "error";
    this.feedback("语音引擎报告错误（{0}），请检查设备后手动再试。".replace("{0}", this.lastError), "error");
    this.render();
    this.win.console.error("[PRS speech] Engine failed", {request_id: this.lastRequest.id, code: this.lastError});
    this.notify("error", this.lastError);
  }

  /** Functionality: Cancel this module's current output when the page becomes hidden.
   * Inputs: doc.visibilityState and current. Outputs: At most one cancellation request when needed.
   * Logic: Cancel only when a request exists; do not resume when the page returns to foreground.
   * Constraints: Report failure explicitly and do not retry automatically.
   */
  handleVisibility() { if (this.doc.visibilityState !== "visible" && this.current) this.stop(); }

  /** Functionality: Request cancellation of current speech when leaving the page.
   * Inputs: current. Outputs: At most one cancel call when needed.
   * Logic: A prior successful cancellation prevents a second lifecycle event from calling cancel again.
   * Constraints: Do not depend on asynchronous work after unload or resume automatically.
   */
  handlePageHide() { if (this.current) this.stop(); }
}

export const speechOutput = typeof document !== "undefined" && document.getElementById("speech-test")
  ? new SpeechOutput() : null;
