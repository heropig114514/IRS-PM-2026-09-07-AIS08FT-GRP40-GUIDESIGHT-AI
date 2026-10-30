/* Responsibilities: Load exact bilingual red/green signal sentences before local playback.
 * Implementation: Fixed same-origin manifest, bounded whole-pack loading, SHA256/WAV/PCM checks.
 * Related Modules: signal-alerts.js; risk-audio-resources.js; assist-mvp.js; risk-speech-cache.js.
 * Declaration Index: failure; exactKeys; key; manifestClips; SignalAudioResources;
 * SignalAudioResources.constructor; SignalAudioResources.describe; SignalAudioResources.read;
 * SignalAudioResources.digest; SignalAudioResources.load; SignalAudioResources.initialize;
 * SignalAudioResources.prepare; SignalAudioResources.dispose.
 * Variable Index: MAX_MANIFEST_BYTES/MAX_PCM_BYTES/MAX_PACK_BYTES bound retained public assets;
 * pcm stores four immutable source clips, while each preparation returns its own sample copy.
 * Loading resources does not authorize a signal announcement, start a speaker or issue TTS. */
import {SIGNAL_TEXTS} from "./signal-alerts.js";
import {pcmFromWav} from "./risk-audio-resources.js";

const MAX_MANIFEST_BYTES = 16384, MAX_PCM_BYTES = 512 * 1024, MAX_PACK_BYTES = 1024 * 1024;
const LANGUAGES = ["zh-CN", "en-US"], STATES = ["red", "green"], HASH = /^[0-9a-f]{64}$/;

/** Functionality: Create a fixed resource error. Inputs: authored code. Outputs: Error.
 * Logic: expose only the existing cache error vocabulary. Constraints: no URLs, text or PCM. */
function failure(code = "invalid_audio_resource") { return Object.assign(new Error(code), {code}); }

/** Functionality: Require one exact object shape. Inputs: object and known keys. Outputs: boolean.
 * Logic: compare own keys. Constraints: arrays, missing fields and extra metadata fail validation. */
function exactKeys(value, fields) {
  return value !== null && typeof value === "object" && !Array.isArray(value) &&
    Object.keys(value).sort().join() === [...fields].sort().join();
}

/** Functionality: Preserve exact transcript identity. Inputs: language/text. Outputs: map key.
 * Logic: JSON encoding keeps boundaries. Constraints: no normalization or partial phrase matching. */
function key(language, text) { return JSON.stringify([language, text]); }

/** Functionality: Admit the fixed four-sentence resource contract.
 * Inputs: parsed manifest. Outputs: exact-text clip map. Logic: check every tuple/field and bounds.
 * Constraints: no general playlist, transcript substitution, alternate voice or additional language. */
function manifestClips(manifest) {
  if (!exactKeys(manifest, ["schema_version", "voice", "rate", "sample_rate", "format", "model", "clips"]) ||
      manifest.schema_version !== 1 || manifest.voice !== "Cherry" || manifest.rate !== 2 ||
      manifest.sample_rate !== 24000 || manifest.format !== "pcm_s16le_mono" ||
      manifest.model !== "qwen3-tts-flash-realtime" || !Array.isArray(manifest.clips) || manifest.clips.length !== 4)
    throw failure();
  const clips = new Map(); let total = 0;
  for (const clip of manifest.clips) {
    if (!exactKeys(clip, ["language", "state", "text", "path", "sha256", "pcm_sha256",
      "byte_length", "pcm_byte_length", "samples", "duration_ms"]) ||
        !LANGUAGES.includes(clip.language) || !STATES.includes(clip.state) ||
        clip.text !== SIGNAL_TEXTS[clip.language][clip.state] ||
        clip.path !== `${clip.language}/${clip.state}.wav` ||
        !HASH.test(clip.sha256) || !HASH.test(clip.pcm_sha256) ||
        !Number.isSafeInteger(clip.pcm_byte_length) || clip.pcm_byte_length < 2 ||
        clip.pcm_byte_length > MAX_PCM_BYTES || clip.pcm_byte_length % 2 ||
        clip.byte_length !== clip.pcm_byte_length + 44 || clip.samples !== clip.pcm_byte_length / 2 ||
        !Number.isFinite(clip.duration_ms) || Math.abs(clip.duration_ms - clip.samples / 24) > 1e-8 ||
        clips.has(key(clip.language, clip.text))) throw failure();
    total += clip.byte_length;
    if (total > MAX_PACK_BYTES) throw failure();
    clips.set(key(clip.language, clip.text), clip);
  }
  return clips;
}

/** Functionality: Own verified fixed signal PCM independently of perception and audio activation.
 * Logic: one idempotent bounded load, then exact prepared requests. Constraints: no retries or TTS fallback. */
export class SignalAudioResources {
  /** Functionality: Freeze resource transport without requesting or playing media.
   * Inputs: fetch/crypto, same-origin base, optional timers. Outputs: idle owner.
   * Logic: admit only the known local/mobile asset mount. Constraints: no query, credentials or remote authority. */
  constructor({fetch = null, crypto = null, baseUrl, origin = globalThis.location?.origin,
    setTimer = globalThis.setTimeout.bind(globalThis), clearTimer = globalThis.clearTimeout.bind(globalThis)} = {}) {
    const base = new URL(baseUrl, origin);
    if (!["http:", "https:"].includes(base.protocol) || base.origin !== origin ||
        base.username || base.password || base.search || base.hash ||
        !["/assets/signal-audio/", "/mvp/assets/signal-audio/"].includes(base.pathname) ||
        typeof setTimer !== "function" || typeof clearTimer !== "function")
      throw new TypeError("Invalid prepared signal audio configuration");
    Object.assign(this, {fetch, crypto, baseUrl: base.href, setTimer, clearTimer});
    this.state = "idle"; this.promise = this.controller = this.timer = null;
    this.pcm = new Map(); this.clips = new Map(); this.bytes = this.preparations = 0; this.failure = null;
  }

  /** Functionality: Expose body-free readiness. Inputs: owner state. Outputs: scalar snapshot.
   * Logic: count retained PCM only. Constraints: ready does not imply an audible or authorized announcement. */
  describe() {
    return {state: this.state, loaded_clips: this.pcm.size, total_clips: 4, ready_bytes: this.bytes,
      preparations: this.preparations, failure: this.failure};
  }

  /** Functionality: Read one exact asset under a strict streaming byte limit.
   * Inputs: admitted relative path and maximum length. Outputs: bytes. Logic: one same-origin fetch.
   * Constraints: reject redirects/status errors and abort excess bodies; no retry or alternate endpoint. */
  async read(path, limit) {
    const url = new URL(path, this.baseUrl).href;
    const response = await this.fetch(url, {signal: this.controller.signal, credentials: "same-origin",
      redirect: "error", cache: "no-cache"});
    if (!response.ok || response.redirected || response.url && response.url !== url)
      throw failure("risk_audio_unavailable");
    const length = response.headers.get("content-length");
    if (length !== null && (!/^\d+$/.test(length) || Number(length) > limit)) throw failure();
    const reader = response.body?.getReader(); if (!reader) throw failure();
    const chunks = []; let total = 0;
    try {
      while (true) {
        const {done, value} = await reader.read(); if (done) break;
        if (!(value instanceof Uint8Array)) throw failure();
        total += value.byteLength; if (total > limit) throw failure(); chunks.push(value);
      }
    } finally { try { await reader.cancel(); } catch {} reader.releaseLock(); }
    const bytes = new Uint8Array(total); let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    return bytes;
  }

  /** Functionality: Hash exact container/sample bytes. Inputs: bytes. Outputs: lowercase SHA256.
   * Logic: use Web Crypto. Constraints: no remote checksum or weaker substitute. */
  async digest(bytes) {
    return Array.from(new Uint8Array(await this.crypto.subtle.digest("SHA-256", bytes)),
      value => value.toString(16).padStart(2, "0")).join("");
  }

  /** Functionality: Load all four sentences once with explicit failure state.
   * Inputs: none. Outputs: owner-ready promise. Logic: share one 60-second deadline and abort on failure.
   * Constraints: failed or disposed owners cannot silently restart, synthesize or emit audio. */
  load() {
    if (this.state === "disposed") return Promise.reject(failure("risk_audio_unavailable"));
    if (this.promise) return this.promise;
    if (typeof this.fetch !== "function" || typeof this.crypto?.subtle?.digest !== "function") {
      this.state = "failed"; this.failure = "risk_audio_unavailable";
      return this.promise = Promise.reject(failure(this.failure));
    }
    this.state = "loading"; this.controller = new AbortController();
    this.timer = this.setTimer(() => this.controller.abort(), 60000);
    this.promise = this.initialize().then(() => {
      if (this.state === "disposed" || this.controller.signal.aborted) throw failure("risk_audio_unavailable");
      this.state = "ready"; return this;
    }).catch(error => {
      const code = error?.code === "invalid_audio_resource" ? error.code : "risk_audio_unavailable";
      this.controller.abort(); this.pcm.clear(); this.clips.clear(); this.bytes = 0;
      if (this.state !== "disposed") { this.state = "failed"; this.failure = code; }
      throw failure(code);
    }).finally(() => { this.clearTimer(this.timer); this.timer = null; });
    return this.promise;
  }

  /** Functionality: Verify every file before making the fixed pack available.
   * Inputs: owner transport. Outputs: complete retained PCM. Logic: at most four concurrent clip reads.
   * Constraints: no late resolution can refill disposed/failed state; no sample editing. */
  async initialize() {
    const raw = await this.read("manifest.json", MAX_MANIFEST_BYTES); let manifest;
    try { manifest = JSON.parse(new TextDecoder("utf-8", {fatal: true}).decode(raw)); }
    catch { throw failure(); }
    this.clips = manifestClips(manifest);
    await Promise.all([...this.clips].map(async ([identity, clip]) => {
      const bytes = await this.read(clip.path, clip.byte_length);
      if (await this.digest(bytes) !== clip.sha256) throw failure();
      const pcm = pcmFromWav(bytes, clip);
      if (await this.digest(pcm) !== clip.pcm_sha256) throw failure();
      if (this.controller.signal.aborted || this.state !== "loading") throw failure("risk_audio_unavailable");
      this.pcm.set(identity, pcm); this.bytes += pcm.byteLength;
    }));
  }

  /** Functionality: Supply an independent ready PCM block for an exact fixed sentence.
   * Inputs: original TTS command language/text/rate. Outputs: prepared protocol payload or uncovered null.
   * Logic: recognize the corpus before loading and preserve provider-baked 2x audio unchanged.
   * Constraints: readiness cannot authorize a stale signal; caller owns all scheduling and confirmation. */
  async prepare(request) {
    if (request?.rate !== 2 || !LANGUAGES.includes(request?.language) ||
        !Object.values(SIGNAL_TEXTS[request.language]).includes(request.text)) return null;
    await this.load();
    if (this.state !== "ready") throw failure("risk_audio_unavailable");
    const bytes = this.pcm.get(key(request.language, request.text)).slice(); this.preparations++;
    return {sample_rate: 24000, format: "pcm_s16le_mono", chunks: [bytes.buffer], audio_bytes: bytes.length};
  }

  /** Functionality: Release PCM and cancel pending resource requests.
   * Inputs: none. Outputs: disposed owner. Logic: abort before clearing state.
   * Constraints: no restart, playback cancellation or changes to another resource owner. */
  dispose() {
    if (this.state === "disposed") return;
    this.state = "disposed"; this.controller?.abort();
    if (this.timer !== null) this.clearTimer(this.timer); this.timer = null;
    this.pcm.clear(); this.clips.clear(); this.bytes = 0;
  }
}
