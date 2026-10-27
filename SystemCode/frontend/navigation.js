/* Responsibilities: Continuous walking guidance over an explicitly planned ORS route.
 * Implementation: Local segment projection, accuracy/freshness gates and one cancellable GPS
 * watch; immutable snapshots serve the UI and MCP. No requests, persistence or rerouting.
 * Related Modules: maps-client.js, navigation-ui.js, tests/navigation.test.mjs.
 * Declaration Index: distanceMeters — spherical distance; projectRoute — nearest segment;
 * WalkingNavigation — lifecycle; WalkingNavigation.constructor — dependencies/state;
 * WalkingNavigation.describe — snapshot; WalkingNavigation.publish — state notification;
 * WalkingNavigation.setRoute — validated route replacement; WalkingNavigation.start — GPS watch;
 * WalkingNavigation.armDeadline — missing-fix deadline; WalkingNavigation.receive — GPS update;
 * WalkingNavigation.fail — terminal error; WalkingNavigation.release — resource cleanup;
 * WalkingNavigation.stop — explicit cancellation.
 * Variable Index: NAVIGATION_POLICY — fixed thresholds exposed in snapshots; EARTH_RADIUS_M —
 * spherical model radius; WalkingNavigation route/cumulative — private route copy and arc lengths;
 * state/reason/position/progress — observable guidance; active/watchId/timer/generation — lifecycle;
 * geo/now/schedule/cancel/onChange/isVisible — injected platform dependencies.
 */
import {validateRoute} from "./maps-client.js";

export const NAVIGATION_POLICY = Object.freeze({
  max_accuracy_m: 30, off_route_m: 40, arrival_m: 15, max_fix_age_ms: 15000,
});
const EARTH_RADIUS_M = 6371008.8;

/* Functionality: Measure the great-circle separation between two validated coordinates.
 * Inputs: a/b [longitude, latitude] in degrees. Outputs: Nonnegative metres.
 * Logic: Haversine with a bounded round-off term handles longitude wrap.
 * Constraints: Spherical approximation, not a terrain or accessibility model. */
export function distanceMeters(a, b) {
  const radians = Math.PI / 180;
  const latitude = Math.sin((b[1] - a[1]) * radians / 2);
  const longitude = Math.sin((b[0] - a[0]) * radians / 2);
  const term = latitude ** 2 + Math.cos(a[1] * radians) * Math.cos(b[1] * radians) * longitude ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(Math.min(1, Math.max(0, term))));
}

/* Functionality: Project a GPS fix onto a walking polyline without modifying its geometry.
 * Inputs: point, route coordinates, cumulative metre lengths and previous along-route distance.
 * Outputs: cross_track_m and along_m for the selected segment.
 * Logic: Segment-local equirectangular projection estimates a clamped fraction; haversine measures
 * separation. Candidates within 3m of the minimum prefer continuity (earliest point initially).
 * Constraints: Local walking approximation; self-intersections remain inherently ambiguous;
 * this is not road map matching. Duplicate points are valid zero-length segments. */
export function projectRoute(point, coordinates, cumulative, previous = 0) {
  const candidates = [];
  for (let i = 0; i < coordinates.length - 1; i++) {
    const a = coordinates[i], b = coordinates[i + 1];
    const longitudeDelta = ((b[0] - a[0] + 540) % 360) - 180;
    const pointDelta = ((point[0] - a[0] + 540) % 360) - 180;
    const cosine = Math.cos((a[1] + b[1]) * Math.PI / 360);
    const x = longitudeDelta * cosine, y = b[1] - a[1];
    const fraction = x * x + y * y === 0 ? 0 : Math.max(0, Math.min(1,
      (pointDelta * cosine * x + (point[1] - a[1]) * y) / (x * x + y * y)));
    const projected = [a[0] + longitudeDelta * fraction, a[1] + y * fraction];
    candidates.push({cross_track_m: distanceMeters(point, projected),
      along_m: cumulative[i] + (cumulative[i + 1] - cumulative[i]) * fraction});
  }
  const minimum = candidates.reduce((value, item) => Math.min(value, item.cross_track_m), Infinity);
  return candidates.filter(item => item.cross_track_m <= minimum + 3)
    .reduce((best, item) => !best || Math.abs(item.along_m - previous) < Math.abs(best.along_m - previous)
      ? item : best, null);
}

/* Functionality: Own a single explicit navigation session for the current planned route.
 * Logic: Only fresh accurate fixes generate guidance; stop/error/arrival invalidate callbacks
 * before clearing the watch. New starts require explicit action and retain the selected route.
 * Constraints: Foreground browser GPS, no automatic recovery, voice, vibration or route requests. */
export class WalkingNavigation {
  /* Functionality: Initialize an idle controller without touching device permissions.
   * Inputs: geo browser geolocation, now clock, schedule/cancel timer functions, onChange callback,
   * isVisible foreground predicate; all optional platform defaults. Outputs: Idle instance.
   * Logic: Dependency injection isolates tests from real sensors; wrapper defaults preserve
   * the Window receiver required by native browser timer functions.
   * Constraints: onChange is a synchronous observer; constructor does not acquire location. */
  constructor({geo = globalThis.navigator?.geolocation, now = Date.now,
    schedule = (callback, delay) => globalThis.setTimeout(callback, delay),
    cancel = handle => globalThis.clearTimeout(handle), onChange = () => {},
    isVisible = () => globalThis.document?.visibilityState !== "hidden"} = {}) {
    Object.assign(this, {geo, now, schedule, cancel, onChange, isVisible});
    this.route = null;
    this.cumulative = [];
    this.state = "idle";
    this.reason = null;
    this.position = this.progress = null;
    this.watchId = this.timer = null;
    this.generation = 0;
    this.active = false;
  }

  /* Functionality: Read detached state without acquiring or transmitting another location.
   * Inputs: Instance state and clock. Outputs: JSON snapshot, including position age, route
   * provenance, current/next instruction and public policy. Logic: Clone to prevent mutation.
   * Constraints: Last position remains visible after stop with an age; active=false means no
   * guidance is running. ETA is a proportion of ORS duration, never a measured walking speed. */
  describe() {
    return structuredClone({available: true, supported: typeof this.geo?.watchPosition === "function" &&
      typeof this.geo?.clearWatch === "function", visible: this.isVisible(), active: this.active,
    state: this.state, reason: this.reason, policy: NAVIGATION_POLICY,
    position: this.position && {...this.position, age_ms: Math.max(0, this.now() - this.position.timestamp)},
    progress: this.progress, route: this.route && {
      request_id: this.route.request_id ?? null, provider: this.route.provider, profile: this.route.profile,
      summary: this.route.summary, geometry_distance_m: this.cumulative.at(-1),
      start: this.route.geometry.coordinates[0], destination: this.route.geometry.coordinates.at(-1),
      step_count: this.route.steps.length, attribution: this.route.attribution ?? null,
    }});
  }

  /* Functionality: Notify consumers and log significant transitions without location values.
   * Inputs: next state/reason, default current state and null reason. Outputs: Snapshot.
   * Logic: Log only changes of state/reason; notify each accepted position for live rendering.
   * Constraints: No coordinate, instruction text or route contents enter logs. */
  publish(state = this.state, reason = null) {
    if (state !== this.state || reason !== this.reason)
      console.info("[PRS navigation] State changed", {state, reason});
    this.state = state;
    this.reason = reason;
    const result = this.describe();
    this.onChange(result);
    return result;
  }

  /* Functionality: Replace or invalidate the route after an explicit planning/form action.
   * Inputs: Valid ORS route or null. Outputs: ready/idle snapshot; throws for invalid geometry.
   * Logic: Validate and clone before replacing; ordered steps must cover the complete polyline;
   * nonzero geometry is required for distance ratios. Release prior GPS before assignment.
   * Constraints: No old route reuse on planner failure; failed release prevents replacement. */
  setRoute(route) {
    let copy = null, cumulative = [];
    if (route) {
      copy = structuredClone(validateRoute(route));
      cumulative = [0];
      const points = copy.geometry.coordinates;
      for (let i = 1; i < points.length; i++)
        cumulative.push(cumulative.at(-1) + distanceMeters(points[i - 1], points[i]));
      if (cumulative.at(-1) <= 0 || copy.steps[0].way_points[0] !== 0 ||
          copy.steps.at(-1).type !== 10 || copy.steps.at(-1).way_points[0] !== points.length - 1 ||
          copy.steps.at(-1).way_points[1] !== points.length - 1 ||
          copy.steps.some((step, index) => index > 0 &&
            step.way_points[0] !== copy.steps[index - 1].way_points[1]))
        throw new Error("导航路线的步骤必须连续并覆盖起终点。");
    }
    if (!this.release()) throw new Error("停止定位失败，请关闭页面后重新打开。");
    this.route = copy;
    this.cumulative = cumulative;
    this.position = this.progress = null;
    return this.publish(copy ? "ready" : "idle");
  }

  /* Functionality: Start one device watch for the already planned route, from UI or MCP.
   * Inputs: Current route, support, visibility and browser-owned location permission.
   * Outputs: accepted/reason plus state; acceptance means watch submitted, not GPS acquired.
   * Logic: Refuse duplicates/missing prerequisites; callbacks capture a generation to ignore
   * late results. A deadline covers missing callbacks, including a permission prompt left open.
   * Constraints: No automatic permission bypass, planning, retries or position substitution. */
  start() {
    const refusal = this.active || this.watchId !== null ? "already_active" : !this.route ? "route_required" :
      !this.describe().supported ? "unsupported" : !this.isVisible() ? "page_hidden" : null;
    if (refusal) return {...this.describe(), operation: "start", accepted: false, reason: refusal};
    this.active = true;
    this.position = this.progress = null;
    const generation = ++this.generation;
    this.publish("locating");
    this.armDeadline(generation);
    try {
      const id = this.geo.watchPosition(position => this.receive(position, generation), error => {
        if (generation === this.generation && this.active)
          this.fail({1: "permission_denied", 2: "position_unavailable", 3: "position_timeout"}[error.code] || "position_error");
      }, {enableHighAccuracy: true, timeout: 10000, maximumAge: 0});
      if (generation === this.generation && this.active) this.watchId = id;
      else this.geo.clearWatch(id);
    } catch (error) {
      console.error("[PRS navigation] watchPosition failed", {error_type: error.name});
      this.fail("watch_failed");
    }
    return {...this.describe(), operation: "start", accepted: this.active};
  }

  /* Functionality: Bound GPS silence independently of browser watch error callbacks.
   * Inputs: generation and remaining freshness budget (default 15s). Outputs: Timer handle.
   * Logic: Replace only the deadline; expiry fails the current session once.
   * Constraints: This is termination, never an automatic repeat of a GPS request. */
  armDeadline(generation, delay = NAVIGATION_POLICY.max_fix_age_ms) {
    if (this.timer !== null) this.cancel(this.timer);
    this.timer = this.schedule(() => {
      if (this.active && generation === this.generation) this.fail("position_stale");
    }, delay);
  }

  /* Functionality: Convert a browser fix into quality-gated turn and progress information.
   * Inputs: GeolocationPosition and captured generation. Outputs: Updated snapshot notification.
   * Logic: Reject malformed, future/stale or non-increasing timestamps; low accuracy withholds
   * guidance; off-route state reports distance without advancing progress. Arrival requires both
   * remaining polyline length and endpoint distance <=15m, with accuracy <=15m. A non-arrived
   * endpoint projection remains on the final walking instruction, never falsely saying arrived.
   * Constraints: Nearest-segment approximation may be ambiguous on loops; no obstacle inference,
   * monotonic-progress assumption, rerouting or use of inaccurate fixes to declare arrival. */
  receive(fix, generation) {
    if (!this.active || generation !== this.generation) return;
    const {longitude, latitude, accuracy} = fix?.coords || {};
    if (![longitude, latitude, accuracy, fix?.timestamp].every(Number.isFinite) ||
        Math.abs(longitude) > 180 || Math.abs(latitude) > 90 || accuracy < 0) {
      this.fail("invalid_position");
      return;
    }
    const age = this.now() - fix.timestamp;
    if (age < 0 || age >= NAVIGATION_POLICY.max_fix_age_ms ||
        (this.position && fix.timestamp <= this.position.timestamp)) return;
    this.position = {longitude, latitude, accuracy_m: accuracy, timestamp: fix.timestamp};
    this.armDeadline(generation, NAVIGATION_POLICY.max_fix_age_ms - age);
    if (accuracy > NAVIGATION_POLICY.max_accuracy_m) {
      this.progress = null;
      this.publish("locating", "accuracy_insufficient");
      return;
    }
    const points = this.route.geometry.coordinates, total = this.cumulative.at(-1);
    const projection = projectRoute([longitude, latitude], points, this.cumulative,
      this.progress?.along_m ?? 0);
    if (projection.cross_track_m > NAVIGATION_POLICY.off_route_m) {
      this.progress = {cross_track_m: projection.cross_track_m, current_step: null, next_step: null};
      this.publish("off_route", "off_route");
      return;
    }
    const remaining = Math.max(0, total - projection.along_m);
    const destinationDistance = distanceMeters([longitude, latitude], points.at(-1));
    const arrived = remaining <= NAVIGATION_POLICY.arrival_m && destinationDistance <= NAVIGATION_POLICY.arrival_m &&
      accuracy <= NAVIGATION_POLICY.arrival_m;
    const steps = this.route.steps;
    let index = arrived ? steps.length - 1 : steps.findIndex(step =>
      step.type !== 10 && this.cumulative[step.way_points[1]] > projection.along_m + 0.01);
    if (index < 0) index = Math.max(0, steps.length - 2);
    this.progress = {...projection, remaining_distance_m: remaining, destination_distance_m: destinationDistance,
      remaining_duration_s: this.route.summary.duration_s * remaining / total,
      step_index: index, current_step: {...steps[index]}, next_step: steps[index + 1] ? {...steps[index + 1]} : null,
      distance_to_next_m: Math.max(0, this.cumulative[steps[index].way_points[1]] - projection.along_m)};
    if (arrived && !this.release()) return;
    this.publish(arrived ? "arrived" : "navigating");
  }

  /* Functionality: Terminate guidance on a location error with an explicit machine reason.
   * Inputs: Stable reason code. Outputs: Error snapshot and resource cleanup.
   * Logic: Clear progress before releasing GPS; preserve the last position with its timestamp.
   * Constraints: Failed cancellation takes precedence because release remains unconfirmed. */
  fail(reason) {
    console.error("[PRS navigation] Location failed", {reason});
    this.progress = null;
    if (this.release()) this.publish("error", reason);
  }

  /* Functionality: Invalidate queued callbacks and cancel the timer/device watch.
   * Inputs: Instance handles. Outputs: True if watch cleanup completed; false on native throw.
   * Logic: Increment generation before native cancellation; preserve a failed watch handle for
   * an explicit subsequent stop. Constraints: Never silently claim cleanup after an exception. */
  release() {
    this.active = false;
    this.generation++;
    if (this.timer !== null) this.cancel(this.timer);
    this.timer = null;
    if (this.watchId !== null) {
      try { this.geo.clearWatch(this.watchId); }
      catch (error) {
        console.error("[PRS navigation] clearWatch failed", {error_type: error.name});
        this.progress = null;
        this.publish("error", "stop_failed");
        return false;
      }
      this.watchId = null;
    }
    return true;
  }

  /* Functionality: Stop navigation while retaining the planned route for explicit restart.
   * Inputs: reason (user/mcp/page_hidden/page_left by callers). Outputs: accepted stop snapshot.
   * Logic: Release resources before publishing; repeated stop is harmless and does not start GPS.
   * Constraints: Stops only this controller; latest position remains an explicitly aged snapshot. */
  stop(reason = "user") {
    const accepted = this.release();
    this.progress = null;
    if (accepted) this.publish(this.route ? "stopped" : "idle", reason);
    return {...this.describe(), operation: "stop", accepted};
  }
}
