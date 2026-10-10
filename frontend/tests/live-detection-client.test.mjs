/** Responsibilities: Verify binary model/hash/order boundaries, twelve pending frames and cancellation.
 * Implementation: Explicit socket fixtures, production plan admission, real WebCrypto and synthetic
 * JPEG markers; no GPU/network.
 * Related Modules: live-detection-client.js; actual image decoding/public-service tests are separate.
 * Declaration Index: FakeSocket; FakeSocket.constructor; FakeSocket.send; FakeSocket.close;
 * FakeSocket.message; packet; fixture; fixture.socketFactory; fixture.failed; fixture.ended;
 * admitted; testBinary; testInvalid; testOrder; testStop; testBudgets; testFinal; testHello;
 * testSelectedPrecision; testDepthResult; testDepthFailures; testDepthCapacity;
 * trackingMetadata; testTrackingResults; testTrackingFailures; testTrackingCapacity;
 * riskMetadata; testRiskResults; testRiskFailures; testRiskCapacity.
 * Variable Index: manifest/contract — original public model identity; plan — current deployment's
 * compute/quality contract for integration regression; JPEG — synthetic markers.
 */
import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import {readFile} from "node:fs/promises";
import test from "node:test";
import {decodeResult, LiveDetectionClient} from "../live-detection-client.js";

const manifest = JSON.parse(await readFile(new URL("../../backend/models/yolo26n.json", import.meta.url)));
const plan = JSON.parse(await readFile(new URL("../../backend/models/inference-plan.json", import.meta.url)));
const contract = {kind: "session", model: "yolo26n", precision: "FP32", classes: 80,
  backend: "PyTorch resident CUDA", output_precision: "FP32",
  validation: {images: 5000, replicates: 3, baseline_ap: 0.4, worst_ap: 0.395,
    maximum_ap_drop: 0.005, summary_sha256: "e".repeat(64), notebook_version: 9},
  source_sha256: manifest.source_sha256, names: manifest.names, confidence_threshold: 0.25,
  max_edge: 640, jpeg_quality: 0.8, max_inflight: 12, session_seconds: 55, cold_seconds: 55, steady_seconds: 10};
const JPEG = Uint8Array.from([255, 216, 1, 255, 217]);

/** Functionality: Accept additive depth fields with finite meters or explicitly unavailable null.
 * Inputs: independent packet/box fixtures. Outputs: canonical distances and unchanged legacy box.
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
 * Inputs: maximal count with legacy/canonical aliases. Outputs: complete result above64KiB.
 * Logic: use real JSON/header/SHA checks. Constraints: no network or browser rendering assertion. */
test("depth packets carry 300 objects retaining legacy fields", async function testDepthCapacity() {
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

/** Functionality: Build an independent Python-compatible response fixture.
 * Inputs: index, metadata overrides, JPEG bytes. Outputs: ArrayBuffer PRS1 packet.
 * Logic: Node crypto produces independent checksum; typed header is big-endian.
 * Constraints: synthetic image markers do not prove JPEG decoding or model accuracy. */
function packet(index, overrides = {}, jpeg = JPEG) {
  const metadata = {schema_version: 1, index, model: "yolo26n", backend: "PyTorch resident CUDA",
    precision: "FP32", output_precision: "FP32", classes: 80,
    confidence_threshold: 0.25, source_sha256: manifest.source_sha256,
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
 * Inputs: None. Outputs: client/socket/state. Logic: callbacks collect diagnostics.
 * Constraints: never fetches HF quota or requests a GPU. */
function fixture() {
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
  return {client: new LiveDetectionClient("wss://test.invalid/ws/detect/hf/", failed, ended, {socketFactory}), socket, state};
}

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

/** Functionality: Preserve risk fields, unknown missing depth, and legacy packet acceptance.
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
 * Logic: additive contract boundaries checked without changing legacy packet acceptance.
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
