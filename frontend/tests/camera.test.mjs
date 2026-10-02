/** Responsibilities: 验证摄像头拒绝、停止后的延迟授权及延迟解码不会恢复采集。
 * Implementation: 页面、媒体权限、轨道及图像解码使用显式替身；不调用真实摄像头或网络。
 * Related Modules: camera.js；真机权限与浏览器行为仍须另行验证。
 * Declaration Index: FakeElement; FakeElement.constructor; FakeElement.addEventListener;
 * FakeElement.removeAttribute; FakeDocument; FakeDocument.constructor; FakeDocument.getElementById;
 * FakeDocument.createElement; FakeDocument.addEventListener; ignoreWindowEvent; makeApp;
 * testDenied; testDenied.denyCamera; testCancelled; testCancelled.waitForPermission;
 * testCancelled.waitForPermission.register; testCancelled.stopTrack;
 * testCancelled.getTracks; testDetectionUrl; testDecodeCancelled;
 * testDecodeCancelled.waitForDecode;
 * testDecodeCancelled.waitForDecode.register。
 * Variable Index: None；所有 mock 限于 Node 的独立测试文件进程。
 */
import assert from "node:assert/strict";
import test from "node:test";

/** Functionality: 最小 DOM 替身。Logic: 保存控件值，不执行渲染。
 * Constraints: 不能证明真实浏览器布局、视频播放或解码。 */
class FakeElement {
  /** Functionality: 建立可变 DOM 状态。Inputs: None。Outputs: 实例。
   * Logic: camera 默认后置，dataset 可写；Constraints: 不启动任何设备。 */
  constructor() {
    this.dataset = {};
    this.hidden = false;
    this.value = "environment";
    this.src = "";
  }
  /** Functionality: 接受事件注册。Inputs: name、handler。Outputs: 无。
   * Logic: 测试直接调用实际控制器；Constraints: 不模拟事件传播。 */
  addEventListener(name, handler) {
    void name;
    void handler;
  }
  /** Functionality: 清除 src。Inputs: name。Outputs: 更新 src。
   * Logic: 只支持本测试用到的属性；Constraints: 不是完整 DOM。 */
  removeAttribute(name) {
    if (name === "src") this.src = "";
  }
}
/** Functionality: 提供受控页面节点。Logic: 缺失节点按需创建。
 * Constraints: 不执行 HTML，独立于真实浏览器。 */
class FakeDocument {
  /** Functionality: 初始化节点集合。Inputs: None。Outputs: 实例。
   * Logic: 使用 Map 存节点；Constraints: 不读取文件。 */
  constructor() {
    this.elements = new Map();
  }
  /** Functionality: 提供稳定节点。Inputs: id。Outputs: FakeElement。
   * Logic: 首次访问创建，后续复用；Constraints: 无选择器解析。 */
  getElementById(id) {
    if (!this.elements.has(id)) this.elements.set(id, new FakeElement());
    return this.elements.get(id);
  }
  /** Functionality: 返回画布占位符。Inputs: tag。Outputs: FakeElement。
   * Logic: 仅用于控制器构造；Constraints: 本测试不执行 drawImage。 */
  createElement(tag) {
    void tag;
    return new FakeElement();
  }
  /** Functionality: 接受页面事件注册。Inputs: name、handler。Outputs: 无。
   * Logic: 不自动派发；Constraints: 不模拟系统后台状态。 */
  addEventListener(name, handler) {
    void name;
    void handler;
  }
}
/** Functionality: 接受窗口事件。Inputs: name、handler。Outputs: 无。
 * Logic: 保持构造契约；Constraints: 无实际窗口。 */
function ignoreWindowEvent(name, handler) {
  void name;
  void handler;
}
globalThis.document = new FakeDocument();
globalThis.window = {
  isSecureContext: true,
  addEventListener: ignoreWindowEvent,
};
const { CameraApp, detectionUrl, detectionSettings } = await import("../camera.js");

/** Functionality: Check explicit detection destinations. Inputs: mode and existing PRS page URL.
 * Outputs: fixed same-origin paths or error. Logic: use actual mapping function.
 * Constraints: no network, model or browser; no automatic CPU substitution for laptop. */
test("检测模式只映射指定后端", function testDetectionUrl() {
  assert.equal(detectionUrl("laptop", "https://test.invalid/prs/").href,
    "https://test.invalid/prs/vision/laptop/");
  assert.equal(detectionUrl("detect", "https://test.invalid/prs/").href,
    "https://test.invalid/prs/vision/");
  assert.equal(detectionUrl("hf", "https://test.invalid/prs/").href,
    "https://test.invalid/prs/vision/hf/");
  assert.equal(detectionSettings("hf").timeoutMs, 60000);
  assert.equal(detectionSettings("hf").backend, "PyTorch ZeroGPU");
  for (const mode of ["echo", "detect", "laptop"]) assert.equal(detectionSettings(mode).timeoutMs, 10000);
  assert.throws(() => detectionUrl("unknown", "https://test.invalid/prs/"), /未知/);
});

/** Functionality: 创建隔离控制器。Inputs: getUserMedia 替身。Outputs: CameraApp。
 * Logic: 更换 fake document/navigator，设置原样模式；Constraints: 测试不验证实际模型。 */
function makeApp(getUserMedia) {
  globalThis.document = new FakeDocument();
  globalThis.document.getElementById("processing").value = "echo";
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: { mediaDevices: { getUserMedia } },
  });
  return new CameraApp();
}

test("权限拒绝后释放状态并显示错误" /** Functionality: 测试权限拒绝分支。
 * Inputs: 明确拒绝的权限替身。Outputs: idle/错误/可重启断言。
 * Logic: 执行实际 start；Constraints: 未访问真实权限服务。 */, async function testDenied() {
  /** Functionality: 拒绝摄像头。Inputs: 浏览器约束。Outputs: rejected Promise。
   * Logic: 合成 NotAllowed 场景；Constraints: 不访问硬件。 */
  async function denyCamera() {
    throw new Error("permission denied");
  }
  const app = makeApp(denyCamera);
  await app.start();
  assert.equal(app.mode, "idle");
  assert.equal(app.stream, null);
  assert.equal(app.status.dataset.kind, "error");
  assert.equal(app.startButton.disabled, false);
});

test("停止后的延迟授权不会打开连接并立即释放轨道" /** Functionality: 测试取消授权竞态。
 * Inputs: 延迟权限与 mock 轨道。Outputs: stop 次数及资源断言。
 * Logic: 先 stop 再完成授权；Constraints: 不能代表真机的授权 UI。 */, async function testCancelled() {
  let finish;
  let stopped = 0;
  /** Functionality: 延迟权限结果。Inputs: 采集约束。Outputs: 未完成 Promise。
   * Logic: 测试控制 resolve 时刻；Constraints: 无真实权限提示。 */
  function waitForPermission() {
    /** Functionality: 保存完成句柄。Inputs: resolve。Outputs: finish 更新。
     * Logic: 暂不返回媒体；Constraints: 测试显式完成。 */
    function register(resolve) {
      finish = resolve;
    }
    return new Promise(register);
  }
  /** Functionality: 计数轨道释放。Inputs: None。Outputs: stopped 更新。
   * Logic: 记录 stop 调用；Constraints: 非实际媒体轨道。 */
  function stopTrack() {
    stopped += 1;
  }
  /** Functionality: 提供轨道替身。Inputs: None。Outputs: 一条 mock 轨道。
   * Logic: 只实现取消场景；Constraints: 不生成视频。 */
  function getTracks() {
    return [{ stop: stopTrack }];
  }
  const app = makeApp(waitForPermission);
  const started = app.start();
  app.stop();
  finish({ getTracks });
  await started;
  assert.equal(stopped, 1);
  assert.equal(app.stream, null);
  assert.equal(app.client, null);
  assert.equal(app.mode, "idle");
});

test("停止后的延迟解码不会重新显示旧帧" /** Functionality: 测试图像解码取消竞态。
 * Inputs: mock decode Promise 和合成二进制。Outputs: 隐藏及 URL 释放断言。
 * Logic: 解码期间 stop，随后完成解码；Constraints: 不验证 JPEG 可解码性。 */, async function testDecodeCancelled() {
  let finish;
  /** Functionality: 延迟图像解码。Inputs: None。Outputs: 未完成 Promise。
   * Logic: 保存完成控制点；Constraints: 非浏览器图像解码器。 */
  function waitForDecode() {
    /** Functionality: 保存完成句柄。Inputs: resolve。Outputs: finish 更新。
     * Logic: 暂不完成；Constraints: 测试显式完成。 */
    function register(resolve) {
      finish = resolve;
    }
    return new Promise(register);
  }
  const app = makeApp(waitForDecode);
  app.image.decode = waitForDecode;
  const displaying = app.showFrame(
    new Uint8Array([1, 2]).buffer,
    app.generation,
  );
  app.stop();
  finish();
  await displaying;
  assert.equal(app.image.hidden, true);
  assert.equal(app.objectUrl, null);
  assert.equal(document.getElementById("verified").hidden, true);
});
