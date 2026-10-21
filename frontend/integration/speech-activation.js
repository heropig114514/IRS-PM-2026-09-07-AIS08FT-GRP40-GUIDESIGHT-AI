/* Responsibilities: Require observed local native speech startup before automatic assist speech.
 * Implementation: Explicit synchronous calibration through the original shared driver; matching
 * manual starts also unlock the gate. One owned queued calibration has a bounded watchdog.
 * Related Modules: assist-mvp.js/assist-guidance.js; ../speech-output.js; integration tests.
 * Declaration Index: SpeechActivation; SpeechActivation.constructor; SpeechActivation.describe;
 * SpeechActivation.publish; SpeechActivation.gate; SpeechActivation.ungate;
 * SpeechActivation.selection; SpeechActivation.failure; SpeechActivation.clearWatchdog;
 * SpeechActivation.activate; SpeechActivation.onDriver; SpeechActivation.cancelOwned;
 * SpeechActivation.stop; SpeechActivation.dispose.
 * Variable Index: CALIBRATION is fixed zh-CN/en-US text, never a generated risk instruction;
 * owned/generation/ignoreThrough isolate calibration IDs, late callbacks and old timers;
 * ready/firstStartedId retain the first real matching native start, never physical audibility;
 * state/reason expose recovery needs; timeoutMs only bounds initial queued calibration.
 * Constraints: No automatic enablement, voice substitution, retry, engine resume loop, media/GPS
 * permission, global speech cancellation, foreign request cancellation or core modifications.
 */

const CALIBRATION = {"zh-CN": "语音提醒已开启。", "en-US": "Voice alerts are enabled."};

/* Functionality: Gate automatic speech until the existing driver observes local native startup.
 * Logic: Keep calibration ownership separate from manager and manual requests, preserving both.
 * Constraints: A native start can establish browser readiness but never confirms audible hardware. */
export class SpeechActivation {
  /* Functionality: Pause automatic speech before output enablement without speaking.
   * Inputs: Shared speech/manager, explicit config getter, observer and optional timer dependencies.
   * Outputs: Blocked activation controller. Logic: subscribe once and preserve unrelated manager errors.
   * Constraints: Construction never requests permissions, enables outputs, selects voices or starts audio. */
  constructor({speech, manager, config, onChange = () => {},
    setTimer = (callback, delay) => globalThis.setTimeout(callback, delay),
    clearTimer = handle => globalThis.clearTimeout(handle), timeoutMs = 5000} = {}) {
    if (!speech || !manager || typeof config !== "function" || typeof onChange !== "function" ||
        typeof setTimer !== "function" || typeof clearTimer !== "function" ||
        !Number.isFinite(timeoutMs) || timeoutMs <= 0)
      throw new TypeError("Speech activation requires explicit drivers/config and a positive deadline");
    Object.assign(this, {speech, manager, config, onChange, setTimer, clearTimer, timeoutMs});
    this.ready = this.disposed = this.cancelling = false;
    this.state = "blocked"; this.reason = "activation_required";
    this.owned = this.watchdog = this.firstStartedId = null;
    this.generation = this.ignoreThrough = 0;
    this.onDriver = this.onDriver.bind(this);
    this.gate();
    this.unsubscribe = speech.subscribe(this.onDriver);
    this.publish();
  }

  /* Functionality: Read non-body activation evidence. Inputs: Current controller state.
   * Outputs: Detached metadata. Logic: retained cancellation failures remain visible by request ID.
   * Constraints: No spoken text, coordinates, native object or physical-audibility claim. */
  describe() {
    return {ready: this.ready, state: this.state, reason: this.reason,
      request_id: this.firstStartedId ?? this.owned?.id ?? null,
      owned_request_id: this.owned?.id ?? null, cancel_failed: this.owned?.cancelFailed === true,
      disposed: this.disposed};
  }

  /* Functionality: Notify the integration UI without changing output semantics.
   * Inputs: Current state. Outputs: One safe observer callback.
   * Logic: observer exceptions are diagnosed without sensitive bodies; Constraints: no state replay/retry. */
  publish() {
    try { this.onChange(this.describe()); }
    catch (error) { console.warn("Speech activation observer failed", {error_type: error?.name}); }
  }

  /* Functionality: Close only the startup gate without overwriting an unrelated channel failure.
   * Inputs: Existing manager state. Outputs: activation_required pause when otherwise unpaused.
   * Logic: current explicit errors retain precedence; Constraints: no driver cancellation or source stop. */
  gate() {
    if (this.manager.describe().paused?.speech !== true)
      this.manager.pause("speech", "activation_required");
  }

  /* Functionality: Release only the pause owned by speech startup.
   * Inputs: Existing manager pause reason. Outputs: Explicit matching-gate resume only.
   * Logic: real startup or a later user activation may clear this gate; Constraints: preserve all other errors. */
  ungate() {
    const state = this.manager.describe();
    if (state.paused?.speech === true && state.reasons?.speech === "activation_required")
      this.manager.resume("speech");
  }

  /* Functionality: Validate the explicit local voice and unchanged user output settings.
   * Inputs: Config getter and original native inventory/controls. Outputs: Options or fixed failure reason.
   * Logic: exact voice ID, supported language and local-service match; no coercion to a default voice.
   * Constraints: no enablement, language fallback, remote voice or volume/rate adjustment. */
  selection() {
    try {
      const {voiceId, language} = this.config() || {};
      if (!Object.hasOwn(CALIBRATION, language)) return {reason: "invalid_language"};
      if (typeof voiceId !== "string" || !voiceId) return {reason: "explicit_local_voice_required"};
      const inventory = this.speech.voiceList();
      const voices = inventory.voices?.filter(voice => voice.id === voiceId) || [];
      if (!inventory.accepted || voices.length !== 1) return {reason: "local_voice_unavailable"};
      if (voices[0].local_service !== true) return {reason: "local_voice_required"};
      if (typeof voices[0].language !== "string" ||
          voices[0].language.split("-")[0].toLowerCase() !== language.split("-")[0].toLowerCase())
        return {reason: "voice_language_mismatch"};
      const rate = Number(this.speech.rate?.value), volume = Number(this.speech.volume?.value);
      if (!Number.isFinite(rate) || rate < 0.5 || rate > 2 || !Number.isFinite(volume) || volume < 0 || volume > 1)
        return {reason: "invalid_parameters"};
      return {voiceId, language, rate, volume};
    } catch { return {reason: "configuration_unavailable"}; }
  }

  /* Functionality: Report a blocked attempt while preserving the startup gate and ownership.
   * Inputs: Fixed reason and state. Outputs: Explicit rejected activation result.
   * Logic: do not retry or discard an uncertain request; Constraints: unrelated manager errors survive. */
  failure(reason, state = "blocked") {
    this.state = state; this.reason = reason; this.gate(); this.publish();
    return {...this.describe(), accepted: false};
  }

  /* Functionality: Remove the one calibration deadline.
   * Inputs: Owned timer. Outputs: Cleared timer identity. Logic: clear before callbacks can reenter.
   * Constraints: no new timer or retry is scheduled here. */
  clearWatchdog() {
    const handle = this.watchdog; this.watchdog = null;
    if (handle !== null) this.clearTimer(handle);
  }

  /* Functionality: Calibrate once directly inside the caller's trusted click handler.
   * Inputs: Current explicit selection and shared-driver prerequisites. Outputs: Accepted/rejected state.
   * Logic: call existing speak synchronously, with ownership installed before synchronous driver events.
   * Constraints: no await/microtask, default voice, output enablement, automatic retry or replacement speech. */
  activate() {
    if (this.disposed) return {...this.describe(), accepted: false, reason: "disposed"};
    if (this.owned?.cancelFailed) return this.failure("cancel_failed", "error");
    if (this.ready) {
      this.state = "ready"; this.reason = null; this.ungate(); this.publish();
      return {...this.describe(), accepted: true};
    }
    if (this.owned) return {...this.describe(), accepted: false, reason: "activation_pending"};
    const options = this.selection();
    if (options.reason) return this.failure(options.reason);
    const status = this.speech.describe();
    if (!status.supported || !status.enabled) return this.failure(status.supported ? "disabled" : "unsupported");
    if (status.visible === false) return this.failure("page_hidden");
    if (status.user_activated === false) return this.failure("activation_required");
    if (status.active_request || status.engine_speaking || status.engine_pending || status.engine_paused)
      return this.failure("busy");
    this.gate();
    const pending = {id: null, generation: ++this.generation, started: false, cancelFailed: false};
    this.owned = pending; this.state = "queued"; this.reason = "awaiting_native_start"; this.publish();
    if (this.owned !== pending || pending.generation !== this.generation)
      return {...this.describe(), accepted: false, reason: "activation_cancelled"};
    let result;
    try {
      result = this.speech.speak(CALIBRATION[options.language], {language: options.language,
        voice_id: options.voiceId, rate: options.rate, volume: options.volume});
    } catch { result = {accepted: false, reason: "speak_failed"}; }
    if (!result?.accepted) {
      if (this.owned === pending && !pending.cancelFailed) {
        this.ignoreThrough = Math.max(this.ignoreThrough, pending.id || 0); this.owned = null;
      }
      return this.failure(result?.reason || "speak_failed", "error");
    }
    // A real synchronous terminal callback may already have released this owner, while an
    // earlier manager listener submitted a newer request. Its result must not replace our ID.
    if (this.owned !== pending)
      return {...this.describe(), accepted: pending.started && this.ready,
        ...(!pending.started || !this.ready ? {reason: this.reason || "activation_cancelled"} : {})};
    const id = result.last_request?.id;
    if (!Number.isSafeInteger(id) || id <= 0 || pending.id !== null && pending.id !== id)
      return this.failure("submission_identity_invalid", "error");
    pending.id = id;
    if (this.owned === pending && !pending.started && !pending.cancelFailed) {
      this.watchdog = this.setTimer(() => {
        if (this.disposed || this.ready || this.owned !== pending || pending.generation !== this.generation || pending.started) return;
        this.watchdog = null; this.cancelOwned("activation_timeout");
      }, this.timeoutMs);
    }
    this.publish(); return {...this.describe(), accepted: true};
  }

  /* Functionality: Consume actual shared-driver starts and calibration terminal events.
   * Inputs: Body-free driver event. Outputs: Readiness, owner cleanup or retained failure.
   * Logic: matching selected local manual starts also unlock; stopped/old IDs cannot reopen the gate.
   * Constraints: submission alone never unlocks, foreign requests are never cancelled or adopted. */
  onDriver(event) {
    if (this.disposed || this.cancelling || event.channel !== "speech" ||
        !Number.isSafeInteger(event.request_id) || event.request_id <= this.ignoreThrough) return;
    const own = this.owned;
    if (own && own.id === null && event.phase === "submitted") own.id = event.request_id;
    if (event.phase === "start") {
      const selection = this.selection(), status = this.speech.describe(), last = status.last_request;
      if (selection.reason || status.active_request !== true || last?.id !== event.request_id ||
          last.voice_id !== selection.voiceId || last.language !== selection.language || last.local_service !== true) return;
      if (own?.id === event.request_id) own.started = true;
      this.clearWatchdog(); this.firstStartedId ??= event.request_id;
      this.ready = true; this.state = "ready"; this.reason = null;
      this.ungate(); this.publish(); return;
    }
    if (!own || own.id !== event.request_id || !["end", "cancel", "error"].includes(event.phase)) return;
    this.clearWatchdog();
    const status = this.speech.describe();
    const retained = event.phase === "error" && (event.reason === "cancel_failed" ||
      status.active_request === true && status.last_request?.id === own.id);
    if (retained) own.cancelFailed = true;
    else { this.ignoreThrough = Math.max(this.ignoreThrough, own.id); this.owned = null; }
    if (event.phase === "end" && own.started) {
      this.state = "ready"; this.reason = null; this.publish();
    } else this.failure(event.reason || (event.phase === "cancel" ? "calibration_cancelled" :
      event.phase === "end" ? "native_ended_before_start" : "native_error"), "error");
  }

  /* Functionality: Cancel only the exact calibration request once for this explicit action/deadline.
   * Inputs: Safe reason. Outputs: Closed owner or honest retained cancel failure.
   * Logic: gate manager before cancel prevents synchronous foreign-cancel pumping another utterance.
   * Constraints: no global cancel, foreign cancellation, automatic retry or readiness fabrication. */
  cancelOwned(reason) {
    const own = this.owned; this.clearWatchdog(); this.gate();
    if (!own || own.id === null) {
      this.owned = null; this.state = this.ready ? "ready" : "stopped"; this.reason = reason;
      this.publish(); return this.describe();
    }
    let result; this.cancelling = true;
    try { result = this.speech.stopOwned(own.id); }
    catch { result = {accepted: false, reason: "cancel_failed"}; }
    finally { this.cancelling = false; }
    if (result?.accepted || result?.reason === "not_owned") {
      this.ignoreThrough = Math.max(this.ignoreThrough, own.id); this.owned = null;
      this.state = this.ready ? "ready" : "blocked"; this.reason = reason;
    } else {
      own.cancelFailed = true; this.state = "error"; this.reason = result?.reason || "cancel_failed";
    }
    this.publish(); return this.describe();
  }

  /* Functionality: Stop calibration without cancelling manual or automatic manager-owned requests.
   * Inputs: Optional safe stop reason. Outputs: Ownership metadata.
   * Logic: invalidate old timers/IDs; readiness may remain after actual browser startup.
   * Constraints: stopping does not submit/resume speech; failed cancellation needs another explicit stop. */
  stop(reason = "stopped") {
    this.generation++; this.clearWatchdog();
    this.ignoreThrough = Math.max(this.ignoreThrough, this.speech.describe().last_request?.id || 0);
    if (this.owned) return this.cancelOwned(reason);
    this.state = this.ready ? "ready" : "stopped"; this.reason = this.ready ? null : reason;
    this.publish(); return this.describe();
  }

  /* Functionality: Release one calibration and this observer subscription.
   * Inputs: Current owner. Outputs: Disposed metadata with any failed cancellation retained.
   * Logic: stop once then detach; Constraints: never disposes shared drivers or retries cleanup. */
  dispose() {
    if (!this.disposed) { this.stop("disposed"); this.disposed = true; this.unsubscribe(); this.publish(); }
    return this.describe();
  }
}
