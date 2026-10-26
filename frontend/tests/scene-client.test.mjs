/** Responsibilities: Verify scene-client object mapping/truncation, request bodies, error display, speech, and same-origin boundaries.
 * Implementation: Use explicit fixtures for DOM, fetch, frame source, and SpeechOutput; no network or real VLM calls.
 * Related Modules: scene-client.js, translations.js, i18n.js; real browser/phone behavior is verified separately.
 * Declaration Index: Control; Control.constructor; Control.addEventListener; makeDoc; makeClient;
 * jsonResponse; box; testMapping; testMapBoxAlignment; testRequestShape; testNoCameraAndQuestionLimit; testErrors;
 * testNetworkAndBusy; testUnexpected; testSpeech; testSameOrigin; testTranslations; okFetch; testDescribeSceneSuccess; testDescribeSceneErrors;
 * testDescribeSceneTimeout; testDescribeSceneInvalidImage; testDescribeSceneBlobQuestion; testDescribeSceneDefaults;
 * testDescribeCurrentScene。
 * Variable Index: JPEG — synthetic five-byte FF D8 ... FF D9 marker; other state is test-local.
 */
import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {SceneClient, SceneApiError, describeScene, describeCurrentScene, mapObjects, encodeBase64, sceneLanguage, errorMessage, resolveSceneUrl,
  ERROR_MESSAGES, MAX_OBJECTS} from "../scene-client.js";
import {translate} from "../i18n.js";

const JPEG = Uint8Array.from([0xff, 0xd8, 1, 0xff, 0xd9]).buffer;

/** Functionality: Minimal control fixture. Logic: Store values/state and record click handlers. Constraints: Not a complete DOM. */
class Control {
  /** Functionality: Initialize. Inputs: None. Outputs: Control. Logic: Empty value by default. Constraints: No event propagation. */
  constructor() { Object.assign(this, {value: "", textContent: "", disabled: false, dataset: {}, handlers: {}}); }
  /** Functionality: Register an event. Inputs: name, handler. Outputs: None. Logic: Store the handler. Constraints: Do not dispatch. */
  addEventListener(name, handler) { this.handlers[name] = handler; }
}

/** Functionality: Create scene-region DOM. Inputs: lang. Outputs: doc fixture.
 * Logic: Fixed-ID controls plus documentElement.dataset.apiBase. Constraints: No HTML parsing. */
function makeDoc(lang = "zh-CN") {
  const nodes = new Map(["scene-describe", "scene-question", "scene-result", "scene-status", "scene-message"]
    .map(id => [id, new Control()]));
  return {documentElement: {lang, dataset: {apiBase: "../"}}, nodes,
    getElementById(id) { return nodes.get(id); }};
}

/** Functionality: Create a JSON Response-style fixture. Inputs: status, body. Outputs: Object.
 * Logic: Derive ok from status. Constraints: No headers. */
function jsonResponse(status, body) {
  return {ok: status >= 200 && status < 300, status, json: async () => body};
}

/** Functionality: Create a client and call record. Inputs: Options. Outputs: {client, doc, calls, speech}.
 * Logic: fetch fixture records URL/init; source returns fixed frame and boxes. Constraints: No real network or speech. */
function makeClient({response = jsonResponse(200, {text: "前方有人。", request_id: "r1", elapsed_ms: 5}),
  boxes = [], frame = JPEG, speechEnabled = false, lang = "zh-CN", fetchImpl} = {}) {
  const doc = makeDoc(lang), calls = [], spoken = [];
  const speech = {enabled: {checked: speechEnabled}, speak: (text, options) => { spoken.push([text, options]); }};
  const win = {location: {href: "https://prs.example/live/"}, console: {info() {}, warn() {}}};
  const client = new SceneClient({doc, win, source: {latestJpeg: async () => frame, latestBoxes: () => boxes},
    speech, fetchImpl: fetchImpl || (async (url, init) => { calls.push({url, init}); return response; })});
  return {client, doc, calls, spoken};
}

/** Functionality: Build a synthetic HF detection box. Inputs: Field overrides. Outputs: box.
 * Logic: Include all risk fields. Constraints: Values are synthetic. */
function box(extra = {}) {
  return {class_id: 0, label: "person", score: 0.9, xyxy: [0, 0, 10, 10], distance_m: 3, track_id: 7,
    motion_state: "approaching", relative_speed_mps: -1, smoothed_distance_m: 3, direction: "front",
    ttc_sec: 2, risk_score: 80, risk_level: "high", alert_priority: 3, alert_required: true,
    risk_reasons: ["x"], ...extra};
}

test("Map objects, omit missing fields, rank, and truncate to 50", function testMapping() {
  const mapped = mapObjects([box()])[0];
  assert.deepEqual(mapped, {label: "person", confidence: 0.9, distance_m: 3, direction: "front",
    risk_level: "high", motion_state: "approaching", track_id: 7});
  const bare = mapObjects([{label: "car", score: 0.5}])[0];
  assert.deepEqual(bare, {label: "car", confidence: 0.5});
  assert.deepEqual(mapObjects([{label: "", score: 0.5}, {label: "x".repeat(41), score: 0.5}, null]), []);
  assert.deepEqual(mapObjects(null), []);
  const ranked = mapObjects([
    box({label: "far", alert_priority: 0, distance_m: 9}),
    box({label: "nodist", alert_priority: 0, distance_m: null}),
    box({label: "near", alert_priority: 0, distance_m: 1}),
    box({label: "urgent", alert_priority: 4, distance_m: 20}),
  ]).map(item => item.label);
  assert.deepEqual(ranked, ["urgent", "near", "far", "nodist"]);
  const many = Array.from({length: 120}, (_, i) => box({label: "l" + i, distance_m: i + 1, alert_priority: 0}));
  const out = mapObjects(many);
  assert.equal(out.length, MAX_OBJECTS);
  assert.equal(out[0].label, "l0");
  assert.equal(many.length, 120);
});

test("mapBox matches backend validation for trim, control characters, score type, and millimeter distance", function testMapBoxAlignment() {
  assert.equal(mapObjects([{label: "   ", score: 0.5}, {label: "a\tb", score: 0.5}, {label: "a\u0000", score: 0.5}]).length, 0);
  assert.equal(mapObjects([{label: "x".repeat(41), score: 0.5}, {label: " " + "x".repeat(41) + " ", score: 0.5}]).length, 0);
  assert.equal(mapObjects([{label: " " + "x".repeat(40) + " ", score: 0.5}])[0].label, "x".repeat(40));
  assert.equal(mapObjects([{label: " person ", score: 0.5}])[0].label, "person");
  assert.deepEqual(mapObjects([{label: "a", score: null}, {label: "a", score: "0.9"}, {label: "a", score: NaN},
    {label: "a", score: 1.1}, {label: "a"}]), []);
  assert.deepEqual(mapObjects([{label: "a", score: 0.5, distance_m: 0.0001}]), [{label: "a", confidence: 0.5}]);
  assert.equal(mapObjects([{label: "a", score: 0.5, distance_m: 0.0006}])[0].distance_m, 0.0006);
});

test("Request body matches the contract and displays the result", async function testRequestShape() {
  const {client, doc, calls} = makeClient({boxes: [box()]});
  doc.nodes.get("scene-question").value = "  前面是什么？ ";
  await client.describe();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "https://prs.example/scene/describe/");
  assert.equal(calls[0].init.method, "POST");
  assert.equal(calls[0].init.headers["Content-Type"], "application/json");
  const body = JSON.parse(calls[0].init.body);
  assert.deepEqual(Object.keys(body).sort(), ["image_jpeg_base64", "language", "objects", "question"]);
  assert.equal(body.language, "zh-CN");
  assert.equal(body.question, "前面是什么？");
  assert.deepEqual(Buffer.from(body.image_jpeg_base64, "base64"), Buffer.from(JPEG));
  assert.equal(body.objects.length, 1);
  assert.equal(doc.nodes.get("scene-result").textContent, "前方有人。");
  assert.equal(doc.nodes.get("scene-describe").disabled, false);
  assert.equal(doc.nodes.get("scene-status").textContent, "已完成");
  const plain = makeClient({lang: "en"});
  await plain.client.describe();
  const minimal = JSON.parse(plain.calls[0].init.body);
  assert.deepEqual(Object.keys(minimal).sort(), ["image_jpeg_base64", "language"]);
  assert.equal(minimal.language, "en-US");
  assert.equal(sceneLanguage("zh-CN"), "zh-CN");
  assert.equal(sceneLanguage("en"), "en-US");
  assert.equal(encodeBase64(new Uint8Array(70000)).length, Math.ceil(70000 / 3) * 4);
});

test("Do not send a request when the camera is stopped or the question is too long", async function testNoCameraAndQuestionLimit() {
  const none = makeClient({frame: null});
  await none.client.describe();
  assert.equal(none.calls.length, 0);
  assert.equal(none.doc.nodes.get("scene-message").textContent, "请先开启摄像头再描述场景。");
  assert.equal(none.doc.nodes.get("scene-describe").disabled, false);
  const long = makeClient();
  long.doc.nodes.get("scene-question").value = "问".repeat(201);
  await long.client.describe();
  assert.equal(long.calls.length, 0);
  assert.equal(long.doc.nodes.get("scene-message").textContent, "问题最多200个字符。");
  long.doc.nodes.get("scene-question").value = "问".repeat(200);
  await long.client.describe();
  assert.equal(long.calls.length, 1);
});

test("Map each backend error code to localized feedback and request_id without showing detail", async function testErrors() {
  for (const code of Object.keys(ERROR_MESSAGES)) {
    const {client, doc, calls} = makeClient({response: jsonResponse(503, {code, detail: "SECRET-DETAIL", request_id: "rid-9"})});
    doc.nodes.get("scene-result").textContent = "旧结果";
    await client.describe();
    assert.equal(calls.length, 1, "no retry " + code);
    const shown = doc.nodes.get("scene-message").textContent;
    assert.equal(shown, `${ERROR_MESSAGES[code]}（rid-9）`);
    assert.ok(!shown.includes("SECRET"));
    assert.equal(doc.nodes.get("scene-result").textContent, "");
    assert.equal(doc.nodes.get("scene-describe").disabled, false);
    assert.equal(doc.nodes.get("scene-message").dataset.kind, "error");
    assert.notEqual(translate(shown, "en"), shown, code);
  }
  assert.equal(errorMessage("weird_code", undefined), "场景描述失败：weird_code");
  assert.equal(translate("场景描述失败：weird_code", "en"), "Scene description failed: weird_code");
  assert.equal(errorMessage({}, "bad id!"), "场景描述失败。");
  const invalid = makeClient({response: jsonResponse(200, {text: "  "})});
  await invalid.client.describe();
  assert.equal(invalid.doc.nodes.get("scene-message").textContent, "场景描述响应格式无效。");
});

test("Show network/timeout errors, ignore duplicate clicks while busy, and do not retry", async function testNetworkAndBusy() {
  const failing = makeClient({fetchImpl: async () => { throw new TypeError("net"); }});
  await failing.client.describe();
  assert.equal(failing.doc.nodes.get("scene-message").textContent, "场景描述请求失败，请检查网络后重试。");
  const aborted = makeClient({fetchImpl: async () => { throw Object.assign(new Error("a"), {name: "AbortError"}); }});
  await aborted.client.describe();
  assert.equal(aborted.doc.nodes.get("scene-message").textContent, "场景描述请求超时，请重试。");
  let release, count = 0;
  const gate = new Promise(resolve => { release = resolve; });
  const slow = makeClient({fetchImpl: async () => { count += 1; await gate; return jsonResponse(200, {text: "ok"}); }});
  const first = slow.client.describe();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(slow.doc.nodes.get("scene-describe").disabled, true);
  await slow.client.describe();
  assert.equal(count, 1);
  release();
  await first;
  assert.equal(slow.doc.nodes.get("scene-describe").disabled, false);
  assert.equal(slow.doc.nodes.get("scene-result").textContent, "ok");
});

test("Speak only when speech output is enabled and use the response language", async function testSpeech() {
  const off = makeClient({speechEnabled: false});
  await off.client.describe();
  assert.equal(off.spoken.length, 0);
  assert.equal(off.doc.nodes.get("scene-result").textContent, "前方有人。");
  const on = makeClient({speechEnabled: true, lang: "en"});
  await on.client.describe();
  assert.deepEqual(on.spoken, [["前方有人。", {language: "en-US"}]]);
  const failed = makeClient({speechEnabled: true, response: jsonResponse(502, {code: "vlm_upstream_error", request_id: "r"})});
  await failed.client.describe();
  assert.equal(failed.spoken.length, 0);
});

test("Unexpected exceptions show generic localized feedback without leaking raw text", async function testUnexpected() {
  const boom = makeClient({boxes: undefined});
  boom.client.source.latestBoxes = () => { throw new TypeError("Cannot read properties of undefined (reading 'boxes')"); };
  await boom.client.describe();
  assert.equal(boom.calls.length, 0);
  assert.equal(boom.doc.nodes.get("scene-message").textContent, "场景描述失败。");
  assert.equal(boom.doc.nodes.get("scene-describe").disabled, false);
  const capture = makeClient();
  capture.client.source.latestJpeg = async () => { throw new Error("raw English capture error"); };
  await capture.client.describe();
  assert.equal(capture.doc.nodes.get("scene-message").textContent, "无法获取摄像头画面，请稍后重试。");
  assert.notEqual(translate("无法获取摄像头画面，请稍后重试。", "en"), "无法获取摄像头画面，请稍后重试。");
});

test("API URL follows data-api-base and must remain same-origin", function testSameOrigin() {
  assert.equal(resolveSceneUrl("../", "scene/describe/", "https://h.example/prs/live/").href,
    "https://h.example/prs/scene/describe/");
  assert.equal(resolveSceneUrl("./", "scene/describe/", "https://h.example/").href, "https://h.example/scene/describe/");
  assert.throws(() => resolveSceneUrl("https://evil.example/", "scene/describe/", "https://h.example/"), /同源/);
});

test("Page, allowlisted messages, and translations cover the scene region", async function testTranslations() {
  const html = await readFile(new URL("../index.html", import.meta.url), "utf8");
  for (const id of ["scene-describe", "scene-question", "scene-result", "scene-status"])
    assert.match(html, new RegExp(`id="${id}"`));
  assert.match(html, /id="scene-question"[^>]*maxlength="200"/);
  assert.match(html, /id="scene-result"[^>]*aria-live="polite"/);
  assert.match(html, /src="\.\/assets\/scene-client\.js"/);
  assert.equal(translate("描述场景", "en"), "Describe scene");
  assert.equal(translate("场景描述", "en"), "Scene description");
  assert.equal(translate("待命", "en"), "Idle");
});

const PAGE = "https://prs.example/maps/";
const OK = {text: "前方有人。", model: "m", language: "zh-CN", elapsed_ms: 5, usage: {}, request_id: "r1"};

/** Functionality: Create a fetch fixture that records calls. Inputs: response. Outputs: {fetchImpl, calls}.
 * Logic: Return a fixed response. Constraints: No network. */
function okFetch(response = jsonResponse(200, OK)) {
  const calls = [];
  return {calls, fetchImpl: async (url, init) => { calls.push({url, init}); return response; }};
}

test("describeScene returns a successful response and builds a contract-shaped request", async function testDescribeSceneSuccess() {
  const {calls, fetchImpl} = okFetch();
  const result = await describeScene(JPEG, [box()], "前面能走吗？", {language: "zh-CN", apiBase: "./", fetchImpl, pageHref: PAGE});
  assert.deepEqual(result, OK);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "https://prs.example/maps/scene/describe/");
  const body = JSON.parse(calls[0].init.body);
  assert.equal(body.question, "前面能走吗？");
  assert.equal(body.objects.length, 1);
  assert.deepEqual(Buffer.from(body.image_jpeg_base64, "base64"), Buffer.from(JPEG));
  await describeScene(new Uint8Array(JPEG), undefined, "  ", {language: "en-US", apiBase: "../", fetchImpl, pageHref: PAGE});
  const bare = JSON.parse(calls[1].init.body);
  assert.deepEqual(Object.keys(bare).sort(), ["image_jpeg_base64", "language"]);
  assert.equal(calls[1].url, "https://prs.example/scene/describe/");
});

test("describeScene maps errors to SceneApiError fields without retrying", async function testDescribeSceneErrors() {
  for (const code of Object.keys(ERROR_MESSAGES)) {
    const {calls, fetchImpl} = okFetch(jsonResponse(503, {code, detail: "SECRET", request_id: "rid-9"}));
    await assert.rejects(describeScene(JPEG, [], null, {language: "zh-CN", apiBase: "./", fetchImpl, pageHref: PAGE}), error => {
      assert.ok(error instanceof SceneApiError && error instanceof Error);
      assert.equal(error.code, code);
      assert.equal(error.status, 503);
      assert.equal(error.requestId, "rid-9");
      assert.equal(error.message, `${ERROR_MESSAGES[code]}（rid-9）`);
      return true;
    });
    assert.equal(calls.length, 1);
  }
  const opts = {language: "zh-CN", apiBase: "./", pageHref: PAGE};
  await assert.rejects(describeScene(JPEG, [], null, {...opts, fetchImpl: async () => { throw new TypeError("net"); }}),
    error => error.code === "network_error" && error.status === null && error.requestId === null);
  await assert.rejects(describeScene(JPEG, [], null, {...opts, fetchImpl: okFetch(jsonResponse(200, {text: " "})).fetchImpl}),
    error => error.code === "invalid_response" && error.status === 200);
  await assert.rejects(describeScene(JPEG, [], null, {...opts, fetchImpl: okFetch(jsonResponse(500, null)).fetchImpl}),
    error => error.code === "http_error" && error.status === 500 && error.message === "场景描述失败。");
  await assert.rejects(describeScene(JPEG, [], null, {...opts, apiBase: "https://evil.example/", fetchImpl: okFetch().fetchImpl}),
    error => error.code === "invalid_api_base");
});

test("describeScene maps timeout and external cancellation to timeout/aborted", async function testDescribeSceneTimeout() {
  const hang = (url, init) => new Promise((_, reject) => init.signal.addEventListener("abort",
    () => reject(Object.assign(new Error("a"), {name: "AbortError"}))));
  const opts = {language: "zh-CN", apiBase: "./", pageHref: PAGE, fetchImpl: hang};
  await assert.rejects(describeScene(JPEG, [], null, {...opts, timeoutMs: 20}), error => error.code === "timeout");
  const controller = new AbortController();
  const pending = describeScene(JPEG, [], null, {...opts, signal: controller.signal});
  setImmediate(() => controller.abort());
  await assert.rejects(pending, error => error.code === "aborted");
});

test("Invalid JPEG throws invalid_image without making a request", async function testDescribeSceneInvalidImage() {
  const {calls, fetchImpl} = okFetch();
  const opts = {language: "zh-CN", apiBase: "./", pageHref: PAGE, fetchImpl};
  const tooBig = new Uint8Array(1024 * 1024 + 1);
  tooBig[0] = 0xff; tooBig[1] = 0xd8;
  for (const bad of [null, undefined, new ArrayBuffer(0), Uint8Array.from([0x89, 0x50, 1]), tooBig, "str", new Blob([])])
    await assert.rejects(describeScene(bad, [], null, opts), error => error instanceof SceneApiError && error.code === "invalid_image");
  const edge = new Uint8Array(1024 * 1024);
  edge[0] = 0xff; edge[1] = 0xd8;
  await describeScene(edge, [], null, opts);
  assert.equal(calls.length, 1);
});

test("Blob input and question handling trims and rejects over 200 characters without truncation", async function testDescribeSceneBlobQuestion() {
  const {calls, fetchImpl} = okFetch();
  const opts = {language: "zh-CN", apiBase: "./", pageHref: PAGE, fetchImpl};
  await describeScene(new Blob([new Uint8Array(JPEG)], {type: "image/jpeg"}), [], "  问  ", opts);
  const body = JSON.parse(calls[0].init.body);
  assert.deepEqual(Buffer.from(body.image_jpeg_base64, "base64"), Buffer.from(JPEG));
  assert.equal(body.question, "问");
  await describeScene(JPEG, [], "问".repeat(200), opts);
  assert.equal(calls.length, 2);
  await assert.rejects(describeScene(JPEG, [], "问".repeat(201), opts),
    error => error.code === "invalid_question" && error.message === "问题最多200个字符。");
  assert.equal(calls.length, 2);
});

test("Default language and apiBase come from document.documentElement", async function testDescribeSceneDefaults() {
  const {calls, fetchImpl} = okFetch();
  const saved = {document: globalThis.document, location: globalThis.location};
  Object.defineProperty(globalThis, "document", {configurable: true, writable: true,
    value: {documentElement: {lang: "en", dataset: {apiBase: "../"}}}});
  Object.defineProperty(globalThis, "location", {configurable: true, writable: true, value: {href: "https://h.example/prs/maps/"}});
  try {
    await describeScene(JPEG, [], null, {fetchImpl});
    assert.equal(calls[0].url, "https://h.example/prs/scene/describe/");
    assert.equal(JSON.parse(calls[0].init.body).language, "en-US");
    globalThis.document.documentElement = {lang: "zh-CN", dataset: {}};
    await describeScene(JPEG, [], null, {fetchImpl});
    assert.equal(calls[1].url, "https://h.example/prs/maps/scene/describe/");
    assert.equal(JSON.parse(calls[1].init.body).language, "zh-CN");
  } finally {
    for (const [key, value] of Object.entries(saved)) {
      if (value === undefined) delete globalThis[key]; else globalThis[key] = value;
    }
  }
});

test("describeCurrentScene uses camera frames and boxes and returns camera_unavailable when stopped", async function testDescribeCurrentScene() {
  const {calls, fetchImpl} = okFetch();
  const base = {language: "zh-CN", apiBase: "./", pageHref: PAGE, fetchImpl};
  const result = await describeCurrentScene("前面能走吗？", {...base,
    source: {latestJpeg: async () => JPEG, latestBoxes: () => [box()]}});
  assert.deepEqual(result, OK);
  const body = JSON.parse(calls[0].init.body);
  assert.equal(body.question, "前面能走吗？");
  assert.equal(body.objects.length, 1);
  for (const latestJpeg of [async () => null, async () => { throw new Error("x"); }])
    await assert.rejects(describeCurrentScene(null, {...base, source: {latestJpeg, latestBoxes: () => []}}),
      error => error instanceof SceneApiError && error.code === "camera_unavailable");
  assert.equal(calls.length, 1);
});
