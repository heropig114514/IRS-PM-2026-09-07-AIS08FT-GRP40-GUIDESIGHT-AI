/* Responsibilities: Load the fixed local bilingual warning pack and compose validated PCM on demand.
 * Implementation: Explicit full-pack or on-demand loading validates the corpus and each required WAV/hash
 * before exposing exact text/language/rate matches. Composition copies samples without resampling or playback.
 * Related Modules: video-alerts.js; assets/risk-audio/manifest.json; risk-speech-cache.js.
 * Declaration Index: resourceError; exactKeys; textKey; expectedText; validateManifest; pcmFromWav;
 * RiskAudioResources; RiskAudioResources.constructor; RiskAudioResources.describe;
 * RiskAudioResources.read; RiskAudioResources.digest; RiskAudioResources.load;
 * RiskAudioResources.initialize; RiskAudioResources.initialize.worker;
 * RiskAudioResources.loadClip; RiskAudioResources.prepare; RiskAudioResources.dispose.
 * Variable Index: LANGUAGES/CLASSES/DIRECTIONS/GRADES constrain the prepared corpus;
 * MAX_MANIFEST_BYTES/MAX_CLIP_BYTES/MAX_PACK_BYTES bound transfers and retained PCM;
 * HASH/CLIP_ID validate content identities; each instance owns one load, controller, timer and PCM map.
 * Constraints: No inference, risk changes, TTS, implicit retry, audio activation or audible-output claim.
 */
import {videoAlertText} from "./video-alerts.js";

const LANGUAGES = ["zh-CN", "en-US"], CLASSES = ["person", "bicycle", "car", "motorcycle", "chair", "obstacle"];
const DIRECTIONS = ["front", "front_left", "front_right"], GRADES = ["medium", "high", "critical"];
const MAX_MANIFEST_BYTES = 256 * 1024, MAX_CLIP_BYTES = 512 * 1024, MAX_PACK_BYTES = 4 * 1024 * 1024;
const HASH = /^[a-f0-9]{64}$/, CLIP_ID = /^(zh-CN|en-US)-(prefix|clause|sentence)-([a-f0-9]{64})$/;

/* Functionality: Produce a fixed resource error without response bodies or paths.
 * Inputs: One selected code. Outputs: Error with code. Logic: Keep diagnostics content-free.
 * Constraints: Callers never substitute a voice or retry when a covered pack fails. */
function resourceError(code = "invalid_audio_resource") {
  const error = new Error(code); error.code = code; return error;
}

/* Functionality: Require a plain object with a known field set.
 * Inputs: Object and fields. Outputs: Boolean. Logic: Compare own keys without mutation.
 * Constraints: Arrays, null and missing/extra fields fail validation. */
function exactKeys(value, fields) {
  return value !== null && typeof value === "object" && !Array.isArray(value) &&
    Object.keys(value).sort().join() === [...fields].sort().join();
}

/* Functionality: Identify one exact supported utterance without normalization.
 * Inputs: Language and transcript. Outputs: Stable map key. Logic: JSON preserves separators.
 * Constraints: No case folding, translation or approximate text match. */
function textKey(language, text) { return JSON.stringify([language, text]); }

/* Functionality: Validate resource wording against the existing production formatter.
 * Inputs: Fixed composition tuple. Outputs: Existing warning text.
 * Logic: Template fields only select the formatter's already defined approach branch.
 * Constraints: These fields are never observations or submitted as risk/model inputs. */
function expectedText(item) {
  return videoAlertText({label: item.label, direction: item.direction, risk_level: item.risk_level,
    motion_state: item.approaching ? "approaching" : "unknown",
    relative_speed_mps: item.approaching ? -2 : null,
    risk_reasons: item.approaching ? ["approaching", "closing_mps_ge_2"] : []}, item.language);
}

/* Functionality: Admit the full immutable bilingual resource contract before fetching WAVs.
 * Inputs: Parsed manifest. Outputs: Clip and exact-text composition maps.
 * Logic: Require every approved tuple, natural fragment identity, bounded sizes and exact wording.
 * Constraints: File and PCM hashes are checked separately; pack_sha256 is metadata only because
 * Python and JavaScript serialize integral/floating JSON numbers differently. No demo audio is loaded. */
function validateManifest(value) {
  if (!exactKeys(value, ["schema_version", "voice", "rate", "sample_rate", "format", "classes",
    "directions", "clips", "compositions", "model", "demos", "pack_sha256"]) ||
      value.schema_version !== 2 || value.voice !== "Cherry" || value.rate !== 2 ||
      value.sample_rate !== 24000 || value.format !== "pcm_s16le_mono" ||
      value.model !== "qwen3-tts-flash-realtime" || !HASH.test(value.pack_sha256) ||
      JSON.stringify(value.classes) !== JSON.stringify(CLASSES) ||
      JSON.stringify(value.directions) !== JSON.stringify(DIRECTIONS) ||
      !Array.isArray(value.clips) || value.clips.length !== 96 ||
      !Array.isArray(value.compositions) || value.compositions.length !== 216 ||
      !Array.isArray(value.demos) || value.demos.length !== 2) throw resourceError();
  const clips = new Map(), counts = {"zh-CN": 0, "en-US": 0}; let total = 0;
  for (const clip of value.clips) {
    const match = typeof clip?.id === "string" && CLIP_ID.exec(clip.id);
    if (!exactKeys(clip, ["id", "language", "text", "kind", "path", "sha256", "pcm_sha256",
      "byte_length", "pcm_byte_length", "samples", "duration_ms"]) || !match ||
        match[1] !== clip.language || match[2] !== clip.kind || clips.has(clip.id) ||
        clip.path !== `${clip.language}/${clip.id}.wav` ||
        typeof clip.text !== "string" || !clip.text.trim() || [...clip.text].length > 120 ||
        !HASH.test(clip.sha256) || !HASH.test(clip.pcm_sha256) ||
        !Number.isSafeInteger(clip.pcm_byte_length) || clip.pcm_byte_length < 2 ||
        clip.pcm_byte_length > MAX_CLIP_BYTES || clip.pcm_byte_length % 2 ||
        clip.byte_length !== clip.pcm_byte_length + 44 || clip.samples !== clip.pcm_byte_length / 2 ||
        !Number.isFinite(clip.duration_ms) || Math.abs(clip.duration_ms - clip.samples / 24) > 1e-8)
      throw resourceError();
    total += clip.byte_length; if (total > MAX_PACK_BYTES) throw resourceError();
    clips.set(clip.id, clip); counts[clip.language]++;
  }
  if (counts["zh-CN"] !== 57 || counts["en-US"] !== 39) throw resourceError();
  const compositions = new Map(), tuples = new Set(), ids = new Set(), used = new Set();
  for (const item of value.compositions) {
    if (!exactKeys(item, ["id", "language", "label", "direction", "risk_level", "approaching",
      "text", "clip_ids"]) || !LANGUAGES.includes(item.language) || !CLASSES.includes(item.label) ||
        !DIRECTIONS.includes(item.direction) || !GRADES.includes(item.risk_level) ||
        typeof item.approaching !== "boolean" || typeof item.id !== "string" || ids.has(item.id) ||
        item.id !== `${item.language}-${item.label}-${item.direction}-${item.risk_level}-${item.approaching ? "approaching" : "present"}` ||
        !Array.isArray(item.clip_ids) || item.clip_ids.length < 1 || item.clip_ids.length > 2 ||
        item.clip_ids.some(id => typeof id !== "string" || !clips.has(id)) || item.text !== expectedText(item))
      throw resourceError();
    const parts = item.clip_ids.map(id => clips.get(id));
    const whole = item.language === "zh-CN" && item.risk_level === "medium" && !item.approaching;
    if (parts.some(part => part.language !== item.language) || parts.map(part => part.text).join("") !== item.text ||
        JSON.stringify(parts.map(part => part.kind)) !== JSON.stringify(whole ? ["sentence"] : ["prefix", "clause"]))
      throw resourceError();
    const tuple = JSON.stringify([item.language, item.label, item.direction, item.risk_level, item.approaching]);
    const key = textKey(item.language, item.text);
    if (tuples.has(tuple) || compositions.has(key)) throw resourceError();
    tuples.add(tuple); ids.add(item.id); item.clip_ids.forEach(id => used.add(id)); compositions.set(key, item);
  }
  if (tuples.size !== 216 || used.size !== 96) throw resourceError();
  return {clips, compositions};
}

/* Functionality: Decode only the generated pack's canonical lossless WAV container.
 * Inputs: Exact file bytes and validated descriptor. Outputs: Detached raw PCM bytes.
 * Logic: Require the fixed 44-byte RIFF/PCM16 mono header and matching declared sample count.
 * Constraints: No general codec, resampling, trimming, inserted silence or WAV-container concatenation. */
export function pcmFromWav(bytes, clip) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (bytes.length !== clip.byte_length || bytes.length < 46 || view.getUint32(0) !== 0x52494646 ||
      view.getUint32(8) !== 0x57415645 || view.getUint32(12) !== 0x666d7420 || view.getUint32(36) !== 0x64617461 ||
      view.getUint32(4, true) !== bytes.length - 8 || view.getUint32(16, true) !== 16 ||
      view.getUint16(20, true) !== 1 || view.getUint16(22, true) !== 1 ||
      view.getUint32(24, true) !== 24000 || view.getUint32(28, true) !== 48000 ||
      view.getUint16(32, true) !== 2 || view.getUint16(34, true) !== 16 ||
      view.getUint32(40, true) !== clip.pcm_byte_length) throw resourceError();
  const pcm = bytes.slice(44);
  if (!pcm.some(value => value !== 0)) throw resourceError();
  return pcm;
}

/* Functionality: Own one prepared audio pack independently of any live warning or audio player.
 * Logic: Validate the manifest, load all or requested clips explicitly, then compose fresh sample copies.
 * Constraints: No retries, TTS fallback, risk state, speaker access or automatic output. */
export class RiskAudioResources {
  /* Functionality: Bind same-origin resource transport and bounded lifecycle controls.
   * Inputs: Fetch, Web Crypto, explicit pack base URL/origin and optional timer implementations.
   * Outputs: Idle resource owner. Logic: Validate configuration without issuing requests.
   * Constraints: Only the fixed risk-audio directory suffix is allowed; no credentials/query/fragment. */
  constructor({fetch = null, crypto = null, baseUrl, loadPolicy = "full",
    origin = globalThis.location?.origin, setTimer = globalThis.setTimeout.bind(globalThis),
    clearTimer = globalThis.clearTimeout.bind(globalThis)} = {}) {
    const base = new URL(baseUrl, origin);
    if (!["full", "on-demand"].includes(loadPolicy) || typeof setTimer !== "function" || typeof clearTimer !== "function" ||
        !["http:", "https:"].includes(base.protocol) || base.origin !== origin ||
        base.username || base.password || base.search || base.hash ||
        !base.pathname.endsWith("/assets/risk-audio/") || base.pathname.includes("//"))
      throw new TypeError("Invalid prepared audio configuration");
    Object.assign(this, {fetch, crypto, baseUrl: base.href, loadPolicy, setTimer, clearTimer});
    this.state = "idle"; this.promise = null; this.controller = null; this.timer = null;
    this.clips = new Map(); this.compositions = new Map(); this.pcm = new Map();
    this.bytes = 0; this.failure = null; this.composed = 0;
    this.clipPromises = new Map(); this.clipTail = Promise.resolve();
  }
  /* Functionality: Expose readiness and bounded counts without utterances or media.
   * Inputs: Owner state. Outputs: Content-free snapshot. Logic: Report only retained resources.
   * Constraints: Ready means the manifest is valid; loaded_clips reports available PCM, not physical audibility. */
  describe() {
    return {state: this.state, loaded_clips: this.pcm.size, total_clips: 96, ready_bytes: this.bytes,
      compositions: this.composed, failure: this.failure};
  }
  /* Functionality: Fetch one exact same-origin resource with streaming size enforcement.
   * Inputs: Fixed relative resource path and maximum bytes. Outputs: Complete byte array.
   * Logic: Reject redirect/status failures and stop as soon as the size budget is exceeded.
   * Constraints: One request, shared abort signal, no body/URL diagnostics or implicit retry. */
  async read(path, limit) {
    const url = new URL(path, this.baseUrl).href;
    const response = await this.fetch(url, {signal: this.controller.signal, credentials: "same-origin",
      redirect: "error", cache: "no-cache"});
    if (!response.ok || response.redirected || (response.url && response.url !== url))
      throw resourceError("risk_audio_unavailable");
    const length = response.headers.get("content-length");
    if (length !== null && (!/^\d+$/.test(length) || Number(length) > limit)) throw resourceError();
    const reader = response.body?.getReader();
    if (!reader) throw resourceError();
    const chunks = []; let bytes = 0;
    try {
      while (true) {
        const {done, value} = await reader.read(); if (done) break;
        if (!(value instanceof Uint8Array)) throw resourceError();
        bytes += value.byteLength; if (bytes > limit) throw resourceError(); chunks.push(value);
      }
    } finally { try { await reader.cancel(); } catch {} reader.releaseLock(); }
    const result = new Uint8Array(bytes); let offset = 0;
    for (const chunk of chunks) { result.set(chunk, offset); offset += chunk.byteLength; }
    return result;
  }
  /* Functionality: Hash resource bytes through the browser's existing cryptographic primitive.
   * Inputs: Bytes. Outputs: Lowercase SHA256. Logic: Hash exact content.
   * Constraints: No remote validation call or alternate integrity path. */
  async digest(bytes) {
    return Array.from(new Uint8Array(await this.crypto.subtle.digest("SHA-256", bytes)),
      value => value.toString(16).padStart(2, "0")).join("");
  }
  /* Functionality: Share manifest initialization and the explicitly selected whole-pack/on-demand policy.
   * Inputs: None. Outputs: Stable promise for validated owner or fixed failure.
   * Logic: One 60-second initialization deadline; full mode uses four workers; no failed-load restart.
   * Constraints: Failure releases all PCM and prevents a partially validated pack from playback. */
  load() {
    if (this.state === "disposed") return Promise.reject(resourceError("risk_audio_unavailable"));
    if (this.promise) return this.promise;
    if (typeof this.fetch !== "function" || typeof this.crypto?.subtle?.digest !== "function") {
      this.state = "failed"; this.failure = "risk_audio_unavailable";
      this.promise = Promise.reject(resourceError(this.failure)); return this.promise;
    }
    this.state = "loading"; this.controller = new AbortController();
    this.timer = this.setTimer(() => this.controller.abort(), 60000);
    this.promise = this.initialize().then(() => {
      if (this.state === "disposed" || this.controller.signal.aborted) throw resourceError("risk_audio_unavailable");
      this.state = "ready"; return this;
    }).catch(error => {
      const code = error?.code === "invalid_audio_resource" ? error.code : "risk_audio_unavailable";
      this.controller.abort(); this.pcm.clear(); this.clips.clear(); this.compositions.clear(); this.bytes = 0;
      if (this.state !== "disposed") { this.state = "failed"; this.failure = code; }
      throw resourceError(code);
    }).finally(() => { this.clearTimer(this.timer); this.timer = null; });
    return this.promise;
  }
  /* Functionality: Validate manifest and fetch all admitted source clips with bounded concurrency.
   * Inputs: Owned loader state. Outputs: Complete internal clip/PCM/composition maps.
   * Logic: Validate identity and file/PCM hashes before retaining any source samples.
   * Constraints: A failure aborts peer requests and prevents further admissions; no preview downloads. */
  async initialize() {
    const raw = await this.read("manifest.json", MAX_MANIFEST_BYTES); let manifest;
    try { manifest = JSON.parse(new TextDecoder("utf-8", {fatal: true}).decode(raw)); }
    catch (error) { if (error?.code) throw error; throw resourceError(); }
    const validated = validateManifest(manifest), pending = [...validated.clips.values()];
    this.clips = validated.clips; this.compositions = validated.compositions; let next = 0;
    if (this.loadPolicy === "on-demand") return;
    /* Functionality: Load one admitted source at a time within the four-worker budget.
     * Inputs: Shared fixed pending list/index. Outputs: Verified retained PCM.
     * Logic: Check cancellation before admission and after asynchronous hash operations.
     * Constraints: No late worker can repopulate cleared or disposed owner state. */
    const worker = async () => {
      while (next < pending.length && !this.controller.signal.aborted && this.state === "loading") {
        const clip = pending[next++];
        try {
          const identity = new TextEncoder().encode(`${clip.language}\0${clip.kind}\0${clip.text}`);
          if (!clip.id.endsWith(await this.digest(identity))) throw resourceError();
          const bytes = await this.read(clip.path, clip.byte_length);
          if (await this.digest(bytes) !== clip.sha256) throw resourceError();
          const pcm = pcmFromWav(bytes, clip);
          if (await this.digest(pcm) !== clip.pcm_sha256) throw resourceError();
          if (this.controller.signal.aborted || this.state !== "loading") throw resourceError("risk_audio_unavailable");
          this.pcm.set(clip.id, pcm); this.bytes += pcm.length;
        } catch (error) { this.controller.abort(); throw error; }
      }
    };
    const results = await Promise.allSettled(Array.from({length: 4}, () => worker()));
    const failure = results.find(result => result.status === "rejected" && result.reason?.code === "invalid_audio_resource") ||
      results.find(result => result.status === "rejected");
    if (failure) throw failure.reason;
    if (this.pcm.size !== 96) throw resourceError("risk_audio_unavailable");
  }
  // Deduplicate exact clips and serialize network work. A corrupt/failed clip invalidates this
  // owner, just as a failed full-pack load does; reload is the explicit recovery boundary.
  loadClip(id) {
    if (this.clipPromises.has(id)) return this.clipPromises.get(id);
    const pending = this.clipTail.then(async () => {
      if (this.state !== "ready" || this.controller.signal.aborted)
        throw resourceError(this.failure || "risk_audio_unavailable");
      const clip = this.clips.get(id);
      const timer = this.setTimer(() => this.controller.abort(), 60000);
      try {
        const identity = new TextEncoder().encode(`${clip.language}\0${clip.kind}\0${clip.text}`);
        if (!clip.id.endsWith(await this.digest(identity))) throw resourceError();
        const bytes = await this.read(clip.path, clip.byte_length);
        if (await this.digest(bytes) !== clip.sha256) throw resourceError();
        const pcm = pcmFromWav(bytes, clip);
        if (await this.digest(pcm) !== clip.pcm_sha256) throw resourceError();
        if (this.state !== "ready" || this.controller.signal.aborted) throw resourceError("risk_audio_unavailable");
        this.pcm.set(id, pcm); this.bytes += pcm.length;
      } catch (error) {
        const code = error?.code === "invalid_audio_resource" ? error.code : "risk_audio_unavailable";
        this.controller.abort(); this.pcm.clear(); this.bytes = 0;
        if (this.state !== "disposed") { this.state = "failed"; this.failure = code; }
        throw resourceError(code);
      } finally { this.clearTimer(timer); }
    });
    this.clipPromises.set(id, pending);
    this.clipTail = pending.catch(() => {});
    return pending;
  }
  /* Functionality: Compose one exact supported warning as an independent PCM block.
   * Inputs: Existing command language/rate/text. Outputs: PCM protocol payload or explicit uncovered null.
   * Logic: Require verified clips; concatenate ordered samples, preserving already synthesized 2x rate.
   * Constraints: Uncovered profile/text is distinct from failure; no WAV-header joins, TTS or output start. */
  async prepare(request) {
    if (request?.rate !== 2 || !LANGUAGES.includes(request?.language)) return null;
    await this.load();
    if (this.state !== "ready") throw resourceError(this.failure || "risk_audio_unavailable");
    // Explicit short-warning mode reuses the verified grade prefix for every class.
    // No fuzzy matching: only an exact prepared prefix is admitted here.
    const prefix = [...this.clips.values()].find(clip => clip.kind === "prefix" &&
      clip.language === request.language && clip.text === request.text);
    const composition = this.compositions.get(textKey(request.language, request.text)) ||
      (prefix ? {clip_ids: [prefix.id]} : null);
    if (!composition) return null;
    if (this.loadPolicy === "on-demand") {
      await Promise.all(composition.clip_ids.map(id => this.loadClip(id)));
      if (this.state !== "ready") throw resourceError(this.failure || "risk_audio_unavailable");
    }
    const parts = composition.clip_ids.map(id => this.pcm.get(id));
    const bytes = new Uint8Array(parts.reduce((total, part) => total + part.length, 0)); let offset = 0;
    for (const part of parts) { bytes.set(part, offset); offset += part.length; }
    this.composed++;
    return {sample_rate: 24000, format: "pcm_s16le_mono", chunks: [bytes.buffer], audio_bytes: bytes.length};
  }
  /* Functionality: Release all prepared media and abort only this owner's pending fetches.
   * Inputs: None. Outputs: Terminal disposed owner. Logic: Set state before aborting and clearing data.
   * Constraints: Disposed instances cannot restart or deliver any late completed resource. */
  dispose() {
    this.state = "disposed"; this.controller?.abort(); this.clearTimer(this.timer); this.timer = null;
    this.pcm.clear(); this.clips.clear(); this.compositions.clear(); this.bytes = 0;
    this.clipPromises.clear();
  }
}
