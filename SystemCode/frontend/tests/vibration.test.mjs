/** Responsibilities: Verify manual-vibration user boundaries, rejection feedback, lifecycle cancellation, and dictionary coverage.
 * Implementation: Use EventTarget to dispatch registered callbacks and test reentrant event snapshots/observer isolation; replace only DOM and native vibration, with no hardware or network access.
 * Related Modules: vibration.js, ai-control.js, index.html, translations.js, i18n.js。
 * Declaration Index: TestControl; TestControl.constructor; TestDocument; TestDocument.constructor;
 * TestDocument.getElementById; TestDocument.querySelectorAll; TestWindow; TestWindow.constructor;
 * TestWindow.info; TestWindow.error; setup; enable; testManual; testManual.native;
 * testUnavailable; testActivation; testActivation.native; testFailures; testFailures.native;
 * testLifecycle; testLifecycle.native; testCancelRejected; testCancelRejected.native; testTranslations。
 * testMcpShared; testMcpShared.native; testMcpFailures; testMcpFailures.native; testGuidanceOwnership;
 * testReentrantBroadcast。
 * Variable Index: None; each test's calls records fixed requests, logs stores non-sensitive diagnostics, and response controls the native fixture.
 */
import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {VibrationTester} from "../vibration.js";
import {translate} from "../i18n.js";
import {performVibration} from "../ai-control.js";

/** Functionality: Verify real driver's accepted request identity protects newer manual patterns.
 * Inputs: accepted native fixture. Outputs: start/cancel events and no foreign cancellation.
 * Logic: each pattern gets a new ID; Constraints: events represent browser API acceptance, not physical motor lifecycle. */
test("vibration event identity protects a newer manual pattern from guidance stop", function testGuidanceOwnership() {
  const calls = [], f = setup(pattern => { calls.push(pattern); return true; }), events = [];
  enable(f.doc); f.tester.subscribe(event => events.push(event));
  const first = f.tester.testPattern("short"), second = f.tester.testPattern("double");
  assert.notEqual(first.request_id, second.request_id); assert.equal(f.tester.stopOwned(first.request_id), false);
  assert.equal(calls.length, 2); assert.equal(f.tester.stopOwned(second.request_id), true);
  assert.deepEqual(events.map(event => event.phase), ["start", "start", "cancel"]);
  assert.ok(events.every(event => event.hardware_verified === false));
});

/** Functionality: Preserve cancellation identity when an observer immediately starts another pattern.
 * Inputs: Real shared driver's cancel, explicit nested pattern and mutation of the first observer's event.
 * Outputs: Old cancellation ID/time for later observers and separate new-pattern start identity.
 * Logic: Exercise actual testPattern/stop with a native acceptance fixture, not a notify reimplementation.
 * Constraints: API events are not physical motor start/end; observer assertions run after dispatch. */
test("reentrant vibration cancellation retains its original request and timestamp", function testReentrantBroadcast() {
  const calls = [], f = setup(pattern => { calls.push(pattern); return true; }); enable(f.doc);
  let firstId = null, original = null, firstReference = null, nested = null;
  const later = [];
  const unsubscribeFirst = f.tester.subscribe(event => {
    if (event.phase !== "cancel" || event.request_id !== firstId) return;
    original = {...event}; firstReference = event;
    event.request_id = -1; event.phase = "observer_mutation"; event.at = -1;
    event.reason = "observer_mutation"; event.channel = "observer_mutation"; event.hardware_verified = true;
    nested = f.tester.testPattern("double");
  });
  const unsubscribeLater = f.tester.subscribe(event => later.push(event));
  const first = f.tester.testPattern("short"); firstId = first.request_id;
  assert.equal(f.tester.stop("manual"), true);
  assert.equal(nested?.accepted, true);
  const cancellation = later.filter(event => event.phase === "cancel");
  assert.equal(cancellation.length, 1);
  assert.deepEqual(cancellation[0], original);
  assert.notEqual(cancellation[0], firstReference);
  assert.equal(cancellation[0].request_id, firstId);
  assert.equal(cancellation[0].at, original.at);
  assert.equal(cancellation[0].hardware_verified, false);
  assert.ok(later.some(event => event.phase === "start" && event.request_id === nested.request_id));
  assert.notEqual(nested.request_id, firstId);
  assert.equal(f.tester.describe().request_id, nested.request_id);
  assert.equal(f.tester.describe().request_pending, true);
  assert.deepEqual(calls, [[200], 0, [150, 100, 150]]);
  unsubscribeFirst(); unsubscribeLater(); f.tester.stop();
});

/** Functionality: Provide an independent control that dispatches click/change events.
 * Logic: EventTarget maintains listeners; fields represent only DOM state needed by tests.
 * Constraints: Does not implement browser layout or disabled-event interception; tests must check disabled explicitly.
 */
class TestControl extends EventTarget {
  /** Functionality: Initialize control fields.
   * Inputs: Optional test pattern. Outputs: Event-dispatching instance.
   * Logic: dataset stores the fixed pattern; start unchecked. Constraints: No HTML parsing or physical vibration.
   */
  constructor(pattern) {
    super();
    this.dataset = pattern ? {vibrationPattern: pattern} : {};
    this.checked = false;
    this.disabled = false;
    this.textContent = "";
  }
}

/** Functionality: Provide the vibration region and controllable page visibility.
 * Logic: Fixed node map with EventTarget events. Constraints: Not a browser document; no media or network APIs.
 */
class TestDocument extends EventTarget {
  /** Functionality: Create fixed test nodes.
   * Inputs: None. Outputs: Node map, button array, and initial visible state.
   * Logic: Match HTML control IDs. Constraints: Verifies controller contracts only, not that HTML contains the nodes.
   */
  constructor() {
    super();
    this.visibilityState = "visible";
    this.nodes = new Map(["vibration-enabled", "vibration-stop", "vibration-status", "vibration-message"]
      .map(id => [id, new TestControl()]));
    this.buttons = ["short", "long", "double"].map(name => new TestControl(name));
  }
  /** Functionality: Find a fixed node. Inputs: id. Outputs: Control or undefined.
   * Logic: Read the map directly. Constraints: Do not create missing nodes on demand.
   */
  getElementById(id) { return this.nodes.get(id); }
  /** Functionality: Return pattern buttons. Inputs: selector. Outputs: Fixed button array.
   * Logic: Check the selector used by the controller. Constraints: Does not implement general CSS queries.
   */
  querySelectorAll(selector) {
    assert.equal(selector, "[data-vibration-pattern]");
    return this.buttons;
  }
}

/** Functionality: Provide explicit native API and page-event boundaries.
 * Logic: Tests inject native return values/errors and capture diagnostics.
 * Constraints: Does not represent operating-system permissions or a device motor.
 */
class TestWindow extends EventTarget {
  /** Functionality: Install an optional native API and diagnostic receivers.
   * Inputs: Optional native vibration fixture. Outputs: navigator/console/logs and window-event instance.
   * Logic: Omit native to simulate a missing API; interaction activation defaults to true.
   * Constraints: Does not inject any media API.
   */
  constructor(native) {
    super();
    this.navigator = {userActivation: {hasBeenActive: true}};
    if (native) this.navigator.vibrate = native;
    this.logs = [];
    this.console = {info: this.info.bind(this), error: this.error.bind(this)};
  }
  /** Functionality: Capture info logs. Inputs: args. Outputs: Append to logs.
   * Logic: Preserve original arguments for assertions. Constraints: Test data contains no credentials or media.
   */
  info(...args) { this.logs.push({level: "info", args}); }
  /** Functionality: Capture error logs. Inputs: args. Outputs: Append to logs.
   * Logic: Preserve error details. Constraints: Log presence is not hardware verification.
   */
  error(...args) { this.logs.push({level: "error", args}); }
}

/** Functionality: Create an isolated test controller.
 * Inputs: Optional native API fixture. Outputs: doc/win/tester.
 * Logic: Run the actual constructor and register actual handlers. Constraints: Do not replace the controller implementation.
 */
function setup(native) {
  const doc = new TestDocument(), win = new TestWindow(native);
  return {doc, win, tester: new VibrationTester(doc, win)};
}

/** Functionality: Dispatch the user's enable choice.
 * Inputs: doc. Outputs: Checkbox state and change event.
 * Logic: Trigger the registered handler. Constraints: Does not simulate browser trust flags; activation is controlled separately.
 */
function enable(doc) {
  doc.getElementById("vibration-enabled").checked = true;
  doc.getElementById("vibration-enabled").dispatchEvent(new Event("change"));
}

/** Functionality: Verify loading/enabling does not vibrate, button patterns, explicit stop, and hardware-claim boundaries.
 * Inputs: Successful native fixture and actual events. Outputs: Call, toggle, and text assertions.
 * Logic: Compare each manual click with the fixed product pattern.
 * Constraints: No physical vibration or media-permission verification.
 */
test("Default off; send the selected pattern only on manual action", function testManual() {
  const calls = [];
  /** Functionality: Record one request. Inputs: pattern. Outputs: true and appended calls entry.
   * Logic: Simulate acceptance. Constraints: Returning true does not prove hardware exists.
   */
  function native(pattern) { calls.push(pattern); return true; }
  const {doc, tester} = setup(native);
  assert.equal(tester.enabled.checked, false);
  assert.ok(doc.buttons.every(button => button.disabled));
  assert.deepEqual(calls, []);
  enable(doc);
  assert.deepEqual(calls, []);
  for (const button of doc.buttons) button.dispatchEvent(new Event("click"));
  assert.deepEqual(calls, [[200], [800], [150, 100, 150]]);
  assert.match(tester.message.textContent, /请求已接受.*不能验证硬件/);
  tester.stopButton.dispatchEvent(new Event("click"));
  assert.deepEqual(calls.at(-1), 0);
  assert.equal(tester.requested, false);
});

/** Functionality: Verify explicit capability feedback and disabled controls when the API is absent.
 * Inputs: navigator without vibrate. Outputs: disabled and message assertions.
 * Logic: Run actual initialization. Constraints: Missing-API fixture only; does not represent all iPhone behavior.
 */
test("Missing API disables test and stop controls with an explanation", function testUnavailable() {
  const {doc, tester} = setup();
  assert.equal(tester.enabled.disabled, true);
  assert.equal(tester.stopButton.disabled, true);
  assert.ok(doc.buttons.every(button => button.disabled));
  assert.match(tester.message.textContent, /未提供震动接口/);
});

/** Functionality: Verify disabled, backgrounded, and inactive pages cannot submit a test request.
 * Inputs: Explicit navigator activation and document visibility. Outputs: Zero native calls and diagnostics.
 * Logic: Dispatch events directly to test handler boundaries. Constraints: Does not rely on fixture disabled-event interception.
 */
test("Toggle, visibility, and user activation block non-explicit requests", function testActivation() {
  const calls = [];
  /** Functionality: Capture a call that must not occur. Inputs: pattern. Outputs: true/calls.
   * Logic: Record the request. Constraints: No native API.
   */
  function native(pattern) { calls.push(pattern); return true; }
  const {doc, win, tester} = setup(native);
  doc.buttons[0].dispatchEvent(new Event("click"));
  enable(doc);
  doc.visibilityState = "hidden";
  doc.buttons[0].dispatchEvent(new Event("click"));
  assert.match(tester.message.textContent, /前台/);
  doc.visibilityState = "visible";
  win.navigator.userActivation.hasBeenActive = false;
  doc.buttons[0].dispatchEvent(new Event("click"));
  assert.match(tester.message.textContent, /先点击页面/);
  assert.deepEqual(calls, []);
});

/** Functionality: Verify native rejection and exceptions fail explicitly without automatic retry.
 * Inputs: Native fixture returning false or throwing NotAllowedError. Outputs: One call, error text, and error log.
 * Logic: Use two independent setups for return-value and exception branches.
 * Constraints: Not a real browser permission test.
 */
test("Rejection and exceptions do not claim acceptance, retry, or trigger substitute alerts", function testFailures() {
  for (const throws of [false, true]) {
    let calls = 0;
    /** Functionality: Simulate explicit failure. Inputs: pattern and current throws setting.
     * Outputs: false or exception; increment call count. Logic: Preserve this failure. Constraints: No retry.
     */
    function native(pattern) {
      assert.deepEqual(pattern, [200]);
      calls++;
      if (throws) throw Object.assign(new Error("Test permission rejection"), {name: "NotAllowedError"});
      return false;
    }
    const {doc, win, tester} = setup(native);
    enable(doc);
    doc.buttons[0].dispatchEvent(new Event("click"));
    assert.equal(calls, 1);
    assert.equal(tester.requested, false);
    assert.equal(tester.message.dataset.kind, "error");
    assert.match(tester.message.textContent, throws ? /调用异常/ : /拒绝/);
    if (throws) assert.equal(win.logs.at(-1).level, "error");
  }
});

/** Functionality: Verify cancellation on disable/background/page exit and no vibration resume on foreground return.
 * Inputs: Successful native fixture and three independent lifecycle scenarios. Outputs: Calls limited to [800] and 0.
 * Logic: Actual event handlers share the cancellation path.
 * Constraints: Does not represent lock-screen or browser-background support.
 */
test("Disable, hide, or leave cancels requests without foreground vibration resume", function testLifecycle() {
  for (const reason of ["disabled", "hidden", "pagehide"]) {
    const calls = [];
    /** Functionality: Record test and cancellation calls. Inputs: pattern. Outputs: true/calls.
     * Logic: Simulate acceptance. Constraints: Does not report motor state.
     */
    function native(pattern) { calls.push(pattern); return true; }
    const {doc, win, tester} = setup(native);
    enable(doc);
    doc.buttons[1].dispatchEvent(new Event("click"));
    if (reason === "disabled") {
      tester.enabled.checked = false;
      tester.enabled.dispatchEvent(new Event("change"));
      assert.ok(doc.buttons.every(button => button.disabled));
    } else if (reason === "hidden") {
      doc.visibilityState = "hidden";
      doc.dispatchEvent(new Event("visibilitychange"));
    } else win.dispatchEvent(new Event("pagehide"));
    doc.visibilityState = "visible";
    doc.dispatchEvent(new Event("visibilitychange"));
    win.dispatchEvent(new Event("pagehide"));
    assert.deepEqual(calls, [[800], 0]);
    assert.equal(tester.requested, false);
  }
});

/** Functionality: Verify cancellation rejection is not overwritten by the toggle's success message.
 * Inputs: Native fixture that accepts the first call and rejects cancellation.
 * Outputs: Explicit failure, retained request record, and clickable Stop.
 * Logic: Allow the user to click Stop explicitly after disabling.
 * Constraints: No automatic retry; prior pattern may still be running.
 */
test("Cancellation rejection preserves diagnostics and allows manual stop", function testCancelRejected() {
  const calls = [];
  /** Functionality: Accept a pattern but reject cancellation. Inputs: pattern.
   * Outputs: Nonzero patterns return true and are recorded in calls. Logic: Simulate cancellation failure.
   * Constraints: Not actual device state.
   */
  function native(pattern) { calls.push(pattern); return pattern !== 0; }
  const {doc, tester} = setup(native);
  enable(doc);
  doc.buttons[0].dispatchEvent(new Event("click"));
  tester.enabled.checked = false;
  tester.enabled.dispatchEvent(new Event("change"));
  assert.deepEqual(calls, [[200], 0]);
  assert.equal(tester.requested, true);
  assert.equal(tester.stopButton.disabled, false);
  assert.match(tester.message.textContent, /拒绝/);
  assert.equal(tester.message.dataset.kind, "error");
});

/** Functionality: Verify English dictionary entries for the vibration region and dynamic Chinese messages.
 * Inputs: Actual module source and HTML. Outputs: Assertion that translation leaves no Chinese text.
 * Logic: Check module strings and region text/aria-label without modifying the dictionary or business parameters.
 * Constraints: Static checking does not verify browser MutationObserver behavior, layout, or translation quality.
 */
test("Vibration controls and all dynamic messages have English translations", async function testTranslations() {
  const source = await readFile(new URL("../vibration.js", import.meta.url), "utf8");
  const html = await readFile(new URL("../index.html", import.meta.url), "utf8");
  const section = html.match(/<section id="vibration-test"[\s\S]*?<\/section>/)[0];
  const strings = [...source.matchAll(/"([^"\n]*\p{Script=Han}[^"\n]*)"/gu),
    ...section.matchAll(/>([^<>]*\p{Script=Han}[^<>]*)</gu),
    ...section.matchAll(/aria-label="([^"]+)"/g)].map(match => match[1].trim());
  assert.ok(strings.length > 20);
  for (const text of strings) assert.doesNotMatch(translate(text, "en"), /\p{Script=Han}/u, text);
});

/** Functionality: Verify MCP dispatch reuses the real controller and preserves manual enablement/activation requirements.
 * Inputs: Controller DOM/native fixtures, explicit activation state, and fixed protocol commands.
 * Outputs: State, explicit rejection reasons, pattern, and unique cancellation call sequence.
 * Logic: Do not call native APIs while disabled or inactive; queries have no side effects, and enabled tests use testPattern.
 * Constraints: Verifies command dispatch and state only, not real MCP networking, trusted interaction, or physical motor behavior.
 */
test("MCP query and test share the controller without auto-enable or fabricated activation", function testMcpShared() {
  const calls = [];
  /** Functionality: Capture one test or cancellation. Inputs: pattern.
   * Outputs: true and updated calls. Logic: Explicitly accepting fixture. Constraints: No real hardware.
   */
  function native(pattern) { calls.push(pattern); return true; }
  const {doc, win, tester} = setup(native);
  const status = performVibration("vibration_status", {}, tester);
  assert.equal(status.enabled, false);
  assert.equal(status.hardware_verified, false);
  const disabled = performVibration("vibration_test", {pattern: "short"}, tester);
  assert.equal(disabled.accepted, false);
  assert.equal(disabled.reason, "disabled");
  assert.equal(tester.enabled.checked, false);
  enable(doc);
  win.navigator.userActivation.hasBeenActive = false;
  assert.equal(performVibration("vibration_test", {pattern: "long"}, tester).reason, "activation_required");
  assert.deepEqual(calls, []);
  win.navigator.userActivation.hasBeenActive = true;
  for (const pattern of ["short", "long", "double"]) {
    const result = performVibration("vibration_test", {pattern}, tester);
    assert.equal(result.accepted, true);
    assert.equal(result.pattern, pattern);
    assert.equal(result.reason, null);
    assert.equal(result.hardware_verified, false);
  }
  tester.enabled.checked = false;
  const stopped = performVibration("vibration_stop", {}, tester);
  assert.equal(stopped.accepted, true);
  assert.equal(stopped.request_pending, false);
  assert.equal(stopped.enabled, false);
  assert.deepEqual(calls, [[200], [800], [150, 100, 150], 0]);
});

/** Functionality: Verify structured distinctions among missing page/API, background state, native rejection, and exceptions.
 * Inputs: Controlled API return/exception, document visibility, and actual MCP browser dispatch function.
 * Outputs: Explicit reason and accepted=false; cancellation rejection retains the uncanceled record; no automatic retry.
 * Logic: Change only test boundaries, not production controller logic.
 * Constraints: No operating-system permission verification.
 */
test("MCP reports native rejection and exceptions and preserves failed-cancellation state", function testMcpFailures() {
  assert.equal(performVibration("vibration_status", {}, null).reason, "page_unavailable");
  const unavailable = setup();
  assert.equal(performVibration("vibration_test", {pattern: "short"}, unavailable.tester).reason, "unsupported");
  assert.equal(performVibration("vibration_stop", {}, unavailable.tester).reason, "unsupported");
  let outcome = "accepted";
  const calls = [];
  /** Functionality: Return a preset acceptance, rejection, or exception.
   * Inputs: pattern/outcome. Outputs: Boolean or exception; record one call.
   * Logic: Do not perform actual vibration. Constraints: Native rejection does not prove motor state.
   */
  function native(pattern) {
    calls.push(pattern);
    if (outcome === "native_error") throw new Error("Explicit test error");
    return outcome === "accepted";
  }
  const {doc, tester} = setup(native);
  enable(doc);
  doc.visibilityState = "hidden";
  assert.equal(performVibration("vibration_test", {pattern: "short"}, tester).reason, "page_hidden");
  assert.deepEqual(calls, []);
  doc.visibilityState = "visible";
  performVibration("vibration_test", {pattern: "short"}, tester);
  outcome = "native_rejected";
  const stopped = performVibration("vibration_stop", {}, tester);
  assert.equal(stopped.accepted, false);
  assert.equal(stopped.reason, "native_rejected");
  assert.equal(stopped.request_pending, true);
  const rejected = performVibration("vibration_test", {pattern: "double"}, tester);
  assert.equal(rejected.reason, "native_rejected");
  outcome = "native_error";
  assert.equal(performVibration("vibration_test", {pattern: "long"}, tester).reason, "native_error");
  assert.deepEqual(calls, [[200], 0, [150, 100, 150], [800]]);
  assert.throws(() => performVibration("vibration_loop", {}, tester), /Unknown vibration command/);
});
