/* Responsibilities: Verify bounded risk-only PCM reuse and request-scoped cancellation independently of browsers.
 * Implementation: Explicit native-socket events, a virtual monotonic clock and a controllable task queue.
 * Related Modules: ../integration/risk-speech-cache.js; the production SpeechOutput still owns playback validation.
 * Declaration Index: Clock; Clock.constructor; Clock.set; Clock.clear; Clock.advance;
 * NativeSocket; NativeSocket.constructor; NativeSocket.open; NativeSocket.send;
 * NativeSocket.message; NativeSocket.close; payload; fixture; fixture.flush; consumer; complete;
 * testCold; testCancelledReuse; testIndependentCopies; testFirstChunkCancellation;
 * testConcurrency; testEntryBound; testLru; testByteBounds; testInvalidCommands;
 * testAddresses; testBadProtocol; testOrphanFailure; testTimeouts; testTtl;
 * testCancellation; testDispose; testNativeFailure; testOpenGuards; testConfiguration;
 * preparedAudio; settlePrepared; testPreparedComposition; testUncoveredComposition;
 * testPreparedFailure; testPreparedCancelledReuse; testPreparedBoundary; testPreparedBounds;
 * testPreparedConcurrency.
 * Variable Index: ADDRESS/ORIGIN are fixed loopback fixtures; NativeSocket.instances and each Clock's timers
 * retain only per-test transport/deadline observations. No network, provider, browser audio or hardware runs.
 */
import test from "node:test";
import assert from "node:assert/strict";
import {RiskSpeechCache} from "../integration/risk-speech-cache.js";

const ORIGIN = "http://127.0.0.1:8766", ADDRESS = "ws://127.0.0.1:8766/mvp/ws/tts/";

/* Functionality: Control preparation deadlines independently of wall-clock time.
 * Logic: Execute due callbacks in monotonic order. Constraints: No provider-latency or performance claim. */
class Clock {
  /* Functionality: Initialize time/handles. Inputs: None. Outputs: Empty timer fixture.
   * Logic: Use a distinct nonzero origin. Constraints: Time advances only explicitly. */
  constructor() { this.time = 100; this.sequence = 0; this.timers = new Map(); }
  /* Functionality: Register a deadline. Inputs: Callback/delay. Outputs: Handle.
   * Logic: Retain absolute time. Constraints: No real timer or repeated callback. */
  set = (callback, delay) => { const id = ++this.sequence; this.timers.set(id, {at: this.time + delay, callback}); return id; };
  /* Functionality: Remove one timer. Inputs: Handle. Outputs: Deletion result.
   * Logic: Exact map identity. Constraints: No unrelated timer changes. */
  clear = id => this.timers.delete(id);
  /* Functionality: Advance time and due callbacks. Inputs: Nonnegative milliseconds. Outputs: Timer effects.
   * Logic: Recompute the earliest deadline after each callback. Constraints: No backward movement. */
  advance(duration) {
    const target = this.time + duration;
    while (true) {
      const due = [...this.timers.entries()].filter(([, timer]) => timer.at <= target).sort((a, b) => a[1].at - b[1].at)[0];
      if (!due) break;
      this.time = due[1].at; this.timers.delete(due[0]); due[1].callback();
    }
    this.time = target;
  }
}

/* Functionality: Model one independently controlled backend transport.
 * Logic: Tests supply every acknowledgement, PCM and terminal event. Constraints: No actual synthesis or audio device. */
class NativeSocket extends EventTarget {
  static instances = [];
  /* Functionality: Capture native creation. Inputs: Address. Outputs: Connecting socket.
   * Logic: No implicit open event. Constraints: Server frames exist only when a test supplies them. */
  constructor(url) { super(); this.url = url; this.sent = []; this.readyState = 0; this.closeCalls = 0; NativeSocket.instances.push(this); }
  /* Functionality: Admit transport. Inputs: None. Outputs: Open event.
   * Logic: Explicit event. Constraints: Does not synthesize text. */
  open() { this.readyState = 1; this.dispatchEvent(new Event("open")); }
  /* Functionality: Capture a command. Inputs: JSON. Outputs: Original parsed request.
   * Logic: Store only test-local fixture state. Constraints: Not a production content log. */
  send(raw) { this.sent.push(JSON.parse(raw)); }
  /* Functionality: Supply a backend frame. Inputs: Text/binary. Outputs: Message event.
   * Logic: Preserve the supplied payload. Constraints: Late frames may be supplied to test ownership guards. */
  message(data) { const event = new Event("message"); Object.defineProperty(event, "data", {value: data}); this.dispatchEvent(event); }
  /* Functionality: Close transport. Inputs: Native-compatible arguments. Outputs: Close event/counter.
   * Logic: Synchronous closure exercises reentrancy. Constraints: Does not fabricate audio completion. */
  close() { this.readyState = 3; this.closeCalls++; this.dispatchEvent(new Event("close")); }
}

/* Functionality: Build a valid short phrase request. Inputs: Distinct numeric suffix/options.
 * Outputs: Existing backend command. Logic: Canonical lowercase UUIDs. Constraints: Test-only public phrases. */
function payload(index = 1, overrides = {}) {
  return {type: "synthesize", request_id: `00000000-0000-4000-8000-${index.toString(16).padStart(12, "0")}`,
    text: "危险，左前方有行人。", language: "zh-CN", rate: 2, ...overrides};
}

/* Functionality: Construct isolated cache dependencies. Inputs: Optional resource limits/transport.
 * Outputs: Cache, clock, task queue and deterministic drain. Logic: No hidden native work.
 * Constraints: Does not replace the real player's fresh-risk gate or establish audibility. */
function fixture(options = {}) {
  NativeSocket.instances = [];
  const clock = new Clock(), tasks = [];
  const cache = new RiskSpeechCache({WebSocket: NativeSocket, origin: ORIGIN,
    now: () => clock.time, setTimer: clock.set, clearTimer: clock.clear, queueTask: callback => tasks.push(callback), ...options});
  return {cache, clock, tasks,
    /* Functionality: Drain deferred event delivery. Inputs: Task queue. Outputs: Current effects.
     * Logic: Reentrant tasks are drained with a hard guard. Constraints: No wall-clock waiting. */
    flush() { let count = 0; while (tasks.length) { assert.ok(++count < 1000); tasks.shift()(); } }};
}

/* Functionality: Subscribe a client and capture its test-local frames.
 * Inputs: Fixture and command. Outputs: Client/messages/close count.
 * Logic: Send on the documented asynchronous open event. Constraints: No page uploads or real transport. */
function consumer(f, request = payload()) {
  const socket = f.cache.createSocket(ADDRESS), result = {socket, messages: [], closed: 0, opened: 0};
  socket.addEventListener("open", () => { result.opened++; socket.send(JSON.stringify(request)); });
  socket.addEventListener("message", event => result.messages.push(event.data));
  socket.addEventListener("close", () => result.closed++);
  f.flush(); return result;
}

/* Functionality: Complete one independently supplied PCM stream.
 * Inputs: Native fixture and short bytes. Outputs: Backend config/PCM/terminal sequence.
 * Logic: Use the actual command identity. Constraints: Bytes are arbitrary test samples, not speech. */
function complete(socket, bytes = Uint8Array.of(1, 2, 3, 4)) {
  if (socket.readyState === 0) socket.open();
  const request_id = socket.sent[0].request_id;
  socket.message(JSON.stringify({type: "audio_config", request_id, sample_rate: 24000, format: "pcm_s16le_mono"}));
  socket.message(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength));
  socket.message(JSON.stringify({type: "complete", request_id, audio_bytes: bytes.byteLength}));
}

/* Functionality: Verify complete-only retention. Inputs: Config/PCM followed by terminal metadata.
 * Outputs: No early playback payload and one complete reply. Logic: Independent protocol sequencing.
 * Constraints: This verifies data delivery, not real sound. */
test("cold risk audio is withheld until full valid completion", function testCold() {
  const f = fixture(), c = consumer(f), native = NativeSocket.instances[0]; native.open();
  assert.equal(c.opened, 1); assert.equal(native.sent.length, 1);
  const request_id = native.sent[0].request_id;
  native.message(JSON.stringify({type: "audio_config", request_id, sample_rate: 24000, format: "pcm_s16le_mono"}));
  native.message(Uint8Array.of(1, 2).buffer); f.flush(); assert.deepEqual(c.messages, []);
  native.message(JSON.stringify({type: "complete", request_id, audio_bytes: 2})); f.flush();
  assert.equal(c.messages.length, 3); assert.equal(JSON.parse(c.messages[0]).request_id, request_id);
  assert.deepEqual([...new Uint8Array(c.messages[1])], [1, 2]); assert.equal(c.closed, 1);
  assert.equal(f.cache.describe().ready_bytes, 2); assert.equal(f.cache.describe().in_flight, 0);
  assert.equal(f.clock.timers.size, 0); f.cache.dispose();
});

/* Functionality: Verify canceled consumers do not discard useful synthesis.
 * Inputs: Cancellation before first PCM, then a later same phrase with another UUID.
 * Outputs: One native call and current-consumer identities. Logic: Preparation survives only as content.
 * Constraints: No original alert/source identity is replayed. */
test("cancelled risk synthesis becomes reusable audio under a new request UUID", function testCancelledReuse() {
  const f = fixture(), old = consumer(f), native = NativeSocket.instances[0];
  old.socket.close(); f.flush(); assert.equal(native.closeCalls, 0);
  complete(native); f.flush(); assert.deepEqual(old.messages, []);
  const current = consumer(f, payload(2)); assert.equal(NativeSocket.instances.length, 1);
  assert.equal(JSON.parse(current.messages[0]).request_id, payload(2).request_id);
  assert.equal(JSON.parse(current.messages.at(-1)).request_id, payload(2).request_id);
  assert.equal(f.cache.describe().hits, 1); f.cache.dispose();
});

/* Functionality: Protect cached bytes across consumers. Inputs: Shared phrase and deliberate client mutation.
 * Outputs: Independent buffers and unchanged later cache hit. Logic: Never expose the stored byte array.
 * Constraints: This is memory isolation rather than an audio correctness assertion. */
test("each client receives independent PCM copies", function testIndependentCopies() {
  const f = fixture(), first = consumer(f), second = consumer(f, payload(2));
  first.socket.addEventListener("message", event => { if (event.data instanceof ArrayBuffer) new Uint8Array(event.data)[0] = 99; });
  complete(NativeSocket.instances[0]); f.flush();
  assert.equal(NativeSocket.instances.length, 1); assert.equal(new Uint8Array(first.messages[1])[0], 99);
  assert.equal(new Uint8Array(second.messages[1])[0], 1);
  const later = consumer(f, payload(3)); assert.equal(new Uint8Array(later.messages[1])[0], 1); f.cache.dispose();
});

/* Functionality: Exercise a real player's synchronous pre-playback cancellation boundary.
 * Inputs: Two cached chunks and close during the first. Outputs: No second PCM or completion to that consumer.
 * Logic: Recheck client ownership between messages. Constraints: Cache data can still serve another fresh request. */
test("closing during first PCM prevents all subsequent delivery", function testFirstChunkCancellation() {
  const f = fixture(), c = consumer(f), native = NativeSocket.instances[0]; native.open();
  c.socket.addEventListener("message", event => { if (event.data instanceof ArrayBuffer) c.socket.close(); });
  const request_id = native.sent[0].request_id;
  native.message(JSON.stringify({type: "audio_config", request_id, sample_rate: 24000, format: "pcm_s16le_mono"}));
  native.message(Uint8Array.of(1, 2).buffer); native.message(Uint8Array.of(3, 4).buffer);
  native.message(JSON.stringify({type: "complete", request_id})); f.flush();
  assert.equal(c.messages.length, 2); assert.equal(c.closed, 1);
  const next = consumer(f, payload(2)); assert.equal(next.messages.length, 4); f.cache.dispose();
});

/* Functionality: Bound active synthesis and remove unstarted orphan work.
 * Inputs: Distinct simultaneous phrases. Outputs: At most two native sockets and no canceled queued send.
 * Logic: Complete one slot and observe the remaining live queue. Constraints: No provider retry. */
test("two preparations run at most and an abandoned queued phrase never starts", function testConcurrency() {
  const f = fixture(); consumer(f); consumer(f, payload(2, {text: "警告，前方有行人。"}));
  const abandoned = consumer(f, payload(3, {text: "注意右前方的行人。"}));
  consumer(f, payload(4, {text: "危险，右前方有行人。"}));
  assert.equal(NativeSocket.instances.length, 2); assert.equal(f.cache.describe().waiting, 2);
  abandoned.socket.close(); complete(NativeSocket.instances[0]); f.flush();
  assert.equal(NativeSocket.instances.length, 3); assert.equal(f.cache.describe().waiting, 0);
  NativeSocket.instances[2].open(); assert.equal(NativeSocket.instances[2].sent[0].text, payload(4, {text: "危险，右前方有行人。"}).text);
  f.cache.dispose(); assert.equal(f.clock.timers.size, 0);
});

/* Functionality: Reject an overfull live subscription set. Inputs: One-slot cache and two distinct live phrases.
 * Outputs: Safe capacity error without evicting active synthesis. Logic: Explicit bounded admission.
 * Constraints: No silent risk-content substitution. */
test("entry capacity does not evict live preparation", function testEntryBound() {
  const f = fixture({maxEntries: 1}); consumer(f);
  const refused = consumer(f, payload(2, {text: "警告，前方有行人。"}));
  assert.equal(JSON.parse(refused.messages[0]).code, "tts_audio_limit"); assert.equal(NativeSocket.instances.length, 1); f.cache.dispose();
});

/* Functionality: Verify LRU eviction without changing completion TTL.
 * Inputs: Two complete phrases, a hit and a third phrase. Outputs: Least-recent clip evicted.
 * Logic: Follow actual transport-creation count. Constraints: No repeated synthesis while a valid cache entry exists. */
test("ready clips use bounded least-recent eviction", function testLru() {
  const f = fixture({maxEntries: 2}); consumer(f); complete(NativeSocket.instances[0]); f.flush();
  consumer(f, payload(2, {text: "警告，前方有行人。"})); complete(NativeSocket.instances[1]); f.flush();
  consumer(f, payload(3));
  consumer(f, payload(4, {text: "危险，右前方有行人。"})); complete(NativeSocket.instances[2]); f.flush();
  consumer(f, payload(5)); assert.equal(NativeSocket.instances.length, 3);
  consumer(f, payload(6, {text: "警告，前方有行人。"})); assert.equal(NativeSocket.instances.length, 4); f.cache.dispose();
});

/* Functionality: Enforce individual and aggregate PCM bounds.
 * Inputs: Multiple small clips and an oversized stream. Outputs: Eviction or explicit audio-limit error.
 * Logic: Count complete byte lengths rather than estimated duration. Constraints: Partial overflows never become ready clips. */
test("PCM size and aggregate memory are bounded", function testByteBounds() {
  const f = fixture({maxBytes: 6, maxClipBytes: 8}); consumer(f); complete(NativeSocket.instances[0]); f.flush();
  consumer(f, payload(2, {text: "警告，前方有行人。"})); complete(NativeSocket.instances[1]); f.flush();
  assert.equal(f.cache.describe().ready_bytes, 4); assert.equal(f.cache.describe().entries, 1);
  const overflow = consumer(f, payload(3)); complete(NativeSocket.instances[2], new Uint8Array(10)); f.flush();
  assert.equal(JSON.parse(overflow.messages[0]).code, "tts_audio_limit"); assert.equal(f.cache.describe().ready_bytes, 4);
  const cannotFit = fixture({maxBytes: 2}); const c = consumer(cannotFit); complete(NativeSocket.instances[0]); cannotFit.flush();
  assert.equal(JSON.parse(c.messages[0]).code, "tts_audio_limit"); cannotFit.cache.dispose(); f.cache.dispose();
});

/* Functionality: Validate short risk commands before opening native transport.
 * Inputs: Invalid fields, Unicode, UUIDs, languages, rates and extra data. Outputs: Safe local errors.
 * Logic: Use separate fixtures to prevent retry assumptions. Constraints: No raw command data in diagnostics. */
test("malformed or non-risk-sized commands never reach the backend", function testInvalidCommands() {
  const invalid = [{text: ""}, {text: "x".repeat(121)}, {text: "\uD800"}, {language: "auto"}, {rate: true}, {rate: 2.1},
    {rate: 0.4}, {request_id: "not-a-uuid"}, {request_id: "ABCDEF00-0000-4000-8000-000000000001"}, {extra: 1}, {type: "other"}];
  for (const overrides of invalid) {
    const f = fixture(), c = consumer(f, payload(1, overrides));
    assert.equal(JSON.parse(c.messages[0]).code, "invalid_speech_request"); assert.equal(NativeSocket.instances.length, 0); f.cache.dispose();
  }
  for (const raw of ["{", "null", "[]", "x".repeat(2049), new ArrayBuffer(2)]) {
    const f = fixture(), socket = f.cache.createSocket(ADDRESS), messages = [];
    socket.addEventListener("message", event => messages.push(JSON.parse(event.data))); f.flush(); socket.send(raw); f.flush();
    assert.equal(messages[0].code, "invalid_speech_request"); assert.equal(NativeSocket.instances.length, 0); f.cache.dispose();
  }
});

/* Functionality: Keep transport local to the explicit web authority.
 * Inputs: Foreign origins, paths, credentials or parameters. Outputs: Immediate rejection.
 * Logic: Native construction is never reached. Constraints: No URL normalization into an alternative provider. */
test("risk socket admits only the exact same-origin TTS route", function testAddresses() {
  const f = fixture();
  for (const address of ["ws://example.org/mvp/ws/tts/", "wss://127.0.0.1:8766/mvp/ws/tts/",
    ADDRESS + "?x=1", ADDRESS + "#x", "ws://user@127.0.0.1:8766/mvp/ws/tts/", "ws://127.0.0.1:8766/ws/agent/"])
    assert.throws(() => f.cache.createSocket(address));
  assert.equal(NativeSocket.instances.length, 0); f.cache.dispose();
});

/* Functionality: Reject corrupt or foreign backend streams.
 * Inputs: Missing/mutated IDs, audio format errors and inconsistent completion metadata.
 * Outputs: Safe protocol errors without PCM delivery. Logic: Complete-only validation is independently exercised.
 * Constraints: Provider details remain absent from errors. */
test("invalid backend streams never become reusable audio", function testBadProtocol() {
  const cases = [
    native => native.message("{"),
    native => native.message("null"),
    native => native.message(Uint8Array.of(1, 2).buffer),
    native => native.message(JSON.stringify({type: "audio_config", sample_rate: 24000, format: "pcm_s16le_mono"})),
    native => native.message(JSON.stringify({type: "audio_config", request_id: payload(2).request_id, sample_rate: 24000, format: "pcm_s16le_mono"})),
    native => native.message(JSON.stringify({type: "audio_config", request_id: payload().request_id, sample_rate: 22050, format: "pcm_s16le_mono"})),
    native => { native.message(JSON.stringify({type: "audio_config", request_id: payload().request_id, sample_rate: 24000, format: "pcm_s16le_mono"})); native.message(new ArrayBuffer(0)); },
    native => { native.message(JSON.stringify({type: "audio_config", request_id: payload().request_id, sample_rate: 24000, format: "pcm_s16le_mono"})); native.message(Uint8Array.of(1).buffer); },
    native => { complete(native); native.message("late"); },
    native => native.message(JSON.stringify({type: "complete", request_id: payload().request_id, audio_bytes: 0})),
    native => { native.message(JSON.stringify({type: "audio_config", request_id: payload().request_id, sample_rate: 24000, format: "pcm_s16le_mono"})); native.message(Uint8Array.of(1, 2)); native.message(JSON.stringify({type: "complete", request_id: payload().request_id, audio_bytes: 4})); },
    native => native.message(JSON.stringify({type: "error", request_id: payload().request_id, code: "provider_secret", detail: "private"})),
  ];
  for (const [index, send] of cases.entries()) {
    const f = fixture(), c = consumer(f), native = NativeSocket.instances[0]; native.open(); send(native); f.flush();
    if (index === 8) assert.equal(JSON.parse(c.messages.at(-1)).type, "complete");
    else { assert.equal(JSON.parse(c.messages[0]).code, "tts_upstream_protocol"); assert.equal(c.messages.length, 1); }
    assert.doesNotMatch(JSON.stringify(f.cache.describe()), /private|危险|request_id|127\.0\.0\.1/); f.cache.dispose();
  }
});

/* Functionality: Prevent automatic retry of an unobserved background failure.
 * Inputs: Closed client, server error and a later same-phrase consumer. Outputs: Retained error without native retry.
 * Logic: A following explicit request after observed failure may create a new preparation.
 * Constraints: The shared output owner controls user-required recovery, not this cache. */
test("orphan failures are surfaced once before any later explicit retry", function testOrphanFailure() {
  const f = fixture({maxEntries: 1}), first = consumer(f), native = NativeSocket.instances[0]; native.open(); first.socket.close();
  native.message(JSON.stringify({type: "error", request_id: payload().request_id, code: "tts_timeout"})); f.flush();
  assert.equal(f.cache.describe().entries, 1); assert.equal(f.cache.describe().in_flight, 0);
  const unrelated = consumer(f, payload(4, {text: "警告，前方有行人。"}));
  assert.equal(JSON.parse(unrelated.messages[0]).code, "tts_audio_limit");
  assert.equal(NativeSocket.instances.length, 1);
  const next = consumer(f, payload(2)); assert.equal(JSON.parse(next.messages[0]).code, "tts_timeout");
  assert.equal(NativeSocket.instances.length, 1); assert.equal(f.cache.describe().entries, 0);
  consumer(f, payload(3)); assert.equal(NativeSocket.instances.length, 2); f.cache.dispose();
});

/* Functionality: Bound connect, first-audio, idle, queue and total preparation waits.
 * Inputs: Explicit virtual-clock progression. Outputs: One timeout per stuck request and no automatic retry.
 * Logic: Exercise independent deadlines. Constraints: No timer shortening or real-provider latency claim. */
test("all preparation phases have finite deadlines", function testTimeouts() {
  for (const phase of ["connect", "first", "idle", "overall", "queue"]) {
    const f = fixture(), first = consumer(f), native = NativeSocket.instances[0]; let checked = first;
    if (phase !== "connect") native.open();
    if (["idle", "overall"].includes(phase)) {
      native.message(JSON.stringify({type: "audio_config", request_id: payload().request_id, sample_rate: 24000, format: "pcm_s16le_mono"}));
      native.message(Uint8Array.of(1, 2).buffer);
    }
    if (phase === "overall") {
      f.clock.advance(30000); native.message(Uint8Array.of(3, 4).buffer); f.clock.advance(30000);
    } else if (phase === "queue") {
      native.message(JSON.stringify({type: "audio_config", request_id: payload().request_id, sample_rate: 24000, format: "pcm_s16le_mono"}));
      native.message(Uint8Array.of(1, 2).buffer);
      consumer(f, payload(2, {text: "警告，前方有行人。"})); const second = NativeSocket.instances[1]; second.open();
      second.message(JSON.stringify({type: "audio_config", request_id: payload(2).request_id, sample_rate: 24000, format: "pcm_s16le_mono"}));
      second.message(Uint8Array.of(1, 2).buffer);
      checked = consumer(f, payload(3, {text: "危险，右前方有行人。"}));
      f.clock.advance(30000); native.message(Uint8Array.of(3, 4).buffer); second.message(Uint8Array.of(3, 4).buffer);
      f.clock.advance(25000); assert.equal(f.cache.describe().in_flight, 2);
    } else f.clock.advance({connect: 15000, first: 55000, idle: 35000}[phase]);
    f.flush(); assert.equal(JSON.parse(checked.messages[0]).code, "tts_timeout"); f.cache.dispose();
    assert.equal(f.cache.describe().in_flight, 0); assert.equal(f.clock.timers.size, 0);
  }
});

/* Functionality: Expire ready clips by original completion time. Inputs: Cache hit before TTL then later request.
 * Outputs: A fresh preparation only after TTL. Logic: Hits affect LRU but never extend retention.
 * Constraints: Cache lifetime does not change detection/source freshness. */
test("cache hits do not extend the clip TTL", function testTtl() {
  const f = fixture({ttlMs: 3000}); consumer(f); complete(NativeSocket.instances[0]); f.flush();
  f.clock.advance(2999); consumer(f, payload(2)); assert.equal(NativeSocket.instances.length, 1);
  f.clock.advance(1); consumer(f, payload(3)); assert.equal(NativeSocket.instances.length, 2); f.cache.dispose();
});

/* Functionality: Stop all unfinished work while keeping completed public phrases.
 * Inputs: Ready, active, queued and connecting consumers. Outputs: No pending resource or late delivery.
 * Logic: Cancellation suspends queue pumping before native/client cleanup.
 * Constraints: Reuse still requires a new explicit owner request. */
test("explicit cancellation aborts work and consumers but retains completed clips", function testCancellation() {
  const f = fixture(); consumer(f); complete(NativeSocket.instances[0]); f.flush();
  const current = consumer(f, payload(2, {text: "警告，前方有行人。"}));
  consumer(f, payload(3, {text: "危险，右前方有行人。"}));
  consumer(f, payload(4, {text: "注意前方的行人。"}));
  const connecting = f.cache.createSocket(ADDRESS); let opens = 0; connecting.addEventListener("open", () => opens++);
  f.cache.cancelPending("user_stop"); f.flush();
  assert.equal(opens, 0); assert.equal(connecting.readyState, 3); assert.equal(current.socket.readyState, 3);
  assert.equal(f.cache.describe().entries, 1); assert.equal(f.cache.describe().ready_bytes, 4);
  assert.equal(f.cache.describe().in_flight, 0); assert.equal(f.clock.timers.size, 0);
  const count = NativeSocket.instances.length; const hit = consumer(f, payload(5));
  assert.equal(hit.messages.length, 3); assert.equal(NativeSocket.instances.length, count); f.cache.dispose();
});

/* Functionality: Release all cache data on disposal. Inputs: Complete and pending clips.
 * Outputs: Empty disposed cache, no future admission. Logic: Exact cleanup includes connecting clients.
 * Constraints: Late native messages cannot restore memory or output. */
test("dispose releases clips and prevents late or future work", function testDispose() {
  const f = fixture(); consumer(f); complete(NativeSocket.instances[0]); f.flush();
  const c = consumer(f, payload(2, {text: "警告，前方有行人。"})), late = NativeSocket.instances[1];
  const connecting = f.cache.createSocket(ADDRESS); f.cache.dispose(); f.flush(); late.message("late");
  assert.deepEqual(c.messages, []); assert.equal(connecting.readyState, 3);
  assert.equal(f.cache.describe().ready_bytes, 0); assert.equal(f.cache.describe().entries, 0);
  assert.equal(f.cache.describe().subscribers, 0); assert.throws(() => f.cache.createSocket(ADDRESS));
});

/* Functionality: Surface transport setup/send/disconnect failures once.
 * Inputs: Independently failing native fixtures. Outputs: Safe error with no reconnect.
 * Logic: Test constructor, send, error and premature close paths. Constraints: No provider diagnostics in output. */
test("native transport failures do not retry", function testNativeFailure() {
  for (const kind of ["constructor", "send", "error", "close"]) {
    const Transport = kind === "constructor" ? class extends NativeSocket { constructor() { throw new Error("private"); } } : NativeSocket;
    const f = fixture({WebSocket: Transport}), c = consumer(f);
    if (kind !== "constructor") {
      const native = NativeSocket.instances[0];
      if (kind === "send") { native.send = () => { throw new Error("private"); }; native.open(); }
      if (kind === "error") native.dispatchEvent(new Event("error"));
      if (kind === "close") native.close();
    }
    f.flush(); assert.equal(JSON.parse(c.messages[0]).code, "tts_upstream_unavailable"); assert.equal(c.messages.length, 1); f.cache.dispose();
  }
});

/* Functionality: Preserve socket opening and one-command ownership.
 * Inputs: Send before open, duplicate send and close before opening. Outputs: Native-like state/error behavior.
 * Logic: No underlying request before a valid send. Constraints: Closing cannot later reopen. */
test("consumer sockets enforce open and single-command state", function testOpenGuards() {
  const f = fixture(), socket = f.cache.createSocket(ADDRESS); assert.throws(() => socket.send(JSON.stringify(payload())));
  f.flush(); const messages = []; socket.addEventListener("message", event => messages.push(JSON.parse(event.data)));
  socket.send(JSON.stringify(payload())); socket.send(JSON.stringify(payload(2))); f.flush();
  assert.equal(messages[0].code, "invalid_message"); assert.equal(socket.readyState, 3); socket.close();
  assert.throws(() => socket.send(JSON.stringify(payload()))); f.cache.dispose();
  const bounded = fixture({maxEntries: 1});
  for (let index = 0; index < 4; index++) bounded.cache.createSocket(ADDRESS);
  assert.throws(() => bounded.cache.createSocket(ADDRESS)); bounded.cache.dispose(); bounded.flush();
});

/* Functionality: Validate constructor and namespace cache keys.
 * Inputs: Invalid dependencies/limits/origins and language/rate variations. Outputs: Explicit rejection or distinct preparation.
 * Logic: Voice is fixed; UUID alone is reusable but different language/rate/text is not.
 * Constraints: No default remote authority or option substitution. */
test("configuration and phrase namespaces stay explicit", function testConfiguration() {
  for (const options of [{WebSocket: null}, {origin: "https://example.org/"}, {origin: "file:///tmp"},
    {maxEntries: 0}, {maxInFlight: 0}, {maxClipBytes: -1}, {ttlMs: 0}, {queueTask: null}, {setTimer: null},
    {resources: {}}, {resources: {prepare: true}}])
    assert.throws(() => fixture(options));
  const f = fixture(); consumer(f); complete(NativeSocket.instances[0]); f.flush();
  consumer(f, payload(2, {language: "en-US", text: "Danger, pedestrian ahead."}));
  consumer(f, payload(3, {rate: 1})); assert.equal(NativeSocket.instances.length, 3); f.cache.dispose();
});

/* Functionality: Supply an independent complete prepared-PCM fixture.
 * Inputs: Optional byte values. Outputs: Fixed-format composition.
 * Logic: Keep little-endian sample bytes unchanged. Constraints: Fixture bytes are not actual speech. */
function preparedAudio(bytes = [1, 2, 3, 4]) {
  return {sample_rate: 24000, format: "pcm_s16le_mono", chunks: [Uint8Array.from(bytes).buffer], audio_bytes: bytes.length};
}

/* Functionality: Drain a resolved resource Promise followed by the cache's event queue.
 * Inputs: Fixture. Outputs: Current transport effects. Logic: Yield microtasks without elapsed time.
 * Constraints: Does not synthesize, render audio or weaken the production freshness gate. */
async function settlePrepared(f) { await Promise.resolve(); await Promise.resolve(); f.flush(); }

/* Functionality: Serve covered wording entirely from prepared bytes under each consumer's own UUID.
 * Inputs: Exact phrase and independent resource buffer. Outputs: No native connection, private PCM and reuse.
 * Logic: Mutate the original buffer after publication to verify cache ownership.
 * Constraints: Audio device scheduling remains owned by SpeechOutput rather than the cache. */
test("prepared risk composition uses no native TTS and preserves independent consumer identity", async function testPreparedComposition() {
  const resource = preparedAudio(), requests = [];
  const f = fixture({resources: {prepare(request) { requests.push(request); return Promise.resolve(resource); }}});
  const first = consumer(f); assert.equal(first.messages.length, 0);
  await settlePrepared(f);
  assert.equal(NativeSocket.instances.length, 0); assert.equal(requests.length, 1);
  assert.deepEqual(requests[0], payload());
  assert.equal(JSON.parse(first.messages[0]).request_id, payload().request_id);
  assert.deepEqual([...new Uint8Array(first.messages[1])], [1, 2, 3, 4]);
  new Uint8Array(resource.chunks[0])[0] = 99;
  const next = consumer(f, payload(2));
  assert.equal(JSON.parse(next.messages[0]).request_id, payload(2).request_id);
  assert.deepEqual([...new Uint8Array(next.messages[1])], [1, 2, 3, 4]);
  assert.equal(f.cache.describe().prepared_compositions, 1);
  assert.equal(f.cache.describe().native_preparations, 0);
  assert.equal(f.cache.describe().in_flight, 0); assert.equal(f.clock.timers.size, 0);
  f.cache.dispose();
});

/* Functionality: Preserve the explicit existing synthesis path for uncovered wording and rates.
 * Inputs: Resolver's null match result. Outputs: Exactly one native command and ordinary complete PCM.
 * Logic: Only null selects synthesis; the original command is forwarded unchanged.
 * Constraints: This is declared corpus selection, not recovery from a missing or corrupted asset. */
test("uncovered prepared wording uses the existing native path once", async function testUncoveredComposition() {
  const f = fixture({resources: {prepare: () => Promise.resolve(null)}}), request = payload(1, {rate: 1});
  const c = consumer(f, request); assert.equal(NativeSocket.instances.length, 0);
  await settlePrepared(f); assert.equal(NativeSocket.instances.length, 1);
  complete(NativeSocket.instances[0]); f.flush();
  assert.deepEqual(NativeSocket.instances[0].sent, [request]); assert.equal(c.messages.length, 3);
  assert.equal(f.cache.describe().native_preparations, 1);
  assert.equal(f.cache.describe().prepared_compositions, 0); f.cache.dispose();
});

/* Functionality: Refuse resource failures without hidden server synthesis or diagnostic leakage.
 * Inputs: Safe asset failures and a synchronous resolver exception. Outputs: One fixed error each.
 * Logic: Network/unknown errors map to resource unavailability; integrity retains its explicit code.
 * Constraints: No native connection or automatic retry is admitted on the failed entry. */
test("prepared resource failures never fall back to native synthesis", async function testPreparedFailure() {
  for (const kind of ["risk_audio_unavailable", "invalid_audio_resource", "private", "synchronous"]) {
    let calls = 0;
    const f = fixture({resources: {prepare() {
      calls++; const error = Object.assign(new Error("private resource detail"), {code: kind});
      if (kind === "synchronous") throw error;
      return Promise.reject(error);
    }}}), c = consumer(f);
    await settlePrepared(f);
    assert.equal(calls, 1); assert.equal(NativeSocket.instances.length, 0);
    assert.equal(c.messages.length, 1);
    assert.equal(JSON.parse(c.messages[0]).code, kind === "invalid_audio_resource" ? kind : "risk_audio_unavailable");
    assert.doesNotMatch(c.messages[0], /private/);
    assert.equal(f.cache.describe().in_flight, 0); assert.equal(f.clock.timers.size, 0); f.cache.dispose();
  }
});

/* Functionality: Retain useful complete preparation after one consumer becomes stale.
 * Inputs: Closed consumer followed by a resolved resource and a new current consumer.
 * Outputs: No delivery to the old UUID; bytes reused under the new UUID with zero native calls.
 * Logic: Preparation content survives independently of alert identity.
 * Constraints: The new consumer must still pass the actual player's owner/freshness gate. */
test("a cancelled consumer cannot receive prepared PCM but a later request can reuse it", async function testPreparedCancelledReuse() {
  let resolve;
  const f = fixture({resources: {prepare: () => new Promise(done => { resolve = done; })}});
  const stale = consumer(f); stale.socket.close(); f.flush();
  resolve(preparedAudio()); await settlePrepared(f);
  assert.deepEqual(stale.messages, []); assert.equal(f.cache.describe().ready_bytes, 4);
  const current = consumer(f, payload(2));
  assert.equal(JSON.parse(current.messages[0]).request_id, payload(2).request_id);
  assert.equal(current.messages.length, 3); assert.equal(NativeSocket.instances.length, 0); f.cache.dispose();
});

/* Functionality: Prevent late prepared results from restoring canceled or disposed work.
 * Inputs: Pending resources, lifecycle cancellation/timeout and late success or unsupported result.
 * Outputs: No revived cache, native request or audio data.
 * Logic: Preserve entry identity and terminal state across every Promise boundary.
 * Constraints: Resource loader ownership is separate; stopping the cache never resumes playback. */
test("explicit stop disposal and timeout discard late prepared results", async function testPreparedBoundary() {
  for (const boundary of ["stop", "dispose", "timeout"])
    for (const result of [preparedAudio(), null]) {
      let resolve;
      const f = fixture({resources: {prepare: () => new Promise(done => { resolve = done; })}});
      const c = consumer(f);
      if (boundary === "stop") f.cache.cancelPending();
      else if (boundary === "dispose") f.cache.dispose();
      else f.clock.advance(60000);
      f.flush(); const before = c.messages.length;
      resolve(result); await settlePrepared(f);
      assert.equal(c.messages.length, before); assert.equal(NativeSocket.instances.length, 0);
      assert.equal(f.cache.describe().ready_bytes, 0); assert.equal(f.cache.describe().in_flight, 0);
      assert.equal(f.clock.timers.size, 0); f.cache.dispose();
    }
});

/* Functionality: Validate the prepared resource handoff independently of loader correctness.
 * Inputs: Wrong formats, empty/odd/mismatched/oversized payloads and invalid metadata.
 * Outputs: Fixed failure with no cached bytes or native request.
 * Logic: Check actual PCM buffers and total independently from the claimed length.
 * Constraints: Corrupt data is never resampled, truncated, repaired or synthesized again. */
test("prepared PCM handoff enforces exact format and bounded byte totals", async function testPreparedBounds() {
  const cases = [undefined, {}, {...preparedAudio(), sample_rate: 22050}, {...preparedAudio(), format: "mp3"},
    {...preparedAudio(), chunks: []}, {...preparedAudio(), chunks: ["pcm"]}, preparedAudio([]), preparedAudio([1]),
    {...preparedAudio(), audio_bytes: 6}, {...preparedAudio(), audio_bytes: 0}, {...preparedAudio(), audio_bytes: 1024},
    {...preparedAudio(), chunks: Array.from({length: 9}, () => Uint8Array.of(1, 2).buffer), audio_bytes: 18}];
  for (const result of cases) {
    const f = fixture({maxClipBytes: 8, resources: {prepare: () => Promise.resolve(result)}}), c = consumer(f);
    await settlePrepared(f);
    assert.equal(JSON.parse(c.messages[0]).type, "error"); assert.equal(c.messages.length, 1);
    assert.equal(f.cache.describe().ready_bytes, 0); assert.equal(NativeSocket.instances.length, 0); f.cache.dispose();
  }
});

/* Functionality: Bound asynchronous prepared lookups with the same work budget as native synthesis.
 * Inputs: Three distinct phrases and two worker slots. Outputs: Third lookup only after one completes.
 * Logic: Deferred callbacks exercise pump admission and cancellation while other work remains pending.
 * Constraints: Resource fetch concurrency is independently bounded by the prepared loader. */
test("prepared lookups respect the cache concurrency budget", async function testPreparedConcurrency() {
  const pending = [];
  const f = fixture({resources: {prepare: () => new Promise(resolve => pending.push(resolve))}});
  consumer(f); consumer(f, payload(2, {text: "警告，前方有行人。"}));
  consumer(f, payload(3, {text: "危险，右前方有行人。"}));
  assert.equal(pending.length, 2); assert.equal(f.cache.describe().waiting, 1);
  pending[0](preparedAudio()); await settlePrepared(f);
  assert.equal(pending.length, 3); assert.equal(f.cache.describe().in_flight, 2);
  f.cache.cancelPending(); pending[1](preparedAudio()); pending[2](null); await settlePrepared(f);
  assert.equal(NativeSocket.instances.length, 0); assert.equal(f.cache.describe().in_flight, 0); f.cache.dispose();
});
