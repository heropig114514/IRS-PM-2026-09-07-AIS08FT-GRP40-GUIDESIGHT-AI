/** Responsibilities: 用真实Chrome验证手动震动UI、语言切换和手机布局。
 * Implementation: 保留真实HTML/CSS/模块/CSP；明确替换原生vibrate及媒体权限边界来控制接受/拒绝/异常。
 * Related Modules: index.html, vibration.js, camera.css, i18n.js, translations.js。
 * Declaration Index: installFixture; installFixture.vibrate; installFixture.denyCamera;
 * expectText; assertLayout; collectUntranslated; checkSupported; checkUnavailable; main。
 * Variable Index: None；main读取PRS_PLAYWRIGHT_PATH开发运行时，report保存非敏感断言与截图路径。
 * Constraints: 无真实马达、GPS、摄像头、HF或GPU验证；不禁用TLS/CSP、不重试网络或发布。
 */
import assert from "node:assert/strict";
import {createRequire} from "node:module";
import {mkdir, writeFile} from "node:fs/promises";
import {dirname} from "node:path";

/** Functionality: 在模块加载前安装明确的浏览器API替身。
 * Inputs: supported决定原生接口是否存在；浏览器navigator/window。
 * Outputs: __vibrationFixture的请求/媒体计数及可控outcome。
 * Logic: 仅替换vibrate和getUserMedia；其他DOM、用户点击及翻译仍由真实浏览器处理。
 * Constraints: 返回true是假定接受，不证明硬件；意外媒体请求直接失败而不申请权限。
 */
function installFixture(supported) {
  window.__vibrationFixture = {calls: [], cameraRequests: 0, outcome: "accept"};
  /** Functionality: 记录API接收者及预定测试结果。
   * Inputs: pattern和__vibrationFixture.outcome。Outputs: boolean或NotAllowedError。
   * Logic: 验证接收者为navigator，复制固定数组，按显式测试状态返回。
   * Constraints: 不是实际原生API，不访问系统马达。
   */
  function vibrate(pattern) {
    if (this !== navigator) throw new TypeError("Incorrect vibration receiver");
    const state = window.__vibrationFixture;
    state.calls.push(Array.isArray(pattern) ? [...pattern] : pattern);
    if (state.outcome === "throw") throw new DOMException("Synthetic vibration rejection", "NotAllowedError");
    return state.outcome === "accept";
  }
  Object.defineProperty(navigator, "vibrate", {configurable: true, value: supported ? vibrate : undefined});
  /** Functionality: 检测意外媒体授权请求。
   * Inputs: navigator.mediaDevices隐式调用。Outputs: 计数及明确异常。
   * Logic: 拒绝请求，防止手动震动回归访问传感器。Constraints: 不验证真实摄像头权限。
   */
  function denyCamera() {
    window.__vibrationFixture.cameraRequests++;
    throw new Error("Vibration UI unexpectedly requested camera access");
  }
  if (navigator.mediaDevices) Object.defineProperty(navigator.mediaDevices, "getUserMedia", {
    configurable: true, value: denyCamera,
  });
}

/** Functionality: 等待实际DOM呈现期望文字。
 * Inputs: page/selector/text。Outputs: 有界等待或超时。
 * Logic: 等待翻译观察器完成，而不使用固定延时。Constraints: 不重试业务请求。
 */
async function expectText(page, selector, text) {
  await page.waitForFunction(({selector, text}) => document.querySelector(selector)?.textContent.trim() === text,
    {selector, text}, {timeout: 5000});
}

/** Functionality: 检查当前宽度无横向溢出及按钮触控尺寸。
 * Inputs: page和当前viewport。Outputs: DOM布局断言。
 * Logic: 从真实布局读取页面宽度与按钮矩形。Constraints: 不等同于物理手机触控验证。
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

/** Functionality: 检查震动区域的英文翻译完整性。
 * Inputs: 浏览器DOM。Outputs: 中文文本/无障碍属性列表。
 * Logic: 遍历实际文本节点与aria-label。Constraints: 不读取摄像头画面或用户输入。
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

/** Functionality: 验证支持接口时的真实控件和拒绝/异常状态。
 * Inputs: browser/url/output/report。Outputs: 请求序列、布局、语言及无媒体调用证据和截图。
 * Logic: 独立英文390pxcontext加载模块；实际点击按钮，切换语言并控制原生替身返回值。
 * Constraints: 硬件结果始终未验证；只进行页面加载和用户测试，不执行检测/发布。
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

/** Functionality: 验证接口不存在时真实UI的操作边界和翻译。
 * Inputs: browser/url/report。Outputs: 全部禁用、支持提示和零请求断言。
 * Logic: 新context显式不提供vibrate，加载同一页面。Constraints: 不是实际Safari设备测试。
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

/** Functionality: 运行明确边界的浏览器回归并保存证据。
 * Inputs: CLI URL/OUTPUT及PRS_PLAYWRIGHT_PATH；已安装Chrome。
 * Outputs: JSON/PNG、退出码；浏览器启动后的失败写入JSON，随后关闭浏览器。
 * Logic: URL只接受无凭据HTTPS或显式loopback，分别验证支持与缺失场景。
 * Constraints: 输出声明无硬件验证，不修改生产依赖、TLS或页面源码；不自动重试。
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
