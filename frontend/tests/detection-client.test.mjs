/* Responsibilities: Verify detection transport, output boundaries and request cancellation.
 * Implementation: Injected fetch responses, real AbortController; no server, weights or camera.
 * Related Modules: detection-client.js; actual public inference verified separately.
 * Declaration Index: frameResponse; testSuccess; testSuccess.fetchFn; testMetadata;
 * testMetadata.fetchFn; testErrors; testErrors.fetchFn; testConfig; testConfig.fetchFn;
 * testCancelled; testCancelled.fetchFn; testCancelled.fetchFn.register;
 * testTimeout; testTimeout.fetchFn; testTimeout.fetchFn.register; testTimeout.fetchFn.register.aborted;
 * testLateConfig; testLateConfig.fetchFn; testLateConfig.fetchFn.json;
 * testLateConfig.fetchFn.json.register.
 * Variable Index: JPEG — synthetic magic bytes; URL — fake service base, no external requests.
 */
import assert from "node:assert/strict";
import test from "node:test";
import { DetectionClient } from "../detection-client.js";

const JPEG = Uint8Array.from([255, 216, 1, 255, 217]);
const URL = "https://test.invalid/vision/";

/* Functionality: Build a fake JPEG response. Inputs: headers/body overrides; Outputs: Response.
 * Logic: realistic HTTP metadata; Constraints: magic bytes aren't a decodable image fixture. */
function frameResponse(headers = {}, body = JPEG) {
  return new Response(body, {
    headers: {
      "Content-Type": "image/jpeg",
      "X-Model": "yolo26n",
      "X-Detection-Count": "5",
      "X-Inference-Ms": "600.5",
      ...headers,
    },
  });
}

/* Functionality: Check POST contract. Inputs: controlled response; Outputs: metadata assertions.
 * Logic: capture injected fetch arguments; Constraints: no real network/model accuracy proof. */
test("JPEG POST returns validated detection metadata", async function testSuccess() {
  let calls = 0;
  /* Functionality: Capture transport. Inputs: url/options; Outputs: fake JPEG response.
   * Logic: assert credentials/method/body; Constraints: never sends bytes externally. */
  async function fetchFn(url, options) {
    calls++;
    assert.equal(url, URL + "frame/");
    assert.equal(options.method, "POST");
    assert.equal(options.credentials, "omit");
    assert.equal(options.redirect, "error");
    assert.equal(options.body, JPEG.buffer);
    return frameResponse();
  }
  const result = await new DetectionClient(URL, { fetchFn }).sendFrame(
    JPEG.buffer,
  );
  assert.equal(result.detections, 5);
  assert.equal(result.inferenceMs, 600.5);
  assert.deepEqual(new Uint8Array(result.bytes), JPEG);
  assert.equal(calls, 1);
});

/* Functionality: Reject malformed metadata/JPEG. Inputs: independent fake boundaries.
 * Outputs: rejections; Logic: actual client checks; Constraints: no Pillow/browser decode. */
test("bad model, statistics and JPEG rejected", async function testMetadata() {
  for (const [headers, body] of [
    [{ "X-Model": "other" }, JPEG],
    [{ "X-Detection-Count": "301" }, JPEG],
    [{ "X-Inference-Ms": "NaN" }, JPEG],
    [{ "Content-Type": "image/png" }, JPEG],
    [{}, new Uint8Array([1, 2])],
    [{}, new Uint8Array(1048577)],
  ]) {
    /* Functionality: Supply one invalid response. Inputs: None; Outputs: Response.
     * Logic: explicit override; Constraints: mocked fetch only. */
    async function fetchFn() {
      return frameResponse(headers, body);
    }
    await assert.rejects(
      new DetectionClient(URL, { fetchFn }).sendFrame(JPEG.buffer),
    );
  }
});

/* Functionality: Surface safe server error. Inputs: synthetic busy error with request ID.
 * Outputs: one failed call; Logic: actual error parsing; Constraints: no retries/network. */
test("server busy error is surfaced without retry", async function testErrors() {
  let calls = 0;
  /* Functionality: Simulate busy. Inputs: None; Outputs: 429 JSON.
   * Logic: count calls; Constraints: no model/session involved. */
  async function fetchFn() {
    calls++;
    return Response.json(
      { detail: "busy", code: "vision_busy", request_id: "abc" },
      { status: 429 },
    );
  }
  await assert.rejects(
    new DetectionClient(URL, { fetchFn }).sendFrame(JPEG.buffer),
    /vision_busy.*abc/,
  );
  assert.equal(calls, 1);
});

/* Functionality: Enforce ready/80-class config. Inputs: fake ready and mismatched models.
 * Outputs: valid connection/rejection; Logic: actual connect; Constraints: not a model load test. */
test("configuration requires the chosen 80-class model", async function testConfig() {
  for (const classes of [80, 1]) {
    /* Functionality: Return controlled config. Inputs: None; Outputs: JSON Response.
     * Logic: override class count; Constraints: does not initialize YOLO. */
    async function fetchFn() {
      return Response.json({ ready: true, model: "yolo26n", classes });
    }
    const client = new DetectionClient(URL, { fetchFn });
    if (classes === 80) await client.connect();
    else await assert.rejects(client.connect(), /配置无效/);
  }
});

/* Functionality: Reject concurrent/late requests. Inputs: manually finished fake fetch.
 * Outputs: stopped result discarded; Logic: stop before completion; Constraints: mock ignores abort. */
test("stop discards late frame and rejects overlapping requests", async function testCancelled() {
  let finish;
  /* Functionality: Hold HTTP completion. Inputs: None; Outputs: pending Promise.
   * Logic: test drives response; Constraints: deliberately ignores native abort to test late data. */
  function fetchFn() {
    /* Functionality: Save completion. Inputs: resolve; Outputs: finish state.
     * Logic: defer; Constraints: test-only promise. */
    function register(resolve) {
      finish = resolve;
    }
    return new Promise(register);
  }
  const client = new DetectionClient(URL, { fetchFn });
  const pending = client.sendFrame(JPEG.buffer);
  await assert.rejects(client.sendFrame(JPEG.buffer), /已有图像/);
  client.stop();
  finish(frameResponse());
  await assert.rejects(pending, /已停止/);
  await assert.rejects(client.sendFrame(JPEG.buffer), /已停止/);
});

/* Functionality: Test abort timeout. Inputs: 5ms injected timer and abort-aware fake fetch.
 * Outputs: timeout rejection; Logic: native AbortController; Constraints: production remains 10s. */
test("timeout aborts instead of retrying", async function testTimeout() {
  /* Functionality: Observe abort. Inputs: url/options; Outputs: rejected Promise on abort.
   * Logic: subscribe to signal; Constraints: no network. */
  function fetchFn(url, options) {
    /* Functionality: Register abort listener. Inputs: resolve/reject; Outputs: subscription.
     * Logic: hold pending request; Constraints: no automatic response. */
    function register(resolve, reject) {
      void resolve;
      /* Functionality: Reject pending request. Inputs: abort event; Outputs: AbortError.
       * Logic: mirror fetch cancellation; Constraints: fake transport only. */
      function aborted() {
        reject(new DOMException("abort", "AbortError"));
      }
      options.signal.addEventListener("abort", aborted, { once: true });
    }
    return new Promise(register);
  }
  await assert.rejects(
    new DetectionClient(URL, { fetchFn, timeoutMs: 5 }).sendFrame(JPEG.buffer),
    /超过 10 秒/,
  );
});

/* Functionality: Discard config stopped during body read. Inputs: delayed JSON body.
 * Outputs: rejection; Logic: separate headers/body completion; Constraints: fake response only. */
test("stop while reading config prevents readiness", async function testLateConfig() {
  let finish;
  /* Functionality: Provide delayed body. Inputs: None; Outputs: minimal fake response.
   * Logic: json waits for test; Constraints: bypasses actual HTTP transport. */
  async function fetchFn() {
    /* Functionality: Defer JSON. Inputs: None; Outputs: Promise.
     * Logic: controlled body read; Constraints: only readiness fixture. */
    function json() {
      /* Functionality: Save resolver. Inputs: resolve; Outputs: finish state.
       * Logic: remain pending; Constraints: test completes explicitly. */
      function register(resolve) {
        finish = resolve;
      }
      return new Promise(register);
    }
    return { ok: true, json };
  }
  const client = new DetectionClient(URL, { fetchFn });
  const pending = client.connect();
  await Promise.resolve();
  client.stop();
  finish({ ready: true, model: "yolo26n", classes: 80 });
  await assert.rejects(pending, /已停止/);
});
