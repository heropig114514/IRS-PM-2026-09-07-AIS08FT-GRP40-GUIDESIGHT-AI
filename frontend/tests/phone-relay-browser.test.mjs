/* Responsibilities: Verify public-relay checker authentication, privacy and model-clock gates offline.
 * Implementation: Pure URL/env/evidence fixtures and temporary private artifacts; no browser or APIs.
 * Related Modules: tools/check_phone_relay_browser.mjs; original PRF2 local model contract.
 * Declaration Index: fixture; rejectsCode.
 * Variable Index: URL/RUNTIME are explicit synthetic test inputs; fixture metadata is never model evidence.
 * Constraints: No Chrome launch, GPU, service/deployment, external request or real secret file access.
 */
import test from "node:test";
import assert from "node:assert/strict";
import {mkdtemp, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {basename, dirname, join, resolve} from "node:path";
import {runInNewContext} from "node:vm";
import {validateArguments, parseAccess, readAccess, requireNew, safeFailure,
  classifyRequest, verifyModelEvidence, verifySpeechActivation, installGPS} from "../tools/check_phone_relay_browser.mjs";

const URL = "https://47.239.50.129/mvp/maps/test", RUNTIME = {PRS_PLAYWRIGHT_PATH: "synthetic-playwright"};

/* Functionality: Assert a fixed safe rejection code without source-value diagnostics.
 * Inputs: failing synchronous callback and expected code. Outputs: test assertion.
 * Logic: inspect only code/message equality; Constraints: synthetic fixtures only, no original body dump. */
function rejectsCode(callback, code) {
  assert.throws(callback, error => error.code === code && error.message === code);
}

/* Functionality: Construct two explicit synthetic native-WSS/model evidence partitions.
 * Inputs: None. Outputs: independent frame/socket records for validation tests.
 * Logic: realistic original identity plus exact capture seconds and reset epoch.
 * Constraints: these records must never be saved or interpreted as real inference evidence. */
function fixture() {
  const frames = [], sockets = [];
  for (let session = 0; session < 2; session++) {
    const id = (session ? "b" : "a").repeat(32), inputs = [];
    const hello = {session_id: id, model: "yolo26n", provider: "local", protocol_version: 2,
      session_seconds: null, source_sha256: "c".repeat(64), precision: "FP32", output_precision: "FP32",
      backend: "PyTorch resident CUDA", classes: 80, confidence_threshold: 0.25, max_edge: 640, jpeg_quality: 0.8};
    for (let index = 0; index < 3; index++) {
      const time = 10 + session * 10 + index * 0.25;
      inputs.push({index, capture_time_s: time, bytes: 5000});
      frames.push({session_id: id, index, model: "yolo26n", provider: "local", precision: "FP32",
        output_precision: "FP32", backend: "PyTorch resident CUDA", source_sha256: "c".repeat(64),
        captured_at_ms: time * 1000, capture_time_s: time, timestamp_source: "browser_capture",
        tracking_epoch: session, tracking_status: "active", tracking_model: "botsort", depth_model: "yolo26n-depth",
        depth_units: "m", risk_model: "rules-v1", capture_to_decoded_ms: 90});
    }
    sockets.push({secure: true, hellos: [hello], inputs});
  }
  return {frames, sockets};
}

test("relay credentials are pinned to the exact approved HTTPS mount before file access", () => {
  const options = validateArguments([URL, "private.env", "new-evidence.json"], RUNTIME);
  assert.equal(options.url, URL);
  for (const url of ["http://47.239.50.129/mvp/maps/test", "https://other.example/mvp/maps/test",
    "https://47.239.50.129/maps/test", "https://phone:private@47.239.50.129/mvp/maps/test",
    URL + "?access=private", URL + "#private", "https://47.239.50.129:8443/mvp/maps/test", "not a URL"])
    rejectsCode(() => validateArguments([url, "private.env", "new.json"], RUNTIME), "INVALID_PHONE_URL");
  rejectsCode(() => validateArguments([URL, "same.json", "same.json"], RUNTIME), "INVALID_REPORT_PATH");
  rejectsCode(() => validateArguments([URL, "private.env", "new.json", "extra"], RUNTIME), "INVALID_ARGUMENTS");
  rejectsCode(() => validateArguments([URL, "private.env", "new.json"], {}), "INVALID_ARGUMENTS");
});

test("safe access parser reads only fixed credentials without expansion or process mutation", () => {
  const before = {...process.env};
  const value = parseAccess(Buffer.from("# Explicit synthetic credentials\nHF_TOKEN=ignore-private\n" +
    "export PHONE_ACCESS_USER='phone'\nPHONE_ACCESS_PASSWORD=\"${NOT_EXPANDED}#literal\" # comment\n"));
  assert.deepEqual(value, {username: "phone", password: "${NOT_EXPANDED}#literal", origin: "https://47.239.50.129"});
  assert.deepEqual({...process.env}, before);
});

test("malformed, duplicated, nonphone, empty and oversized secrets fail without content disclosure", () => {
  const values = ["PHONE_ACCESS_USER=other\nPHONE_ACCESS_PASSWORD=private-value",
    "PHONE_ACCESS_USER=phone\nPHONE_ACCESS_PASSWORD=private-one\nPHONE_ACCESS_PASSWORD=private-two",
    "PHONE_ACCESS_USER=phone\nPHONE_ACCESS_PASSWORD=\"private-broken",
    "PHONE_ACCESS_USER=phone\nPHONE_ACCESS_PASSWORD=",
    "PHONE_ACCESS_USER=phone\nPHONE_ACCESS_PASSWORD=\"private\\nvalue\"",
    "PHONE_ACCESS_USER=phone\nPHONE_ACCESS_PASSWORD=private\nmalformed private",
    "PHONE_ACCESS_USER=phone\nPHONE_ACCESS_PASSWORD=" + "x".repeat(17000)];
  for (const input of values) rejectsCode(() => parseAccess(Buffer.from(input)), "INVALID_ACCESS_FILE");
  rejectsCode(() => parseAccess(Buffer.from([255])), "INVALID_ACCESS_FILE");
});

test("private file reads are bounded and report preservation is exclusive", async () => {
  const directory = await mkdtemp(join(tmpdir(), "prs-phone-checker-"));
  try {
    const privateEnv = join(directory, "private.env"), output = join(directory, "evidence.json");
    await writeFile(privateEnv, "PHONE_ACCESS_USER=phone\nPHONE_ACCESS_PASSWORD=synthetic-private");
    assert.equal((await readAccess(privateEnv)).username, "phone");
    await writeFile(privateEnv, Buffer.alloc(100000, 97));
    await assert.rejects(readAccess(privateEnv), error => error.code === "INVALID_ACCESS_FILE");
    await requireNew(output); await writeFile(output, "preserved");
    await assert.rejects(requireNew(output), error => error.code === "REPORT_ALREADY_EXISTS");
  } finally {
    assert.equal(dirname(resolve(directory)), resolve(tmpdir()));
    assert.equal(basename(directory).startsWith("prs-phone-checker-"), true);
    await rm(directory, {recursive: true, force: true});
  }
});

test("dependency evidence omits query/userinfo and rejects leaf modules escaping mvp", () => {
  const request = classifyRequest("https://47.239.50.129/mvp/assets/camera.js?private=value");
  assert.deepEqual(request, {origin: "https://47.239.50.129", path: "/mvp/assets/camera.js", scope: "relay", allowed: true});
  assert.equal(classifyRequest("https://47.239.50.129/assets/camera.js").allowed, false);
  assert.equal(classifyRequest("https://47.239.50.129/mvpx/assets/camera.js").allowed, false);
  assert.equal(classifyRequest("https://47.239.50.129/mvp/live/assets/camera.js", "script").allowed, false);
  assert.equal(classifyRequest("https://47.239.50.129/mvp/integration/assets/events.js", "script").allowed, false);
  assert.equal(classifyRequest("http://47.239.50.129/mvp/maps/test").allowed, false);
  assert.equal(classifyRequest("https://unpkg.com/leaflet@1.9.4/dist/leaflet.js").allowed, true);
  assert.equal(classifyRequest("https://unpkg.com/unapproved.js").allowed, false);
  assert.equal(classifyRequest("https://tile.openstreetmap.org/16/1/2.png").allowed, true);
  assert.equal(classifyRequest("https://other.example/16/1/2.png").allowed, false);
  assert.equal(classifyRequest("blob:https://47.239.50.129/synthetic"), null);
});

test("failure evidence never copies exception messages, stacks or private values", () => {
  const error = Object.assign(new Error("user phone password synthetic-private"),
    {code: "unexpected-private-value", stack: "private stack", name: "PlaywrightError"});
  const result = safeFailure(error, "browser_and_tls");
  assert.deepEqual(result, {phase: "browser_and_tls", code: "EXECUTION_FAILED", type: "PlaywrightError"});
  assert.equal(JSON.stringify(result).includes("private"), false);
});

test("explicit GPS fixture owns one watch, delivers fresh epoch fixes and releases its emitter", () => {
  let now = 1000, nextTimer = 0; const timers = new Map(), microtasks = [], fixes = [];
  const sandbox = {navigator: {geolocation: {watchPosition: () => {}}}, window: {}, Date: {now: () => now},
    setInterval: callback => { const id = ++nextTimer; timers.set(id, callback); return id; },
    clearInterval: id => timers.delete(id), queueMicrotask: callback => microtasks.push(callback)};
  runInNewContext(`(${installGPS.toString()})({longitude:103.7766,latitude:1.2971,accuracy:5})`, sandbox);
  const geo = sandbox.navigator.geolocation, fixture = sandbox.window.__phoneGPS;
  const id = geo.watchPosition(fix => fixes.push(fix));
  assert.equal(fixes.length, 0);
  assert.throws(() => geo.watchPosition(() => {}), /CONCURRENT_GPS_OWNER/);
  microtasks.shift()(); now = 3000; [...timers.values()][0]();
  assert.deepEqual(fixes.map(fix => fix.timestamp), [1000, 3000]);
  geo.clearWatch(id); assert.equal(timers.size, 0);
  assert.throws(() => geo.clearWatch(id), /UNKNOWN_GPS_OWNER/);
  const description = JSON.parse(JSON.stringify(fixture.describe()));
  assert.equal(description.source, "explicit_synthetic_watchPosition_NUS_Date_now");
  assert.equal(description.native_api_present, true);
  assert.equal(description.max_active_watch_count, 1);
  assert.equal(description.active_watch_count, 0);
  assert.equal(description.cleared_watch_count, 1);
  assert.equal(description.emitter_active, false);
  assert.equal(JSON.stringify(description).includes("longitude"), false);
  const second = geo.watchPosition(fix => fixes.push(fix));
  geo.clearWatch(second); microtasks.shift()(); assert.equal(fixes.length, 2);
  assert.throws(() => geo.getCurrentPosition(), /UNEXPECTED_GPS_ONESHOT/);
  assert.equal(fixture.describe().one_shot_requests, 1);
});

test("model acceptance matches two real-format capture-clock sessions without accepting synthetic risk", () => {
  const value = fixture(), result = verifyModelEvidence(value.frames, value.sockets);
  assert.deepEqual(result.map(session => session.frames), [3, 3]);
  value.frames.push({...value.frames[0], session_id: "synthetic-phone-tts-only"});
  rejectsCode(() => verifyModelEvidence(value.frames, value.sockets), "FOREIGN_MODEL_FRAME");
});

test("speech activation needs a real matching native start from the first explicit user gesture", () => {
  const evidence = {activation: {ready: true, request_id: 7}, calls: [{user_gesture_active: true}],
    native: [{phase: "submitted", request_id: 7}, {phase: "start", request_id: 7}]};
  assert.equal(verifySpeechActivation(evidence).request_id, 7);
  rejectsCode(() => verifySpeechActivation({...evidence, activation: {ready: false, request_id: 7}}), "SPEECH_ACTIVATION_NOT_READY");
  rejectsCode(() => verifySpeechActivation({...evidence, calls: [{user_gesture_active: false}]}), "SPEECH_ACTIVATION_NOT_GESTURE");
  rejectsCode(() => verifySpeechActivation({...evidence, native: [{phase: "submitted", request_id: 7}]}), "SPEECH_ACTIVATION_NO_NATIVE_START");
  rejectsCode(() => verifySpeechActivation({...evidence, native: [{phase: "start", request_id: 8}]}), "SPEECH_ACTIVATION_NO_NATIVE_START");
});

test("wire time, 55second contract, insecure WSS, metadata identity and reset failures are rejected", () => {
  let value = fixture(); value.sockets[0].hellos[0].session_seconds = 55;
  rejectsCode(() => verifyModelEvidence(value.frames, value.sockets), "MODEL_SESSION_CONTRACT");
  value = fixture(); value.sockets[0].secure = false;
  rejectsCode(() => verifyModelEvidence(value.frames, value.sockets), "MODEL_SESSION_COUNT");
  value = fixture(); value.frames[0].capture_time_s += 0.001;
  rejectsCode(() => verifyModelEvidence(value.frames, value.sockets), "MODEL_FRAME_IDENTITY");
  value = fixture(); value.frames[0].provider = "synthetic";
  rejectsCode(() => verifyModelEvidence(value.frames, value.sockets), "MODEL_FRAME_IDENTITY");
  value = fixture(); value.sockets[1].inputs[2].capture_time_s = value.sockets[1].inputs[1].capture_time_s;
  rejectsCode(() => verifyModelEvidence(value.frames, value.sockets), "MODEL_FRAME_IDENTITY");
  value = fixture(); value.frames.filter(frame => frame.session_id === "b".repeat(32)).forEach(frame => { frame.tracking_epoch = 0; });
  rejectsCode(() => verifyModelEvidence(value.frames, value.sockets), "TRACKER_NOT_RESET");
});
