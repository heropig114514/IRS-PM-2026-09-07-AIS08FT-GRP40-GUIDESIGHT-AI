/** Responsibilities: 验证手动震动的用户边界、拒绝反馈、生命周期取消及词典覆盖。
 * Implementation: 用 EventTarget 派发真实注册回调；仅替换 DOM 和原生震动函数，不调用硬件或网络。
 * Related Modules: vibration.js, ai-control.js, index.html, translations.js, i18n.js。
 * Declaration Index: TestControl; TestControl.constructor; TestDocument; TestDocument.constructor;
 * TestDocument.getElementById; TestDocument.querySelectorAll; TestWindow; TestWindow.constructor;
 * TestWindow.info; TestWindow.error; setup; enable; testManual; testManual.native;
 * testUnavailable; testActivation; testActivation.native; testFailures; testFailures.native;
 * testLifecycle; testLifecycle.native; testCancelRejected; testCancelRejected.native; testTranslations。
 * testMcpShared; testMcpShared.native; testMcpFailures; testMcpFailures.native。
 * Variable Index: None；每个测试的calls记录固定请求，logs记录非敏感诊断，response控制原生替身结果。
 */
import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {VibrationTester} from "../vibration.js";
import {translate} from "../i18n.js";
import {performVibration} from "../ai-control.js";

/** Functionality: 提供可派发点击/变更事件的独立控件。
 * Logic: EventTarget维护监听器，字段仅表示测试所需DOM状态。
 * Constraints: 不实现浏览器布局或disabled事件拦截，测试需显式验证disabled属性。
 */
class TestControl extends EventTarget {
  /** Functionality: 初始化控件字段。
   * Inputs: pattern 可选测试类型。Outputs: 可派发事件的实例。
   * Logic: dataset保存固定类型；默认未勾选。Constraints: 无HTML解析或物理震动。
   */
  constructor(pattern) {
    super();
    this.dataset = pattern ? {vibrationPattern: pattern} : {};
    this.checked = false;
    this.disabled = false;
    this.textContent = "";
  }
}

/** Functionality: 提供震动区域与可控页面可见性。
 * Logic: 固定节点映射配合EventTarget事件。Constraints: 不是浏览器文档，不提供媒体或网络API。
 */
class TestDocument extends EventTarget {
  /** Functionality: 创建固定测试节点。
   * Inputs: None。Outputs: 节点映射、按钮数组及visible初态。
   * Logic: 与HTML控件id对应。Constraints: 只验证控制器契约，不证明HTML实际包含节点。
   */
  constructor() {
    super();
    this.visibilityState = "visible";
    this.nodes = new Map(["vibration-enabled", "vibration-stop", "vibration-status", "vibration-message"]
      .map(id => [id, new TestControl()]));
    this.buttons = ["short", "long", "double"].map(name => new TestControl(name));
  }
  /** Functionality: 查找固定节点。Inputs: id。Outputs: 控件或undefined。
   * Logic: 直接查询映射。Constraints: 不按需制造缺失节点。
   */
  getElementById(id) { return this.nodes.get(id); }
  /** Functionality: 返回节奏按钮。Inputs: selector。Outputs: 固定按钮数组。
   * Logic: 核对控制器使用的选择器。Constraints: 不实现通用CSS查询。
   */
  querySelectorAll(selector) {
    assert.equal(selector, "[data-vibration-pattern]");
    return this.buttons;
  }
}

/** Functionality: 提供显式原生接口和页面事件边界。
 * Logic: 测试注入原生返回值/异常并捕获诊断。Constraints: 不代表操作系统权限或设备马达。
 */
class TestWindow extends EventTarget {
  /** Functionality: 安装可选原生接口及诊断接收器。
   * Inputs: native 可选震动替身。Outputs: navigator/console/logs和窗口事件实例。
   * Logic: 不传native即模拟接口缺失；交互激活默认为true。Constraints: 不注入任何媒体API。
   */
  constructor(native) {
    super();
    this.navigator = {userActivation: {hasBeenActive: true}};
    if (native) this.navigator.vibrate = native;
    this.logs = [];
    this.console = {info: this.info.bind(this), error: this.error.bind(this)};
  }
  /** Functionality: 捕获状态日志。Inputs: args。Outputs: logs追加。
   * Logic: 保留原参数供断言。Constraints: 测试数据无凭据或媒体。
   */
  info(...args) { this.logs.push({level: "info", args}); }
  /** Functionality: 捕获异常日志。Inputs: args。Outputs: logs追加。
   * Logic: 保留错误层级。Constraints: 不把日志存在等同于硬件验证。
   */
  error(...args) { this.logs.push({level: "error", args}); }
}

/** Functionality: 创建隔离测试控制器。
 * Inputs: native 可选原生替身。Outputs: doc/win/tester。
 * Logic: 运行实际构造器并注册实际处理器。Constraints: 不替换控制器实现。
 */
function setup(native) {
  const doc = new TestDocument(), win = new TestWindow(native);
  return {doc, win, tester: new VibrationTester(doc, win)};
}

/** Functionality: 派发用户启用选择。
 * Inputs: doc。Outputs: checkbox字段和change事件。
 * Logic: 触发已注册处理器。Constraints: 不模拟浏览器信任标记，激活状态另行控制。
 */
function enable(doc) {
  doc.getElementById("vibration-enabled").checked = true;
  doc.getElementById("vibration-enabled").dispatchEvent(new Event("change"));
}

/** Functionality: 验证加载/启用不震动、按钮节奏、显式停止及硬件措辞边界。
 * Inputs: 成功原生替身和实际事件。Outputs: 调用、开关和文字断言。
 * Logic: 逐次手动点击后比较预定产品节奏。Constraints: 无物理震动或媒体权限验证。
 */
test("默认关闭且每次仅手动发出所选节奏", function testManual() {
  const calls = [];
  /** Functionality: 记录一次请求。Inputs: pattern。Outputs: true并追加calls。
   * Logic: 合成接受结果。Constraints: 返回true不能证明硬件存在。
   */
  function native(pattern) { calls.push(pattern); return true; }
  const {doc, tester} = setup(native);
  assert.equal(tester.enabled.checked, false);
  assert.ok(doc.buttons.every(button => button.disabled));
  assert.deepEqual(calls, []);
  enable(doc);
  assert.deepEqual(calls, []);
  for (const button of doc.buttons) button.dispatchEvent(new Event("click"));
  assert.deepEqual(calls, [[200], [800], [150, 100, 150]]);
  assert.match(tester.message.textContent, /请求已接受.*不能验证硬件/);
  tester.stopButton.dispatchEvent(new Event("click"));
  assert.deepEqual(calls.at(-1), 0);
  assert.equal(tester.requested, false);
});

/** Functionality: 验证接口不存在时给出明确支持诊断且禁用控件。
 * Inputs: 无vibrate的navigator。Outputs: disabled及文字断言。
 * Logic: 运行实际初始化。Constraints: 仅接口缺失替身，不代表所有iPhone行为。
 */
test("接口缺失时禁用测试和停止并说明原因", function testUnavailable() {
  const {doc, tester} = setup();
  assert.equal(tester.enabled.disabled, true);
  assert.equal(tester.stopButton.disabled, true);
  assert.ok(doc.buttons.every(button => button.disabled));
  assert.match(tester.message.textContent, /未提供震动接口/);
});

/** Functionality: 验证关闭、后台和未激活的页面都不能提交测试请求。
 * Inputs: 显式navigator激活和文档可见性。Outputs: 零原生调用及诊断。
 * Logic: 直接派发事件验证处理器本身的边界。Constraints: 不依赖替身禁用拦截。
 */
test("开关、可见性和用户激活阻止非主动请求", function testActivation() {
  const calls = [];
  /** Functionality: 捕获不应发生的调用。Inputs: pattern。Outputs: true/calls。
   * Logic: 记录请求。Constraints: 无原生接口。
   */
  function native(pattern) { calls.push(pattern); return true; }
  const {doc, win, tester} = setup(native);
  doc.buttons[0].dispatchEvent(new Event("click"));
  enable(doc);
  doc.visibilityState = "hidden";
  doc.buttons[0].dispatchEvent(new Event("click"));
  assert.match(tester.message.textContent, /前台/);
  doc.visibilityState = "visible";
  win.navigator.userActivation.hasBeenActive = false;
  doc.buttons[0].dispatchEvent(new Event("click"));
  assert.match(tester.message.textContent, /先点击页面/);
  assert.deepEqual(calls, []);
});

/** Functionality: 验证原生拒绝/异常均显式失败且不自动重试。
 * Inputs: false或抛出NotAllowedError的原生替身。Outputs: 单次调用、错误文字、错误日志。
 * Logic: 两次独立setup覆盖返回值和异常分支。Constraints: 不是浏览器真实权限测试。
 */
test("拒绝与异常不会宣称接受、重试或触发替代提醒", function testFailures() {
  for (const throws of [false, true]) {
    let calls = 0;
    /** Functionality: 模拟明确失败。Inputs: pattern和当前throws。
     * Outputs: false或异常；调用计数递增。Logic: 保留本次失败。Constraints: 不重试。
     */
    function native(pattern) {
      assert.deepEqual(pattern, [200]);
      calls++;
      if (throws) throw Object.assign(new Error("Test permission rejection"), {name: "NotAllowedError"});
      return false;
    }
    const {doc, win, tester} = setup(native);
    enable(doc);
    doc.buttons[0].dispatchEvent(new Event("click"));
    assert.equal(calls, 1);
    assert.equal(tester.requested, false);
    assert.equal(tester.message.dataset.kind, "error");
    assert.match(tester.message.textContent, throws ? /调用异常/ : /拒绝/);
    if (throws) assert.equal(win.logs.at(-1).level, "error");
  }
});

/** Functionality: 验证关闭、后台和离页发送取消且返回前台不恢复震动。
 * Inputs: 成功原生替身与三个独立生命周期场景。Outputs: 仅[800]和0的调用断言。
 * Logic: 实际事件处理器共享取消路径。Constraints: 不代表锁屏或浏览器后台调用支持。
 */
test("关闭、隐藏或离页取消请求，回到前台不自动震动", function testLifecycle() {
  for (const reason of ["disabled", "hidden", "pagehide"]) {
    const calls = [];
    /** Functionality: 记录测试和取消。Inputs: pattern。Outputs: true/calls。
     * Logic: 合成接受。Constraints: 不反馈马达状态。
     */
    function native(pattern) { calls.push(pattern); return true; }
    const {doc, win, tester} = setup(native);
    enable(doc);
    doc.buttons[1].dispatchEvent(new Event("click"));
    if (reason === "disabled") {
      tester.enabled.checked = false;
      tester.enabled.dispatchEvent(new Event("change"));
      assert.ok(doc.buttons.every(button => button.disabled));
    } else if (reason === "hidden") {
      doc.visibilityState = "hidden";
      doc.dispatchEvent(new Event("visibilitychange"));
    } else win.dispatchEvent(new Event("pagehide"));
    doc.visibilityState = "visible";
    doc.dispatchEvent(new Event("visibilitychange"));
    win.dispatchEvent(new Event("pagehide"));
    assert.deepEqual(calls, [[800], 0]);
    assert.equal(tester.requested, false);
  }
});

/** Functionality: 验证取消拒绝不被关闭开关的成功提示覆盖。
 * Inputs: 首次接受、取消拒绝的原生替身。Outputs: 明确失败、保留请求记录和可点击停止。
 * Logic: 关闭后允许用户显式再点停止。Constraints: 不自动重试，不能确定先前节奏是否仍在运行。
 */
test("取消被拒绝时保留诊断并允许手动停止", function testCancelRejected() {
  const calls = [];
  /** Functionality: 接受节奏但拒绝取消。Inputs: pattern。
   * Outputs: 非0为true并记录calls。Logic: 合成取消失败。Constraints: 非实际设备状态。
   */
  function native(pattern) { calls.push(pattern); return pattern !== 0; }
  const {doc, tester} = setup(native);
  enable(doc);
  doc.buttons[0].dispatchEvent(new Event("click"));
  tester.enabled.checked = false;
  tester.enabled.dispatchEvent(new Event("change"));
  assert.deepEqual(calls, [[200], 0]);
  assert.equal(tester.requested, true);
  assert.equal(tester.stopButton.disabled, false);
  assert.match(tester.message.textContent, /拒绝/);
  assert.equal(tester.message.dataset.kind, "error");
});

/** Functionality: 验证震动区域及动态中文提示都有英文词典项。
 * Inputs: 真实模块源码和HTML。Outputs: 翻译无中文残留断言。
 * Logic: 检查模块中文字符串及区域文本/aria-label；不修改词典或业务参数。
 * Constraints: 静态检查不是浏览器MutationObserver、布局或翻译质量验证。
 */
test("震动控件及所有动态提示均有英文翻译", async function testTranslations() {
  const source = await readFile(new URL("../vibration.js", import.meta.url), "utf8");
  const html = await readFile(new URL("../index.html", import.meta.url), "utf8");
  const section = html.match(/<section id="vibration-test"[\s\S]*?<\/section>/)[0];
  const strings = [...source.matchAll(/"([^"\n]*\p{Script=Han}[^"\n]*)"/gu),
    ...section.matchAll(/>([^<>]*\p{Script=Han}[^<>]*)</gu),
    ...section.matchAll(/aria-label="([^"]+)"/g)].map(match => match[1].trim());
  assert.ok(strings.length > 20);
  for (const text of strings) assert.doesNotMatch(translate(text, "en"), /\p{Script=Han}/u, text);
});

/** Functionality: 验证MCP分派复用真实控制器，保持人工启用和激活要求。
 * Inputs: 控制器的DOM/原生替身，显式激活状态和固定协议命令。
 * Outputs: 状态、明确拒绝原因、节奏和取消的唯一调用序列。
 * Logic: 未启用或未激活不调用原生API；查询无副作用，启用后通过同一testPattern入口。
 * Constraints: 这里只验证命令分派和状态，不证明真实MCP网络、可信交互或物理马达。
 */
test("MCP查询和测试复用控制器且不自动启用或伪造激活", function testMcpShared() {
  const calls = [];
  /** Functionality: 捕获单次测试或取消。Inputs: pattern。
   * Outputs: true并更新calls。Logic: 显式接受替身。Constraints: 无实际硬件。
   */
  function native(pattern) { calls.push(pattern); return true; }
  const {doc, win, tester} = setup(native);
  const status = performVibration("vibration_status", {}, tester);
  assert.equal(status.enabled, false);
  assert.equal(status.hardware_verified, false);
  const disabled = performVibration("vibration_test", {pattern: "short"}, tester);
  assert.equal(disabled.accepted, false);
  assert.equal(disabled.reason, "disabled");
  assert.equal(tester.enabled.checked, false);
  enable(doc);
  win.navigator.userActivation.hasBeenActive = false;
  assert.equal(performVibration("vibration_test", {pattern: "long"}, tester).reason, "activation_required");
  assert.deepEqual(calls, []);
  win.navigator.userActivation.hasBeenActive = true;
  for (const pattern of ["short", "long", "double"]) {
    const result = performVibration("vibration_test", {pattern}, tester);
    assert.equal(result.accepted, true);
    assert.equal(result.pattern, pattern);
    assert.equal(result.reason, null);
    assert.equal(result.hardware_verified, false);
  }
  tester.enabled.checked = false;
  const stopped = performVibration("vibration_stop", {}, tester);
  assert.equal(stopped.accepted, true);
  assert.equal(stopped.request_pending, false);
  assert.equal(stopped.enabled, false);
  assert.deepEqual(calls, [[200], [800], [150, 100, 150], 0]);
});

/** Functionality: 验证缺失页面、接口缺失、后台及原生拒绝/异常的结构化区分。
 * Inputs: 受控接口返回值/异常、文档可见性；实际MCP浏览器分派函数。
 * Outputs: 明确reason和accepted=false，取消拒绝保留未取消记录；无自动重试。
 * Logic: 只改变测试边界，不改变生产控制器逻辑。Constraints: 无操作系统权限验证。
 */
test("MCP明确返回设备拒绝和异常并保留取消失败状态", function testMcpFailures() {
  assert.equal(performVibration("vibration_status", {}, null).reason, "page_unavailable");
  const unavailable = setup();
  assert.equal(performVibration("vibration_test", {pattern: "short"}, unavailable.tester).reason, "unsupported");
  assert.equal(performVibration("vibration_stop", {}, unavailable.tester).reason, "unsupported");
  let outcome = "accepted";
  const calls = [];
  /** Functionality: 返回预设接受/拒绝/异常。
   * Inputs: pattern/outcome。Outputs: bool或异常，记录单次调用。
   * Logic: 不执行实际震动。Constraints: 原生拒绝不能证明马达状态。
   */
  function native(pattern) {
    calls.push(pattern);
    if (outcome === "native_error") throw new Error("Explicit test error");
    return outcome === "accepted";
  }
  const {doc, tester} = setup(native);
  enable(doc);
  doc.visibilityState = "hidden";
  assert.equal(performVibration("vibration_test", {pattern: "short"}, tester).reason, "page_hidden");
  assert.deepEqual(calls, []);
  doc.visibilityState = "visible";
  performVibration("vibration_test", {pattern: "short"}, tester);
  outcome = "native_rejected";
  const stopped = performVibration("vibration_stop", {}, tester);
  assert.equal(stopped.accepted, false);
  assert.equal(stopped.reason, "native_rejected");
  assert.equal(stopped.request_pending, true);
  const rejected = performVibration("vibration_test", {pattern: "double"}, tester);
  assert.equal(rejected.reason, "native_rejected");
  outcome = "native_error";
  assert.equal(performVibration("vibration_test", {pattern: "long"}, tester).reason, "native_error");
  assert.deepEqual(calls, [[200], 0, [150, 100, 150], [800]]);
  assert.throws(() => performVibration("vibration_loop", {}, tester), /Unknown vibration command/);
});
