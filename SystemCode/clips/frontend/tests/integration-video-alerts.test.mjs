/** Responsibilities: Independently verify concise video risk speech, backend-confirmed fast approach and scheduling boundaries.
 * Implementation: Pure selection/text assertions plus deterministic GuidanceManager/device fixtures without GPU or audio.
 * Related Modules: integration/video-alerts.js, integration/guidance.js, integration/events.js; SystemCode/clips/backend/models/yolo26n.json.
 * Declaration Index: TestClock; TestClock.constructor; TestClock.now; TestClock.setTimer; TestClock.clearTimer;
 * TestClock.advance; TestSpeech; TestSpeech.constructor; TestSpeech.subscribe; TestSpeech.describe;
 * TestSpeech.voiceList; TestSpeech.speak; TestSpeech.start; TestSpeech.end; TestSpeech.stopOwned;
 * alertBox; fixture; fixture.frame; testServerGate; testPriority; testTtcOrdering; testUnknownTtcOrdering;
 * testDistanceTrackTies; testInvalidDistance; testNoMutation; testExactText; testDirections; testMediumDirections;
 * testNumericIndependence; testConfirmedFastApproach; testUnconfirmedMotion; testModelClassContract;
 * testObjectCategories; testUmbrellaCategory; testFutureCategory; testWalkModelCategories;
 * testCriticalUserExample; testMediumUserExample; testMediumFastAttention; testUnknownDirectionMotion;
 * testEnglishVideoSpeech; testVideoInjection; testStartCooldown; testOwnUpgrade; testManualOwnership;
 * testNoAlertSpeech; testStaleFrames; testQueuedSourceExpiry; testStartedNoAlertCompletion; testStartedExpiryCompletion;
 * testSameLowerNoPreemption; testQueuedAbsenceCancellation; testDisableStartedSpeech; testUserCancelStartedSpeech;
 * testDefaultCameraCancellation; testEndCleanup.
 * Variable Index: VOICES is an explicit synthetic local inventory; MODEL_NAMES reads the real model's class contract;
 * CLASS_LABELS_ZH holds reviewed product translations independently of the speech implementation;
 * fixture clocks and requests are isolated.
 */
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import test from "node:test";
import {EventBus} from "../integration/events.js";
import {GuidanceManager} from "../integration/guidance.js";
import {selectVideoAlert, videoAlertText} from "../integration/video-alerts.js";

const VOICES = [{id: "local-zh", language: "zh-CN", local_service: true},
  {id: "local-en", language: "en-US", local_service: true}];
const MODEL_NAMES = Object.freeze(JSON.parse(readFileSync(new URL("../../backend/models/yolo26n.json", import.meta.url), "utf8")).names);
const CLASS_LABELS_ZH = Object.freeze({
  person: "行人", bicycle: "自行车", car: "汽车", motorcycle: "摩托车", airplane: "飞机", bus: "巴士",
  train: "火车", truck: "货车", boat: "船", "traffic light": "交通信号灯", "fire hydrant": "消防栓",
  "stop sign": "停车标志", "parking meter": "停车计时器", bench: "长椅", bird: "鸟", cat: "猫", dog: "狗",
  horse: "马", sheep: "羊", cow: "牛", elephant: "大象", bear: "熊", zebra: "斑马", giraffe: "长颈鹿",
  backpack: "背包", umbrella: "雨伞", handbag: "手提包", tie: "领带", suitcase: "行李箱", frisbee: "飞盘",
  skis: "双板滑雪板", snowboard: "单板滑雪板", "sports ball": "球", kite: "风筝", "baseball bat": "棒球棒",
  "baseball glove": "棒球手套", skateboard: "滑板", surfboard: "冲浪板", "tennis racket": "网球拍",
  bottle: "瓶子", "wine glass": "高脚杯", cup: "杯子", fork: "叉子", knife: "刀", spoon: "勺子", bowl: "碗",
  banana: "香蕉", apple: "苹果", sandwich: "三明治", orange: "橙子", broccoli: "西兰花", carrot: "胡萝卜",
  "hot dog": "热狗", pizza: "披萨", donut: "甜甜圈", cake: "蛋糕", chair: "椅子", couch: "沙发",
  "potted plant": "盆栽", bed: "床", "dining table": "餐桌", toilet: "马桶", tv: "电视", laptop: "笔记本电脑",
  mouse: "鼠标", remote: "遥控器", keyboard: "键盘", "cell phone": "手机", microwave: "微波炉", oven: "烤箱",
  toaster: "烤面包机", sink: "水槽", refrigerator: "冰箱", book: "书", clock: "时钟", vase: "花瓶",
  scissors: "剪刀", "teddy bear": "泰迪熊玩偶", "hair drier": "吹风机", toothbrush: "牙刷",
});

/** Functionality: Model monotonic scheduling. Logic: execute due callbacks in chronological order.
 * Constraints: no real waiting or browser timer performance claims. */
class TestClock {
  /** Functionality: Initialize deterministic time. Inputs: None. Outputs: isolated timer state.
   * Logic: start above zero; Constraints: time advances only explicitly. */
  constructor() { this.time = 100; this.sequence = 0; this.timers = new Map(); }
  /** Functionality: Read current time. Inputs: state. Outputs: milliseconds.
   * Logic: direct lookup; Constraints: no wall-clock dependency. */
  now = () => this.time;
  /** Functionality: Schedule callback. Inputs: callback/delay. Outputs: timer ID.
   * Logic: store absolute deadline; Constraints: no interval or retry. */
  setTimer = (callback, delay) => {
    const id = ++this.sequence; this.timers.set(id, {callback, at: this.time + delay}); return id;
  };
  /** Functionality: Cancel callback. Inputs: ID. Outputs: deletion result.
   * Logic: remove matching entry; Constraints: missing IDs are harmless. */
  clearTimer = id => this.timers.delete(id);
  /** Functionality: Advance simulated time. Inputs: target. Outputs: timer effects and new time.
   * Logic: remove due callbacks before executing; Constraints: cannot travel backward. */
  advance(target) {
    assert.ok(target >= this.time);
    while (true) {
      const due = [...this.timers.entries()].filter(([, timer]) => timer.at <= target)
        .sort((a, b) => a[1].at - b[1].at)[0];
      if (!due) break;
      this.timers.delete(due[0]); this.time = due[1].at; due[1].callback();
    }
    this.time = target;
  }
}

/** Functionality: Expose native-style speech lifecycle independently of manager decisions.
 * Logic: accepted requests remain queued until explicit start/end; Constraints: no real voice synthesis. */
class TestSpeech {
  /** Functionality: Create enabled local output fixture. Inputs: clock. Outputs: isolated driver state.
   * Logic: requests and cancellations are observable; Constraints: manager remains explicitly enabled separately. */
  constructor(clock) {
    Object.assign(this, {clock, events: new EventBus(), current: null, sequence: 0, requests: [], cancels: []});
  }
  /** Functionality: Register observer. Inputs: listener. Outputs: unsubscribe.
   * Logic: local EventBus; Constraints: no implicit start events. */
  subscribe(listener) { return this.events.subscribe(listener); }
  /** Functionality: Return output admission state. Inputs: current request. Outputs: controller contract.
   * Logic: queued manual requests are busy; Constraints: support is synthetic. */
  describe() { return {supported: true, enabled: true, active_request: this.current !== null,
    last_request: this.current ? {id: this.current.id} : null}; }
  /** Functionality: Return matching local voices. Inputs: None. Outputs: explicit inventory.
   * Logic: copied array; Constraints: no remote or default voice substitution. */
  voiceList() { return {accepted: true, voices: [...VOICES]}; }
  /** Functionality: Accept one speech request. Inputs: text/options. Outputs: acknowledgement.
   * Logic: synchronous submitted event precedes deferred start; Constraints: busy requests fail the test. */
  speak(text, options = {}) {
    assert.equal(this.current, null);
    this.current = {id: ++this.sequence, text, options}; this.requests.push(this.current);
    this.events.publish({channel: "speech", phase: "submitted", request_id: this.current.id, at: this.clock.now()});
    return {accepted: true, last_request: {id: this.current.id}};
  }
  /** Functionality: Observe simulated engine start. Inputs: queued request. Outputs: start event.
   * Logic: uses current monotonic time; Constraints: not proof of audible sound. */
  start() {
    this.events.publish({channel: "speech", phase: "start", request_id: this.current.id, at: this.clock.now()});
  }
  /** Functionality: Complete current request. Inputs: active request. Outputs: free driver/end event.
   * Logic: clear busy state before notifying manager; Constraints: no automatic completion timer. */
  end() {
    const id = this.current.id; this.current = null;
    this.events.publish({channel: "speech", phase: "end", request_id: id, at: this.clock.now()});
  }
  /** Functionality: Cancel only the requested owner. Inputs: request ID. Outputs: acknowledgement/event.
   * Logic: refuse foreign identity; Constraints: manager cannot stop manual requests. */
  stopOwned(id) {
    if (this.current?.id !== id) return {accepted: false, reason: "not_owned"};
    this.cancels.push(id); this.current = null;
    this.events.publish({channel: "speech", phase: "cancel", request_id: id, at: this.clock.now()});
    return {accepted: true};
  }
}

/** Functionality: Build canonical backend-required alert. Inputs: overrides. Outputs: independent object.
 * Logic: keep server fields explicit; Constraints: no risk/TTC calculation or threshold duplication. */
function alertBox(overrides = {}) {
  return {label: "person", track_id: 1, alert_required: true, alert_priority: 3,
    risk_level: "high", direction: "front", distance_m: 2, ttc_sec: 2,
    motion_state: "approaching", relative_speed_mps: -2.5,
    risk_reasons: ["approaching", "closing_mps_ge_2", "distance_near"], ...overrides};
}

/** Functionality: Wire video speech into the existing manager. Inputs: video strategy toggle/language.
 * Outputs: manager, driver, clock, events and frame helper. Logic: policies are injected only when explicitly selected.
 * Constraints: opt-in completion applies only to video fixtures; no navigation, GPU or real output invocation. */
function fixture(video = true, language = "zh-CN") {
  const clock = new TestClock(), speech = new TestSpeech(clock), events = [];
  const manager = new GuidanceManager({speech, now: clock.now, setTimer: clock.setTimer,
    clearTimer: clock.clearTimer, ...(video ? {selectAlert: selectVideoAlert, alertText: videoAlertText,
      finishStartedRiskSpeech: true} : {})});
  let index = 0;
  manager.subscribe(event => events.push(event));
  manager.setVoice(language === "zh-CN" ? "local-zh" : "local-en", language);
  manager.begin("video-session"); manager.setEnabled(true);
  /** Functionality: Ingest one source frame. Inputs: boxes/capture time/session.
   * Outputs: scheduling effects. Logic: monotonically number independent snapshots; Constraints: boxes represent validated results. */
  function frame(boxes = [alertBox()], capturedAt = clock.now(), sessionId = "video-session") {
    manager.ingest({sessionId, capturedAt, metadata: {index: index++, tracking_epoch: 0, boxes}});
  }
  return {clock, speech, manager, events, frame};
}

/** Functionality: Verify server alert admission. Inputs: false/missing flags and nonpositive priority.
 * Outputs: only explicitly admitted alert or null. Logic: TTC cannot manufacture risk.
 * Constraints: no algorithm threshold changes. */
test("video selection requires backend alert_required and positive priority", function testServerGate() {
  const ignored = [alertBox({alert_required: false, ttc_sec: 0.01}),
    alertBox({alert_required: undefined}), alertBox({alert_required: 1}),
    alertBox({alert_priority: 0}), alertBox({alert_priority: -1})];
  assert.equal(selectVideoAlert(ignored), null);
  const admitted = alertBox({track_id: 8, alert_priority: 2, risk_level: "medium", ttc_sec: null});
  assert.equal(selectVideoAlert([...ignored, admitted]), admitted);
});

/** Functionality: Preserve server priority ahead of TTC. Inputs: increasing levels with longer/unknown TTC.
 * Outputs: critical target. Logic: server rank remains dominant; Constraints: TTC does not reclassify alerts. */
test("server alert priority dominates TTC and distance", function testPriority() {
  const critical = alertBox({track_id: 3, alert_priority: 4, risk_level: "critical", ttc_sec: null, distance_m: 20});
  assert.equal(selectVideoAlert([alertBox({alert_priority: 2, ttc_sec: 0.01, distance_m: 0.1}),
    alertBox({ttc_sec: 0.1}), critical]), critical);
});

/** Functionality: Verify same-level TTC selection. Inputs: two valid TTCs and a nearer unknown.
 * Outputs: shortest finite positive TTC. Logic: TTC breaks only priority ties; Constraints: no urgency threshold. */
test("equal-level video alerts choose the shortest known positive TTC", function testTtcOrdering() {
  const shortest = alertBox({track_id: 2, ttc_sec: 0.01, distance_m: 10});
  assert.equal(selectVideoAlert([alertBox({ttc_sec: null, distance_m: 0.1}),
    alertBox({track_id: 3, ttc_sec: 1, distance_m: 1}), shortest]), shortest);
});

/** Functionality: Treat invalid TTCs as unknown. Inputs: null/nonfinite/nonpositive/nonnumber values.
 * Outputs: known TTC wins, otherwise distance wins. Logic: reject coercion; Constraints: zero does not mean immediate collision. */
test("invalid TTC values are unknown rather than coerced or treated as imminent", function testUnknownTtcOrdering() {
  for (const ttc of [null, undefined, 0, -1, NaN, Infinity, -Infinity, "0.01", true, {}, []]) {
    const unknown = alertBox({ttc_sec: ttc, distance_m: 0.1});
    const known = alertBox({track_id: 2, ttc_sec: 5, distance_m: 10});
    assert.equal(selectVideoAlert([unknown, known]), known);
    assert.equal(selectVideoAlert([alertBox({track_id: 3, ttc_sec: null, distance_m: 2}), unknown]), unknown);
  }
});

/** Functionality: Verify deterministic remaining ties. Inputs: equal priority/TTC with distance and track variations.
 * Outputs: closest distance then smallest ID. Logic: preserve existing final ordering; Constraints: no arrival-order dependence. */
test("video alert TTC ties use distance then track ID", function testDistanceTrackTies() {
  const winner = alertBox({track_id: 2, distance_m: 1});
  assert.equal(selectVideoAlert([alertBox({track_id: 1, distance_m: 3}),
    alertBox({track_id: 9, distance_m: 1}), winner]), winner);
  const unknownWinner = alertBox({track_id: 2, ttc_sec: null, distance_m: 1});
  assert.equal(selectVideoAlert([alertBox({track_id: 9, ttc_sec: 0, distance_m: 1}), unknownWinner]), unknownWinner);
});

/** Functionality: Reject invalid distance as a tie-breaker. Inputs: nonfinite/nonpositive/nonnumber distances.
 * Outputs: known positive distance wins equal TTC ties. Logic: retain numeric validation without coercion.
 * Constraints: no fabricated replacement distance. */
test("unknown or invalid distance cannot outrank a known positive distance", function testInvalidDistance() {
  for (const distance_m of [null, undefined, 0, -1, NaN, Infinity, -Infinity, "0.1", true, {}, []]) {
    const known = alertBox({track_id: 9, distance_m: 10});
    assert.equal(selectVideoAlert([alertBox({distance_m}), known]), known);
  }
});

/** Functionality: Protect algorithm output identity/content. Inputs: frozen unsorted boxes.
 * Outputs: selected original object with unchanged array. Logic: detect in-place sorting or field writes.
 * Constraints: this adapter has no authority to mutate inference metadata. */
test("video selection does not mutate backend boxes or their array", function testNoMutation() {
  const first = Object.freeze(alertBox({ttc_sec: 3})), winner = Object.freeze(alertBox({track_id: 2, ttc_sec: 1}));
  const boxes = Object.freeze([first, winner]);
  assert.equal(selectVideoAlert(boxes), winner); assert.deepEqual(boxes, [first, winner]);
});

/** Functionality: Lock bilingual level-specific short speech. Inputs: existing medium/high/critical alerts.
 * Outputs: concise location/category speech with backend-confirmed fast approach at every grade.
 * Logic: consume server grades without adding numeric narration; Constraints: no new risk classification or safe-road promise. */
test("video alerts render exact short Chinese and English level speech", function testExactText() {
  const cases = [
    ["medium", 2, "注意，前方有行人靠近。", "Caution. Person ahead is approaching."],
    ["high", 3, "警告，前方有行人靠近。", "Warning. Person ahead is approaching."],
    ["critical", 4, "危险，前方有行人靠近。", "Danger. Person ahead is approaching."],
  ];
  for (const [risk_level, alert_priority, zh, en] of cases) {
    const box = alertBox({risk_level, alert_priority});
    assert.equal(videoAlertText(box, "zh-CN"), zh); assert.equal(videoAlertText(box, "en-US"), en);
  }
});

/** Functionality: Preserve camera-relative direction in confirmed fast approach warnings.
 * Inputs: seven canonical directions. Outputs: bilingual high-level direction and approach clauses.
 * Logic: consume backend direction and motion fields; Constraints: never invent a safe direction or global navigation bearing. */
test("high video speech preserves all directions and confirmed approach", function testDirections() {
  for (const [direction, zh, en] of [["front", "前方有行人靠近。", "Person ahead is approaching."],
    ["front_left", "左前方有行人靠近。", "Person ahead left is approaching."],
    ["front_right", "右前方有行人靠近。", "Person ahead right is approaching."],
    ["left", "左侧有行人靠近。", "Person to the left is approaching."],
    ["right", "右侧有行人靠近。", "Person to the right is approaching."],
    ["behind", "后方有行人靠近。", "Person behind is approaching."],
    ["unknown", "有行人靠近，方向未知。", "Person in an unknown direction is approaching."]]) {
    assert.equal(videoAlertText(alertBox({direction}), "zh-CN"), "警告，" + zh);
    assert.equal(videoAlertText(alertBox({direction}), "en-US"), "Warning. " + en);
  }
});

/** Functionality: Keep medium slow-approach alerts to a short attention instruction in every direction.
 * Inputs: canonical directions with approaching state but no backend fast marker. Outputs: no approach suffix or numeric clauses.
 * Logic: the existing grade is retained; Constraints: only the backend fast conclusion can add approach wording. */
test("medium slow-approach speech remains one attention sentence in all directions", function testMediumDirections() {
  for (const [direction, zh, en] of [["front", "注意前方的行人。", "Person ahead."],
    ["front_left", "注意左前方的行人。", "Person ahead left."],
    ["front_right", "注意右前方的行人。", "Person ahead right."],
    ["left", "注意左侧的行人。", "Person to the left."],
    ["right", "注意右侧的行人。", "Person to the right."],
    ["behind", "注意后方的行人。", "Person behind."],
    ["unknown", "注意行人，方向未知。", "Person in an unknown direction."]]) {
    const box = alertBox({direction, alert_priority: 2, risk_level: "medium",
      relative_speed_mps: -0.5, risk_reasons: ["approaching"]});
    assert.equal(videoAlertText(box, "zh-CN"), zh);
    assert.equal(videoAlertText(box, "en-US"), "Caution. " + en);
  }
});

/** Functionality: Separate numeric selection metadata from concise spoken output.
 * Inputs: all spoken grades with varying known/missing/extreme distance/TTC and backend-confirmed fast negative speeds.
 * Outputs: identical grade-specific bilingual short speech. Logic: numeric details stay in results and target ordering.
 * Constraints: no numbers, unknown-distance/time notices, speed narration or additional risk thresholds in speech. */
test("distance TTC and confirmed speed magnitude never add numeric narration to any grade", function testNumericIndependence() {
  const values = [0.001, 0.1, 2.3, 1000000, null, undefined, 0, -1, NaN, Infinity, "2"];
  for (const [risk_level, alert_priority, zh, en] of [
    ["medium", 2, "注意，前方有行人靠近。", "Caution. Person ahead is approaching."],
    ["high", 3, "警告，前方有行人靠近。", "Warning. Person ahead is approaching."],
    ["critical", 4, "危险，前方有行人靠近。", "Danger. Person ahead is approaching."],
  ]) {
    for (const value of values) {
      for (const relative_speed_mps of [-2.5, -10, -1000]) {
        const box = alertBox({risk_level, alert_priority, distance_m: value, ttc_sec: value, relative_speed_mps});
        assert.equal(videoAlertText(box, "zh-CN"), zh); assert.equal(videoAlertText(box, "en-US"), en);
      }
    }
  }
});

/** Functionality: Consume the backend's fast approach conclusion without copying its numeric threshold.
 * Inputs: canonical approach state, finite negative speed and both approach/fast reason markers.
 * Outputs: approach wording at all server grades. Logic: all required server conclusions agree.
 * Constraints: speech adapter neither recalculates motion nor changes grade or numeric thresholds. */
test("fast approach uses state negative speed and both backend reason markers", function testConfirmedFastApproach() {
  for (const relative_speed_mps of [-2.5, -10, -1000]) {
    for (const [risk_level, alert_priority, zh, en] of [
      ["medium", 2, "注意，前方有行人靠近。", "Caution. Person ahead is approaching."],
      ["high", 3, "警告，前方有行人靠近。", "Warning. Person ahead is approaching."],
      ["critical", 4, "危险，前方有行人靠近。", "Danger. Person ahead is approaching."],
    ]) {
      const box = alertBox({risk_level, alert_priority, relative_speed_mps,
        risk_reasons: ["distance_near", "approaching", "closing_mps_ge_2"]});
      assert.equal(videoAlertText(box, "zh-CN"), zh); assert.equal(videoAlertText(box, "en-US"), en);
    }
  }
});

/** Functionality: Avoid fast-approach claims from slow, unknown, stable, receding or inconsistent metadata.
 * Inputs: canonical alternative motion states plus hysteresis/mismatched state, speed or reasons.
 * Outputs: neutral warnings at every retained backend grade.
 * Logic: approach wording requires the complete backend fast conclusion; Constraints: no guessed motion or grade downgrade. */
test("slow unknown stationary receding hysteresis and inconsistent motion never claim fast approach", function testUnconfirmedMotion() {
  const cases = [
    {motion_state: "unknown", relative_speed_mps: null, risk_reasons: ["motion_unknown"]},
    {motion_state: "stationary", relative_speed_mps: 0, risk_reasons: ["stationary", "distance_near"]},
    {motion_state: "moving_away", relative_speed_mps: 1, risk_reasons: ["moving_away", "distance_near"]},
    {motion_state: "approaching", relative_speed_mps: -0.1, risk_reasons: ["distance_near"]},
    {motion_state: "approaching", relative_speed_mps: -1.5, risk_reasons: ["approaching", "closing_mps_ge_1"]},
    {motion_state: "approaching", relative_speed_mps: -2.5, risk_reasons: ["approaching"]},
    {motion_state: "approaching", relative_speed_mps: -2.5, risk_reasons: ["approaching", "closing_mps_ge_20"]},
    {motion_state: "approaching", relative_speed_mps: -2.5, risk_reasons: ["closing_mps_ge_2"]},
    {motion_state: "approaching", relative_speed_mps: -2.5, risk_reasons: []},
    {motion_state: "unknown", relative_speed_mps: -2.5, risk_reasons: ["approaching", "closing_mps_ge_2"]},
    {motion_state: "stationary", relative_speed_mps: -2.5, risk_reasons: ["approaching", "closing_mps_ge_2"]},
    {motion_state: "moving_away", relative_speed_mps: -2.5, risk_reasons: ["approaching", "closing_mps_ge_2"]},
    ...[null, undefined, 0, 1, NaN, Infinity, -Infinity, "-1", true].map(relative_speed_mps =>
      ({motion_state: "approaching", relative_speed_mps, risk_reasons: ["approaching", "closing_mps_ge_2"]})),
  ];
  for (const motion of cases) {
    for (const [risk_level, alert_priority, zh, en] of [
      ["medium", 2, "注意前方的行人。", "Caution. Person ahead."],
      ["high", 3, "警告，前方有行人。", "Warning. Person ahead."],
      ["critical", 4, "危险，前方有行人。", "Danger. Person ahead."],
    ]) {
      const box = alertBox({...motion, risk_level, alert_priority, ttc_sec: null});
      assert.equal(videoAlertText(box, "zh-CN"), zh); assert.equal(videoAlertText(box, "en-US"), en);
      assert.equal(box.risk_level, risk_level); assert.equal(box.alert_priority, alert_priority);
    }
  }
});

/** Functionality: Bind reviewed translations to the real model's complete category vocabulary.
 * Inputs: saved model manifest and independent expected names. Outputs: equal sets of80 unique canonical labels.
 * Logic: detect model additions, omissions or invented labels; Constraints: no inference, label remapping or model mutation. */
test("speech category contract covers exactly all eighty labels in the real model manifest", function testModelClassContract() {
  assert.equal(MODEL_NAMES.length, 80); assert.equal(new Set(MODEL_NAMES).size, 80);
  assert.equal(Object.keys(CLASS_LABELS_ZH).length, 80);
  assert.deepEqual(Object.keys(CLASS_LABELS_ZH).sort(), [...MODEL_NAMES].sort());
});

/** Functionality: Speak every existing model class in concise alerts.
 * Inputs: all80 actual backend manifest labels with confirmed approach. Outputs: reviewed Chinese classes and original English labels.
 * Logic: verify localization independently of the policy dictionary; Constraints: no generic substitution for a current class. */
test("short video speech names all eighty canonical backend object classes", function testObjectCategories() {
  for (const label of MODEL_NAMES) {
    const spoken = CLASS_LABELS_ZH[label];
    const box = alertBox({label}), english = label[0].toUpperCase() + label.slice(1);
    assert.equal(videoAlertText(box, "zh-CN"), "警告，前方有" + spoken + "靠近。");
    assert.equal(videoAlertText(box, "en-US"), "Warning. " + english + " ahead is approaching.");
  }
});

/** Functionality: Verify a previously unmapped current category now has its own spoken name.
 * Inputs: frozen canonical umbrella label at left. Outputs: rain-umbrella wording and original English label.
 * Logic: consume an existing detector class; Constraints: no inference change or metadata mutation. */
test("canonical umbrella speech names the detected umbrella instead of a generic object", function testUmbrellaCategory() {
  const box = Object.freeze(alertBox({label: "umbrella", direction: "left", distance_m: 4, ttc_sec: 3}));
  assert.equal(videoAlertText(box, "zh-CN"), "警告，左侧有雨伞靠近。");
  assert.equal(videoAlertText(box, "en-US"), "Warning. Umbrella to the left is approaching.");
  assert.equal(box.label, "umbrella"); assert.equal(box.direction, "left");
});

/** Functionality: Keep future unmapped-class handling explicit and generic.
 * Inputs: deliberately noncurrent model label. Outputs: Chinese object wording and preserved English label.
 * Logic: distinguish defensive future behavior from the current80-class contract; Constraints: no invented class translation. */
test("a deliberately noncurrent future class uses explicit generic object speech", function testFutureCategory() {
  const label = "future_class_stub";
  assert.equal(MODEL_NAMES.includes(label), false);
  const box = alertBox({label, direction: "left"});
  assert.equal(videoAlertText(box, "zh-CN"), "警告，左侧有物体靠近。");
  assert.equal(videoAlertText(box, "en-US"), "Warning. Future_class_stub to the left is approaching.");
});

/** Functionality: Lock the requested approaching-person critical example.
 * Inputs: confirmed left-front person and server critical grade. Outputs: exact bilingual warning.
 * Logic: use existing direction/category/motion conclusion; Constraints: no distance, timing or extra action. */
test("critical speech matches the requested approaching left-front person example", function testCriticalUserExample() {
  const box = alertBox({direction: "front_left", alert_priority: 4, risk_level: "critical"});
  assert.equal(videoAlertText(box, "zh-CN"), "危险，左前方有行人靠近。");
  assert.equal(videoAlertText(box, "en-US"), "Danger. Person ahead left is approaching.");
});

/** Functionality: Lock the requested slow-approach medium example. Inputs: right-front person and server medium grade.
 * Outputs: exact concise bilingual warning. Logic: no fast marker means no approach wording.
 * Constraints: use server grade rather than changing it from speed. */
test("medium speech matches the requested right-front person attention example", function testMediumUserExample() {
  const box = alertBox({direction: "front_right", alert_priority: 2, risk_level: "medium",
    relative_speed_mps: -0.5, risk_reasons: ["approaching"]});
  assert.equal(videoAlertText(box, "zh-CN"), "注意右前方的行人。");
  assert.equal(videoAlertText(box, "en-US"), "Caution. Person ahead right.");
});

/** Functionality: Add confirmed fast approach to medium attention while retaining its grade and location grammar.
 * Inputs: right-front or unknown direction with the backend fast marker.
 * Outputs: concise medium approach sentences, including explicit unknown bearing.
 * Logic: wording consumes the server marker; Constraints: no grade promotion, numeric narration or extra action. */
test("medium confirmed fast approach uses concise right-front and unknown-direction attention", function testMediumFastAttention() {
  for (const [direction, zh, en] of [
    ["front_right", "注意，右前方有行人靠近。", "Caution. Person ahead right is approaching."],
    ["unknown", "注意，有行人靠近，方向未知。", "Caution. Person in an unknown direction is approaching."],
  ]) {
    const box = alertBox({direction, alert_priority: 2, risk_level: "medium"});
    assert.equal(videoAlertText(box, "zh-CN"), zh); assert.equal(videoAlertText(box, "en-US"), en);
    assert.equal(box.risk_level, "medium"); assert.equal(box.alert_priority, 2);
  }
});

/** Functionality: Keep unknown bearing explicit for confirmed and unconfirmed critical motion.
 * Inputs: unknown direction with a matching approach conclusion or canonical stationary conclusion.
 * Outputs: short critical wording that never invents direction or approach.
 * Logic: independently preserve backend direction and confirmed motion; Constraints: no numeric or action suffix. */
test("critical unknown direction remains explicit without inventing approach", function testUnknownDirectionMotion() {
  const approaching = alertBox({direction: "unknown", alert_priority: 4, risk_level: "critical"});
  assert.equal(videoAlertText(approaching, "zh-CN"), "危险，有行人靠近，方向未知。");
  assert.equal(videoAlertText(approaching, "en-US"), "Danger. Person in an unknown direction is approaching.");
  const stationary = alertBox({direction: "unknown", alert_priority: 4, risk_level: "critical",
    motion_state: "stationary", relative_speed_mps: 0, risk_reasons: ["stationary"], ttc_sec: null});
  assert.equal(videoAlertText(stationary, "zh-CN"), "危险，有行人，方向未知。");
  assert.equal(videoAlertText(stationary, "en-US"), "Danger. Person in an unknown direction.");
});

/** Functionality: Exercise concise English motion speech through the real manager policy seam.
 * Inputs: server critical bus behind with confirmed approach and explicit local English voice.
 * Outputs: one short native-driver request and matching voice selection.
 * Logic: manager consumes the selected backend object; Constraints: fixture acceptance does not verify audible output. */
test("English video guidance submits short confirmed approach speech", function testEnglishVideoSpeech() {
  const f = fixture(true, "en-US");
  f.frame([alertBox({label: "bus", direction: "behind", distance_m: 7.5, ttc_sec: 3.2,
    alert_priority: 4, risk_level: "critical"})]);
  assert.equal(f.speech.requests.length, 1); assert.equal(f.speech.current.text, "Danger. Bus behind is approaching.");
  assert.equal(f.speech.current.options.language, "en-US");
  assert.equal(f.speech.current.options.voice_id, "local-en"); f.manager.dispose();
});

/** Functionality: Verify actual video policy injection without changing default guidance.
 * Inputs: same-level nearer/longer TTC and farther/shorter TTC boxes. Outputs: different selected IDs and video wording.
 * Logic: defaults retain distance ordering; Constraints: no navigation strategy changes. */
test("only explicitly injected video guidance chooses TTC and speaks the video template", function testVideoInjection() {
  const video = fixture(), original = fixture(false);
  const boxes = [alertBox({track_id: 1, distance_m: 1, ttc_sec: 5}),
    alertBox({track_id: 2, distance_m: 8, ttc_sec: 1})];
  video.frame(boxes); original.frame(boxes);
  assert.equal(video.manager.active.speech.box.track_id, 2);
  assert.equal(video.speech.current.text, "警告，前方有行人靠近。");
  assert.equal(original.manager.active.speech.box.track_id, 1);
  assert.equal(original.speech.current.text, "前方存在高风险目标，距离约1.0米。请停下确认周围情况。");
  video.manager.dispose(); original.manager.dispose();
});

/** Functionality: Retain existing start-based cooldown with changing TTC. Inputs: same-track fresh frames and deferred engine start.
 * Outputs: no same-level interruption and next request exactly3s after actual start with unchanged short text.
 * Logic: shorter TTC changes selection only; Constraints: no new urgency cutoff or cooldown. */
test("shorter TTC does not bypass same-level start-based three-second cooldown", function testStartCooldown() {
  const f = fixture(); f.frame(); f.clock.advance(200); f.speech.start();
  f.clock.advance(250); f.frame([alertBox({ttc_sec: 0.1})]);
  assert.equal(f.speech.current.id, 1); assert.deepEqual(f.speech.cancels, []);
  f.speech.end(); assert.equal(f.manager.history.get(1).at, 200);
  f.clock.advance(3199); f.frame([alertBox({ttc_sec: 1})]); assert.equal(f.speech.requests.length, 1);
  f.clock.advance(3200); f.frame([alertBox({ttc_sec: 0.5})]); assert.equal(f.speech.requests.length, 2);
  assert.equal(f.speech.current.text, "警告，前方有行人靠近。");
  f.manager.dispose();
});

/** Functionality: Preserve immediate server-level escalation. Inputs: owned active high then critical.
 * Outputs: owned cancellation and critical request. Logic: rank increase bypasses cooldown; Constraints: TTC does not alter rank. */
test("server level escalation interrupts only the manager-owned video alert", function testOwnUpgrade() {
  const f = fixture(); f.frame(); f.speech.start(); f.clock.advance(200);
  f.frame([alertBox({alert_priority: 4, risk_level: "critical", ttc_sec: 3})]);
  assert.deepEqual(f.speech.cancels, [1]); assert.equal(f.speech.current.id, 2);
  assert.equal(f.speech.current.text, "危险，前方有行人靠近。");
  assert.equal(f.manager.active.speech.box.alert_priority, 4); f.manager.dispose();
});

/** Functionality: Protect independently requested speech. Inputs: manual request then increasing video alerts and EOF.
 * Outputs: manual request remains active and uncancelled. Logic: existing controller ownership gate remains authoritative.
 * Constraints: no risk, stop or source expiry may cancel another owner. */
test("video risk and EOF leave manual speech ownership intact", function testManualOwnership() {
  const f = fixture(); f.speech.speak("manual speech"); f.frame();
  f.frame([alertBox({alert_priority: 4, risk_level: "critical", ttc_sec: 0.01})]);
  assert.equal(f.speech.requests.length, 1); assert.equal(f.manager.active.speech, null);
  f.manager.stop("eof"); assert.equal(f.speech.current.text, "manual speech");
  assert.deepEqual(f.speech.cancels, []); f.manager.dispose();
});

/** Functionality: Test backend admission through real manager scheduling. Inputs: short TTC but no server alert.
 * Outputs: no speech submission. Logic: presentation layer cannot invent alarms; Constraints: no re-scoring. */
test("non-alert video targets never produce speech even with tiny TTC", function testNoAlertSpeech() {
  const f = fixture();
  f.frame([alertBox({alert_required: false, ttc_sec: 0.001}), alertBox({alert_priority: 0, ttc_sec: 0.001})]);
  assert.equal(f.speech.requests.length, 0); assert.equal(f.manager.active.speech, null); f.manager.dispose();
});

/** Functionality: Preserve capture freshness admission. Inputs: expired/future/foreign frames.
 * Outputs: no alarm submission. Logic: TTL is measured from original capture; Constraints: TTC does not refresh stale data. */
test("expired future and foreign-session video frames cannot trigger speech", function testStaleFrames() {
  const f = fixture(); f.clock.advance(1100);
  f.frame([alertBox()], 100); f.frame([alertBox()], 1200); f.frame([alertBox()], 1100, "other-session");
  assert.equal(f.speech.requests.length, 0); f.manager.dispose();
});

/** Functionality: Retain queued request source deadlines. Inputs: unstarted old source then newer fresh TTC.
 * Outputs: old request cancelled at source expiry and only newest content submitted.
 * Logic: latest frames cannot extend an older request's TTL; Constraints: submission is not actual audio start. */
test("unstarted video speech expires by its own source and consumes only the latest fresh TTC", function testQueuedSourceExpiry() {
  const f = fixture(); f.frame([alertBox({ttc_sec: 4})]); f.clock.advance(900);
  f.frame([alertBox({ttc_sec: 1})], 850); f.clock.advance(1100);
  assert.deepEqual(f.speech.cancels, [1]); assert.equal(f.speech.requests.length, 2);
  assert.equal(f.speech.current.text, "警告，前方有行人靠近。");
  assert.equal(f.manager.active.speech.source.captured_at_ms, 850);
  assert.equal(f.manager.history.size, 0); assert.equal(f.events.filter(event => event.phase === "output_start").length, 0);
  f.manager.dispose();
});

/** Functionality: Prevent transient no-alert frames from truncating started video speech.
 * Inputs: started alert followed by fresh empty and server-non-alert snapshots, then engine completion.
 * Outputs: original speech completes without cancellation or new speech from latest non-alert input.
 * Logic: completion belongs to the accepted started request; Constraints: no FIFO or implicit alert generation. */
test("started video speech completes across fresh empty and non-alert frames", function testStartedNoAlertCompletion() {
  const f = fixture(); f.frame(); f.speech.start(); f.clock.advance(200); f.frame([]);
  assert.equal(f.speech.current?.id, 1); assert.deepEqual(f.speech.cancels, []);
  f.clock.advance(300); f.frame([alertBox({alert_required: false, ttc_sec: 0.001})]);
  assert.equal(f.speech.current?.id, 1); assert.deepEqual(f.speech.cancels, []);
  f.speech.end(); assert.equal(f.speech.current, null); assert.equal(f.manager.active.speech, null);
  assert.equal(f.speech.requests.length, 1); f.manager.dispose();
});

/** Functionality: Prevent source TTL from truncating an already started video sentence.
 * Inputs: started risk request and original source expiry before native end.
 * Outputs: old speech remains until end, then stale input produces no further request.
 * Logic: TTL still controls admission rather than terminating accepted started video speech.
 * Constraints: do not refresh the source, replay stale risk or fabricate native completion. */
test("started video speech survives source expiry and does not replay stale risk at end", function testStartedExpiryCompletion() {
  const f = fixture(); f.frame(); f.speech.start(); f.clock.advance(1100);
  assert.equal(f.manager.fresh(), false); assert.equal(f.speech.current?.id, 1);
  assert.deepEqual(f.speech.cancels, []); assert.equal(f.speech.requests.length, 1);
  f.speech.end(); assert.equal(f.speech.current, null); assert.equal(f.manager.active.speech, null);
  f.clock.advance(5000); assert.equal(f.speech.requests.length, 1); f.manager.dispose();
});

/** Functionality: Keep same/lower ranked targets from interrupting a video alert sentence.
 * Inputs: started high alert then same-grade shorter-TTC and lower-grade different tracks.
 * Outputs: original request remains uncancelled. Logic: only existing server-level escalation may preempt.
 * Constraints: no new TTC urgency threshold or cross-track FIFO. */
test("same or lower priority video targets do not preempt started speech", function testSameLowerNoPreemption() {
  const f = fixture(); f.frame(); f.speech.start(); f.clock.advance(200);
  f.frame([alertBox({track_id: 2, ttc_sec: 0.1})]);
  assert.equal(f.speech.current?.id, 1); assert.deepEqual(f.speech.cancels, []);
  f.clock.advance(300);
  f.frame([alertBox({track_id: 3, alert_priority: 2, risk_level: "medium", ttc_sec: 0.001})]);
  assert.equal(f.speech.current?.id, 1); assert.equal(f.speech.requests.length, 1);
  assert.deepEqual(f.speech.cancels, []); f.manager.dispose();
});

/** Functionality: Preserve cancellation of video requests that have not actually started.
 * Inputs: queued alert followed by empty/non-alert input or its source TTL expiry.
 * Outputs: owned queued request cancelled without speech start or replay.
 * Logic: opt-in completion begins at native start only; Constraints: queue acknowledgement does not extend source validity. */
test("queued video speech still cancels when alert disappears or source expires", function testQueuedAbsenceCancellation() {
  for (const cause of ["empty", "non-alert", "expiry"]) {
    const f = fixture(); f.frame();
    if (cause === "expiry") f.clock.advance(1100);
    else { f.clock.advance(200); f.frame(cause === "empty" ? [] : [alertBox({alert_required: false})]); }
    assert.deepEqual(f.speech.cancels, [1]); assert.equal(f.speech.current, null);
    assert.equal(f.manager.active.speech, null); assert.equal(f.speech.requests.length, 1);
    assert.equal(f.events.filter(event => event.phase === "output_start").length, 0); f.manager.dispose();
  }
});

/** Functionality: Respect disabling during started video speech. Inputs: started request and explicit off switch.
 * Outputs: immediate owned cancellation with cleared work and no further output while disabled.
 * Logic: completion option never overrides explicit user controls; Constraints: switch does not auto-enable or restart. */
test("disabling video guidance still cancels its started speech immediately", function testDisableStartedSpeech() {
  const f = fixture(); f.frame(); f.speech.start(); f.manager.setEnabled(false);
  assert.deepEqual(f.speech.cancels, [1]); assert.equal(f.speech.current, null);
  assert.equal(f.manager.active.speech, null); assert.equal(f.manager.latest, null);
  assert.equal(f.manager.history.size, 0); assert.equal(f.clock.timers.size, 0);
  f.frame(); assert.equal(f.speech.requests.length, 1); assert.equal(f.manager.enabled, false); f.manager.dispose();
});

/** Functionality: Respect manual device cancellation of started guidance speech.
 * Inputs: native-style cancel event then newer high-priority snapshot.
 * Outputs: speech channel stays paused without automatic replay.
 * Logic: explicit cancellation remains authoritative; Constraints: video completion option is not an output retry policy. */
test("manual cancellation still stops started video speech without replay", function testUserCancelStartedSpeech() {
  const f = fixture(); f.frame(); f.speech.start(); f.speech.stopOwned(1);
  assert.equal(f.manager.paused.speech, true); assert.equal(f.speech.current, null);
  f.frame([alertBox({track_id: 2, alert_priority: 4, risk_level: "critical"})]);
  assert.equal(f.speech.requests.length, 1); assert.deepEqual(f.speech.cancels, [1]); f.manager.dispose();
});

/** Functionality: Keep default camera scheduling behavior unchanged by the video opt-in.
 * Inputs: default manager's started alert followed by empty/non-alert input or source expiry.
 * Outputs: immediate cancellation for all three causes.
 * Logic: only video fixtures request completion; Constraints: no global output-policy change. */
test("default camera guidance still cancels started speech on no-alert frames and expiry", function testDefaultCameraCancellation() {
  for (const cause of ["empty", "non-alert", "expiry"]) {
    const f = fixture(false); f.frame(); f.speech.start();
    if (cause === "expiry") f.clock.advance(1100);
    else { f.clock.advance(200); f.frame(cause === "empty" ? [] : [alertBox({alert_required: false})]); }
    assert.deepEqual(f.speech.cancels, [1]); assert.equal(f.speech.current, null);
    assert.equal(f.manager.active.speech, null); assert.equal(f.speech.requests.length, 1); f.manager.dispose();
  }
});

/** Functionality: Release video-owned output at every terminal path. Inputs: stop/EOF/error after accepted alarm.
 * Outputs: no active request, latest frame, history, timer or late-frame replay.
 * Logic: exercise existing explicit lifecycle; Constraints: no automatic reconnect or output restart. */
test("video stop EOF and error clear owned alerts and reject late frames", function testEndCleanup() {
  for (const reason of ["stop", "eof", "error"]) {
    const f = fixture(); f.frame(); f.speech.start(); f.manager.stop(reason);
    assert.deepEqual(f.speech.cancels, [1]); assert.equal(f.speech.current, null);
    assert.equal(f.manager.latest, null); assert.equal(f.manager.sessionId, null);
    assert.equal(f.manager.history.size, 0); assert.equal(f.clock.timers.size, 0);
    f.frame(); f.clock.advance(10000); assert.equal(f.speech.requests.length, 1); f.manager.dispose();
  }
});

/** Functionality: Speak the walk model's obstacle labels and its neutral pedestrian-light label.
 * Inputs: the four walk-model obstacle classes plus the merged signal label. Outputs: reviewed Chinese, verbatim English.
 * Logic: colour classes (ped_red/ped_green/ped_off) never reach speech; the backend merges them into "pedestrian light".
 * Constraints: no light-colour announcement is added here. */
test("walk-model obstacles and the merged pedestrian light have reviewed speech", function testWalkModelCategories() {
  const expected = {pole: "柱子", bollard: "护栏柱", traffic_cone: "锥桶", stairs: "台阶", "pedestrian light": "人行信号灯"};
  for (const [label, zh] of Object.entries(expected))
    assert.equal(videoAlertText(alertBox({label}), "zh-CN"), `警告，前方有${zh}靠近。`);
  assert.equal(videoAlertText(alertBox({label: "traffic_cone"}), "en-US"), "Warning. Traffic_cone ahead is approaching.");
});
