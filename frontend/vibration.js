/** Responsibilities: Provide shared manual-UI and explicit-MCP vibration tests with browser capability and rejection feedback.
 * Implementation: A shared entry point enforces user enablement, foreground, and activation requirements, sends fixed patterns, and returns structured results;
 * request cancellation on disable, hide, or page exit without resuming automatically; publish fixed event snapshots with an independent copy per listener.
 * Related Modules: index.html, camera.css, translations.js, ai-control.js, integration/guidance.js; core.camera supplies the asset.
 * Declaration Index: VibrationTester; VibrationTester.constructor; VibrationTester.render;
 * VibrationTester.feedback; VibrationTester.callNative; VibrationTester.handleEnable;
 * VibrationTester.describe; VibrationTester.testResult; VibrationTester.testPattern;
 * VibrationTester.handleTest; VibrationTester.stop; VibrationTester.handleStop;
 * VibrationTester.handleVisibility; VibrationTester.handlePageHide; VibrationTester.subscribe;
 * VibrationTester.notify; VibrationTester.stopOwned。
 * Variable Index: PATTERNS — fixed millisecond patterns; tester — current-page controller.
 * Instance doc/win are browser dependencies; enabled/buttons/stopButton/status/message are independent controls.
 * supported reports API presence only; requested records an accepted request not yet successfully canceled and does not prove the motor is still vibrating.
 * nativeOutcome stores the latest native result; sequence/requestId/listeners provide explicit ownership and API lifecycle to guidance modules.
 * notify's local event fixes the request/phase/time for that event and isolates listener mutations and synchronous new requests.
 */
const PATTERNS = {short: [200], long: [800], double: [150, 100, 150]};

/** Functionality: Manage vibration tests triggered by manual controls and explicit MCP requests.
 * Logic: Keep vibration independent of the camera and detection pipeline; report missing API, rejected calls, exceptions, and accepted requests separately.
 * Constraints: No network, media permissions, persisted preferences, automatic retries, or substitute alert mechanisms.
 */
export class VibrationTester {
  /** Functionality: Bind test controls and page-lifecycle listeners.
   * Inputs: doc/win, defaulting to the current browser document/window; the page must contain the vibration-test region.
   * Outputs: Instance, initial control state, and one capability log; no vibration is triggered.
   * Logic: Start disabled; injected dependencies support controller tests while real events remain browser-dispatched.
   * Constraints: supported does not prove that a motor exists or the operating system permits vibration; no permission prompt is shown.
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
  /** Functionality: Publish one accepted-request event snapshot to independent observers.
   * Inputs: phase/reason and current request ID. Outputs: Independent body-free event copies.
   * Logic: Capture identity/time and listeners before dispatch; reentrant new patterns cannot
   * change the old event, and one observer cannot mutate another observer's copy.
   * Constraints: No hardware verification, retries or synthetic activation; failures are diagnosed. */
  notify(phase, reason = null) {
    const event = Object.freeze({channel: "vibration", phase, request_id: this.requestId, reason,
      at: performance.now(), hardware_verified: false});
    for (const listener of [...this.listeners]) {
      try { listener({...event}); }
      catch (error) { this.win.console.error("Vibration observer failed", {error_type: error.name}); }
    }
  }
  /** Functionality: Cancel only the caller's accepted pattern. Inputs: requestId. Outputs: boolean.
   * Logic: verify identity and requested flag before existing cancellation.
   * Constraints: cannot cancel a newer manual pattern or infer actual motor completion. */
  stopOwned(requestId) {
    return this.requested && requestId === this.requestId ? this.stop("guidance") : false;
  }

  /** Functionality: Refresh operation boundaries from API support and the user toggle.
   * Inputs: supported/enabled.checked. Outputs: disabled properties; does not call the vibration API.
   * Logic: Tests require explicit enablement; keep Stop available whenever the API exists, including cancellation-rejection cases after disable.
   * Constraints: Do not infer internal state from displayed text or affect camera controls.
   */
  render() {
    this.enabled.disabled = !this.supported;
    for (const button of this.buttons) button.disabled = !this.supported || !this.enabled.checked;
    this.stopButton.disabled = !this.supported;
  }

  /** Functionality: Display separate diagnostic text and status styling.
   * Inputs: text is the dictionary source string; kind is idle/live/error. Outputs: Translatable aria-live text.
   * Logic: Use textContent and let the existing language observer translate it.
   * Constraints: Do not describe an accepted request as physically observed vibration.
   */
  feedback(text, kind = "idle") {
    this.message.textContent = text;
    this.message.dataset.kind = kind;
  }

  /** Functionality: Call the native vibration API and record a diagnosable outcome.
   * Inputs: pattern is a fixed millisecond array or cancellation value 0; action is a test type or cancellation reason; win.navigator/console.
   * Outputs: true only when the native API returns true; false with an error message for rejection or exception.
   * Logic: Preserve the navigator method receiver; catch only this native call's exception and let initialization/DOM errors propagate.
   * Constraints: One call per request; no retries, alternate APIs, or hardware-success inference; logs omit media and identity information.
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

  /** Functionality: Apply the user's explicit enable/disable choice.
   * Inputs: enabled.checked/requested. Outputs: Control state and feedback; send cancellation value 0 after disabling when needed.
   * Logic: Enabling alone does not vibrate; clear an accepted request only after cancellation succeeds.
   * Constraints: Show cancellation failure after disable and allow manual Stop; do not persist or enable preferences automatically.
   */
  handleEnable() {
    if (!this.enabled.checked && this.requested) this.stop("disabled");
    else this.feedback(this.enabled.checked ? "已启用。点击短震、长震或双震测试。" : "震动测试已关闭。");
    this.render();
    this.win.console.info("[PRS vibration] Setting changed", {enabled: this.enabled.checked});
  }

  /** Functionality: Read vibration prerequisites and request state.
   * Inputs: supported, toggle, doc.visibilityState, navigator.userActivation, and requested.
   * Outputs: JSON-compatible state; user_activated is null when the browser does not expose it.
   * Logic: Read this controller and current request_id for UI/MCP/guidance consumers without calling vibration or requesting permission.
   * Constraints: request_pending means only an accepted request not yet successfully canceled; hardware_verified is always false.
   */
  describe() {
    return {available: true, supported: this.supported, enabled: this.enabled.checked,
      visible: this.doc.visibilityState === "visible",
      user_activated: this.win.navigator.userActivation?.hasBeenActive ?? null,
      request_pending: this.requested, request_id: this.requestId, hardware_verified: false};
  }

  /** Functionality: Build one machine-readable test result and record rejection boundaries.
   * Inputs: name, accepted, reason, and current controller state.
   * Outputs: State with operation/pattern/accepted/reason that does not depend on translated DOM text.
   * Logic: Record pre-native-call rejection separately; callNative records outcomes from the native call itself.
   * Constraints: No retries or unbounded history; browser acceptance is not physical vibration completion.
   */
  testResult(name, accepted, reason) {
    if (!accepted && !reason.startsWith("native_"))
      this.win.console.info("[PRS vibration] Test blocked", {pattern: name, reason});
    return {...this.describe(), operation: "test", pattern: name, accepted, reason};
  }

  /** Functionality: Send one fixed pattern for a manual button or explicit MCP call.
   * Inputs: name (short/long/double), toggle, document visibility, and available user-activation state.
   * Outputs: Structured acceptance/rejection result and UI feedback; set requested after acceptance.
   * Logic: Validate fixed pattern, API, and toggle before checking foreground and prior browser interaction.
   * Constraints: No camera dependency, queue, or loop; the native API handles a new request replacing an old pattern.
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

  /** Functionality: Delegate a manual button to the shared test entry point.
   * Inputs: event.currentTarget.dataset.vibrationPattern.
   * Outputs: testPattern's structured result and the same UI side effects.
   * Logic: Read only the fixed button type; do not manufacture user activation.
   * Constraints: Do not duplicate the native call.
   */
  handleTest(event) {
    return this.testPattern(event.currentTarget.dataset.vibrationPattern);
  }

  /** Functionality: Request cancellation and preserve rejection diagnostics.
   * Inputs: reason, default manual or disabled/hidden/pagehide/mcp; supported/requested.
   * Outputs: Whether native cancellation was accepted; on success clear requested and show corresponding feedback.
   * Logic: Cancellation does not require the toggle to remain on or the document to remain visible, allowing lifecycle boundaries to release accepted requests.
   * Constraints: Browsers may reject in the background; failure preserves the record and is not retried; this is not motor-state detection.
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

  /** Functionality: Handle the user's Stop click.
   * Inputs: Current instance support state. Outputs: One manual cancellation request and diagnostic feedback.
   * Logic: Allow explicit cancellation independently of the test toggle. Constraints: No loop or retry.
   */
  handleStop() { this.stop(); }

  /** Functionality: Cancel an accepted request when the page becomes hidden.
   * Inputs: doc.visibilityState/requested. Outputs: Send cancellation value 0 when needed.
   * Logic: Act only when hidden with an uncanceled request; do not vibrate when returning to foreground.
   * Constraints: No background-alert capability; callNative records request rejection explicitly.
   */
  handleVisibility() {
    if (this.doc.visibilityState !== "visible" && this.requested) this.stop("hidden");
  }

  /** Functionality: Cancel an accepted request when leaving the page.
   * Inputs: requested. Outputs: Send cancellation value 0 when needed.
   * Logic: Share cancellation logic with the hidden event and avoid duplicate calls after success.
   * Constraints: Do not wait for asynchronous unload work or resume vibration automatically.
   */
  handlePageHide() { if (this.requested) this.stop("pagehide"); }
}

export const tester = typeof document !== "undefined" && document.getElementById("vibration-test")
  ? new VibrationTester() : null;
