/** Responsibilities: Independently schedule risk and demo navigation using existing device controllers.
 * Implementation: latest snapshot, 1s capture TTL, per-session/track start-based 3s cooldown;
 * priority/distance/track ordering, owned preemption, foreign-pending vibration admission guard,
 * one pending navigation valid for10s.
 * Related Modules: events.js, catalog.js, local-mvp.js; camera/speech-output/vibration subscription seams.
 * Declaration Index: selectRisk; GuidanceManager; GuidanceManager.constructor; GuidanceManager.subscribe;
 * GuidanceManager.report; GuidanceManager.setEnabled; GuidanceManager.setVoice; GuidanceManager.resume;
 * GuidanceManager.begin; GuidanceManager.ingest; GuidanceManager.fresh; GuidanceManager.navigationEvent;
 * GuidanceManager.outputEvent; GuidanceManager.pause; GuidanceManager.cancel; GuidanceManager.eligible;
 * GuidanceManager.riskDescriptor; GuidanceManager.context; GuidanceManager.suppress;
 * GuidanceManager.submitSpeech; GuidanceManager.submitVibration; GuidanceManager.pump;
 * GuidanceManager.stop; GuidanceManager.dispose.
 * Variable Index: PERCEPTION_TTL_MS/COOLDOWN_MS/NAVIGATION_TTL_MS are output policies, not algorithm parameters;
 * PATTERNS maps existing priority to existing fixed device patterns; instance latest/navigation are non-FIFO;
 * history stores at most300 track start records; trackingEpoch prevents ID reuse across tracker resets;
 * active/pendingSubmission preserve request ownership and frozen source frame context;
 * riskTimer cancels only unstarted own speech at its original source deadline; lastSuppression bounds repeated diagnostic state;
 * paused channels require explicit resume; timers/unsubscribers own bounded lifecycles, events omit text/images.
 */
import {EventBus} from "./events.js";
import {languageOf, riskText} from "./catalog.js";

export const PERCEPTION_TTL_MS = 1000;
export const COOLDOWN_MS = 3000;
export const NAVIGATION_TTL_MS = 10000;
const PATTERNS = {2: {name: "short", duration: 200}, 3: {name: "double", duration: 400},
  4: {name: "long", duration: 800}, 1: {name: "short", duration: 200}};

/** Functionality: Select one server-required alert. Inputs: validated boxes. Outputs: selected box or null.
 * Logic: priority descending, current distance ascending, track ID ascending.
 * Constraints: no invented alerts, re-scoring, threshold changes or mutation of algorithm output. */
export function selectRisk(boxes) {
  return [...boxes].filter(box => box.alert_required === true && box.alert_priority > 0)
    .sort((a, b) => b.alert_priority - a.alert_priority ||
      (a.distance_m ?? Infinity) - (b.distance_m ?? Infinity) || a.track_id - b.track_id)[0] || null;
}

/** Functionality: Schedule software output without changing perception algorithms or device drivers.
 * Logic: synchronous subscription bridges immutable snapshots and native request events.
 * Constraints: default disabled, local explicit voices, no FIFO/retries/remote fallback, no manual/scene cancellation. */
export class GuidanceManager {
  /** Functionality: Bind existing controllers. Inputs: speech/vibration, injectable clock/timers.
   * Outputs: disabled manager and event subscriptions. Logic: bounded state; default timer wrappers retain the Window/global receiver.
   * Constraints: device acceptance does not establish audible output or physical vibration. */
  constructor({speech, vibration, now = () => performance.now(),
    setTimer = (callback, delay) => globalThis.setTimeout(callback, delay),
    clearTimer = timer => globalThis.clearTimeout(timer)}) {
    Object.assign(this, {speech, vibration, now, setTimer, clearTimer});
    this.events = new EventBus();
    this.enabled = false;
    this.voiceId = "";
    this.language = "zh-CN";
    this.sessionId = null;
    this.latest = this.navigation = null;
    this.history = new Map();
    this.trackingEpoch = null;
    this.active = {speech: null, vibration: null};
    this.paused = {speech: false, vibration: false};
    this.reasons = {speech: null, vibration: null};
    this.pendingSubmission = null;
    this.perceptionTimer = this.navigationTimer = this.vibrationTimer = this.riskTimer = null;
    this.lastSuppression = null;
    this.suspended = false;
    this.pumping = false;
    this.unsubscribers = [speech?.subscribe(event => this.outputEvent(event)),
      vibration?.subscribe(event => this.outputEvent(event))].filter(Boolean);
  }
  /** Functionality: Observe machine-readable state. Inputs: listener. Outputs: unsubscribe callback.
   * Logic: delegate to local stream; Constraints: no DOM interpretation or output replay. */
  subscribe(listener) { return this.events.subscribe(listener); }
  /** Functionality: Publish diagnosed guidance state. Inputs: phase/details. Outputs: event.
   * Logic: include session/paused/ownership state; Constraints: omit frame bytes, navigation and speech bodies. */
  report(phase, details = {}) {
    this.events.publish({phase, session_id: this.sessionId, enabled: this.enabled,
      paused: {...this.paused}, reasons: {...this.reasons}, at: this.now(), ...details});
  }
  /** Functionality: Bind risk input to the request being submitted. Inputs: selected validated box.
   * Outputs: source descriptor. Logic: copy session/frame/capture/epoch rather than consulting a later snapshot.
   * Constraints: no speech body or images; this is observation context, not a changed algorithm result. */
  riskDescriptor(box) {
    return {kind: "risk", box, source: Object.freeze({session_id: this.sessionId, frame_index: this.latest.metadata.index,
      captured_at_ms: this.latest.capturedAt, tracking_epoch: this.latest.metadata.tracking_epoch ?? null})};
  }
  /** Functionality: Extract non-body request context. Inputs: descriptor. Outputs: safe event metadata.
   * Logic: retain original risk source/identity or demo source, never current latest state.
   * Constraints: absent context is explicit null, not inferred capture or actual output start. */
  context(descriptor) {
    return {kind: descriptor?.kind ?? null, ...descriptor?.source,
      source: descriptor?.kind === "risk" ? "perception" : descriptor?.source?.source ?? null,
      track_id: descriptor?.box?.track_id ?? null,
      alert_priority: descriptor?.box?.alert_priority ?? null};
  }
  /** Functionality: Report a suppression state once per reason/target/channel transition.
   * Inputs: reason/descriptor/channel. Outputs: bounded diagnostic event.
   * Logic: stable key avoids per-frame repeated device-prerequisite logs; Constraints: no FIFO/retry or fabricated start timestamp. */
  suppress(reason, descriptor, channel = null) {
    const key = `${reason}/${channel}/${descriptor?.kind}/${descriptor?.box?.track_id}/${descriptor?.box?.alert_priority}`;
    if (key === this.lastSuppression) return;
    this.lastSuppression = key;
    this.report("suppressed", {reason, channel, ...this.context(descriptor), actual_start: false});
  }
  /** Functionality: Apply the explicit guidance switch. Inputs: boolean. Outputs: state/cancellation.
   * Logic: disabling clears owned work; enabling waits for fresh data, never enables device switches.
   * Constraints: no automatic channel recovery or persistence. */
  setEnabled(enabled) {
    this.enabled = enabled === true;
    if (!this.enabled) { const sessionId = this.sessionId; this.stop("disabled"); this.sessionId = sessionId; }
    this.report(this.enabled ? "enabled" : "disabled");
  }
  /** Functionality: Store explicit local voice selection. Inputs: voiceId/language. Outputs: configuration.
   * Logic: validation occurs against live native inventory at submission; Constraints: no auto replacement/recovery. */
  setVoice(voiceId, language) { this.voiceId = voiceId; this.language = languageOf(language); }
  /** Functionality: Resume one channel at the user's request. Inputs: speech/vibration. Outputs: unpaused state.
   * Logic: clear only that error latch and consume the latest eligible state.
   * Constraints: never turn on the device switch or replay an expired frame/navigation event. */
  resume(channel) {
    if (!Object.hasOwn(this.paused, channel)) throw new Error("Unknown guidance channel");
    this.paused[channel] = false;
    this.reasons[channel] = null;
    this.report("resumed", {channel});
    this.pump();
  }
  /** Functionality: Begin an explicit camera generation. Inputs: sessionId. Outputs: fresh bounded state.
   * Logic: cancel own previous work and reset per-track records; Constraints: retain explicit switches and error latches. */
  begin(sessionId) { this.stop("new_session"); this.sessionId = sessionId; this.report("session"); }
  /** Functionality: Consume a validated result. Inputs: {sessionId,capturedAt,metadata}.
   * Outputs: latest snapshot, TTL timer and eligible guidance. Logic: age begins at source capture.
   * Constraints: stale frames still publish for display/performance, but never create risk reminders. */
  ingest({sessionId, capturedAt, metadata}) {
    if (sessionId !== this.sessionId) {
      if (this.sessionId) this.report("wrong_session", {received_session_id: sessionId, frame_index: metadata.index});
      return;
    }
    if (metadata.tracking_epoch !== this.trackingEpoch) {
      this.history.clear(); this.trackingEpoch = metadata.tracking_epoch;
    }
    this.latest = {capturedAt, metadata};
    this.clearTimer(this.perceptionTimer);
    const age = this.now() - capturedAt;
    if (!Number.isFinite(age) || age < 0 || age >= PERCEPTION_TTL_MS) {
      this.report("stale", {frame_index: metadata.index, age_ms: age});
      this.pump();
      return;
    }
    this.report("frame", {frame_index: metadata.index, age_ms: age});
    this.perceptionTimer = this.setTimer(() => { this.report("stale", {frame_index: metadata.index}); this.pump(); }, PERCEPTION_TTL_MS - age);
    this.pump();
  }
  /** Functionality: Determine source freshness. Inputs: latest/current monotonic time. Outputs: boolean.
   * Logic: finite nonnegative age below1s; Constraints: receipt/decode time never refreshes old data. */
  fresh() {
    const age = this.latest ? this.now() - this.latest.capturedAt : Infinity;
    return Number.isFinite(age) && age >= 0 && age < PERCEPTION_TTL_MS;
  }
  /** Functionality: Accept an explicitly labeled demo navigation event. Inputs: text/language/source.
   * Outputs: acknowledgement and one replaceable pending instruction. Logic: original10s expiry is preserved across interruption.
   * Constraints: no GPS/routes, FIFO, automatic navigation, or speech body in logs. */
  navigationEvent({text, language, source}) {
    if (!this.enabled || !this.sessionId) return {accepted: false, reason: "disabled_or_no_session"};
    if (source !== "demo" || typeof text !== "string" || !text.trim() || [...text].length > 1000)
      return {accepted: false, reason: "invalid_navigation"};
    this.navigation = {id: Symbol("navigation"), text, language: languageOf(language), source,
      expiresAt: this.now() + NAVIGATION_TTL_MS};
    this.clearTimer(this.navigationTimer);
    this.navigationTimer = this.setTimer(() => {
      this.navigation = null;
      if (this.active.speech?.kind === "navigation" && !this.active.speech.started) this.cancel("speech");
      this.report("navigation_expired", {source: "demo"});
    }, NAVIGATION_TTL_MS);
    this.report("navigation_pending", {source: "demo"});
    this.pump();
    return {accepted: true, source: "demo"};
  }
  /** Functionality: Observe actual output/request lifecycle. Inputs: controller event.
   * Outputs: cooldown, completion, paused channel or next eligible latest work.
   * Logic: bind synchronous submitted/start events to the pending request; ignore other request IDs.
   * Constraints: manual/scene outputs remain independent; vibration start means API acceptance only. */
  outputEvent(event) {
    const channel = event.channel;
    let active = this.active[channel];
    if (!active && this.pendingSubmission?.channel === channel && ["submitted", "start", "error"].includes(event.phase)) {
      active = this.pendingSubmission;
      active.requestId = event.request_id;
      this.active[channel] = active;
    }
    if (!active || active.requestId !== event.request_id) {
      if (event.phase === "end" || event.phase === "cancel") this.pump();
      return;
    }
    if (event.phase === "start") {
      active.started = true;
      if (channel === "speech") { this.clearTimer(this.riskTimer); this.riskTimer = null; }
      if (active.kind === "risk") {
        if (!this.history.has(active.box.track_id) && this.history.size >= 300) this.history.delete(this.history.keys().next().value);
        this.history.set(active.box.track_id, {priority: active.box.alert_priority, at: event.at});
      }
      this.lastSuppression = null;
      this.report("output_start", {channel, request_id: active.requestId, ...this.context(active),
        capture_to_start_ms: active.kind === "risk" ? event.at - active.source.captured_at_ms : null,
        started_at_ms: event.at, actual_start: true, hardware_verified: false});
    } else if (event.phase === "error") {
      this.active[channel] = null;
      if (channel === "speech") { this.clearTimer(this.riskTimer); this.riskTimer = null; }
      this.pause(channel, event.reason || "output_error", active);
    } else if (event.phase === "end" || event.phase === "cancel") {
      this.active[channel] = null;
      if (channel === "speech") { this.clearTimer(this.riskTimer); this.riskTimer = null; }
      if (active.kind === "navigation" && this.navigation?.id === active.navigationId) this.navigation = null;
      this.report("output_" + event.phase, {channel, request_id: active.requestId, ...this.context(active)});
      // A user/device cancellation is not a new perception event and must not resubmit an unstarted request.
      if (event.phase === "cancel") this.pause(channel, "cancelled");
      this.pump();
    }
  }
  /** Functionality: Latch a channel failure. Inputs: channel/reason/optional original descriptor. Outputs: paused state.
   * Logic: retain reason until explicit resume; Constraints: no timed retry or alternate-channel activation. */
  pause(channel, reason, descriptor = null) {
    this.paused[channel] = true; this.reasons[channel] = reason;
    this.report("paused", {channel, reason, ...this.context(descriptor), request_id: descriptor?.requestId ?? null});
  }
  /** Functionality: Cancel this manager's current request. Inputs: channel. Outputs: ownership cleanup/diagnosis.
   * Logic: detach before cancellation to isolate synchronous callbacks; stopOwned verifies current identity.
   * Constraints: not-owned is harmless; cancellation failure pauses the channel, never retries. */
  cancel(channel) {
    const active = this.active[channel];
    if (!active) return;
    this.active[channel] = null;
    if (channel === "speech") { this.clearTimer(this.riskTimer); this.riskTimer = null; }
    if (channel === "vibration") { this.clearTimer(this.vibrationTimer); this.vibrationTimer = null; }
    const wasSuspended = this.suspended;
    let result;
    this.suspended = true;
    try { result = this[channel]?.stopOwned(active.requestId); }
    finally { this.suspended = wasSuspended; }
    this.report("output_cancel", {channel, request_id: active.requestId, ...this.context(active),
      accepted: channel === "speech" ? result?.accepted === true : result === true,
      reason: channel === "speech" ? result?.reason : result === true ? null : "not_cancelled"});
    if (channel === "speech" && result?.accepted === false && result.reason !== "not_owned") this.pause(channel, result.reason, active);
    if (channel === "vibration" && result === false && this.vibration.describe().request_id === active.requestId)
      this.pause(channel, "cancel_failed", active);
  }
  /** Functionality: Apply only output cooldown. Inputs: selected box. Outputs: eligibility.
   * Logic: first request/upgrades immediate, equal level needs3s since actual start, downgrades do not generate new alerts.
   * Constraints: never alter server alert_required/priority, risk scores or parameters. */
  eligible(box) {
    const previous = this.history.get(box.track_id);
    return !previous || box.alert_priority > previous.priority ||
      box.alert_priority === previous.priority && this.now() - previous.at >= COOLDOWN_MS;
  }
  /** Functionality: Submit one owned local utterance. Inputs: active descriptor/text/language.
   * Outputs: active request or paused channel. Logic: inspect live voice and busy state, then call shared driver once.
   * Constraints: no default/remote voice, FIFO, silent voice substitution or manual/scene preemption. */
  submitSpeech(descriptor, text, language) {
    const status = this.speech?.describe();
    if (!status?.enabled || !status.supported || this.paused.speech || !this.voiceId ||
        status.active_request || status.engine_speaking || status.engine_pending || status.engine_paused)
      return this.suppress(this.paused.speech ? "channel_paused" : !this.voiceId ? "explicit_local_voice_required"
        : !status?.enabled ? "channel_disabled" : "device_busy_or_unavailable", descriptor, "speech");
    const inventory = this.speech.voiceList(), matches = inventory.voices?.filter(voice => voice.id === this.voiceId) || [];
    if (!inventory.accepted || matches.length !== 1 || matches[0].local_service !== true ||
        matches[0].language.split("-")[0].toLowerCase() !== language.split("-")[0].toLowerCase())
      return this.pause("speech", "local_voice_unavailable", descriptor);
    this.pendingSubmission = {...descriptor, channel: "speech", started: false};
    const result = this.speech.speak(text, {language, voice_id: this.voiceId,
      rate: Number(this.speech.rate?.value ?? 1), volume: Number(this.speech.volume?.value ?? 1)});
    const pending = this.pendingSubmission;
    this.pendingSubmission = null;
    if (!result.accepted) { this.active.speech = null; this.pause("speech", result.reason, pending); return; }
    if (this.paused.speech) return;
    if (!this.active.speech) this.active.speech = {...pending, requestId: result.last_request.id};
    const active = this.active.speech;
    if (active.kind === "risk" && !active.started) {
      this.riskTimer = this.setTimer(() => {
        if (this.active.speech?.requestId !== active.requestId || this.active.speech.started) return;
        this.cancel("speech"); this.suppress("source_expired_before_start", active, "speech"); this.pump();
      }, Math.max(0, PERCEPTION_TTL_MS - (this.now() - active.source.captured_at_ms)));
    }
    this.lastSuppression = null;
    this.report("output_submitted", {channel: "speech", request_id: result.last_request.id, ...this.context(descriptor),
      accepted: true, actual_start: active.started});
  }
  /** Functionality: Submit an explicitly enabled fixed vibration pattern. Inputs: frozen-source selected risk descriptor.
   * Outputs: accepted request identity and nominal-duration completion event.
   * Logic: verify pending driver identity before submitting; estimate only own pattern elapsed, never hardware completion.
   * Constraints: never replace another owner's pending request; no default enablement, retry or physical motor claims. */
  submitVibration(descriptor) {
    const box = descriptor.box;
    const status = this.vibration?.describe();
    if (!status?.enabled || !status.supported || this.paused.vibration)
      return this.suppress(this.paused.vibration ? "channel_paused" : "channel_disabled_or_unavailable", descriptor, "vibration");
    if (status.request_pending && status.request_id !== this.active.vibration?.requestId)
      return this.suppress("foreign_request_pending", descriptor, "vibration");
    const pattern = PATTERNS[box.alert_priority];
    this.pendingSubmission = {...descriptor, channel: "vibration", started: false};
    const result = this.vibration.testPattern(pattern.name), pending = this.pendingSubmission;
    this.pendingSubmission = null;
    if (!result.accepted) { this.active.vibration = null; this.pause("vibration", result.reason, pending); return; }
    if (!this.active.vibration) this.active.vibration = {...pending, requestId: result.request_id};
    const requestId = result.request_id;
    this.report("output_submitted", {channel: "vibration", request_id: requestId, ...this.context(descriptor), accepted: true,
      actual_start: this.active.vibration.started, hardware_verified: false});
    this.clearTimer(this.vibrationTimer);
    this.vibrationTimer = this.setTimer(() => {
      if (this.active.vibration?.requestId !== requestId) return;
      // End is the nominal pattern interval, not a motor observation. Keep the driver cancellable.
      this.active.vibration.elapsed = true;
      this.report("pattern_elapsed", {channel: "vibration", request_id: requestId, hardware_verified: false});
    }, pattern.duration);
  }
  /** Functionality: Consume latest eligible risk or unexpired navigation. Inputs: manager state.
   * Outputs: one target per snapshot, owned output actions. Logic: reentrancy guard protects synchronous native callbacks.
   * Constraints: no frame FIFO, retries, hidden output enablement or cancellation of unrelated requests. */
  pump() {
    if (!this.enabled || !this.sessionId || this.suspended || this.pumping) return;
    this.pumping = true;
    try {
      const box = this.fresh() ? selectRisk(this.latest.metadata.boxes) : null;
      for (const channel of ["speech", "vibration"]) {
        const active = this.active[channel];
        if (active && (!box && active.kind === "risk" || box &&
            (active.kind === "navigation" || box.alert_priority > active.box.alert_priority))) this.cancel(channel);
      }
      if (box) {
        const descriptor = this.riskDescriptor(box);
        if (!this.eligible(box)) return this.suppress("cooldown_or_downgrade", descriptor);
        if (!this.active.speech) this.submitSpeech(descriptor, riskText(box, this.language), this.language);
        if (!this.active.vibration || this.active.vibration.elapsed) {
          if (this.active.vibration) this.cancel("vibration");
          this.submitVibration(descriptor);
        }
      } else if (!this.active.speech && this.navigation && this.navigation.expiresAt > this.now()) {
        this.submitSpeech({kind: "navigation", navigationId: this.navigation.id, source: {source: "demo"}}, this.navigation.text, this.navigation.language);
      }
    } finally { this.pumping = false; }
  }
  /** Functionality: Clear session-owned output and pending work. Inputs: reason. Outputs: cancellation and empty state.
   * Logic: suspend scheduling during synchronous cancellation, clear timers/history, retain explicit user switches/errors.
   * Constraints: no manual/scene cancellation, output retry, automatic restart or persisted snapshot. */
  stop(reason = "stop") {
    this.suspended = true;
    for (const timer of [this.perceptionTimer, this.navigationTimer, this.vibrationTimer, this.riskTimer]) this.clearTimer(timer);
    this.latest = this.navigation = null;
    this.history.clear();
    this.trackingEpoch = null;
    this.lastSuppression = null;
    this.cancel("speech"); this.cancel("vibration");
    this.sessionId = null;
    this.suspended = false;
    this.report(reason);
  }
  /** Functionality: Release subscriptions and work. Inputs: manager state. Outputs: disposed listeners/timers.
   * Logic: stop before unsubscribing; Constraints: no page-independent outputs after disposal. */
  dispose() { this.stop("disposed"); for (const unsubscribe of this.unsubscribers) unsubscribe(); }
}
