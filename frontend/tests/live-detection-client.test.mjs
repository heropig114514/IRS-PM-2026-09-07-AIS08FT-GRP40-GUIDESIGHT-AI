/** Responsibilities: Verify binary model/hash/order boundaries, eight pending frames and cancellation.
 * Implementation: Explicit socket fixtures, production plan admission, real WebCrypto and synthetic
 * JPEG markers; no GPU/network.
 * Related Modules: live-detection-client.js; actual image decoding/public-service tests are separate.
 * Declaration Index: FakeSocket; FakeSocket.constructor; FakeSocket.send; FakeSocket.close;
 * FakeSocket.message; packet; fixture; fixture.socketFactory; fixture.failed; fixture.ended;
 * admitted; testBinary; testInvalid; testOrder; testStop; testBudgets; testFinal; testHello;
 * testSelectedPrecision.
 * Variable Index: manifest/contract — original public model identity; plan — current deployment's
 * compute/quality contract for integration regression; JPEG — synthetic markers.
 */
import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import {readFile} from "node:fs/promises";
import test from "node:test";
import {LiveDetectionClient} from "../live-detection-client.js";

const manifest = JSON.parse(await readFile(new URL("../../backend/models/yolo26n.json", import.meta.url)));
const plan = JSON.parse(await readFile(new URL("../../backend/models/inference-plan.json", import.meta.url)));
const contract = {kind: "session", model: "yolo26n", precision: "FP32", classes: 80,
  backend: "PyTorch resident CUDA", output_precision: "FP32",
  validation: {images: 5000, replicates: 3, baseline_ap: 0.4, worst_ap: 0.395,
    maximum_ap_drop: 0.005, summary_sha256: "e".repeat(64), notebook_version: 9},
  source_sha256: manifest.source_sha256, names: manifest.names, confidence_threshold: 0.25,
  max_edge: 640, jpeg_quality: 0.8, max_inflight: 8, session_seconds: 55, cold_seconds: 55, steady_seconds: 10};
const JPEG = Uint8Array.from([255, 216, 1, 255, 217]);

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

/** Functionality: Bound eight requests and verify untouched input/full ordered output.
 * Inputs: actual client and eight marker responses. Outputs: header/hash/count assertions.
 * Logic: ninth submission rejected, all asynchronous digests processed in order.
 * Constraints: protocol test only, no real image/accuracy/latency claim. */
test("binary packets preserve JPEG and bound eight pending requests", async function testBinary() {
  const {client, socket, state} = await admitted();
  try {
    const promises = [];
    for (let index = 0; index < 8; index++) {
      promises.push(client.sendFrame(JPEG.slice().buffer));
      const sent = socket.sent[index], view = new DataView(sent);
      assert.equal(view.getUint32(0), 0x50524631);
      assert.equal(view.getUint32(4), index);
      assert.deepEqual(new Uint8Array(sent, 8), JPEG);
    }
    assert.throws(() => client.sendFrame(JPEG.slice().buffer), /在途/);
    for (let index = 0; index < 8; index++) socket.message(packet(index));
    const results = await Promise.all(promises);
    assert.equal(client.expected, 8); assert.equal(client.pending.size, 0);
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
  for (const change of [{max_inflight: 6}, {precision: "INT8"}, {backend: "unknown"},
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
