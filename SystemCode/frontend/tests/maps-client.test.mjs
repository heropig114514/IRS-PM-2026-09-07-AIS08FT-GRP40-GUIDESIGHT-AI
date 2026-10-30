/* Responsibilities: Test location permissions, coordinate order, route transport, and response contracts.
 * Implementation: Inject geolocation/fetch fixtures; live external-service integration is separate.
 * Related Modules: ../maps-client.js.
 * Declaration Index: routeFixture — synthetic route; responseFixture — synthetic HTTP response.
 * Variable Index: None; test-local counters do not change production configuration.
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

/* Functionality: Create an independent synthetic route. Inputs: None. Outputs: Valid route object.
 * Logic: Two fixture points and departure/arrival steps. Constraints: Does not represent device location or real ORS.
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

/* Functionality: Create a synthetic HTTP response. Inputs: data/status. Outputs: fetch response fixture.
 * Logic: Derive ok from status and return data asynchronously from json. Constraints: Does not simulate real network/TLS.
 */
function responseFixture(data, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => data };
}

// Functionality: Preserve location options. Inputs: navigator fixture. Outputs: Original-position assertion.
// Logic: One successful callback. Constraints: Does not request real permission or verify GPS.
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

// Functionality: Do not substitute after location failure. Inputs: Fixtures for each error code.
// Outputs: Rejection and call-count assertions. Logic: Trigger failures individually.
// Constraints: No automatic retry or sample/IP location.
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

// Functionality: Coordinate order and bounds. Inputs: Text/invalid values. Outputs: Exact conversions or exceptions.
// Logic: Missing values must not become zero; a real zero coordinate is valid.
// Constraints: Do not change production defaults.
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

// Functionality: Same-origin POST contract and credential boundary. Inputs: fetch fixture.
// Outputs: Body/headers/route assertions. Logic: Check coordinate order and absence of cookies.
// Constraints: Proves client transport parameters only.
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

// Functionality: Keep non-success statuses diagnosable. Inputs: HTTP 403/429/503 fixtures.
// Outputs: code/id and single-call assertions. Logic: Do not retry failures.
// Constraints: No real quota or authentication verification.
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

// Functionality: Verify timeout cancellation and non-JSON diagnostics. Inputs: Blocking/invalid-response fixtures.
// Outputs: Rejection. Logic: Fixture rejects only on abort; use a short test timeout.
// Constraints: Preserve the production 25-second default.
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

// Functionality: Reject invalid routes before map use. Inputs: Independent synthetic object for each case.
// Outputs: Rejection. Logic: Corrupt provider, coordinates, step indexes, and numeric fields separately.
// Constraints: Do not modify or repair returned data.
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

// Functionality: Reject identical-point requests before network access. Inputs: Two identical coordinates.
// Outputs: No fetch call. Logic: Validate input before creating the request.
// Constraints: Do not infer a substitute endpoint.
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
