/* Responsibilities: Validate explicit synthetic navigation bundles and replay local GPS fixes.
 * Implementation: Bounded deep copies, source-time-preserving asynchronous timers and generation
 * guards implement the existing WalkingNavigation geolocation interface without device/network use.
 * Related Modules: ../maps-client.js, ../navigation.js, navigation-sample.json,
 * ../tests/integration-navigation-replay.test.mjs.
 * Declaration Index: validateFixes; validateNavigationReplay; ReplayGeolocation;
 * ReplayGeolocation.constructor; ReplayGeolocation.emit; ReplayGeolocation.setReplay;
 * ReplayGeolocation.watchPosition; ReplayGeolocation.clearWatch; ReplayGeolocation.describe;
 * ReplayGeolocation.dispose.
 * Variable Index: MAX_POINTS/MAX_FIXES — import cardinality limits; MAX_OFFSET_MS — native timer
 * integer ceiling; ReplayGeolocation fixes/index — copied source and delivered count;
 * watchId/sequence/generation/timers — one owned watch and stale-callback isolation;
 * now/setTimer/clearTimer/onEvent — injected platform boundaries; disposed — terminal lifecycle.
 */
import {validateRoute} from "../maps-client.js";

const MAX_POINTS = 5000, MAX_FIXES = 2000, MAX_OFFSET_MS = 2147483647;

/* Functionality: Validate and detach a finite ordered positioning replay.
 * Inputs: Nonempty fixes array of offset_ms/longitude/latitude/accuracy_m records.
 * Outputs: Deep copy or TypeError. Logic: First offset is zero; integer offsets strictly increase;
 * coordinates and accuracy are finite and bounded to their native domains.
 * Constraints: Up to2000 fixes; native timers cannot represent offsets beyond signed32-bit ms;
 * no interpolation, source-time repair, location substitution or speed interpretation. */
function validateFixes(fixes) {
  if (!Array.isArray(fixes) || !fixes.length || fixes.length > MAX_FIXES)
    throw new TypeError("定位回放需要1至2000个定位点。");
  for (const [index, fix] of fixes.entries()) {
    if (!fix || typeof fix !== "object" || Array.isArray(fix) ||
        !Number.isSafeInteger(fix.offset_ms) || fix.offset_ms < 0 || fix.offset_ms > MAX_OFFSET_MS ||
        (index === 0 ? fix.offset_ms !== 0 : fix.offset_ms <= fixes[index - 1].offset_ms) ||
        ![fix.longitude, fix.latitude, fix.accuracy_m].every(value => typeof value === "number" && Number.isFinite(value)) ||
        Math.abs(fix.longitude) > 180 || Math.abs(fix.latitude) > 90 || fix.accuracy_m < 0)
      throw new TypeError("定位点的时间、经纬度或精度无效；首点须为0毫秒且时间严格递增。");
  }
  return structuredClone(fixes);
}

/* Functionality: Validate an explicitly synthetic local navigation test bundle.
 * Inputs: Parsed schema_version1 JSON with provenance, ORS-compatible route and replay fixes.
 * Outputs: Detached validated bundle or TypeError/Error. Logic: Validate provenance/cardinality,
 * delegate route fields to the existing contract and validate source fixes before copying.
 * Constraints: ORS schema compatibility does not assert an ORS request or real street; UI owns
 * the1MiB file limit; WalkingNavigation.setRoute retains geometry/step-continuity validation. */
export function validateNavigationReplay(bundle) {
  if (!bundle || typeof bundle !== "object" || Array.isArray(bundle) ||
      bundle.schema_version !== 1 || bundle.source !== "synthetic" ||
      typeof bundle.label !== "string" || !bundle.label.trim() || bundle.label.length > 200 ||
      typeof bundle.description !== "string" || !bundle.description.trim() || bundle.description.length > 1000)
    throw new TypeError("请选择标明synthetic来源的第1版合成导航回放文件。");
  const route = validateRoute(bundle.route);
  if (route.geometry.coordinates.length > MAX_POINTS || route.steps.length > MAX_POINTS ||
      typeof route.request_id !== "string" || !route.request_id.trim() || route.request_id.length > 200 ||
      typeof route.attribution !== "string" || !route.attribution.trim() || route.attribution.length > 1000)
    throw new TypeError("回放路线最多5000个路线点或步骤，并须声明请求标识和来源说明。");
  const fixes = validateFixes(bundle.fixes);
  return {...structuredClone(bundle), fixes};
}

/* Functionality: Supply one cancellable geolocation-compatible local replay watch.
 * Logic: Schedule each unchanged source fix once from a captured start time; generation and
 * handle checks make cleared/previous-session callbacks inert.
 * Constraints: No real GPS, network, looping, seeking, retry, synthesized intermediate points
 * or automatic replay completion declared as route arrival. */
export class ReplayGeolocation {
  /* Functionality: Create an unconfigured replay without starting timers.
   * Inputs: Optional clock/timer functions and metadata-only event observer. Outputs: Idle instance.
   * Logic: Capture dependencies and empty owned state. Constraints: Timers must deliver asynchronously;
   * constructor never requests browser geolocation or silently substitutes platform dependencies. */
  constructor({now = Date.now, setTimer = (callback, delay) => globalThis.setTimeout(callback, delay),
    clearTimer = handle => globalThis.clearTimeout(handle), onEvent = () => {}} = {}) {
    if (![now, setTimer, clearTimer, onEvent].every(value => typeof value === "function"))
      throw new TypeError("回放时钟、定时器和事件观察器必须为函数。");
    Object.assign(this, {now, setTimer, clearTimer, onEvent});
    this.fixes = [];
    this.index = this.sequence = this.generation = 0;
    this.watchId = null;
    this.timers = new Set();
    this.disposed = false;
  }

  /* Functionality: Publish diagnostics without coordinate or route content.
   * Inputs: phase plus scheduler-owned numeric metadata. Outputs: Observer notification.
   * Logic: Observer errors are isolated and logged by type. Constraints: No body/location logging,
   * replay mutation or retry results from an observer failure. */
  emit(phase, metadata = {}) {
    try { this.onEvent({phase, watch_id: this.watchId, index: this.index, ...metadata}); }
    catch (error) { console.error("[PRS navigation replay] Observer failed", {error_type: error?.name}); }
  }

  /* Functionality: Select explicit source fixes while no watch owns them.
   * Inputs: Valid finite source fixes. Outputs: Idle metadata snapshot or TypeError/Error.
   * Logic: Fully validate/copy before replacing source; reset progress only after acceptance.
   * Constraints: A running or disposed replay cannot be replaced; no pending inputs are queued. */
  setReplay(fixes) {
    if (this.disposed) throw new Error("导航回放已释放，请重新打开页面。");
    if (this.watchId !== null) throw new Error("请先停止导航，再更换定位回放。");
    this.fixes = validateFixes(fixes);
    this.index = 0;
    return this.describe();
  }

  /* Functionality: Begin one explicit geolocation-style replay watch.
   * Inputs: success callback, optional error callback and native-compatible options.
   * Outputs: Unique watch handle; queued asynchronous source deliveries.
   * Logic: Capture start clock once and schedule original offsets, including asynchronous0ms first
   * fix; each source timestamp is start+offset even when the timer fires late.
   * Constraints: One watch, no accuracy filtering/time refresh; production WalkingNavigation owns
   * freshness/accuracy/arrival gates. Exhausted fixes leave its watchdog responsible for silence. */
  watchPosition(success, error, options) {
    if (typeof success !== "function" || error !== undefined && error !== null && typeof error !== "function")
      throw new TypeError("定位回放回调必须为函数。");
    if (this.disposed || !this.fixes.length || this.watchId !== null)
      throw new Error("定位回放未配置、已运行或已释放。");
    const startedAt = this.now();
    if (!Number.isFinite(startedAt)) throw new TypeError("定位回放时钟无效。");
    const id = ++this.sequence, generation = ++this.generation;
    this.watchId = id;
    this.index = 0;
    try {
      for (const [index, fix] of this.fixes.entries()) {
        const scheduledAt = startedAt + fix.offset_ms;
        const timer = this.setTimer(() => {
          if (this.disposed || this.watchId !== id || this.generation !== generation) return;
          this.timers.delete(timer);
          this.index = index + 1;
          this.emit("fix", {scheduled_at: scheduledAt, delivered_at: this.now()});
          if (this.disposed || this.watchId !== id || this.generation !== generation) return;
          success({coords: {longitude: fix.longitude, latitude: fix.latitude, accuracy: fix.accuracy_m},
            timestamp: scheduledAt});
          if (this.watchId === id && this.index === this.fixes.length) this.emit("exhausted");
        }, Math.max(0, scheduledAt - this.now()));
        this.timers.add(timer);
      }
    } catch (caught) {
      this.clearWatch(id);
      throw caught;
    }
    this.emit("watch_started", {scheduled_at: startedAt});
    return id;
  }

  /* Functionality: Cancel only the currently owned watch and all its scheduled deliveries.
   * Inputs: Previously returned watch handle. Outputs: Metadata snapshot.
   * Logic: Invalidate generation/ownership before clearing native timers; retain delivered count.
   * Constraints: Unknown/older handles cannot cancel a later watch; no callbacks or automatic restart. */
  clearWatch(id) {
    if (id !== this.watchId || this.watchId === null) return this.describe();
    this.generation++;
    this.watchId = null;
    for (const timer of this.timers) this.clearTimer(timer);
    this.timers.clear();
    this.emit("watch_cleared", {watch_id: id});
    return this.describe();
  }

  /* Functionality: Describe replay lifecycle without exposing source positions.
   * Inputs: Instance state. Outputs: Detached finite metadata object.
   * Logic: Count delivered fixes and current watch ownership. Constraints: No coordinates,
   * route contents, callback references or native timer handles appear in this snapshot. */
  describe() {
    return {mode: "replay", configured: this.fixes.length > 0, count: this.fixes.length,
      index: this.index, active: this.watchId !== null, watch_id: this.watchId, disposed: this.disposed};
  }

  /* Functionality: Permanently release owned timers and copied source data.
   * Inputs: Current instance. Outputs: Disposed metadata snapshot.
   * Logic: Cancel existing watch once, clear source and prevent future starts/replacement.
   * Constraints: Idempotent; no real browser geolocation or another owner's output is touched. */
  dispose() {
    if (!this.disposed) {
      this.clearWatch(this.watchId);
      this.fixes = [];
      this.index = 0;
      this.disposed = true;
      this.emit("disposed");
    }
    return this.describe();
  }
}
