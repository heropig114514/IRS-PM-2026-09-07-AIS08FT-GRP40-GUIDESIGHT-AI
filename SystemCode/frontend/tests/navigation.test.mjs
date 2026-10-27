/* Responsibilities: Verify walking guidance geometry, location quality and cancellation contracts.
 * Implementation: Synthetic route/GPS/clock/deadlines exercise the production controller and MCP
 * dispatcher; assertions cover outcomes rather than duplicating the projection implementation.
 * Related Modules: navigation.js, ai-control.js, translations.js, navigation-ui.js, maps.html.
 * Declaration Index: routeFixture — L-shaped route; fixture — controlled GPS/clock;
 * fixture.geo.watchPosition/clearWatch — synthetic native API;
 * fixture.fix — deliver one fix; fixture.expire — run current watchdog.
 * Variable Index: None. Each test owns all synthetic sensor state; no real GPS/ORS or permissions.
 */
import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {WalkingNavigation, distanceMeters, NAVIGATION_POLICY} from "../navigation.js";
import {performNavigation} from "../ai-control.js";
import {translate} from "../i18n.js";

/* Functionality: Supply a ~222m corner route with indexed departure/turn/arrival.
 * Inputs: None. Outputs: Fresh contract-valid route. Logic: Two ~111m legs at the equator.
 * Constraints: Coordinates are synthetic, not an actual street or device position. */
function routeFixture() {
  return {provider: "openrouteservice", profile: "foot-walking", request_id: "navigation-fixture",
    geometry: {type: "LineString", coordinates: [[0, 0], [0.001, 0], [0.001, 0.001]]},
    summary: {distance_m: 222.4, duration_s: 180}, attribution: "Synthetic test route",
    steps: [
      {type: 11, instruction: "Head east", way_points: [0, 1], distance_m: 111.2, duration_s: 90},
      {type: 0, instruction: "Turn left", way_points: [1, 2], distance_m: 111.2, duration_s: 90},
      {type: 10, instruction: "Arrive", way_points: [2, 2], distance_m: 0, duration_s: 0},
    ]};
}

/* Functionality: Create deterministic GPS/deadline boundaries with zero initial watch ID.
 * Inputs: optional platform overrides. Outputs: controller, calls, watches, fix/expire helpers.
 * Logic: Fake native calls retain callbacks to test late deliveries after cancellation.
 * Constraints: No browser, hardware, network or wall-clock timing is validated. */
function fixture(overrides = {}) {
  let time = 100000, timer = 0;
  const watches = [], calls = [], timers = new Map();
  const geo = {
    // Functionality: Retain callbacks; Inputs: success/error/options; Outputs: zero-based handle;
    // Logic: record once; Constraints: no automatic callback or permission grant.
    watchPosition(success, error, options) {
      watches.push({success, error}); calls.push({operation: "watch", options}); return watches.length - 1;
    },
    // Functionality: Record cancellation; Inputs: id; Outputs: call record;
    // Logic: keep callbacks for late-delivery tests; Constraints: no native resource exists.
    clearWatch(id) { calls.push({operation: "clear", id}); },
  };
  const controller = new WalkingNavigation({geo, now: () => time, isVisible: () => true,
    schedule: callback => { timers.set(++timer, callback); return timer; },
    cancel: id => timers.delete(id), ...overrides});
  return {controller, geo, calls, watches, timers,
    // Functionality: Deliver chosen coordinates; Inputs: lon/lat/accuracy/index/timestamp;
    // Outputs: callback effects; Logic: advance test clock; Constraints: synthetic GPS only.
    fix(longitude, latitude, accuracy = 5, index = watches.length - 1, timestamp = null) {
      time += 1000;
      watches[index].success({coords: {longitude, latitude, accuracy}, timestamp: timestamp ?? time});
    },
    // Functionality: Expire watchdog; Inputs: active timer; Outputs: terminal state;
    // Logic: advance fixed clock and call timer; Constraints: no actual scheduler latency claim.
    expire() { time += 15000; [...timers.values()].at(-1)(); },
  };
}

// Functionality: Verify turn progression and arrival cleanup; Inputs: L route/accurate fixes;
// Outputs: distance/current/next/arrival assertions; Logic: traverse corner; Constraints: synthetic.
test("guidance advances at the corner and releases GPS on arrival", () => {
  const f = fixture(), nav = f.controller;
  nav.setRoute(routeFixture());
  assert.equal(nav.start().accepted, true);
  assert.deepEqual(f.calls[0].options, {enableHighAccuracy: true, timeout: 10000, maximumAge: 0});
  f.fix(0.0005, 0);
  let state = nav.describe();
  assert.equal(state.state, "navigating");
  assert.equal(state.progress.current_step.instruction, "Head east");
  assert.equal(state.progress.next_step.instruction, "Turn left");
  assert.ok(Math.abs(state.progress.remaining_distance_m - 166.8) < 1);
  f.fix(0.001, 0.0002);
  assert.equal(nav.describe().progress.step_index, 1);
  f.fix(0.001, 0.001);
  state = nav.describe();
  assert.equal(state.state, "arrived");
  assert.equal(state.active, false);
  assert.equal(state.progress.current_step.type, 10);
  assert.deepEqual(f.calls.at(-1), {operation: "clear", id: 0});
  assert.equal(f.timers.size, 0);
});

// Functionality: Withhold unreliable guidance; Inputs: low accuracy/deviation/near endpoint;
// Outputs: no stale instructions or false arrival; Logic: inspect gates; Constraints: no GPS proof.
test("poor accuracy, off-route fixes and near-end projections cannot falsely arrive", () => {
  const f = fixture(), nav = f.controller;
  nav.setRoute(routeFixture()); nav.start(); f.fix(0, 0);
  f.fix(0.001, 0.001, 80);
  assert.equal(nav.describe().reason, "accuracy_insufficient");
  assert.equal(nav.describe().progress, null);
  f.fix(0.003, 0.001);
  assert.equal(nav.describe().state, "off_route");
  assert.equal(nav.describe().progress.current_step, null);
  f.fix(0.0012, 0.001);
  assert.equal(nav.describe().state, "navigating");
  assert.notEqual(nav.describe().progress.current_step.type, 10);
  f.fix(0.001, 0.001, 20);
  assert.equal(nav.describe().state, "navigating");
  nav.stop();
});

// Functionality: Enforce freshness; Inputs: stale/out-of-order timestamps and missing callbacks;
// Outputs: bounded stop, no stale progress; Logic: controlled watchdog; Constraints: synthetic time.
test("stale and out-of-order fixes do not extend the freshness deadline", () => {
  const f = fixture(), nav = f.controller;
  nav.setRoute(routeFixture()); nav.start(); f.fix(0, 0);
  const timer = [...f.timers.keys()][0], timestamp = nav.describe().position.timestamp;
  f.fix(0.001, 0.001, 5, 0, timestamp - 20000);
  f.fix(0.001, 0.001, 5, 0, timestamp);
  assert.equal([...f.timers.keys()][0], timer);
  assert.equal(nav.describe().state, "navigating");
  f.expire();
  assert.equal(nav.describe().reason, "position_stale");
  assert.equal(nav.describe().active, false);
  assert.equal(nav.describe().progress, null);
});

// Functionality: Cancel while permission is pending and ignore prior generations;
// Inputs: captured callbacks before/after restart; Outputs: no resurrection or duplicate watch;
// Logic: exercise start/stop/route invalidation; Constraints: native prompt not tested.
test("stop, route edits and restarts reject late callbacks without retrying", () => {
  const f = fixture(), nav = f.controller;
  nav.setRoute(routeFixture()); nav.start();
  assert.equal(nav.start().reason, "already_active");
  assert.equal(f.watches.length, 1);
  assert.equal(nav.stop().accepted, true);
  f.fix(0.001, 0.001, 5, 0);
  assert.equal(nav.describe().state, "stopped");
  nav.start(); f.fix(0.001, 0.001, 5, 0);
  f.watches[0].error({code: 1});
  assert.equal(nav.describe().state, "locating");
  f.fix(0, 0);
  nav.setRoute(null); f.fix(0.001, 0.001);
  assert.equal(nav.describe().route, null);
  assert.equal(nav.describe().state, "idle");
  assert.equal(f.watches.length, 2);
});

// Functionality: Report native errors and cancellation exceptions; Inputs: fake native failures;
// Outputs: stable reasons and retained cleanup handle; Logic: no retry; Constraints: fake GPS.
test("GPS failures stop once and cancellation failures remain explicit", () => {
  for (const [code, reason] of [[1, "permission_denied"], [2, "position_unavailable"], [3, "position_timeout"]]) {
    const f = fixture(), nav = f.controller;
    nav.setRoute(routeFixture()); nav.start(); f.watches[0].error({code});
    assert.equal(nav.describe().reason, reason);
    assert.equal(nav.describe().active, false);
    assert.equal(f.calls.length, 2);
  }
  const f = fixture(), nav = f.controller;
  nav.setRoute(routeFixture()); nav.start();
  f.geo.clearWatch = () => { throw new Error("synthetic clear failure"); };
  assert.equal(nav.stop().accepted, false);
  assert.equal(nav.describe().reason, "stop_failed");
  assert.equal(nav.start().accepted, false);
  f.fix(0.001, 0.001);
  assert.equal(nav.describe().state, "error");
  f.geo.clearWatch = () => {};
  assert.equal(nav.stop().accepted, true);
});

// Functionality: Refuse unsupported or incomplete sessions and preserve snapshot isolation;
// Inputs: missing route/visibility/native API and detached snapshots; Outputs: refusal/no watch;
// Logic: exercise public interface; Constraints: no browser permissions are inferred.
test("MCP uses shared state, refuses wrong pages and never starts GPS on read", () => {
  const f = fixture(), nav = f.controller;
  assert.equal(performNavigation("navigation_status", null).reason, "page_unavailable");
  assert.equal(performNavigation("navigation_start", nav).reason, "route_required");
  nav.setRoute(routeFixture());
  const state = performNavigation("navigation_status", nav);
  assert.deepEqual(state.policy, NAVIGATION_POLICY);
  state.route.destination[0] = 99;
  assert.equal(nav.describe().route.destination[0], 0.001);
  assert.equal(f.watches.length, 0);
  assert.equal(performNavigation("navigation_start", nav).accepted, true);
  assert.equal(performNavigation("navigation_stop", nav).accepted, true);
  assert.equal(nav.describe().reason, "mcp");
  for (const [options, reason] of [[{geo: {}}, "unsupported"], [{isVisible: () => false}, "page_hidden"]]) {
    const other = fixture(options).controller;
    other.setRoute(routeFixture());
    assert.equal(other.start().reason, reason);
  }
});

// Functionality: Reject unusable navigation routes and allow ordinary duplicate vertices;
// Inputs: malformed coverage/zero length; Outputs: exceptions before watches;
// Logic: real route validation plus distance boundary; Constraints: synthetic data only.
test("navigation rejects disconnected steps and zero-length geometry", () => {
  const f = fixture();
  const broken = routeFixture(); broken.steps[1].way_points[0] = 0;
  assert.throws(() => f.controller.setRoute(broken), /连续/);
  const zero = routeFixture(); zero.geometry.coordinates = [[0, 0], [0, 0], [0, 0]];
  assert.throws(() => f.controller.setRoute(zero), /连续/);
  assert.ok(Math.abs(distanceMeters([179.999, 0], [-179.999, 0]) - 222.4) < 1);
  assert.equal(f.watches.length, 0);
});

// Functionality: Terminate silent acquisition, thrown startup and invalid fixes;
// Inputs: native/clock substitutes; Outputs: stable terminal reasons and no implicit retry;
// Logic: fail before a usable fix; Constraints: no real browser permission prompt is exercised.
test("silent acquisition and invalid native data terminate without starting a replacement", () => {
  const f = fixture(), nav = f.controller;
  nav.setRoute(routeFixture()); nav.start(); f.expire();
  assert.equal(nav.describe().reason, "position_stale");
  assert.equal(f.watches.length, 1);
  nav.start(); f.fix(NaN, 0);
  assert.equal(nav.describe().reason, "invalid_position");
  assert.equal(nav.describe().active, false);
  f.geo.watchPosition = () => { throw new Error("explicit native failure"); };
  assert.equal(nav.start().accepted, false);
  assert.equal(nav.describe().reason, "watch_failed");
  assert.equal(f.timers.size, 0);
});

// Functionality: Verify bilingual source coverage for the navigation panel and controller;
// Inputs: committed source strings; Outputs: no untranslated Chinese UI strings;
// Logic: run existing translator on literal/template text; Constraints: not visual browser QA.
test("navigation text is covered by the existing bilingual translator", async () => {
  const source = await readFile(new URL("../navigation-ui.js", import.meta.url), "utf8");
  for (const match of source.matchAll(/"([^"\n]*[\u3400-\u9fff][^"\n]*)"/g))
    assert.notEqual(translate(match[1], "en"), match[1], match[1]);
  assert.equal(translate("剩余 123 米 · 预计 2 分钟", "en"), "123 m remaining · Estimated 2 min");
  assert.equal(translate("左转 · 距下一指令 18 米", "en"), "Turn left · 18 m to next instruction");
});
