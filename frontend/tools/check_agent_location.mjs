/* Responsibilities: Regress the reported Chinese NUS navigation request and geographic boundaries.
 * Implementation: Real Qwen, ORS geocoding/routing, MCP and browser; explicit Singapore GPS fixture.
 * Related Modules: check_agent_browser.mjs; navigation skill/references; core.agent.places.
 * Declaration Index: main.
 * Variable Index: None; PRS_PLAYWRIGHT_PATH selects installed Playwright, PRS_TLS_SPKI optionally
 * pins the inspected server certificate; report/page/context are scenario-local state.
 * Constraints: --live-qwen is required; synthetic GPS is not proof of actual user location,
 * walkability, physical hardware or recognition accuracy. Failed reports remain visible. */
import assert from "node:assert/strict";
import {createRequire} from "node:module";
import {mkdir, writeFile} from "node:fs/promises";
import {dirname} from "node:path";
import {installDevices, ask} from "./check_agent_browser.mjs";

/* Functionality: Exercise original intent, meaningful campus clarification and explicit overseas scope.
 * Inputs: URL OUTPUT --live-qwen and package/TLS environment; Outputs: JSON evidence and screenshot.
 * Logic: Observe actual tool arguments, visible map state and real route HTTP responses; retain
 * every failure without replaying it or substituting a geocoder response. The denial case
 * reloads an empty map so it tests new-route GPS acquisition rather than valid route resumption.
 * Constraints: No model, timeout or GPS-policy changes; location denial is an explicit fixture. */
async function main() {
  const [url, output, flag] = process.argv.slice(2);
  if (!url || !output || flag !== "--live-qwen" || !process.env.PRS_PLAYWRIGHT_PATH)
    throw new Error("Supply URL OUTPUT --live-qwen and PRS_PLAYWRIGHT_PATH");
  const {chromium} = createRequire(import.meta.url)(process.env.PRS_PLAYWRIGHT_PATH);
  const args = ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"];
  if (process.env.PRS_TLS_SPKI) {
    if (!/^[A-Za-z0-9+/]{43}=$/.test(process.env.PRS_TLS_SPKI)) throw new Error("Invalid TLS key pin");
    args.push("--ignore-certificate-errors-spki-list=" + process.env.PRS_TLS_SPKI);
  }
  const browser = await chromium.launch({channel: "chrome", headless: true, args});
  const report = {complete: false, scope: "Real Qwen/ORS search/ORS routes/MCP/UI; synthetic Singapore GPS",
    originalInput: "嗯，帮我开启导航去新加坡国立大学。", turns: [], checks: [], errors: [], toolEvents: [], routes: []};
  await mkdir(dirname(output), {recursive: true});
  try {
    const context = await browser.newContext({viewport: {width: 390, height: 844}});
    await context.addInitScript(installDevices);
    const page = await context.newPage();
    page.on("pageerror", error => report.errors.push(error.message));
    page.on("response", response => {
      if (response.url().endsWith("/maps/route/")) report.routes.push({status: response.status()});
    });
    page.on("websocket", socket => {
      if (!socket.url().endsWith("/ws/agent/")) return;
      socket.on("framereceived", ({payload}) => {
        const event = JSON.parse(String(payload));
        if (event.type === "tool_start") report.toolEvents.push({name: event.name, arguments: event.arguments});
      });
    });
    await page.goto(url);
    await ask(page, report.originalInput, report);
    const first = report.turns.at(-1);
    const searchIndex = first.tools.findIndex(tool => tool.name === "places_search");
    const locateIndex = first.tools.findIndex(tool => tool.name === "maps_locate");
    assert.ok(locateIndex >= 0 && searchIndex > locateIndex, "Device location must precede named navigation search");
    const query = first.tools[searchIndex].arguments;
    assert.match(query.text, /National University of Singapore/i);
    assert.deepEqual(query.focus, [103.7766, 1.2971]);
    assert.ok(query.country == null || query.country === "SGP");
    assert.doesNotMatch(first.reply, /苏州|中国|是否.{0,15}新加坡|是不是.{0,15}新加坡/);
    assert.match(first.reply, /肯特岗|武吉知马|校区/);
    assert.equal(report.routes.length, 0, "Resolve actual campus ambiguity before planning");
    report.checks.push("original_chinese_intent_device_focus_canonical_name_country_scope");
    await ask(page, "去肯特岗主校区，使用搜索返回的校区地点规划路线并开始导航。", report);
    const state = await page.evaluate(async () => (await import(new URL("../assets/maps-page.js", location.href).href)).describeMap());
    assert.equal(report.routes.length, 1); assert.equal(report.routes[0].status, 200);
    assert.equal(state.origin_source, "device");
    assert.ok(state.destination[0] > 103.76 && state.destination[0] < 103.80);
    assert.ok(state.destination[1] > 1.28 && state.destination[1] < 1.31);
    assert.equal(state.navigation.state, "navigating");
    report.destination = state.destination;
    report.checks.push("campus_selection_real_ors_route_navigation");
    await ask(page, "停止导航", report);
    assert.equal(await page.evaluate(async () => (await import(new URL("../assets/navigation-ui.js", location.href).href)).navigation.describe().active), false);
    report.checks.push("navigation_stop");
    await page.locator("#agent-panel").screenshot({path: output + ".png"});
    await page.locator("#agent-clear").click();
    await page.waitForFunction(() => document.querySelectorAll(".agent-message").length === 0);
    await ask(page, "请只搜索中国苏州的新加坡国立大学苏州研究院，不规划路线、不导航。", report);
    const overseas = report.turns.at(-1).tools.filter(tool => tool.name === "places_search");
    assert.equal(overseas.length, 1); assert.equal(overseas[0].arguments.country, "CHN");
    assert.match(overseas[0].arguments.text, /苏州|Suzhou/i);
    assert.equal(report.routes.length, 1);
    report.checks.push("explicit_overseas_branch_preserved");
    await page.locator("#agent-clear").click();
    await page.waitForFunction(() => document.querySelectorAll(".agent-message").length === 0);
    await page.reload();
    await page.locator("#agent-panel[data-connected=true][data-busy=false]").waitFor();
    await page.waitForFunction(async () => {
      const map = (await import(new URL("../assets/maps-page.js", location.href).href)).describeMap();
      return map.ready && !map.navigation.route && map.origin === null && map.destination === null;
    });
    await page.evaluate(() => { window.agentFixture.denyLocation = true; });
    await ask(page, "从我的当前位置导航去新加坡国立大学。", report);
    const denied = report.turns.at(-1);
    assert.ok(denied.tools.some(tool => tool.name === "maps_locate"));
    assert.ok(!denied.tools.some(tool => ["places_search", "maps_plan", "navigation_start"].includes(tool.name)));
    assert.match(denied.reply, /定位|权限|拒绝/);
    report.checks.push("denied_location_does_not_become_deployment_location");
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
