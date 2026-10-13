/** Responsibilities: Same-origin bounded binary video-session transport and result validation.
 * Implementation: PRF1/PRS1 packets, twelve pending requests, serialized SHA256 validation;
 * resident/AOT/compiled Torch admission, one cold response budget, explicit stop/errors/end,
 * no reconnect or HTTP per frame.
 * Related Modules: frame-pipeline.js, camera.js; backend core.hf_video_relay/realtime.protocol/risk.
 * Declaration Index: browserSocket; sha256; validateSession; decodeResult; LiveDetectionClient;
 * LiveDetectionClient.constructor; LiveDetectionClient.connect; LiveDetectionClient.connect.register;
 * LiveDetectionClient.sendFrame; LiveDetectionClient.sendFrame.register; LiveDetectionClient.sendFrame.deadline;
 * LiveDetectionClient.handleMessage; LiveDetectionClient.handleMessage.verify;
 * LiveDetectionClient.handleMessage.failed; LiveDetectionClient.handleError;
 * LiveDetectionClient.handleMessage.ended; LiveDetectionClient.handleClose;
 * LiveDetectionClient.handleClose.closed; LiveDetectionClient.rejectPending; LiveDetectionClient.fail;
 * LiveDetectionClient.end; LiveDetectionClient.stop.
 * Variable Index: MAX_INFLIGHT — twelve; MAX_METADATA_BYTES/MAX_PACKET_BYTES — fused wire bounds;
 * COLD_MS/STEADY_MS —55s/10s failure budgets, not latency targets; HASH_PATTERN — SHA256 syntax.
 * Metadata limit is256KiB for300 fused/tracked objects retaining the existing box fields.
 * MOTION_STATES — accepted relative-motion labels; optional tracking fields remain additive.
 * RISK_LEVELS/DIRECTIONS — additive rule-result vocabularies; rule weights stay on the server.
 * Valid unexpired motion may persist while the current raw depth is null; server owns timeout.
 * Instance contract/sequence/expected/pending/queued bound identity, order and memory;
 * connectWait/messageChain/stopped/notified coordinate cancellation and error delivery.
 */
import {MAX_FRAME_BYTES} from "./echo-client.js";

export const MAX_INFLIGHT = 12;
const MAX_METADATA_BYTES = 256 * 1024;
const MAX_PACKET_BYTES = 8 + MAX_METADATA_BYTES + MAX_FRAME_BYTES;
const COLD_MS = 55000;
const STEADY_MS = 10000;
const HASH_PATTERN = /^[0-9a-f]{64}$/;
const MOTION_STATES = new Set(["unknown", "approaching", "moving_away", "stationary"]);
const RISK_LEVELS = new Set(["unknown", "low", "medium", "high", "critical"]);
const DIRECTIONS = new Set(["front", "front_left", "front_right", "left", "right", "behind", "unknown"]);

/** Functionality: Construct a native same-origin socket. Inputs: url. Outputs: WebSocket.
 * Logic: caller validates destination. Constraints: browser only; tests inject a factory. */
function browserSocket(url) { return new WebSocket(url); }

/** Functionality: Hash full returned JPEG bytes. Inputs: ArrayBuffer. Outputs: lowercase SHA256.
 * Logic: native WebCrypto digest, hexadecimal encoding. Constraints: HTTPS required, no media logs;
 * integrity does not itself prove detection accuracy or authenticate a service. */
async function sha256(buffer) {
  const raw = new Uint8Array(await crypto.subtle.digest("SHA-256", buffer));
  let result = "";
  for (const byte of raw) result += byte.toString(16).padStart(2, "0");
  return result;
}

/** Functionality: Validate the public PRS session contract. Inputs: parsed hello. Outputs: same object.
 * Logic: original model/settings/budgets; explicit resident/AOT/compiled Torch backend/precision
 * and three complete COCO quality records admitted by the server; no capability/token accepted.
 * Constraints: public provenance metadata does not independently prove COCO accuracy, GPU quota
 * or steady-state performance; per-frame compute identity must match the admitted session. */
function validateSession(message) {
  const validation = message?.validation;
  if (!message || message.kind !== "session" || message.model !== "yolo26n" ||
      !["FP32", "FP16", "BF16"].includes(message.precision) || message.output_precision !== "FP32" ||
      !["PyTorch resident CUDA", "PyTorch AOT CUDA", "PyTorch compiled CUDA"].includes(message.backend) ||
      message.classes !== 80 || message.confidence_threshold !== 0.25 ||
      message.max_edge !== 640 || message.jpeg_quality !== 0.8 || message.max_inflight !== MAX_INFLIGHT ||
      message.session_seconds !== 55 || message.cold_seconds !== 55 || message.steady_seconds !== 10 ||
      !HASH_PATTERN.test(message.source_sha256) || !Array.isArray(message.names) || message.names.length !== 80 ||
      Object.hasOwn(message, "capability") || Object.hasOwn(message, "token") ||
      !validation || validation.images !== 5000 || !Number.isInteger(validation.replicates) ||
      validation.replicates < 3 || validation.maximum_ap_drop !== 0.005 ||
      !Number.isInteger(validation.notebook_version) || validation.notebook_version < 1 ||
      !HASH_PATTERN.test(validation.summary_sha256) ||
      !Number.isFinite(validation.baseline_ap) || !Number.isFinite(validation.worst_ap) ||
      validation.baseline_ap < 0 || validation.baseline_ap > 1 ||
      validation.worst_ap < 0 || validation.worst_ap > 1 ||
      validation.baseline_ap - validation.worst_ap > 0.005 + 1e-12)
    throw new Error("视频会话配置无效，请检查服务端。");
  for (const name of message.names)
    if (typeof name !== "string" || !name.length || name.length > 100)
      throw new Error("视频会话类别配置无效。");
  return message;
}

/** Functionality: Decode and verify a complete ordered annotated result.
 * Inputs: packet ArrayBuffer, expected uint32 index, validated session contract.
 * Outputs: bytes/metadata/count/inferenceMs. Logic: bound header/JSON/JPEG; match admitted model,
 * compute precision/backend, FP32 output and hashes;
 * finite boxes and optional depth/tracking/TTC/risk fields checked before display; no filtering.
 * Temporary missing depth may retain a historical estimate; the server expires motion history.
 * Constraints: browser image decoder checks actual dimensions separately; no accuracy/mAP claim. */
export async function decodeResult(packet, expected, contract) {
  if (!(packet instanceof ArrayBuffer) || packet.byteLength <= 8 || packet.byteLength > MAX_PACKET_BYTES)
    throw new Error("视频结果包大小无效。");
  const header = new DataView(packet);
  const length = header.getUint32(4);
  if (header.getUint32(0) !== 0x50525331 || !length || length > MAX_METADATA_BYTES || 8 + length >= packet.byteLength)
    throw new Error("视频结果包协议无效。");
  let metadata;
  try { metadata = JSON.parse(new TextDecoder("utf-8", {fatal: true}).decode(new Uint8Array(packet, 8, length))); }
  catch { throw new Error("视频结果元数据无效。"); }
  if (!metadata || metadata.schema_version !== 1 || metadata.index !== expected ||
      !Number.isInteger(metadata.index) || metadata.model !== "yolo26n" ||
      metadata.backend !== contract.backend || metadata.precision !== contract.precision ||
      metadata.output_precision !== "FP32" ||
      metadata.classes !== 80 || metadata.confidence_threshold !== 0.25 ||
      metadata.source_sha256 !== contract.source_sha256 || !HASH_PATTERN.test(metadata.raw_sha256) ||
      !HASH_PATTERN.test(metadata.jpeg_sha256) || !Number.isInteger(metadata.count) ||
      metadata.count < 0 || metadata.count > 300 || !Array.isArray(metadata.boxes) || metadata.boxes.length !== metadata.count ||
      !Number.isInteger(metadata.width) || !Number.isInteger(metadata.height) ||
      metadata.width < 1 || metadata.height < 1 || Math.max(metadata.width, metadata.height) > 640)
    throw new Error("视频结果模型或统计不匹配。");
  for (const field of ["inference_ms", "graph_setup_ms", "prepare_ms", "gpu_stage_ms", "render_ms", "server_ms"])
    if (!Number.isFinite(metadata[field]) || metadata[field] < 0)
      throw new Error("视频结果耗时无效。");
  if (Object.hasOwn(metadata, "depth_model") &&
      (typeof metadata.depth_model !== "string" || !metadata.depth_model || metadata.depth_units !== "m" ||
       !Number.isFinite(metadata.depth_ms) || metadata.depth_ms < 0))
    throw new Error("视频深度模型或耗时无效。");
  const hasTracking = Object.hasOwn(metadata, "tracking_model");
  if (hasTracking && (metadata.tracking_model !== "botsort" ||
      !["active", "unavailable"].includes(metadata.tracking_status) ||
      !Number.isSafeInteger(metadata.tracking_epoch) || metadata.tracking_epoch < 0 ||
      !["sparseOptFlow", "orb", "sift", "ecc"].includes(metadata.gmc_method) || metadata.with_reid !== false ||
      !Number.isFinite(metadata.tracking_ms) || metadata.tracking_ms < 0 ||
      !Number.isFinite(metadata.tracking_timestamp_s) || metadata.tracking_timestamp_s < 0))
    throw new Error("视频跟踪配置或耗时无效。");
  const trackIds = new Set();
  const hasRisk = Object.hasOwn(metadata, "risk_model");
  if ((hasRisk && (!hasTracking || metadata.risk_model !== "rules-v1" ||
       !Number.isFinite(metadata.risk_ms) || metadata.risk_ms < 0)) ||
      (!hasRisk && Object.hasOwn(metadata, "risk_ms")))
    throw new Error("视频风险规则或耗时无效。");
  for (const box of metadata.boxes) {
    if (!box || !Number.isInteger(box.class_id) || box.class_id < 0 || box.class_id >= 80 ||
        box.label !== contract.names[box.class_id] || !Number.isFinite(box.score) || box.score < 0.25 || box.score > 1 ||
        !Array.isArray(box.xyxy) || box.xyxy.length !== 4)
      throw new Error("视频检测框无效。");
    const [x1, y1, x2, y2] = box.xyxy;
    if (![x1, y1, x2, y2].every(Number.isFinite) || x1 < 0 || y1 < 0 || x1 >= x2 || y1 >= y2 ||
        x2 > metadata.width || y2 > metadata.height)
      throw new Error("视频检测框坐标无效。");
    if (Object.hasOwn(metadata, "depth_model")) {
      if (box.class_name !== box.label || box.confidence !== box.score ||
          !Array.isArray(box.bbox) || box.bbox.length !== 4 ||
          !box.bbox.every((value, index) => value === box.xyxy[index]) ||
          !Object.hasOwn(box, "distance_m") ||
          (box.distance_m !== null && (!Number.isFinite(box.distance_m) || box.distance_m <= 0)))
        throw new Error("视频目标深度无效。");
    } else if (Object.hasOwn(box, "distance_m")) {
      throw new Error("视频目标深度缺少模型标识。");
    }
    const trackingFields = ["track_id", "motion_state", "relative_speed_mps", "smoothed_distance_m"];
    if (hasTracking) {
      if (trackingFields.some(field => !Object.hasOwn(box, field)) ||
          box.class_name !== box.label || box.confidence !== box.score ||
          !Array.isArray(box.bbox) || box.bbox.length !== 4 ||
          !box.bbox.every((value, index) => value === box.xyxy[index]) ||
          (box.track_id !== null && (!Number.isSafeInteger(box.track_id) || box.track_id <= 0 ||
                                    trackIds.has(box.track_id))) ||
          !MOTION_STATES.has(box.motion_state) ||
          (box.smoothed_distance_m !== null && (!Number.isFinite(box.smoothed_distance_m) ||
                                               box.smoothed_distance_m <= 0)) ||
          (box.relative_speed_mps !== null && !Number.isFinite(box.relative_speed_mps)) ||
          (box.relative_speed_mps !== null && box.smoothed_distance_m === null) ||
          (box.motion_state !== "unknown" && (box.track_id === null || box.relative_speed_mps === null)) ||
          (box.track_id === null && (box.smoothed_distance_m !== null || box.relative_speed_mps !== null ||
                                    box.motion_state !== "unknown")) ||
          (!Object.hasOwn(metadata, "depth_model") && (box.smoothed_distance_m !== null ||
            box.relative_speed_mps !== null || box.motion_state !== "unknown")) ||
          (metadata.tracking_status === "unavailable" && box.track_id !== null))
        throw new Error("视频目标跟踪或运动状态无效。");
      if (box.track_id !== null) trackIds.add(box.track_id);
    } else if (trackingFields.some(field => Object.hasOwn(box, field))) {
      throw new Error("视频目标跟踪缺少模型标识。");
    }
    const riskFields = ["direction", "ttc_sec", "risk_score", "risk_level", "alert_priority",
      "alert_required", "risk_reasons"];
    if (hasRisk) {
      if (riskFields.some(field => !Object.hasOwn(box, field)) ||
          !DIRECTIONS.has(box.direction) || !RISK_LEVELS.has(box.risk_level) ||
          (box.risk_score !== null && (!Number.isFinite(box.risk_score) || box.risk_score < 0 || box.risk_score > 100)) ||
          ((box.risk_level === "unknown") !== (box.risk_score === null)) ||
          !Number.isInteger(box.alert_priority) || box.alert_priority < 0 || box.alert_priority > 4 ||
          typeof box.alert_required !== "boolean" ||
          (box.alert_required !== (box.alert_priority > 0)) ||
          (["low", "unknown"].includes(box.risk_level) && box.alert_required) ||
          (["high", "critical"].includes(box.risk_level) && !box.alert_required) ||
          !Array.isArray(box.risk_reasons) || !box.risk_reasons.length || box.risk_reasons.length > 8 ||
          box.risk_reasons.some(reason => typeof reason !== "string" || !reason.length || reason.length > 64) ||
          (box.ttc_sec !== null && (!Number.isFinite(box.ttc_sec) || box.ttc_sec <= 0 ||
            box.motion_state !== "approaching" || box.relative_speed_mps === null || box.relative_speed_mps >= 0 ||
            box.distance_m === null || box.smoothed_distance_m === null || box.track_id === null)) ||
          ((box.distance_m == null || box.smoothed_distance_m === null || box.track_id === null) &&
            (box.risk_level !== "unknown" || box.ttc_sec !== null)))
        throw new Error("视频目标 TTC 或风险状态无效。");
    } else if (riskFields.some(field => Object.hasOwn(box, field))) {
      throw new Error("视频目标风险缺少规则标识。");
    }
  }
  const bytes = packet.slice(8 + length), jpeg = new Uint8Array(bytes);
  if (!jpeg.length || jpeg.length > MAX_FRAME_BYTES || jpeg[0] !== 255 || jpeg[1] !== 216 ||
      jpeg.at(-2) !== 255 || jpeg.at(-1) !== 217 || await sha256(bytes) !== metadata.jpeg_sha256)
    throw new Error("视频回传 JPEG 完整性校验失败。");
  return {bytes, metadata, detections: metadata.count, inferenceMs: metadata.inference_ms};
}

/** Functionality: Manage one twelve-request binary video session.
 * Logic: Map owns per-frame timers; messageChain preserves async hashing order; errors reject all.
 * Constraints: caller holds additional admission through display, no automatic reconnect/retry. */
export class LiveDetectionClient {
  /** Functionality: Initialize transport and lifecycle callbacks.
   * Inputs: fixed same-origin url, onDisconnect, onEnd, optional socketFactory.
   * Outputs: instance. Logic: bind events and bounded counters; no immediate network or camera.
   * Constraints: callbacks do not receive credentials; no arbitrary destination from query parameters. */
  constructor(url, onDisconnect, onEnd, {socketFactory = browserSocket} = {}) {
    this.url = url;
    this.onDisconnect = onDisconnect;
    this.onEnd = onEnd;
    this.socketFactory = socketFactory;
    this.socket = null;
    this.contract = null;
    this.connectWait = null;
    this.pending = new Map();
    this.sequence = this.expected = this.queued = 0;
    this.messageChain = Promise.resolve();
    this.stopped = this.notified = false;
    this.handleMessage = this.handleMessage.bind(this);
    this.handleError = this.handleError.bind(this);
    this.handleClose = this.handleClose.bind(this);
  }
  /** Functionality: Connect once and await validated session admission.
   * Inputs: stored url/factory. Outputs: contract Promise.
   * Logic: model hello, not merely socket open, completes the55s connection budget.
   * Constraints: no GPU dispatch by browser, capability or HF token. */
  connect() {
    if (this.socket || this.stopped) throw new Error("视频连接不能重复启动。");
    this.socket = this.socketFactory(this.url);
    this.socket.binaryType = "arraybuffer";
    this.socket.onmessage = this.handleMessage;
    this.socket.onerror = this.handleError;
    this.socket.onclose = this.handleClose;
    const client = this;
    /** Functionality: Register admission promise. Inputs: resolve/reject. Outputs: connectWait.
     * Logic: one timer until model hello. Constraints: timeout stops instead of reconnecting. */
    function register(resolve, reject) {
      client.connectWait = {resolve, reject, timer: setTimeout(client.handleError, COLD_MS)};
    }
    return new Promise(register);
  }
  /** Functionality: Send one original JPEG with an ordered uint32 header.
   * Inputs: ArrayBuffer. Outputs: validated result/RTT Promise.
   * Logic: reserve pending before send; first55s, later10s; enforce both count and socket byte bounds.
   * Constraints: no encoding, base64, stale-frame queue or retry; max12 requests, caller gates capture. */
  sendFrame(buffer) {
    if (this.stopped || !this.contract || this.socket.readyState !== 1)
      throw new Error("视频连接尚未就绪或已停止。");
    if (!(buffer instanceof ArrayBuffer) || !buffer.byteLength || buffer.byteLength > MAX_FRAME_BYTES)
      throw new Error("图像帧为空或超过 1 MiB 限制");
    if (this.pending.size >= MAX_INFLIGHT || this.sequence > 0xFFFFFFFF ||
        this.socket.bufferedAmount > MAX_INFLIGHT * (MAX_FRAME_BYTES + 8))
      throw new Error("视频发送超过在途边界。");
    const index = this.sequence++, client = this;
    const packet = new Uint8Array(8 + buffer.byteLength);
    const view = new DataView(packet.buffer);
    view.setUint32(0, 0x50524631);
    view.setUint32(4, index);
    packet.set(new Uint8Array(buffer), 8);
    /** Functionality: Stop a timed-out frame. Inputs: closure index/client. Outputs: session failure.
     * Logic: reject every outstanding request. Constraints: no replacement frame or silent discard. */
    function deadline() { client.fail(new Error(`视频帧 ${index} 返回超时，请检查网络后重新开始。`)); }
    /** Functionality: Register one frame promise. Inputs: resolve/reject. Outputs: pending Map entry.
     * Logic: monotonic send timestamp and bounded timer. Constraints: synchronous registration before send. */
    function register(resolve, reject) {
      client.pending.set(index, {resolve, reject, started: performance.now(),
        timer: setTimeout(deadline, index === 0 ? COLD_MS : STEADY_MS)});
    }
    const result = new Promise(register);
    try { this.socket.send(packet.buffer); }
    catch (error) { this.fail(error); }
    return result;
  }
  /** Functionality: Handle public control or serialized full-result verification.
   * Inputs: MessageEvent. Outputs: connect/result completion or explicit end/error.
   * Logic: asynchronous digests run in order; queued messages bounded by12; late stopped results ignored.
   * Constraints: remote excess, corruption or unexpected text fails the whole session, no fallback. */
  handleMessage(event) {
    if (this.stopped) return;
    try {
      if (typeof event.data === "string") {
        if (event.data.length > MAX_METADATA_BYTES) throw new Error("视频控制消息过大。");
        let message;
        try { message = JSON.parse(event.data); }
        catch { throw new Error("视频控制消息无效。"); }
        if (message.kind === "error")
          throw new Error(typeof message.detail === "string" && message.detail.length <= 500
            ? message.detail : "视频会话失败，请检查服务端日志后重新开始。");
        if (message.kind === "ended" && this.contract) {
          const client = this;
          /** Functionality: End only after earlier binary digests resolve. Inputs: ordered chain.
           * Outputs: end callback. Logic: preserve wire order across async verification.
           * Constraints: close/end cannot discard a valid result merely because hashing is pending. */
          function ended() { client.end(); }
          this.messageChain = this.messageChain.then(ended);
          return;
        }
        if (!this.connectWait || this.contract) throw new Error("非预期视频控制消息。");
        this.contract = validateSession(message);
        const waiting = this.connectWait;
        this.connectWait = null;
        clearTimeout(waiting.timer);
        waiting.resolve(this.contract);
        return;
      }
      if (!this.contract || !(event.data instanceof ArrayBuffer) || ++this.queued > MAX_INFLIGHT)
        throw new Error("非预期视频结果或接收队列超限。");
      const client = this, packet = event.data;
      /** Functionality: Verify the next ordered result. Inputs: closure packet/client state.
       * Outputs: resolve one pending promise. Logic: digest before count/order advancement.
       * Constraints: stop during digest discards the late result; no UI access here. */
      async function verify() {
        try {
          if (client.stopped) return;
          const pending = client.pending.get(client.expected);
          if (!pending) throw new Error("收到未请求的视频帧。");
          const result = await decodeResult(packet, client.expected, client.contract);
          if (client.stopped) return;
          client.pending.delete(client.expected++);
          clearTimeout(pending.timer);
          pending.resolve({...result, rttMs: performance.now() - pending.started});
        } finally { client.queued -= 1; }
      }
      /** Functionality: Publish validation failure. Inputs: Error. Outputs: stopped transport.
       * Logic: notify once and reject all waiters. Constraints: no media or control body logging. */
      function failed(error) { client.fail(error); }
      this.messageChain = this.messageChain.then(verify).catch(failed);
    } catch (error) { this.fail(error); }
  }
  /** Functionality: Report browser connection/hello failure. Inputs: browser event or timer.
   * Outputs: fail. Logic: safe diagnostic. Constraints: browser does not expose the exact network hop. */
  handleError() { this.fail(new Error("视频连接失败或超时，请检查网络和服务端配置。")); }
  /** Functionality: Handle unexpected closure. Inputs: CloseEvent. Outputs: fail.
   * Logic: process closure after queued binary/end messages, preserving asynchronous validation.
   * Constraints: no automatic new GPU allocation. */
  handleClose(event) {
    const client = this;
    /** Functionality: Close after earlier messages are processed. Inputs: event.code/client.
     * Outputs: failure only if no explicit end/stop. Logic: preserve valid final-frame ordering.
     * Constraints: no raw close reason is logged or used to reconnect. */
    function closed() {
      if (!client.stopped) client.fail(new Error(`视频连接已关闭（${event.code}），请重新开始。`));
    }
    this.messageChain = this.messageChain.then(closed);
  }
  /** Functionality: Reject all bounded operations. Inputs: Error, instance waiters.
   * Outputs: timer removal and rejection. Logic: detach state before callbacks.
   * Constraints: does not create a replacement request. */
  rejectPending(error) {
    if (this.connectWait) {
      const waiting = this.connectWait;
      this.connectWait = null;
      clearTimeout(waiting.timer);
      waiting.reject(error);
    }
    for (const pending of this.pending.values()) { clearTimeout(pending.timer); pending.reject(error); }
    this.pending.clear();
  }
  /** Functionality: Stop on failure and notify once. Inputs: Error. Outputs: closed WS and callback.
   * Logic: stopped before callbacks avoids reentrant close errors. Constraints: no retry/fallback. */
  fail(error) {
    if (this.stopped) return;
    this.stopped = true;
    this.rejectPending(error);
    if (this.socket?.readyState < 2) this.socket.close(4000, "video_error");
    if (!this.notified) { this.notified = true; this.onDisconnect(error); }
  }
  /** Functionality: Finish a bounded provider session. Inputs: instance operations/onEnd.
   * Outputs: reject uncompleted requests and lifecycle callback. Logic: no automatic restart.
   * Constraints: unresolved frames are not counted as completed; page keeps sent/returned counters. */
  end() {
    if (this.stopped) return;
    this.stopped = true;
    const ended = new Error("本次视频会话已结束，请重新开始。");
    ended.name = "VideoSessionEnded";
    this.rejectPending(ended);
    if (this.socket?.readyState < 2) this.socket.close(1000, "session_ended");
    this.onEnd();
  }
  /** Functionality: Explicitly stop the session and release its provider.
   * Inputs: socket/pending. Outputs: exact stop control, close, cancelled operations.
   * Logic: stop is sent on OPEN before close; failures are logged without payload.
   * Constraints: GPU may finish its current frame; server awaits actual completion, no restart. */
  stop() {
    if (this.stopped) return;
    this.stopped = true;
    this.rejectPending(new Error("已停止"));
    if (this.socket?.readyState === 1) {
      try { this.socket.send('{"kind":"stop"}'); }
      catch (error) { console.warn("Video stop control failed:", error.name); }
    }
    if (this.socket?.readyState < 2) this.socket.close(1000, "user_stop");
  }
}
