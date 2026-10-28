/* Responsibilities: Verify embedded navigation routes and shared speech-control dispatch.
 * Implementation: Exercise exported controls with focused DOM/controller fixtures.
 * Related Modules: ai-control.js; check_assist_browser.mjs covers the live pages.
 * Declaration Index: None (test callbacks are anonymous).
 * Variable Index: None; fixtures are scoped to individual tests. */
import assert from "node:assert/strict";
import test from "node:test";
import {navigatePage, performAction, performSpeech} from "../ai-control.js";

// Functionality: Allow explicit agent requests to operate application settings and camera controls.
// Inputs: Synthetic checkbox/button DOM controls and one assistant-submit request. Outputs: updated
// checkbox state or click dispatch; Logic: exercise real ui_action validation and snapshots.
// Constraints: browser permissions and trusted activation are not mocked or granted by this fixture.
test("agent UI actions can change requested output settings and invoke camera controls", async t => {
  const previousCSS = Object.getOwnPropertyDescriptor(globalThis, "CSS");
  const previousComputedStyle = Object.getOwnPropertyDescriptor(globalThis, "getComputedStyle");
  Object.defineProperty(globalThis, "CSS", {configurable: true, value: {escape: value => value}});
  Object.defineProperty(globalThis, "getComputedStyle", {configurable: true,
    value: () => ({visibility: "visible"})});
  t.after(() => {
    if (previousCSS) Object.defineProperty(globalThis, "CSS", previousCSS);
    else delete globalThis.CSS;
    if (previousComputedStyle) Object.defineProperty(globalThis, "getComputedStyle", previousComputedStyle);
    else delete globalThis.getComputedStyle;
  });

  for (const [id, type, action] of [
    ["speech-enabled", "checkbox", "check"],
    ["vibration-enabled", "checkbox", "check"],
    ["navigation-speech-enabled", "checkbox", "check"],
    ["assist-risk-enabled", "checkbox", "check"],
    ["product-camera-start", "button", "click"],
    ["start", "button", "click"],
    ["assist-devices-authorize", "button", "click"],
  ]) {
    const events = [];
    let clicks = 0;
    const element = {
      id, type, tagName: type === "checkbox" ? "INPUT" : "BUTTON",
      disabled: false, readOnly: false, checked: false, value: "camera-1", innerText: "",
      labels: [], options: [{value: "camera-1", disabled: false}],
      closest: () => null, getClientRects: () => [{}], getBoundingClientRect: () => ({x: 0, y: 0, width: 40, height: 24}),
      getAttribute: () => null, matches: selector => selector === ":disabled" && element.disabled,
      scrollIntoView() {}, click() { clicks++; }, dispatchEvent(event) { events.push(event.type); },
    };
    if (type === "checkbox") element.getClientRects = () => [];
    const doc = {
      location: {href: "https://example.test/mvp/maps/"}, title: "Assist",
      documentElement: {lang: "en", dataset: {}}, body: {innerText: ""},
      querySelector: () => null,
      querySelectorAll: selector => selector === `#${id}` ||
        selector === "button,a[href],input,select,textarea,[role=button],[contenteditable=true]" ||
        selector === "[role=status],[aria-live],.status" ? [element] : [],
    };
    element.ownerDocument = doc;
    const args = action === "check" ? {action, selector: `#${id}`, value: true} :
      {action, selector: `#${id}`};
    const result = await performAction("agent_ui_action", args, doc);
    assert.equal(result.controls[0].selector, `#${id}`);
    if (action === "check") {
      assert.equal(result.controls[0].checked, true);
      assert.deepEqual(events, ["input", "change"]);
    } else assert.equal(clicks, 1);
  }
});

// Functionality: Keep hidden non-output application controls unavailable to agent UI actions.
// Inputs: One hidden synthetic stop button and its document. Outputs: Visibility rejection.
// Logic: Exercise the ordinary hidden-control guard outside the explicit output-switch allowlist.
// Constraints: The fixture does not execute a real application action.
test("agent cannot operate hidden non-output controls", async () => {
  const previousCSS = Object.getOwnPropertyDescriptor(globalThis, "CSS");
  const previousComputedStyle = Object.getOwnPropertyDescriptor(globalThis, "getComputedStyle");
  Object.defineProperty(globalThis, "CSS", {configurable: true, value: {escape: value => value}});
  Object.defineProperty(globalThis, "getComputedStyle", {configurable: true,
    value: () => ({visibility: "visible"})});
  try {
    const element = {id: "hidden-stop", type: "button", tagName: "BUTTON", disabled: false,
      readOnly: false, innerText: "", labels: [], options: [], closest: () => null,
      getClientRects: () => [], matches: selector => selector === ":disabled" && element.disabled};
    const doc = {location: {href: "https://example.test/mvp/maps/"}, title: "Assist",
      documentElement: {lang: "en", dataset: {}}, body: {innerText: ""},
      querySelector: () => null,
      querySelectorAll: selector => selector === "#hidden-stop" ? [element] : []};
    element.ownerDocument = doc;
    await assert.rejects(performAction("agent_ui_action", {action: "click", selector: "#hidden-stop"}, doc),
      /Control is hidden or disabled/);
  } finally {
    if (previousCSS) Object.defineProperty(globalThis, "CSS", previousCSS);
    else delete globalThis.CSS;
    if (previousComputedStyle) Object.defineProperty(globalThis, "getComputedStyle", previousComputedStyle);
    else delete globalThis.getComputedStyle;
  }
});

// Functionality: Keep the assistant panel outside the set of application controls the agent can operate.
// Inputs: One assistant-owned synthetic control/document. Outputs: Explicit rejection.
// Logic: Verify the ownership guard before any DOM mutation. Constraints: No real page or request.
test("agent cannot manipulate its own assistant controls", async () => {
  const element = {id: "assistant-send", closest: selector => selector === "#agent-panel" ? {} : null};
  const doc = {querySelectorAll: selector => selector === "#assistant-send" ? [element] : []};
  await assert.rejects(performAction("agent_ui_action", {action: "click", selector: "#assistant-send"}, doc),
    /Assistant-owned controls cannot be manipulated/);
});

// Functionality: Focus an available embedded camera without requesting a route change.
// Inputs: MVP document fixture and configured base URL; Outputs: same-page section result.
// Logic: Record the scroll invocation and inspect the structured acknowledgement.
// Constraints: Does not emulate camera capture or browser scrolling.
test("unified assist camera navigation stays on the current page", () => {
  let scrolls = 0;
  const panel = {hidden: false, scrollIntoView: options => {
    assert.deepEqual(options, {block: "start"});
    scrolls += 1;
  }};
  const doc = {documentElement: {dataset: {assist: "true"}}, querySelector: () => panel};
  assert.deepEqual(navigatePage("camera", doc, new URL("https://example.test/mvp/maps/")), {
    same_page: true, available: true, page: "camera", section: "#assist-camera-panel",
  });
  assert.equal(scrolls, 1);
});

// Functionality: Refuse an unavailable embedded panel without creating a broken destination.
// Inputs: Assist document with a hidden camera section; Outputs: explicit capability reason.
// Logic: Check the existing hidden state and ensure it remains unchanged.
// Constraints: Does not switch perception source or start a device.
test("unified assist reports a hidden camera panel instead of navigating to live/", () => {
  const panel = {hidden: true, scrollIntoView() { assert.fail("hidden panel must not scroll"); }};
  const doc = {documentElement: {dataset: {assist: "true"}}, querySelector: () => panel};
  assert.deepEqual(navigatePage("camera", doc, new URL("https://example.test/mvp/maps/")), {
    same_page: true, available: false, reason: "camera_panel_hidden",
  });
  assert.equal(panel.hidden, true);
});

// Functionality: Report a broken template capability without fabricating a page destination.
// Inputs: Assist document with no camera panel; Outputs: page_unavailable result.
// Logic: Keep the current route and avoid side effects when the expected node is absent.
// Constraints: Does not inspect a deployed template.
test("unified assist reports a missing camera panel as unavailable", () => {
  const doc = {documentElement: {dataset: {assist: "true"}}, querySelector: () => null};
  assert.deepEqual(navigatePage("camera", doc, new URL("https://example.test/mvp/maps/")), {
    same_page: true, available: false, reason: "page_unavailable",
  });
});

// Functionality: Preserve the standalone PRS camera and maps route contract.
// Inputs: Standard page fixture and mount URL; Outputs: explicit route destination.
// Logic: Resolve the same relative routes used before embedded assist was added.
// Constraints: Does not load either destination in a browser.
test("standard PRS pages retain their existing routes", () => {
  const doc = {documentElement: {dataset: {}}};
  const base = new URL("https://example.test/prs/");
  assert.equal(navigatePage("camera", doc, base).navigating_to, "https://example.test/prs/live/");
  assert.equal(navigatePage("maps", doc, base).navigating_to, "https://example.test/prs/maps/");
});

// Functionality: Reuse the product page's existing local voice for a natural-language read request.
// Inputs: Local-only controller with one selected voice and a speech request without voice_id.
// Outputs: One controller submission carrying that exact voice identity.
// Logic: Inspect the option already selected by the page; no voice inventory or replacement is used.
// Constraints: Does not enable output, create trusted activation, or emulate audible playback.
test("speech request inherits the exact selected voice on a local-only page", () => {
  let submitted;
  const controller = {localOnly: true, voice: {value: "windows-local-zh"},
    speak: (text, options) => (submitted = {text, options})};
  performSpeech("speech_speak", {text: "测试朗读", language: "zh-CN"}, controller);
  assert.deepEqual(submitted, {text: "测试朗读", options: {
    language: "zh-CN", voice_id: "windows-local-zh", rate: 1, volume: 1,
  }});
});

// Functionality: Preserve an explicit voice supplied by the user or agent.
// Inputs: Local-only controller, requested voice and selected page voice.
// Outputs: Submission uses the explicit requested voice.
// Logic: Caller choice takes precedence over the page's current selection.
// Constraints: Does not substitute if the caller's voice is unavailable; controller validates it.
test("explicit speech voice is never replaced by the page selection", () => {
  let submitted;
  const controller = {localOnly: true, voice: {value: "windows-local-zh"},
    speak: (text, options) => (submitted = {text, options})};
  performSpeech("speech_speak", {text: "测试朗读", language: "zh-CN", voice_id: "requested-voice"}, controller);
  assert.equal(submitted.options.voice_id, "requested-voice");
});

// Functionality: Preserve the standard page's browser-default voice behavior when no choice exists.
// Inputs: Non-local-only controller and speech request without voice_id.
// Outputs: Submission retains null so the existing controller selects its browser default.
// Logic: The local-only override is deliberately limited to pages that already enforce it.
// Constraints: Does not inspect or mutate a standalone page's selector.
test("standard speech request retains its browser-default voice behavior", () => {
  let submitted;
  const controller = {localOnly: false, voice: {value: "ignored-selection"},
    speak: (text, options) => (submitted = {text, options})};
  performSpeech("speech_speak", {text: "测试朗读", language: "zh-CN"}, controller);
  assert.equal(submitted.options.voice_id, null);
});
