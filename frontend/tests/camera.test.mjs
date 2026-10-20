/** Responsibilities: 验证取消、画布复用、原采集参数及首帧/稳态计时边界。
 * Implementation: 页面、媒体权限、轨道、画布及图像解码使用显式替身；不调用真实摄像头或网络。
 * Related Modules: camera.js；真机权限与浏览器行为仍须另行验证。
 * Declaration Index: FakeElement; FakeElement.constructor; FakeElement.addEventListener;
 * FakeElement.removeAttribute; FakeDocument; FakeDocument.constructor; FakeDocument.getElementById;
 * FakeDocument.createElement; FakeDocument.addEventListener; ignoreWindowEvent; makeApp;
 * FakeCanvas; FakeCanvas.constructor; FakeCanvas.width; FakeCanvas.height;
 * FakeCanvas.getContext; FakeCanvas.drawImage; FakeCanvas.toBlob;
 * testDenied; testDenied.denyCamera; testCancelled; testCancelled.waitForPermission;
 * testCancelled.waitForPermission.register; testCancelled.stopTrack;
 * testCancelled.getTracks; testProcessingPath; testApiMount; testDecodeCancelled;
 * testDecodeCancelled.waitForDecode;
 * testDecodeCancelled.waitForDecode.register; testCaptureReuse; testSceneHooks; testTimingBoundaries; testCameraEvents; testLocalDeviceGate。
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
/** Functionality: 跟踪尺寸赋值、绘制和编码参数。
 * Logic: 属性 setter 计数画布重置，toBlob 返回显式合成 marker；Constraints: 不执行真实绘制/编码。 */
class FakeCanvas extends FakeElement {
  /** Functionality: 初始化画布替身。Inputs: None。Outputs: 实例。
   * Logic: _width/_height存尺寸，resets计赋值，drawn/encoded存测试参数；Constraints: 不申请浏览器资源。 */
  constructor() {
    super();
    this._width = 0;
    this._height = 0;
    this.resets = 0;
    this.drawn = [];
    this.encoded = [];
  }
  /** Functionality: 读取宽度。Inputs: _width。Outputs: number。
   * Logic: 返回保存值；Constraints: 不计算布局。 */
  get width() { return this._width; }
  /** Functionality: 记录宽度赋值。Inputs: value。Outputs: 状态和计数更新。
   * Logic: 即使同值也计为重置，模拟实际画布属性语义；Constraints: 不分配像素缓冲。 */
  set width(value) { this._width = value; this.resets += 1; }
  /** Functionality: 读取高度。Inputs: _height。Outputs: number。
   * Logic: 返回保存值；Constraints: 不计算布局。 */
  get height() { return this._height; }
  /** Functionality: 记录高度赋值。Inputs: value。Outputs: 状态和计数更新。
   * Logic: 同值也计为重置；Constraints: 不分配像素缓冲。 */
  set height(value) { this._height = value; this.resets += 1; }
  /** Functionality: 提供绘制记录器。Inputs: kind。Outputs: 最小 context。
   * Logic: 仅接受2d，绑定真实测试记录方法；Constraints: 不执行图像渲染。 */
  getContext(kind) {
    assert.equal(kind, "2d");
    return {drawImage: this.drawImage.bind(this)};
  }
  /** Functionality: 记录目标尺寸。Inputs: video、x、y、width、height。Outputs: drawn 更新。
   * Logic: 核对来源和原点，保存尺寸；Constraints: 不读取真实视频像素。 */
  drawImage(video, x, y, width, height) {
    assert.ok(video);
    assert.equal(x, 0);
    assert.equal(y, 0);
    this.drawn.push([width, height]);
  }
  /** Functionality: 返回显式合成JPEG标记。Inputs: callback、mime、quality。Outputs: callback(Blob)。
   * Logic: 记录格式和质量；Constraints: marker不是可解码图像，测试只证明参数与控制逻辑。 */
  toBlob(callback, mime, quality) {
    this.encoded.push({mime, quality});
    callback(new Blob([Uint8Array.from([255, 216, 1, 255, 217])], {type: mime}));
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
  /** Functionality: 返回明确的元素替身。Inputs: tag。Outputs: FakeCanvas或FakeElement。
   * Logic: canvas跟踪尺寸/绘制/编码参数；Constraints: 无真实浏览器性能验证。 */
  createElement(tag) {
    return tag === "canvas" ? new FakeCanvas() : new FakeElement();
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
const { CameraApp, processingPath } = await import("../camera.js");

/** Functionality: Verify explicit Echo, HF, local and resident modes and their explanatory notes.
 * Inputs: actual mode mapping and retired/unknown enums. Outputs: fixed paths or explicit errors.
 * Logic: reject every former mode, rather than silently substituting a new provider.
 * Constraints: no external camera, socket, CPU detector or GPU request. */
test("Echo、HF与常驻GPU模式使用明确路径和提示", function testProcessingPath() {
  assert.equal(processingPath("echo"), "./ws/echo/");
  assert.equal(processingPath("hf"), "./ws/detect/hf/");
  assert.equal(processingPath("resident"), "./ws/detect/resident/");
  assert.equal(processingPath("local"), "./ws/detect/local/");
  const app = new CameraApp();
  for (const mode of ["echo", "hf", "resident", "local"]) {
    app.processing.value = mode;
    app.handleProcessingChange();
    assert.equal(document.getElementById("hf-note").hidden, mode !== "hf");
    assert.equal(document.getElementById("resident-note").hidden, mode !== "resident");
  }
  for (const mode of ["detect", "stream", "hf_stream", "hf_boxes", "hf_boxes_fast", "hf_live", "unknown"])
    assert.throws(() => processingPath(mode), /未知/);
});

/** Functionality: Verify event subscription and generation ownership. Inputs: actual controller and listener.
 * Outputs: subscribed result identity and no replay after unsubscribe.
 * Logic: stop publishes old-session cancellation before advancing generation; Constraints: no camera/network. */
test("camera publishes explicit session/result/stop events without DOM polling", function testCameraEvents() {
  const app = makeApp(ignoreWindowEvent), events = [], bytes = new ArrayBuffer(3);
  const unsubscribe = app.subscribe(event => events.push(event));
  app.sessionId = "local-session"; app.generation = 5;
  app.emit({type: "frame", result: {bytes}, capturedAt: 100});
  assert.equal(events[0].result.bytes, bytes); assert.equal(events[0].generation, 5);
  app.stop(); assert.equal(events[1].type, "stop"); assert.equal(events[1].sessionId, "local-session");
  unsubscribe(); app.emit({type: "frame"}); assert.equal(events.length, 2);
});
/** Functionality: Verify local-only provider/device gates occur before media authorization.
 * Inputs: local/HF mode and explicit local-only marker. Outputs: zero media calls until an explicit device.
 * Logic: no camera/facing fallback; Constraints: actual device selection is validated by the browser. */
test("local mode requires explicit device and local-only page blocks HF", async function testLocalDeviceGate() {
  let calls = 0;
  const app = makeApp(async () => { calls++; throw new Error("unexpected media call"); });
  app.processing.value = "local"; await app.start(); assert.equal(calls, 0);
  app.localOnly = true; app.processing.value = "hf"; await app.start(); assert.equal(calls, 0);
  assert.match(app.message.textContent, /本地 MVP/);
});

/** Functionality: Resolve the additive live page against the existing same-origin API mount.
 * Inputs: explicit /prs/live/ location and ../ mount. Outputs: fixed root API URLs/cross-origin error.
 * Logic: real controller URL resolution; Constraints: no socket/GPU or real page rendering. */
test("独立视频入口沿用同源API挂载点", function testApiMount() {
  const previous = window.location;
  window.location = {href: "https://47.239.50.129/prs/live/"};
  try {
    const app = new CameraApp(); app.apiBase = "../";
    assert.equal(app.apiUrl("./ws/detect/hf/").href, "https://47.239.50.129/prs/ws/detect/hf/");
    assert.equal(app.apiUrl(processingPath("echo")).href, "https://47.239.50.129/prs/ws/echo/");
    app.apiBase = "https://foreign.example/";
    assert.throws(() => app.apiUrl("./ws/detect/hf/"), /同源/);
  } finally { window.location = previous; }
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

/** Functionality: 检查稳定分辨率复用画布，旋转后更新尺寸，并保持原采集条件。
 * Inputs: 1280x960及960x1280视频尺寸、显式画布替身。Outputs: 重置次数/比例/格式/质量断言。
 * Logic: 调用实际capture三次，仅首次和旋转重置；Constraints: 不代表真实JPEG编码耗时或质量。 */
test("相同尺寸复用画布且保持640/JPEG0.8", async function testCaptureReuse() {
  const app = makeApp(ignoreWindowEvent);
  app.video.videoWidth = 1280;
  app.video.videoHeight = 960;
  await app.capture();
  await app.capture();
  assert.equal(app.canvas.resets, 2);
  app.video.videoWidth = 960;
  app.video.videoHeight = 1280;
  await app.capture();
  assert.equal(app.canvas.resets, 4);
  assert.deepEqual(app.canvas.drawn, [[640, 480], [640, 480], [480, 640]]);
  assert.deepEqual(app.canvas.encoded, Array(3).fill({mime: "image/jpeg", quality: 0.8}));
});

/** Functionality: 检查场景描述取帧/取检测框钩子。Inputs: 受控 mode/lastFrame/lastDetection。
 * Outputs: 复用、过期重采、空数组及停止清空断言。Logic: 实际 CameraApp 方法；
 * Constraints: 不启动摄像头或网络，时间用真实 performance.now 的相对年龄。 */
test("latestJpeg复用新鲜帧，过期重采，未开启返回null", async function testSceneHooks() {
  const app = makeApp(ignoreWindowEvent);
  app.video.videoWidth = 640;
  app.video.videoHeight = 480;
  assert.equal(await app.latestJpeg(), null);
  assert.deepEqual(app.latestBoxes(), []);
  app.mode = "camera";
  const fresh = new ArrayBuffer(3);
  app.lastFrame = {bytes: fresh, at: performance.now()};
  assert.equal(await app.latestJpeg(), fresh);
  app.lastFrame = {bytes: fresh, at: performance.now() - 10000};
  const recaptured = await app.latestJpeg();
  assert.notEqual(recaptured, fresh);
  assert.equal(recaptured.byteLength, 5);
  assert.equal(app.lastFrame.bytes, recaptured);
  const boxes = [{label: "person"}];
  app.lastDetection = {metadata: {boxes}, at: performance.now()};
  assert.equal(app.latestBoxes(), boxes);
  app.lastDetection = {metadata: {boxes}, at: performance.now() - 10000};
  assert.deepEqual(app.latestBoxes(), []);
  app.lastDetection = {metadata: undefined, at: performance.now()};
  assert.deepEqual(app.latestBoxes(), []);
  app.lastDetection = {metadata: {boxes: null}, at: performance.now()};
  assert.deepEqual(app.latestBoxes(), []);
  app.lastDetection = {metadata: {boxes}, at: performance.now()};
  app.stop();
  assert.equal(app.lastFrame, null);
  assert.equal(app.lastDetection, null);
});

/** Functionality: 检查首帧单独记录而慢稳态帧保留在延迟和速率统计中。
 * Inputs: 首帧无调度字段、零时钟间隔/慢帧及显式定时器/信用等待样本。
 * Outputs: 有限聚合、null、2.5FPS及仅有观测的调度统计断言。
 * Logic: 实际计时方法处理三帧；Constraints: 输入是受控时钟，不是外部服务性能测试。 */
test("慢首帧与稳态分开，停顿不被排除", function testTimingBoundaries() {
  const app = makeApp(ignoreWindowEvent);
  assert.equal(app.recordFrameTimings("hf", {
    captureMs: 10, transportMs: 1055, renderQueueMs: 0, decodeMs: 15,
    captureToResultMs: 1065, captureToDecodedMs: 1080, completedAt: 1080,
  }), null);
  assert.equal(app.frameTimings.pacingFrames, 0);
  assert.equal(app.recordFrameTimings("hf", {
    captureMs: 10, transportMs: 55, renderQueueMs: 0, decodeMs: 15,
    captureToResultMs: 65, captureToDecodedMs: 80, completedAt: 1080,
    pacingLateMs: 10, creditWaitMs: 0,
  }), null);
  assert.equal(app.recordFrameTimings("hf", {
    captureMs: 10, transportMs: 675, renderQueueMs: 0, decodeMs: 15,
    captureToResultMs: 685, captureToDecodedMs: 700, completedAt: 1880,
    pacingLateMs: 20, creditWaitMs: 90,
  }), 2.5);
  assert.equal(app.frameTimings.steadyFrames, 2);
  assert.equal(app.frameTimings.transportTotalMs, 730);
  assert.equal(app.frameTimings.captureToResultMaxMs, 685);
  assert.equal(app.frameTimings.captureToDecodedMaxMs, 700);
  assert.equal(app.frameTimings.first.captureToDecodedMs, 1080);
  assert.equal(app.frameTimings.pacingFrames, 2);
  assert.equal(app.frameTimings.pacingLateTotalMs, 30);
  assert.equal(app.frameTimings.pacingLateMaxMs, 20);
  assert.equal(app.frameTimings.creditWaitTotalMs, 90);
  assert.equal(app.frameTimings.creditWaitFrames, 1);
});
