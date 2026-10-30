/* Responsibilities: Verify real prepared signal resources and fail-closed loader boundaries.
 * Implementation: Fixed WAV fixtures, exact PCM comparison, damaged manifests and controlled fetches.
 * Related Modules: integration/signal-audio-resources.js; assets/signal-audio; signal-alerts.js.
 * Declaration Index: fixture; fixture.fetch; owner.
 * Variable Index: ROOT/ORIGIN/BASE locate the four public fixture clips; tests never open TTS or audio. */
import assert from "node:assert/strict";
import {createHash, webcrypto} from "node:crypto";
import {readFileSync} from "node:fs";
import test from "node:test";
import {SIGNAL_TEXTS} from "../integration/signal-alerts.js";
import {SignalAudioResources} from "../integration/signal-audio-resources.js";

const ROOT = new URL("../assets/signal-audio/", import.meta.url);
const ORIGIN = "http://127.0.0.1:8766", BASE = `${ORIGIN}/mvp/assets/signal-audio/`;

/** Functionality: Serve test-owned copies of real fixed resources. Inputs: optional body/manifest mutation.
 * Outputs: fetch function, recorded calls and an optional barrier. Logic: exact base and AbortSignal.
 * Constraints: no native fetch, synthesis, playback or arbitrary disk path. */
function fixture({changeManifest, changeBody, holdClips = false} = {}) {
  const manifest = JSON.parse(readFileSync(new URL("manifest.json", ROOT), "utf8"));
  changeManifest?.(manifest);
  let release;
  const barrier = holdClips ? new Promise(resolve => { release = resolve; }) : Promise.resolve();
  const state = {manifest, calls: [], active: 0, maximum: 0, release};
  /** Functionality: Return one bounded fixture response. Inputs: exact URL/options. Outputs: Response.
   * Logic: async yield and optional barrier expose concurrent/disposal paths.
   * Constraints: an aborted request never returns resources or switches to network access. */
  state.fetch = async (url, options) => {
    assert.ok(url.startsWith(BASE));
    assert.equal(options.redirect, "error"); assert.equal(options.credentials, "same-origin");
    const path = url.slice(BASE.length);
    assert.ok(path === "manifest.json" || /^(zh-CN|en-US)\/(red|green)\.wav$/.test(path));
    state.calls.push(path); state.active++; state.maximum = Math.max(state.maximum, state.active);
    try {
      await (path === "manifest.json" ? Promise.resolve() : barrier);
      if (options.signal.aborted) throw new DOMException("Fixture request aborted", "AbortError");
      let bytes = path === "manifest.json" ? Buffer.from(JSON.stringify(manifest)) : Buffer.from(readFileSync(new URL(path, ROOT)));
      bytes = changeBody?.(path, bytes) ?? bytes;
      return new Response(bytes, {headers: {"content-length": String(bytes.length)}});
    } finally { state.active--; }
  };
  return state;
}

/** Functionality: Construct the real loader with local dependencies. Inputs: transport and overrides.
 * Outputs: idle owner. Logic: explicit mount/crypto. Constraints: no global dependency mutation. */
function owner(transport, extra = {}) {
  return new SignalAudioResources({fetch: transport.fetch, crypto: webcrypto, origin: ORIGIN,
    baseUrl: BASE, ...extra});
}

test("four exact prepared sentences preserve complete PCM and baked rate without extra requests", async () => {
  const transport = fixture(), resources = owner(transport);
  assert.equal(transport.calls.length, 0);
  await resources.load();
  assert.equal(resources.describe().loaded_clips, 4); assert.equal(resources.describe().state, "ready");
  assert.equal(transport.calls.length, 5); assert.equal(transport.maximum, 4);
  for (const [language, states] of Object.entries(SIGNAL_TEXTS)) for (const [state, text] of Object.entries(states)) {
    const prepared = await resources.prepare({language, rate: 2, text});
    assert.equal(prepared.sample_rate, 24000); assert.equal(prepared.format, "pcm_s16le_mono");
    const expected = readFileSync(new URL(`${language}/${state}.wav`, ROOT)).subarray(44);
    assert.deepEqual(Buffer.from(prepared.chunks[0]), expected); assert.equal(prepared.audio_bytes, expected.length);
    new Uint8Array(prepared.chunks[0]).fill(0);
    assert.deepEqual(Buffer.from((await resources.prepare({language, rate: 2, text})).chunks[0]), expected);
  }
  assert.equal(transport.calls.length, 5); resources.dispose();
});

test("unsupported wording language and rates stay explicitly uncovered without loading", async () => {
  const transport = fixture(), resources = owner(transport);
  for (const command of [{language: "fr", rate: 2, text: SIGNAL_TEXTS["zh-CN"].red},
    {language: "zh-CN", rate: 1, text: SIGNAL_TEXTS["zh-CN"].red},
    {language: "zh-CN", rate: 2, text: "红灯"}, null]) assert.equal(await resources.prepare(command), null);
  assert.equal(transport.calls.length, 0); resources.dispose();
});

test("loading is idempotent and malformed manifest identities cannot become playable", async () => {
  const original = fixture(), resources = owner(original);
  const first = resources.load(); assert.equal(resources.load(), first); await first;
  assert.equal(original.calls.length, 5); resources.dispose();
  for (const mutate of [manifest => {manifest.voice = "Other";}, manifest => {manifest.rate = 1;},
    manifest => {manifest.clips[0].text = "Wrong transcript";}, manifest => {manifest.clips[0].path = "../private.wav";},
    manifest => {manifest.clips[0].pcm_byte_length = 3;}, manifest => {manifest.clips[0].duration_ms += 1;},
    manifest => {manifest.clips[0].sha256 = "invalid";}, manifest => {manifest.extra = true;},
    manifest => {manifest.clips[1] = manifest.clips[0];}]) {
    const transport = fixture({changeManifest: mutate}), invalid = owner(transport);
    await assert.rejects(invalid.load(), {code: "invalid_audio_resource"});
    assert.equal(invalid.describe().state, "failed"); assert.equal(invalid.describe().ready_bytes, 0);
    const attempts = transport.calls.length;
    await assert.rejects(invalid.load(), {code: "invalid_audio_resource"});
    assert.equal(transport.calls.length, attempts); invalid.dispose();
  }
});

test("container and PCM corruption fail independently and never retain a partial pack", async () => {
  for (const change of ["bytes", "wav", "pcm-hash"]) {
    const transport = fixture({changeManifest: manifest => {
      if (change === "pcm-hash") manifest.clips[0].pcm_sha256 = "0".repeat(64);
      if (change === "wav") {
        const bytes = Buffer.from(readFileSync(new URL(manifest.clips[0].path, ROOT)));
        bytes.writeUInt16LE(2, 22);
        manifest.clips[0].sha256 = createHash("sha256").update(bytes).digest("hex");
      }
    }, changeBody: (path, bytes) => {
      if (path !== "zh-CN/red.wav") return bytes;
      if (change === "bytes") bytes[bytes.length - 1] ^= 255;
      if (change === "wav") bytes.writeUInt16LE(2, 22);
      return bytes;
    }}), resources = owner(transport);
    await assert.rejects(resources.load(), {code: "invalid_audio_resource"});
    assert.equal(resources.describe().loaded_clips, 0); assert.equal(resources.describe().ready_bytes, 0);
    resources.dispose();
  }
});

test("stream and configuration boundaries fail without alternate resources", async () => {
  const transport = fixture({changeBody: (path, bytes) => path === "manifest.json" ? Buffer.alloc(16385) : bytes});
  const resources = owner(transport);
  await assert.rejects(resources.load(), {code: "invalid_audio_resource"}); resources.dispose();
  for (const baseUrl of ["https://other.example/assets/signal-audio/", `${BASE}?token=fixture`,
    `${ORIGIN}/private/`, `${BASE}#fragment`]) assert.throws(() => owner(fixture(), {baseUrl}), TypeError);
  const unavailable = owner(fixture(), {crypto: null});
  await assert.rejects(unavailable.load(), {code: "risk_audio_unavailable"}); unavailable.dispose();
});

test("disposal and timeout abort pending clips and prevent late PCM from returning", async () => {
  for (const mode of ["dispose", "timeout"]) {
    const transport = fixture({holdClips: true}); let deadline;
    const resources = owner(transport, {setTimer: callback => {deadline = callback; return 1;}, clearTimer() {}});
    const loading = resources.load();
    while (transport.calls.length < 5) await new Promise(resolve => setImmediate(resolve));
    if (mode === "dispose") resources.dispose(); else deadline();
    transport.release(); await assert.rejects(loading, {code: "risk_audio_unavailable"});
    assert.equal(resources.describe().loaded_clips, 0); assert.equal(resources.describe().ready_bytes, 0);
    assert.equal(resources.describe().state, mode === "dispose" ? "disposed" : "failed");
    await assert.rejects(resources.load(), {code: "risk_audio_unavailable"});
    assert.equal(transport.calls.length, 5); resources.dispose();
  }
});
