/* Responsibilities: Reuse prepared or backend-generated short risk phrases without replaying stale alerts.
 * Implementation: A bounded in-memory PCM cache exposes request-scoped WebSocket-compatible sockets;
 * an abandoned synthesis may finish once, while every playback remains owned by SpeechOutput's fresh-risk gate.
 * Related Modules: assist-mvp.js, assist-guidance.js, risk-audio-resources.js, ../speech-output.js;
 * unchanged /ws/tts/ protocol remains the explicit path for text/rates outside the prepared corpus.
 * Declaration Index: command; messageEvent; CachedRiskSocket; CachedRiskSocket.constructor;
 * CachedRiskSocket.send; CachedRiskSocket.close; CachedRiskSocket.message; CachedRiskSocket.finish;
 * RiskSpeechCache; RiskSpeechCache.constructor; RiskSpeechCache.createSocket; RiskSpeechCache.describe;
 * RiskSpeechCache.attach; RiskSpeechCache.detach; RiskSpeechCache.touch; RiskSpeechCache.remove;
 * RiskSpeechCache.makeRoom; RiskSpeechCache.pump; RiskSpeechCache.start; RiskSpeechCache.startNative;
 * RiskSpeechCache.receivePrepared; RiskSpeechCache.arm;
 * RiskSpeechCache.clearTimers; RiskSpeechCache.receive; RiskSpeechCache.complete;
 * RiskSpeechCache.fail; RiskSpeechCache.deliver; RiskSpeechCache.error;
 * RiskSpeechCache.cancelPending; RiskSpeechCache.dispose.
 * Variable Index: PCM_RATE/ERROR_CODES bound the existing backend contract; UUID validates canonical IDs;
 * entries retains only bounded short phrases and complete immutable PCM; active/bytes track actual resources;
 * each entry owns native transport/timers/subscribers; clients bounds consumers independently of phrase entries;
 * each client retains only its current request identity; resources is an optional prepared-PCM resolver;
 * limits cap concurrency, entries, ready bytes, individual clips and lifetime; counters omit content.
 * Constraints: No disk, microphone, camera, voice substitution, provider retry, model/risk change or audible-output claim.
 */
const PCM_RATE = 24000;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const ERROR_CODES = new Set(["tts_not_configured", "tts_timeout", "tts_upstream_error",
  "tts_upstream_unavailable", "tts_upstream_protocol", "tts_audio_limit", "invalid_speech_request", "invalid_message",
  "risk_audio_unavailable", "invalid_audio_resource"]);

/* Functionality: Validate one short, explicit backend risk-phrase command.
 * Inputs: JSON text. Outputs: Parsed immutable command or TypeError.
 * Logic: Preserve exact text, language and rate; exclude arbitrary extra fields and malformed Unicode/UUIDs.
 * Constraints: Only callers explicitly choosing the risk transport may use it; assistant/manual text is not cached. */
function command(raw) {
  if (typeof raw !== "string" || raw.length > 2048) throw new TypeError("Invalid risk command");
  const value = JSON.parse(raw), fields = ["language", "rate", "request_id", "text", "type"];
  if (!value || Array.isArray(value) || Object.keys(value).sort().join() !== fields.join() ||
      value.type !== "synthesize" || typeof value.request_id !== "string" || !UUID.test(value.request_id) ||
      typeof value.text !== "string" || !value.text.trim() || [...value.text].length > 120 ||
      [...value.text].some(character => character.codePointAt(0) >= 0xD800 && character.codePointAt(0) <= 0xDFFF) ||
      !["zh-CN", "en-US"].includes(value.language) || !Number.isFinite(value.rate) || value.rate < 0.5 || value.rate > 2)
    throw new TypeError("Invalid risk command");
  return Object.freeze(value);
}

/* Functionality: Create a text/binary transport event on browsers and independent Node fixtures.
 * Inputs: One bounded protocol payload. Outputs: Event with read-only data.
 * Logic: Use the standard event API; Constraints: This does not dispatch audio to hardware. */
function messageEvent(data) {
  const event = new Event("message"); Object.defineProperty(event, "data", {value: data}); return event;
}

/* Functionality: Represent one cancelable consumer of a cached risk clip.
 * Logic: Match WebSocket's opening/send/close boundary while a separate cache owns any preparation.
 * Constraints: This class never schedules audio; close immediately prevents later protocol delivery. */
export class CachedRiskSocket extends EventTarget {
  /* Functionality: Create an asynchronously opened consumer. Inputs: Cache/address. Outputs: Connecting socket.
   * Logic: Defer open until SpeechOutput has installed handlers. Constraints: No native connection before send. */
  constructor(cache, url) {
    super(); Object.assign(this, {cache, url, readyState: 0, binaryType: "arraybuffer", sent: false, entry: null, request: null});
    cache.queueTask(() => {
      if (cache.disposed) return this.close();
      if (this.readyState === 0) { this.readyState = 1; this.dispatchEvent(new Event("open")); }
    });
  }
  /* Functionality: Submit exactly one synthesis command. Inputs: Original JSON. Outputs: Cache subscription or safe error.
   * Logic: Native-like state guard precedes validation. Constraints: No implicit second request or retries. */
  send(raw) {
    if (this.readyState !== 1) throw new Error("Risk socket is not open");
    if (this.sent) return this.cache.error(this, "invalid_message");
    this.sent = true;
    try { this.request = command(raw); } catch { return this.cache.error(this, "invalid_speech_request"); }
    this.cache.attach(this);
  }
  /* Functionality: Detach this consumer immediately. Inputs: Optional native-compatible close arguments.
   * Outputs: Closed consumer and one deferred close event. Logic: Keep running preparation independent.
   * Constraints: No later audio/config/completion can reach this consumer. */
  close() {
    if (this.readyState === 3) return;
    this.readyState = 3; this.cache.detach(this);
    this.cache.queueTask(() => this.dispatchEvent(new Event("close")));
  }
  /* Functionality: Deliver one current protocol frame. Inputs: Text or fresh ArrayBuffer.
   * Outputs: Message event only while open. Logic: Recheck state after every synchronous owner callback.
   * Constraints: A first-PCM cancellation prevents all subsequent chunks. */
  message(data) { if (this.readyState === 1) this.dispatchEvent(messageEvent(data)); }
  /* Functionality: Close after terminal protocol delivery. Inputs: None. Outputs: Closed consumer.
   * Logic: Delegate exact subscription cleanup. Constraints: Completion does not prove sound. */
  finish() { this.close(); }
}

/* Functionality: Prepare and reuse a bounded set of short exact risk phrases.
 * Logic: Full validated PCM is retained briefly; independent consumers receive their own IDs and byte copies.
 * Constraints: Native synthesis serves explicitly uncovered text/rates; asset failures never fall back.
 * No automatic retry; freshness, risk priority and playback authorization stay with the owner. */
export class RiskSpeechCache {
  /* Functionality: Bind explicit native transport, clocks and resource limits.
   * Inputs: WebSocket, same-origin authority, optional prepared resources, timers/task queue and limits.
   * Outputs: Empty cache. Logic: Bound two active preparations, 24 entries, 4 MiB ready PCM and 512 KiB per clip.
   * Constraints: No network or audio starts at construction; TTL is five minutes from completion, not each cache hit. */
  constructor({WebSocket = globalThis.WebSocket, origin = globalThis.location?.origin, resources = null,
    now = () => performance.now(), setTimer = globalThis.setTimeout.bind(globalThis), clearTimer = globalThis.clearTimeout.bind(globalThis),
    queueTask = globalThis.queueMicrotask.bind(globalThis), maxEntries = 24, maxBytes = 4 * 1024 * 1024,
    maxClipBytes = 512 * 1024, maxInFlight = 2, ttlMs = 300000} = {}) {
    if (![WebSocket, now, setTimer, clearTimer, queueTask].every(value => typeof value === "function") || !origin ||
        ![maxEntries, maxBytes, maxClipBytes, maxInFlight, ttlMs].every(value => Number.isSafeInteger(value) && value > 0) ||
        resources !== null && typeof resources?.prepare !== "function")
      throw new TypeError("Invalid risk cache configuration");
    const authority = new URL(origin);
    if (!["http:", "https:"].includes(authority.protocol) || authority.origin !== origin) throw new TypeError("Invalid risk cache origin");
    Object.assign(this, {WebSocket, origin, resources, now, setTimer, clearTimer, queueTask,
      maxEntries, maxBytes, maxClipBytes, maxInFlight, ttlMs});
    this.entries = new Map(); this.clients = new Set(); this.active = this.bytes = 0; this.disposed = this.cancelling = false;
    this.counters = {hits: 0, misses: 0, preparations: 0, prepared_compositions: 0,
      native_preparations: 0, completed: 0, failures: 0, evictions: 0};
  }
  /* Functionality: Create one request-scoped same-origin risk transport.
   * Inputs: Existing TTS WebSocket address. Outputs: CachedRiskSocket.
   * Logic: Require the exact current authority and fixed TTS path suffix, without URL parameters.
   * Constraints: No remote origin, credentials, custom endpoint, disposed cache or provider request is admitted;
   * consumers are additionally bounded to twice the entry limit plus two connecting clients. */
  createSocket(address) {
    const url = new URL(address), authority = new URL(this.origin);
    if (this.disposed || this.clients.size >= this.maxEntries * 2 + 2 || url.protocol !== (authority.protocol === "https:" ? "wss:" : "ws:") ||
        url.host !== authority.host || !["/ws/tts/", "/mvp/ws/tts/"].includes(url.pathname) ||
        url.username || url.password || url.search || url.hash) throw new TypeError("Invalid risk socket address");
    const client = new CachedRiskSocket(this, url.href); this.clients.add(client); return client;
  }
  /* Functionality: Expose bounded operational counters. Inputs: Cache state. Outputs: Content-free metadata.
   * Logic: Count resources only. Constraints: No text, keys, UUIDs, URLs or PCM bodies are returned. */
  describe() {
    return {...this.counters, entries: this.entries.size, ready_bytes: this.bytes, in_flight: this.active,
      waiting: [...this.entries.values()].filter(entry => entry.state === "queued").length,
      subscribers: this.clients.size, disposed: this.disposed};
  }
  /* Functionality: Subscribe to one phrase or create a bounded preparation.
   * Inputs: Validated client command. Outputs: Queued preparation or deferred ready/error delivery.
   * Logic: UUID is excluded from the key; expire completed PCM by original completion time.
   * Constraints: A failed orphan is delivered once before removal, preventing a hidden immediate retry. */
  attach(client) {
    if (this.disposed) return this.error(client, "tts_upstream_unavailable");
    for (const entry of [...this.entries.values()])
      if (entry.state === "ready" && this.now() - entry.at >= this.ttlMs) this.remove(entry);
    const request = client.request, key = JSON.stringify([client.url, "Cherry", request.language, request.rate, request.text]);
    let entry = this.entries.get(key);
    if (entry) { this.counters.hits++; this.touch(entry); }
    else {
      if (!this.makeRoom()) return this.error(client, "tts_audio_limit");
      this.counters.misses++;
      entry = {key, url: client.url, request, state: "queued", clients: new Set(), chunks: [], bytes: 0,
        config: false, native: null, timers: new Map(), at: this.now(), error: null};
      this.entries.set(key, entry); this.arm(entry, "queue", 55000);
    }
    entry.clients.add(client); client.entry = entry;
    if (["ready", "failed"].includes(entry.state)) this.queueTask(() => this.deliver(entry, client));
    else this.pump();
  }
  /* Functionality: Remove one consumer without discarding a running reusable preparation.
   * Inputs: Closed client. Outputs: Detached subscription. Logic: Unstarted orphan work is removed.
   * Constraints: Active synthesis continues only within existing time/size limits or until explicit cancelPending. */
  detach(client) {
    this.clients.delete(client);
    const entry = client.entry; client.entry = null;
    if (!entry) return;
    entry.clients.delete(client);
    if (entry.state === "queued" && !entry.clients.size) this.remove(entry);
  }
  /* Functionality: Refresh eviction ordering. Inputs: Owned entry. Outputs: Same identity at the map tail.
   * Logic: Preserve completion TTL. Constraints: No capture-clock or output-state changes. */
  touch(entry) { if (this.entries.get(entry.key) === entry) { this.entries.delete(entry.key); this.entries.set(entry.key, entry); } }
  /* Functionality: Release a nonrunning cache entry. Inputs: Owned ready/failed/queued entry.
   * Outputs: Removed entry and accounted PCM bytes. Logic: Clear deadlines before deletion.
   * Constraints: Active native preparations are stopped only by complete/fail/cancelPending. */
  remove(entry) {
    if (this.entries.get(entry.key) !== entry) return;
    this.clearTimers(entry);
    if (entry.state === "ready") this.bytes -= entry.bytes;
    this.entries.delete(entry.key); this.counters.evictions++;
  }
  /* Functionality: Reserve one bounded entry slot. Inputs: Optional protected entry/extra ready bytes.
   * Outputs: Capacity Boolean. Logic: Evict least-recent unobserved ready clips.
   * Constraints: Never evict active work, live consumers or undelivered failures merely to admit more work;
   * forgetting an unseen failure could silently retry the same phrase on a later frame. */
  makeRoom(protectedEntry = null, extraBytes = 0) {
    while ((!protectedEntry && this.entries.size >= this.maxEntries) || this.bytes + extraBytes > this.maxBytes) {
      const entry = [...this.entries.values()].find(item => item !== protectedEntry &&
        item.state === "ready" && !item.clients.size);
      if (!entry) return false;
      this.remove(entry);
    }
    return true;
  }
  /* Functionality: Start only admitted preparation. Inputs: Queued subscriptions. Outputs: At most two active entries.
   * Logic: FIFO map order, no repeated sends or failed-item retries. Constraints: Explicit cancellation disables pumping. */
  pump() {
    if (this.disposed || this.cancelling) return;
    for (const entry of this.entries.values()) {
      if (this.active >= this.maxInFlight) break;
      if (entry.state === "queued" && entry.clients.size) this.start(entry);
    }
  }
  /* Functionality: Select prepared PCM before the explicit uncovered-corpus native path.
   * Inputs: Queued entry. Outputs: One bounded preparation using the exact text/language/rate.
   * Logic: Resolve assets once; null means unsupported wording/rate, while rejection is terminal.
   * Constraints: Asset loading never relaxes freshness or emits audio; late results after an explicit
   * cancellation/disposal are ignored, and asset failure never starts native synthesis. */
  start(entry) {
    this.clearTimers(entry); entry.state = "loading"; this.active++; this.counters.preparations++;
    this.arm(entry, "overall", 60000);
    if (!this.resources) return this.startNative(entry);
    try {
      Promise.resolve(this.resources.prepare(entry.request)).then(result => {
        if (this.disposed || entry.state !== "loading" || this.entries.get(entry.key) !== entry) return;
        try {
          if (result === null) this.startNative(entry);
          else this.receivePrepared(entry, result);
        } catch { this.fail(entry, "invalid_audio_resource"); }
      }, error => {
        if (this.disposed || entry.state !== "loading" || this.entries.get(entry.key) !== entry) return;
        this.fail(entry, error?.code === "invalid_audio_resource" ? "invalid_audio_resource" : "risk_audio_unavailable");
      });
    } catch (error) {
      this.fail(entry, error?.code === "invalid_audio_resource" ? "invalid_audio_resource" : "risk_audio_unavailable");
    }
  }
  /* Functionality: Open one unchanged same-origin backend synthesis for uncovered wording or rate.
   * Inputs: Active entry with no prepared match, or a cache without prepared resources.
   * Outputs: Request-bound native socket and bounded connection timer.
   * Logic: One original command; full PCM is buffered, never forwarded while incomplete.
   * Constraints: No provider URL/key, reconnection, asset-error fallback or second synthesis. */
  startNative(entry) {
    if (entry.state !== "loading" || this.entries.get(entry.key) !== entry || this.disposed) return;
    this.counters.native_preparations++; this.arm(entry, "connect", 15000);
    try {
      const socket = entry.native = new this.WebSocket(entry.url); socket.binaryType = "arraybuffer";
      socket.addEventListener("open", () => {
        if (entry.state !== "loading") return;
        this.clearTimer(entry.timers.get("connect")); entry.timers.delete("connect"); this.arm(entry, "first", 55000);
        try { socket.send(JSON.stringify(entry.request)); } catch { this.fail(entry, "tts_upstream_unavailable"); }
      });
      socket.addEventListener("message", event => this.receive(entry, event.data));
      socket.addEventListener("error", () => this.fail(entry, "tts_upstream_unavailable"));
      socket.addEventListener("close", () => { if (entry.state === "loading") this.fail(entry, "tts_upstream_unavailable"); });
    } catch { this.fail(entry, "tts_upstream_unavailable"); }
  }
  /* Functionality: Admit one complete prepared composition without changing its samples.
   * Inputs: Active entry and resolver metadata/PCM buffers. Outputs: Private immutable cache copies.
   * Logic: Check fixed format, chunk count, even PCM bytes and exact bounded total before publication.
   * Constraints: No resampling, second speed change or playback; owner validation still precedes audio. */
  receivePrepared(entry, result) {
    if (!result || result.sample_rate !== PCM_RATE || result.format !== "pcm_s16le_mono" ||
        !Array.isArray(result.chunks) || !result.chunks.length || result.chunks.length > 8 ||
        !Number.isSafeInteger(result.audio_bytes) || result.audio_bytes <= 0)
      return this.fail(entry, "invalid_audio_resource");
    if (result.audio_bytes > this.maxClipBytes) return this.fail(entry, "tts_audio_limit");
    const chunks = []; let total = 0;
    for (const chunk of result.chunks) {
      if (!(chunk instanceof ArrayBuffer) && !ArrayBuffer.isView(chunk)) return this.fail(entry, "invalid_audio_resource");
      const bytes = chunk instanceof ArrayBuffer ? new Uint8Array(chunk) : new Uint8Array(chunk.buffer, chunk.byteOffset, chunk.byteLength);
      if (!bytes.byteLength || bytes.byteLength % 2) return this.fail(entry, "invalid_audio_resource");
      total += bytes.byteLength;
      if (total > this.maxClipBytes) return this.fail(entry, "tts_audio_limit");
      chunks.push(new Uint8Array(bytes));
    }
    if (total !== result.audio_bytes) return this.fail(entry, "invalid_audio_resource");
    entry.config = true; entry.chunks = chunks; entry.bytes = total; entry.prepared = true;
    this.complete(entry);
  }
  /* Functionality: Bound one preparation phase. Inputs: Entry, phase and timeout milliseconds.
   * Outputs: A safe terminal timeout on expiry. Logic: Original active identity owns each timer.
   * Constraints: Timeouts fail once and never retry. */
  arm(entry, phase, delay) {
    if (entry.timers.has(phase)) this.clearTimer(entry.timers.get(phase));
    entry.timers.set(phase, this.setTimer(() => this.fail(entry, "tts_timeout"), delay));
  }
  /* Functionality: Clear every owned preparation deadline. Inputs: Entry. Outputs: Empty timer map.
   * Logic: Exact timer handles only. Constraints: Does not alter other cache entries or the playback owner. */
  clearTimers(entry) { for (const timer of entry.timers.values()) this.clearTimer(timer); entry.timers.clear(); }
  /* Functionality: Validate and retain one backend PCM/control message.
   * Inputs: Loading entry and native payload. Outputs: Buffered immutable chunks, complete clip or safe failure.
   * Logic: Require exact request identity, one fixed audio config, even nonempty PCM and bounded byte count.
   * Constraints: No downstream audio before successful complete; raw server diagnostics are never forwarded. */
  receive(entry, data) {
    if (entry.state !== "loading") return;
    if (data instanceof ArrayBuffer || ArrayBuffer.isView(data)) {
      const bytes = data instanceof ArrayBuffer ? new Uint8Array(data) : new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
      if (!entry.config || !bytes.byteLength || bytes.byteLength % 2) return this.fail(entry, "tts_upstream_protocol");
      if (entry.bytes + bytes.byteLength > this.maxClipBytes) return this.fail(entry, "tts_audio_limit");
      entry.bytes += bytes.byteLength; entry.chunks.push(new Uint8Array(bytes));
      if (entry.timers.has("first")) { this.clearTimer(entry.timers.get("first")); entry.timers.delete("first"); }
      this.arm(entry, "idle", 35000); return;
    }
    let value;
    try { value = JSON.parse(data); } catch { return this.fail(entry, "tts_upstream_protocol"); }
    if (!value || typeof value !== "object" || Array.isArray(value) ||
        value.request_id && value.request_id !== entry.request.request_id) return this.fail(entry, "tts_upstream_protocol");
    if (value.type === "error") return this.fail(entry, ERROR_CODES.has(value.code) ? value.code : "tts_upstream_protocol");
    if (value.request_id !== entry.request.request_id) return this.fail(entry, "tts_upstream_protocol");
    if (value.type === "audio_config" && !entry.config && !entry.bytes &&
        value.sample_rate === PCM_RATE && value.format === "pcm_s16le_mono") { entry.config = true; return; }
    if (value.type === "complete" && entry.config && entry.bytes > 0 &&
        (value.audio_bytes === undefined || value.audio_bytes === entry.bytes)) return this.complete(entry);
    this.fail(entry, "tts_upstream_protocol");
  }
  /* Functionality: Publish a complete immutable clip to current subscribers.
   * Inputs: Fully validated loading entry. Outputs: Ready cache record and deferred per-client replies.
   * Logic: Release native work, enforce aggregate PCM bound, retain original completion time for TTL.
   * Constraints: Readiness is preparation only; SpeechOutput still validates current risk before any audio node starts. */
  complete(entry) {
    if (!this.makeRoom(entry, entry.bytes)) return this.fail(entry, "tts_audio_limit");
    this.clearTimers(entry); entry.state = "ready"; this.active--; this.bytes += entry.bytes;
    entry.at = this.now(); this.counters.completed++;
    if (entry.prepared) this.counters.prepared_compositions++;
    this.touch(entry);
    try { entry.native?.close(1000, "risk_prepared"); } catch {} entry.native = null;
    for (const client of [...entry.clients]) this.queueTask(() => this.deliver(entry, client));
    this.pump();
  }
  /* Functionality: Finish one failed preparation without retry or raw diagnostic leakage.
   * Inputs: Queued/loading entry and safe error code. Outputs: Retained orphan failure or delivered error.
   * Logic: Release timers/native resources once; preserve an unobserved failure for its next requester.
   * Constraints: Error delivery does not create another provider call. */
  fail(entry, code) {
    if (!["queued", "loading"].includes(entry.state)) return;
    if (entry.state === "loading") this.active--;
    entry.state = "failed"; entry.error = ERROR_CODES.has(code) ? code : "tts_upstream_protocol";
    entry.chunks.length = 0; entry.bytes = 0; this.counters.failures++; this.clearTimers(entry);
    try { entry.native?.close(1000, "risk_failed"); } catch {} entry.native = null;
    for (const client of [...entry.clients]) this.queueTask(() => this.deliver(entry, client));
    this.pump();
  }
  /* Functionality: Deliver one complete cached clip or retained failure to its exact consumer.
   * Inputs: Entry/client ownership. Outputs: Current UUID controls and independent PCM ArrayBuffers.
   * Logic: Synchronous callbacks may close or replace the consumer; recheck identity between chunks.
   * Constraints: A canceled consumer receives no later payload; cached byte arrays remain private and immutable. */
  deliver(entry, client) {
    if (client.readyState !== 1 || client.entry !== entry || !entry.clients.has(client)) return;
    if (entry.state === "failed") {
      this.error(client, entry.error, false);
      if (!entry.clients.size) this.remove(entry);
      return;
    }
    if (entry.state !== "ready") return;
    const request_id = client.request.request_id;
    client.message(JSON.stringify({type: "audio_config", request_id, sample_rate: PCM_RATE, format: "pcm_s16le_mono"}));
    for (const bytes of entry.chunks) {
      if (client.readyState !== 1 || client.entry !== entry) return;
      client.message(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength));
    }
    if (client.readyState !== 1 || client.entry !== entry) return;
    client.message(JSON.stringify({type: "complete", request_id, audio_bytes: entry.bytes})); client.finish();
  }
  /* Functionality: Send one fixed safe transport error. Inputs: Consumer/code and optional deferral.
   * Outputs: Error frame then close. Logic: Use the current request UUID when available.
   * Constraints: No command text, URL, provider detail, automatic enabling or retry. */
  error(client, code, defer = true) {
    const deliver = () => {
      if (client.readyState !== 1) return;
      client.message(JSON.stringify({type: "error", request_id: client.request?.request_id ?? null, code,
        detail: "Risk speech preparation failed."})); client.finish();
    };
    if (defer) this.queueTask(deliver); else deliver();
  }
  /* Functionality: Stop every unfinished preparation and subscriber at an explicit lifecycle boundary.
   * Inputs: Fixed caller reason. Outputs: Resource counters; completed short clips remain reusable.
   * Logic: Suspend queue pumping before closing clients/native sockets and removing partial PCM.
   * Constraints: No output resumes automatically; a completed cache hit still needs a fresh owner request. */
  cancelPending(reason = "risk_stop") {
    this.cancelling = true;
    try {
      for (const client of [...this.clients]) client.close();
      for (const entry of [...this.entries.values()]) {
        if (!["queued", "loading"].includes(entry.state)) continue;
        if (entry.state === "loading") this.active--;
        entry.state = "cancelled"; this.clearTimers(entry);
        try { entry.native?.close(1000, "risk_stop"); } catch {} entry.native = null;
        entry.chunks.length = 0; this.remove(entry);
      }
    } finally { this.cancelling = false; }
    return {reason, ...this.describe()};
  }
  /* Functionality: Release all risk cache state on page disposal.
   * Inputs: None. Outputs: Disposed empty cache. Logic: Abort pending work before clearing completed PCM.
   * Constraints: No audio files, retained subscriber callbacks or future native requests. */
  dispose() {
    this.disposed = true; this.cancelPending("disposed");
    for (const entry of [...this.entries.values()]) {
      for (const client of [...entry.clients]) client.close();
      this.remove(entry);
    }
    return this.describe();
  }
}
