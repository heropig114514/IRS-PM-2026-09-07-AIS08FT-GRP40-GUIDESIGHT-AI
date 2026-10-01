/* Responsibilities: SDK 网络/认证失败、定位权限和瓦片契约测试。
 * Implementation: 注入最小浏览器/SDK fake；不联系 Google 或申请真实位置。
 * Related Modules: ../maps-client.js。
 * Declaration Index: fixture — SDK 脚本环境。
 * Variable Index: None；各测试使用独立局部 fake 状态。
 */
import test from "node:test";
import assert from "node:assert/strict";
import {
  loadGoogleMaps,
  getDevicePosition,
  waitForTiles,
} from "../maps-client.js";

/* Functionality: 构造仅覆盖脚本加载的 DOM fake。
 * Inputs: None；Outputs: document/window 和捕获 script。
 * Logic: append 记录元素、remove 标记释放；Constraints: 无真实 CSP 或外网。
 */
function fixture() {
  const script = {
    remove() {
      this.removed = true;
    },
  };
  return {
    script,
    window: {},
    document: { createElement: () => script, head: { append() {} } },
  };
}

// Functionality: SDK 成功及加载后认证失败通知；Inputs: fake；Outputs: SDK/锁断言。
// Logic: 手动触发全局回调；Constraints: 不代表 Google 真实认证通过。
test("SDK callback and late authentication failure", async () => {
  const env = fixture();
  let failures = 0;
  const pending = loadGoogleMaps("fake-key", {
    ...env,
    nonce: "test-nonce",
    onAuthFailure: () => failures++,
  });
  assert.equal(new URL(env.script.src).hostname, "maps.googleapis.com");
  assert.equal(env.script.nonce, "test-nonce");
  env.window.google = { maps: { Map: class {} } };
  env.window.prsMapsReady();
  assert.equal(await pending, env.window.google.maps);
  env.window.gm_authFailure();
  assert.equal(failures, 1);
});

// Functionality: SDK 网络与超时失败；Inputs: 两个 fake；Outputs: rejection/释放断言。
// Logic: onerror 和短测试超时分别触发；Constraints: 测试超时不改生产默认值。
test("SDK network error and timeout are explicit", async () => {
  const env = fixture();
  const failed = loadGoogleMaps("fake-key", env);
  env.script.onerror();
  await assert.rejects(failed, /无法连接/);
  const timeout = fixture();
  await assert.rejects(
    loadGoogleMaps("fake-key", { ...timeout, timeoutMs: 5 }),
    /超时/,
  );
  assert.equal(timeout.script.removed, true);
  await assert.rejects(loadGoogleMaps(""), /未配置/);
});

// Functionality: 定位成功保留坐标和显式参数；Inputs: geolocation fake；Outputs: 结果断言。
// Logic: 返回合成坐标，不存储；Constraints: 不验证实际 GPS 或权限。
test("location preserves device coordinates and constraints", async () => {
  const position = {
    coords: { latitude: 1.3, longitude: 103.8, accuracy: 12 },
  };
  const result = await getDevicePosition({
    geolocation: {
      getCurrentPosition(success, failure, options) {
        assert.deepEqual(options, {
          enableHighAccuracy: true,
          timeout: 10000,
          maximumAge: 0,
        });
        success(position);
      },
    },
  });
  assert.equal(result, position);
});

// Functionality: 明确拒绝/不可用/超时；Inputs: 浏览器错误码 fake；Outputs: rejection。
// Logic: 每次只有一次定位调用；Constraints: 不采用 IP 或示例位置。
test("location errors preserve failure semantics", async () => {
  for (const [code, text] of [
    [1, /权限被拒绝/],
    [2, /无法获取/],
    [3, /超时/],
  ]) {
    let calls = 0;
    await assert.rejects(
      getDevicePosition({
        geolocation: {
          getCurrentPosition(success, failure) {
            calls++;
            failure({ code });
          },
        },
      }),
      text,
    );
    assert.equal(calls, 1);
  }
  await assert.rejects(getDevicePosition({}), /不支持/);
});

// Functionality: tilesloaded 与超时清理；Inputs: SDK 事件 fake；Outputs: 状态/释放断言。
// Logic: 手动触发瓦片事件；Constraints: 无真实地图瓦片。
test("tile readiness requires event and cleans timeout listener", async () => {
  let callback;
  let removed = 0;
  const maps = {
    event: {
      addListenerOnce(map, event, fn) {
        assert.equal(event, "tilesloaded");
        callback = fn;
        return 1;
      },
      removeListener() {
        removed++;
      },
    },
  };
  const ready = waitForTiles(maps, {});
  callback();
  await ready;
  await assert.rejects(waitForTiles(maps, {}, 5), /瓦片加载超时/);
  assert.equal(removed, 1);
});
