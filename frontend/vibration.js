/** Responsibilities: 手动UI与显式MCP调用共享的手机震动测试和浏览器能力/拒绝反馈。
 * Implementation: 统一入口保留用户启用、前台和激活要求，发送固定节奏并返回结构化结果；
 * 关闭、隐藏或离页时请求取消，不自动恢复。
 * Related Modules: index.html, camera.css, translations.js, ai-control.js, integration/guidance.js；core.camera提供资源。
 * Declaration Index: VibrationTester; VibrationTester.constructor; VibrationTester.render;
 * VibrationTester.feedback; VibrationTester.callNative; VibrationTester.handleEnable;
 * VibrationTester.describe; VibrationTester.testResult; VibrationTester.testPattern;
 * VibrationTester.handleTest; VibrationTester.stop; VibrationTester.handleStop;
 * VibrationTester.handleVisibility; VibrationTester.handlePageHide; VibrationTester.subscribe;
 * VibrationTester.notify; VibrationTester.stopOwned。
 * Variable Index: PATTERNS — 固定毫秒节奏；tester — 当前页面测试控制器。
 * 实例 doc/win 为浏览器依赖；enabled/buttons/stopButton/status/message 为独立控件；
 * supported 仅说明接口存在，requested 记录尚未成功取消的已接受请求，不证明马达仍在震动；
 * nativeOutcome记录最近一次原生结果；sequence/requestId及listeners给引导模块明确所有权与API生命周期。
 */
const PATTERNS = {short: [200], long: [800], double: [150, 100, 150]};

/** Functionality: 管理手动按钮和显式MCP请求触发的震动测试。
 * Logic: 与摄像头和检测流水线解耦，分别报告接口缺失、调用拒绝、异常及请求被接受。
 * Constraints: 不联网、不申请媒体权限、不保存偏好，不自动重试或改用其他提醒方式。
 */
export class VibrationTester {
  /** Functionality: 安装测试控件与页面生命周期监听。
   * Inputs: doc/win，默认当前浏览器文档及窗口；页面须包含 vibration-test 区域。
   * Outputs: 实例、控件初始状态和一次能力日志，不触发震动。
   * Logic: 默认关闭；通过注入依赖支持控制逻辑测试，真实事件仍由浏览器派发。
   * Constraints: supported 不证明设备具有马达或系统允许震动；无权限弹窗。
   */
  constructor(doc = document, win = window) {
    this.doc = doc;
    this.win = win;
    this.enabled = doc.getElementById("vibration-enabled");
    this.buttons = [...doc.querySelectorAll("[data-vibration-pattern]")];
    this.stopButton = doc.getElementById("vibration-stop");
    this.status = doc.getElementById("vibration-status");
    this.message = doc.getElementById("vibration-message");
    this.supported = typeof win.navigator.vibrate === "function";
    this.requested = false;
    this.nativeOutcome = null;
    this.sequence = 0;
    this.requestId = null;
    this.listeners = new Set();
    this.enabled.checked = false;
    this.handleEnable = this.handleEnable.bind(this);
    this.handleTest = this.handleTest.bind(this);
    this.handleStop = this.handleStop.bind(this);
    this.handleVisibility = this.handleVisibility.bind(this);
    this.handlePageHide = this.handlePageHide.bind(this);
    this.enabled.addEventListener("change", this.handleEnable);
    for (const button of this.buttons) button.addEventListener("click", this.handleTest);
    this.stopButton.addEventListener("click", this.handleStop);
    doc.addEventListener("visibilitychange", this.handleVisibility);
    win.addEventListener("pagehide", this.handlePageHide);
    this.status.textContent = this.supported ? "接口可用" : "接口不可用";
    this.status.dataset.kind = this.supported ? "live" : "error";
    this.feedback(this.supported ? "勾选启用后，点击下方按钮测试。" :
      "当前浏览器未提供震动接口。可在安卓 Chrome 中测试；iPhone Safari 不支持。",
    this.supported ? "idle" : "error");
    this.render();
    win.console.info("[PRS vibration] Initialized", {supported: this.supported});
  }
  /** Functionality: Subscribe to vibration API lifecycle. Inputs: listener. Outputs: unsubscribe callback.
   * Logic: Set retains listeners. Constraints: API acknowledgement is not observed motor start/end. */
  subscribe(listener) { this.listeners.add(listener); return () => this.listeners.delete(listener); }
  /** Functionality: Publish non-sensitive request events. Inputs: phase/reason. Outputs: callbacks.
   * Logic: each accepted pattern has an identity; cancel/error refer to the current request.
   * Constraints: no hardware verification, retries or synthetic activation; observer failures are diagnosed. */
  notify(phase, reason = null) {
    for (const listener of this.listeners) {
      try { listener({channel: "vibration", phase, request_id: this.requestId, reason,
        at: performance.now(), hardware_verified: false}); }
      catch (error) { this.win.console.error("Vibration observer failed", {error_type: error.name}); }
    }
  }
  /** Functionality: Cancel only the caller's accepted pattern. Inputs: requestId. Outputs: boolean.
   * Logic: verify identity and requested flag before existing cancellation.
   * Constraints: cannot cancel a newer manual pattern or infer actual motor completion. */
  stopOwned(requestId) {
    return this.requested && requestId === this.requestId ? this.stop("guidance") : false;
  }

  /** Functionality: 根据能力和用户开关刷新操作边界。
   * Inputs: supported/enabled.checked。Outputs: disabled 属性，不调用震动接口。
   * Logic: 测试必须明确启用；停止按钮在接口存在时始终可用，包括关闭后的取消拒绝场景。
   * Constraints: 不根据展示文字推断内部状态，不影响摄像头控件。
   */
  render() {
    this.enabled.disabled = !this.supported;
    for (const button of this.buttons) button.disabled = !this.supported || !this.enabled.checked;
    this.stopButton.disabled = !this.supported;
  }

  /** Functionality: 显示独立诊断文字与提示颜色。
   * Inputs: text 为词典中文原文，kind 为 idle/live/error。Outputs: 可翻译的 aria-live 文本。
   * Logic: 仅使用 textContent，交由现有语言观察器翻译。
   * Constraints: 不把请求被接受表述为已观察到物理震动。
   */
  feedback(text, kind = "idle") {
    this.message.textContent = text;
    this.message.dataset.kind = kind;
  }

  /** Functionality: 调用原生震动接口并记录可诊断的结果。
   * Inputs: pattern 为固定毫秒数组或取消值0，action 为测试类型或取消原因；win.navigator/console。
   * Outputs: 仅原生返回true时为true；拒绝或接口异常为false并显示错误。
   * Logic: 保持 navigator 方法接收者；只捕获本次原生调用的异常，不吞掉初始化/DOM错误。
   * Constraints: 每次仅调用一次；无重试、替代接口或硬件成功推断；日志不包含媒体和身份信息。
   */
  callNative(pattern, action) {
    try {
      const accepted = this.win.navigator.vibrate(pattern) === true;
      this.nativeOutcome = accepted ? "accepted" : "native_rejected";
      this.win.console.info("[PRS vibration] Native request", {action, pattern, accepted});
      if (!accepted) this.feedback(
        "浏览器拒绝了震动请求。请检查页面交互和系统设置后手动再试。", "error");
      return accepted;
    } catch (error) {
      this.nativeOutcome = "native_error";
      this.win.console.error("[PRS vibration] Native request failed", {
        action, pattern, errorName: error?.name, errorMessage: error?.message,
      });
      this.feedback("震动调用异常，请查看浏览器控制台后手动再试。", "error");
      return false;
    }
  }

  /** Functionality: 应用用户明确的启用/关闭选择。
   * Inputs: enabled.checked/requested。Outputs: 控件状态、提示；关闭后必要时发送取消值0。
   * Logic: 启用本身不震动；曾被接受的请求只有在取消成功后才清除。
   * Constraints: 关闭时取消失败仍明确显示，可手动点停止；不保存或自动开启偏好。
   */
  handleEnable() {
    if (!this.enabled.checked && this.requested) this.stop("disabled");
    else this.feedback(this.enabled.checked ? "已启用。点击短震、长震或双震测试。" : "震动测试已关闭。");
    this.render();
    this.win.console.info("[PRS vibration] Setting changed", {enabled: this.enabled.checked});
  }

  /** Functionality: 读取震动调用前提与请求记录。
   * Inputs: supported、开关、doc.visibilityState、navigator.userActivation和requested。
   * Outputs: JSON兼容状态；user_activated在浏览器不提供该信息时为null。
   * Logic: 读取同一控制器与当前request_id，供UI/MCP/引导共享，不调用原生震动或申请权限。
   * Constraints: request_pending仅指已接受且未成功取消的记录；hardware_verified始终为false。
   */
  describe() {
    return {available: true, supported: this.supported, enabled: this.enabled.checked,
      visible: this.doc.visibilityState === "visible",
      user_activated: this.win.navigator.userActivation?.hasBeenActive ?? null,
      request_pending: this.requested, request_id: this.requestId, hardware_verified: false};
  }

  /** Functionality: 构造一次测试的机器可读结果并记录拒绝边界。
   * Inputs: name、accepted、reason；当前控制器状态。
   * Outputs: 带operation/pattern/accepted/reason的状态，不依赖被翻译的DOM文字。
   * Logic: 原生调用前的拒绝单独记录原因；原生调用本身由callNative记录。
   * Constraints: 不重试、不保存无限历史、不把浏览器接受解释为物理震动完成。
   */
  testResult(name, accepted, reason) {
    if (!accepted && !reason.startsWith("native_"))
      this.win.console.info("[PRS vibration] Test blocked", {pattern: name, reason});
    return {...this.describe(), operation: "test", pattern: name, accepted, reason};
  }

  /** Functionality: 为手动按钮或显式MCP调用发送一次固定节奏。
   * Inputs: name(short/long/double)、开关、文档可见性及可用的用户激活状态。
   * Outputs: 结构化接受/拒绝结果和UI提示；接受后设置 requested。
   * Logic: 先校验固定节奏、接口和开关，再检查前台与浏览器已发生的用户交互。
   * Constraints: 不依赖摄像头、不排队或循环；新请求替换旧节奏由原生API处理。
   */
  testPattern(name) {
    if (!Object.hasOwn(PATTERNS, name)) {
      this.win.console.error("[PRS vibration] Unknown pattern", {name});
      this.feedback("未知震动测试类型，请刷新页面检查控件。", "error");
      return this.testResult(name, false, "unknown_pattern");
    }
    if (!this.supported || !this.enabled.checked) {
      this.feedback("当前无法测试震动，请确认浏览器支持并启用测试。", "error");
      return this.testResult(name, false, this.supported ? "disabled" : "unsupported");
    }
    if (this.doc.visibilityState !== "visible") {
      this.feedback("请保持页面在前台，再手动测试震动。", "error");
      return this.testResult(name, false, "page_hidden");
    }
    if (this.win.navigator.userActivation && !this.win.navigator.userActivation.hasBeenActive) {
      this.feedback("请先点击页面启用测试，再点击震动按钮。", "error");
      return this.testResult(name, false, "activation_required");
    }
    if (this.callNative([...PATTERNS[name]], name)) {
      this.requested = true;
      this.requestId = ++this.sequence;
      this.notify("start");
      this.feedback("震动请求已接受，请在手机上确认实际震动；接口返回值不能验证硬件。", "live");
      return this.testResult(name, true, null);
    }
    this.notify("error", this.nativeOutcome);
    return this.testResult(name, false, this.nativeOutcome);
  }

  /** Functionality: 将手动按钮委派到共同测试入口。
   * Inputs: event.currentTarget.dataset.vibrationPattern。
   * Outputs: testPattern的结构化结果及相同UI副作用。
   * Logic: 只读取固定按钮类型，不制造用户激活。Constraints: 不重复原生调用。
   */
  handleTest(event) {
    return this.testPattern(event.currentTarget.dataset.vibrationPattern);
  }

  /** Functionality: 请求取消震动并保留拒绝诊断。
   * Inputs: reason 默认manual，或disabled/hidden/pagehide/mcp；supported/requested。
   * Outputs: 原生取消是否被接受；成功时清除requested并显示相应提示。
   * Logic: 取消不要求开关仍开启或文档仍可见，以便生命周期边界尽力释放已接受请求。
   * Constraints: 浏览器可在后台拒绝；失败不清除记录、不自动重试；不是物理马达状态检测。
   */
  stop(reason = "manual") {
    if (!this.supported) {
      this.nativeOutcome = "unsupported";
      return false;
    }
    if (!this.callNative(0, reason)) { this.notify("error", this.nativeOutcome); return false; }
    this.requested = false;
    this.notify("cancel");
    const text = reason === "disabled" ? "震动测试已关闭，已发送停止请求。" :
      reason === "hidden" || reason === "pagehide" ?
        "页面已隐藏或离开，已发送停止请求。返回后需手动测试。" : "已发送停止震动请求。";
    this.feedback(text);
    return true;
  }

  /** Functionality: 处理用户点击停止。
   * Inputs: 当前实例支持状态。Outputs: 一次manual取消请求及诊断。
   * Logic: 独立于测试开关，允许主动取消。Constraints: 无循环或重试。
   */
  handleStop() { this.stop(); }

  /** Functionality: 页面进入后台时取消曾被接受的请求。
   * Inputs: doc.visibilityState/requested。Outputs: 必要时发送取消值0。
   * Logic: 仅隐藏且存在未取消请求时执行；返回前台不发起震动。
   * Constraints: 无后台提醒能力；请求拒绝由callNative明确记录。
   */
  handleVisibility() {
    if (this.doc.visibilityState !== "visible" && this.requested) this.stop("hidden");
  }

  /** Functionality: 离开页面时取消曾被接受的请求。
   * Inputs: requested。Outputs: 必要时发送取消值0。
   * Logic: 与隐藏事件共享取消逻辑，已成功取消则不重复调用。
   * Constraints: 不等待异步卸载、不自动恢复震动。
   */
  handlePageHide() { if (this.requested) this.stop("pagehide"); }
}

export const tester = typeof document !== "undefined" && document.getElementById("vibration-test")
  ? new VibrationTester() : null;
