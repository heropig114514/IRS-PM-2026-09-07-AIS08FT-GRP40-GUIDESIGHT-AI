/** Responsibilities: Provide shared browser speech output, test UI, lifecycle events, and explicit failure feedback.
 * Implementation: Unified assist pages stream backend Qwen PCM through Web Audio; legacy standalone pages retain native SpeechSynthesis.
 * Both modes are off by default, share manual/MCP speak/stop, preserve activation/foreground rules, and never retry.
 * Explicit risk requests may use composed prepared PCM through the integration cache;
 * other utterances use the original socket.
 * Snapshot request events before broadcasting and give each subscriber a copy to isolate synchronous reentry or mutation.
 * Related Modules: index.html, camera.css, translations.js, ai-control.js, integration/guidance.js,
 * integration/risk-speech-cache.js; core.ai_mcp/core.camera.
 * Declaration Index: SpeechOutput; SpeechOutput.constructor; SpeechOutput.feedback;
 * SpeechOutput.render; SpeechOutput.refreshVoices; SpeechOutput.describe; SpeechOutput.voiceList;
 * SpeechOutput.result; SpeechOutput.speak; SpeechOutput.unlock; SpeechOutput.speakBackend;
 * SpeechOutput.clearBackendWatchdog; SpeechOutput.armBackendWatchdog;
 * SpeechOutput.handleBackendMessage; SpeechOutput.schedulePcm; SpeechOutput.finishBackend;
 * SpeechOutput.handleBackendError; SpeechOutput.stopBackend; SpeechOutput.stop; SpeechOutput.handlePlay;
 * SpeechOutput.handleEnable; SpeechOutput.handleSample; SpeechOutput.handleStart;
 * SpeechOutput.handleEnd; SpeechOutput.handleError; SpeechOutput.handleVisibility;
 * SpeechOutput.handlePageHide; SpeechOutput.subscribe; SpeechOutput.notify; SpeechOutput.stopOwned.
 * Variable Index: MAX_TEXT_LENGTH — 1,000 Unicode code points; LANGUAGES/SPEECH_LANGUAGES — explicit and auto Chinese/English modes;
 * BACKEND_VOICE — fixed cross-language Cherry voice; PCM_SAMPLE_RATE — 24 kHz server PCM contract;
 * BACKEND_CONNECT_TIMEOUT_MS/BACKEND_FIRST_AUDIO_TIMEOUT_MS/BACKEND_AUDIO_IDLE_TIMEOUT_MS bound browser TTS waits;
 * SAMPLES — test text that is never played automatically; PHASE_LABELS/BACKEND_PHASE_LABELS — native/server phase labels; ERROR_CODES — standard error allowlist;
 * speechOutput — shared camera-page instance, null on other pages. engine/doc/win — browser dependencies;
 * current retains the utterance and isolates stale events; canceling isolates events during cancellation; sequence identifies requests;
 * phase/lastRequest/lastError/nativeVoices/voiceReadError — bounded state; listeners observe lifecycle without utterance text;
 * notify's local event fixes phase/request_id/reason/at for the current event and gives each snapshotted listener a copy;
 * backendOnly selects same-origin server TTS only on the product/diagnostic assist entries; it retains no native voice inventory.
 * before_start lets the current output owner synchronously withdraw stale guidance before any PCM source is scheduled.
 * riskSocketFactory is an optional explicit risk-only transport binding; a missing binding fails instead of falling back.
 * audioContext/gain/sources/nextAudioTime own decoded PCM playback; request watchdog and
 * setTimer/clearTimer bound TTS progress; stream state is request-object guarded; backendVoice is fixed Cherry.
 * localOnly remains restricted to the standalone integration page's native speech policy.
 */
export const MAX_TEXT_LENGTH = 1000;
const LANGUAGES = ["zh-CN", "en-US"];
const SPEECH_LANGUAGES = [...LANGUAGES, "auto"];
const BACKEND_VOICE = "Cherry";
const PCM_SAMPLE_RATE = 24000;
const BACKEND_CONNECT_TIMEOUT_MS = 15000;
const BACKEND_FIRST_AUDIO_TIMEOUT_MS = 55000;
const BACKEND_AUDIO_IDLE_TIMEOUT_MS = 35000;
const SAMPLES = {"zh-CN": "这是 PRS 语音输出测试。", "en-US": "This is a PRS speech output test."};
const PHASE_LABELS = {idle: "等待朗读", queued: "等待引擎开始", speaking: "引擎正在朗读",
  completed: "引擎报告完成", stopped: "已请求停止", error: "语音输出失败"};
const BACKEND_PHASE_LABELS = {idle: "等待音频", queued: "等待后端音频", speaking: "音频正在播放",
  completed: "音频播放完成", stopped: "已请求停止", error: "语音输出失败"};
const ERROR_CODES = ["canceled", "interrupted", "audio-busy", "audio-hardware", "network",
  "synthesis-unavailable", "synthesis-failed", "language-unavailable", "voice-unavailable",
  "text-too-long", "invalid-argument", "not-allowed"];

/** Functionality: Manage one browser speech request and separate manual/MCP output interfaces.
 * Logic: Assist product/test entries use backend binary PCM and Web Audio; standalone entries retain native SpeechSynthesis events.
 * Isolate late events from cancelled requests by object identity. Constraints: No recording, camera access, or text storage;
 * no automatic queueing, retry, voice replacement, or audio-file generation. The backend owns API credentials and never logs request text.
 */
export class SpeechOutput {
  /** Functionality: Bind speech UI and browser lifecycle, then read the initial voice list.
   * Inputs: doc/win, defaulting to current document/window; page must contain speech-test controls.
   * Outputs: Instance, status message, and event handlers; no automatic speech or sensor permission request.
   * Logic: Begin disabled; assist pages bind only AudioContext/WebSocket, while standalone pages refresh native voices asynchronously.
   * Constraints: Support status proves only browser API presence, not backend configuration or physical speaker audibility.
   */
  constructor(doc = document, win = window) {
    this.doc = doc;
    this.win = win;
    this.setTimer = typeof win.setTimeout === "function" ? win.setTimeout.bind(win) : globalThis.setTimeout;
    this.clearTimer = typeof win.clearTimeout === "function" ? win.clearTimeout.bind(win) : globalThis.clearTimeout;
    this.setTimer = typeof win.setTimeout === "function" ? win.setTimeout.bind(win) : globalThis.setTimeout;
    this.clearTimer = typeof win.clearTimeout === "function" ? win.clearTimeout.bind(win) : globalThis.clearTimeout;
    this.backendOnly = doc.documentElement?.dataset?.assist === "true";
    this.riskSocketFactory = null;
    this.engine = this.backendOnly ? null : win.speechSynthesis;
    this.AudioContext = win.AudioContext || win.webkitAudioContext;
    this.supported = this.backendOnly
      ? typeof this.AudioContext === "function" && typeof win.WebSocket === "function"
      : typeof win.SpeechSynthesisUtterance === "function" &&
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
    this.audioContext = null;
    this.audioGain = null;
    this.audioSources = new Set();
    this.nextAudioTime = 0;
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
    if (this.supported && !this.backendOnly) {
      this.engine.addEventListener("voiceschanged", this.refreshVoices);
      this.refreshVoices();
    } else if (this.backendOnly && this.supported) {
      this.feedback("后端中英文语音已就绪；首次使用请在页面内点击启用。", "idle");
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
    const phaseLabels = this.backendOnly ? BACKEND_PHASE_LABELS : PHASE_LABELS;
    this.status.textContent = this.supported ? phaseLabels[this.phase] : "接口不可用";
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
    if (this.backendOnly) {
      const selected = this.voice?.value || BACKEND_VOICE;
      const primary = this.doc.createElement("option");
      primary.value = BACKEND_VOICE;
      primary.textContent = "后端语音 · Cherry（中文 / English）";
      primary.dataset.languageOriginal = "";
      this.voice?.replaceChildren(primary);
      if (this.voice) this.voice.value = selected === BACKEND_VOICE ? selected : BACKEND_VOICE;
      this.render();
      return;
    }
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
   * Inputs: Controls, current provider request, playback state, and bounded request/error records. Outputs: JSON-compatible state.
   * Logic: Distinguish queued, audio started, completed, and cancelled; never read or return current text input.
   * Constraints: audio_output_verified is always false; speaking/pending/paused do not prove the device is audible.
   */
  describe() {
    if (this.backendOnly) return {available: true, supported: this.supported,
      enabled: this.enabled.checked, visible: this.doc.visibilityState === "visible",
      user_activated: this.win.navigator.userActivation?.hasBeenActive ?? null,
      state: this.phase, active_request: this.current !== null,
      engine_speaking: this.phase === "speaking", engine_pending: this.phase === "queued",
      engine_paused: false, voice_count: 1, voice_query_failed: false,
      last_request: this.lastRequest ? {...this.lastRequest} : null, last_error: this.lastError,
      audio_output_verified: false, speech_service: "backend"};
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
   * Inputs: Current speech service. Outputs: Fixed backend voice metadata on assist pages, otherwise native inventory.
   * Logic: Query only the active provider and expose identities needed for explicit selection.
   * Constraints: Do not speak or enable output; on failure, do not report stale native cache as current.
   */
  voiceList() {
    if (this.backendOnly) return {...this.describe(), operation: "voices", accepted: this.supported,
      reason: this.supported ? null : "unsupported", voices: this.supported ? [{id: BACKEND_VOICE,
        name: "Cherry", language: "multi", local_service: false, service: "backend",
        languages: ["zh-CN", "en-US"], default: true}] : []};
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
   * Inputs: text; options.language (zh-CN/en-US, plus auto in backend mode), voice_id (Cherry on backend;
   * null/empty selects the browser default on standalone pages), rate (0.5..2), volume (0..1),
   * optional purpose="risk" for the integration's cached backend transport, and page prerequisites.
   * Outputs: Submission status and structured reason; backend mode streams PCM while standalone mode uses native events.
   * Logic: Validate Unicode length/options and prerequisites before using only the page's configured speech provider;
   * native voices must be unique and language-matched, while backend voice and provider region remain server-fixed.
   * Risk-only transport selection is local metadata and never changes the server's synthesis command.
   * Constraints: Do not queue or interrupt older requests automatically; do not retry native exceptions/events or rewrite/store text.
   * Local-MVP standalone support accepts only explicitly localService voices and never substitutes a missing voice.
   */
  speak(text, {language = "zh-CN", voice_id = null, rate = 1, volume = 1, purpose = null} = {}) {
    const length = typeof text === "string" ? [...text].length : 0;
    if (typeof text !== "string" || !text.trim() || length > MAX_TEXT_LENGTH) {
      this.feedback("请输入1至1000个字符的朗读文本。", "error");
      return this.result("speak", false, "invalid_text");
    }
    if (!(this.backendOnly ? SPEECH_LANGUAGES : LANGUAGES).includes(language) || !Number.isFinite(rate) || rate < 0.5 || rate > 2 ||
        !Number.isFinite(volume) || volume < 0 || volume > 1 ||
        voice_id !== null && (typeof voice_id !== "string" || voice_id.length > 1024) ||
        purpose !== null && purpose !== "risk") {
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
    if (this.current || !this.backendOnly &&
        (this.engine.speaking || this.engine.pending || this.engine.paused)) {
      this.feedback("已有语音请求，请先停止或等待完成。", "error");
      return this.result("speak", false, "busy");
    }
    if (this.backendOnly) {
      if (voice_id && voice_id !== BACKEND_VOICE) {
        this.feedback("请求的后端语音不可用。", "error");
        return this.result("speak", false, "voice_unavailable");
      }
      if (purpose === "risk" && typeof this.riskSocketFactory !== "function") {
        this.feedback("风险语音准备模块不可用，请检查页面配置。", "error");
        return this.result("speak", false, "risk_audio_unavailable");
      }
      return this.speakBackend(text, {language, rate, volume, purpose});
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

  /** Functionality: Create/resume the audio output context during a real user gesture.
   * Inputs: Browser AudioContext and current state. Outputs: Promise with accepted/reason.
   * Logic: Call resume synchronously before returning so browsers can observe the activation event.
   * Constraints: Does not synthesize, request microphone access, or infer physical speaker output.
   */
  unlock() {
    if (!this.backendOnly || !this.supported) return Promise.resolve({accepted: false, reason: "unsupported"});
    try {
      const context = this.audioContext || (this.audioContext = new this.AudioContext());
      const resumed = context.state === "running" ? Promise.resolve() : context.resume();
      return Promise.resolve(resumed).then(() => {
        if (context.state !== "running") throw new Error("AudioContext did not enter running state");
        return {accepted: true};
      }).catch(error => {
        this.win.console.error("[PRS speech] Audio context unlock failed", {error_name: error?.name});
        return {accepted: false, reason: "audio_context_unavailable"};
      });
    } catch (error) {
      this.win.console.error("[PRS speech] Audio context unlock failed", {error_name: error?.name});
      return Promise.resolve({accepted: false, reason: "audio_context_unavailable"});
    }
  }

  /** Functionality: Submit one text request to the same-origin backend synthesis stream.
   * Inputs: validated text and language/rate/volume/purpose options, enabled/visible/activation gates.
   * Outputs: queued request metadata; binary PCM later advances start/end/error events.
   * Logic: Open one request-bound WebSocket, using the bound cache adapter only for explicit risk,
   * and schedule little-endian PCM chunks through the unchanged gain and pre-playback gates.
   * Constraints: No native voice lookup, text logging, retry, language rewriting or implicit queue;
   * connection, first-audio, stream-idle and scheduled-playback waits have client-side deadlines.
   */
  speakBackend(text, {language, rate, volume, purpose = null}) {
    let context, socket, audioReady;
    try {
      context = this.audioContext || (this.audioContext = new this.AudioContext());
      if (context.state !== "running") {
        audioReady = Promise.resolve(context.resume()).then(() => {
          if (context.state !== "running") throw new Error("AudioContext did not resume");
        });
      } else audioReady = Promise.resolve();
      const base = new URL(this.doc.documentElement.dataset.apiBase || "../", this.win.location.href);
      const address = new URL("ws/tts/", base);
      address.protocol = address.protocol === "https:" ? "wss:" : "ws:";
      socket = purpose === "risk" ? this.riskSocketFactory(address) : new this.win.WebSocket(address);
      socket.binaryType = "arraybuffer";
    } catch (error) {
      this.phase = "error"; this.lastError = "transport_unavailable";
      this.feedback("无法连接后端语音服务，请检查网络连接。", "error"); this.render();
      this.win.console.error("[PRS speech] Backend transport setup failed", {error_name: error?.name});
      this.notify("error", this.lastError);
      return this.result("speak", false, this.lastError);
    }
    const requestId = ++this.sequence;
    const request = {id: requestId, socket, sources: new Set(), gain: null, sampleRate: null,
      receivedBytes: 0, streamComplete: false, started: false, language, rate, volume, watchdog: null};
    this.lastRequest = {id: requestId, text_length: [...text].length, language, voice_id: BACKEND_VOICE,
      rate, volume, service: "backend", local_service: false,
      audio_transport: purpose === "risk" ? "risk_cache" : "websocket"};
    this.current = request; this.phase = "queued"; this.lastError = null;
    this.nextAudioTime = 0;
    this.feedback("已连接后端，等待中英文语音音频流。", "idle"); this.render(); this.notify("submitted");
    this.armBackendWatchdog(request, "connect", BACKEND_CONNECT_TIMEOUT_MS);
    socket.addEventListener("open", () => {
      if (this.current !== request) return;
      this.armBackendWatchdog(request, "first_audio", BACKEND_FIRST_AUDIO_TIMEOUT_MS);
      audioReady.then(() => {
        if (this.current !== request) return;
        try {
          const command = {type: "synthesize", request_id: this.win.crypto.randomUUID(), text, language, rate};
          request.requestId = command.request_id;
          socket.send(JSON.stringify(command));
        } catch (error) { this.handleBackendError(request, "transport_failed", error); }
      }, error => {
        this.handleBackendError(request,
          error?.name === "NotAllowedError" ? "activation_required" : "audio_context_unavailable", error);
      });
    });
    socket.addEventListener("message", event => this.handleBackendMessage(request, event));
    socket.addEventListener("error", event => this.handleBackendError(request, "transport_failed", event));
    socket.addEventListener("close", () => {
      if (this.current === request && !request.streamComplete)
        this.handleBackendError(request, "connection_interrupted");
    });
    this.win.console.info("[PRS speech] Backend request submitted", {request_id: requestId,
      text_length: this.lastRequest.text_length, language, rate, volume});
    return this.result("speak", true);
  }

  /** Functionality: Clear the active backend request watchdog.
   * Inputs: One request object. Outputs: no timer and no phase marker.
   * Logic: clear before terminal state changes so close/error callbacks cannot fire a second terminal event.
   * Constraints: Does not close the socket or alter playback ownership. */
  clearBackendWatchdog(request) {
    if (request?.watchdog !== null && request?.watchdog !== undefined) this.clearTimer(request.watchdog);
    if (request) request.watchdog = null;
  }

  /** Functionality: Bound a backend stream while it waits for connection or audio.
   * Inputs: request, phase and positive delay. Outputs: one terminal client error on expiry.
   * Logic: identity-check the request before classifying the timeout; later frames rearm the idle deadline.
   * Constraints: This is a stuck-transport guard, not a provider retry or audibility claim. */
  armBackendWatchdog(request, phase, delay) {
    this.clearBackendWatchdog(request);
    request.watchdog = this.setTimer(() => {
      if (this.current !== request) return;
      request.watchdog = null;
      const reason = {connect: "connection_timeout", first_audio: "first_audio_timeout",
        audio_idle: "audio_idle_timeout", playback: "playback_timeout"}[phase];
      this.handleBackendError(request, reason || "stream_timeout");
    }, delay);
  }

  /** Functionality: Route one backend control or PCM frame to the owned audio stream.
   * Inputs: Current request identity and native WebSocket message. Outputs: State transition/audio scheduling.
   * Logic: Validate control IDs and PCM format; use request identity to ignore late frames after stop,
   * reset idle timing on audio progress and switch to a playback deadline after server completion.
   * Constraints: Never decode provider text, create files, or claim that scheduled audio was audible.
   */
  handleBackendMessage(request, event) {
    if (this.current !== request) return;
    if (event.data instanceof ArrayBuffer || ArrayBuffer.isView(event.data)) {
      if (request.sampleRate !== PCM_SAMPLE_RATE) return this.handleBackendError(request, "invalid_audio_format");
      return this.schedulePcm(request, event.data instanceof ArrayBuffer ? new Uint8Array(event.data)
        : new Uint8Array(event.data.buffer, event.data.byteOffset, event.data.byteLength));
    }
    let message;
    try { message = JSON.parse(event.data); }
    catch (error) { return this.handleBackendError(request, "invalid_server_message", error); }
    if (message.request_id && request.requestId && message.request_id !== request.requestId)
      return this.handleBackendError(request, "request_identity_mismatch");
    if (message.type === "audio_config" && message.format === "pcm_s16le_mono" &&
        message.sample_rate === PCM_SAMPLE_RATE) {
      request.sampleRate = message.sample_rate;
      this.armBackendWatchdog(request, "audio_idle", BACKEND_AUDIO_IDLE_TIMEOUT_MS);
    } else if (message.type === "complete") {
      request.streamComplete = true;
      this.clearBackendWatchdog(request);
      if (!request.receivedBytes) return this.handleBackendError(request, "empty_audio");
      if (request.sources.size === 0) this.finishBackend(request);
      else {
        const context = this.audioContext;
        const remainingMs = context && Number.isFinite(context.currentTime) && Number.isFinite(this.nextAudioTime)
          ? Math.max(0, this.nextAudioTime - context.currentTime) * 1000 : 0;
        this.armBackendWatchdog(request, "playback", remainingMs + 10000);
      }
    } else if (message.type === "error") {
      const allowed = ["risk_audio_unavailable", "invalid_audio_resource",
        "tts_not_configured", "tts_timeout", "tts_upstream_error", "tts_upstream_unavailable",
        "tts_upstream_protocol", "tts_audio_limit", "invalid_speech_request", "invalid_message"];
      this.handleBackendError(request, allowed.includes(message.code) ? message.code : "synthesis_failed");
    } else this.handleBackendError(request, "invalid_server_message");
  }

  /** Functionality: Schedule one little-endian PCM16 block in the shared Web Audio timeline.
   * Inputs: Current request and even-length PCM bytes. Outputs: Playback source and start event.
   * Logic: Before the first block, notify the output owner to revalidate its source; normalize signed
   * samples, append continuously after queued audio, apply gain, and bound gaps between audio chunks.
   * Constraints: bounded by the backend stream limit; sample rate is fixed by protocol metadata.
   */
  schedulePcm(request, bytes) {
    if (!bytes.byteLength || bytes.byteLength % 2 || request.streamComplete)
      return this.handleBackendError(request, "invalid_audio_format");
    if (!request.started) {
      this.notify("before_start");
      if (this.current !== request || this.phase !== "queued" || this.lastError) return;
    }
    try {
      this.clearBackendWatchdog(request);
      const context = this.audioContext;
      if (!context || context.state !== "running") return this.handleBackendError(request, "audio_context_unavailable");
      const samples = bytes.byteLength / 2, buffer = context.createBuffer(1, samples, request.sampleRate);
      const channel = buffer.getChannelData(0), view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
      for (let index = 0; index < samples; index++) channel[index] = view.getInt16(index * 2, true) / 32768;
      if (!request.gain) {
        request.gain = context.createGain(); request.gain.gain.value = request.volume;
        request.gain.connect(context.destination);
      }
      const source = context.createBufferSource(); source.buffer = buffer; source.connect(request.gain);
      request.sources.add(source); this.audioSources.add(source);
      const startAt = Math.max(context.currentTime + 0.025, this.nextAudioTime);
      this.nextAudioTime = startAt + buffer.duration;
      source.addEventListener("ended", () => {
        request.sources.delete(source); this.audioSources.delete(source);
        if (this.current === request && request.streamComplete && request.sources.size === 0)
          this.finishBackend(request);
      }, {once: true});
      source.start(startAt); request.receivedBytes += bytes.byteLength;
      this.armBackendWatchdog(request, "audio_idle", BACKEND_AUDIO_IDLE_TIMEOUT_MS);
      if (!request.started) {
        request.started = true; this.phase = "speaking";
        this.feedback("后端语音已开始流式播放。", "live"); this.render();
        this.win.console.info("[PRS speech] Backend audio started", {request_id: this.lastRequest.id});
        this.notify("start");
      }
    } catch (error) { this.handleBackendError(request, "audio_playback_failed", error); }
  }

  /** Functionality: End the current server stream after every scheduled PCM source finishes.
   * Inputs: Owned request identity. Outputs: Completed phase and resource release.
   * Logic: Completion requires both server terminal metadata and exhausted AudioBufferSourceNodes;
   * a playback deadline releases ownership if a scheduled source never ends.
   * Constraints: This reports browser scheduling completion, not physical speaker verification.
   */
  finishBackend(request) {
    if (this.current !== request) return;
    this.clearBackendWatchdog(request);
    this.current = null; this.phase = "completed"; this.lastError = null;
    try { request.gain?.disconnect(); } catch {}
    this.feedback("后端语音播放完成；实际声音仍需在设备上确认。", "live"); this.render();
    this.win.console.info("[PRS speech] Backend audio ended", {request_id: this.lastRequest.id,
      audio_bytes: request.receivedBytes}); this.notify("end");
  }

  /** Functionality: Stop a request or expose one allowlisted backend failure.
   * Inputs: Owned request, safe reason and optional browser exception. Outputs: Released state/event.
   * Logic: Close the exact WebSocket and stop only its buffer sources; stale callbacks are ignored.
   * Constraints: No retries or exception-message disclosure; cancellation does not verify hardware.
   */
  handleBackendError(request, reason, error = null) {
    if (!request || this.current !== request) return;
    this.clearBackendWatchdog(request);
    this.current = null; this.phase = "error"; this.lastError = reason;
    try { request.socket.close(); } catch {}
    for (const source of request.sources) {
      this.audioSources.delete(source);
      try { source.stop(); } catch {}
      try { source.disconnect(); } catch {}
    }
    request.sources.clear();
    try { request.gain?.disconnect(); } catch {}
    this.feedback(`后端语音失败（${reason.split('_').join(' ')}），请检查连接后重试。`, "error");
    this.render(); this.win.console.error("[PRS speech] Backend synthesis failed", {
      request_id: this.lastRequest?.id, reason, error_name: error?.name}); this.notify("error", reason);
  }

  /** Functionality: Cancel the active backend socket and its locally scheduled audio.
   * Inputs: Current request and Web Audio sources. Outputs: Structured cancellation acknowledgement.
   * Logic: Clear ownership before stopping nodes so late close/onended events cannot relabel state.
   * Constraints: No native global cancellation, provider retry, or claim about physical audibility.
   */
  stopBackend() {
    const request = this.current;
    if (!request) {
      this.phase = "stopped"; this.feedback("当前没有进行中的语音输出。", "idle"); this.render();
      return this.result("stop", true);
    }
    this.current = null;
    try { request.socket.close(1000, "user_stop"); }
    catch (error) {
      this.current = request; this.lastError = "cancel_failed";
      this.win.console.error("[PRS speech] Backend stream cancellation failed", {request_id: this.lastRequest?.id,
        error_name: error?.name}); return this.result("stop", false, "cancel_failed");
    }
    this.clearBackendWatchdog(request);
    for (const source of request.sources) {
      this.audioSources.delete(source); try { source.stop(); } catch {}
      try { source.disconnect(); } catch {}
    }
    request.sources.clear(); try { request.gain?.disconnect(); } catch {}
    this.phase = "stopped"; this.lastError = null;
    this.feedback("已停止语音输出。", "idle"); this.render();
    this.win.console.info("[PRS speech] Backend stream cancelled", {request_id: this.lastRequest?.id});
    this.notify("cancel"); return this.result("stop", true);
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
    if (this.backendOnly) return this.stopBackend();
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
