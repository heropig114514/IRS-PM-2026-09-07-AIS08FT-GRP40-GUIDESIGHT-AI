/* Responsibilities: Verify navigation UI and real MCP HTTP/WebSocket integration in Chrome.
 * Implementation: Real page/Leaflet/CSP/i18n/control bridge, explicit route/config/GPS fixtures,
 * and the official Python MCP client. Assert lifecycle, page scope and responsive layouts.
 * Related Modules: navigation.js, navigation-ui.js, maps-page.js, core.ai_mcp/ai_bridge.
 * Declaration Index: installGPS — synthetic native boundary; installGPS.fix — controlled fix;
 * installGPS.geo.watchPosition/clearWatch — synthetic watch registration/cancellation;
 * runTool — official MCP request; main — isolated browser regression.
 * Variable Index: MCP_SCRIPT — fixed read-only-discovery/navigation Python client source;
 * PRS_PLAYWRIGHT_PATH/PYTHON — explicit development runtimes, no production dependencies.
 * Constraints: No real GPS, permission prompt, ORS, GPU, database mutation or deployment verified.
 */
import assert from "node:assert/strict";
import {createRequire} from "node:module";
import {execFile} from "node:child_process";
import {promisify} from "node:util";
import {mkdir, writeFile} from "node:fs/promises";
import {dirname} from "node:path";

const MCP_SCRIPT = `
import asyncio,json,sys
from mcp.client import Client
async def run():
    # Functionality: Invoke one explicit navigation tool through real HTTP.
    # Inputs: URL/session/tool argv. Outputs: JSON result. Logic: Discover then call once.
    # Constraints: No browser selection, retries or database tools.
    async with Client(sys.argv[1]) as client:
        names={tool.name for tool in (await client.list_tools()).tools}
        assert {"navigation_start","navigation_stop","navigation_status"} <= names
        assert "database_sql" not in names, "Use the outputs test profile"
        result=await client.call_tool(sys.argv[3], {"session_id":sys.argv[2]})
        assert not result.is_error, result.content
        print(json.dumps(result.structured_content))
asyncio.run(run())
`;

/* Functionality: Install a synthetic location watch, preserving real controller/UI behavior.
 * Inputs: None, browser window implicit. Outputs: __navigationGPS fixture and API replacement.
 * Logic: Retain callbacks even after clear to test late native delivery; count active watches.
 * Constraints: Test-only sensor replacement, not browser permission or GPS evidence. */
function installGPS() {
  const watches = [], cleared = [];
  window.__navigationGPS = {watches, cleared,
    // Functionality: Deliver synthetic fix; Inputs: coordinates/accuracy/index; Outputs: callback;
    // Logic: browser timestamp; Constraints: does not claim actual location acquisition.
    fix(longitude, latitude, accuracy = 5, index = watches.length - 1) {
      watches[index].success({coords: {longitude, latitude, accuracy}, timestamp: Date.now()});
    }};
  const geo = {
    // Functionality: Register fake GPS; Inputs: callbacks/options; Outputs: watch handle;
    // Logic: retain callbacks; Constraints: no immediate fix or permission grant.
    watchPosition(success, error, options) { watches.push({success, error, options}); return watches.length - 1; },
    // Functionality: Record cleanup; Inputs: id; Outputs: cancellation record;
    // Logic: keep callbacks to test stale delivery; Constraints: no native resource exists.
    clearWatch(id) { cleared.push(id); },
  };
  Object.defineProperty(navigator, "geolocation", {configurable: true, value: geo});
}

/* Functionality: Execute one tool with the official SDK against an explicit page session.
 * Inputs: maps URL, selected session ID and navigation tool name. Outputs: Parsed JSON result.
 * Logic: Fixed Python program/argument array without shell; bounded process and hidden window.
 * Constraints: No command retry; timeout does not imply that a mutation was not executed. */
async function runTool(url, session, name) {
  const {stdout} = await promisify(execFile)(process.env.PYTHON,
    ["-c", MCP_SCRIPT, new URL("../mcp", url).href, session, name],
    {windowsHide: true, timeout: 20000});
  return JSON.parse(stdout.trim());
}

/* Functionality: Exercise start/status/stop and map guidance through a real browser/control hub.
 * Inputs: CLI maps URL/output path and explicit Python/Playwright package env variables.
 * Outputs: JSON evidence plus 390px screenshot; failures recorded and propagated.
 * Logic: Plan via UI with a fixed NUS L route, command through real MCP, drive sensor fixtures,
 * then verify stop, arrival, hidden-page cleanup, language switching and wrong-page refusal.
 * Constraints: Routes/config/GPS are synthetic; CDN/TLS/SRI/CSP remain real; no silent retries. */
async function main() {
  const [url, output] = process.argv.slice(2);
  if (!url || !output || !process.env.PYTHON || !process.env.PRS_PLAYWRIGHT_PATH)
    throw new Error("Supply maps URL/output and PYTHON/PRS_PLAYWRIGHT_PATH");
  const {chromium} = createRequire(import.meta.url)(process.env.PRS_PLAYWRIGHT_PATH);
  const browser = await chromium.launch({channel: "chrome", headless: true});
  const report = {complete: false, scope: "Real Chrome/Leaflet/CSP/MCP HTTP/WS; synthetic ORS and GPS", errors: []};
  try {
    const context = await browser.newContext({viewport: {width: 390, height: 844}});
    await context.addInitScript(installGPS);
    await context.route("**/maps/config/", route => route.fulfill({json: {
      provider: "openrouteservice", profile: "foot-walking", configured: true}}));
    let routeCalls = 0;
    await context.route("**/maps/route/", route => {
      routeCalls++;
      return route.fulfill({json: {provider: "openrouteservice", profile: "foot-walking",
        request_id: "browser-navigation-fixture", attribution: "Synthetic ORS route",
        geometry: {type: "LineString", coordinates: [[103.7766, 1.2971], [103.7776, 1.2971], [103.7776, 1.2981]]},
        summary: {distance_m: 222, duration_s: 180}, steps: [
          {type: 11, instruction: "Head east", distance_m: 111, duration_s: 90, way_points: [0, 1]},
          {type: 0, instruction: "Turn left", distance_m: 111, duration_s: 90, way_points: [1, 2]},
          {type: 10, instruction: "Arrive", distance_m: 0, duration_s: 0, way_points: [2, 2]},
        ]}});
    });
    const page = await context.newPage();
    page.on("pageerror", error => report.errors.push(error.message));
    const response = await page.goto(url, {waitUntil: "domcontentloaded"});
    assert.equal(response.status(), 200);
    await page.waitForFunction(() => !document.querySelector("#example").disabled);
    await page.locator("#language").selectOption("en");
    const session = await page.evaluate(() => sessionStorage.getItem("prs.ai.session"));
    report.before = await runTool(url, session, "navigation_status");
    assert.equal(report.before.state, "idle");
    assert.equal((await runTool(url, session, "navigation_start")).reason, "route_required");
    await page.locator("#example").click();
    await page.locator("#plan").click();
    await page.waitForFunction(() => !document.querySelector("#navigation-start").disabled);
    report.start = await runTool(url, session, "navigation_start");
    assert.equal(report.start.accepted, true);
    assert.equal(report.start.state, "locating");
    await page.evaluate(() => __navigationGPS.fix(103.7771, 1.2971));
    report.moving = await runTool(url, session, "navigation_status");
    assert.equal(report.moving.state, "navigating");
    assert.equal(report.moving.progress.current_step.instruction, "Head east");
    assert.equal(await page.locator("#navigation-instruction").innerText(), "Head east");
    for (const width of [320, 390, 1280]) {
      await page.setViewportSize({width, height: 844});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    }
    await page.setViewportSize({width: 390, height: 844});
    await page.locator("#navigation-panel").scrollIntoViewIfNeeded();
    await mkdir(dirname(output), {recursive: true});
    await page.screenshot({path: output + ".png", fullPage: true});
    await page.locator("#language").selectOption("zh-CN");
    await page.waitForFunction(() => document.querySelector("#navigation-status").textContent === "导航进行中");
    await page.locator("#language").selectOption("en");
    await page.evaluate(() => __navigationGPS.fix(103.7796, 1.2981));
    assert.equal((await runTool(url, session, "navigation_status")).state, "off_route");
    report.stop = await runTool(url, session, "navigation_stop");
    assert.equal(report.stop.accepted, true);
    await page.evaluate(() => __navigationGPS.fix(103.7776, 1.2981));
    assert.equal((await runTool(url, session, "navigation_status")).state, "stopped");
    await page.locator("#navigation-start").click();
    await page.evaluate(() => __navigationGPS.fix(103.7776, 1.2981));
    report.arrival = await runTool(url, session, "navigation_status");
    assert.equal(report.arrival.state, "arrived");
    assert.equal(report.arrival.active, false);
    await page.locator("#navigation-start").click();
    // Explicit visibility fixture isolates the cleanup handler; not an OS background assertion.
    await page.evaluate(() => {
      Object.defineProperty(document, "visibilityState", {configurable: true, value: "hidden"});
      document.dispatchEvent(new Event("visibilitychange"));
    });
    report.hidden = await runTool(url, session, "navigation_status");
    assert.equal(report.hidden.reason, "page_hidden");
    assert.equal(report.hidden.active, false);
    assert.equal((await runTool(url, session, "navigation_start")).reason, "page_hidden");
    assert.equal(await page.evaluate(() => __navigationGPS.cleared.length), 3);
    assert.equal(routeCalls, 1, "navigation must never request another route");
    const camera = await context.newPage();
    await camera.goto(new URL("../live/", url).href, {waitUntil: "domcontentloaded"});
    const cameraSession = await camera.evaluate(() => sessionStorage.getItem("prs.ai.session"));
    assert.equal((await runTool(url, cameraSession, "navigation_start")).reason, "page_unavailable");
    assert.deepEqual(report.errors, []);
    report.complete = true;
    console.log(JSON.stringify({complete: true, route_calls: routeCalls, layouts: [320, 390, 1280]}));
  } catch (error) {
    report.failure = error.message;
    throw error;
  } finally {
    await mkdir(dirname(output), {recursive: true});
    await writeFile(output, JSON.stringify(report, null, 2) + "\n");
    await browser.close();
  }
}

await main();
