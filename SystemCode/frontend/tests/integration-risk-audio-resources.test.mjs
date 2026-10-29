/* Responsibilities: Verify fixed risk-audio resources with real packaged WAVs and local transport fixtures.
 * Implementation: Exact corpus/PCM comparisons, concurrent loads, corrupt resources, bounded reads and
 * cancellation exercise the production loader without sockets, TTS, browser activation or playback.
 * Related Modules: ../integration/risk-audio-resources.js; ../assets/risk-audio/manifest.json.
 * Declaration Index: fixture; fixture.fetch; owner; rejectCode; testRealPack; testUnsupported;
 * testConcurrent; testManifest; testIntegrity; testWav; testPcmHash; testReadBoundaries;
 * testDispose; testTimeout; testConfiguration; testMissingCapability.
 * Variable Index: ROOT/BASE/ORIGIN locate fixed public fixtures; manifest and WAV bytes are test-owned copies;
 * fixture barrier state synchronizes the first four clip reads for deterministic concurrency checks.
 * Constraints: Assertions establish resource/protocol integrity, not pronunciation or physical audibility.
 */
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {createHash, webcrypto} from "node:crypto";
import test from "node:test";
import {RiskAudioResources} from "../integration/risk-audio-resources.js";
import {briefVideoAlertText, preparedVideoAlertText} from "../integration/video-alerts.js";

const ROOT = new URL("../assets/risk-audio/", import.meta.url);
const ORIGIN = "http://127.0.0.1:8766", BASE = `${ORIGIN}/mvp/assets/risk-audio/`;

test("prepared content keeps direction and grade and needs no synthesis for named or generic targets", async () => {
  const transport = fixture(), resources = owner(transport);
  await resources.load(); const attempts = transport.calls.length;
  const cases = [
    [{label:"chair",direction:"front_left",risk_level:"high"},"警告，左前方有椅子。"],
    [{label:"stairs",direction:"front_right",risk_level:"critical"},"危险，右前方有障碍物。"],
    [{label:"person",direction:"front",risk_level:"medium"},"注意前方的行人。"],
  ];
  for (const [box, expected] of cases) {
    assert.equal(preparedVideoAlertText(box,"zh-CN"),expected);
    for (const language of ["zh-CN","en-US"]) {
      const prepared=await resources.prepare({language,rate:2,text:preparedVideoAlertText(box,language)});
      assert.ok(prepared?.audio_bytes > 0);
    }
  }
  assert.equal(transport.calls.length,attempts); resources.dispose();
});

test("all short grades and arbitrary classes reuse exact verified PCM with no extra fetch", async () => {
  const transport = fixture(), resources = owner(transport);
  await resources.load();
  const initialFetches = transport.calls.length;
  for (const language of ["zh-CN", "en-US"])
    for (const risk_level of ["medium", "high", "critical"])
      for (const label of ["chair", "stairs", "person", "unmapped-object"] ) {
        const text = briefVideoAlertText({risk_level, label}, language);
        const prepared = await resources.prepare({text, language, rate: 2});
        const prefix = transport.manifest.clips.find(clip => clip.language === language &&
          clip.kind === "prefix" && clip.text === text);
        assert.ok(prefix);
        assert.deepEqual(Buffer.from(prepared.chunks[0]), readFileSync(new URL(prefix.path, ROOT)).subarray(44));
      }
  assert.equal(transport.calls.length, initialFetches);
  assert.equal(await resources.prepare({language: "zh-CN", rate: 2, text: "警告"}), null);
  resources.dispose();
});

/* Functionality: Serve only test-owned copies of the real local warning resource pack.
 * Inputs: Optional descriptor/body mutations and a four-clip synchronization option.
 * Outputs: Fetch fixture and observable attempt/concurrency counters.
 * Logic: The optional barrier makes the four-worker concurrency assertion independent of crypto scheduling.
 * Constraints: Paths are resolved only after asserting exact expected base; no upload or provider call. */
function fixture({changeManifest = null, changeBody = null, synchronizeClipWorkers = false} = {}) {
  const manifest = JSON.parse(readFileSync(new URL("manifest.json", ROOT), "utf8"));
  changeManifest?.(manifest);
  const state = {manifest, calls: [], active: 0, maximum: 0, clipBarrierArrivals: 0,
    clipBarrierTimedOut: false};
  let releaseClipBarrier;
  const clipBarrier = synchronizeClipWorkers ? new Promise(resolve => { releaseClipBarrier = resolve; }) : null;
  let clipBarrierTimeout;
  if (clipBarrier) clipBarrierTimeout = setTimeout(() => {
    state.clipBarrierTimedOut = true; releaseClipBarrier();
  }, 1000);
  /* Functionality: Return one explicit local resource response and record concurrency.
   * Inputs: Requested URL/options. Outputs: Standard Response. Logic: Yield once before reading fixtures.
   * Constraints: The fixture never falls through to native fetch or obeys arbitrary absolute paths. */
  state.fetch = async (url, options) => {
    assert.ok(url.startsWith(BASE));
    assert.equal(options.redirect, "error"); assert.equal(options.credentials, "same-origin");
    const path = url.slice(BASE.length);
    assert.ok(path === "manifest.json" || state.manifest.clips.some(clip => clip.path === path));
    state.calls.push(path); state.active++; state.maximum = Math.max(state.maximum, state.active);
    if (clipBarrier && path !== "manifest.json" && state.clipBarrierArrivals < 4) {
      state.clipBarrierArrivals++;
      if (state.clipBarrierArrivals === 4) {
        clearTimeout(clipBarrierTimeout); releaseClipBarrier();
      }
      await clipBarrier;
    }
    await new Promise(resolve => setImmediate(resolve)); state.active--;
    if (options.signal.aborted) throw new DOMException("Aborted", "AbortError");
    let body = path === "manifest.json" ? Buffer.from(JSON.stringify(state.manifest)) :
      Buffer.from(readFileSync(new URL(path, ROOT)));
    body = changeBody?.(path, body) || body;
    return new Response(body, {headers: {"content-length": String(body.length)}});
  };
  return state;
}

/* Functionality: Bind one isolated production loader to a fixture transport.
 * Inputs: Fixture plus explicit overrides. Outputs: Idle resource owner.
 * Logic: Use Node's standard Web Crypto implementation. Constraints: No global fetch substitution. */
function owner(transport, extra = {}) {
  return new RiskAudioResources({fetch: transport.fetch, crypto: webcrypto, baseUrl: BASE,
    origin: ORIGIN, ...extra});
}

/* Functionality: Check the exact safe resource error code.
 * Inputs: Promise and expected code. Outputs: Rejection assertion.
 * Logic: No body/path detail is exposed. Constraints: Does not retry the rejected operation. */
function rejectCode(promise, code) {
  return assert.rejects(promise, error => error.code === code && error.message === code);
}

/* Functionality: Exercise every real bilingual combination with unchanged packaged samples.
 * Inputs: All real source WAVs and manifest. Outputs: Exact ordered PCM and isolated consumer buffers.
 * Logic: Compare the entire corpus, not a formatter imitation. Constraints: No speaker or TTS call. */
test("prepared pack loads 96 verified WAVs and composes all 216 exact warnings", async function testRealPack() {
  const transport = fixture({synchronizeClipWorkers: true}), resources = owner(transport);
  assert.equal(resources.describe().state, "idle");
  await resources.load();
  assert.equal(transport.calls.length, 97); assert.equal(transport.clipBarrierTimedOut, false);
  assert.equal(transport.maximum, 4);
  assert.deepEqual(resources.describe(), {state: "ready", loaded_clips: 96, total_clips: 96,
    ready_bytes: transport.manifest.clips.reduce((sum, clip) => sum + clip.pcm_byte_length, 0),
    compositions: 0, failure: null});
  const clips = new Map(transport.manifest.clips.map(clip => [clip.id, clip]));
  for (const composition of transport.manifest.compositions) {
    const prepared = await resources.prepare({...composition, rate: 2});
    const expected = Buffer.concat(composition.clip_ids.map(id =>
      readFileSync(new URL(clips.get(id).path, ROOT)).subarray(44)));
    assert.equal(prepared.sample_rate, 24000); assert.equal(prepared.format, "pcm_s16le_mono");
    assert.equal(prepared.audio_bytes, expected.length); assert.equal(prepared.chunks.length, 1);
    assert.deepEqual(Buffer.from(prepared.chunks[0]), expected);
    new Uint8Array(prepared.chunks[0]).fill(0);
    const repeated = await resources.prepare({...composition, rate: 2});
    assert.deepEqual(Buffer.from(repeated.chunks[0]), expected);
  }
  assert.equal(transport.calls.length, 97); assert.equal(resources.describe().compositions, 432);
  assert.equal(await resources.prepare({language: "zh-CN", rate: 2, text: "未覆盖的文本。"}), null);
  assert.equal(await resources.prepare({language: "en-US", rate: 2,
    text: transport.manifest.compositions[0].text}), null);
  resources.dispose(); assert.equal(resources.describe().ready_bytes, 0);
});

/* Functionality: Distinguish an uncovered profile from a broken covered resource pack.
 * Inputs: Unsupported rate/language and malformed optional request. Outputs: Null without loading.
 * Logic: Bypass only explicit profile mismatch. Constraints: Never pretend another rate matches. */
test("uncovered rates and languages cause no resource work", async function testUnsupported() {
  const transport = fixture(), resources = owner(transport);
  for (const request of [{language: "zh-CN", rate: 1, text: "危险。"},
    {language: "ja-JP", rate: 2, text: "警告"}, null]) assert.equal(await resources.prepare(request), null);
  assert.equal(transport.calls.length, 0); assert.equal(resources.describe().state, "idle");
  resources.dispose(); await rejectCode(resources.load(), "risk_audio_unavailable");
});

/* Functionality: Deduplicate simultaneous preload and covered warning requests.
 * Inputs: One owner and concurrent callers. Outputs: One complete fetch pass and fresh outputs.
 * Logic: Share the original promise without sharing mutable audio buffers. Constraints: No retries. */
test("concurrent load and prepare calls share one bounded load", async function testConcurrent() {
  const transport = fixture(), resources = owner(transport), item = transport.manifest.compositions[0];
  const first = resources.load(); assert.equal(first, resources.load());
  const [, one, two] = await Promise.all([first, resources.prepare({...item, rate: 2}),
    resources.prepare({...item, rate: 2})]);
  assert.equal(transport.calls.length, 97); assert.notEqual(one.chunks[0], two.chunks[0]);
  resources.dispose();
});

/* Functionality: Reject altered corpus, wording and path descriptors before loading any source file.
 * Inputs: Independent manifest corruptions. Outputs: Sticky invalid-resource failure.
 * Logic: Keep failed loads distinguishable from uncovered text. Constraints: No alternate synthesis. */
test("manifest failures cannot become partial packs or trigger retries", async function testManifest() {
  const changes = [value => { value.voice = "Other"; }, value => { value.clips.pop(); },
    value => { value.clips[0].path = "../private.wav"; },
    value => { value.compositions[0].text += " altered"; },
    value => { value.compositions[0].clip_ids = ["missing"]; },
    value => { value.compositions[1] = value.compositions[0]; },
    value => { value.clips[0].samples++; }, value => { value.extra = true; },
    value => { value.clips[0].language = "en-US"; },
    value => { value.clips[0].pcm_byte_length = 600000; }];
  for (const changeManifest of changes) {
    const transport = fixture({changeManifest}), resources = owner(transport);
    const first = resources.load(); await rejectCode(first, "invalid_audio_resource");
    assert.equal(first, resources.load());
    await rejectCode(resources.prepare({rate: 2, language: "zh-CN", text: "uncovered"}), "invalid_audio_resource");
    assert.deepEqual(transport.calls, ["manifest.json"]); assert.equal(resources.describe().ready_bytes, 0);
    resources.dispose();
  }
});

/* Functionality: Refuse altered source bytes while clearing already admitted peer data.
 * Inputs: One modified WAV and concurrent workers. Outputs: Fixed failure and no retained PCM.
 * Logic: Verify exact container hash before exposing any samples. Constraints: No retry or partial use. */
test("source hash failure aborts the bounded load and stays failed", async function testIntegrity() {
  const transport = fixture({changeBody(path, body) {
    if (path !== "manifest.json") body[body.length - 1] ^= 1; return body;
  }}), resources = owner(transport);
  await rejectCode(resources.load(), "invalid_audio_resource");
  assert.equal(resources.describe().state, "failed"); assert.equal(resources.describe().loaded_clips, 0);
  assert.ok(transport.calls.length <= 5); const attempts = transport.calls.length;
  await rejectCode(resources.load(), "invalid_audio_resource"); assert.equal(transport.calls.length, attempts);
  resources.dispose();
});

/* Functionality: Reject a WAV whose file checksum matches but whose declared PCM format does not.
 * Inputs: Modified sample rate plus corresponding container hash. Outputs: Invalid-resource failure.
 * Logic: Container validation is independent of the checksum. Constraints: Never resample to hide mismatch. */
test("canonical WAV format is validated independently of its file hash", async function testWav() {
  let changed;
  const transport = fixture({changeManifest(value) {
    const clip = value.clips[0]; changed = Buffer.from(readFileSync(new URL(clip.path, ROOT)));
    changed.writeUInt32LE(22050, 24); clip.sha256 = createHash("sha256").update(changed).digest("hex");
  }, changeBody(path, body) { return path === transport.manifest.clips[0].path ? changed : body; }});
  const resources = owner(transport); await rejectCode(resources.load(), "invalid_audio_resource"); resources.dispose();
});

/* Functionality: Check raw sample integrity independently of WAV-container integrity.
 * Inputs: Valid WAV with a wrong PCM digest. Outputs: Invalid-resource failure.
 * Logic: Both hashes must match. Constraints: Metadata never overrides actual samples. */
test("PCM hashes are verified separately", async function testPcmHash() {
  const resources = owner(fixture({changeManifest(value) { value.clips[0].pcm_sha256 = "0".repeat(64); }}));
  await rejectCode(resources.load(), "invalid_audio_resource"); resources.dispose();
});

/* Functionality: Bound manifest transfer and reject failed, redirected or malformed responses.
 * Inputs: Explicit response fixtures. Outputs: Safe sticky failure and one attempt.
 * Logic: Cover status, URL, streaming limits, JSON and announced size independently.
 * Constraints: No native fetch, redirects, credentials or response bodies in errors. */
test("resource reads enforce byte limits and same-origin responses", async function testReadBoundaries() {
  const redirected = new Response("{}"); Object.defineProperty(redirected, "redirected", {value: true});
  const wrongUrl = new Response("{}"); Object.defineProperty(wrongUrl, "url", {value: "https://example.org/pack.json"});
  for (const [response, code] of [[new Response("private", {status: 503}), "risk_audio_unavailable"],
    [redirected, "risk_audio_unavailable"], [wrongUrl, "risk_audio_unavailable"],
    [new Response("{}", {headers: {"content-length": "999999"}}), "invalid_audio_resource"],
    [new Response("{}", {headers: {"content-length": "bad"}}), "invalid_audio_resource"],
    [new Response(new Uint8Array(262145)), "invalid_audio_resource"],
    [new Response("{bad"), "invalid_audio_resource"], [new Response(null), "invalid_audio_resource"]]) {
    let calls = 0;
    const resources = owner({fetch: async () => { calls++; return response; }});
    await rejectCode(resources.load(), code); assert.equal(calls, 1); resources.dispose();
  }
  const resources = owner({fetch: async () => { throw new TypeError("private network failure"); }});
  await rejectCode(resources.load(), "risk_audio_unavailable"); resources.dispose();
});

/* Functionality: Prevent a pending pack from repopulating data after explicit teardown.
 * Inputs: Aborted fixture request. Outputs: Terminal disposed owner and zero resources.
 * Logic: Abort the exact owned request and retain no ready publication. Constraints: No restart. */
test("disposal aborts an active load and rejects late preparation", async function testDispose() {
  let signal;
  const resources = owner({fetch: (_url, options) => {
    signal = options.signal;
    return new Promise((_resolve, reject) => signal.addEventListener("abort", () =>
      reject(new DOMException("Aborted", "AbortError")), {once: true}));
  }});
  const loading = resources.load(); resources.dispose();
  assert.equal(signal.aborted, true); await rejectCode(loading, "risk_audio_unavailable");
  assert.equal(resources.describe().state, "disposed"); assert.equal(resources.describe().ready_bytes, 0);
  await rejectCode(resources.prepare({language: "zh-CN", rate: 2, text: "危险。"}), "risk_audio_unavailable");
});

/* Functionality: Bound a stalled transfer with the explicit whole-pack deadline.
 * Inputs: Injectable timer and abort-aware stalled fetch. Outputs: One failure and timer release.
 * Logic: Fire the selected deadline without sleeping. Constraints: Failure never retries. */
test("whole-pack timeout aborts fetch and remains a safe failure", async function testTimeout() {
  let expire, cleared = 0;
  const resources = owner({fetch: (_url, options) => new Promise((_resolve, reject) =>
    options.signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), {once: true}))},
  {setTimer(callback, ms) { assert.equal(ms, 60000); expire = callback; return 7; },
    clearTimer(handle) { if (handle === 7) cleared++; }});
  const loading = resources.load(); expire(); await rejectCode(loading, "risk_audio_unavailable");
  assert.equal(cleared, 1); assert.equal(resources.describe().failure, "risk_audio_unavailable"); resources.dispose();
});

/* Functionality: Restrict pack ownership to the explicit local static-resource directory.
 * Inputs: Invalid authorities and resource paths. Outputs: Constructor refusal without I/O.
 * Logic: Reject credentials, query and alternate origins/suffixes. Constraints: No URL fallback. */
test("resource owner rejects invalid origins and paths", function testConfiguration() {
  for (const baseUrl of ["https://example.org/assets/risk-audio/", `${BASE}?key=hidden`, `${BASE}#x`,
    "http://user:password@127.0.0.1:8766/assets/risk-audio/", `${ORIGIN}/private/`,
    `${ORIGIN}/mvp//assets/risk-audio/`])
    assert.throws(() => owner(fixture(), {baseUrl}), TypeError);
});

/* Functionality: Keep pages usable when prepared-resource browser capabilities are absent.
 * Inputs: Missing fetch or Web Crypto. Outputs: Constructible owner and explicit failed load.
 * Logic: Capability refusal is deferred to load. Constraints: No global transport or TTS fallback. */
test("missing browser capabilities fail safely without throwing at construction", async function testMissingCapability() {
  for (const extra of [{fetch: undefined}, {crypto: undefined}]) {
    const resources = owner(fixture(), extra);
    await rejectCode(resources.load(), "risk_audio_unavailable");
    assert.equal(resources.describe().state, "failed"); resources.dispose();
  }
});

test("on-demand loads only requested clips, deduplicates, and preserves all bilingual PCM", async () => {
  const transport = fixture(), resources = owner(transport, {loadPolicy: "on-demand"});
  await resources.load();
  assert.deepEqual(transport.calls, ["manifest.json"]);
  assert.equal(resources.describe().loaded_clips, 0);
  const first = transport.manifest.compositions[0];
  const [one, two] = await Promise.all([resources.prepare({...first, rate: 2}), resources.prepare({...first, rate: 2})]);
  assert.equal(transport.calls.length, 1 + first.clip_ids.length);
  assert.notEqual(one.chunks[0], two.chunks[0]);
  const clips = new Map(transport.manifest.clips.map(clip => [clip.id, clip]));
  for (const item of transport.manifest.compositions) {
    const result = await resources.prepare({...item, rate: 2});
    const expected = Buffer.concat(item.clip_ids.map(id => readFileSync(new URL(clips.get(id).path, ROOT)).subarray(44)));
    assert.deepEqual(Buffer.from(result.chunks[0]), expected);
  }
  assert.equal(transport.calls.length, 97);
  assert.equal(transport.maximum, 1);
  assert.equal(resources.describe().loaded_clips, 96);
  resources.dispose();
});

test("on-demand rejects corrupt audio without retries, partial playback or native substitution", async () => {
  const transport = fixture({changeBody(path, bytes) {
    if (path !== "manifest.json") bytes[bytes.length - 1] ^= 1; return bytes;
  }}), resources = owner(transport, {loadPolicy: "on-demand"});
  const item = transport.manifest.compositions.find(item => item.clip_ids.length === 2);
  await resources.load();
  await rejectCode(resources.prepare({...item, rate: 2}), "invalid_audio_resource");
  assert.equal(resources.describe().loaded_clips, 0);
  assert.equal(resources.describe().state, "failed");
  const calls = transport.calls.length;
  await rejectCode(resources.prepare({...item, rate: 2}), "invalid_audio_resource");
  assert.equal(transport.calls.length, calls); assert.equal(calls, 2);
  resources.dispose();
});

test("on-demand cancellation and timeout abort pending clip work and prevent late publication", async () => {
  for (const cause of ["dispose", "timeout"]) {
    const transport = fixture(); let expire, signal;
    const resources = owner(transport, {loadPolicy: "on-demand",
      setTimer(callback, ms) { assert.equal(ms, 60000); expire = callback; return 1; }, clearTimer() {}});
    await resources.load();
    resources.fetch = (_url, options) => { signal = options.signal; return new Promise((_resolve, reject) =>
      signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), {once: true})); };
    const preparing = resources.prepare({...transport.manifest.compositions[0], rate: 2});
    while (!signal) await new Promise(resolve => setImmediate(resolve));
    if (cause === "dispose") resources.dispose(); else expire();
    await rejectCode(preparing, "risk_audio_unavailable");
    assert.equal(signal.aborted, true); assert.equal(resources.describe().loaded_clips, 0);
    assert.equal(resources.describe().state, cause === "dispose" ? "disposed" : "failed");
    resources.dispose();
  }
});
