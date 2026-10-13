/** Responsibilities: Verify bilingual maps in real Chrome before and after deployment.
 * Implementation: Exercise the actual HTML, Leaflet, controller and MutationObserver; fixture
 * mode mocks only config/route responses and device location. --live-route additionally sends
 * one explicit NUS example to the real PRS/ORS service, without retries or physical GPS access.
 * Route instruction assertions are scoped to #steps; navigation has separate instruction fields.
 * Related Modules: maps.html, maps-page.js, maps-client.js, i18n.js; backend deployment manifest.
 * Declaration Index: fixtureRoute; expectText; collectUntranslated; assertEnglish;
 * switchLanguage; openMaps; denyLocation; checkFixtureFlows; checkLiveRoute; main.
 * Variable Index: args — URL/output/optional real-route flag; report — non-secret evidence;
 * PRS_PLAYWRIGHT_PATH — explicit development-only Playwright package location, read by main.
 * Constraints: Verified TLS and real CDN/SRI loading; synthetic location is not GPS validation.
 */
import assert from "node:assert/strict";
import {createRequire} from "node:module";
import {mkdir, writeFile} from "node:fs/promises";
import {dirname} from "node:path";

const args = process.argv.slice(2);
const report = {complete: false, fixture: {}, pageErrors: [],
  scope: "real Chrome/Leaflet/DOM; synthetic device location and fixture API; optional one real NUS route"};

/** Functionality: Return a valid, explicitly synthetic route with untranslated street text.
 * Inputs: None. Outputs: New PRS route object. Logic: NUS test points and two valid ORS turns.
 * Constraints: The fixture does not prove road existence or real upstream service behavior. */
function fixtureRoute() {
  return {provider: "openrouteservice", profile: "foot-walking",
    geometry: {type: "LineString", coordinates: [[103.7766, 1.2971], [103.7735, 1.2991]]},
    summary: {distance_m: 120, duration_s: 90}, steps: [
      {type: 11, instruction: "Head north on College Avenue", distance_m: 120,
        duration_s: 90, way_points: [0, 1]},
      {type: 10, instruction: "Arrive at University Town", distance_m: 0,
        duration_s: 0, way_points: [1, 1]},
    ], attribution: "© openrouteservice.org by HeiGIT | © OpenStreetMap contributors",
    request_id: "maps-language-fixture"};
}

/** Functionality: Await a rendered UI value, including asynchronous translation.
 * Inputs: page, selector and expected exact normalized text. Outputs: Assertion or timeout.
 * Logic: Observe DOM completion rather than delaying by an arbitrary sleep.
 * Constraints: Bounded eight-second UI observation does not retry any network operation. */
async function expectText(page, selector, expected) {
  await page.waitForFunction(({selector, expected}) =>
    document.querySelector(selector)?.textContent.trim().replace(/\s+/g, " ") === expected,
  {selector, expected}, {timeout: 8000});
}

/** Functionality: Find untranslated Chinese UI text and accessibility attributes.
 * Inputs: Actual page DOM, implicitly read in page.evaluate. Outputs: Text-only omissions.
 * Logic: Inspect individual text nodes and aria-label/title/alt, excluding language names and
 * protected original ORS instructions. Constraints: No coordinates or input values are read. */
function collectUntranslated() {
  const missing = [], walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const node = walker.currentNode;
    if (!node.parentElement.closest("script,style,select,[data-language-original]") &&
        /\p{Script=Han}/u.test(node.nodeValue)) missing.push(node.nodeValue.trim());
  }
  for (const node of document.querySelectorAll("[aria-label],[title],[alt]")) {
    for (const name of ["aria-label", "title", "alt"]) {
      const value = node.getAttribute(name);
      if (value && /\p{Script=Han}/u.test(value)) missing.push(`${name}: ${value}`);
    }
  }
  return missing;
}

/** Functionality: Require complete English UI coverage at the current state.
 * Inputs: page and diagnostic phase. Outputs: Assertion; no mutations or external calls.
 * Logic: Check lang and actual text/attributes. Constraints: Third-party original instructions
 * are excluded intentionally; this does not assess the linguistic quality of external content. */
async function assertEnglish(page, phase) {
  assert.equal(await page.locator("html").getAttribute("lang"), "en", phase);
  assert.deepEqual(await page.evaluate(collectUntranslated), [], phase);
}

/** Functionality: Select a language and await its in-place rendering.
 * Inputs: page and language enum. Outputs: DOM lang assertion.
 * Logic: Use the real select/change event. Constraints: Never reload or restart route planning. */
async function switchLanguage(page, language) {
  await page.locator("#language").selectOption(language);
  await page.waitForFunction(language => document.documentElement.lang === language, language);
}

/** Functionality: Open maps with real module loading and wait for usable controls.
 * Inputs: page and validated URL. Outputs: Ready page or HTTP/UI failure.
 * Logic: DOMContentLoaded then bounded controller readiness; collect uncaught page exceptions.
 * Constraints: Preserve TLS, SRI and production CSP; no network/initialization retry. */
async function openMaps(page, url) {
  page.on("pageerror", error => report.pageErrors.push(error.message));
  const response = await page.goto(url, {waitUntil: "domcontentloaded", timeout: 20000});
  assert.equal(response.status(), 200);
  await page.waitForFunction(() => document.querySelector("#example")?.disabled === false,
    null, {timeout: 15000});
}

/** Functionality: Install a deliberate permission-denied geolocation test boundary.
 * Inputs: Browser geolocation instance. Outputs: Synthetic error from a single invocation.
 * Logic: Override only getCurrentPosition. Constraints: Does not change production source,
 * request real permission or represent an operating-system permission test. */
function denyLocation() {
  Object.defineProperty(navigator.geolocation, "getCurrentPosition", {
    configurable: true, value: (success, failure) => failure({code: 1}),
  });
}

/** Functionality: Verify success, errors, mobile layout and persisted cross-page preferences.
 * Inputs: browser, maps URL and output prefix. Outputs: Evidence and English screenshot.
 * Logic: Control only API responses and device position; run real UI/controller code, cycle
 * languages with existing results and prove that no new route request occurs when switching;
 * compare the #steps provider list independently from the navigation panel's current/next fields.
 * Constraints: No real ORS/GPS calls in fixture mode; failures remain explicit and are not retried. */
async function checkFixtureFlows(browser, url, output) {
  const context = await browser.newContext({locale: "en-US", viewport: {width: 390, height: 844},
    geolocation: {longitude: 103.7766, latitude: 1.2971, accuracy: 12}, permissions: ["geolocation"]});
  let requests = 0, failRoute = false;
  try {
    await context.route("**/maps/config/", route => route.fulfill({json: {
      provider: "openrouteservice", profile: "foot-walking", configured: true, code: "ready"}}));
    await context.route("**/maps/route/", route => {
      requests++;
      assert.deepEqual(route.request().postDataJSON(), {
        start: [103.7766, 1.2971], end: [103.7735, 1.2991]});
      return route.fulfill(failRoute ? {status: 429, json: {
        detail: "ORS 配额已达到上限，请检查账号额度。", code: "ors_quota", request_id: "fixture-error-id"}}
        : {json: fixtureRoute()});
    });
    const page = await context.newPage();
    await openMaps(page, url);
    await expectText(page, "#maps-status", "ORS configured");
    await assertEnglish(page, "initial system-English page");
    await page.locator("#map").click({position: {x: 120, y: 150}});
    await expectText(page, "#maps-status", "Ready to plan");
    await assertEnglish(page, "map destination selected");
    await page.locator("#locate").click();
    await expectText(page, "#location-source", "Current device location");
    await expectText(page, "#accuracy", "About 12 m");
    await assertEnglish(page, "synthetic location acquired");
    await page.locator("#start-latitude").fill("1.2971");
    await expectText(page, "#location-source", "Manually entered start");
    await page.locator("#example").click();
    await expectText(page, "#location-source", "Fixed NUS example (not device location)");
    await page.locator("#plan").click();
    await expectText(page, "#route-summary", "120 m · About 2 min · 2 instructions");
    await expectText(page, "#steps strong", "Depart · 120 m");
    const originals = await page.locator("#steps [data-language-original]").allTextContents();
    assert.deepEqual(originals, fixtureRoute().steps.map(step => step.instruction));
    await assertEnglish(page, "route results");
    // Leaflet circleMarker renders an SVG path, so observe its real event target.
    await page.locator(".leaflet-overlay-pane path.leaflet-interactive").last().dispatchEvent("mouseover");
    await expectText(page, ".leaflet-tooltip", "Route destination");
    await switchLanguage(page, "zh-CN");
    await expectText(page, "#route-summary", "120 米 · 约 2 分钟 · 2 条指令");
    await expectText(page, "#steps strong", "出发 · 120 米");
    await expectText(page, ".leaflet-tooltip", "路线终点");
    await switchLanguage(page, "en");
    await expectText(page, "#route-summary", "120 m · About 2 min · 2 instructions");
    assert.deepEqual(await page.locator("#steps [data-language-original]").allTextContents(), originals);
    assert.equal(requests, 1, "switching an existing route must not call ORS again");
    await assertEnglish(page, "existing route and marker after language cycle");
    assert.equal(await page.evaluate(() =>
      document.documentElement.scrollWidth <= window.innerWidth), true, "mobile horizontal overflow");
    await page.screenshot({path: output + ".png", fullPage: true});
    failRoute = true;
    await page.locator("#plan").click();
    await expectText(page, "#maps-status", "Incomplete");
    assert.match(await page.locator("#maps-message").textContent(),
      /ORS quota exhausted.*ors_quota.*fixture-error-id/);
    assert.equal(await page.locator("#steps li").count(), 0);
    await assertEnglish(page, "route failure with preserved diagnostic identifiers");
    await switchLanguage(page, "zh-CN");
    assert.match(await page.locator("#maps-message").textContent(), /ORS 配额已达到上限/);
    await switchLanguage(page, "en");
    assert.equal(requests, 2, "error translation must not retry the route");
    await page.evaluate(denyLocation);
    await page.locator("#locate").click();
    await expectText(page, "#maps-message", "Location permission denied. Allow location in site permissions.");
    await assertEnglish(page, "location permission denied");
    const camera = new URL("../live/", url).href;
    await page.goto(camera, {waitUntil: "domcontentloaded"});
    await page.waitForFunction(() => document.getElementById("language")?.value === "en");
    assert.equal(await page.locator("html").getAttribute("lang"), "en");
    await openMaps(page, url);
    assert.equal(await page.locator("#language").inputValue(), "en");
    await assertEnglish(page, "preference retained across camera/maps navigation");
    report.fixture = {passed: true, routeRequests: requests, viewport: "390x844",
      screenshot: output + ".png", languageCycle: "en -> zh-CN -> en",
      checks: ["static text and accessibility", "selected destination", "synthetic location/accuracy",
        "manual source", "route summary/turn labels", "marker tooltip", "original ORS text preserved",
        "route error identifiers", "permission denial", "cross-page preference", "mobile overflow"]};
  } finally { await context.close(); }
}

/** Functionality: Verify one real public NUS route and translation of its existing result.
 * Inputs: browser and maps URL. Outputs: Public route count/status/summary evidence.
 * Logic: No API mocks; click the explicitly labeled example, submit once, then change display.
 * Constraints: Failure is reported without retry or recovery; no physical location or GPU access. */
async function checkLiveRoute(browser, url) {
  const context = await browser.newContext({locale: "en-US"});
  try {
    const page = await context.newPage();
    let requests = 0;
    page.on("request", request => { if (new URL(request.url()).pathname.endsWith("/maps/route/")) requests++; });
    await openMaps(page, url);
    await expectText(page, "#maps-status", "ORS configured");
    await page.locator("#example").click();
    await page.locator("#plan").click();
    await page.waitForFunction(() => ["Route received", "Incomplete"].includes(
      document.getElementById("maps-status").textContent.trim()), null, {timeout: 30000});
    assert.equal(await page.locator("#maps-status").textContent(), "Route received",
      await page.locator("#maps-message").textContent());
    const summary = await page.locator("#route-summary").textContent();
    const count = await page.locator("#steps li").count();
    assert.ok(count > 0);
    await assertEnglish(page, "real ORS result");
    await switchLanguage(page, "zh-CN");
    assert.match(await page.locator("#route-summary").textContent(), /米.*分钟.*条指令/);
    await switchLanguage(page, "en");
    assert.equal(await page.locator("#route-summary").textContent(), summary);
    await assertEnglish(page, "real route after switching");
    assert.equal(requests, 1);
    report.live = {passed: true, routeRequests: requests, summary, steps: count};
  } finally { await context.close(); }
}

/** Functionality: Run explicitly requested browser checks and save complete/failure evidence.
 * Inputs: CLI URL/output/[--live-route], installed Chrome and PRS_PLAYWRIGHT_PATH.
 * Outputs: JSON report, fixture screenshot and process exit status; browser always closes.
 * Logic: Reject credentials/unsafe targets, run fixture coverage, then optionally one real route.
 * Constraints: Does not install packages, bypass TLS, retry APIs or alter production settings. */
async function main() {
  if (args.length < 2 || args.length > 3 || args.length === 3 && args[2] !== "--live-route")
    throw new Error("Usage: node tools/check_maps_browser.mjs MAPS_URL OUTPUT [--live-route]");
  const [url, output] = args, parsed = new URL(url);
  if (parsed.username || parsed.password || parsed.search || parsed.hash ||
      !parsed.pathname.endsWith("/maps/") || !(parsed.protocol === "https:" ||
      parsed.protocol === "http:" && ["127.0.0.1", "localhost"].includes(parsed.hostname)))
    throw new Error("Use a credential-free HTTPS or explicit loopback maps URL");
  const packagePath = process.env.PRS_PLAYWRIGHT_PATH;
  if (!packagePath) throw new Error("Set PRS_PLAYWRIGHT_PATH to the explicit test runtime package");
  await mkdir(dirname(output), {recursive: true});
  const {chromium} = createRequire(import.meta.url)(packagePath);
  const browser = await chromium.launch({channel: "chrome", headless: true});
  report.browser = browser.version(); report.url = url;
  try {
    await checkFixtureFlows(browser, url, output);
    if (args[2] === "--live-route") await checkLiveRoute(browser, url);
    assert.deepEqual(report.pageErrors, []);
    report.complete = true;
  } catch (error) {
    report.error = error.message;
    process.exitCode = 1;
  } finally {
    await browser.close();
    await writeFile(output, JSON.stringify(report, null, 2) + "\n");
    console.log(JSON.stringify(report));
  }
}

await main();
