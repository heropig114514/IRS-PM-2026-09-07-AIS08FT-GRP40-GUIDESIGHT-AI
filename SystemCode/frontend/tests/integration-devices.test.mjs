/** Responsibilities: Verify explicit permission cleanup, failure propagation and no implicit camera choice.
 * Implementation: Independent media-device/track fixtures; no real browser permission or sensor.
 * Related Modules: integration/devices.js and local-mvp.js; root browser test verifies native prompt/UI behavior.
 * Declaration Index: testPageGranted; testPageGranted.getUserMedia; testPageGranted.enumerateDevices;
 * testPageDenied; testPageDenied.getUserMedia; testPageDenied.enumerateDevices;
 * testGranted; testGranted.stopFirst; testGranted.stopSecond; testGranted.getUserMedia;
 * testGranted.enumerateDevices; testDenied; testDenied.getUserMedia; testDenied.enumerateDevices;
 * testCleanupFailure; testCleanupFailure.stopFirst; testCleanupFailure.stopSecond;
 * testCleanupFailure.getUserMedia; testCleanupFailure.enumerateDevices; testExplicitSelection.
 * Variable Index: None; fixtures are local to each test and never open media/network/GPU.
 */
import assert from "node:assert/strict";
import test from "node:test";
import {authorizeCameraDevices, explicitDeviceId, requestPageMediaPermissions} from "../integration/devices.js";

/** Functionality: Verify page-entry media permission releases audio and video before inventory reads.
 * Inputs: Combined-media and track fixtures. Outputs: Inventory after exact request/cleanup ordering.
 * Logic: One browser request authorizes both features; each temporary track changes to ended.
 * Constraints: Fixtures do not start a recorder, preview or inference session. */
test("page-entry media permission stops its temporary audio and video tracks", async function testPageGranted() {
  const calls = [], devices = [{kind: "videoinput", deviceId: "camera-1"}];
  const tracks = ["audio", "video"].map(kind => ({kind, readyState: "live", stop() {
    calls.push(`stop:${kind}`); this.readyState = "ended";
  }}));
  /** Functionality: Return one explicit combined-permission stream. Inputs: Media constraints.
   * Outputs: Two stoppable synthetic tracks. Logic: Record one call; Constraints: no native device. */
  async function getUserMedia(options) { calls.push(options); return {getTracks: () => tracks}; }
  /** Functionality: Supply a readable inventory after cleanup. Inputs: None. Outputs: fixed devices.
   * Logic: Ordering marker proves cleanup precedes enumeration. Constraints: no real labels or IDs. */
  async function enumerateDevices() { calls.push("enumerate"); return devices; }
  const inventory = await requestPageMediaPermissions({getUserMedia, enumerateDevices});
  assert.deepEqual(calls, [{audio: true, video: true}, "stop:audio", "stop:video", "enumerate"]);
  assert.equal(tracks.every(track => track.readyState === "ended"), true);
  assert.equal(inventory, devices);
});

/** Functionality: Preserve one-shot denial semantics for page-entry media permission.
 * Inputs: Explicit rejected getUserMedia fixture. Outputs: Original typed error and one call.
 * Logic: A denial never falls through to inventory or retries. Constraints: no permission prompt. */
test("page-entry media denial is surfaced without retry or inventory fallback", async function testPageDenied() {
  const calls = [], denied = Object.assign(new Error("denied"), {name: "NotAllowedError"});
  /** Functionality: Reject the combined media request. Inputs: Constraints. Outputs: Original error.
   * Logic: Count exactly one request. Constraints: no native browser state is changed. */
  async function getUserMedia(options) { calls.push(options); throw denied; }
  /** Functionality: Detect prohibited enumeration after denial. Inputs: None. Outputs: marker.
   * Logic: Record any unexpected access. Constraints: no device inventory. */
  async function enumerateDevices() { calls.push("enumerate"); return []; }
  await assert.rejects(requestPageMediaPermissions({getUserMedia, enumerateDevices}), error => error === denied);
  assert.deepEqual(calls, [{audio: true, video: true}]);
});

/** Functionality: Verify granting permission releases every temporary track before reading IDs.
 * Inputs: native-style stream fixture with two tracks. Outputs: video-only request/order/inventory.
 * Logic: permission adapter does not retain/display/encode media or launch inference.
 * Constraints: browser prompt and real tracks are separately verified. */
test("explicit authorization stops temporary tracks before enumerating and never selects", async function testGranted() {
  const calls = [], devices = [{kind: "videoinput", deviceId: "camera-1", label: "Fixture camera"}];
  /** Functionality: Record first track cleanup. Inputs: None. Outputs: call marker.
   * Logic: append once; Constraints: no real media track. */
  function stopFirst() { calls.push("stop-1"); }
  /** Functionality: Record second track cleanup. Inputs: None. Outputs: call marker.
   * Logic: append once; Constraints: no real media track. */
  function stopSecond() { calls.push("stop-2"); }
  /** Functionality: Provide a temporary stream fixture. Inputs: options. Outputs: tracks.
   * Logic: record explicit media request; Constraints: no browser permission emulation. */
  async function getUserMedia(options) { calls.push(options); return {getTracks: () => [{stop: stopFirst}, {stop: stopSecond}]}; }
  /** Functionality: Return readable inventory. Inputs: None. Outputs: devices.
   * Logic: order marker verifies prior cleanup; Constraints: no native enumeration. */
  async function enumerateDevices() { calls.push("enumerate"); return devices; }
  const media = {getUserMedia, enumerateDevices};
  const inventory = await authorizeCameraDevices(media);
  assert.deepEqual(calls, [{video: true, audio: false}, "stop-1", "stop-2", "enumerate"]);
  assert.equal(inventory, devices); assert.equal(explicitDeviceId(inventory, ""), "");
});
/** Functionality: Verify denied permission is surfaced without repeat/enumeration/fallback.
 * Inputs: rejected getUserMedia fixture. Outputs: original typed error and one call.
 * Logic: no stream exists to clean up; Constraints: no browser permission-state inference. */
test("denied authorization does not retry or read devices as a fallback", async function testDenied() {
  let grants = 0, inventories = 0;
  const denied = Object.assign(new Error("denied"), {name: "NotAllowedError"});
  /** Functionality: Deny one request. Inputs: None. Outputs: typed error.
   * Logic: count original request; Constraints: no real prompt. */
  async function getUserMedia() { grants++; throw denied; }
  /** Functionality: Count unexpected inventory access. Inputs: None. Outputs: empty list.
   * Logic: independent counter; Constraints: invocation would indicate a forbidden fallback. */
  async function enumerateDevices() { inventories++; return []; }
  await assert.rejects(authorizeCameraDevices({getUserMedia, enumerateDevices}), error => error === denied);
  assert.equal(grants, 1); assert.equal(inventories, 0);
});
/** Functionality: Verify a failing cleanup does not skip another track or silently proceed.
 * Inputs: one throwing stop and one normal stop. Outputs: both attempted and error propagated.
 * Logic: no automatic retry; Constraints: inability to release is explicit rather than reported as authorization success. */
test("cleanup failure still attempts all tracks once and stays explicit", async function testCleanupFailure() {
  const calls = [], failure = new Error("stop failed");
  /** Functionality: Fail one cleanup. Inputs: None. Outputs: error/call marker.
   * Logic: independent failure; Constraints: no automatic stop retry. */
  function stopFirst() { calls.push(1); throw failure; }
  /** Functionality: Record remaining cleanup. Inputs: None. Outputs: marker.
   * Logic: proves another stop still runs; Constraints: no actual track. */
  function stopSecond() { calls.push(2); }
  /** Functionality: Return cleanup-error stream. Inputs: None. Outputs: two tracks.
   * Logic: explicit fixture; Constraints: no real media permission. */
  async function getUserMedia() { return {getTracks: () => [{stop: stopFirst}, {stop: stopSecond}]}; }
  /** Functionality: Record prohibited success continuation. Inputs: None. Outputs: empty list.
   * Logic: marker checked absent; Constraints: no fallback inventory after unconfirmed release. */
  async function enumerateDevices() { calls.push("enumerate"); return []; }
  await assert.rejects(authorizeCameraDevices({getUserMedia, enumerateDevices}), error => error === failure);
  assert.deepEqual(calls, [1, 2]);
});
/** Functionality: Verify blank/hidden/removed device IDs never become a default camera choice.
 * Inputs: read-only inventory and requested user IDs. Outputs: blank or exact user choice.
 * Logic: no first-device preference; Constraints: browser validates the chosen device at actual startup. */
test("inventory preserves only an explicit currently available video ID", function testExplicitSelection() {
  const devices = [{kind: "videoinput", deviceId: "first"}, {kind: "videoinput", deviceId: "second"},
    {kind: "audioinput", deviceId: "mic"}, {kind: "videoinput", deviceId: ""}];
  assert.equal(explicitDeviceId(devices, ""), ""); assert.equal(explicitDeviceId(devices, "gone"), "");
  assert.equal(explicitDeviceId(devices, "mic"), ""); assert.equal(explicitDeviceId(devices, "second"), "second");
});
