/* Responsibilities: Verify semantic navigation respects embedded MVP and standard page routes.
 * Implementation: Exercise the exported route resolver with focused DOM fixtures.
 * Related Modules: ai-control.js; check_assist_browser.mjs covers the live pages.
 * Declaration Index: None (test callbacks are anonymous).
 * Variable Index: None; fixtures are scoped to individual tests. */
import assert from "node:assert/strict";
import test from "node:test";
import {navigatePage} from "../ai-control.js";

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
