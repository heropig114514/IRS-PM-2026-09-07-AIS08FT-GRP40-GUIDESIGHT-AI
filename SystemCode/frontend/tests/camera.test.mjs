/** Responsibilities: Verify explicit rear-camera/device selection, cancellation, canvas reuse, capture parameters, and first-frame/steady-state timing boundaries.
 * Implementation: Use explicit page, media-permission, track, canvas, and image-decoding fixtures; do not access a real camera or network.
 * Related Modules: camera.js, agent-device.js; real-device permissions and browser behavior require separate verification.
 * Declaration Index: testCaptureProfiles; testInvalidCaptureProfile; FakeElement; FakeElement.constructor; FakeElement.addEventListener;
 * FakeElement.removeAttribute; FakeDocument; FakeDocument.constructor; FakeDocument.getElementById;
 * FakeDocument.createElement; FakeDocument.addEventListener; ignoreWindowEvent; makeApp;
 * FakeCanvas; FakeCanvas.constructor; FakeCanvas.width; FakeCanvas.height;
 * FakeCanvas.getContext; FakeCanvas.drawImage; FakeCanvas.toBlob;
 * testDenied; testDenied.denyCamera; testCancelled; testCancelled.waitForPermission;
 * testCancelled.waitForPermission.register; testCancelled.stopTrack;
 * testCancelled.getTracks; testProcessingPath; testApiMount; testDecodeCancelled;
 * testDecodeCancelled.waitForDecode;
 * testDecodeCancelled.waitForDecode.register; testCaptureReuse; testSceneHooks; testTimingBoundaries; testCameraEvents;
 * testSemanticCameraStartWithoutActivation; testLocalDeviceGate;
 * testLocalSessionForwarding; testLocalSessionForwarding.recordAdmission; testLocalSessionForwarding.requestCamera;
 * testLocalSessionForwarding.playVideo; testLocalSessionForwarding.getTracks; testLocalSessionForwarding.stopTrack;
 * testLocalRearCamera; testLocalRearCamera.requestCamera; testLocalRearCamera.playVideo;
 * testLocalRearCamera.getTracks; testLocalRearCamera.stopTrack;
 * testLocalCameraPolicy; testLocalCameraPolicy.rejectUnexpected; testLocalCameraPolicy.rejectSelected;
 * testRearUnavailable; testRearUnavailable.rejectRear;
 * testRearPermissionGeneration; testRearPermissionGeneration.requestCamera; testRearPermissionGeneration.registerPermission;
 * testRearPermissionGeneration.playVideo; testRearPermissionGeneration.registerPlayback;
 * testRearPermissionGeneration.oldTracks; testRearPermissionGeneration.newTracks;
 * testRearPermissionGeneration.stopOld; testRearPermissionGeneration.stopNew.
 * Variable Index: None; all mocks are scoped to this isolated Node test process.
 */
import assert from "node:assert/strict";
import test from "node:test";
import {performDevice} from "../agent-device.js";
import {LiveDetectionClient} from "../live-detection-client.js";

/** Functionality: Minimal DOM fixture. Logic: Store control values without rendering.
 * Constraints: Does not prove real browser layout, video playback, or decoding. */
class FakeElement {
  /** Functionality: Initialize mutable DOM state. Inputs: None. Outputs: Instance.
   * Logic: Default camera is rear-facing and dataset is writable. Constraints: Does not start a device. */
  constructor() {
    this.dataset = {};
    this.hidden = false;
    this.value = "environment";
    this.src = "";
  }
  /** Functionality: Accept event registration. Inputs: name and handler. Outputs: None.
   * Logic: Tests call the actual controller directly. Constraints: Does not simulate event propagation. */
  addEventListener(name, handler) {
    void name;
    void handler;
  }
  /** Functionality: Clear src. Inputs: name. Outputs: Updated src.
   * Logic: Support only the property used by this test. Constraints: Not a complete DOM. */
  removeAttribute(name) {
    if (name === "src") this.src = "";
  }
}
/** Functionality: Track size assignments, draw calls, and encoding parameters.
 * Logic: Property setters count canvas resets; toBlob returns an explicit synthetic marker.
 * Constraints: Does not perform real drawing or encoding. */
class FakeCanvas extends FakeElement {
  /** Functionality: Initialize the canvas fixture. Inputs: None. Outputs: Instance.
   * Logic: _width/_height store dimensions, resets counts assignments, and drawn/encoded store test parameters.
   * Constraints: Does not allocate browser resources. */
  constructor() {
    super();
    this._width = 0;
    this._height = 0;
    this.resets = 0;
    this.drawn = [];
    this.encoded = [];
  }
  /** Functionality: Read width. Inputs: _width. Outputs: Number.
   * Logic: Return the stored value. Constraints: Does not calculate layout. */
  get width() { return this._width; }
  /** Functionality: Record a width assignment. Inputs: value. Outputs: Updated state and count.
   * Logic: Count same-value assignments as resets to match canvas property semantics.
   * Constraints: Does not allocate a pixel buffer. */
  set width(value) { this._width = value; this.resets += 1; }
  /** Functionality: Read height. Inputs: _height. Outputs: Number.
   * Logic: Return the stored value. Constraints: Does not calculate layout. */
  get height() { return this._height; }
  /** Functionality: Record a height assignment. Inputs: value. Outputs: Updated state and count.
   * Logic: Count same-value assignments as resets. Constraints: Does not allocate a pixel buffer. */
  set height(value) { this._height = value; this.resets += 1; }
  /** Functionality: Provide a draw recorder. Inputs: kind. Outputs: Minimal context.
   * Logic: Accept only 2d and bind the test's recorder. Constraints: Does not render images. */
  getContext(kind) {
    assert.equal(kind, "2d");
    return {drawImage: this.drawImage.bind(this)};
  }
  /** Functionality: Record target dimensions. Inputs: video, x, y, width, height. Outputs: Updated drawn.
   * Logic: Check source and origin, then save dimensions. Constraints: Does not read real video pixels. */
  drawImage(video, x, y, width, height) {
    assert.ok(video);
    assert.equal(x, 0);
    assert.equal(y, 0);
    this.drawn.push([width, height]);
  }
  /** Functionality: Return an explicit synthetic JPEG marker. Inputs: callback, mime, quality. Outputs: callback(Blob).
   * Logic: Record format and quality. Constraints: The marker is not decodable; this verifies parameters and control flow only. */
  toBlob(callback, mime, quality) {
    this.encoded.push({mime, quality});
    callback(new Blob([Uint8Array.from([255, 216, 1, 255, 217])], {type: mime}));
  }
}
/** Functionality: Provide controlled page nodes. Logic: Create missing nodes on demand.
 * Constraints: Does not execute HTML and is independent of a real browser. */
class FakeDocument {
  /** Functionality: Initialize the node collection. Inputs: None. Outputs: Instance.
   * Logic: Store nodes in a Map. Constraints: Does not read files. */
  constructor() {
    this.elements = new Map();
  }
  /** Functionality: Return a stable node. Inputs: id. Outputs: FakeElement.
   * Logic: Create on first access and reuse afterwards. Constraints: No selector parsing. */
  getElementById(id) {
    if (!this.elements.has(id)) this.elements.set(id, new FakeElement());
    return this.elements.get(id);
  }
  /** Functionality: Return an explicit element fixture. Inputs: tag. Outputs: FakeCanvas or FakeElement.
   * Logic: Canvas tracks dimensions, draw calls, and encoding parameters. Constraints: No real-browser performance verification. */
  createElement(tag) {
    return tag === "canvas" ? new FakeCanvas() : new FakeElement();
  }
  /** Functionality: Accept page event registration. Inputs: name and handler. Outputs: None.
   * Logic: Do not dispatch automatically. Constraints: Does not simulate system background state. */
  addEventListener(name, handler) {
    void name;
    void handler;
  }
}
/** Functionality: Accept window events. Inputs: name and handler. Outputs: None.
 * Logic: Preserve the constructor contract. Constraints: No real window is present. */
function ignoreWindowEvent(name, handler) {
  void name;
  void handler;
}
globalThis.document = new FakeDocument();
globalThis.window = {
  isSecureContext: true,
  addEventListener: ignoreWindowEvent,
};
const { CameraApp, cameraApp, processingPath, captureProfile } = await import("../camera.js");

/** Functionality: Verify explicit Echo/HF/local endpoints and retired modes stay rejected.
 * Inputs: actual mode mapping and retired/unknown enums. Outputs: fixed paths or explicit errors.
 * Logic: reject every former mode, rather than silently substituting a new provider.
 * Constraints: no external camera, socket, CPU detector or GPU request. */
test("Explicitly support Echo, HF, and independent local video modes", function testProcessingPath() {
  assert.equal(processingPath("echo"), "./ws/echo/");
  assert.equal(processingPath("hf"), "./ws/detect/hf/");
  assert.equal(processingPath("local"), "./ws/detect/local/");
  for (const mode of ["detect", "stream", "hf_stream", "hf_boxes", "hf_boxes_fast", "hf_live", "unknown"])
    assert.throws(() => processingPath(mode), /未知/);
});

/** Functionality: Verify event subscription and generation ownership. Inputs: actual controller and listener.
 * Outputs: subscribed result identity and no replay after unsubscribe.
 * Logic: stop publishes old-session cancellation before advancing generation; Constraints: no camera/network. */
test("camera publishes explicit session/result/stop events without DOM polling", function testCameraEvents() {
  const app = makeApp(ignoreWindowEvent), events = [], bytes = new ArrayBuffer(3);
  const unsubscribe = app.subscribe(event => events.push(event));
  app.sessionId = "local-session"; app.generation = 5;
  app.emit({type: "frame", result: {bytes}, capturedAt: 100});
  assert.equal(events[0].result.bytes, bytes); assert.equal(events[0].generation, 5);
  app.stop(); assert.equal(events[1].type, "stop"); assert.equal(events[1].sessionId, "local-session");
  unsubscribe(); app.emit({type: "frame"}); assert.equal(events.length, 2);
});

/** Functionality: Verify semantic camera start reaches the browser permission API without transient activation.
 * Inputs: assist-product document fixture, inactive userActivation and one synthetic permission denial.
 * Outputs: accepted start acknowledgement, one media request and the observed permission-denied state.
 * Logic: Invoke the actual agent adapter and CameraApp while the browser fixture rejects getUserMedia.
 * Constraints: No real camera, granted permission, network or inference; browser denial is preserved. */
test("semantic product camera start requests browser permission without a user gesture", async function testSemanticCameraStartWithoutActivation() {
  const previousDocument = globalThis.document;
  const previousNavigator = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  let requests = 0;
  globalThis.document = new FakeDocument();
  Object.defineProperty(globalThis, "navigator", {configurable: true, value: {
    userActivation: {isActive: false},
    mediaDevices: {getUserMedia: async () => {
      requests++;
      const error = new Error("synthetic camera permission denial");
      error.name = "NotAllowedError";
      throw error;
    }},
  }});
  const doc = {visibilityState: "visible", documentElement: {dataset: {assistView: "product"}},
    querySelector: selector => selector === "#preview" ? {} : null};
  try {
    cameraApp.processing.value = "echo";
    const result = await performDevice("camera_start", {}, doc);
    assert.equal(result.accepted, true);
    assert.equal(requests, 1);
    await new Promise(resolve => setImmediate(resolve));
    const state = await performDevice("camera_status", {}, doc);
    assert.equal(state.active, false);
    assert.equal(state.reason, "camera_permission_denied");
  } finally {
    globalThis.document = previousDocument;
    if (previousNavigator) Object.defineProperty(globalThis, "navigator", previousNavigator);
    else delete globalThis.navigator;
  }
});

/** Functionality: Verify local-only provider/device gates occur before media authorization.
 * Inputs: local/HF mode and explicit local-only marker. Outputs: zero media calls until an explicit device.
 * Logic: no camera/facing fallback; Constraints: actual device selection is validated by the browser. */
test("local mode requires explicit device and local-only page blocks HF", async function testLocalDeviceGate() {
  let calls = 0;
  const app = makeApp(async () => { calls++; throw new Error("unexpected media call"); });
  app.processing.value = "local"; await app.start(); assert.equal(calls, 0);
  app.localOnly = true; app.processing.value = "hf"; await app.start(); assert.equal(calls, 0);
  assert.match(app.message.textContent, /本地 MVP/);
});

/** Functionality: Verify an explicitly enabled product camera uses a single exact rear request.
 * Inputs: supported facingMode, no device ID and synthetic permission/preview.
 * Outputs: exact rear constraint, original dimensions/no audio, one permission call and released track.
 * Logic: stop during preview startup before the transport seam; Constraints: no real permission, media or inference. */
test("explicit local rear camera needs no device enumeration or preauthorization", async function testLocalRearCamera() {
  const requests = [];
  let stopped = 0;
  /** Functionality: Count release. Inputs: None. Outputs: counter increment.
   * Logic: actual controller stop owns cleanup; Constraints: synthetic track only. */
  function stopTrack() { stopped++; }
  const track = {stop: stopTrack, addEventListener: ignoreWindowEvent};
  /** Functionality: Return the same owned track. Inputs: None. Outputs: one track.
   * Logic: identity remains stable; Constraints: no hardware. */
  function getTracks() { return [track]; }
  /** Functionality: Capture the single permission request. Inputs: constraints. Outputs: synthetic stream.
   * Logic: no enumerateDevices or separate authorization call; Constraints: does not validate browser constraint enforcement. */
  async function requestCamera(constraints) { requests.push(constraints); return {getTracks}; }
  const app = makeApp(requestCamera);
  assert.equal(app.localFacingMode, null);
  app.processing.value = "local"; app.localFacingMode = "environment";
  navigator.mediaDevices.getSupportedConstraints = () => ({facingMode: true});
  /** Functionality: End the fixture before WebSocket creation. Inputs: None. Outputs: resolved preview Promise.
   * Logic: stop advances generation while preview awaits; Constraints: no actual playback. */
  async function playVideo() { app.stop(); }
  app.video.play = playVideo;
  await app.start();
  assert.deepEqual(requests, [{video: {facingMode: {exact: "environment"},
    width: {ideal: 640}, height: {ideal: 480}}, audio: false}]);
  assert.equal(stopped, 1); assert.equal(app.mode, "idle");
  assert.equal(app.stream, null); assert.equal(app.client, null);
});

/** Functionality: Reject invalid rear policy/unsupported constraints and preserve explicit device selection.
 * Inputs: invalid non-null policies, missing/false facing support, explicit device override.
 * Outputs: no media calls for invalid policies, clear failure and device constraint when explicitly selected.
 * Logic: unsupported mandatory constraints must never silently capture another lens; Constraints: synthetic failures only. */
test("local rear policy rejects invalid or unsupported values and explicit device wins", async function testLocalCameraPolicy() {
  let calls = 0;
  /** Functionality: Detect unwanted permission requests. Inputs: None. Outputs: rejection.
   * Logic: count calls before rejecting; Constraints: no media. */
  async function rejectUnexpected() { calls++; throw new Error("unexpected permission request"); }
  for (const invalid of [undefined, "", "user", "rear", false, {}]) {
    const app = makeApp(rejectUnexpected);
    app.processing.value = "local"; app.localFacingMode = invalid; app.localDeviceId = "explicit-device";
    await app.start();
    assert.match(app.message.textContent, /方向配置无效/);
    assert.equal(app.mode, "idle"); assert.equal(calls, 0);
  }
  for (const support of [undefined, () => ({}), () => ({facingMode: false})]) {
    const app = makeApp(rejectUnexpected);
    app.processing.value = "local"; app.localFacingMode = "environment";
    navigator.mediaDevices.getSupportedConstraints = support;
    await app.start();
    assert.match(app.message.textContent, /不支持明确选择后置摄像头/);
    assert.equal(app.mode, "idle"); assert.equal(calls, 0);
  }
  const requests = [];
  /** Functionality: Inspect an explicit device selection without media acquisition. Inputs: constraints. Outputs: rejection.
   * Logic: record once and stop before preview; Constraints: not a physical device check. */
  async function rejectSelected(constraints) { requests.push(constraints); throw new Error("synthetic device admission stop"); }
  const app = makeApp(rejectSelected);
  app.processing.value = "local"; app.localFacingMode = "environment"; app.localDeviceId = "explicit-device";
  await app.start();
  assert.deepEqual(requests, [{video: {deviceId: {exact: "explicit-device"},
    width: {ideal: 640}, height: {ideal: 480}}, audio: false}]);
});

/** Functionality: Verify denied or unavailable rear cameras never trigger a second capture request.
 * Inputs: native-style NotAllowedError and OverconstrainedError fixtures.
 * Outputs: one call, idle cleanup and original failure shown.
 * Logic: rejection follows existing error cleanup; Constraints: does not test physical iPhone permission UI. */
test("rear camera denial or absence fails visibly without front camera fallback or retry", async function testRearUnavailable() {
  for (const name of ["NotAllowedError", "OverconstrainedError"]) {
    let calls = 0;
    /** Functionality: Reject one requested rear camera. Inputs: constraints. Outputs: typed rejection.
     * Logic: preserve browser-style error; Constraints: no real permission or device. */
    async function rejectRear(constraints) {
      calls++;
      assert.equal(constraints.video.facingMode.exact, "environment");
      const error = new Error(`synthetic ${name}`); error.name = name; throw error;
    }
    const app = makeApp(rejectRear);
    app.processing.value = "local"; app.localFacingMode = "environment";
    navigator.mediaDevices.getSupportedConstraints = () => ({facingMode: true});
    await app.start();
    assert.equal(calls, 1); assert.equal(app.mode, "idle"); assert.equal(app.stream, null);
    assert.equal(app.client, null); assert.equal(app.status.dataset.kind, "error");
    assert.equal(app.message.textContent, `synthetic ${name}`);
  }
});

/** Functionality: Preserve generation cleanup when an old rear-camera permission resolves after a new start.
 * Inputs: two manually completed permission Promises, pending preview and separate synthetic tracks.
 * Outputs: stale stream released without touching current preview; final stop releases new stream and opens no transport.
 * Logic: real stop/start owns independent generations; Constraints: synthetic permissions cannot prove Safari behavior. */
test("late rear permission cannot replace or stop the current camera generation", async function testRearPermissionGeneration() {
  const permissions = [];
  let finishPlayback, oldStopped = 0, newStopped = 0;
  /** Functionality: Store permission resolution. Inputs: resolve. Outputs: queue entry.
   * Logic: fixture explicitly chooses return order; Constraints: no browser prompt. */
  function registerPermission(resolve) { permissions.push(resolve); }
  /** Functionality: Defer each explicit start's permission. Inputs: constraints. Outputs: pending Promise.
   * Logic: verify rear policy on both generations; Constraints: no auto retry. */
  function requestCamera(constraints) {
    assert.equal(constraints.video.facingMode.exact, "environment");
    return new Promise(registerPermission);
  }
  /** Functionality: Store preview resolution. Inputs: resolve. Outputs: completion handle.
   * Logic: block before transport; Constraints: not real video. */
  function registerPlayback(resolve) { finishPlayback = resolve; }
  /** Functionality: Hold newer preview pending. Inputs: None. Outputs: pending Promise.
   * Logic: old permission can resolve while new preview owns stream; Constraints: no pixels. */
  function playVideo() { return new Promise(registerPlayback); }
  /** Functionality: Count old track cleanup. Inputs: None. Outputs: increment.
   * Logic: independent old ownership; Constraints: no hardware. */
  function stopOld() { oldStopped++; }
  /** Functionality: Count current track cleanup. Inputs: None. Outputs: increment.
   * Logic: independent current ownership; Constraints: no hardware. */
  function stopNew() { newStopped++; }
  const oldTrack = {stop: stopOld}, newTrack = {stop: stopNew, addEventListener: ignoreWindowEvent};
  /** Functionality: Provide old track identity. Inputs: None. Outputs: old track.
   * Logic: late permission retains its own resource; Constraints: synthetic stream. */
  function oldTracks() { return [oldTrack]; }
  /** Functionality: Provide current track identity. Inputs: None. Outputs: new track.
   * Logic: current preview retains its own resource; Constraints: synthetic stream. */
  function newTracks() { return [newTrack]; }
  const app = makeApp(requestCamera), currentStream = {getTracks: newTracks};
  app.processing.value = "local"; app.localFacingMode = "environment"; app.video.play = playVideo;
  navigator.mediaDevices.getSupportedConstraints = () => ({facingMode: true});
  const oldStart = app.start();
  app.stop();
  const newStart = app.start();
  assert.equal(permissions.length, 2);
  permissions[1](currentStream); await Promise.resolve();
  assert.equal(app.stream, currentStream);
  permissions[0]({getTracks: oldTracks}); await oldStart;
  assert.equal(oldStopped, 1); assert.equal(newStopped, 0);
  assert.equal(app.stream, currentStream); assert.equal(app.video.srcObject, currentStream);
  app.stop(); finishPlayback(); await newStart;
  assert.equal(oldStopped, 1); assert.equal(newStopped, 1);
  assert.equal(app.stream, null); assert.equal(app.client, null); assert.equal(app.mode, "idle");
});

/** Functionality: Forward only explicit local duration policy through the real camera start path.
 * Inputs: default/continuous local and HF configurations with isolated media/play/admission fixtures.
 * Outputs: constructor policy/provider, original media constraints and released tracks.
 * Logic: stop at the transport admission seam before capture; HF never receives the local override.
 * Constraints: mocked connect deliberately rejects; no real media, WebSocket, frames or GPU are exercised. */
test("camera forwards continuous policy only to local transport and preserves55s defaults", async function testLocalSessionForwarding(t) {
  const clients = [], requests = [], previousWindowLocation = window.location;
  const previousLocation = Object.getOwnPropertyDescriptor(globalThis, "location");
  let stopped = 0;
  /** Functionality: Record constructed transport before capture. Inputs: instance receiver. Outputs: controlled rejection.
   * Logic: test observes real constructor options; Constraints: never opens a socket or starts a pipeline. */
  function recordAdmission() { clients.push(this); return Promise.reject(new Error("synthetic admission stop")); }
  /** Functionality: Record track cleanup. Inputs: None. Outputs: count increment.
   * Logic: real CameraApp.stop invokes this fixture; Constraints: no physical device. */
  function stopTrack() { stopped++; }
  const track = {addEventListener: ignoreWindowEvent, stop: stopTrack};
  /** Functionality: Supply a stable track fixture. Inputs: None. Outputs: one track.
   * Logic: same identity across permission and cleanup; Constraints: no generated media. */
  function getTracks() { return [track]; }
  /** Functionality: Resolve synthetic media permission. Inputs: requested constraints. Outputs: minimal stream Promise.
   * Logic: preserve constraints for assertions; Constraints: no permissions UI or capture. */
  function requestCamera(constraints) { requests.push(constraints); return Promise.resolve({getTracks}); }
  /** Functionality: Resolve synthetic preview startup. Inputs: None. Outputs: fulfilled Promise.
   * Logic: allows actual start to reach client construction; Constraints: no video playback. */
  function playVideo() { return Promise.resolve(); }
  t.mock.method(LiveDetectionClient.prototype, "connect", recordAdmission);
  window.location = {href: "https://test.invalid/"};
  Object.defineProperty(globalThis, "location", {configurable: true, value: {protocol: "https:"}});
  try {
    for (const {provider, configured, expected} of [
      {provider: "local", configured: 55, expected: 55},
      {provider: "local", configured: null, expected: null},
      {provider: "hf", configured: null, expected: 55},
    ]) {
      const app = makeApp(requestCamera);
      assert.equal(app.localSessionSeconds, 55);
      app.processing.value = provider; app.localDeviceId = "explicit-device";
      app.localSessionSeconds = configured; app.video.play = playVideo;
      await app.start();
      assert.equal(clients.at(-1).provider, provider);
      assert.equal(clients.at(-1).localSessionSeconds, expected);
      assert.deepEqual(requests.at(-1), {video: {
        ...(provider === "local" ? {deviceId: {exact: "explicit-device"}} : {facingMode: {exact: "environment"}}),
        width: {ideal: 640}, height: {ideal: 480},
      }, audio: false});
      assert.equal(app.mode, "idle"); assert.equal(app.client, null);
    }
    assert.equal(clients.length, 3); assert.equal(stopped, 3);
  } finally {
    window.location = previousWindowLocation;
    if (previousLocation) Object.defineProperty(globalThis, "location", previousLocation);
    else delete globalThis.location;
  }
});

/** Functionality: Resolve the additive live page against the existing same-origin API mount.
 * Inputs: explicit /prs/live/ location and ../ mount. Outputs: fixed root API URLs/cross-origin error.
 * Logic: real controller URL resolution; Constraints: no socket/GPU or real page rendering. */
test("Independent video entry point retains same-origin API mounting", function testApiMount() {
  const previous = window.location;
  window.location = {href: "https://47.239.50.129/prs/live/"};
  try {
    const app = new CameraApp(); app.apiBase = "../";
    assert.equal(app.apiUrl("./ws/detect/hf/").href, "https://47.239.50.129/prs/ws/detect/hf/");
    assert.equal(app.apiUrl(processingPath("echo")).href, "https://47.239.50.129/prs/ws/echo/");
    app.apiBase = "https://foreign.example/";
    assert.throws(() => app.apiUrl("./ws/detect/hf/"), /同源/);
  } finally { window.location = previous; }
});

/** Functionality: Create an isolated controller. Inputs: getUserMedia fixture. Outputs: CameraApp.
 * Logic: Replace fake document/navigator and set echo mode. Constraints: The test does not verify a real model. */
function makeApp(getUserMedia) {
  globalThis.document = new FakeDocument();
  globalThis.document.getElementById("processing").value = "echo";
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: { mediaDevices: { getUserMedia } },
  });
  return new CameraApp();
}

test("Permission denial releases state and reports an error" /** Functionality: Test the permission-denial branch.
 * Inputs: Explicitly rejecting permission fixture. Outputs: idle/error/restart assertions.
 * Logic: Call the real start method. Constraints: Does not access a real permission service. */, async function testDenied() {
  /** Functionality: Reject camera access. Inputs: Browser constraints. Outputs: Rejected Promise.
   * Logic: Simulate NotAllowed. Constraints: No hardware access. */
  async function denyCamera() {
    throw new Error("permission denied");
  }
  const app = makeApp(denyCamera);
  await app.start();
  assert.equal(app.mode, "idle");
  assert.equal(app.stream, null);
  assert.equal(app.status.dataset.kind, "error");
  assert.equal(app.startButton.disabled, false);
});

test("Late permission after stop does not open a connection and releases tracks" /** Functionality: Test cancellation during permission acquisition.
 * Inputs: Delayed permission result and mocked tracks. Outputs: Stop-call count and resource assertions.
 * Logic: Stop first, then resolve permission. Constraints: Does not represent the real-device permission UI. */, async function testCancelled() {
  let finish;
  let stopped = 0;
  /** Functionality: Delay the permission result. Inputs: Capture constraints. Outputs: Pending Promise.
   * Logic: The test controls when resolve runs. Constraints: No real permission prompt. */
  function waitForPermission() {
    /** Functionality: Save a completion handle. Inputs: resolve. Outputs: Updated finish callback.
     * Logic: Do not return media yet. Constraints: The test completes it explicitly. */
    function register(resolve) {
      finish = resolve;
    }
    return new Promise(register);
  }
  /** Functionality: Count track releases. Inputs: None. Outputs: Updated stopped state.
   * Logic: Record stop calls. Constraints: Not a real media track. */
  function stopTrack() {
    stopped += 1;
  }
  /** Functionality: Provide a track fixture. Inputs: None. Outputs: One mocked track.
   * Logic: Implement only the cancellation case. Constraints: Does not generate video. */
  function getTracks() {
    return [{ stop: stopTrack }];
  }
  const app = makeApp(waitForPermission);
  const started = app.start();
  app.stop();
  finish({ getTracks });
  await started;
  assert.equal(stopped, 1);
  assert.equal(app.stream, null);
  assert.equal(app.client, null);
  assert.equal(app.mode, "idle");
});

test("Late decode after stop does not redisplay the stale frame" /** Functionality: Test cancellation during image decoding.
 * Inputs: Mock decode Promise and synthetic binary. Outputs: Hidden-state and URL-release assertions.
 * Logic: Stop during decode, then resolve it. Constraints: Does not verify JPEG decodability. */, async function testDecodeCancelled() {
  let finish;
  /** Functionality: Delay image decoding. Inputs: None. Outputs: Pending Promise.
   * Logic: Save the completion control point. Constraints: Not a browser image decoder. */
  function waitForDecode() {
    /** Functionality: Save a completion handle. Inputs: resolve. Outputs: Updated finish callback.
     * Logic: Keep it pending until the test completes it explicitly. */
    function register(resolve) {
      finish = resolve;
    }
    return new Promise(register);
  }
  const app = makeApp(waitForDecode);
  app.image.decode = waitForDecode;
  const displaying = app.showFrame(
    new Uint8Array([1, 2]).buffer,
    app.generation,
  );
  app.stop();
  finish();
  await displaying;
  assert.equal(app.image.hidden, true);
  assert.equal(app.objectUrl, null);
  assert.equal(document.getElementById("verified").hidden, true);
});

/** Functionality: Check canvas reuse at stable resolution, dimension updates after rotation, and unchanged capture settings.
 * Inputs: 1280x960 and 960x1280 video sizes plus an explicit canvas fixture.
 * Outputs: Reset count, ratio, format, and quality assertions.
 * Logic: Call actual capture three times; only the first capture and rotation reset the canvas.
 * Constraints: Does not represent real JPEG encoding time or quality. */
test("Reuse canvas at stable dimensions and retain 640/JPEG0.8", async function testCaptureReuse() {
  const app = makeApp(ignoreWindowEvent);
  app.video.videoWidth = 1280;
  app.video.videoHeight = 960;
  await app.capture();
  await app.capture();
  assert.equal(app.canvas.resets, 2);
  app.video.videoWidth = 960;
  app.video.videoHeight = 1280;
  await app.capture();
  assert.equal(app.canvas.resets, 4);
  assert.deepEqual(app.canvas.drawn, [[640, 480], [640, 480], [480, 640]]);
  assert.deepEqual(app.canvas.encoded, Array(3).fill({mime: "image/jpeg", quality: 0.8}));
});

/** Functionality: Check scene-description hooks for frame and detection-box access.
 * Inputs: Controlled mode/lastFrame/lastDetection. Outputs: Reuse, stale-frame recapture, empty-array, and stop-clearing assertions.
 * Logic: Exercise actual CameraApp methods. Constraints: No camera or network; age uses relative real performance.now values. */
test("latestJpeg reuses fresh frames, recaptures stale frames, and returns null when stopped", async function testSceneHooks() {
  const app = makeApp(ignoreWindowEvent);
  app.video.videoWidth = 640;
  app.video.videoHeight = 480;
  assert.equal(await app.latestJpeg(), null);
  assert.deepEqual(app.latestBoxes(), []);
  app.mode = "camera";
  const fresh = new ArrayBuffer(3);
  app.lastFrame = {bytes: fresh, at: performance.now()};
  assert.equal(await app.latestJpeg(), fresh);
  app.lastFrame = {bytes: fresh, at: performance.now() - 10000};
  const recaptured = await app.latestJpeg();
  assert.notEqual(recaptured, fresh);
  assert.equal(recaptured.byteLength, 5);
  assert.equal(app.lastFrame.bytes, recaptured);
  const boxes = [{label: "person"}];
  app.lastDetection = {metadata: {boxes}, at: performance.now()};
  assert.equal(app.latestBoxes(), boxes);
  app.lastDetection = {metadata: {boxes}, at: performance.now() - 10000};
  assert.deepEqual(app.latestBoxes(), []);
  app.lastDetection = {metadata: undefined, at: performance.now()};
  assert.deepEqual(app.latestBoxes(), []);
  app.lastDetection = {metadata: {boxes: null}, at: performance.now()};
  assert.deepEqual(app.latestBoxes(), []);
  app.lastDetection = {metadata: {boxes}, at: performance.now()};
  app.stop();
  assert.equal(app.lastFrame, null);
  assert.equal(app.lastDetection, null);
});

/** Functionality: Check that first-frame timing is separate while slow steady-state frames remain in latency and rate statistics.
 * Inputs: First frame without scheduling fields, zero clock interval/slow frame, and explicit timer/credit-wait samples.
 * Outputs: Assertions for finite aggregates, null, 2.5 FPS, and observed-only scheduling statistics.
 * Logic: Process three frames through the real timing methods.
 * Constraints: Inputs use a controlled clock and are not an external-service performance test. */
test("Separate slow startup frame and include stalls in steady-state metrics", function testTimingBoundaries() {
  const app = makeApp(ignoreWindowEvent);
  assert.equal(app.recordFrameTimings("hf", {
    captureMs: 10, transportMs: 1055, renderQueueMs: 0, decodeMs: 15,
    captureToResultMs: 1065, captureToDecodedMs: 1080, completedAt: 1080,
  }), null);
  assert.equal(app.frameTimings.pacingFrames, 0);
  assert.equal(app.recordFrameTimings("hf", {
    captureMs: 10, transportMs: 55, renderQueueMs: 0, decodeMs: 15,
    captureToResultMs: 65, captureToDecodedMs: 80, completedAt: 1080,
    pacingLateMs: 10, creditWaitMs: 0,
  }), null);
  assert.equal(app.recordFrameTimings("hf", {
    captureMs: 10, transportMs: 675, renderQueueMs: 0, decodeMs: 15,
    captureToResultMs: 685, captureToDecodedMs: 700, completedAt: 1880,
    pacingLateMs: 20, creditWaitMs: 90,
  }), 2.5);
  assert.equal(app.frameTimings.steadyFrames, 2);
  assert.equal(app.frameTimings.transportTotalMs, 730);
  assert.equal(app.frameTimings.captureToResultMaxMs, 685);
  assert.equal(app.frameTimings.captureToDecodedMaxMs, 700);
  assert.equal(app.frameTimings.first.captureToDecodedMs, 1080);
  assert.equal(app.frameTimings.pacingFrames, 2);
  assert.equal(app.frameTimings.pacingLateTotalMs, 30);
  assert.equal(app.frameTimings.pacingLateMaxMs, 20);
  assert.equal(app.frameTimings.creditWaitTotalMs, 90);
  assert.equal(app.frameTimings.creditWaitFrames, 1);
});

/** Functionality: Preserve explicit camera policy bounds. Inputs: Valid/invalid names. Outputs: Settings/errors.
 * Logic: Original image/cadence remain unchanged; the new policy bounds only admitted work.
 * Constraints: Unit checks do not establish actual network latency. */
test("camera profiles retain original throughput and explicitly bound phone work", function testCaptureProfiles() {
  assert.deepEqual(captureProfile("standard"), {rate:30,maxPending:12});
  assert.deepEqual(captureProfile("low-latency-1"), {rate:30,maxPending:1});
  assert.deepEqual(captureProfile("low-latency-2"), {rate:30,maxPending:2});
  assert.throws(()=>{captureProfile("low-latency-1").maxPending=12;},TypeError);
  for(const value of ["", "auto", "__proto__", null, undefined]) assert.throws(()=>captureProfile(value),/采样配置无效/);
});

/** Functionality: Reject invalid local profiles before sensor access. Inputs: Unknown profile. Outputs: Visible failure.
 * Logic: Start validates policy synchronously. Constraints: No automatic fallback or device request. */
test("invalid local camera profile never acquires media", async function testInvalidCaptureProfile() {
  let acquisitions=0;const app=makeApp(async()=>{acquisitions++;throw new Error("Unexpected camera access");});
  app.processing.value="local";app.cameraProfile="invalid";
  await app.start();assert.equal(acquisitions,0);assert.equal(app.mode,"idle");
  assert.match(app.message.textContent,/采样配置无效/);
});
