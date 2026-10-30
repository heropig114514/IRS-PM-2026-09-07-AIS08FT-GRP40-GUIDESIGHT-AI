/* Responsibilities: Speak existing walking-navigation states independently of video perception.
 * Implementation: Explicit local voice and opt-in output; one latest semantic candidate, existing
 * GPS freshness policy, per-step transition deduplication and request-owned cancellation.
 * Related Modules: ../navigation.js, ../maps-client.js, ../speech-output.js, events.js, catalog.js;
 * ../tests/integration-navigation-speech.test.mjs. No route requests or risk scheduling.
 * Declaration Index: navigationSpeechText; NavigationSpeechAdapter;
 * NavigationSpeechAdapter.constructor; NavigationSpeechAdapter.subscribe;
 * NavigationSpeechAdapter.describe; NavigationSpeechAdapter.report;
 * NavigationSpeechAdapter.configure; NavigationSpeechAdapter.setEnabled;
 * NavigationSpeechAdapter.begin; NavigationSpeechAdapter.fresh;
 * NavigationSpeechAdapter.selectCandidate; NavigationSpeechAdapter.refresh;
 * NavigationSpeechAdapter.ingest; NavigationSpeechAdapter.suppress;
 * NavigationSpeechAdapter.pause; NavigationSpeechAdapter.cancelOwned;
 * NavigationSpeechAdapter.pump; NavigationSpeechAdapter.outputEvent;
 * NavigationSpeechAdapter.resume; NavigationSpeechAdapter.stop;
 * NavigationSpeechAdapter.dispose.
 * Variable Index: EN_TURNS translates the existing 0..13 instruction types;
 * NOTICE_TEXT contains finite state notices. Instance latest/candidate hold one snapshot only;
 * semanticKey/revision/consumed deduplicate transitions, not GPS distance updates;
 * active/pendingSubmission bind native events to owned request IDs; generation/runId separate
 * explicit sessions; sourceTimer enforces the existing GPS deadline; paused requires manual resume;
 * cancelFailed preserves ownership after either adapter-owned or shared-driver stop failure.
 */
import {NAVIGATION_POLICY} from "../navigation.js";
import {turnLabel} from "../maps-client.js";
import {languageOf} from "./catalog.js";
import {EventBus} from "./events.js";

const EN_TURNS = ["Turn left", "Turn right", "Turn sharp left", "Turn sharp right",
  "Turn slightly left", "Turn slightly right", "Continue straight", "Enter the roundabout",
  "Exit the roundabout", "Make a U-turn", "Arrive at the destination",
  "Start walking along the planned route", "Keep left", "Keep right"];
const NOTICE_TEXT = {
  off_route: ["已偏离规划路线，请确认位置后手动重新规划。",
    "You are off the planned route. Check your position and replan manually."],
  accuracy_insufficient: ["定位精度不足，暂停转向播报。",
    "Location accuracy is insufficient. Turn announcements are paused."],
  arrived: ["已到达路线终点。", "You have reached the route endpoint."],
  error: ["导航已停止，请检查定位后手动重新开始。",
    "Navigation has stopped. Check location services and start again manually."],
};

/* Functionality: Describe an existing navigation step or finite state notice without route inference.
 * Inputs: Controller snapshot and zh-CN/en-US language. Outputs: Short speech text or empty text.
 * Logic: Use existing step types and rounded distance-to-next; endpoint-ahead is never arrival.
 * Constraints: No new distance trigger, route planning, translation service or safe-path claim. */
export function navigationSpeechText(snapshot, language) {
  const zh = languageOf(language) === "zh-CN", index = zh ? 0 : 1;
  const notice = snapshot?.state === "locating" && snapshot.reason === "accuracy_insufficient"
    ? "accuracy_insufficient" : snapshot?.state;
  if (NOTICE_TEXT[notice]) return NOTICE_TEXT[notice][index];
  if (snapshot?.state !== "navigating") return "";
  const {current_step: current, next_step: next, distance_to_next_m: meters} = snapshot.progress || {};
  if (!current || !Number.isInteger(current.type) || !EN_TURNS[current.type] || current.type === 10 ||
      !Number.isFinite(meters) || meters < 0 ||
      next && (!Number.isInteger(next.type) || !EN_TURNS[next.type])) return "";
  const currentText = zh ? current.type === 11 ? "开始沿规划路线行走。" : `${turnLabel(current.type)}。`
    : `${EN_TURNS[current.type]}.`;
  if (!next) return currentText;
  const tiny = meters > 0 && Math.round(meters) === 0;
  if (zh) {
    const distance = tiny ? "不足1米" : `约${Math.round(meters)}米`;
    return currentText + (next.type === 10 ? `前方${distance}为路线终点。`
      : `沿当前路段行进，${distance}后${turnLabel(next.type)}。`);
  }
  const distance = tiny ? "less than 1 meter" : `about ${Math.round(meters)} meters`;
  return currentText + (next.type === 10 ? ` The route endpoint is ${distance} ahead.`
    : ` Continue along this section. In ${distance}, ${EN_TURNS[next.type].toLowerCase()}.`);
}

/* Functionality: Adapt navigation snapshots to the existing local speech driver.
 * Logic: Speak each semantic transition once; retain only the latest unsent candidate while
 * another owner is busy, and invalidate obsolete owned speech before accepting new guidance.
 * Constraints: Disabled initially; no automatic error recovery, default voices or sensor access. */
export class NavigationSpeechAdapter {
  /* Functionality: Bind speech lifecycle and injectable clocks without starting output.
   * Inputs: speech driver, epoch clock and optional timer functions. Outputs: Disabled adapter.
   * Logic: Own bounded state and one event subscription; timer wrappers retain native receivers.
   * Constraints: GPS timestamps use Date.now units; hardware audibility is never inferred. */
  constructor({speech, now = Date.now,
    setTimer = (callback, delay) => globalThis.setTimeout(callback, delay),
    clearTimer = timer => globalThis.clearTimeout(timer)} = {}) {
    Object.assign(this, {speech, now, setTimer, clearTimer});
    this.events = new EventBus();
    this.enabled = false;
    this.paused = false;
    this.reason = null;
    this.voiceId = "";
    this.language = "zh-CN";
    this.runId = null;
    this.generation = 0;
    this.revision = 0;
    this.latest = this.candidate = this.semanticKey = null;
    this.active = this.pendingSubmission = this.sourceTimer = null;
    this.consumed = this.pumping = this.suspended = this.disposed = false;
    this.lastSuppression = null;
    this.unsubscribe = speech?.subscribe(event => this.outputEvent(event)) || (() => {});
  }

  /* Functionality: Observe non-body integration lifecycle. Inputs: Listener function.
   * Outputs: Unsubscribe callback. Logic: Delegate to the local event bus.
   * Constraints: No event replay, instruction text or coordinates. */
  subscribe(listener) { return this.events.subscribe(listener); }

  /* Functionality: Read detached adapter state without exposing route or spoken content.
   * Inputs: Instance state. Outputs: JSON-compatible metadata.
   * Logic: Separate latest unsent work, native request ownership and error latch.
   * Constraints: Request acceptance/start/completion does not verify audible output. */
  describe() {
    return {enabled: this.enabled, paused: this.paused, reason: this.reason,
      run_id: this.runId, generation: this.generation, navigation_state: this.latest?.state ?? null,
      pending: Boolean(this.candidate && !this.consumed),
      active_request_id: this.active?.requestId ?? null, active_started: this.active?.started === true,
      audio_output_verified: false};
  }

  /* Functionality: Publish diagnostic transitions without route contents.
   * Inputs: Phase and bounded machine details. Outputs: Subscriber event and diagnostic log.
   * Logic: Include run, output request and current configuration state.
   * Constraints: Never include GPS coordinates, external instruction text or speech body. */
  report(phase, details = {}) {
    const event = {phase, run_id: this.runId, generation: this.generation,
      enabled: this.enabled, paused: this.paused, reason: this.reason, at: this.now(), ...details};
    console.info("[PRS navigation speech] State changed", event);
    this.events.publish(event);
  }

  /* Functionality: Store an explicit voice selection without selecting a replacement.
   * Inputs: voiceId and language. Outputs: Adapter snapshot.
   * Logic: Normalize only the supported language; validate live voice identity at submission.
   * Constraints: Does not enable output, resume errors or replay a completed instruction. */
  configure({voiceId, language}) {
    this.voiceId = voiceId;
    this.language = languageOf(language);
    return this.describe();
  }

  /* Functionality: Apply the separate user navigation-speech switch.
   * Inputs: Boolean. Outputs: Adapter snapshot and owned cancellation when disabled.
   * Logic: Disabling clears input/timer/ownership but retains the explicit current run ID.
   * Constraints: Never enables the shared speech driver or clears an error latch. */
  setEnabled(enabled) {
    if (this.disposed) return this.describe();
    if (enabled !== true) {
      const runId = this.runId;
      this.enabled = false;
      this.stop("disabled");
      this.runId = runId;
    } else {
      this.enabled = true;
      this.report("enabled");
      this.pump();
    }
    return this.describe();
  }

  /* Functionality: Begin an explicit independently identified navigation run.
   * Inputs: Nonempty string runId. Outputs: Empty-run snapshot.
   * Logic: Invalidate prior work before assigning the run; route request IDs are not sessions.
   * Constraints: Retains user switches, voice configuration and manual-recovery error latch. */
  begin(runId) {
    if (typeof runId !== "string" || !runId.trim()) throw new TypeError("Navigation run ID is required");
    if (this.disposed) return this.describe();
    this.stop("new_run");
    this.runId = runId;
    this.report("run");
    return this.describe();
  }

  /* Functionality: Check the existing navigation GPS deadline against the source timestamp.
   * Inputs: Snapshot position. Outputs: Boolean.
   * Logic: Require finite nonfuture age strictly below the core freshness boundary.
   * Constraints: Receipt time and a cached age_ms never refresh an old GPS observation. */
  fresh(snapshot) {
    const timestamp = snapshot?.position?.timestamp;
    const age = this.now() - timestamp;
    return Number.isFinite(timestamp) && Number.isFinite(age) && age >= 0 &&
      age < NAVIGATION_POLICY.max_fix_age_ms;
  }

  /* Functionality: Select one eligible semantic instruction from current core state.
   * Inputs: Latest snapshot. Outputs: Candidate with key/kind/step_index or null.
   * Logic: Respect core activity, accuracy and arrival gates without projecting another route.
   * Constraints: Error notices need no GPS; all position-dependent notices require fresh fixes. */
  selectCandidate(snapshot) {
    if (!snapshot) return null;
    if (snapshot.state === "error") return {key: `error/${snapshot.reason || "unknown"}`, kind: "error", step_index: null};
    if (!this.fresh(snapshot)) return null;
    const accuracy = snapshot.position?.accuracy_m;
    if (!Number.isFinite(accuracy) || accuracy < 0) return null;
    if (snapshot.active === true && snapshot.state === "locating" && snapshot.reason === "accuracy_insufficient")
      return {key: "accuracy_insufficient", kind: "accuracy_insufficient", step_index: null};
    if (accuracy > NAVIGATION_POLICY.max_accuracy_m) return null;
    if (snapshot.state === "arrived" && accuracy <= NAVIGATION_POLICY.arrival_m)
      return {key: "arrived", kind: "arrived", step_index: null};
    if (snapshot.active !== true) return null;
    if (snapshot.state === "off_route") return {key: "off_route", kind: "off_route", step_index: null};
    const step = snapshot.progress?.step_index;
    if (snapshot.state !== "navigating" || !Number.isInteger(step) || step < 0 ||
        !navigationSpeechText(snapshot, this.language)) return null;
    return {key: `step/${step}`, kind: "turn", step_index: step};
  }

  /* Functionality: Replace candidate and enforce its original GPS source deadline.
   * Inputs: Latest snapshot and epoch clock. Outputs: Updated candidate and bounded timer.
   * Logic: Semantic transitions invalidate older output; an unstarted utterance retains its
   * original GPS deadline, while started speech follows latest same-step GPS freshness.
   * Constraints: The timer terminates stale work; it is neither a retry nor a new distance trigger. */
  refresh() {
    if (this.sourceTimer !== null) this.clearTimer(this.sourceTimer);
    this.sourceTimer = null;
    const candidate = this.runId && this.latest ? this.selectCandidate(this.latest) : null;
    const key = candidate?.key ?? null;
    if (key !== this.semanticKey) {
      this.revision++;
      this.semanticKey = key;
      this.consumed = false;
      this.lastSuppression = null;
      this.cancelOwned("source_changed");
      this.report("source_changed", {kind: candidate?.kind ?? null, step_index: candidate?.step_index ?? null});
    }
    this.candidate = candidate && {...candidate, revision: this.revision, snapshot: this.latest};
    if (this.active && this.active.kind !== "error" && !this.active.started &&
        !this.active.cancelFailed && !this.fresh(this.active.snapshot)) {
      this.cancelOwned("source_expired_before_start");
      this.consumed = true;
      this.suppress("source_expired_before_start");
    }
    if (candidate && candidate.kind !== "error" && this.enabled) {
      const generation = this.generation;
      const timestamp = this.active && !this.active.started && !this.active.cancelFailed
        ? Math.min(this.latest.position.timestamp, this.active.snapshot.position.timestamp)
        : this.latest.position.timestamp;
      this.sourceTimer = this.setTimer(() => {
        if (generation !== this.generation || this.disposed) return;
        this.sourceTimer = null;
        this.pump();
      }, Math.max(0, NAVIGATION_POLICY.max_fix_age_ms - (this.now() - timestamp)));
    }
  }

  /* Functionality: Consume a detached core snapshot without changing navigation state.
   * Inputs: Controller snapshot. Outputs: Adapter snapshot and eligible output attempt.
   * Logic: Store only the latest input within an explicit run; pump applies quality gates.
   * Constraints: No run means input is discarded; no route request, GPS call or implicit start. */
  ingest(snapshot) {
    if (!this.runId || this.disposed) return this.describe();
    this.latest = structuredClone(snapshot);
    this.pump();
    return this.describe();
  }

  /* Functionality: Report one suppression per reason and semantic revision.
   * Inputs: Stable reason. Outputs: At most one diagnostic event for repeated GPS updates.
   * Logic: Bound repeated device-busy/prerequisite messages.
   * Constraints: Suppression does not submit requests or change user settings. */
  suppress(reason) {
    const key = `${reason}/${this.revision}`;
    if (key === this.lastSuppression) return;
    this.lastSuppression = key;
    this.report("suppressed", {reason, kind: this.candidate?.kind ?? null,
      step_index: this.candidate?.step_index ?? null});
  }

  /* Functionality: Latch output failure until explicit user recovery.
   * Inputs: Stable machine reason. Outputs: Paused adapter.
   * Logic: Keep the latest input available for explicit resume after checks.
   * Constraints: No timer, new GPS fix or voice-list update clears the error. */
  pause(reason) {
    this.paused = true;
    this.reason = reason;
    this.report("paused");
  }

  /* Functionality: Cancel only this adapter's current native request.
   * Inputs: Diagnostic reason and explicitStop flag. Outputs: Accepted cancellation or paused failure.
   * Logic: Detach before native cancellation so synchronous events cannot affect new work.
   * Constraints: A failed cancellation retains ownership for a later explicit stop, never retries. */
  cancelOwned(reason, explicitStop = false) {
    const active = this.active;
    if (!active || active.cancelFailed && !explicitStop) return;
    this.active = null;
    const wasSuspended = this.suspended;
    this.suspended = true;
    let result;
    try { result = this.speech?.stopOwned(active.requestId); }
    catch { result = {accepted: false, reason: "cancel_failed"}; }
    finally { this.suspended = wasSuspended; }
    this.report("output_cancel", {request_id: active.requestId, kind: active.kind,
      step_index: active.step_index, accepted: result?.accepted === true, reason});
    if (result?.accepted !== true && result?.reason !== "not_owned") {
      active.cancelFailed = true;
      this.active = active;
      this.pause(result?.reason || "cancel_failed");
    }
  }

  /* Functionality: Submit at most one fresh latest instruction through existing speech output.
   * Inputs: Current candidate, explicit voice, driver state and user rate/volume controls.
   * Outputs: Owned native request or diagnosed suppression/failure.
   * Logic: Do not preempt foreign output; bind synchronous native events before speak returns.
   * Constraints: No FIFO, automatic failed-request retry, voice substitution or shared enablement. */
  pump() {
    if (this.disposed || this.suspended || this.pumping) return;
    this.pumping = true;
    try {
      this.refresh();
      if (!this.enabled || !this.runId || !this.candidate || this.consumed || this.active) return;
      if (this.paused) return this.suppress("paused");
      const status = this.speech?.describe();
      if (!status?.supported || !status.enabled) return this.suppress("speech_disabled_or_unavailable");
      if (status.visible === false) return this.suppress("page_hidden");
      if (status.user_activated === false) return this.suppress("activation_required");
      if (status.active_request || status.engine_speaking || status.engine_pending || status.engine_paused)
        return this.suppress("speech_busy");
      if (typeof this.voiceId !== "string" || !this.voiceId) return this.suppress("explicit_local_voice_required");
      const inventory = this.speech.voiceList();
      const matches = inventory.voices?.filter(voice => voice.id === this.voiceId) || [];
      if (!inventory.accepted || matches.length !== 1 || matches[0].local_service !== true ||
          typeof matches[0].language !== "string" ||
          matches[0].language.split("-")[0].toLowerCase() !== this.language.split("-")[0].toLowerCase())
        return this.pause("local_voice_unavailable");
      const candidate = this.candidate;
      this.pendingSubmission = {...candidate, generation: this.generation, requestId: null, started: false};
      this.consumed = true;
      let result;
      try {
        result = this.speech.speak(navigationSpeechText(candidate.snapshot, this.language), {
          language: this.language, voice_id: this.voiceId,
          rate: Number(this.speech.rate?.value ?? 2), volume: Number(this.speech.volume?.value ?? 1)});
      } catch { result = {accepted: false, reason: "speak_failed"}; }
      const pending = this.pendingSubmission;
      this.pendingSubmission = null;
      if (!result?.accepted) {
        this.consumed = false;
        this.pause(result?.reason || "speak_failed");
        return;
      }
      if (this.paused) return;
      if (!pending.finished && !this.active)
        this.active = {...pending, requestId: result.last_request.id};
      this.lastSuppression = null;
      this.report("output_submitted", {request_id: result.last_request.id,
        kind: candidate.kind, step_index: candidate.step_index, actual_start: pending.started});
    } finally { this.pumping = false; }
  }

  /* Functionality: Reconcile native lifecycle while preserving output ownership.
   * Inputs: Existing speech-driver non-body event. Outputs: Start/end/cancel/error state.
   * Logic: Bind pending events; a failed shared-driver cancellation retains ownership because its
   * native utterance remains current. Foreign completion may release a fresh unsent candidate.
   * Constraints: Old request IDs never complete another request; errors/cancel require manual resume. */
  outputEvent(event) {
    if (this.disposed || this.suspended || event.channel !== "speech") return;
    let active = this.active;
    if (!active && this.pendingSubmission && ["submitted", "start", "error", "end"].includes(event.phase)) {
      active = this.pendingSubmission;
      active.requestId = event.request_id;
      this.active = active;
    }
    if (!active || active.requestId !== event.request_id) {
      if (event.phase === "end" || event.phase === "cancel") this.pump();
      return;
    }
    if (event.phase === "start") {
      active.started = true;
      this.report("output_start", {request_id: active.requestId, kind: active.kind,
        step_index: active.step_index, actual_start: true, hardware_verified: false});
      this.pump();
    } else if (["end", "error", "cancel"].includes(event.phase)) {
      if (event.phase === "error" && event.reason === "cancel_failed") active.cancelFailed = true;
      else {
        this.active = null;
        active.finished = true;
      }
      this.report(`output_${event.phase}`, {request_id: active.requestId, kind: active.kind,
        step_index: active.step_index, reason: event.reason ?? null});
      if (event.phase !== "end") {
        this.consumed = false;
        this.pause(event.reason || (event.phase === "cancel" ? "cancelled" : "output_error"));
      } else this.pump();
    }
  }

  /* Functionality: Recover only through an explicit user action.
   * Inputs: Current latest snapshot and error latch. Outputs: Adapter snapshot/output attempt.
   * Logic: Clear the latch then re-evaluate source validity and live device prerequisites.
   * Constraints: Never starts navigation, enables devices or repeats expired instructions;
   * unconfirmed cancellation still requires an explicit successful stop before recovery. */
  resume() {
    if (this.disposed) return this.describe();
    if (this.active?.cancelFailed) return this.describe();
    this.paused = false;
    this.reason = null;
    this.report("resumed");
    this.pump();
    return this.describe();
  }

  /* Functionality: Invalidate this run and cancel bounded owned work.
   * Inputs: Diagnostic reason, default stop. Outputs: Empty-run snapshot or latched cancellation failure.
   * Logic: Clear source and timers before owned cancellation, then advance the generation.
   * Constraints: Never stops another owner or clears a previously latched output failure. */
  stop(reason = "stop") {
    if (this.sourceTimer !== null) this.clearTimer(this.sourceTimer);
    this.sourceTimer = null;
    this.latest = this.candidate = this.semanticKey = null;
    this.consumed = false;
    this.lastSuppression = null;
    this.cancelOwned(reason, true);
    this.pendingSubmission = null;
    this.generation++;
    this.revision++;
    this.runId = null;
    this.report("stopped", {stop_reason: reason});
    return this.describe();
  }

  /* Functionality: Release this adapter's subscription and owned lifecycle.
   * Inputs: Current run. Outputs: Disposed snapshot.
   * Logic: Stop before unsubscribing so cancellation failures remain diagnosed.
   * Constraints: Disposal never resumes or retries output, and later input cannot start speech. */
  dispose() {
    if (!this.disposed) {
      this.stop("disposed");
      this.unsubscribe();
      this.disposed = true;
    }
    return this.describe();
  }
}
