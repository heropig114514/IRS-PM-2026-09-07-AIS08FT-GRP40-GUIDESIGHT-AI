/* Responsibilities: Connect original maps-navigation snapshots to an injected speech adapter.
 * Implementation: One initial read and navigation events; active edges identify explicit runs,
 * terminal snapshots precede final notices, and capture lifecycle stops invalidate owned speech.
 * Related Modules: ../navigation.js, ../navigation-ui.js, navigation-speech.js;
 * ../tests/integration-maps-navigation-bridge.test.mjs. No core method replacement or device access.
 * Declaration Index: MapsNavigationSpeechBridge; MapsNavigationSpeechBridge.constructor;
 * MapsNavigationSpeechBridge.describe; MapsNavigationSpeechBridge.visible;
 * MapsNavigationSpeechBridge.endRun; MapsNavigationSpeechBridge.ingest;
 * MapsNavigationSpeechBridge.handleNavigation; MapsNavigationSpeechBridge.handleVisibility;
 * MapsNavigationSpeechBridge.handlePageHide; MapsNavigationSpeechBridge.stop;
 * MapsNavigationSpeechBridge.dispose.
 * Variable Index: adapter is caller-owned speech integration; events/doc/win are injected event
 * targets; runIdFactory creates non-route identities; runId/generation identify accepted runs;
 * previousActive tracks core lifecycle edges; blockedUntilStart rejects stopped-run updates;
 * latestState/disposed expose finite state only. No coordinates or instruction text are retained.
 */

/* Functionality: Observe the existing maps controller without owning its navigation lifecycle.
 * Logic: Begin speech on an active edge, ingest the original snapshot and cancel only adapter
 * output on route invalidation or explicit lifecycle stops.
 * Constraints: Never creates navigation, acquires GPS, plans routes or calls core start/stop. */
export class MapsNavigationSpeechBridge {
  /* Functionality: Attach independent navigation and page-lifecycle observers.
   * Inputs: Existing navigation/adapter, optional document/window/event targets, initial snapshot
   * reader and runIdFactory(generation). Outputs: Observing bridge and one initial snapshot read.
   * Logic: Subscribe before the initial read; capture stops precede existing bubbling handlers.
   * Constraints: Targets must implement EventTarget APIs; constructor never starts a device or route. */
  constructor({navigation, adapter, document: doc = globalThis.document, window: win = globalThis.window,
    events = doc, initialSnapshot = () => navigation.describe(),
    runIdFactory = generation => `maps-navigation-${generation}`} = {}) {
    if (!["begin", "ingest", "stop"].every(name => typeof adapter?.[name] === "function") ||
        typeof initialSnapshot !== "function" || typeof runIdFactory !== "function" ||
        typeof events?.addEventListener !== "function" || typeof events?.removeEventListener !== "function" ||
        [doc, win].some(target => target && (typeof target.addEventListener !== "function" ||
          typeof target.removeEventListener !== "function")))
      throw new TypeError("Maps speech bridge requires an adapter and event targets");
    Object.assign(this, {adapter, events, doc, win, runIdFactory});
    this.runId = this.latestState = null;
    this.generation = 0;
    this.previousActive = this.blockedUntilStart = this.disposed = false;
    this.handleNavigation = this.handleNavigation.bind(this);
    this.handleVisibility = this.handleVisibility.bind(this);
    this.handlePageHide = this.handlePageHide.bind(this);
    events.addEventListener("prs-navigation-change", this.handleNavigation);
    doc?.addEventListener("visibilitychange", this.handleVisibility, true);
    win?.addEventListener("pagehide", this.handlePageHide, true);
    try { this.ingest(initialSnapshot()); }
    catch (error) {
      events.removeEventListener("prs-navigation-change", this.handleNavigation);
      doc?.removeEventListener("visibilitychange", this.handleVisibility, true);
      win?.removeEventListener("pagehide", this.handlePageHide, true);
      throw error;
    }
  }

  /* Functionality: Describe bridge lifecycle without navigation contents.
   * Inputs: Internal finite state. Outputs: Detached metadata object.
   * Logic: Keep adapter ownership and core device state separate.
   * Constraints: No route, position, instruction, native handles or audibility assertion. */
  describe() {
    return {available: true, run_id: this.runId, generation: this.generation,
      previous_active: this.previousActive, navigation_state: this.latestState,
      blocked_until_start: this.blockedUntilStart, disposed: this.disposed};
  }

  /* Functionality: Check the observed foreground prerequisite.
   * Inputs: Injected document visibility. Outputs: Boolean.
   * Logic: Event-only tests may omit visibility; real hidden documents reject navigation input.
   * Constraints: Does not change browser visibility or infer background execution support. */
  visible() { return !this.doc || this.doc.visibilityState === undefined || this.doc.visibilityState === "visible"; }

  /* Functionality: Invalidate one speech run without repeating an already handled core boundary.
   * Inputs: Reason, explicitStop flag and blockUntilStart flag. Outputs: Cleared bridge run.
   * Logic: Detach run identity before adapter cancellation; only a later explicit stop can retry
   * an unconfirmed cancellation after the same inactive/lifecycle boundary has been consumed.
   * Constraints: Never invokes navigation stop, retries from GPS updates or clears adapter pause. */
  endRun(reason, explicitStop = false, blockUntilStart = false) {
    const ownedRun = this.runId !== null;
    this.runId = null;
    this.blockedUntilStart ||= blockUntilStart;
    if (ownedRun || explicitStop) this.adapter.stop(reason);
    if (ownedRun || explicitStop)
      console.info("[PRS maps speech bridge] Run stopped", {reason, generation: this.generation});
  }

  /* Functionality: Consume an original controller snapshot and identify its speech generation.
   * Inputs: Snapshot from the existing controller or navigation DOM event. Outputs: Bridge state.
   * Logic: Active edges create runs independent of route request IDs; arrived/error retain the
   * current run for its terminal notice. Intentional stops require a fresh empty locating start
   * before later active updates are accepted, preventing old GPS events from reviving speech.
   * Constraints: Never rewrites snapshots, invents arrival, polls state or touches device controls. */
  ingest(snapshot) {
    if (this.disposed || !snapshot || typeof snapshot.active !== "boolean" || typeof snapshot.state !== "string")
      return this.describe();
    this.latestState = snapshot.state;
    const active = snapshot.active === true;
    if (!active) this.previousActive = false;
    if (["idle", "ready", "stopped"].includes(snapshot.state)) {
      this.endRun(`navigation_${snapshot.state}`, false, true);
      return this.describe();
    }
    if (!this.visible()) {
      this.endRun("page_hidden", false, true);
      return this.describe();
    }
    const rising = active && !this.previousActive;
    if (this.blockedUntilStart) {
      const explicitStart = rising && snapshot.state === "locating" && !snapshot.reason &&
        snapshot.position === null && snapshot.progress === null;
      if (!explicitStart) return this.describe();
      this.blockedUntilStart = false;
    }
    if (rising) {
      const runId = this.runIdFactory(this.generation + 1);
      if (typeof runId !== "string" || !runId.trim()) throw new TypeError("Speech run ID must be nonempty");
      this.generation++;
      this.runId = runId;
      this.adapter.begin(runId);
      console.info("[PRS maps speech bridge] Run started", {generation: this.generation});
    }
    this.previousActive = active;
    if (this.runId !== null) this.adapter.ingest(snapshot);
    return this.describe();
  }

  /* Functionality: Receive the existing navigation event without reading core state again.
   * Inputs: prs-navigation-change event. Outputs: Snapshot consumption.
   * Logic: Pass the original detail object to ingest.
   * Constraints: No event fabrication, private core access or navigation action. */
  handleNavigation(event) { this.ingest(event.detail); }

  /* Functionality: Stop owned speech before ordinary hidden-page handlers run.
   * Inputs: Captured visibilitychange. Outputs: Run invalidation only while hidden.
   * Logic: Consume a boundary once; becoming visible never resumes speech or navigation.
   * Constraints: Does not change device watches or retry a previously failed cancellation. */
  handleVisibility() { if (!this.visible()) this.endRun("page_hidden", false, true); }

  /* Functionality: Invalidate owned speech when leaving the page.
   * Inputs: Captured pagehide. Outputs: Stopped speech run.
   * Logic: Share the already-consumed lifecycle guard with hidden-page stops.
   * Constraints: No second cancellation after visibilitychange and no automatic restart. */
  handlePageHide() { this.endRun("page_left", false, true); }

  /* Functionality: Explicitly stop speech independently of the original controller.
   * Inputs: User/lifecycle reason, default manual. Outputs: Bridge state.
   * Logic: Invalidate the current speech run; a later explicit call may retry uncertain cleanup.
   * Constraints: Same-run GPS does not restart speech; callers own all navigation start/stop actions. */
  stop(reason = "manual") {
    if (!this.disposed) this.endRun(reason, true, true);
    return this.describe();
  }

  /* Functionality: Remove observers and invalidate this bridge's current speech run.
   * Inputs: Current lifecycle. Outputs: Disposed bridge state.
   * Logic: Consume remaining ownership once, then remove the exact listener registrations.
   * Constraints: Does not retry a prior failed stop, dispose caller-owned adapter or stop GPS. */
  dispose() {
    if (!this.disposed) {
      this.endRun("disposed", false, true);
      this.events.removeEventListener("prs-navigation-change", this.handleNavigation);
      this.doc?.removeEventListener("visibilitychange", this.handleVisibility, true);
      this.win?.removeEventListener("pagehide", this.handlePageHide, true);
      this.disposed = true;
    }
    return this.describe();
  }
}
