/** Responsibilities: Verify vibration MCP commands across real HTTP, WebSocket and Chrome.
 * Implementation: Use a dedicated tab and official Python MCP client; only native vibration and
 * media APIs are fixtures. Compare synthetic enable events with actual trusted browser clicks.
 * Related Modules: SystemCode/backend/tools/check_vibration_mcp.py; ai-control.js; vibration.js; MCP.md.
 * Declaration Index: installFixture; installFixture.vibrate; installFixture.denyCamera;
 * runClient; runClient.completion; runClient.completion.exited; main.
 * Variable Index: None; PRS_PLAYWRIGHT_PATH/PYTHON are explicit development runtimes;
 * main browser/context/page/cdp/session/report belong only to this disposable verification;
 * pre-interaction Runtime.evaluate explicitly sets userGesture=false because Playwright's
 * ordinary page evaluation grants activation and would invalidate the prerequisite check.
 * Constraints: No database writes, GPU, GPS, physical motor or deployment; no TLS bypass/retry.
 */
import assert from "node:assert/strict";
import {createRequire} from "node:module";
import {spawn} from "node:child_process";
import {readFile, writeFile, mkdir} from "node:fs/promises";
import {dirname} from "node:path";

/** Functionality: Install explicit native-device test boundaries before modules load.
 * Inputs: supported determines whether navigator.vibrate exists; actual browser userActivation.
 * Outputs: Mutable __vibrationFixture calls/outcome/cameraRequests and overridden device methods.
 * Logic: Simulate native accept/refuse/throw only; preserve real browser activation and DOM events.
 * Constraints: Does not vibrate physical hardware or grant activation/media permissions.
 */
function installFixture(supported) {
  window.__vibrationFixture = {calls: [], outcome: "accept", cameraRequests: 0};
  /** Functionality: Record a single native request with correct navigator receiver.
   * Inputs: pattern/current fixture outcome. Outputs: Boolean or explicit synthetic exception.
   * Logic: Store fixed request then return the selected outcome. Constraints: No motor access.
   */
  function vibrate(pattern) {
    if (this !== navigator) throw new TypeError("Incorrect navigator receiver");
    const state = window.__vibrationFixture;
    state.calls.push(Array.isArray(pattern) ? [...pattern] : pattern);
    if (state.outcome === "throw") throw new DOMException("Synthetic native failure", "NotAllowedError");
    return state.outcome === "accept";
  }
  Object.defineProperty(navigator, "vibrate", {configurable: true, value: supported ? vibrate : undefined});
  /** Functionality: Detect unexpected camera requests without authorizing a sensor.
   * Inputs: Implicit getUserMedia call. Outputs: Incremented count and exception.
   * Logic: Fail immediately. Constraints: No camera capture or permission verification.
   */
  function denyCamera() {
    window.__vibrationFixture.cameraRequests++;
    throw new Error("Vibration MCP unexpectedly requested camera access");
  }
  if (navigator.mediaDevices) Object.defineProperty(navigator.mediaDevices, "getUserMedia", {
    configurable: true, value: denyCamera,
  });
}

/** Functionality: Run one official-client verification against an explicitly chosen tab.
 * Inputs: Camera url, output prefix, session identity and controlled scenario.
 * Outputs: Parsed JSON evidence; failure retains the child diagnostics and is not repeated.
 * Logic: Spawn the backend Python checker with fixed arguments and bounded HTTP scenario work.
 * Constraints: No shell interpolation, hidden window only, no database or browser setup here.
 */
async function runClient(url, output, session, scenario) {
  const path = output + "." + scenario + ".json";
  const child = spawn(process.env.PYTHON, ["-m", "tools.check_vibration_mcp",
    "--url", new URL("../mcp", url).href, "--session", session,
    "--scenario", scenario, "--output", path],
  {cwd: new URL("../../backend/", import.meta.url), stdio: "inherit", windowsHide: true});
  /** Functionality: Register process completion and launch failure.
   * Inputs: resolve/reject and child process. Outputs: Settled Promise.
   * Logic: Observe one exit event. Constraints: No process relaunch or retry.
   */
  function completion(resolve, reject) {
    child.once("error", reject);
    /** Functionality: Convert the checker exit code into explicit completion/failure.
     * Inputs: code. Outputs: resolve or Error. Logic: Zero alone means scenario passed.
     * Constraints: Does not infer native hardware success from process completion.
     */
    function exited(code) {
      if (code === 0) resolve();
      else reject(new Error("Vibration MCP checker failed with exit " + code));
    }
    child.once("exit", exited);
  }
  await new Promise(completion);
  const report = JSON.parse(await readFile(path, "utf8"));
  assert.equal(report.complete, true);
  return report;
}

/** Functionality: Exercise MCP prerequisites, fixed requests and native failures in real Chrome.
 * Inputs: CLI CAMERA_URL/OUTPUT; PRS_PLAYWRIGHT_PATH/PYTHON; an already running single-worker ASGI.
 * Outputs: JSON evidence from six scenarios and exact native request/zero-camera counters.
 * Logic: Read/session setup and synthetic enabling use CDP userGesture=false; query and test
 * while disabled, then use real
 * trusted clicks before explicit MCP test/stop calls. A separate context omits the native API.
 * Constraints: No sensor, motor, database mutation, route planning or inference; fixtures never
 * replace activation. Always close this checker browser, preserve errors and never retry.
 */
async function main() {
  const [url, output] = process.argv.slice(2);
  if (!url || !output || !process.env.PRS_PLAYWRIGHT_PATH || !process.env.PYTHON)
    throw new Error("Use CAMERA_URL OUTPUT and set PRS_PLAYWRIGHT_PATH/PYTHON");
  const parsed = new URL(url);
  if (parsed.username || parsed.password || parsed.search || parsed.hash ||
      !(parsed.protocol === "https:" || parsed.protocol === "http:" && ["localhost", "127.0.0.1"].includes(parsed.hostname)))
    throw new Error("Use a credential-free HTTPS or explicit loopback URL");
  await mkdir(dirname(output), {recursive: true});
  const report = {complete: false, hardwareVerified: false, pageErrors: [], scenarios: {},
    scope: "Real HTTP/WS/MCP/Chrome; native API fixtures, unchanged real browser activation"};
  const {chromium} = createRequire(import.meta.url)(process.env.PRS_PLAYWRIGHT_PATH);
  const browser = await chromium.launch({channel: "chrome", headless: true});
  try {
    const context = await browser.newContext({viewport: {width: 390, height: 844}, locale: "en-US"});
    await context.addInitScript(installFixture, true);
    const page = await context.newPage();
    page.on("pageerror", error => report.pageErrors.push(error.message));
    assert.equal((await page.goto(url, {waitUntil: "networkidle"})).status(), 200);
    const cdp = await context.newCDPSession(page);
    const observed = await cdp.send("Runtime.evaluate", {
      expression: 'JSON.stringify({session: sessionStorage.getItem("prs.ai.session"), activated: navigator.userActivation.hasBeenActive})',
      returnByValue: true, userGesture: false,
    });
    const identity = JSON.parse(observed.result.value);
    assert.equal(identity.activated, false);
    assert.ok(identity.session);
    const session = identity.session;
    report.scenarios.disabled = await runClient(url, output, session, "disabled");
    const enabled = await cdp.send("Runtime.evaluate", {
      expression: 'document.getElementById("vibration-enabled").checked = true; document.getElementById("vibration-enabled").dispatchEvent(new Event("change", {bubbles: true})); navigator.userActivation.hasBeenActive',
      returnByValue: true, userGesture: false,
    });
    assert.equal(enabled.result.value, false);
    report.scenarios.activation = await runClient(url, output, session, "activation");
    const untouched = await cdp.send("Runtime.evaluate", {
      expression: "JSON.stringify(window.__vibrationFixture.calls)",
      returnByValue: true, userGesture: false,
    });
    assert.deepEqual(JSON.parse(untouched.result.value), []);
    await page.locator("#vibration-enabled").uncheck();
    await page.locator("#vibration-enabled").check();
    report.scenarios.patterns = await runClient(url, output, session, "patterns");
    assert.deepEqual(await page.evaluate(() => window.__vibrationFixture.calls),
      [[200], [800], [150, 100, 150], 0]);
    await page.evaluate(() => { window.__vibrationFixture.outcome = "reject"; });
    report.scenarios.rejected = await runClient(url, output, session, "rejected");
    await page.evaluate(() => { window.__vibrationFixture.outcome = "throw"; });
    report.scenarios.exception = await runClient(url, output, session, "exception");
    const state = await page.evaluate(() => window.__vibrationFixture);
    assert.deepEqual(state.calls, [[200], [800], [150, 100, 150], 0, [200], 0, [200], 0]);
    assert.equal(state.cameraRequests, 0);
    report.nativeCalls = state.calls;
    report.cameraRequests = state.cameraRequests;
    await context.close();
    const unavailable = await browser.newContext({locale: "en-US"});
    await unavailable.addInitScript(installFixture, false);
    const unsupported = await unavailable.newPage();
    unsupported.on("pageerror", error => report.pageErrors.push(error.message));
    assert.equal((await unsupported.goto(url, {waitUntil: "networkidle"})).status(), 200);
    const otherSession = await unsupported.evaluate(() => sessionStorage.getItem("prs.ai.session"));
    report.scenarios.unsupported = await runClient(url, output, otherSession, "unsupported");
    assert.deepEqual(await unsupported.evaluate(() => window.__vibrationFixture.calls), []);
    assert.equal(await unsupported.evaluate(() => window.__vibrationFixture.cameraRequests), 0);
    await unavailable.close();
    assert.deepEqual(report.pageErrors, []);
    report.complete = true;
  } catch (error) {
    report.error = {name: error.name, message: error.message};
    throw error;
  } finally {
    await writeFile(output, JSON.stringify(report, null, 2) + "\n");
    await browser.close();
  }
  console.info(JSON.stringify({complete: report.complete, scenarios: Object.keys(report.scenarios),
    cameraRequests: report.cameraRequests, hardwareVerified: false}));
}

await main();
