/* Responsibilities: Verify discovered tools operate generic destinations and existing devices.
 * Implementation: Real Qwen, ORS name/category/route services, MCP and UI; synthetic GPS/media.
 * Related Modules: check_agent_browser.mjs; backend/core/agent and declarative skill scopes.
 * Declaration Index: turn; main.
 * Variable Index: None; PRS_PLAYWRIGHT_PATH selects the installed browser package;
 * PRS_TLS_SPKI optionally pins the reviewed server certificate. Report state is local to main.
 * Constraints: Explicit --live-qwen required; no retries, hidden substitute geocoder or fake POIs.
 * Tests establish observed behavior, not physical location, shop coverage or speech accuracy. */
import assert from "node:assert/strict";
import {createRequire} from "node:module";
import {mkdir, writeFile} from "node:fs/promises";
import {dirname} from "node:path";
import {installDevices, ask} from "./check_agent_browser.mjs";

/* Functionality: Submit one instruction and check discovery precedes workflow and action.
 * Inputs: page, user text, report; Outputs: actual turn evidence including result counts.
 * Logic: Use shared UI harness, attach observed result events and enforce protocol ordering.
 * Constraints: Does not judge natural-language success without independent state checks. */
async function turn(page, text, report) {
  const entry = await ask(page, text, report);
  entry.results = report.toolResults.splice(0);
  assert.equal(entry.tools[0]?.name, "discover_tools");
  assert.equal(entry.tools[1]?.name, "read_skill");
  return entry;
}

/* Functionality: Exercise named stores, category searches, unknown matches and device scope.
 * Inputs: URL OUTPUT --live-qwen plus package/TLS environment; Outputs: JSON evidence/screenshots.
 * Logic: Read each page's actual module URL to avoid instantiating a duplicate camera controller;
 * assert actual route/controller state separately from replies, preserve real zero-result
 * queries, check no automatic fallback search, then reload for independent permission/device cases.
 * Constraints: Singapore GPS and media are fixtures; external POI coverage may legitimately be
 * empty. Empty category queries pass only as honest empty responses, never as successful navigation. */
async function main() {
  const [url, output, flag] = process.argv.slice(2);
  if (!url || !output || flag !== "--live-qwen" || !process.env.PRS_PLAYWRIGHT_PATH)
    throw new Error("Supply URL OUTPUT --live-qwen and PRS_PLAYWRIGHT_PATH");
  const {chromium} = createRequire(import.meta.url)(process.env.PRS_PLAYWRIGHT_PATH);
  const args = ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"];
  if (process.env.PRS_TLS_SPKI) {
    if (!/^[A-Za-z0-9+/]{43}=$/.test(process.env.PRS_TLS_SPKI)) throw new Error("Invalid TLS pin");
    args.push("--ignore-certificate-errors-spki-list=" + process.env.PRS_TLS_SPKI);
  }
  const browser = await chromium.launch({channel: "chrome", headless: true, args});
  const report = {complete: false, scope: "Real Qwen/ORS/MCP/UI; synthetic GPS and media",
    turns: [], checks: [], routes: [], errors: [], toolEvents: [], toolResults: []};
  await mkdir(dirname(output), {recursive: true});
  try {
    const context = await browser.newContext({viewport: {width: 390, height: 844},
      permissions: ["camera", "microphone"]});
    await context.addInitScript(installDevices);
    const page = await context.newPage();
    page.on("pageerror", error => report.errors.push(error.message));
    page.on("response", response => {
      if (response.url().endsWith("/maps/route/")) report.routes.push({status: response.status(),
        request: response.request().postDataJSON()});
    });
    page.on("websocket", socket => {
      if (!socket.url().endsWith("/ws/agent/")) return;
      socket.on("framereceived", ({payload}) => {
        const event = JSON.parse(String(payload));
        if (event.type === "tool_start") report.toolEvents.push({name: event.name, arguments: event.arguments});
        if (event.type === "tool_result") report.toolResults.push({name: event.name, result: event.result});
      });
    });
    await page.goto(url);
    const stores = await turn(page, "帮我导航到 Sheng Siong 超市，先给我可选分店。", report);
    assert.ok(stores.tools.some(tool => tool.name === "maps_locate"));
    assert.equal(stores.tools.filter(tool => tool.name === "places_search").length, 1);
    assert.equal(report.routes.length, 0);
    assert.match(stores.reply, /Clementi|金文泰/i);
    report.checks.push("named_store_search_without_alias_table");
    await turn(page, "去 Clementi West 那家，规划路线并开始导航。", report);
    const state = await page.evaluate(async () => (await import(new URL("../assets/maps-page.js", location.href).href)).describeMap());
    assert.equal(report.routes.length, 1);
    assert.equal(report.routes[0].status, 200);
    assert.equal(state.origin_source, "device");
    assert.equal(state.navigation.state, "navigating");
    report.destination = state.destination;
    report.checks.push("selected_store_real_route_and_navigation");
    await turn(page, "停止导航", report);
    assert.equal(await page.evaluate(async () => (await import(new URL("../assets/navigation-ui.js", location.href).href)).navigation.describe().active), false);
    report.checks.push("existing_navigation_stop");
    for (const text of ["找一下我附近的超市，只搜索。", "Find pharmacies near me; search only."]) {
      const entry = await turn(page, text, report);
      const catalogIndex = entry.tools.findIndex(tool => tool.name === "places_categories");
      const queryIndex = entry.tools.findIndex(tool => tool.name === "places_nearby");
      assert.ok(catalogIndex >= 0 && queryIndex > catalogIndex);
      assert.equal(entry.tools.filter(tool => tool.name === "places_nearby").length, 1);
      assert.ok(!entry.tools.some(tool => ["places_search", "maps_plan", "navigation_start"].includes(tool.name)));
      const result = entry.results.find(tool => tool.name === "places_nearby")?.result;
      assert.equal(typeof result?.candidates_count, "number");
      if (result.candidates_count === 0) assert.match(entry.reply, /未|没|no |not |couldn|unable/i);
      report.checks.push({case: "category_discovered_and_queried", input: text, candidates: result.candidates_count});
    }
    const mismatch = await turn(page, "帮我导航到 FairPrice Clementi Mall 超市。", report);
    assert.ok(mismatch.tools.some(tool => tool.name === "places_search"));
    assert.ok(!mismatch.tools.some(tool => ["maps_plan", "navigation_start"].includes(tool.name)));
    report.checks.push("mall_result_not_silently_substituted_for_requested_store");
    await page.locator("#agent-panel").screenshot({path: output + ".png"});
    await page.locator("#agent-clear").click();
    await page.waitForFunction(() => document.querySelectorAll(".agent-message").length === 0);
    await page.reload();
    await page.locator("#agent-panel[data-connected=true][data-busy=false]").waitFor();
    await page.evaluate(() => { window.agentFixture.denyLocation = true; });
    const denied = await turn(page, "导航去附近超市。", report);
    assert.ok(denied.tools.some(tool => tool.name === "maps_locate"));
    assert.ok(!denied.tools.some(tool => ["places_nearby", "places_search", "maps_plan", "navigation_start"].includes(tool.name)));
    report.checks.push("location_denial_stops_search_and_navigation");
    const opened = await turn(page, "开启 Echo 摄像头。", report);
    assert.ok(opened.tools.some(tool => tool.name === "read_skill" && tool.arguments.name === "camera"));
    assert.equal(await page.evaluate(async () => (await import(document.querySelector('script[src$="/camera.js"]').src)).cameraApp.stream?.active), true);
    await turn(page, "关闭摄像头。", report);
    assert.equal(await page.evaluate(async () => (await import(document.querySelector('script[src$="/camera.js"]').src)).cameraApp.stream), null);
    report.checks.push("cross_domain_discovery_camera_start_stop");
    assert.deepEqual(report.errors, []);
    report.complete = true;
  } catch (error) {
    report.errors.push(error.stack);
    throw error;
  } finally {
    await writeFile(output, JSON.stringify(report, null, 2));
    await browser.close();
  }
}
await main();
