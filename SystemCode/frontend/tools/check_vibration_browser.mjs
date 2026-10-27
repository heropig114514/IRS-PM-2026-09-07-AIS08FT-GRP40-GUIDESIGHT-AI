/** Responsibilities: Verify manual vibration UI, language switching, and mobile layout in real Chrome.
 * Implementation: Retain real HTML/CSS/modules/CSP; explicitly replace native vibrate and media-permission boundaries to control acceptance/rejection/errors.
 * Related Modules: index.html, vibration.js, camera.css, i18n.js, translations.js。
 * Declaration Index: installFixture; installFixture.vibrate; installFixture.denyCamera;
 * expectText; assertLayout; collectUntranslated; checkSupported; checkUnavailable; main。
 * Variable Index: None; main reads the development runtime from PRS_PLAYWRIGHT_PATH; report stores non-sensitive assertions and screenshot paths.
 * Constraints: No real motor, GPS, camera, HF, or GPU verification; do not disable TLS/CSP, retry network calls, or publish.
 */
import assert from "node:assert/strict";
import {createRequire} from "node:module";
import {mkdir, writeFile} from "node:fs/promises";
import {dirname} from "node:path";

/** Functionality: Install explicit browser API fixtures before loading the module.
 * Inputs: supported controls whether the native API exists; browser navigator/window.
 * Outputs: Request/media counters and configurable outcome in __vibrationFixture.
 * Logic: Replace only vibrate and getUserMedia; real browser code still handles DOM, user clicks, and translations.
 * Constraints: Returning true simulates acceptance and proves no hardware state; unexpected media requests fail without requesting permission.
 */
function installFixture(supported) {
  window.__vibrationFixture = {calls: [], cameraRequests: 0, outcome: "accept"};
  /** Functionality: Record the API receiver and return the configured test outcome.
   * Inputs: pattern and __vibrationFixture.outcome. Outputs: Boolean or NotAllowedError.
   * Logic: Verify navigator as the receiver, copy fixed arrays, and follow explicit fixture state.
   * Constraints: This is not the native API and does not access the system motor.
   */
  function vibrate(pattern) {
    if (this !== navigator) throw new TypeError("Incorrect vibration receiver");
    const state = window.__vibrationFixture;
    state.calls.push(Array.isArray(pattern) ? [...pattern] : pattern);
    if (state.outcome === "throw") throw new DOMException("Synthetic vibration rejection", "NotAllowedError");
    return state.outcome === "accept";
  }
  Object.defineProperty(navigator, "vibrate", {configurable: true, value: supported ? vibrate : undefined});
  /** Functionality: Detect unexpected media-permission requests.
   * Inputs: Implicit calls to navigator.mediaDevices. Outputs: Counter increment and explicit exception.
   * Logic: Reject requests so the manual-vibration regression cannot access sensors.
   * Constraints: Does not verify real camera permissions.
   */
  function denyCamera() {
    window.__vibrationFixture.cameraRequests++;
    throw new Error("Vibration UI unexpectedly requested camera access");
  }
  if (navigator.mediaDevices) Object.defineProperty(navigator.mediaDevices, "getUserMedia", {
    configurable: true, value: denyCamera,
  });
}

/** Functionality: Wait for expected text to appear in the real DOM.
 * Inputs: page/selector/text. Outputs: Bounded wait or timeout.
 * Logic: Wait for the translation observer instead of using a fixed delay.
 * Constraints: Do not retry business requests.
 */
async function expectText(page, selector, text) {
  await page.waitForFunction(({selector, text}) => document.querySelector(selector)?.textContent.trim() === text,
    {selector, text}, {timeout: 5000});
}

/** Functionality: Check for horizontal overflow and button touch-target size at the current width.
 * Inputs: page and current viewport. Outputs: DOM layout assertions.
 * Logic: Read page width and button rectangles from the actual layout.
 * Constraints: Does not equal physical phone touch testing.
 */
async function assertLayout(page) {
  const layout = await page.evaluate(() => ({width: innerWidth, contentWidth: document.documentElement.scrollWidth,
    buttons: [...document.querySelectorAll(".vibration-actions button")].map(button => {
      const bounds = button.getBoundingClientRect();
      return {height: bounds.height, left: bounds.left, right: bounds.right};
    })}));
  assert.ok(layout.contentWidth <= layout.width, JSON.stringify(layout));
  for (const button of layout.buttons) {
    assert.ok(button.height >= 44);
    assert.ok(button.left >= 0 && button.right <= layout.width);
  }
}

/** Functionality: Check English translation completeness for the vibration region.
 * Inputs: Browser DOM. Outputs: List of remaining Chinese text/accessibility attributes.
 * Logic: Traverse actual text nodes and aria-label values.
 * Constraints: Do not read camera images or user input.
 */
function collectUntranslated() {
  const root = document.getElementById("vibration-test"), missing = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) if (/\p{Script=Han}/u.test(walker.currentNode.nodeValue))
    missing.push(walker.currentNode.nodeValue.trim());
  for (const node of root.querySelectorAll("[aria-label]"))
    if (/\p{Script=Han}/u.test(node.getAttribute("aria-label"))) missing.push(node.getAttribute("aria-label"));
  return missing;
}

/** Functionality: Verify real controls and rejection/error states when the API is available.
 * Inputs: browser/url/output/report. Outputs: Request sequence, layout, languages, proof of no media calls, and screenshots.
 * Logic: Load the module in an isolated English 390px context; click real controls, switch languages, and control native-fixture outcomes.
 * Constraints: Hardware remains unverified; this covers page loading and user interaction only, not detection or publication.
 */
async function checkSupported(browser, url, output, report) {
  const context = await browser.newContext({locale: "en-US", viewport: {width: 390, height: 844}});
  try {
    await context.addInitScript(installFixture, true);
    const page = await context.newPage();
    page.on("pageerror", error => report.pageErrors.push(error.message));
    const response = await page.goto(url, {waitUntil: "networkidle", timeout: 20000});
    assert.equal(response.status(), 200);
    await expectText(page, "#vibration-status", "API available");
    assert.equal(await page.locator("#vibration-enabled").isChecked(), false);
    assert.equal(await page.locator('[data-vibration-pattern="short"]').isDisabled(), true);
    assert.deepEqual(await page.evaluate(() => window.__vibrationFixture.calls), []);
    await page.locator("#vibration-enabled").check();
    assert.deepEqual(await page.evaluate(() => window.__vibrationFixture.calls), []);
    for (const name of ["short", "long", "double"]) await page.locator(`[data-vibration-pattern="${name}"]`).click();
    await expectText(page, "#vibration-message", "Vibration request accepted. Confirm the actual vibration on your phone; the API response does not verify hardware.");
    assert.deepEqual(await page.evaluate(() => window.__vibrationFixture.calls), [[200], [800], [150, 100, 150]]);
    assert.deepEqual(await page.evaluate(collectUntranslated), []);
    await assertLayout(page);
    await page.locator("#vibration-test").screenshot({path: output + ".mobile-en.png"});
    await page.locator("#language").selectOption("zh-CN");
    await expectText(page, "#vibration-title", "手机震动测试");
    await expectText(page, "#vibration-message", "震动请求已接受，请在手机上确认实际震动；接口返回值不能验证硬件。");
    await page.locator("#vibration-stop").click();
    await expectText(page, "#vibration-message", "已发送停止震动请求。");
    assert.equal(await page.evaluate(() => window.__vibrationFixture.calls.at(-1)), 0);
    await page.locator('[data-vibration-pattern="long"]').click();
    await page.locator("#vibration-enabled").uncheck();
    await expectText(page, "#vibration-message", "震动测试已关闭，已发送停止请求。");
    assert.deepEqual(await page.evaluate(() => window.__vibrationFixture.calls.slice(-2)), [[800], 0]);
    await page.locator("#vibration-enabled").check();
    await page.evaluate(() => { window.__vibrationFixture.outcome = "reject"; });
    await page.locator('[data-vibration-pattern="short"]').click();
    await expectText(page, "#vibration-message", "浏览器拒绝了震动请求。请检查页面交互和系统设置后手动再试。");
    await page.evaluate(() => { window.__vibrationFixture.outcome = "throw"; });
    await page.locator('[data-vibration-pattern="short"]').click();
    await expectText(page, "#vibration-message", "震动调用异常，请查看浏览器控制台后手动再试。");
    await page.setViewportSize({width: 320, height: 740});
    await assertLayout(page);
    await page.locator("#vibration-test").screenshot({path: output + ".mobile-zh.png"});
    await page.locator("#language").selectOption("en");
    await expectText(page, "#vibration-message", "The vibration call failed. Check the browser console, then try again manually.");
    assert.deepEqual(await page.evaluate(collectUntranslated), []);
    await assertLayout(page);
    await page.setViewportSize({width: 1280, height: 900});
    await assertLayout(page);
    const state = await page.evaluate(() => window.__vibrationFixture);
    assert.equal(state.cameraRequests, 0);
    assert.equal(state.calls.length, 8);
    report.supported = {calls: state.calls, cameraRequests: state.cameraRequests,
      layoutWidths: [390, 320, 1280], languages: ["en", "zh-CN"], rejectedAndThrownCalls: true};
  } finally { await context.close(); }
}

/** Functionality: Verify UI boundaries and translations when the API is unavailable.
 * Inputs: browser/url/report. Outputs: All controls disabled, support message, and zero-call assertions.
 * Logic: Load the same page in a new context that explicitly omits vibrate.
 * Constraints: Not a test on a real Safari device.
 */
async function checkUnavailable(browser, url, report) {
  const context = await browser.newContext({locale: "en-US", viewport: {width: 390, height: 844}});
  try {
    await context.addInitScript(installFixture, false);
    const page = await context.newPage();
    page.on("pageerror", error => report.pageErrors.push(error.message));
    assert.equal((await page.goto(url, {waitUntil: "networkidle", timeout: 20000})).status(), 200);
    await expectText(page, "#vibration-status", "API unavailable");
    assert.equal(await page.locator("#vibration-enabled").isDisabled(), true);
    for (const button of await page.locator(".vibration-actions button").all()) assert.equal(await button.isDisabled(), true);
    assert.deepEqual(await page.evaluate(collectUntranslated), []);
    const state = await page.evaluate(() => window.__vibrationFixture);
    assert.deepEqual(state.calls, []);
    assert.equal(state.cameraRequests, 0);
    report.unavailable = {allControlsDisabled: true, calls: [], cameraRequests: 0};
  } finally { await context.close(); }
}

/** Functionality: Run a browser regression with explicit boundaries and save evidence.
 * Inputs: CLI URL/OUTPUT, PRS_PLAYWRIGHT_PATH, and installed Chrome.
 * Outputs: JSON/PNG and exit code; after browser startup, write failures to JSON and close the browser.
 * Logic: Accept only credential-free HTTPS or explicit loopback URLs; verify both supported and unavailable cases.
 * Constraints: Output states that hardware is unverified; do not modify production dependencies, TLS, or page source; do not retry automatically.
 */
async function main() {
  const [url, output] = process.argv.slice(2), runtime = process.env.PRS_PLAYWRIGHT_PATH;
  if (!url || !output || !runtime) throw new Error("Use URL OUTPUT and set PRS_PLAYWRIGHT_PATH");
  const parsed = new URL(url);
  if (parsed.username || parsed.password || parsed.search || parsed.hash ||
      !(parsed.protocol === "https:" || parsed.protocol === "http:" && ["127.0.0.1", "localhost"].includes(parsed.hostname)))
    throw new Error("Use a credential-free HTTPS or explicit loopback URL");
  await mkdir(dirname(output), {recursive: true});
  const report = {complete: false, pageErrors: [], hardwareVerified: false,
    scope: "Actual Chrome/DOM/CSS/i18n; explicit vibration and media API fixtures; no phone hardware verification"};
  const {chromium} = createRequire(import.meta.url)(runtime);
  const browser = await chromium.launch({channel: "chrome", headless: true});
  try {
    await checkSupported(browser, url, output, report);
    await checkUnavailable(browser, url, report);
    assert.deepEqual(report.pageErrors, []);
    report.complete = true;
  } catch (error) {
    report.error = {name: error.name, message: error.message};
    throw error;
  } finally {
    await writeFile(output, JSON.stringify(report, null, 2) + "\n");
    await browser.close();
  }
  console.info(JSON.stringify(report));
}

await main();
