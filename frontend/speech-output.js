/** Responsibilities: 浏览器语音输出共用接口、测试UI、状态事件及明确失败反馈。
 * Implementation: 默认关闭；手动UI与MCP共享speak/stop，保留前台和用户激活要求；
 * 只提交一个utterance，按start/end/error记录引擎状态，停止及后台取消，不重试或替换语音。
 * Related Modules: index.html, camera.css, translations.js, ai-control.js; core.ai_mcp/core.camera。
 * Declaration Index: SpeechOutput; SpeechOutput.constructor; SpeechOutput.feedback;
 * SpeechOutput.render; SpeechOutput.refreshVoices; SpeechOutput.describe; SpeechOutput.voiceList;
 * SpeechOutput.result; SpeechOutput.speak; SpeechOutput.stop; SpeechOutput.handlePlay;
 * SpeechOutput.handleEnable; SpeechOutput.handleSample; SpeechOutput.handleStart;
 * SpeechOutput.handleEnd; SpeechOutput.handleError; SpeechOutput.handleVisibility;
 * SpeechOutput.handlePageHide。
 * Variable Index: MAX_TEXT_LENGTH — 1000个Unicode码点；LANGUAGES — 显式中英文语言；
 * SAMPLES — 不会自动播放的测试文本；PHASE_LABELS — 引擎状态对应UI；ERROR_CODES — 标准错误白名单；
 * speechOutput — 摄像头页共享实例，其他页为null。实例engine/doc/win为浏览器依赖；
 * current保留utterance并隔离旧事件，canceling隔离取消过程事件，sequence标识请求；
 * phase/lastRequest/lastError/nativeVoices/voiceReadError为有限状态，不持久化或记录朗读正文。
 */
export const MAX_TEXT_LENGTH = 1000;
const LANGUAGES = ["zh-CN", "en-US"];
const SAMPLES = {"zh-CN": "这是 PRS 语音输出测试。", "en-US": "This is a PRS speech output test."};
const PHASE_LABELS = {idle: "等待朗读", queued: "等待引擎开始", speaking: "引擎正在朗读",
  completed: "引擎报告完成", stopped: "已请求停止", error: "语音输出失败"};
const ERROR_CODES = ["canceled", "interrupted", "audio-busy", "audio-hardware", "network",
  "synthesis-unavailable", "synthesis-failed", "language-unavailable", "voice-unavailable",
  "text-too-long", "invalid-argument", "not-allowed"];

/** Functionality: 管理单个浏览器语音请求及独立的手动/MCP输出接口。
 * Logic: 使用SpeechSynthesisUtterance的真实事件，不把speak的void返回值当成播放完成；
 * 以对象身份隔离已取消请求的迟到事件。Constraints: 不录音、不开摄像头、不保存正文，
 * 无自动排队、重试、语音替换或音频文件生成；远程语音是否联网由浏览器语音服务决定。
 */
export class SpeechOutput {
  /** Functionality: 绑定语音UI和浏览器生命周期，读取初始语音列表。
   * Inputs: doc/win，默认当前文档/窗口；页面必须包含speech-test控件。
   * Outputs: 实例、状态提示、事件注册；不自动朗读或请求传感器权限。
   * Logic: 当前请求为空且开关关闭；voiceschanged更新异步语音列表，保留显式选择。
   * Constraints: 支持状态只证明接口存在；列表可能稍后加载，错误明确记录而不自动重试。
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

  /** Functionality: 发布可翻译的语音提示。
   * Inputs: text为固定UI原文，kind为idle/live/error。Outputs: textContent及颜色状态。
   * Logic: 现有i18n观察器处理显示；从不显示朗读正文或外部异常消息。
   * Constraints: 引擎事件和请求接受均不证明扬声器实际出声。
   */
  feedback(text, kind = "idle") {
    this.message.textContent = text;
    this.message.dataset.kind = kind;
  }

  /** Functionality: 根据接口、用户开关和当前请求刷新控件。
   * Inputs: supported/enabled/current/phase、range值及lastRequest。
   * Outputs: disabled、状态、数值标签和实际请求元数据摘要。
   * Logic: 一次只允许一个请求；停止始终可用于支持接口，设置在当前请求期间锁定。
   * Constraints: 文本输入不被翻译或自动改写；不根据已翻译的状态文字推断程序状态。
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

  /** Functionality: 读取语音列表并维护稳定的显式选择。
   * Inputs: engine.getVoices、voice.value和doc。Outputs: 原生列表、选项及读取错误状态。
   * Logic: 注册异步列表事件；仅值确实改变时记录计数。已选ID消失时保留禁用占位选项，
   * 不自动选用其他声音。名称/语言/local/remote标记保留原生身份以便MCP精确选择。
   * Constraints: 只捕获原生列表调用的异常；默认语音是显式选项，不是失败后的替代路径。
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

  /** Functionality: 查询状态及最近一次请求的非正文元数据。
   * Inputs: 控件、当前utterance、原生状态和有限请求/错误记录。Outputs: JSON兼容状态。
   * Logic: 区分排队、开始、完成与取消；不读取或返回当前文本输入。
   * Constraints: audio_output_verified始终false；speaking/pending/paused不是设备可听见的证明。
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

  /** Functionality: 返回此设备可用语音的结构化身份。
   * Inputs: 当前原生语音服务。Outputs: 状态、完整语音列表或明确voice_query_failed。
   * Logic: 一次列表查询；暴露URI/名称/语言/本地性/default供显式选择。
   * Constraints: 不朗读、不启用；失败不返回旧缓存为当前有效列表。
   */
  voiceList() {
    this.refreshVoices();
    return {...this.describe(), operation: "voices", accepted: this.supported && !this.voiceReadError,
      reason: !this.supported ? "unsupported" : this.voiceReadError ? "voice_query_failed" : null,
      voices: this.voiceReadError ? [] : this.nativeVoices.map(voice => ({id: voice.voiceURI,
        name: voice.name, language: voice.lang, local_service: voice.localService, default: voice.default}))};
  }

  /** Functionality: 构造操作应答，明确提交/取消与实际声音的区别。
   * Inputs: operation、accepted和reason。Outputs: 当前状态和机器可读结果。
   * Logic: 返回有限状态快照；拒绝只记录操作/原因，不记录正文。
   * Constraints: 无重试，无隐式启用或声音切换，结果不保证start/end事件会到达。
   */
  result(operation, accepted, reason = null) {
    if (!accepted) this.win.console.info("[PRS speech] Request refused", {operation, reason});
    return {...this.describe(), operation, accepted, reason};
  }

  /** Functionality: 提交一次受控文本朗读请求。
   * Inputs: text；options.language(zh-CN/en-US)、voice_id(null/空字符串表示浏览器默认)、
   * rate(0.5..2，默认1)、volume(0..1，默认1)；用户开关、前台、真实激活与引擎状态。
   * Outputs: 提交是否完成及结构化原因；成功先为queued，由原生事件更新状态。
   * Logic: 校验Unicode长度和参数，再检查前提/繁忙状态；指定声音必须唯一存在且主语言匹配。
   * 保留utterance强引用后绑定start/end/error并只调用一次speak。
   * Constraints: 不排队或自动中断旧请求；原生异常/事件错误不重试，不改写或记录正文；
   * 默认声音由浏览器按明确language选择，指定声音失效不会替换成默认。
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
      return this.result("speak", false, "native_error");
    }
  }

  /** Functionality: 请求取消本页面语音引擎的当前/排队输出。
   * Inputs: 当前engine/current；不要求启用开关、前台或仍有本模块请求。
   * Outputs: 取消调用是否返回；成功清除当前引用并标记stopped，异常保留请求记录。
   * Logic: canceling期间忽略同步取消事件，成功后的旧事件以对象身份隔离。
   * Constraints: cancel的void返回不证明实际声音已停止；失败保持状态可继续观察并手动停止，
   * 不自动重试；本页面语音引擎由此共用接口统一管理。
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
      return this.result("stop", true);
    } catch (error) {
      this.lastError = "cancel_failed";
      this.win.console.error("[PRS speech] Cancel failed", {request_id: this.lastRequest?.id ?? null,
        state: this.phase, error_name: error?.name});
      this.feedback("停止请求异常，输出状态尚未确认，请手动检查。", "error");
      return this.result("stop", false, "cancel_failed");
    } finally { this.canceling = false; }
  }

  /** Functionality: 用测试UI的明确配置调用共用输出入口。
   * Inputs: 用户文本、language/voice/rate/volume控件。Outputs: speak结果及相同状态事件。
   * Logic: 仅range值转为number；不改写文本。Constraints: 不自动启用或替换语音。
   */
  handlePlay() {
    return this.speak(this.text.value, {language: this.language.value, voice_id: this.voice.value || null,
      rate: Number(this.rate.value), volume: Number(this.volume.value)});
  }

  /** Functionality: 应用用户的启用/关闭选择。
   * Inputs: enabled.checked和current。Outputs: 状态/控件；关闭时取消当前输出。
   * Logic: 开关本身不朗读，关闭后的取消失败不被成功提示覆盖。
   * Constraints: 不持久化开关，MCP接口不会自动开启它。
   */
  handleEnable() {
    if (!this.enabled.checked && this.current) this.stop();
    else this.feedback(this.enabled.checked ? "已启用语音输出，可以点击朗读。" : "语音输出已关闭。");
    this.render();
    this.win.console.info("[PRS speech] Setting changed", {enabled: this.enabled.checked});
  }

  /** Functionality: 填入明确的中英文示例而不播放。
   * Inputs: event.currentTarget.dataset.speechSample。Outputs: 文本/语言控件。
   * Logic: 只接受固定语言键，保留当前声音和速度供用户检查。
   * Constraints: 不隐式开始朗读或更换所选声音。
   */
  handleSample(event) {
    const language = event.currentTarget.dataset.speechSample;
    if (!Object.hasOwn(SAMPLES, language)) return;
    this.text.value = SAMPLES[language];
    this.language.value = language;
  }

  /** Functionality: 记录语音引擎的开始事件。
   * Inputs: event.target和current/canceling。Outputs: speaking状态和非正文日志。
   * Logic: 仅当前对象生效。Constraints: 开始事件不证明用户听到了声音。
   */
  handleStart(event) {
    if (event.target !== this.current || this.canceling) return;
    this.phase = "speaking";
    this.feedback("语音引擎已开始朗读；请确认设备实际声音。", "live");
    this.render();
    this.win.console.info("[PRS speech] Engine started", {request_id: this.lastRequest.id});
  }

  /** Functionality: 记录语音引擎的结束事件并释放当前引用。
   * Inputs: event.target/current/canceling。Outputs: completed状态及控件恢复。
   * Logic: 旧对象或取消期间的事件不修改状态。Constraints: 只报告引擎完成，不验证音频硬件。
   */
  handleEnd(event) {
    if (event.target !== this.current || this.canceling) return;
    this.current = null;
    this.phase = "completed";
    this.feedback("语音引擎报告完成；实际声音仍需在设备上确认。", "live");
    this.render();
    this.win.console.info("[PRS speech] Engine ended", {request_id: this.lastRequest.id});
  }

  /** Functionality: 明确报告当前请求的异步原生错误。
   * Inputs: event.target/error；current/canceling。Outputs: error状态、白名单错误码和资源释放。
   * Logic: 只接受当前对象；未知外部错误内容不回显，日志只包含请求ID和标准代码。
   * Constraints: 取消及旧事件被隔离；不自动重试、合成替代声音或记录正文。
   */
  handleError(event) {
    if (event.target !== this.current || this.canceling) return;
    this.lastError = ERROR_CODES.includes(event.error) ? event.error : "unknown";
    this.current = null;
    this.phase = "error";
    this.feedback("语音引擎报告错误（{0}），请检查设备后手动再试。".replace("{0}", this.lastError), "error");
    this.render();
    this.win.console.error("[PRS speech] Engine failed", {request_id: this.lastRequest.id, code: this.lastError});
  }

  /** Functionality: 页面转入后台时取消本模块的当前输出。
   * Inputs: doc.visibilityState/current。Outputs: 必要时一次取消请求。
   * Logic: 只在存在当前请求时取消；回到前台不恢复。Constraints: 失败明确反馈，不自动重试。
   */
  handleVisibility() { if (this.doc.visibilityState !== "visible" && this.current) this.stop(); }

  /** Functionality: 离页时请求取消当前语音。
   * Inputs: current。Outputs: 必要时一次cancel。
   * Logic: 已成功取消则不会因第二个生命周期事件重复调用。
   * Constraints: 不依赖卸载后的异步工作，不自动恢复。
   */
  handlePageHide() { if (this.current) this.stop(); }
}

export const speechOutput = typeof document !== "undefined" && document.getElementById("speech-test")
  ? new SpeechOutput() : null;
