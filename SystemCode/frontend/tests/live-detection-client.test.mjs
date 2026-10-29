/** Responsibilities: Verify binary model/hash/order boundaries, twelve pending frames and cancellation.
 * Implementation: Explicit socket fixtures, production plan admission, real WebCrypto and synthetic
 * JPEG markers; no GPU/network.
 * Related Modules: live-detection-client.js; actual image decoding/public-service tests are separate.
 * Declaration Index: testBufferedBytes; FakeSocket; FakeSocket.constructor; FakeSocket.send; FakeSocket.close;
 * FakeSocket.message; packet; fixture; fixture.socketFactory; fixture.failed; fixture.ended;
 * admitted; testBinary; testInvalid; testOrder; testStop; testBudgets; testFinal; testHello;
 * testSelectedPrecision; testDepthResult; testDepthFailures; testDepthCapacity;
 * trackingMetadata; testTrackingResults; testTrackingFailures; testTrackingCapacity;
 * riskMetadata; testRiskResults; testRiskFailures; testRiskCapacity;
 * testResidentAdmission; testResidentIdentity; testResidentWorkload; testResidentEnd; testResidentErrors;
 * testLocalHello; testLocalCapture; testLocalCorrelation; testDistanceModes; testDistanceModeFailures;
 * testProviderIsolation; testLocalContinuous; testSessionDurationAdmission;
 * testSessionDurationOptions; testHfDurationIsolation; testWalkModel; testWalkModelFailures;
 * testWalkProviderBoundary.
 * Variable Index: manifest/contract — original public model identity; walkManifest/walkContract —
 * opt-in 87-class walking-aid identity; plan — deployment compute/quality contract;
 * residentContract/localContract — independent native admissions; JPEG — synthetic markers;
 * duration options exercise explicit continuous-local admission with virtual timers, never actual long-running inference.
 */
import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import {readFile} from "node:fs/promises";
import test from "node:test";
import {decodeResult, LiveDetectionClient} from "../live-detection-client.js";

const manifest = JSON.parse(await readFile(new URL("../../backend/models/yolo26n.json", import.meta.url)));
const plan = JSON.parse(await readFile(new URL("../../backend/models/inference-plan.json", import.meta.url)));
const walkManifest = JSON.parse(await readFile(new URL("../../backend/models/yolo26n-walk.json", import.meta.url)));
const contract = {kind: "session", model: "yolo26n", precision: "FP32", classes: 80,
  backend: "PyTorch resident CUDA", output_precision: "FP32",
  validation: {images: 5000, replicates: 3, baseline_ap: 0.4, worst_ap: 0.395,
    maximum_ap_drop: 0.005, summary_sha256: "e".repeat(64), notebook_version: 9},
  source_sha256: manifest.source_sha256, names: manifest.names, confidence_threshold: 0.25,
  max_edge: 640, jpeg_quality: 0.8, max_inflight: 12, session_seconds: 55, cold_seconds: 55, steady_seconds: 10};
const JPEG = Uint8Array.from([255, 216, 1, 255, 217]);
const residentContract = {...contract, protocol: "resident-v1", mode: "detection",
  session_seconds: null, idle_seconds: 15, model_load_ms: 100};
for (const key of ["validation", "cold_seconds", "steady_seconds"]) delete residentContract[key];
const localContract = {...contract, provider: "local", protocol_version: 2, session_id: "1".repeat(32),
  depth_model: "yolo26n-depth", depth_units: "m", distance_mode: "depth", distance_units: "m",
  depth_interval: 1, tracking_model: "botsort", risk_model: "rules-v1",
  capabilities: {detection: true, depth: true, tracking: true, risk: true}};
delete localContract.validation;

/** Functionality: Keep all three provider contracts isolated after shared-client integration.
 * Inputs: each valid hello against each explicit provider and local lifetime, plus conflicting selectors.
 * Outputs: only matching admission succeeds. Logic: exercise provider API and legacy resident alias.
 * Constraints: synthetic admission verifies protocol separation, not model readiness or quality. */
test("HF, local and resident contracts cannot substitute for each other", async function testProviderIsolation() {
  for (const localSessionSeconds of [55, null]) {
    const contracts = {hf: contract, local: {...localContract, session_seconds: localSessionSeconds},
      resident: residentContract};
    for (const [provider, hello] of Object.entries(contracts)) {
      for (const selected of Object.keys(contracts)) {
        const f = fixture(selected, {localSessionSeconds});
        if (selected === provider) {
          await admitted(f, hello); f.client.stop();
        } else {
          const waiting = assert.rejects(f.client.connect());
          f.socket.message(JSON.stringify(hello)); await waiting;
          assert.equal(f.socket.sent.length, 0);
        }
      }
    }
  }
  assert.throws(() => new LiveDetectionClient("wss://test.invalid/", null, null,
    {provider: "local", protocol: "resident-v1"}), /协议/);
  assert.throws(() => new LiveDetectionClient("wss://test.invalid/", null, null,
    {provider: "resident", protocol: "hf"}), /协议/);
});

/** Functionality: Admit and decode complete local B/C outputs through unchanged distance/risk fields.
 * Inputs: explicit mode identities and independent existing risk fixtures. Outputs: valid distance and Track ID.
 * Logic: geometry carries its own estimator identity; C keeps depth identity on skipped frames.
 * Constraints: synthetic protocol fixtures do not verify model performance or accuracy. */
test("local geometry and temporal depth preserve distance tracking and risk interfaces", async function testDistanceModes() {
  for (const mode of ["geometry", "temporal_depth"]) {
    const hello = {...localContract, distance_mode: mode, distance_units: "m", depth_interval: mode === "geometry" ? 0 : 3};
    const metadata = {...riskMetadata(), provider: "local", session_id: localContract.session_id,
      capture_time_s: 2, timestamp_source: "browser_capture", distance_mode: mode, distance_units: "m",
      depth_interval: hello.depth_interval, depth_frame: false, distance_ms: 0.1};
    if (mode === "geometry") {
      delete hello.depth_model; delete hello.depth_units;
      hello.distance_estimator = metadata.distance_estimator = "pinhole-height-v1";
      hello.capabilities = {...hello.capabilities, depth: false, distance: true};
      delete metadata.depth_model; delete metadata.depth_units; delete metadata.depth_ms;
    } else {
      metadata.boxes = metadata.boxes.map(box => ({...box, distance_source: "cached"}));
    }
    const f = await admitted(fixture("local"), hello);
    try {
      const parsed = await decodeResult(packet(0, metadata), 0, f.client.contract, {captureTimeS: 2});
      assert.equal(parsed.metadata.distance_mode, mode);
      assert.equal(parsed.metadata.boxes[0].distance_m, metadata.boxes[0].distance_m);
      assert.equal(parsed.metadata.boxes[0].track_id, metadata.boxes[0].track_id);
      assert.equal(parsed.metadata.boxes[0].risk_level, metadata.boxes[0].risk_level);
    } finally { f.client.stop(); }
  }
});

/** Functionality: Reject implicit/misidentified geometry and mismatched temporal intervals.
 * Inputs: corrupt explicit mode metadata. Outputs: rejected before guidance receives values.
 * Logic: keep A depth identity checks and require selected B/C identity. Constraints: no silent fallback. */
test("distance strategies require explicit valid identity and selected cadence", async function testDistanceModeFailures() {
  const base = {...riskMetadata(), distance_mode: "geometry", distance_units: "m",
    distance_estimator: "pinhole-height-v1", distance_ms: 0.1};
  delete base.depth_model; delete base.depth_units; delete base.depth_ms;
  for (const changes of [{distance_mode: undefined}, {distance_mode: "unknown"},
    {distance_estimator: undefined}, {distance_units: "cm"}, {depth_interval: undefined},
    {distance_ms: -1}, {depth_model: "yolo26n-depth"}])
    await assert.rejects(decodeResult(packet(0, {...base, ...changes}), 0, contract));
  const hello = {...localContract, distance_mode: "temporal_depth", distance_units: "m", depth_interval: 3};
  const temporal = {...riskMetadata(), provider: "local", session_id: hello.session_id,
    capture_time_s: 2, timestamp_source: "browser_capture", distance_mode: "temporal_depth",
    distance_units: "m", depth_interval: 2, depth_frame: false};
  await assert.rejects(decodeResult(packet(0, temporal), 0, hello, {captureTimeS: 2}));
});

/** Functionality: Verify independent local admission and preserved HF quality gate.
 * Inputs: native capability hello and separately corrupted identities. Outputs: local success/HF rejection.
 * Logic: local never borrows COCO validation; Constraints: admission does not prove real model readiness. */
test("local native hello is independent from the preserved HF COCO gate", async function testLocalHello() {
  const good = await admitted(fixture("local"), localContract); good.client.stop();
  for (const changes of [{provider: "hf"}, {protocol_version: 1}, {precision: "FP16"},
    {validation: contract.validation}, {capabilities: {detection: true}}, {session_id: "wrong"},
    {risk_model: undefined}, {distance_mode: undefined}, {distance_units: undefined},
    {depth_interval: undefined}, {depth_interval: 0}]) {
    const f = fixture("local"), waiting = assert.rejects(f.client.connect());
    f.socket.message(JSON.stringify({...localContract, ...changes})); await waiting;
    assert.equal(f.socket.sent.length, 0); assert.equal(f.state.errors.length, 1);
  }
  const hf = fixture(), waiting = assert.rejects(hf.client.connect());
  hf.socket.message(JSON.stringify(localContract)); await waiting;
  assert.equal(hf.state.errors.length, 1);
});

/** Functionality: Admit continuous local sessions without relaxing frame response deadlines.
 * Inputs: explicit null option/hello, synthetic packets and virtual timer. Outputs: post55s frame admission and10s failure.
 * Logic: finish the first frame, advance past the bounded-session duration, then time out a second frame.
 * Constraints: verifies protocol/lifecycle only; no real elapsed time, GPU, capture or latency claim. */
test("explicit continuous local admission preserves per-frame budgets beyond55s", async function testLocalContinuous(t) {
  t.mock.timers.enable({apis: ["setTimeout"]});
  const f = await admitted(fixture("local", {localSessionSeconds: null}), {...localContract, session_seconds: null});
  const first = f.client.sendFrame(JPEG.slice().buffer, {capturedAt: 1000});
  f.socket.message(packet(0, {...riskMetadata(), provider: "local", session_id: localContract.session_id,
    capture_time_s: 1, timestamp_source: "browser_capture"}));
  await first;
  t.mock.timers.tick(55001);
  assert.equal(f.client.stopped, false); assert.equal(f.client.contract.session_seconds, null);
  const rejected = assert.rejects(f.client.sendFrame(JPEG.slice().buffer, {capturedAt: 56001}), /超时/);
  t.mock.timers.tick(9999); assert.equal(f.client.stopped, false);
  t.mock.timers.tick(1); await rejected;
  assert.equal(f.state.errors.length, 1); assert.equal(f.client.sequence, 2);
});

/** Functionality: Reject an unrequested session lifetime before sending frames.
 * Inputs: default/explicit55/undefined/null client policies and contrary local hellos. Outputs: admission or clear rejection.
 * Logic: default stays55; continuous mode requires both explicit null policy and matching hello.
 * Constraints: fixtures prove transport contracts, not server lifetime enforcement. */
test("local duration defaults stay55 and explicit policies must match the hello", async function testSessionDurationAdmission() {
  for (const options of [{}, {localSessionSeconds: 55}, {localSessionSeconds: undefined}]) {
    const good = await admitted(fixture("local", options), localContract);
    assert.equal(good.client.localSessionSeconds, 55); good.client.stop();
    const bad = fixture("local", options), rejected = assert.rejects(bad.client.connect(), /会话配置/);
    bad.socket.message(JSON.stringify({...localContract, session_seconds: null})); await rejected;
    assert.equal(bad.socket.sent.length, 0); assert.equal(bad.state.errors.length, 1);
  }
  const bounded = fixture("local", {localSessionSeconds: null}), rejected = assert.rejects(bounded.client.connect(), /会话配置/);
  bounded.socket.message(JSON.stringify(localContract)); await rejected;
  assert.equal(bounded.socket.sent.length, 0); assert.equal(bounded.state.errors.length, 1);
});

/** Functionality: Reject malformed opt-in settings synchronously.
 * Inputs: unsupported numbers, strings and other values. Outputs: constructor errors before any socket.
 * Logic: only literal55 or null admitted; undefined is covered by the default-policy test.
 * Constraints: does not coerce values or change model settings. */
test("local session policy rejects unsupported or coerced values", function testSessionDurationOptions() {
  for (const value of [0, -1, 54, 56, Infinity, NaN, "55", "null", false, {}, []])
    assert.throws(() => fixture("local", {localSessionSeconds: value}), /会话时长/);
});

/** Functionality: Keep HF duration admission fixed despite the local-only option.
 * Inputs: HF clients configured with explicit null and otherwise valid55/null hellos. Outputs:55 success, null failure.
 * Logic: local policy cannot override HF model/quality/duration requirements.
 * Constraints: no HF requests or quality measurements; existing independent quality fixtures remain intact. */
test("continuous local option cannot relax HF session duration", async function testHfDurationIsolation() {
  const good = await admitted(fixture("hf", {localSessionSeconds: null}), contract);
  assert.equal(good.client.contract.session_seconds, 55); good.client.stop();
  const bad = fixture("hf", {localSessionSeconds: null}), rejected = assert.rejects(bad.client.connect(), /会话配置/);
  bad.socket.message(JSON.stringify({...contract, session_seconds: null})); await rejected;
  assert.equal(bad.socket.sent.length, 0); assert.equal(bad.state.errors.length, 1);
});

/** Functionality: Verify PRF2 source timestamp and strictly monotonic capture admission.
 * Inputs: real client, explicit source milliseconds, independent response packet.
 * Outputs: exact big-endian bytes and original matching result. Logic: capturedAt originates before encoding.
 * Constraints: synthetic JPEG/GPU fixture; no frame loss, timestamp invention or automatic retry. */
test("local PRF2 carries big-endian source capture time and preserves JPEG", async function testLocalCapture() {
  const f = await admitted(fixture("local"), localContract);
  assert.throws(() => f.client.sendFrame(JPEG.slice().buffer), /采集时间/);
  const first = f.client.sendFrame(JPEG.slice().buffer, {capturedAt: 1234.5});
  const bytes = f.socket.sent[0], view = new DataView(bytes);
  assert.equal(view.getUint32(0), 0x50524632); assert.equal(view.getUint32(4), 0);
  assert.equal(view.getFloat64(8), 1.2345); assert.deepEqual(new Uint8Array(bytes, 16), JPEG);
  for (const capturedAt of [1234.5, 1000, -1, Infinity])
    assert.throws(() => f.client.sendFrame(JPEG.slice().buffer, {capturedAt}), /采集时间/);
  f.socket.message(packet(0, {...riskMetadata(), provider: "local", session_id: localContract.session_id,
    capture_time_s: 1.2345, timestamp_source: "browser_capture"}));
  assert.equal((await first).metadata.capture_time_s, 1.2345); f.client.stop();
});

/** Functionality: Reject foreign/stale timestamp association at the protocol boundary.
 * Inputs: independently corrupted local session/source/algorithm identity. Outputs: errors before UI/guidance.
 * Logic: match original pending capture timestamp exactly; Constraints: no silent omission of required capabilities. */
test("local responses bind the same session and exact source timestamp", async function testLocalCorrelation() {
  const base = {...riskMetadata(), provider: "local", session_id: localContract.session_id,
    capture_time_s: 2, timestamp_source: "browser_capture"};
  const valid = await decodeResult(packet(0, base), 0, localContract, {captureTimeS: 2});
  assert.equal(valid.metadata.risk_model, "rules-v1");
  for (const changes of [{session_id: "2".repeat(32)}, {capture_time_s: 2.1},
    {timestamp_source: "server_received"}, {provider: "hf"}, {depth_model: undefined}, {risk_model: undefined}])
    await assert.rejects(decodeResult(packet(0, {...base, ...changes}), 0, localContract, {captureTimeS: 2}));
});

/** Functionality: Accept additive depth fields with finite meters or explicitly unavailable null.
 * Inputs: independent packet/box fixtures. Outputs: canonical distances and unchanged detection fields.
 * Logic: actual packet/hash/client validation. Constraints: synthetic data, no depth accuracy claim. */
test("depth metadata accepts meters and null while preserving the existing box fields", async function testDepthResult() {
  for (const distance of [2.4, null]) {
    const box = {class_id: 0, label: manifest.names[0], score: 0.93, xyxy: [0, 0, 5, 9],
      class_name: manifest.names[0], confidence: 0.93, bbox: [0, 0, 5, 9], distance_m: distance};
    const result = await decodeResult(packet(0, {depth_model: "yolo26n-depth", depth_units: "m",
      depth_ms: 2, count: 1, boxes: [box]}), 0, contract);
    assert.equal(result.metadata.boxes[0].distance_m, distance);
    assert.deepEqual(result.metadata.boxes[0].bbox, result.metadata.boxes[0].xyxy);
  }
});

/** Functionality: Reject invalid metric distances, aliases and incomplete model identity.
 * Inputs: corrupted depth metadata/boxes. Outputs: errors before display.
 * Logic: independently change each contract boundary. Constraints: no silent depth omission. */
test("depth metadata rejects invalid distances and mismatched box aliases", async function testDepthFailures() {
  const box = {class_id: 0, label: manifest.names[0], score: 0.93, xyxy: [0, 0, 5, 9],
    class_name: manifest.names[0], confidence: 0.93, bbox: [0, 0, 5, 9], distance_m: 2.4};
  const metadata = {depth_model: "yolo26n-depth", depth_units: "m", depth_ms: 2, count: 1, boxes: [box]};
  for (const changes of [{distance_m: 0}, {distance_m: -1}, {distance_m: "2.4"},
    {distance_m: undefined}, {class_name: "wrong"}, {confidence: 0.7}, {bbox: [1, 0, 5, 9]}])
    await assert.rejects(decodeResult(packet(0, {...metadata, boxes: [{...box, ...changes}]}), 0, contract));
  for (const changes of [{depth_units: "cm"}, {depth_model: ""}, {depth_ms: -1}, {depth_ms: undefined}])
    await assert.rejects(decodeResult(packet(0, {...metadata, ...changes}), 0, contract));
  await assert.rejects(decodeResult(packet(0, {count: 1, boxes: [box]}), 0, contract));
});

/** Functionality: Support300 full-precision fused detections within the coordinated256KiB limit.
 * Inputs: maximal count with the complete detection field set. Outputs: complete result above64KiB.
 * Logic: use real JSON/header/SHA checks. Constraints: no network or browser rendering assertion. */
test("depth packets carry 300 objects with the complete detection fields", async function testDepthCapacity() {
  const xyxy = [0.12345678901234, 0.23456789012345, 8.12345678901234, 9.23456789012345];
  const box = {class_id: 9, label: manifest.names[9], score: 0.9312345678901234, xyxy,
    class_name: manifest.names[9], confidence: 0.9312345678901234, bbox: xyxy, distance_m: 2.4};
  const data = packet(0, {depth_model: "yolo26n-depth", depth_units: "m", depth_ms: 2,
    count: 300, boxes: Array(300).fill(box)});
  assert.ok(data.byteLength > 64 * 1024);
  assert.equal((await decodeResult(data, 0, contract)).detections, 300);
});

/** Functionality: Capture socket operations and deliver explicit events.
 * Logic: caller drives messages, no automatic response. Constraints: not a browser/network stack. */
class FakeSocket {
  /** Functionality: Initialize OPEN fixture. Inputs: None. Outputs: instance.
   * Logic: sent/closed arrays record side effects. Constraints: no real handshake. */
  constructor() { this.readyState = 1; this.bufferedAmount = 0; this.sent = []; this.closed = []; }
  /** Functionality: Record packet/control. Inputs: data. Outputs: sent append.
   * Logic: leave bytes unchanged. Constraints: no transmission. */
  send(data) { this.sent.push(data); }
  /** Functionality: Record closure. Inputs: code/reason. Outputs: CLOSED state.
   * Logic: no event automatically dispatched. Constraints: no TLS shutdown verification. */
  close(code, reason) { this.closed.push({code, reason}); this.readyState = 3; }
  /** Functionality: Deliver one explicit message. Inputs: data. Outputs: client handler call.
   * Logic: use real client callback. Constraints: not browser parsing. */
  message(data) { this.onmessage({data}); }
}

/** Functionality: Expose only validated native send-buffer readings on an admitted open session.
 * Inputs: controlled socket state/count. Outputs: exact bytes or visible error.
 * Logic: observe no send or sequence change; test closed, stopped and malformed readings.
 * Constraints: synthetic socket, not a network throughput measurement. */
test("buffered bytes require an admitted open socket and never send", async function testBufferedBytes() {
  const f = fixture();
  assert.throws(() => f.client.bufferedBytes(), /尚未就绪/);
  await admitted(f);
  const sent = f.socket.sent.length;
  for (const value of [0, 65536]) {
    f.socket.bufferedAmount = value;
    assert.equal(f.client.bufferedBytes(), value);
  }
  for (const value of [-1, NaN, Infinity, 1.5, undefined]) {
    f.socket.bufferedAmount = value;
    assert.throws(() => f.client.bufferedBytes(), /队列状态/);
    assert.throws(() => f.client.sendFrame(JPEG.slice().buffer), /队列状态/);
  }
  assert.equal(f.socket.sent.length, sent); assert.equal(f.client.sequence, 0);
  f.socket.readyState = 3;
  assert.throws(() => f.client.bufferedBytes(), /尚未就绪/);
  f.client.stop();
  assert.throws(() => f.client.bufferedBytes(), /尚未就绪/);
});

/** Functionality: Build an independent Python-compatible response fixture.
 * Inputs: index, metadata overrides, JPEG bytes. Outputs: ArrayBuffer PRS1 packet.
 * Logic: Node crypto produces independent checksum; typed header is big-endian.
 * Constraints: synthetic image markers do not prove JPEG decoding or model accuracy. */
function packet(index, overrides = {}, jpeg = JPEG) {
  const metadata = {schema_version: 1, index, model: "yolo26n", backend: "PyTorch resident CUDA",
    precision: "FP32", output_precision: "FP32", classes: 80,
    confidence_threshold: 0.25, source_sha256: manifest.source_sha256,
    distance_mode: "depth", distance_units: "m", depth_interval: 1,
    width: 10, height: 10, count: 0, boxes: [], raw_sha256: "a".repeat(64),
    jpeg_sha256: createHash("sha256").update(jpeg).digest("hex"),
    inference_ms: 1, graph_setup_ms: 0, prepare_ms: 1, gpu_stage_ms: 1, render_ms: 1, server_ms: 3,
    ...overrides};
  const raw = new TextEncoder().encode(JSON.stringify(metadata));
  const bytes = new Uint8Array(8 + raw.length + jpeg.length), header = new DataView(bytes.buffer);
  header.setUint32(0, 0x50525331);
  header.setUint32(4, raw.length);
  bytes.set(raw, 8); bytes.set(jpeg, 8 + raw.length);
  return bytes.buffer;
}

/** Functionality: Bind actual client to explicit socket/lifecycle fixtures.
 * Inputs: provider or resident-v1 alias, HF by default, optional local duration. Outputs: client/socket/state.
 * Logic: collect diagnostics and exercise both selection APIs.
 * Constraints: never fetches HF quota or requests a GPU. */
function fixture(protocol = "hf", options = {}) {
  const socket = new FakeSocket(), state = {errors: [], ends: 0};
  /** Functionality: Return injected socket. Inputs: ignored url. Outputs: fixture.
   * Logic: no new socket. Constraints: explicitly synthetic. */
  function socketFactory() { return socket; }
  /** Functionality: Record failure. Inputs: error. Outputs: errors append.
   * Logic: no retry. Constraints: test diagnostic only. */
  function failed(error) { state.errors.push(error); }
  /** Functionality: Record bounded end. Inputs: None. Outputs: end count.
   * Logic: no auto-start. Constraints: no actual resource-release proof. */
  function ended() { state.ends += 1; }
  return {client: new LiveDetectionClient("wss://test.invalid/ws/detect/fixture/", failed, ended,
    {...options, socketFactory, ...(protocol === "resident-v1" ? {protocol} : {provider: protocol})}), socket, state};
}

/** Functionality: Admit all explicit native workloads and verify an actual envelope response.
 * Inputs: resident hello and appropriate synthetic metadata. Outputs: no quality-plan dependency.
 * Logic: actual client/hash validation; full workload retains risk/track/depth checks.
 * Constraints: no GPU/accuracy claim; all frame parameters remain unchanged. */
test("resident admission is explicit and supports its three workloads", async function testResidentAdmission() {
  for (const mode of ["detection", "depth", "full"]) {
    const selected = {...residentContract, mode};
    const {client, socket, state} = await admitted(fixture("resident-v1"), selected);
    try {
      const result = client.sendFrame(JPEG.slice().buffer);
      const extra = mode === "full" ? riskMetadata() : mode === "depth" ?
        {depth_model: "yolo26n-depth", depth_units: "m", depth_ms: 2} : {};
      socket.message(packet(0, {...extra, resident_queue_ms: 0.1}));
      assert.equal((await result).metadata.precision, "FP32");
      assert.equal(client.contract.session_seconds, null);
      assert.deepEqual(state.errors, []);
    } finally { client.stop(); }
  }
});

/** Functionality: Reject cross-provider hello and unapproved native configurations.
 * Inputs: HF/resident substitutions and mutated native contract. Outputs: pre-frame failures.
 * Logic: protocol is selected by caller, never inferred from provider data.
 * Constraints: no weakening of existing HF COCO/precision checks. */
test("resident cannot bypass HF admission or accept a changed native plan", async function testResidentIdentity() {
  const cases = [["hf", residentContract], ["resident-v1", contract]];
  for (const change of [{precision: "FP16"}, {mode: "unknown"}, {session_seconds: 55},
    {idle_seconds: 0}, {model_load_ms: -1}, {validation: contract.validation},
    {cold_seconds: 55}, {max_inflight: 8}, {token: "synthetic"}, {protocol: undefined}])
    cases.push(["resident-v1", {...residentContract, ...change}]);
  for (const [protocol, selected] of cases) {
    const {client, socket, state} = fixture(protocol);
    const rejected = assert.rejects(client.connect());
    socket.message(JSON.stringify(selected));
    await rejected;
    assert.equal(state.errors.length, 1);
    assert.equal(socket.sent.length, 0);
  }
});

/** Functionality: Reject omitted or added stages and invalid resident queue timings.
 * Inputs: synthetic full metadata and staged removals. Outputs: validation failures.
 * Logic: declared workload must match every frame, including no-detection frames.
 * Constraints: does not infer successful depth/tracking from labels alone. */
test("resident per-frame stages must match the admitted workload", async function testResidentWorkload() {
  const selected = {...residentContract, mode: "full"};
  const full = {...riskMetadata(), resident_queue_ms: 0};
  for (const field of ["depth_model", "tracking_model", "risk_model", "resident_queue_ms"]) {
    const changed = {...full}; delete changed[field];
    await assert.rejects(decodeResult(packet(0, changed), 0, selected));
  }
  await assert.rejects(decodeResult(packet(0, {...full, resident_queue_ms: -1}), 0, selected));
  await assert.rejects(decodeResult(packet(0, full), 0, residentContract));
});

/** Functionality: Verify resident final counts after asynchronous JPEG hashing finishes.
 * Inputs: final result followed by correct/incorrect drain summaries. Outputs: end or failure.
 * Logic: match sent/verified counts on the message chain. Constraints: partial success is not ended. */
test("resident end preserves final JPEG and checks complete delivery", async function testResidentEnd() {
  for (const count of [1, 0, 2]) {
    const {client, socket, state} = await admitted(fixture("resident-v1"), residentContract);
    const result = client.sendFrame(JPEG.slice().buffer);
    socket.message(packet(0, {resident_queue_ms: 0}));
    socket.message(JSON.stringify({kind: "ended", received: count, delivered: count}));
    socket.onclose({code: 1000});
    await result; await client.messageChain;
    assert.equal(state.ends, count === 1 ? 1 : 0);
    assert.equal(state.errors.length, count === 1 ? 0 : 1);
  }
});

/** Functionality: Show actionable resident busy/unavailable diagnostics without auto-reconnect.
 * Inputs: safe server error codes. Outputs: rejected hello and one user-facing error.
 * Logic: use fixed client text, retain one socket. Constraints: no provider substitution or retries. */
test("resident errors identify busy and unavailable service", async function testResidentErrors() {
  for (const code of ["resident_busy", "resident_unavailable", "resident_stream_failed"]) {
    const {client, socket, state} = fixture("resident-v1");
    const rejected = assert.rejects(client.connect(), /常驻 GPU/);
    socket.message(JSON.stringify({kind: "error", code}));
    await rejected;
    assert.equal(state.errors.length, 1);
    assert.equal(socket.sent.length, 0);
  }
});

/** Functionality: Resolve explicit public hello. Inputs: fixture and explicit contract.
 * Outputs: admitted fixture.
 * Logic: use actual connect/validation. Constraints: no real handshake or GPU availability proof. */
async function admitted(value = fixture(), selected = contract) {
  const connected = value.client.connect();
  value.socket.message(JSON.stringify(selected));
  await connected;
  return value;
}

/** Functionality: Bound twelve requests and verify untouched input/full ordered output.
 * Inputs: actual client and twelve marker responses. Outputs: header/hash/count assertions.
 * Logic: thirteenth submission rejected, all asynchronous digests processed in order.
 * Constraints: protocol test only, no real image/accuracy/latency claim. */
test("binary packets preserve JPEG and bound twelve pending requests", async function testBinary() {
  const {client, socket, state} = await admitted();
  try {
    const promises = [];
    for (let index = 0; index < 12; index++) {
      promises.push(client.sendFrame(JPEG.slice().buffer));
      const sent = socket.sent[index], view = new DataView(sent);
      assert.equal(view.getUint32(0), 0x50524631);
      assert.equal(view.getUint32(4), index);
      assert.deepEqual(new Uint8Array(sent, 8), JPEG);
    }
    assert.throws(() => client.sendFrame(JPEG.slice().buffer), /在途/);
    for (let index = 0; index < 12; index++) socket.message(packet(index));
    const results = await Promise.all(promises);
    assert.equal(client.expected, 12); assert.equal(client.pending.size, 0);
    for (const result of results) assert.deepEqual(new Uint8Array(result.bytes), JPEG);
    assert.deepEqual(state.errors, []);
  } finally { client.stop(); }
  assert.equal(socket.sent.at(-1), '{"kind":"stop"}');
});

/** Functionality: Reject corrupted hash/model/settings and invalid boxes rather than render.
 * Inputs: explicit metadata corruptions. Outputs: rejection and one failure notification each.
 * Logic: real parser/digest. Constraints: synthetic image/metadata, not actual model validation. */
test("corruption and changed model conditions fail without fallback", async function testInvalid() {
  for (const change of [{precision: "FP16"}, {source_sha256: "b".repeat(64)}, {confidence_threshold: 0.1},
    {width: 641}, {count: 1}, {server_ms: null}, {jpeg_sha256: "c".repeat(64)},
    {count: 1, boxes: [{class_id: 0, label: manifest.names[0], score: 0.9, xyxy: [0, 0, 11, 5]}]}]) {
    const {client, socket, state} = await admitted();
    const failure = assert.rejects(client.sendFrame(JPEG.slice().buffer));
    socket.message(packet(0, change));
    await failure;
    assert.equal(state.errors.length, 1); assert.equal(client.pending.size, 0);
    assert.equal(client.sequence, 1); assert.equal(client.stopped, true);
  }
});

/** Functionality: Reject reordering and cancel every outstanding promise.
 * Inputs: response1 before response0. Outputs: two rejections, one callback, no resend.
 * Logic: client expected sequence remains0. Constraints: does not simulate TCP behavior. */
test("out-of-order result stops the whole session", async function testOrder() {
  const {client, socket, state} = await admitted();
  const first = assert.rejects(client.sendFrame(JPEG.slice().buffer));
  const second = assert.rejects(client.sendFrame(JPEG.slice().buffer));
  socket.message(packet(1));
  await Promise.all([first, second]);
  assert.equal(state.errors.length, 1); assert.equal(client.expected, 0);
  assert.equal(socket.sent.length, 2);
});

/** Functionality: Preserve explicit stop/end semantics and suppress late data.
 * Inputs: stopped client and separate ended-session message. Outputs: cancellation/callback counts.
 * Logic: no late result becomes success. Constraints: cannot prove external GPU release. */
test("stop and provider end never auto-restart", async function testStop() {
  const one = await admitted(), rejected = assert.rejects(one.client.sendFrame(JPEG.slice().buffer));
  one.client.stop(); one.socket.message(packet(0)); await rejected;
  assert.equal(one.state.errors.length, 0); assert.equal(one.client.expected, 0);
  const two = await admitted(), ended = assert.rejects(two.client.sendFrame(JPEG.slice().buffer));
  two.socket.message(JSON.stringify({kind: "ended", received: 1, delivered: 0})); await ended;
  assert.equal(two.state.ends, 1); assert.equal(two.client.sequence, 1);
});

/** Functionality: Verify cold55s and later10s budgets with virtual timers.
 * Inputs: timer fixture and real parser. Outputs: exact deadline rejection, no replacement frame.
 * Logic: resolve cold result, then advance later timer; Constraints: no real network performance. */
test("first response uses55s and steady response10s", async function testBudgets(t) {
  t.mock.timers.enable({apis: ["setTimeout"]});
  const {client, socket, state} = await admitted();
  const first = client.sendFrame(JPEG.slice().buffer);
  t.mock.timers.tick(10000); assert.equal(client.stopped, false);
  socket.message(packet(0)); await first;
  const rejected = assert.rejects(client.sendFrame(JPEG.slice().buffer), /超时/);
  t.mock.timers.tick(9999); assert.equal(client.stopped, false);
  t.mock.timers.tick(1); await rejected;
  assert.equal(state.errors.length, 1); assert.equal(client.sequence, 2);
});

/** Functionality: Preserve final wire order across asynchronous hashing and close.
 * Inputs: binary then ended then close before digest completes. Outputs: valid final result/end.
 * Logic: messages and closure share one serialized chain. Constraints: synthetic JPEG only. */
test("final binary is verified before end and close", async function testFinal() {
  const {client, socket, state} = await admitted();
  const result = client.sendFrame(JPEG.slice().buffer);
  socket.message(packet(0));
  socket.message(JSON.stringify({kind: "ended", received: 1, delivered: 1}));
  socket.onclose({code: 1000});
  assert.deepEqual(new Uint8Array((await result).bytes), JPEG);
  await client.messageChain;
  assert.equal(client.expected, 1); assert.equal(state.ends, 1); assert.deepEqual(state.errors, []);
});

/** Functionality: Reject changed settings or leaked upstream session keys at admission.
 * Inputs: explicit invalid public hellos. Outputs: connect rejection and no frame send.
 * Logic: client independently checks relay contract. Constraints: no real server secret is used. */
test("invalid hello or private capability rejects before input", async function testHello() {
  for (const change of [{max_inflight: 6}, {max_inflight: 8}, {precision: "INT8"}, {backend: "unknown"},
    {validation: {...contract.validation, worst_ap: 0.3949}},
    {validation: {...contract.validation, replicates: 2}},
    {capability: "synthetic"}, {token: "synthetic"}]) {
    const {client, socket, state} = fixture();
    const rejected = assert.rejects(client.connect());
    socket.message(JSON.stringify({...contract, ...change})); await rejected;
    assert.equal(state.errors.length, 1); assert.equal(socket.sent.length, 0);
  }
});

/** Functionality: Admit AOT and the current production plan while rejecting a changed frame backend.
 * Inputs: existing AOT boundary fixture, saved production compute/COCO plan, independent marker packets.
 * Outputs: accepted matching compute/FP32 output metadata and changed-backend rejection.
 * Logic: project the plan into the public relay contract; session identity governs every frame.
 * Constraints: no GPU/network or new accuracy claim; saved provenance only verifies admission wiring. */
test("selected precision and backend must match every returned frame", async function testSelectedPrecision() {
  const current = {...contract, precision: plan.precision, backend: plan.backend,
    source_sha256: plan.source_sha256, validation: {images: plan.quality.images,
      replicates: plan.quality.replicates.length, baseline_ap: plan.quality.baseline_ap,
      worst_ap: Math.min(...plan.quality.replicates.map(row => row.ap)),
      maximum_ap_drop: plan.quality.maximum_ap_drop, summary_sha256: plan.provenance.summary_sha256,
      notebook_version: plan.provenance.version}};
  for (const selected of [{...contract, precision: "FP16", backend: "PyTorch AOT CUDA"}, current]) {
    const {client, socket} = await admitted(fixture(), selected);
    try {
      const result = client.sendFrame(JPEG.slice().buffer);
      socket.message(packet(0, {precision: selected.precision, backend: selected.backend}));
      const accepted = (await result).metadata;
      assert.equal(accepted.precision, selected.precision);
      assert.equal(accepted.backend, selected.backend);
      assert.equal(accepted.output_precision, "FP32");
      const rejected = assert.rejects(client.sendFrame(JPEG.slice().buffer));
      socket.message(packet(1, {precision: selected.precision, backend: "PyTorch resident CUDA"}));
      await rejected;
    } finally { client.stop(); }
  }
});

/** Functionality: Build independent complete tracked/depth protocol fixtures.
 * Inputs: None. Outputs: finite metadata. Logic: original box plus additive fields and CMC marker.
 * Constraints: no tracker simulation or speed/accuracy claim. */
function trackingMetadata() {
  return {depth_model: "yolo26n-depth", depth_units: "m", depth_ms: 2,
    tracking_model: "botsort", tracking_status: "active", tracking_epoch: 0,
    gmc_method: "sparseOptFlow", with_reid: false, tracking_ms: 3, tracking_timestamp_s: 2,
    count: 1, boxes: [{class_id: 0, label: manifest.names[0], score: 0.93, xyxy: [0, 0, 5, 9],
      class_name: manifest.names[0], confidence: 0.93, bbox: [0, 0, 5, 9], distance_m: 3.2,
      track_id: 7, smoothed_distance_m: 3.8, relative_speed_mps: -0.5, motion_state: "approaching"}]};
}

/** Functionality: Build complete additive risk metadata using the existing tracking fixture.
 * Inputs: None. Outputs: finite TTC and explainable alert fields.
 * Logic: consistent smoothed distance/speed. Constraints: backend owns score weights and thresholds. */
function riskMetadata() {
  const data = {...trackingMetadata(), risk_model: "rules-v1", risk_ms: 0.1};
  Object.assign(data.boxes[0], {direction: "front", ttc_sec: 7.6, risk_score: 50,
    risk_level: "high", alert_priority: 3, alert_required: true,
    risk_reasons: ["distance_m_le_5", "approaching", "ttc_s_le_8", "direction_front", "class_person"]});
  return data;
}

/** Functionality: Preserve risk fields, unknown missing depth, and the complete current packet contract.
 * Inputs: all five levels, finite long-horizon TTC, cached motion with no current depth.
 * Outputs: complete unmodified metadata. Logic: real decoder validation.
 * Constraints: no scoring or alert delivery in browser. */
test("risk fields preserve deterministic scores and unavailable TTC", async function testRiskResults() {
  for (const changes of [{}, {risk_score: 15, risk_level: "low", alert_priority: 0, alert_required: false},
    {risk_score: 40, risk_level: "medium", alert_priority: 0, alert_required: false},
    {risk_score: 48, risk_level: "medium", alert_priority: 2, alert_required: true},
    {risk_score: 97, risk_level: "critical", alert_priority: 4},
    {ttc_sec: 15}, {motion_state: "stationary", relative_speed_mps: 0, ttc_sec: null},
    {distance_m: null, ttc_sec: null, risk_score: null, risk_level: "unknown", alert_priority: 0,
      alert_required: false, risk_reasons: ["depth_unavailable"]}]) {
    const data = riskMetadata(); Object.assign(data.boxes[0], changes);
    assert.deepEqual((await decodeResult(packet(0, data), 0, contract)).metadata.boxes[0], data.boxes[0]);
  }
  const unavailable = riskMetadata(); unavailable.tracking_status = "unavailable";
  Object.assign(unavailable.boxes[0], {track_id: null, motion_state: "unknown", relative_speed_mps: null,
    smoothed_distance_m: null, ttc_sec: null, risk_score: null, risk_level: "unknown", alert_priority: 0,
    alert_required: false, risk_reasons: ["track_unavailable"]});
  await decodeResult(packet(0, unavailable), 0, contract);
  const noDepth = riskMetadata();
  delete noDepth.depth_model; delete noDepth.depth_units; delete noDepth.depth_ms;
  delete noDepth.boxes[0].distance_m;
  Object.assign(noDepth.boxes[0], {motion_state: "unknown", relative_speed_mps: null,
    smoothed_distance_m: null, ttc_sec: null, risk_score: null, risk_level: "unknown", alert_priority: 0,
    alert_required: false, risk_reasons: ["depth_unavailable"]});
  await decodeResult(packet(0, noDepth), 0, contract);
  await decodeResult(packet(0, trackingMetadata()), 0, contract);
});

/** Functionality: Reject malformed TTC, scores, alerts, directions and incomplete field groups.
 * Inputs: independently corrupted metadata. Outputs: rejection before display.
 * Logic: type/availability invariants only. Constraints: no duplicated configurable risk cutoffs. */
test("risk rejects invalid or incomplete rule output", async function testRiskFailures() {
  for (const changes of [{ttc_sec: 0}, {ttc_sec: -1}, {ttc_sec: "2"}, {ttc_sec: undefined},
    {motion_state: "stationary", relative_speed_mps: 0}, {relative_speed_mps: 0.5}, {distance_m: null},
    {risk_score: -1}, {risk_score: 101}, {risk_score: null}, {risk_level: "safe"},
    {risk_level: "unknown"}, {alert_priority: 1.5}, {alert_priority: 5}, {alert_priority: 0},
    {alert_required: "true"}, {alert_required: false}, {direction: "outside"},
    {risk_reasons: []}, {risk_reasons: ["a".repeat(65)]}, {risk_reasons: [1]},
    {risk_reasons: Array(9).fill("a")}, {risk_reasons: undefined}]) {
    const data = riskMetadata(); Object.assign(data.boxes[0], changes);
    await assert.rejects(decodeResult(packet(0, data), 0, contract));
  }
  for (const changes of [{risk_model: undefined}, {risk_model: "neural"}, {risk_ms: -1},
    {risk_ms: undefined}, {tracking_model: undefined}])
    await assert.rejects(decodeResult(packet(0, {...riskMetadata(), ...changes}), 0, contract));
  const noMarker = riskMetadata(); delete noMarker.risk_model; delete noMarker.risk_ms;
  await assert.rejects(decodeResult(packet(0, noMarker), 0, contract));
});

/** Functionality: Carry300 risk-enriched detections with full numeric precision and reasons.
 * Inputs: maximum safe IDs and long floats. Outputs: complete bounded wire packet.
 * Logic: independent JS encoder and production decoder. Constraints: no dropping or rounding. */
test("risk supports 300 complete objects", async function testRiskCapacity() {
  const data = riskMetadata(); data.width = 120; data.height = 80; data.count = 300;
  const box = {...data.boxes[0], xyxy: [12.34567890123456, 2.34567890123456, 99.34567890123456, 75.34567890123456],
    score: 0.9312345678901234, confidence: 0.9312345678901234, distance_m: 1.234567890123456,
    smoothed_distance_m: 1.234567890123456, relative_speed_mps: -2.1234567890123456,
    ttc_sec: 0.5813953511809089, risk_score: 100, risk_level: "critical", alert_priority: 4,
    risk_reasons: ["distance_m_le_1.5", "approaching", "closing_mps_ge_2", "ttc_s_le_1.5",
      "direction_front", "class_person"]};
  box.bbox = [...box.xyxy];
  data.boxes = Array.from({length: 300}, (_, index) => ({...box, track_id: 2 ** 53 - 1 - index}));
  const input = packet(0, data);
  assert.ok(new DataView(input).getUint32(4) > 128 * 1024);
  assert.deepEqual((await decodeResult(input, 0, contract)).metadata.boxes, data.boxes);
});

/** Functionality: Preserve new fields and graceful unavailable ID/depth cases before display.
 * Inputs: all four states and explicit nulls. Outputs: intact tracking metadata and old boxes.
 * Logic: real decodeResult validation, including temporarily missing depth with a held estimate.
 * Constraints: frontend does not independently classify speeds or add TTC/risk scores. */
test("tracking fields accept motion and unavailable depth or IDs", async function testTrackingResults() {
  for (const changes of [{}, {motion_state: "moving_away", relative_speed_mps: 0.5},
    {motion_state: "stationary", relative_speed_mps: 0}, {motion_state: "unknown", relative_speed_mps: null},
    {motion_state: "unknown", relative_speed_mps: null, distance_m: null},
    {distance_m: null},
    {motion_state: "unknown", relative_speed_mps: -0.5},
    {track_id: null, motion_state: "unknown", relative_speed_mps: null, smoothed_distance_m: null}]) {
    const data = trackingMetadata();
    data.boxes[0] = {...data.boxes[0], ...changes};
    const result = await decodeResult(packet(0, data), 0, contract);
    assert.deepEqual(result.metadata.boxes[0], data.boxes[0]);
  }
  const unavailable = trackingMetadata();
  unavailable.tracking_status = "unavailable";
  Object.assign(unavailable.boxes[0], {track_id: null, motion_state: "unknown",
    smoothed_distance_m: null, relative_speed_mps: null});
  await decodeResult(packet(0, unavailable), 0, contract);
  const noDepth = trackingMetadata();
  delete noDepth.depth_model; delete noDepth.depth_units; delete noDepth.depth_ms;
  delete noDepth.boxes[0].distance_m;
  Object.assign(noDepth.boxes[0], {motion_state: "unknown", smoothed_distance_m: null,
    relative_speed_mps: null});
  await decodeResult(packet(0, noDepth), 0, contract);
});

/** Functionality: Reject malformed IDs/motion fields, duplicated identities and disabled CMC.
 * Inputs: independently corrupted box and tracker fields. Outputs: rejection before JPEG display.
 * Logic: validate every current tracking field before display.
 * Constraints: does not compare state sign during hysteresis, since switching is intentionally delayed. */
test("tracking rejects malformed or incomplete motion data", async function testTrackingFailures() {
  for (const changes of [{track_id: 0}, {track_id: -1}, {track_id: 1.5}, {track_id: 2 ** 53},
    {track_id: undefined}, {motion_state: "risk"}, {motion_state: undefined},
    {relative_speed_mps: "-0.5"}, {relative_speed_mps: null}, {smoothed_distance_m: 0},
    {smoothed_distance_m: -1}, {smoothed_distance_m: null}]) {
    const data = trackingMetadata(); data.boxes[0] = {...data.boxes[0], ...changes};
    await assert.rejects(decodeResult(packet(0, data), 0, contract));
  }
  for (const changes of [{tracking_model: "bytetrack"}, {tracking_model: undefined},
    {gmc_method: "none"}, {with_reid: true}, {tracking_epoch: -1}, {tracking_epoch: 1.5},
    {tracking_ms: -1}, {tracking_timestamp_s: -1}, {tracking_status: "unavailable"}])
    await assert.rejects(decodeResult(packet(0, {...trackingMetadata(), ...changes}), 0, contract));
  const data = trackingMetadata(); data.count = 2; data.boxes.push({...data.boxes[0]});
  await assert.rejects(decodeResult(packet(0, data), 0, contract));
  const noDepthSource = trackingMetadata();
  delete noDepthSource.depth_model; delete noDepthSource.depth_units; delete noDepthSource.depth_ms;
  delete noDepthSource.boxes[0].distance_m;
  await assert.rejects(decodeResult(packet(0, noDepthSource), 0, contract));
});

/** Functionality: Carry300 full-precision tracked detections beyond the former128KiB limit.
 * Inputs: maximum distinct safe IDs and full numeric aliases. Outputs: complete ordered packet.
 * Logic: real encoder/hash/client decoder. Constraints: no precision truncation or box dropping. */
test("tracking supports 300 objects with complete motion fields", async function testTrackingCapacity() {
  const data = trackingMetadata(); data.width = 120; data.height = 80; data.count = 300;
  const box = {...data.boxes[0], xyxy: [12.34567890123456, 2.34567890123456, 99.34567890123456, 75.34567890123456],
    score: 0.9312345678901234, confidence: 0.9312345678901234, distance_m: 3.234567890123456,
    smoothed_distance_m: 3.234567890123456, relative_speed_mps: -0.1234567890123456};
  box.bbox = [...box.xyxy];
  data.boxes = Array.from({length: 300}, (_, index) => ({...box, track_id: 2 ** 53 - 1 - index}));
  const input = packet(0, data);
  assert.ok(new DataView(input).getUint32(4) > 128 * 1024);
  const result = await decodeResult(input, 0, contract);
  assert.deepEqual(result.metadata.boxes, data.boxes);
});

const walkContract = {...localContract, model: "yolo26n-walk", classes: 87, names: walkManifest.names,
  source_sha256: walkManifest.source_sha256};

/** Functionality: Admit the opt-in walk model, its neutral pedestrian-light box and lowered class thresholds.
 * Inputs: walk hello and results with a merged signal and a 0.12 traffic light. Outputs: decoded boxes.
 * Logic: thresholds come from the result's declared class_confidence_thresholds (never above 0.25).
 * Constraints: synthetic protocol fixtures; no colour accuracy is established here. */
test("walk model admits 87 classes, pedestrian-light state and declared lower thresholds", async function testWalkModel() {
  const value = await admitted(fixture("local"), walkContract);
  assert.deepEqual(value.state.errors, []);
  const signal = {class_id: 84, label: "pedestrian light", score: 0.07, xyxy: [1, 1, 3, 8],
    light_state: "red", light_scores: {red: 0.07, green: 0, off: 0}};
  const light = {class_id: 9, label: "traffic light", score: 0.13, xyxy: [4, 1, 6, 8]};
  const pole = {class_id: 80, label: "pole", score: 0.6, xyxy: [7, 0, 9, 9]};
  const thresholds = {"traffic light": 0.12, ped_red: 0.05, ped_green: 0.05, ped_off: 0.05, "pedestrian light": 0.05};
  const base = riskMetadata();
  const boxes = [signal, light, pole].map((box, index) => ({...base.boxes[0], ...box,
    track_id: index + 1, class_name: box.label, confidence: box.score, bbox: box.xyxy}));
  const result = await decodeResult(packet(0, {...base, model: "yolo26n-walk", classes: 87,
    source_sha256: walkManifest.source_sha256, provider: "local", session_id: walkContract.session_id,
    capture_time_s: 2, timestamp_source: "browser_capture",
    class_confidence_thresholds: thresholds, count: 3, boxes}), 0, walkContract, {captureTimeS: 2});
  assert.deepEqual(result.metadata.boxes.map(box => box.label), ["pedestrian light", "traffic light", "pole"]);
  // The COCO model keeps working with its own declared traffic-light threshold.
  const coco = await decodeResult(packet(0, {class_confidence_thresholds: {"traffic light": 0.12}, count: 1,
    boxes: [light]}), 0, contract);
  assert.equal(coco.metadata.boxes[0].score, 0.13);
});

/** Functionality: Reject forged model identity, signal labels outside the walk model and threshold abuse.
 * Inputs: corrupted hello/result fixtures. Outputs: errors before display.
 * Logic: one boundary per case. Constraints: no fallback to the COCO identity. */
test("walk model rejects forged identities, labels and thresholds", async function testWalkModelFailures() {
  const forged = fixture("local");
  const connected = forged.client.connect();
  forged.socket.message(JSON.stringify({...walkContract, names: manifest.names}));
  await assert.rejects(connected);
  const walkResult = {...riskMetadata(), model: "yolo26n-walk", classes: 87,
    source_sha256: walkManifest.source_sha256, provider: "local", session_id: walkContract.session_id,
    capture_time_s: 2, timestamp_source: "browser_capture"};
  const signal = {class_id: 84, label: "pedestrian light", score: 0.07, xyxy: [1, 1, 3, 8], light_state: "red"};
  const cases = [
    [contract, {class_confidence_thresholds: {"pedestrian light": 0.05}, count: 1, boxes: [signal]}],
    [walkContract, {...walkResult, count: 1, boxes: [signal]}],
    [walkContract, {...walkResult, class_confidence_thresholds: {"pedestrian light": 0.05}, count: 1,
      boxes: [{...signal, light_state: "amber"}]}],
    [walkContract, {...walkResult, class_confidence_thresholds: {"traffic light": 0.5}, count: 0, boxes: []}],
    [walkContract, {...walkResult, count: 1, boxes: [{class_id: 87, label: "x", score: 0.9, xyxy: [0, 0, 1, 1]}]}],
    [walkContract, {count: 0, boxes: []}],
    [walkContract, {...walkResult, class_confidence_thresholds: {"pedestrian light": 0.05}, count: 1,
      boxes: [{...signal, class_id: 85}]}],
    [walkContract, {...walkResult, count: 1, boxes: [{class_id: 85, label: "ped_green", score: 0.9, xyxy: [1, 1, 3, 8]}]}],
  ];
  for (const [selected, overrides] of cases)
    await assert.rejects(decodeResult(packet(0, overrides), 0, selected, {captureTimeS: 2}));
});

/** Functionality: Prevent original-model HF qualification from admitting optional walk weights.
 * Inputs: walk identity substituted into valid HF/resident contracts. Outputs: admission failure.
 * Logic: keep local 87-class support separate from other providers. Constraints: no GPU claims. */
test("walk weights require explicit local admission", async function testWalkProviderBoundary() {
  for (const provider of ["hf", "resident"]) {
    const value = fixture(provider);
    const connected = value.client.connect();
    const base = provider === "hf" ? contract : residentContract;
    value.socket.message(JSON.stringify({...base, model: "yolo26n-walk", classes: 87,
      names: walkManifest.names, source_sha256: walkManifest.source_sha256}));
    await assert.rejects(connected);
  }
});


function compactPacket(overrides = {}) {
  const full = packet(0, {...riskMetadata(), provider:"local", session_id:localContract.session_id,
    capture_time_s:2, timestamp_source:"browser_capture"});
  const size = new DataView(full).getUint32(4);
  const metadata = JSON.parse(new TextDecoder().decode(new Uint8Array(full,8,size)));
  const encoded = new TextEncoder().encode(JSON.stringify({...metadata,result_transport:"metadata-v1",
    source_jpeg_sha256:createHash("sha256").update(JPEG).digest("hex"),worker_packet_bytes:full.byteLength,...overrides}));
  const result=new Uint8Array(encoded.length+8),header=new DataView(result.buffer);
  header.setUint32(0,0x50524d31);header.setUint32(4,encoded.length);result.set(encoded,8);return result.buffer;
}

test("compact results retain risk metadata and verify the locally retained JPEG", async function testCompactResults() {
  const selected={...localContract,result_transport:"metadata-v1"};
  const result=await decodeResult(compactPacket(),0,selected,{captureTimeS:2,sourceBytes:JPEG.slice().buffer});
  assert.deepEqual(result.metadata.boxes,riskMetadata().boxes);assert.deepEqual(new Uint8Array(result.bytes),JPEG);
  assert.equal(result.metadata.capture_time_s,2);
  for(const options of [{captureTimeS:2},{captureTimeS:3,sourceBytes:JPEG.slice().buffer},
    {captureTimeS:2,sourceBytes:new Uint8Array([255,216,9,255,217]).buffer}])
    await assert.rejects(decodeResult(compactPacket(),0,selected,options));
  for(const overrides of [{source_jpeg_sha256:"f".repeat(64)},{result_transport:"annotated-jpeg"},{worker_packet_bytes:0}])
    await assert.rejects(decodeResult(compactPacket(overrides),0,selected,{captureTimeS:2,sourceBytes:JPEG.slice().buffer}));
  await assert.rejects(decodeResult(compactPacket(),0,localContract,{captureTimeS:2,sourceBytes:JPEG.slice().buffer}));
  const extra=new Uint8Array(compactPacket().byteLength+1);extra.set(new Uint8Array(compactPacket()));
  await assert.rejects(decodeResult(extra.buffer,0,selected,{captureTimeS:2,sourceBytes:JPEG.slice().buffer}));
});

test("compact transport requires explicit admission and freezes the input bytes", async function testCompactAdmission() {
  const f=fixture("local",{resultTransport:"metadata-v1"});
  await admitted(f,{...localContract,result_transport:"metadata-v1"});
  try {
    const input=JPEG.slice();const result=f.client.sendFrame(input.buffer,{capturedAt:2000});input[2]=9;
    f.socket.message(compactPacket());assert.deepEqual(new Uint8Array((await result).bytes),JPEG);
  } finally {f.client.stop();}
  const mismatch=fixture("local",{resultTransport:"metadata-v1"});
  await assert.rejects(admitted(mismatch,localContract),/传输配置/);mismatch.client.stop();
  assert.throws(()=>fixture("hf",{resultTransport:"metadata-v1"}),/传输配置/);
});
