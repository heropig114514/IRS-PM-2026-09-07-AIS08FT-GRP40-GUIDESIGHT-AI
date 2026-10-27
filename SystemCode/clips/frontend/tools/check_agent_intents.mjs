/* Responsibilities: Verify real intent admission, follow-ups and phonetic candidate confirmation.
 * Implementation: Actual Qwen, ORS, MCP and browser; explicit synthetic Singapore GPS only.
 * Related Modules: check_agent_browser.mjs; SystemCode/clips/backend/core/agent; agent_skills/intents.json.
 * Declaration Index: main; main.turn; main.mapState.
 * Variable Index: None; PRS_PLAYWRIGHT_PATH selects Playwright; optional PRS_TLS_SPKI pins TLS.
 * Constraints: --live-qwen required; no physical GPS, camera capture, ASR or hardware claims. */
import assert from "node:assert/strict";
import {createRequire} from "node:module";
import {mkdir, writeFile} from "node:fs/promises";
import {dirname} from "node:path";
import {installDevices, ask} from "./check_agent_browser.mjs";

/* Functionality: Exercise routing and pronunciation recovery end-to-end with live services.
 * Inputs: URL OUTPUT --live-qwen and package/TLS configuration; Outputs: retained JSON/screenshot.
 * Logic: Assert decisions before tools, unchanged scope, exact provider identity and no route
 * until the user selects an actual returned entity rather than the hypothesized mall.
 * Constraints: One attempt per step, no result substitution or model/timeout changes. */
async function main() {
  const [url, output, flag] = process.argv.slice(2);
  assert.ok(url && output && flag === "--live-qwen" && process.env.PRS_PLAYWRIGHT_PATH);
  const {chromium} = createRequire(import.meta.url)(process.env.PRS_PLAYWRIGHT_PATH);
  const args = [];
  if (process.env.PRS_TLS_SPKI) {
    assert.match(process.env.PRS_TLS_SPKI, /^[A-Za-z0-9+/]{43}=$/);
    args.push("--ignore-certificate-errors-spki-list=" + process.env.PRS_TLS_SPKI);
  }
  const browser = await chromium.launch({channel: "chrome", headless: true, args});
  const report = {complete: false, scope: "Real Qwen/ORS/MCP/UI; synthetic GPS; no camera capture",
    turns: [], checks: [], toolEvents: [], intents: [], results: [], routes: [], errors: []};
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
        if (event.type === "tool_start") report.toolEvents.push({name: event.name, arguments: event.arguments, seq: event.seq});
        if (event.type === "intent") report.intents.push(event);
        if (event.type === "tool_result") report.results.push(event);
      });
    });
    // Functionality: Assert one expected route for a real submitted turn.
    // Inputs: text, route and sorted IDs; Outputs: completed turn evidence.
    // Logic: Require a fresh decision before any tool; Constraints: no interpreting reply as action proof.
    async function turn(text, route, ids) {
      const count = report.intents.length;
      const entry = await ask(page, text, report);
      assert.equal(report.intents.length, count + 1);
      const intent = report.intents.at(-1);
      assert.equal(intent.route, route);
      assert.deepEqual(intent.intents.sort(), ids);
      if (entry.tools.length) assert.ok(intent.seq < entry.tools[0].seq);
      entry.intent = intent;
      return entry;
    }
    // Functionality: Observe actual map controller state without acting on it.
    // Inputs: current page; Outputs: describeMap result; Logic: existing module.
    // Constraints: Only call while the map page is active.
    async function mapState() {
      return page.evaluate(async () => (await import(new URL("../assets/maps-page.js", location.href).href)).describeMap());
    }
    await page.goto(url);
    for (const [text, route, ids] of [
      ["你能做什么？", "system", ["assistant.help"]],
      ["为什么天空是蓝色的？", "general", []],
      ["帮我打开微信并给朋友发消息", "unsupported", []],
      ["停止", "clarify", []],
    ]) {
      const entry = await turn(text, route, ids);
      assert.equal(entry.tools.length, 0);
      report.checks.push("non_action_route_" + route);
    }
    const missing = await turn("嗯，帮我开启导航。", "system", ["navigation.start"]);
    assert.ok(missing.tools.some(tool => tool.name === "maps_status"));
    assert.ok(!missing.tools.some(tool => ["places_search", "maps_plan", "navigation_start"].includes(tool.name)));
    assert.match(missing.reply, /哪里|目的地|终点/);
    report.checks.push("missing_slot_hits_navigation_then_clarifies");
    const search = await turn("Sheng Siong 超市，先列出可选门店。", "system", ["navigation.start"]);
    assert.equal(search.tools.filter(tool => tool.name === "places_search").length, 1);
    assert.equal(report.routes.length, 0);
    report.checks.push("followup_inherits_navigation");
    assert.match(search.reply, /4\. Sheng Siong, Clementi West/);
    await turn("第四个", "system", ["navigation.start"]);
    assert.equal((await mapState()).navigation.state, "navigating");
    assert.equal(report.routes.length, 1);
    report.checks.push("selected_store_real_route_navigation");
    const status = await turn("还有多远？", "system", ["navigation.status"]);
    assert.ok(status.tools.some(tool => tool.name === "navigation_status"));
    assert.ok(!status.tools.some(tool => ["maps_plan", "navigation_start"].includes(tool.name)));
    await turn("停止导航", "system", ["navigation.stop"]);
    assert.equal((await mapState()).navigation.active, false);
    report.checks.push("status_and_stop_do_not_restart");
    const phonetic = await turn("导航到一个商场，我读音不准，听起来像‘克莱门提摩尔’，请按读音找候选给我确认。", "system", ["navigation.start"]);
    const query = phonetic.tools.find(tool => tool.name === "places_search")?.arguments;
    assert.ok(query?.pronunciation_queries?.length > 0 && query.pronunciation_queries.length <= 2);
    assert.match(query.text, /克莱门提摩尔/);
    assert.match(query.original_text, /克莱门提摩尔/);
    assert.ok(query.pronunciation_queries.some(value => /Clementi/i.test(value)));
    assert.deepEqual(query.focus, [103.7766, 1.2971]);
    assert.equal(report.results.findLast(event => event.name === "places_search").result.requires_confirmation, true);
    assert.ok(!phonetic.tools.some(tool => ["maps_plan", "navigation_start"].includes(tool.name)));
    assert.equal(report.routes.length, 1);
    assert.match(phonetic.reply, /Clementi/i);
    assert.match(phonetic.reply, /Bridge inside Clement MRT Station that connects to Clementi Mall/);
    report.checks.push("phonetic_candidates_require_confirmation");
    await turn("是 The Clementi Mall，使用刚才返回的商场坐标规划并开始导航。", "system", ["navigation.start"]);
    assert.equal(report.routes.length, 1, "The provider bridge must not be silently substituted for the mall");
    assert.ok(!report.turns.at(-1).tools.some(tool => ["maps_plan", "navigation_start"].includes(tool.name)));
    assert.match(report.turns.at(-1).reply, /桥|Bridge/i);
    report.checks.push("mall_name_does_not_confirm_adjacent_bridge");
    await turn("第一个", "system", ["navigation.start"]);
    assert.equal((await mapState()).navigation.state, "navigating");
    assert.equal(report.routes.length, 2);
    assert.ok(!report.turns.at(-1).tools.some(tool => tool.name === "places_search"));
    report.checks.push("explicitly_accepted_provider_bridge_real_navigation");
    await turn("停止导航，然后关闭摄像头", "system", ["camera.stop", "navigation.stop"]);
    assert.deepEqual(new Set(report.turns.at(-1).tools.filter(tool => tool.name === "read_skill")
      .map(tool => tool.arguments.name)), new Set(["camera", "navigation"]));
    assert.ok(!report.turns.at(-1).tools.some(tool => tool.name === "camera_start"));
    report.checks.push("combined_request_loads_mapped_skills");
    await turn("界面换成英文", "system", ["interface.language"]);
    assert.ok((await page.locator("html").getAttribute("lang")).startsWith("en"));
    report.checks.push("output_skill_changes_existing_language_control");
    assert.ok(report.routes.every(route => route.status === 200));
    await page.locator("#agent-activity").evaluate(element => { element.closest("details").open = true; });
    await page.locator("#agent-panel").screenshot({path: output + ".png"});
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
