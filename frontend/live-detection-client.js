/** Responsibilities: Same-origin bounded binary video-session transport and result validation.
 * Implementation: HF PRF1/local PRF2 capture timestamps and shared PRS1 packets, twelve requests, serialized SHA256 validation;
 * resident/AOT/compiled Torch admission, one cold response budget, explicit stop/errors/end,
 * no reconnect or HTTP per frame.
 * Related Modules: frame-pipeline.js, camera.js; backend integration.asgi/services and core.hf_video_relay/realtime.protocol/risk.
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
 * MODEL_CLASSES — admitted model names (COCO yolo26n, walking-aid yolo26n-walk) and class counts.
 * Metadata limit is256KiB for300 fused/tracked objects retaining the existing box fields.
 * MOTION_STATES — accepted relative-motion labels; optional tracking fields remain additive.
 * RISK_LEVELS/DIRECTIONS — additive rule-result vocabularies; rule weights stay on the server.
 * Valid unexpired motion may persist while the current raw depth is null; server owns timeout.
 * Instance contract/sequence/expected/pending/queued bound identity, order and memory;
 * connectWait/messageChain/stopped/notified coordinate cancellation and error delivery;
 * provider selects hf/local explicitly; localSessionSeconds admits only55s/default or explicit null continuous local sessions;
 * HF always requires55s; cold/steady failure budgets remain55s/10s. lastCaptureTimeS enforces monotonic local source timestamps.
 * Local sessions and all results require an explicit distance mode, unit, and canonical interval.
 */
import {MAX_FRAME_BYTES} from "./echo-client.js";

export const MAX_INFLIGHT = 12;
const MAX_METADATA_BYTES = 256 * 1024;
const MAX_PACKET_BYTES = 8 + MAX_METADATA_BYTES + MAX_FRAME_BYTES;
const COLD_MS = 55000;
const STEADY_MS = 10000;
const HASH_PATTERN = /^[0-9a-f]{64}$/;
// Admitted detection models and their class counts (backend core.detection_model.PROFILES).
const MODEL_CLASSES = Object.freeze({"yolo26n": 80, "yolo26n-walk": 87});
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

/** Functionality: Validate an explicitly selected HF or local session.
 * Inputs: parsed hello, provider and localSessionSeconds(55 or explicit null). Outputs: accepted contract copy.
 * Logic: original model/settings/budgets; explicit resident/AOT/compiled Torch backend/precision
 * and three complete COCO quality records admitted by the server; no capability/token accepted.
 * Constraints: local resident FP32 admission requires real capability and distance identities,
 * never HF COCO evidence; HF quality checks and55s session limit remain intact; continuous local
 * admission changes no frame budgets.
 * Admission alone does not prove accuracy, hardware or performance. */
function validateSession(message, provider, localSessionSeconds) {
  const validation = message?.validation;
  const sessionSeconds = provider === "local" ? localSessionSeconds : 55;
  const classes = Object.hasOwn(MODEL_CLASSES, message?.model) ? MODEL_CLASSES[message.model] : null;
  if (!message || message.kind !== "session" || classes === null ||
      !["FP32", "FP16", "BF16"].includes(message.precision) || message.output_precision !== "FP32" ||
      !["PyTorch resident CUDA", "PyTorch AOT CUDA", "PyTorch compiled CUDA"].includes(message.backend) ||
      message.classes !== classes || message.confidence_threshold !== 0.25 ||
      message.max_edge !== 640 || message.jpeg_quality !== 0.8 || message.max_inflight !== MAX_INFLIGHT ||
      message.session_seconds !== sessionSeconds || message.cold_seconds !== 55 || message.steady_seconds !== 10 ||
      !HASH_PATTERN.test(message.source_sha256) || !Array.isArray(message.names) || message.names.length !== classes ||
      Object.hasOwn(message, "capability") || Object.hasOwn(message, "token"))
    throw new Error("视频会话配置无效，请检查服务端。");
  if (provider === "local") {
    const mode = message.distance_mode;
    const geometry = mode === "geometry";
    const validInterval = mode === "geometry" ? message.depth_interval === 0 :
      mode === "depth" ? message.depth_interval === 1 :
        mode === "temporal_depth" && Number.isSafeInteger(message.depth_interval) && message.depth_interval >= 1;
    if (message.provider !== "local" || message.protocol_version !== 2 ||
        message.backend !== "PyTorch resident CUDA" || message.precision !== "FP32" ||
        typeof message.session_id !== "string" || !/^[a-f0-9]{32}$/.test(message.session_id) ||
        !["depth", "geometry", "temporal_depth"].includes(mode) ||
        message.distance_units !== "m" || !validInterval ||
        (geometry ? (Object.hasOwn(message, "depth_model") || Object.hasOwn(message, "depth_units") ||
          message.distance_estimator !== "pinhole-height-v1" || message.capabilities?.distance !== true) :
          (message.depth_model !== "yolo26n-depth" || message.depth_units !== "m")) ||
        message.tracking_model !== "botsort" || message.risk_model !== "rules-v1" ||
        ["detection", "tracking", "risk"].some(name => message.capabilities?.[name] !== true) ||
        message.capabilities?.depth !== !geometry ||
        Object.hasOwn(message, "validation"))
      throw new Error("本地视频会话配置无效，请检查服务端。");
  } else if (!validation || validation.images !== 5000 || !Number.isInteger(validation.replicates) ||
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
  return {...message, provider};
}

/** Functionality: Decode and verify a complete ordered annotated result.
 * Inputs: packet ArrayBuffer, expected uint32 index, contract and optional original local captureTimeS.
 * Outputs: bytes/metadata/count/inferenceMs. Logic: bound header/JSON/JPEG; match admitted model,
 * compute precision/backend, FP32 output and hashes;
 * finite boxes and optional depth/tracking/TTC/risk fields checked before display; no filtering.
 * Every result must declare a supported metric distance mode and canonical interval.
 * Temporary missing depth may retain a historical estimate; the server expires motion history.
 * Constraints: browser image decoder checks actual dimensions separately; no accuracy/mAP claim. */
export async function decodeResult(packet, expected, contract, {captureTimeS} = {}) {
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
      !Number.isInteger(metadata.index) || metadata.model !== contract.model ||
      metadata.backend !== contract.backend || metadata.precision !== contract.precision ||
      metadata.output_precision !== "FP32" ||
      metadata.classes !== contract.classes || metadata.confidence_threshold !== 0.25 ||
      metadata.source_sha256 !== contract.source_sha256 || !HASH_PATTERN.test(metadata.raw_sha256) ||
      !HASH_PATTERN.test(metadata.jpeg_sha256) || !Number.isInteger(metadata.count) ||
      metadata.count < 0 || metadata.count > 300 || !Array.isArray(metadata.boxes) || metadata.boxes.length !== metadata.count ||
      !Number.isInteger(metadata.width) || !Number.isInteger(metadata.height) ||
      metadata.width < 1 || metadata.height < 1 || Math.max(metadata.width, metadata.height) > 640)
    throw new Error("视频结果模型或统计不匹配。");
  if (contract.provider === "local" &&
      (metadata.provider !== "local" || metadata.session_id !== contract.session_id ||
       metadata.timestamp_source !== "browser_capture" || !Number.isFinite(captureTimeS) ||
       metadata.capture_time_s !== captureTimeS || metadata.depth_model !== contract.depth_model ||
       metadata.distance_mode !== contract.distance_mode ||
       metadata.distance_units !== contract.distance_units ||
       metadata.depth_interval !== contract.depth_interval ||
       (contract.distance_mode === "geometry" &&
         metadata.distance_estimator !== contract.distance_estimator) ||
       (["geometry", "temporal_depth"].includes(contract.distance_mode) &&
         (metadata.depth_interval !== contract.depth_interval || metadata.distance_units !== "m")) ||
       metadata.tracking_model !== contract.tracking_model || metadata.risk_model !== contract.risk_model))
    throw new Error("本地视频结果会话或采集时间不匹配。");
  for (const field of ["inference_ms", "graph_setup_ms", "prepare_ms", "gpu_stage_ms", "render_ms", "server_ms"])
    if (!Number.isFinite(metadata[field]) || metadata[field] < 0)
      throw new Error("视频结果耗时无效。");
  if (Object.hasOwn(metadata, "depth_model") &&
      (typeof metadata.depth_model !== "string" || !metadata.depth_model || metadata.depth_units !== "m" ||
       !Number.isFinite(metadata.depth_ms) || metadata.depth_ms < 0))
    throw new Error("视频深度模型或耗时无效。");
  const mode = metadata.distance_mode;
  const geometry = mode === "geometry";
  if (!["depth", "geometry", "temporal_depth"].includes(mode) ||
      metadata.distance_units !== "m" ||
      (mode === "depth" && metadata.depth_interval !== 1) ||
      (mode === "geometry" && metadata.depth_interval !== 0) ||
      (geometry && (Object.hasOwn(metadata, "depth_model") ||
        metadata.distance_estimator !== "pinhole-height-v1" || metadata.distance_units !== "m" ||
        !Number.isFinite(metadata.distance_ms) || metadata.distance_ms < 0)) ||
      (mode === "temporal_depth" && (!Object.hasOwn(metadata, "depth_model") ||
        !Number.isSafeInteger(metadata.depth_interval) || metadata.depth_interval < 1 ||
        typeof metadata.depth_frame !== "boolean")))
    throw new Error("视频测距模式或耗时无效。");
  const hasDistance = geometry || Object.hasOwn(metadata, "depth_model");
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
  // Per-class thresholds may only LOWER the 0.25 default (e.g. traffic light 0.12, walk-model signals 0.05).
  const overrides = metadata.class_confidence_thresholds ?? {};
  if (typeof overrides !== "object" || Array.isArray(overrides) ||
      !Object.values(overrides).every(value => Number.isFinite(value) && value > 0 && value <= 0.25))
    throw new Error("视频检测阈值无效。");
  // The walk model merges colour boxes into one neutral "pedestrian light" (class id of ped_red) carrying
  // light_state; raw colour labels must never reach display or speech.
  const walk = contract.model === "yolo26n-walk";
  const signalId = walk ? contract.names.indexOf("ped_red") : -1;
  for (const box of metadata.boxes) {
    const signal = walk && box?.label === "pedestrian light" && box.class_id === signalId &&
      ["red", "green", "unknown"].includes(box.light_state);
    if (walk && ["ped_red", "ped_green", "ped_off"].includes(box?.label))
      throw new Error("视频检测框无效。");
    const minScore = Object.hasOwn(overrides, box?.label) ? overrides[box.label] : 0.25;
    if (!box || !Number.isInteger(box.class_id) || box.class_id < 0 || box.class_id >= contract.classes ||
        (box.label !== contract.names[box.class_id] && !signal) ||
        !Number.isFinite(box.score) || box.score < minScore || box.score > 1 ||
        !Array.isArray(box.xyxy) || box.xyxy.length !== 4)
      throw new Error("视频检测框无效。");
    const [x1, y1, x2, y2] = box.xyxy;
    if (![x1, y1, x2, y2].every(Number.isFinite) || x1 < 0 || y1 < 0 || x1 >= x2 || y1 >= y2 ||
        x2 > metadata.width || y2 > metadata.height)
      throw new Error("视频检测框坐标无效。");
    if (hasDistance) {
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
          (!hasDistance && (box.smoothed_distance_m !== null ||
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
   * Inputs: same-origin url, onDisconnect, onEnd, socketFactory, provider(hf default), localSessionSeconds(55 default/null).
   * Outputs: instance. Logic: bind events and bounded counters; no immediate network or camera.
   * Constraints: only explicit null opts local into continuous sessions; HF stays55s regardless this option.
   * Callbacks do not receive credentials; no arbitrary destination from query parameters. */
  constructor(url, onDisconnect, onEnd, {socketFactory = browserSocket, provider = "hf", localSessionSeconds = 55} = {}) {
    if (!["hf", "local"].includes(provider)) throw new Error("未知视频服务类型。");
    if (localSessionSeconds !== 55 && localSessionSeconds !== null)
      throw new Error("本地视频会话时长必须为 55 或 null。");
    this.provider = provider;
    this.localSessionSeconds = localSessionSeconds;
    this.lastCaptureTimeS = -1;
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
  /** Functionality: Send one original JPEG with ordered PRF1/PRF2 identity and local source timestamp.
   * Inputs: ArrayBuffer and optional capturedAt milliseconds (mandatory for local). Outputs: result/RTT Promise.
   * Logic: reserve pending before send; local PRF2 adds big-endian float64 capture seconds after uint32 index;
   * first55s, later10s; enforce count/socket byte bounds and monotonic local capture times.
   * Constraints: no encoding, base64, stale-frame queue or retry; max12 requests, caller gates capture. */
  sendFrame(buffer, {capturedAt} = {}) {
    if (this.stopped || !this.contract || this.socket.readyState !== 1)
      throw new Error("视频连接尚未就绪或已停止。");
    if (!(buffer instanceof ArrayBuffer) || !buffer.byteLength || buffer.byteLength > MAX_FRAME_BYTES)
      throw new Error("图像帧为空或超过 1 MiB 限制");
    if (this.pending.size >= MAX_INFLIGHT || this.sequence > 0xFFFFFFFF ||
        this.socket.bufferedAmount > MAX_INFLIGHT * (MAX_FRAME_BYTES + (this.provider === "local" ? 16 : 8)))
      throw new Error("视频发送超过在途边界。");
    const captureTimeS = capturedAt / 1000, local = this.provider === "local";
    if (local && (!Number.isFinite(captureTimeS) || captureTimeS < 0 || captureTimeS <= this.lastCaptureTimeS))
      throw new Error("本地视频需要严格递增的真实采集时间。");
    if (local) this.lastCaptureTimeS = captureTimeS;
    const index = this.sequence++, client = this, headerBytes = local ? 16 : 8;
    const packet = new Uint8Array(headerBytes + buffer.byteLength);
    const view = new DataView(packet.buffer);
    view.setUint32(0, local ? 0x50524632 : 0x50524631);
    view.setUint32(4, index);
    if (local) view.setFloat64(8, captureTimeS);
    packet.set(new Uint8Array(buffer), headerBytes);
    /** Functionality: Stop a timed-out frame. Inputs: closure index/client. Outputs: session failure.
     * Logic: reject every outstanding request. Constraints: no replacement frame or silent discard. */
    function deadline() { client.fail(new Error(`视频帧 ${index} 返回超时，请检查网络后重新开始。`)); }
    /** Functionality: Register one frame promise. Inputs: resolve/reject. Outputs: pending Map entry.
     * Logic: monotonic send timestamp and bounded timer. Constraints: synchronous registration before send. */
    function register(resolve, reject) {
      client.pending.set(index, {resolve, reject, captureTimeS, started: performance.now(),
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
        this.contract = validateSession(message, this.provider, this.localSessionSeconds);
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
          const result = await decodeResult(packet, client.expected, client.contract, {captureTimeS: pending.captureTimeS});
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
