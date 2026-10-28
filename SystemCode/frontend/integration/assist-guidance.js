/* Responsibilities: Arbitrate live assistant replies, navigation and algorithm-required risk output in one owner.
 * Implementation: Latest-source scheduling, separate monotonic/GPS clocks, existing short-text
 * policies, a temporary FIFO for live assistant replies, owned preemption, queued synthesis preparation,
 * current semantic confirmation before first PCM, same-target sentence completion and
 * start-based channel cooldowns. Assistant replies use automatic bilingual TTS language selection;
 * risk/navigation guidance follows the explicit UI language. Long replies use safe text boundaries.
 * Related Modules: video-alerts.js, navigation-speech.js, maps-navigation-bridge.js, events.js,
 * catalog.js, ../navigation.js, ../speech-output.js, ../vibration.js; no core/device creation.
 * Declaration Index: splitAssistantReply; AssistGuidanceManager; AssistGuidanceManager.constructor;
 * AssistGuidanceManager.navPort.begin; AssistGuidanceManager.navPort.ingest;
 * AssistGuidanceManager.navPort.stop; AssistGuidanceManager.navPort.describe;
 * AssistGuidanceManager.subscribe; AssistGuidanceManager.riskSpeechStatus; AssistGuidanceManager.describe; AssistGuidanceManager.context;
 * AssistGuidanceManager.report; AssistGuidanceManager.configure; AssistGuidanceManager.pause;
 * AssistGuidanceManager.suppress; AssistGuidanceManager.resume; AssistGuidanceManager.setRiskEnabled;
 * AssistGuidanceManager.setNavigationEnabled; AssistGuidanceManager.beginRisk;
 * AssistGuidanceManager.ingestRisk; AssistGuidanceManager.stopRisk;
 * AssistGuidanceManager.beginNavigation; AssistGuidanceManager.ingestNavigation;
 * AssistGuidanceManager.stopNavigation; AssistGuidanceManager.riskFresh;
 * AssistGuidanceManager.riskPreparationCurrent; AssistGuidanceManager.riskSpeechConfirmation;
 * AssistGuidanceManager.sameConfirmedRiskTarget;
 * AssistGuidanceManager.navigationFresh; AssistGuidanceManager.navigationCandidate;
 * AssistGuidanceManager.refreshNavigation; AssistGuidanceManager.eligible;
 * AssistGuidanceManager.driverOwns; AssistGuidanceManager.cancel;
 * AssistGuidanceManager.enqueueAssistantReply; AssistGuidanceManager.clearAssistantReplies;
 * AssistGuidanceManager.submitSpeech; AssistGuidanceManager.submitVibration;
 * AssistGuidanceManager.outputEvent; AssistGuidanceManager.armTimer;
 * AssistGuidanceManager.pump; AssistGuidanceManager.stop; AssistGuidanceManager.dispose.
 * Variable Index: riskSpeechState bounds public diagnostic transitions without changing scheduling; RISK_TTL_MS/COOLDOWN_MS retain existing output policies; PATTERNS retains
 * existing driver pattern names/durations. latestRisk/latestNavigation are bounded sources;
 * active/pendingSubmission preserve ownership; pumpRequested coalesces synchronous lifecycle reentry;
 * histories separate speech/vibration actual starts;
 * speechText/speechLanguage/speechVoiceId retain the submitted phrase and voice configuration;
 * queued synthesis is only preparation until an eligible highest-priority current target confirms it;
 * synthesis provenance stays unchanged while separately reported confirmation identifies the current source;
 * confirmedTarget freezes actual speech-start target/session/epoch identity independently of the synthesis source;
 * assistantReplies/assistantReplySequence retain only queued live reply text and request identity;
 * navigationRevision/completedNavigation deduplicate completed steps; paused/reasons require
 * explicit recovery; schedulerTimer expires sources without retrying failed output.
 */
import {EventBus} from "./events.js";
import {languageOf} from "./catalog.js";
import {MAX_TEXT_LENGTH} from "../speech-output.js";
import {selectVideoAlert, videoAlertText} from "./video-alerts.js";
import {navigationSpeechText} from "./navigation-speech.js";
import {NAVIGATION_POLICY} from "../navigation.js";

const RISK_TTL_MS = 1000, COOLDOWN_MS = 3000;
const PATTERNS = {1: {name: "short", duration: 200}, 2: {name: "short", duration: 200},
  3: {name: "double", duration: 400}, 4: {name: "long", duration: 800}};

/* Functionality: Divide an assistant response into driver-sized utterances without losing text.
 * Inputs: Nonempty response string. Outputs: Ordered chunks of at most MAX_TEXT_LENGTH code points.
 * Logic: Prefer the last sentence/word boundary within the limit, otherwise cut at the exact limit.
 * Constraints: Concatenating chunks reproduces the input exactly; no paraphrase or truncation. */
function splitAssistantReply(text) {
  const remaining = [...text], chunks = [];
  while (remaining.length > MAX_TEXT_LENGTH) {
    let boundary = MAX_TEXT_LENGTH;
    for (let index = MAX_TEXT_LENGTH - 1; index >= Math.floor(MAX_TEXT_LENGTH * 0.6); index--) {
      if (/[\s。！？.!?；;，,]/u.test(remaining[index])) { boundary = index + 1; break; }
    }
    chunks.push(remaining.splice(0, boundary).join(""));
  }
  if (remaining.length) chunks.push(remaining.join(""));
  return chunks;
}

/* Functionality: Own assistant reply, navigation and risk speech plus vibration for one assistance page.
 * Logic: Risk precedes live assistant replies, which precede navigation; assistant replies resume
 * in order after higher-priority work, and completed navigation steps are not repeated.
 * Constraints: Disabled initially; no algorithms, navigation controls, voice replacement or failed-output retries. */
export class AssistGuidanceManager {
  /* Functionality: Bind caller-owned drivers and separate source clocks without starting output.
   * Inputs: Speech/vibration, monotonic now, epochNow and injectable timers. Outputs: Disabled owner.
   * Logic: Subscribe once per driver and expose a navigation-only port for the existing bridge.
   * Constraints: Do not instantiate other automatic output managers over these same drivers. */
  constructor({speech, vibration, now = () => performance.now(), epochNow = Date.now,
    setTimer = (callback, delay) => globalThis.setTimeout(callback, delay),
    clearTimer = timer => globalThis.clearTimeout(timer)} = {}) {
    Object.assign(this, {speech, vibration, now, epochNow, setTimer, clearTimer});
    this.events = new EventBus();
    this.riskEnabled = this.navigationEnabled = false;
    this.voiceId = ""; this.language = "zh-CN";
    this.riskSessionId = this.navigationRunId = null;
    this.latestRisk = this.latestNavigation = null;
    this.riskGeneration = this.navigationGeneration = 0;
    this.trackingEpoch = null; this.riskSpeechState = null;
    this.histories = {speech: new Map(), vibration: new Map()};
    this.active = {speech: null, vibration: null};
    this.paused = {speech: false, vibration: false};
    this.reasons = {speech: null, vibration: null};
    this.navigationKey = this.navigation = null;
    this.navigationRevision = 0; this.completedNavigation = null;
    this.assistantReplies = []; this.assistantReplySequence = 0;
    this.pendingSubmission = this.schedulerTimer = null;
    this.suspended = this.pumping = this.pumpRequested = this.disposed = false;
    this.suppressions = new Map();
    this.navPort = Object.freeze({
      // Functionality: Accept bridge run identity. Inputs: ID. Outputs: Manager state.
      // Logic: Delegate only navigation lifecycle. Constraints: Does not clear risk source/output.
      begin: runId => this.beginNavigation(runId),
      // Functionality: Accept original navigation snapshot. Inputs: Snapshot. Outputs: Manager state.
      // Logic: Delegate source validation. Constraints: Does not create or control navigation.
      ingest: snapshot => this.ingestNavigation(snapshot),
      // Functionality: Stop bridge-owned navigation output. Inputs: Reason. Outputs: Manager state.
      // Logic: Clear only navigation lifecycle. Constraints: Risk remains independently active.
      stop: reason => this.stopNavigation(reason),
      // Functionality: Read unified ownership metadata. Inputs: None. Outputs: State.
      // Logic: Delegate description. Constraints: No source text or coordinate exposure.
      describe: () => this.describe(),
    });
    this.unsubscribers = [speech?.subscribe(event => this.outputEvent(event)),
      vibration?.subscribe(event => this.outputEvent(event))].filter(Boolean);
  }

  /* Functionality: Observe non-body output lifecycle. Inputs: Listener. Outputs: Unsubscribe.
   * Logic: Local event stream. Constraints: No source replay or browser/device acquisition. */
  subscribe(listener) { return this.events.subscribe(listener); }

  /* Functionality: Explain current risk speech eligibility without changing output decisions.
   * Inputs: Original capture clock, server alert flags, ownership and driver state.
   * Outputs: Finite reason, frame age/count and pause code without objects, media or utterance text.
   * Logic: Preserve one-second freshness, algorithm-required selection and per-track cooldown;
   * distinguish completion of already-started speech from admission of a new warning.
   * Constraints: Read-only; no risk reclassification, timestamp refresh, retry or physical-audio claim. */
  riskSpeechStatus() {
    const input = this.latestRisk, age = input ? this.now() - input.capturedAt : null;
    const boxes = input?.metadata.boxes ?? [], box = selectVideoAlert(boxes);
    const active = this.active.speech, driver = this.speech?.describe();
    const state = !this.riskEnabled ? "disabled" : !this.riskSessionId ? "idle" : !input ? "waiting"
      : this.paused.speech ? "paused" : active?.kind === "risk" && active.started ? "playing"
      : !this.riskFresh() ? "stale" : !box ? "no_alert"
      : !driver?.supported || !driver.enabled || driver.visible === false || driver.user_activated === false ? "unavailable"
      : active?.kind === "risk" ? "preparing"
      : !this.eligible(box, "speech") ? "cooldown"
      : driver.active_request || driver.engine_speaking || driver.engine_pending || driver.engine_paused ? "busy" : "ready";
    return {state, frame_index: input?.metadata.index ?? null,
      capture_age_ms: Number.isFinite(age) ? age : null,
      alert_count: boxes.filter(item => item.alert_required === true && item.alert_priority > 0).length,
      pause_reason: this.paused.speech ? this.reasons.speech : null};
  }

  /* Functionality: Describe independent sources and unified ownership. Inputs: Instance state.
   * Outputs: JSON-compatible metadata, including queued reply count but no reply text.
   * Logic: Distinguish enabled sources, queued assistant replies and paused channels.
   * Constraints: No utterance, coordinates, images or hardware-success claim. */
  describe() {
    return {risk_enabled: this.riskEnabled, navigation_enabled: this.navigationEnabled,
      risk_speech: this.riskSpeechStatus(),
      risk_session_id: this.riskSessionId, navigation_run_id: this.navigationRunId,
      queued_assistant_replies: this.assistantReplies.length,
      paused: {...this.paused}, reasons: {...this.reasons},
      navigation_state: this.latestNavigation?.state ?? null,
      navigation_pending: Boolean(this.navigation && this.completedNavigation !== this.navigation.revision),
      active: Object.fromEntries(Object.entries(this.active).map(([channel, active]) => [channel, active && {
        request_id: active.requestId, kind: active.kind, started: active.started,
        cancel_failed: active.cancelFailed === true, ...this.context(active)}])),
      hardware_outputs_verified: false, disposed: this.disposed};
  }

  /* Functionality: Extract original request context and separately retained playback confirmation. Inputs: Owned descriptor.
   * Outputs: Finite metadata. Logic: Keep original captured identity and actual speech-start identity distinct.
   * Constraints: No box body, spoken text, instruction or GPS coordinate. */
  context(descriptor) {
    return {kind: descriptor?.kind ?? null, ...descriptor?.source,
      source: descriptor?.kind === "risk" ? "perception" : descriptor?.kind === "navigation" ? "navigation" : null,
      notice: descriptor?.notice ?? null,
      track_id: descriptor?.box?.track_id ?? null, alert_priority: descriptor?.box?.alert_priority ?? null,
      ...(descriptor?.confirmedTarget ? {confirmed_track_id: descriptor.confirmedTarget.track_id,
        confirmed_tracking_epoch: descriptor.confirmedTarget.tracking_epoch} : {})};
  }

  /* Functionality: Publish diagnosed state changes. Inputs: Phase/details. Outputs: Event.
   * Logic: Include current independent run IDs and monotonic event time.
   * Constraints: Only body-free machine metadata is reported. */
  report(phase, details = {}) {
    this.events.publish({phase, risk_session_id: this.riskSessionId, navigation_run_id: this.navigationRunId,
      paused: {...this.paused}, reasons: {...this.reasons}, at: this.now(), ...details});
  }

  /* Functionality: Store the shared voice ID and language selection. Inputs: voiceId/language.
   * Outputs: State. Logic: The unified assist pages require the server-provided Cherry voice;
   * revalidate pending preparation immediately after an explicit configuration change.
   * Constraints: No enablement, channel recovery or voice fallback; local synthesis is not an assist-page path. */
  configure({voiceId, language}) {
    this.voiceId = voiceId; this.language = languageOf(language); this.pump(); return this.describe();
  }

  /* Functionality: Queue one live assistant reply for prioritized automatic speech.
   * Inputs: Reply text. Outputs: Acceptance and reply identity; no text is returned in metadata.
   * Logic: Preserve complete text in bounded driver-sized chunks; risk and current assistant order
   * take precedence over navigation. Completion advances one chunk; explicit stop clears the queue.
   * Constraints: Callers must establish a visible page, enabled output, configured speech service and
   * genuine speech activation first; errors never trigger an automatic retry. */
  enqueueAssistantReply(text) {
    if (this.disposed) return {accepted: false, reason: "disposed"};
    if (typeof text !== "string" || !text.trim()) return {accepted: false, reason: "invalid_text"};
    const reply = {id: ++this.assistantReplySequence, chunks: splitAssistantReply(text), index: 0};
    this.assistantReplies.push(reply);
    this.pump();
    return {accepted: true, reply_id: reply.id, queued_replies: this.assistantReplies.length};
  }

  /* Functionality: Discard pending reply speech after an explicit stop or output disable.
   * Inputs: Fixed stop reason. Outputs: Cleared pending text and body-free lifecycle metadata.
   * Logic: Remove every not-yet-completed reply before future source changes can schedule it.
   * Constraints: Does not stop unrelated speech or change source/channel recovery state. */
  clearAssistantReplies(reason = "assistant_replies_cleared") {
    const count = this.assistantReplies.length;
    this.assistantReplies.length = 0;
    if (count) this.report("assistant_replies_cleared", {reason, count});
    return this.describe();
  }

  /* Functionality: Latch one output-channel failure. Inputs: Channel/reason/descriptor.
   * Outputs: Paused state/event. Logic: Preserve ownership if cancellation is unconfirmed.
   * Constraints: New source data and timers never clear this latch. */
  pause(channel, reason, descriptor = null) {
    this.paused[channel] = true; this.reasons[channel] = reason;
    this.report("paused", {channel, reason, request_id: descriptor?.requestId ?? null, ...this.context(descriptor)});
  }

  /* Functionality: Bound repeated prerequisite diagnostics. Inputs: Channel/reason/descriptor.
   * Outputs: One suppression event per transition. Logic: Stable target keys omit per-frame changes.
   * Constraints: No queue, retry or hidden setting changes. */
  suppress(channel, reason, descriptor) {
    const key = `${reason}/${descriptor?.kind}/${descriptor?.box?.track_id}/${descriptor?.box?.alert_priority}/${descriptor?.revision}`;
    if (this.suppressions.get(channel) === key) return;
    this.suppressions.set(channel, key);
    this.report("suppressed", {channel, reason, ...this.context(descriptor)});
  }

  /* Functionality: Recover a channel only at explicit user request. Inputs: speech/vibration.
   * Outputs: State/eligible output. Logic: Unconfirmed owned cancellation must be cleaned first.
   * Constraints: No device enablement, voice substitution or expired-source replay. */
  resume(channel) {
    if (!Object.hasOwn(this.paused, channel)) throw new TypeError("Unknown assist output channel");
    if (this.disposed || this.active[channel]?.cancelFailed) return this.describe();
    this.paused[channel] = false; this.reasons[channel] = null;
    this.suppressions.delete(channel); this.report("resumed", {channel}); this.pump(); return this.describe();
  }

  /* Functionality: Apply the explicit risk-output switch. Inputs: Boolean. Outputs: State.
   * Logic: Disable clears risk only while preserving current camera identity for future input.
   * Constraints: Never enables either driver or stops navigation. */
  setRiskEnabled(enabled) {
    this.riskEnabled = enabled === true;
    if (!this.riskEnabled) { const id = this.riskSessionId; this.stopRisk("risk_disabled"); this.riskSessionId = id; }
    else this.pump();
    this.report("risk_enabled", {enabled: this.riskEnabled}); return this.describe();
  }

  /* Functionality: Apply the explicit navigation-output switch. Inputs: Boolean. Outputs: State.
   * Logic: Disable clears only navigation work; later current-run GPS remains independently valid.
   * Constraints: Never changes GPS, route, risk state or shared driver switches. */
  setNavigationEnabled(enabled) {
    this.navigationEnabled = enabled === true;
    if (!this.navigationEnabled) { const id = this.navigationRunId; this.stopNavigation("navigation_disabled"); this.navigationRunId = id; }
    else this.pump();
    this.report("navigation_enabled", {enabled: this.navigationEnabled}); return this.describe();
  }

  /* Functionality: Begin one explicit perception session. Inputs: Nonempty session ID.
   * Outputs: Fresh risk history. Logic: Stop only previous risk ownership before assigning identity.
   * Constraints: Does not discard navigation or clear channel failures. */
  beginRisk(sessionId) {
    if (typeof sessionId !== "string" || !sessionId.trim()) throw new TypeError("Risk session ID required");
    if (this.disposed) return this.describe();
    this.stopRisk("new_risk_session"); this.riskSessionId = sessionId;
    this.report("risk_session"); return this.describe();
  }

  /* Functionality: Accept a validated perception result from the current session.
   * Inputs: Session ID, capture monotonic timestamp and metadata. Outputs: State/eligible output.
   * Logic: Keep only newest receipt; tracker-epoch changes clear per-channel track histories.
   * Constraints: Receipt does not refresh source age, classify risk or start another session. */
  ingestRisk({sessionId, capturedAt, metadata}) {
    if (this.disposed || !this.riskSessionId || sessionId !== this.riskSessionId) return this.describe();
    const epoch = metadata.tracking_epoch ?? null;
    if (epoch !== this.trackingEpoch) {
      this.trackingEpoch = epoch;
      this.histories.speech.clear(); this.histories.vibration.clear();
    }
    this.latestRisk = structuredClone({capturedAt, metadata}); this.pump(); return this.describe();
  }

  /* Functionality: Stop perception-owned work independently. Inputs: Reason.
   * Outputs: State. Logic: Clear source/history before one explicit owned cancellation per channel.
   * Constraints: Navigation and foreign driver requests remain intact; failed cleanup stays owned. */
  stopRisk(reason = "risk_stop") {
    this.riskSessionId = this.latestRisk = this.trackingEpoch = null; this.riskGeneration++;
    this.histories.speech.clear(); this.histories.vibration.clear();
    for (const channel of ["speech", "vibration"])
      if (this.active[channel]?.kind === "risk") this.cancel(channel, reason, true);
    this.report("risk_stopped", {reason}); this.pump(); return this.describe();
  }

  /* Functionality: Begin a real navigation speech generation supplied by the existing bridge.
   * Inputs: Nonempty run ID. Outputs: State. Logic: Invalidate previous navigation only.
   * Constraints: No navigation core call or risk reset, and failures require explicit recovery. */
  beginNavigation(runId) {
    if (typeof runId !== "string" || !runId.trim()) throw new TypeError("Navigation run ID required");
    if (this.disposed) return this.describe();
    this.stopNavigation("new_navigation_run"); this.navigationRunId = runId;
    this.report("navigation_run"); return this.describe();
  }

  /* Functionality: Consume the existing navigation snapshot unchanged in meaning.
   * Inputs: Core snapshot. Outputs: State/eligible output. Logic: Detach latest source and schedule.
   * Constraints: No run means ignore; no coordinate inference, device request or new policy. */
  ingestNavigation(snapshot) {
    if (this.disposed || !this.navigationRunId) return this.describe();
    this.latestNavigation = structuredClone(snapshot); this.pump(); return this.describe();
  }

  /* Functionality: Stop navigation-owned output independently. Inputs: Reason.
   * Outputs: State. Logic: Invalidate its candidate before cancelling its own speech once.
   * Constraints: Perception speech/vibration and manual output remain untouched. */
  stopNavigation(reason = "navigation_stop") {
    this.navigationRunId = this.latestNavigation = this.navigation = this.navigationKey = null;
    this.completedNavigation = null; this.navigationGeneration++; this.navigationRevision++;
    if (this.active.speech?.kind === "navigation") this.cancel("speech", reason, true);
    this.report("navigation_stopped", {reason}); this.pump(); return this.describe();
  }

  /* Functionality: Check perception source age on the monotonic clock. Inputs: Latest source.
   * Outputs: Boolean. Logic: Existing one-second TTL, rejecting future/nonfinite ages.
   * Constraints: Started short risk speech may finish without extending new-submission eligibility. */
  riskFresh() {
    const age = this.latestRisk ? this.now() - this.latestRisk.capturedAt : Infinity;
    return Number.isFinite(age) && age >= 0 && age < RISK_TTL_MS;
  }

  /* Functionality: Bound pending risk synthesis without treating preparation as audible guidance.
   * Inputs: Owned descriptor and latest original frame. Outputs: Boolean.
   * Logic: Require a fresh frame and unchanged session/generation/epoch/language/voice while
   * permitting transient changes in target identity, direction and risk meaning during synthesis.
   * Constraints: The one-second capture TTL remains strict; playback needs separate current semantic
   * confirmation. Higher global priority, stop/disable and available lower-priority work are handled by pump. */
  riskPreparationCurrent(descriptor) {
    return Boolean(this.riskEnabled && this.riskSessionId && this.riskFresh() &&
      descriptor?.generation === this.riskGeneration &&
      descriptor.source.session_id === this.riskSessionId &&
      descriptor.source.tracking_epoch === this.trackingEpoch &&
      descriptor.speechLanguage === this.language && descriptor.speechVoiceId === this.voiceId);
  }

  /* Functionality: Identify a current target that permits the prepared phrase to begin playback.
   * Inputs: Owned descriptor, latest fresh boxes and actual-start speech cooldown history. Outputs: Box or null.
   * Logic: Require exact submitted phrase/priority at the global highest current priority and an eligible
   * target; prefer the original track, otherwise use the existing TTC/distance/track ordering.
   * Constraints: Never rewrite original request provenance, downgrade urgency, refresh capture time or
   * use an ineligible/cooled-down target merely because an older phrase has finished synthesis. */
  riskSpeechConfirmation(descriptor) {
    if (!this.riskPreparationCurrent(descriptor)) return null;
    const boxes = this.latestRisk.metadata.boxes, highest = selectVideoAlert(boxes);
    if (!highest || highest.alert_priority !== descriptor.box.alert_priority) return null;
    const matches = boxes.filter(box => box.alert_required === true && box.alert_priority === highest.alert_priority &&
      videoAlertText(box, this.language) === descriptor.speechText && this.eligible(box, "speech"));
    return matches.find(box => box.track_id === descriptor.box.track_id) || selectVideoAlert(matches);
  }

  /* Functionality: Recognize the actual target of an already-started risk sentence.
   * Inputs: Owned speech descriptor and a current validated detection. Outputs: Boolean.
   * Logic: Match retained speech-start track, session, generation and tracking epoch rather than
   * the original synthesis target, which may differ after semantic confirmation.
   * Constraints: Target IDs from another tracker epoch are distinct; no algorithm identity inference. */
  sameConfirmedRiskTarget(descriptor, box) {
    const confirmed = descriptor?.confirmedTarget;
    return Boolean(descriptor?.started && confirmed && box &&
      confirmed.track_id === box.track_id && confirmed.session_id === this.riskSessionId &&
      confirmed.generation === this.riskGeneration && confirmed.tracking_epoch === this.trackingEpoch);
  }

  /* Functionality: Check source GPS age on the epoch clock. Inputs: Core snapshot.
   * Outputs: Boolean. Logic: Existing NAVIGATION_POLICY freshness boundary.
   * Constraints: Never compare GPS epoch timestamps to performance.now or use cached age_ms. */
  navigationFresh(snapshot) {
    const timestamp = snapshot?.position?.timestamp, age = this.epochNow() - timestamp;
    return Number.isFinite(timestamp) && Number.isFinite(age) && age >= 0 && age < NAVIGATION_POLICY.max_fix_age_ms;
  }

  /* Functionality: Derive an eligible existing navigation semantic state. Inputs: Latest snapshot.
   * Outputs: Semantic key/kind/step or null. Logic: Mirror existing quality/activity/arrival gates.
   * Constraints: Error notices need no fix; no risk inference or new navigation thresholds. */
  navigationCandidate() {
    const snapshot = this.latestNavigation;
    if (!this.navigationRunId || !snapshot) return null;
    if (snapshot.state === "error") return {key: `error/${snapshot.reason}`, notice: "error", step_index: null};
    if (!this.navigationFresh(snapshot)) return null;
    const accuracy = snapshot.position?.accuracy_m;
    if (!Number.isFinite(accuracy) || accuracy < 0) return null;
    if (snapshot.active === true && snapshot.state === "locating" && snapshot.reason === "accuracy_insufficient")
      return {key: "accuracy_insufficient", notice: "accuracy_insufficient", step_index: null};
    if (accuracy > NAVIGATION_POLICY.max_accuracy_m) return null;
    if (snapshot.state === "arrived" && accuracy <= NAVIGATION_POLICY.arrival_m)
      return {key: "arrived", notice: "arrived", step_index: null};
    if (!snapshot.active) return null;
    if (snapshot.state === "off_route") return {key: "off_route", notice: "off_route", step_index: null};
    const index = snapshot.progress?.step_index;
    return snapshot.state === "navigating" && Number.isInteger(index) && index >= 0 &&
      navigationSpeechText(snapshot, this.language) ? {key: `step/${index}`, notice: "turn", step_index: index} : null;
  }

  /* Functionality: Replace only the latest navigation candidate and invalidate obsolete output.
   * Inputs: Current GPS source/time. Outputs: Candidate/revision or cancellation.
   * Logic: Same-step GPS updates keep utterances; semantic changes permit a new instruction.
   * Constraints: An originally queued instruction expires on its own GPS source deadline. */
  refreshNavigation() {
    const candidate = this.navigationCandidate(), key = candidate?.key ?? null;
    if (key !== this.navigationKey) {
      this.navigationKey = key; this.navigationRevision++; this.completedNavigation = null;
      if (this.active.speech?.kind === "navigation") this.cancel("speech", "navigation_changed");
    }
    this.navigation = candidate && {...candidate, kind: "navigation", revision: this.navigationRevision,
      generation: this.navigationGeneration, snapshot: this.latestNavigation,
      source: {run_id: this.navigationRunId, step_index: candidate.step_index,
        source_timestamp: this.latestNavigation.position?.timestamp ?? null}};
    const active = this.active.speech;
    if (active?.kind === "navigation" && active.notice !== "error" && !active.started && !active.cancelFailed &&
        !this.navigationFresh(active.snapshot)) {
      this.cancel("speech", "navigation_expired_before_start");
      this.completedNavigation = this.navigationRevision;
    }
  }

  /* Functionality: Check channel-specific risk output cooldown. Inputs: Selected box/channel.
   * Outputs: Boolean. Logic: First/upgraded priority immediate, equal priority after3s since start.
   * Constraints: Downgrades do not generate new reminders; vibration cannot consume speech cooldown. */
  eligible(box, channel) {
    const previous = this.histories[channel].get(box.track_id);
    return !previous || box.alert_priority > previous.priority ||
      box.alert_priority === previous.priority && this.now() - previous.at >= COOLDOWN_MS;
  }

  /* Functionality: Check whether an unconfirmed driver request is still this owner's request.
   * Inputs: Channel/request ID. Outputs: Boolean. Logic: Use existing driver ownership metadata.
   * Constraints: Does not stop, replace or infer actual device activity. */
  driverOwns(channel, requestId) {
    const state = this[channel]?.describe();
    return channel === "speech" ? state?.active_request === true && state.last_request?.id === requestId
      : state?.request_pending === true && state.request_id === requestId;
  }

  /* Functionality: Cancel only a request created by this manager. Inputs: Channel/reason/explicit flag.
   * Outputs: Confirmed cleanup or retained ownership/pause. Logic: Detach during synchronous events.
   * Constraints: Failed cancellation is never retried by source updates, timers or another channel. */
  cancel(channel, reason, explicit = false) {
    const active = this.active[channel];
    if (!active || active.cancelFailed && !explicit) return;
    this.active[channel] = null;
    const previous = this.suspended; this.suspended = true;
    let result;
    try { result = this[channel]?.stopOwned(active.requestId); }
    catch { result = channel === "speech" ? {accepted: false, reason: "cancel_failed"} : false; }
    finally { this.suspended = previous; }
    const accepted = channel === "speech" ? result?.accepted === true : result === true;
    const notOwned = channel === "speech" ? result?.reason === "not_owned" : !this.driverOwns(channel, active.requestId);
    this.report("output_cancel", {channel, request_id: active.requestId, ...this.context(active), accepted, reason});
    if (!accepted && !notOwned) {
      active.cancelFailed = true; this.active[channel] = active;
      this.pause(channel, channel === "speech" ? result?.reason || "cancel_failed" : "cancel_failed", active);
    }
  }

  /* Functionality: Submit one backend risk, navigation or assistant utterance.
   * Inputs: Frozen descriptor/text.
   * Outputs: Request ownership or diagnosed pause/suppression. Logic: Protect all foreign speech.
   * Constraints: Only risk requests carry the explicit risk-preparation purpose; backend Cherry is
   * mandatory and no local voice, automatic enablement, retry or scene/manual preemption is permitted. */
  submitSpeech(descriptor, text) {
    const status = this.speech?.describe();
    if (this.paused.speech) return this.suppress("speech", "channel_paused", descriptor);
    if (!status?.supported || !status.enabled || status.visible === false || status.user_activated === false)
      return this.suppress("speech", "speech_unavailable", descriptor);
    if (status.active_request || status.engine_speaking || status.engine_pending || status.engine_paused)
      return this.suppress("speech", "foreign_speech_busy", descriptor);
    if (typeof this.voiceId !== "string" || !this.voiceId) return this.suppress("speech", "speech_voice_unavailable", descriptor);
    const inventory = this.speech.voiceList(), voices = inventory.voices?.filter(voice => voice.id === this.voiceId) || [];
    const voice = voices[0];
    if (!this.speech.backendOnly || !inventory.accepted || voices.length !== 1 || voice?.service !== "backend" ||
        voice.id !== "Cherry" || !voice.languages?.includes(this.language))
      return this.pause("speech", "backend_voice_unavailable", descriptor);
    const language = descriptor.kind === "assistant" ? "auto" : this.language;
    this.pendingSubmission = {...descriptor, speechText: text, speechLanguage: language, speechVoiceId: this.voiceId,
      channel: "speech", started: false, requestId: null};
    let result;
    try { result = this.speech.speak(text, {language, voice_id: this.voiceId,
      rate: Number(this.speech.rate?.value ?? 2), volume: Number(this.speech.volume?.value ?? 1),
      ...(descriptor.kind === "risk" ? {purpose: "risk"} : {})}); }
    catch { result = {accepted: false, reason: "speak_failed"}; }
    const pending = this.pendingSubmission; this.pendingSubmission = null;
    if (!result?.accepted) return this.pause("speech", result?.reason || "speak_failed", pending);
    if (!pending.finished && !this.active.speech && !this.paused.speech)
      this.active.speech = {...pending, requestId: result.last_request.id};
    this.suppressions.delete("speech");
    this.report("output_submitted", {channel: "speech", request_id: result.last_request.id,
      ...this.context(descriptor), actual_start: pending.started});
  }

  /* Functionality: Submit the existing fixed risk vibration pattern once. Inputs: Risk descriptor.
   * Outputs: Owned accepted request or paused/suppressed channel.
   * Logic: Foreign pending patterns cannot be replaced; source output remains risk-only.
   * Constraints: Native acceptance is not physical motor verification or completion. */
  submitVibration(descriptor) {
    const status = this.vibration?.describe();
    if (this.paused.vibration) return this.suppress("vibration", "channel_paused", descriptor);
    if (!status?.supported || !status.enabled || status.visible === false || status.user_activated === false)
      return this.suppress("vibration", "vibration_unavailable", descriptor);
    if (status.request_pending) return this.suppress("vibration", "foreign_vibration_pending", descriptor);
    const pattern = PATTERNS[descriptor.box.alert_priority];
    this.pendingSubmission = {...descriptor, channel: "vibration", started: false, requestId: null,
      elapsed: false, endsAt: this.now() + pattern.duration};
    let result;
    try { result = this.vibration.testPattern(pattern.name); }
    catch { result = {accepted: false, reason: "vibration_failed"}; }
    const pending = this.pendingSubmission; this.pendingSubmission = null;
    if (!result?.accepted) return this.pause("vibration", result?.reason || "vibration_failed", pending);
    if (!pending.finished && !this.active.vibration && !this.paused.vibration)
      this.active.vibration = {...pending, requestId: result.request_id};
    this.suppressions.delete("vibration");
    this.report("output_submitted", {channel: "vibration", request_id: result.request_id,
      ...this.context(descriptor), actual_start: pending.started});
  }

  /* Functionality: Consume real driver lifecycle with synchronous submission support.
   * Inputs: Body-free shared-driver event. Outputs: Start cooldown, completion or latched failure.
   * Logic: Match exact owned IDs; recheck risk sources before first PCM scheduling, retain the actual
   * confirming target for sentence-completion decisions, then advance assistant replies after completion.
   * Unconfirmed external stop retains ownership and pauses.
   * Constraints: Old/foreign callbacks cannot complete another request or silently recover a channel. */
  outputEvent(event) {
    if (this.disposed || this.suspended || !Object.hasOwn(this.active, event.channel)) return;
    const channel = event.channel; let active = this.active[channel];
    if (!active && this.pendingSubmission?.channel === channel && ["submitted", "start", "end", "error"].includes(event.phase)) {
      active = this.pendingSubmission; active.requestId = event.request_id; this.active[channel] = active;
    }
    if (!active || active.requestId !== event.request_id) {
      if (["end", "cancel"].includes(event.phase)) this.pump();
      return;
    }
    if (event.phase === "before_start") {
      this.pump();
      if (this.active[channel] === active && active.kind === "risk" && !active.started && !active.cancelFailed &&
          !this.riskSpeechConfirmation(active)) {
        this.cancel(channel, "risk_changed_before_start"); this.pump();
      }
      return;
    }
    if (event.phase === "start") {
      const confirmation = channel === "speech" && active.kind === "risk" ? this.riskSpeechConfirmation(active) : null;
      active.started = true;
      if (confirmation) active.confirmedTarget = Object.freeze({track_id: confirmation.track_id,
        session_id: this.riskSessionId, generation: this.riskGeneration, tracking_epoch: this.trackingEpoch});
      const historyBox = channel === "vibration" && active.kind === "risk" &&
        active.generation === this.riskGeneration && active.source.tracking_epoch === this.trackingEpoch
        ? active.box : confirmation;
      if (historyBox) {
        const history = this.histories[channel];
        if (!history.has(historyBox.track_id) && history.size >= 300) history.delete(history.keys().next().value);
        history.set(historyBox.track_id, {priority: historyBox.alert_priority, at: this.now()});
      }
      this.report("output_start", {channel, request_id: active.requestId, ...this.context(active),
        actual_start: true, hardware_verified: false,
        capture_to_start_ms: active.kind === "risk" ? this.now() - active.source.captured_at_ms : null,
        ...(confirmation ? {confirmed_track_id: confirmation.track_id, confirmed_frame_index: this.latestRisk.metadata.index,
          confirmed_capture_to_start_ms: this.now() - this.latestRisk.capturedAt} : {})});
    } else if (["end", "cancel", "error"].includes(event.phase)) {
      const retained = event.phase === "error" &&
        (event.reason === "cancel_failed" || this.driverOwns(channel, active.requestId));
      if (retained) active.cancelFailed = true;
      else { this.active[channel] = null; active.finished = true; }
      if (event.phase === "end" && active.kind === "navigation" && active.revision === this.navigationRevision &&
          active.generation === this.navigationGeneration) this.completedNavigation = active.revision;
      if (event.phase === "end" && active.kind === "assistant") {
        const reply = this.assistantReplies[0];
        if (reply?.id === active.replyId) {
          reply.index++;
          if (reply.index >= reply.chunks.length) this.assistantReplies.shift();
        }
      }
      this.report("output_" + event.phase, {channel, request_id: active.requestId, ...this.context(active), reason: event.reason ?? null});
      if (event.phase !== "end") this.pause(channel, event.reason || (event.phase === "cancel" ? "cancelled" : "output_error"), active);
    }
    this.pump();
  }

  /* Functionality: Arm only the earliest source/nominal-pattern deadline. Inputs: Source clocks.
   * Outputs: One timer. Logic: Expire current risk evidence at its original capture deadline;
   * pending preparation may continue while latest source evidence stays fresh; semantic approval
   * happens before first PCM. Navigation
   * retains its original GPS-source deadline; assistant replies have none.
   * Constraints: No cooldown loop, failed-output retry or request-provenance rewriting. */
  armTimer() {
    if (this.schedulerTimer !== null) this.clearTimer(this.schedulerTimer);
    this.schedulerTimer = null;
    if (this.disposed) return;
    const delays = [];
    if (this.riskEnabled && this.latestRisk && this.riskFresh())
      delays.push(RISK_TTL_MS - (this.now() - this.latestRisk.capturedAt));
    if (this.navigationEnabled && this.navigation && this.navigation.notice !== "error")
      delays.push(NAVIGATION_POLICY.max_fix_age_ms - (this.epochNow() - this.navigation.snapshot.position.timestamp));
    const speech = this.active.speech;
    if (speech && !speech.started && !speech.cancelFailed) {
      if (speech.kind === "navigation" && speech.notice !== "error")
        delays.push(NAVIGATION_POLICY.max_fix_age_ms - (this.epochNow() - speech.snapshot.position.timestamp));
    }
    const vibration = this.active.vibration;
    if (vibration && !vibration.elapsed && !vibration.cancelFailed) delays.push(vibration.endsAt - this.now());
    const delay = Math.min(...delays.filter(value => Number.isFinite(value) && value > 0));
    if (Number.isFinite(delay)) this.schedulerTimer = this.setTimer(() => { this.schedulerTimer = null; this.pump(); }, delay);
  }

  /* Functionality: Arbitrate current risk, queued assistant replies and real navigation.
   * Inputs: Independent source/driver state. Outputs: Owned speech/vibration actions.
   * Logic: Retain risk synthesis as preparation through fresh-frame target/meaning changes until
   * first PCM requires exact current confirmation. A started short sentence finishes for its actual
   * confirmed target even if that target escalates; latest valid escalation is reconsidered on completion.
   * Higher priority from another target or tracker epoch still preempts immediately;
   * no-alert preparation yields to available assistant/navigation work. Risk preempts owned navigation
   * or assistant replies; replies preempt navigation and
   * continue in FIFO order; latest fresh navigation resumes after higher-priority speech.
   * Constraints: No manual/foreign preemption, frame FIFO, algorithm changes or failed cleanup retries. */
  pump() {
    if (this.disposed || this.suspended) return;
    if (this.pumping) { this.pumpRequested = true; return; }
    this.pumping = true;
    try {
      this.refreshNavigation();
      const box = this.riskEnabled && this.riskSessionId && this.riskFresh()
        ? selectVideoAlert(this.latestRisk.metadata.boxes) : null;
      const speech = this.active.speech;
      if (speech?.kind === "risk") {
        const expiredQueued = !speech.started && !this.riskFresh();
        const yieldPreparation = !speech.started && !box && (this.assistantReplies.length > 0 ||
          this.navigationEnabled && this.navigation && this.completedNavigation !== this.navigation.revision);
        const interruptingRisk = speech.started && box
          ? selectVideoAlert(this.latestRisk.metadata.boxes.filter(candidate => !this.sameConfirmedRiskTarget(speech, candidate))) : box;
        if ((!speech.started && !this.riskPreparationCurrent(speech)) ||
            interruptingRisk && interruptingRisk.alert_priority > speech.box.alert_priority || yieldPreparation)
          this.cancel("speech", expiredQueued ? "risk_expired_before_start"
            : yieldPreparation ? "risk_preparation_yield" : "risk_changed");
      } else if (speech?.kind === "navigation" && (box || this.assistantReplies.length))
        this.cancel("speech", box ? "risk_preempted_navigation" : "assistant_reply_priority");
      else if (speech?.kind === "assistant" && box) this.cancel("speech", "risk_preempted_assistant");
      const vibration = this.active.vibration;
      if (vibration) {
        if (!vibration.elapsed && this.now() >= vibration.endsAt) {
          vibration.elapsed = true;
          this.report("pattern_elapsed", {channel: "vibration", request_id: vibration.requestId, ...this.context(vibration), hardware_verified: false});
        }
        if (!box || box.alert_priority > vibration.box.alert_priority || vibration.elapsed && this.eligible(box, "vibration"))
          this.cancel("vibration", "risk_changed");
      }
      if (box) {
        const descriptor = {kind: "risk", box, generation: this.riskGeneration,
          source: Object.freeze({session_id: this.riskSessionId, frame_index: this.latestRisk.metadata.index,
            captured_at_ms: this.latestRisk.capturedAt, tracking_epoch: this.trackingEpoch})};
        if (!this.active.speech && this.eligible(box, "speech")) this.submitSpeech(descriptor, videoAlertText(box, this.language));
        if (!this.active.vibration && this.eligible(box, "vibration")) this.submitVibration(descriptor);
      } else if (!this.active.speech && this.assistantReplies.length) {
        const reply = this.assistantReplies[0];
        this.submitSpeech({kind: "assistant", replyId: reply.id,
          source: Object.freeze({reply_id: reply.id, part_index: reply.index + 1,
            part_count: reply.chunks.length})}, reply.chunks[reply.index]);
      } else if (!this.active.speech && this.navigationEnabled && this.navigation &&
          this.completedNavigation !== this.navigation.revision)
        this.submitSpeech(this.navigation, navigationSpeechText(this.navigation.snapshot, this.language));
    } finally {
      const riskState = this.riskSpeechStatus().state;
      if (riskState !== this.riskSpeechState) {
        this.riskSpeechState = riskState;
        this.report("risk_speech_state", {reason: riskState});
      }
      this.pumping = false; this.armTimer();
      if (this.pumpRequested) { this.pumpRequested = false; this.pump(); }
    }
  }

  /* Functionality: Stop all manager-owned work at a page/global lifecycle boundary.
   * Inputs: Reason. Outputs: Cleared independent sources with honest retained failed ownership.
   * Logic: Clear pending assistant text, cancel owned assistant speech, then suspend both source
   * stops to prevent intermediate submissions.
   * Constraints: Driver switches, channel errors and unrelated manual requests remain unchanged. */
  stop(reason = "stop") {
    const previous = this.suspended; this.suspended = true;
    try {
      this.clearAssistantReplies(reason);
      if (this.active.speech?.kind === "assistant") this.cancel("speech", reason, true);
      this.stopRisk(reason); this.stopNavigation(reason);
    }
    finally { this.suspended = previous; }
    if (this.schedulerTimer !== null) this.clearTimer(this.schedulerTimer);
    this.schedulerTimer = null; this.report("stopped", {reason}); return this.describe();
  }

  /* Functionality: Release owned work and both driver subscriptions.
   * Inputs: Current state. Outputs: Disposed metadata. Logic: One stop then unsubscribe.
   * Constraints: Does not dispose shared drivers, restart sources or assert physical silence. */
  dispose() {
    if (!this.disposed) { this.stop("disposed"); this.disposed = true; for (const unsubscribe of this.unsubscribers) unsubscribe(); }
    return this.describe();
  }
}
