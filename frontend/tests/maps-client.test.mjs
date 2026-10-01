/* Responsibilities: 定位权限、坐标顺序、路线传输和返回契约测试。
 * Implementation: 注入定位/fetch fake；真实外部服务另做联调。
 * Related Modules: ../maps-client.js。
 * Declaration Index: routeFixture — 合成业务路线；responseFixture — 合成 HTTP 响应。
 * Variable Index: None；测试局部计数器不修改生产配置。
 */
import test from "node:test";
import assert from "node:assert/strict";
import {
  getDevicePosition,
  parseCoordinateInputs,
  requestWalkingRoute,
  validateRoute,
  turnLabel,
} from "../maps-client.js";

/* Functionality: 创建独立合成路线；Inputs: None；Outputs: 有效路线对象。
 * Logic: 两个测试点和出发/到达步骤；Constraints: 不代表设备位置或真实 ORS。
 */
function routeFixture() {
  return {
    provider: "openrouteservice",
    profile: "foot-walking",
    geometry: {
      type: "LineString",
      coordinates: [
        [103.7, 1.3],
        [103.8, 1.4],
      ],
    },
    summary: { distance_m: 100, duration_s: 60 },
    steps: [
      {
        type: 11,
        instruction: "Depart",
        distance_m: 100,
        duration_s: 60,
        way_points: [0, 1],
      },
      {
        type: 10,
        instruction: "Arrive",
        distance_m: 0,
        duration_s: 0,
        way_points: [1, 1],
      },
    ],
    attribution: "Test attribution",
    request_id: "fixture-id",
  };
}

/* Functionality: 合成 HTTP 响应；Inputs: data、status；Outputs: fetch 响应 fake。
 * Logic: 按状态确定 ok，json 异步返回 data；Constraints: 不模拟真实网络/TLS。
 */
function responseFixture(data, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => data };
}

// Functionality: 保留定位参数；Inputs: navigator fake；Outputs: 原位置断言。
// Logic: 单次成功回调；Constraints: 不申请真实权限或验证 GPS。
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

// Functionality: 定位失败不替代；Inputs: 各错误码 fake；Outputs: 拒绝及调用次数。
// Logic: 逐个触发失败；Constraints: 无自动重试和示例/IP 位置。
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

// Functionality: 经纬度顺序与边界；Inputs: 文本/非法值；Outputs: 精确转换/抛错。
// Logic: 缺失不能变成零，真实零点有效；Constraints: 不改变生产默认值。
test("coordinates preserve longitude first and reject missing values", () => {
  assert.deepEqual(parseCoordinateInputs("103.7", "1.3"), [103.7, 1.3]);
  assert.deepEqual(parseCoordinateInputs(0, 0), [0, 0]);
  for (const values of [
    ["", 0],
    [" ", 1],
    [true, 1],
    [181, 0],
    [0, 91],
    [NaN, 1],
    [Infinity, 0],
    [null, 0],
  ])
    assert.throws(() => parseCoordinateInputs(...values), /有效经纬度/);
});

// Functionality: 同源 POST 契约和凭据边界；Inputs: fetch fake；Outputs: body/headers/路线断言。
// Logic: 检查坐标顺序和无 Cookie；Constraints: 仅证明客户端传输参数。
test("walking route uses same-origin POST without a browser key", async () => {
  const route = routeFixture();
  let calls = 0;
  const result = await requestWalkingRoute([103.7, 1.3], [103.8, 1.4], {
    fetchFn: async (url, options) => {
      calls++;
      assert.equal(url, "./route/");
      assert.equal(options.method, "POST");
      assert.equal(options.mode, "same-origin");
      assert.equal(options.credentials, "omit");
      assert.deepEqual(options.headers, { "Content-Type": "application/json" });
      assert.deepEqual(JSON.parse(options.body), {
        start: [103.7, 1.3],
        end: [103.8, 1.4],
      });
      assert.equal(options.signal.aborted, false);
      return responseFixture(route);
    },
  });
  assert.equal(result, route);
  assert.equal(calls, 1);
});

// Functionality: 非成功状态可诊断；Inputs: HTTP 403/429/503 fake；Outputs: code/id 与单次调用。
// Logic: 失败不重试；Constraints: 无真实配额或认证验证。
test("HTTP errors expose safe code and request id without retry", async () => {
  for (const status of [403, 429, 503]) {
    let calls = 0;
    await assert.rejects(
      requestWalkingRoute([1, 2], [3, 4], {
        fetchFn: async () => {
          calls++;
          return responseFixture(
            { detail: "明确失败", code: "test_error", request_id: "test-id" },
            status,
          );
        },
      }),
      /明确失败.*test_error.*test-id/,
    );
    assert.equal(calls, 1);
  }
});

// Functionality: 超时终止及非 JSON 诊断；Inputs: 阻塞/非法响应 fake；Outputs: 拒绝。
// Logic: fake 仅在 abort 时拒绝，短超时用于测试；Constraints: 不修改生产 25 秒默认值。
test("abort and malformed response fail explicitly", async () => {
  let calls = 0;
  await assert.rejects(
    requestWalkingRoute([1, 2], [3, 4], {
      timeoutMs: 5,
      fetchFn: async (url, { signal }) => {
        calls++;
        return new Promise((resolve, reject) =>
          signal.addEventListener("abort", () => reject(new Error("aborted"))),
        );
      },
    }),
    /超过 25 秒/,
  );
  assert.equal(calls, 1);
  await assert.rejects(
    requestWalkingRoute([1, 2], [3, 4], {
      fetchFn: async () => ({
        ok: false,
        status: 502,
        json: async () => {
          throw new SyntaxError("private upstream body");
        },
      }),
    }),
    /不可解析的 JSON.*502/,
  );
  await assert.rejects(
    requestWalkingRoute([1, 2], [3, 4], {
      fetchFn: async () => {
        throw new TypeError("network");
      },
    }),
    /网络请求失败/,
  );
});

// Functionality: 非法路线不得进入地图；Inputs: 每次独立合成对象；Outputs: 拒绝。
// Logic: 分别破坏 provider/坐标/步骤索引/数值；Constraints: 不修改或修复返回内容。
test("route validation rejects invalid geometry and instructions", () => {
  assert.equal(validateRoute(routeFixture()).steps.length, 2);
  for (const mutate of [
    (r) => {
      r.provider = "wrong";
    },
    (r) => {
      r.geometry.coordinates[0][0] = 181;
    },
    (r) => {
      r.geometry.coordinates[0][0] = "103.7";
    },
    (r) => {
      r.steps[0].way_points[1] = 99;
    },
    (r) => {
      r.steps[0].type = 14;
    },
    (r) => {
      r.steps[0].instruction = "";
    },
    (r) => {
      r.summary.duration_s = -1;
    },
  ]) {
    const route = routeFixture();
    mutate(route);
    assert.throws(() => validateRoute(route));
  }
  assert.equal(turnLabel(10), "到达终点");
  for (let type = 0; type <= 13; type++) assert.ok(turnLabel(type));
  assert.throws(() => turnLabel("1"), /未知/);
});

// Functionality: 同点请求在联网前失败；Inputs: 两个相同坐标；Outputs: 无 fetch 调用。
// Logic: 输入校验在创建请求前执行；Constraints: 不推断替代终点。
test("identical endpoints never call the backend", async () => {
  let calls = 0;
  await assert.rejects(
    requestWalkingRoute([1, 2], [1, 2], {
      fetchFn: async () => {
        calls++;
      },
    }),
    /不能相同/,
  );
  assert.equal(calls, 0);
});
