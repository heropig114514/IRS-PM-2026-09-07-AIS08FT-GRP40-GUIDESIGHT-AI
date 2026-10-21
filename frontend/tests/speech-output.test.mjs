/** Responsibilities: 验证共用语音接口的输入、生命周期、语音选择与MCP分派边界。
 * Implementation: 用EventTarget替身驱动原生事件和设备接口；额外检验重入广播的请求快照与观察者隔离；不播放真实声音或请求网络。
 * Related Modules: speech-output.js, ai-control.js, index.html, translations.js, i18n.js。
 * Declaration Index: Control; Control.constructor; Control.replaceChildren; Control.append;
 * SpeechDocument; SpeechDocument.constructor; SpeechDocument.getElementById;
 * SpeechDocument.querySelectorAll; SpeechDocument.createElement; Utterance; Utterance.constructor;
 * Engine; Engine.constructor; Engine.getVoices; Engine.speak; Engine.cancel; Engine.emit;
 * fixture; enable; testPrerequisites; testLifecycle; testLimits; testVoices;
 * testVoiceQuery; testCancellation; testNativeErrors; testVisibility; testMcp; testTranslations;
 * testGuidanceEvents; testLocalVoiceOnly; testReentrantBroadcast。
 * Variable Index: VOICES — 明确本地/远程测试身份；其他变量只保存本测试的控件、请求和诊断。
 * Constraints: 替身不能证明真实MCP网络、TTS引擎或扬声器；真实浏览器回归另行执行。
 */
import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {SpeechOutput, MAX_TEXT_LENGTH} from "../speech-output.js";
import {performSpeech} from "../ai-control.js";
import {translate} from "../i18n.js";

const VOICES = [
  {voiceURI: "local-zh", name: "Fixture Chinese", lang: "zh-CN", localService: true, default: true},
  {voiceURI: "remote-en", name: "Fixture English", lang: "en-US", localService: false, default: false},
];

/** Functionality: Verify real driver's request lifecycle/ownership seam. Inputs: native event fixture.
 * Outputs: submitted/start/end and safe refusal to cancel another request ID.
 * Logic: engine event determines actual start, stale events remain isolated; Constraints: no audible hardware assertion. */
test("output subscriptions expose native lifecycle and preserve request ownership", function testGuidanceEvents() {
  const {engine, output} = fixture(), events = []; enable(output);
  const unsubscribe = output.subscribe(event => events.push(event));
  const first = output.speak("first", {voice_id: "local-zh"});
  assert.equal(events[0].phase, "submitted"); assert.equal(events[0].request_id, first.last_request.id);
  assert.equal(output.stopOwned(999).reason, "not_owned"); assert.equal(engine.cancels, 0);
  engine.emit(engine.requests[0], "start"); engine.emit(engine.requests[0], "end");
  assert.deepEqual(events.map(event => event.phase), ["submitted", "start", "end"]);
  const second = output.speak("second", {voice_id: "local-zh"});
  assert.equal(output.stopOwned(first.last_request.id).reason, "not_owned");
  assert.equal(output.stopOwned(second.last_request.id).accepted, true);
  assert.equal(events.at(-1).phase, "cancel"); unsubscribe();
});
/** Functionality: Verify explicit local-MVP gate without weakening original voice selection.
 * Inputs: local-only controller, default/remote/explicit-local choices. Outputs: no remote/default submission.
 * Logic: invalid local voices never replace themselves; Constraints: synthetic voice inventory. */
test("local-MVP marker rejects remote and browser-default speech", function testLocalVoiceOnly() {
  const {output, engine} = fixture(); output.localOnly = true; enable(output);
  assert.equal(output.speak("sample").reason, "local_voice_required");
  assert.equal(output.speak("sample", {language: "en-US", voice_id: "remote-en"}).reason, "local_voice_required");
  assert.equal(engine.requests.length, 0);
  assert.equal(output.speak("sample", {voice_id: "local-zh"}).accepted, true); output.stop();
});

/** Functionality: Preserve a terminal event when its first observer immediately submits new output.
 * Inputs: Real driver end/cancel, explicit second request and observer-local event mutation.
 * Outputs: Old request identity/time for every terminal observer plus distinct nested submitted event.
 * Logic: Exercise actual driver dispatch and native-fixture completion rather than calling notify directly.
 * Constraints: No native audio, substituted clock or assertions swallowed inside driver observers. */
test("reentrant end and cancel broadcasts retain their original request and timestamp", function testReentrantBroadcast() {
  for (const phase of ["end", "cancel"]) {
    const {engine, output} = fixture(); enable(output);
    let firstId = null, original = null, firstReference = null, nested = null;
    const later = [];
    const unsubscribeFirst = output.subscribe(event => {
      if (event.phase !== phase || event.request_id !== firstId) return;
      original = {...event}; firstReference = event;
      event.request_id = -1; event.phase = "observer_mutation"; event.at = -1;
      event.channel = "observer_mutation"; event.reason = "observer_mutation";
      nested = output.speak("second request", {voice_id: "local-zh"});
    });
    const unsubscribeLater = output.subscribe(event => later.push(event));
    const first = output.speak("first request", {voice_id: "local-zh"}); firstId = first.last_request.id;
    const oldUtterance = engine.requests[0];
    if (phase === "end") engine.emit(oldUtterance, "end");
    else output.stop();
    assert.equal(nested?.accepted, true);
    const terminal = later.filter(event => event.phase === phase);
    assert.equal(terminal.length, 1);
    assert.deepEqual(terminal[0], original);
    assert.notEqual(terminal[0], firstReference);
    assert.equal(terminal[0].request_id, firstId);
    assert.equal(terminal[0].at, original.at);
    assert.ok(later.some(event => event.phase === "submitted" && event.request_id === nested.last_request.id));
    assert.notEqual(nested.last_request.id, firstId);
    assert.equal(output.current, engine.requests[1]);
    unsubscribeFirst(); unsubscribeLater(); output.stop();
  }
});

/** Functionality: 提供具有实际事件派发的简单控件。
 * Logic: 保存值、状态和选项，不解析HTML。Constraints: 不模拟布局或disabled事件拦截。
 */
class Control extends EventTarget {
  /** Functionality: 初始化节点。Inputs: value可选初值。
   * Outputs: 控件对象。Logic: 默认未选中并有空子节点列表。Constraints: 无浏览器环境。
   */
  constructor(value = "") {
    super();
    Object.assign(this, {value, checked: false, disabled: false, textContent: "", dataset: {}, children: []});
  }
  /** Functionality: 替换选项。Inputs: nodes。Outputs: children更新。
   * Logic: 直接替换列表。Constraints: 不隐式替换value，以便测试控制器明确保留选择。
   */
  replaceChildren(...nodes) { this.children = nodes; }
  /** Functionality: 添加选项。Inputs: node。Outputs: children追加。
   * Logic: 保存引用。Constraints: 不实现通用DOM渲染。
   */
  append(node) { this.children.push(node); }
}

/** Functionality: 提供语音UI固定节点与可见性。
 * Logic: EventTarget派发生命周期事件。Constraints: 无页面、权限、文本持久化或网络。
 */
class SpeechDocument extends EventTarget {
  /** Functionality: 创建完整控制器所需节点。
   * Inputs: None。Outputs: 节点映射和visible状态。
   * Logic: rate/volume初值1，language为zh-CN。Constraints: 不读取真实HTML，另有实际页面测试。
   */
  constructor() {
    super();
    this.visibilityState = "visible";
    const ids = ["enabled", "text", "language", "voice", "rate", "volume", "play", "stop",
      "status", "message", "request", "rate-value", "volume-value"];
    this.nodes = new Map(ids.map(id => ["speech-" + id, new Control()]));
    this.getElementById("speech-rate").value = "1";
    this.getElementById("speech-volume").value = "1";
    this.getElementById("speech-language").value = "zh-CN";
  }
  /** Functionality: 查找固定节点。Inputs: id。Outputs: 控件或undefined。
   * Logic: 映射读取。Constraints: 不制造缺失节点。
   */
  getElementById(id) { return this.nodes.get(id); }
  /** Functionality: 提供示例按钮集合。Inputs: selector。
   * Outputs: 空集合。Logic: 本组测试不模拟示例按钮，真实UI回归覆盖它。
   * Constraints: 只接受控制器的固定选择器。
   */
  querySelectorAll(selector) { assert.equal(selector, "[data-speech-sample]"); return []; }
  /** Functionality: 创建语音选项节点。Inputs: tag。
   * Outputs: Control。Logic: 仅支持option。Constraints: 不实现其他DOM类型。
   */
  createElement(tag) { assert.equal(tag, "option"); return new Control(); }
}

/** Functionality: 表示可接收start/end/error的语音请求。
 * Logic: EventTarget提供真实事件身份。Constraints: 不合成或播放音频。
 */
class Utterance extends EventTarget {
  /** Functionality: 保存原始文本。Inputs: text。
   * Outputs: 测试utterance。Logic: 不改写正文。Constraints: 仅测试用内存对象。
   */
  constructor(text) { super(); this.text = text; }
}

/** Functionality: 明确模拟语音引擎、列表加载和失败。
 * Logic: 请求仅记录，事件由测试显式推进。Constraints: 没有真实系统语音或扬声器。
 */
class Engine extends EventTarget {
  /** Functionality: 初始化原生状态。
   * Inputs: None。Outputs: 固定voices、请求列表、计数和可控制异常。
   * Logic: 不自动触发事件。Constraints: 每个测试独立实例。
   */
  constructor() {
    super();
    Object.assign(this, {voices: [...VOICES], requests: [], cancels: 0, speaking: false,
      pending: false, paused: false, failSpeak: false, failCancel: false, failVoices: false});
  }
  /** Functionality: 返回当前语音列表或显式失败。
   * Inputs: voices/failVoices。Outputs: 列表或Error。
   * Logic: 测试可更改列表并触发voiceschanged。Constraints: 不调用操作系统服务。
   */
  getVoices() { if (this.failVoices) throw new Error("Fixture voice lookup failure"); return this.voices; }
  /** Functionality: 接收一次语音请求。
   * Inputs: utterance。Outputs: void或Error；请求计数增加。
   * Logic: 模拟排队但不自动开始。Constraints: API返回不代表播放。
   */
  speak(utterance) {
    this.requests.push(utterance);
    if (this.failSpeak) throw new Error("Fixture native failure");
    this.pending = true;
  }
  /** Functionality: 接收一次取消请求。
   * Inputs: 引擎状态/failCancel。Outputs: void或Error。
   * Logic: 成功时模拟同步canceled事件，测试控制器是否隔离它。
   * Constraints: 不证明真实浏览器取消时序或实际停止声音。
   */
  cancel() {
    this.cancels++;
    if (this.failCancel) throw new Error("Fixture cancellation failure");
    this.pending = this.speaking = false;
    if (this.requests.length) this.emit(this.requests.at(-1), "error", "canceled");
  }
  /** Functionality: 推进当前或旧请求的标准事件。
   * Inputs: utterance/type及可选error。Outputs: 事件派发和引擎状态更新。
   * Logic: 保留真实event.target对象身份。Constraints: 明确受控时序，不代表真实声音。
   */
  emit(utterance, type, error) {
    if (type === "start") { this.pending = false; this.speaking = true; }
    if (type === "end" || type === "error") { this.pending = false; this.speaking = false; }
    utterance.dispatchEvent(Object.assign(new Event(type), {error}));
  }
}

/** Functionality: 创建隔离的真实控制器实例。
 * Inputs: supported决定原生接口是否存在。Outputs: doc/win/engine/output/logs。
 * Logic: 仅模拟设备依赖；控制器与MCP分派使用真实源码。
 * Constraints: console接收器只记录测试数据，不验证真实日志服务。
 */
function fixture(supported = true) {
  const doc = new SpeechDocument(), win = new EventTarget(), engine = new Engine(), logs = [];
  Object.assign(win, {navigator: {userActivation: {hasBeenActive: true}},
    speechSynthesis: supported ? engine : undefined, SpeechSynthesisUtterance: supported ? Utterance : undefined,
    console: {info: (...args) => logs.push(args), error: (...args) => logs.push(args)}});
  return {doc, win, engine, logs, output: new SpeechOutput(doc, win)};
}

/** Functionality: 派发测试用明确启用选择。
 * Inputs: output。Outputs: 开关变更事件。Logic: 不直接调用speak。
 * Constraints: 可信用户激活由测试状态显式控制，不假冒操作系统交互。
 */
function enable(output) {
  output.enabled.checked = true;
  output.enabled.dispatchEvent(new Event("change"));
}

/** Functionality: 验证默认关闭、前台/激活限制与不支持结果。
 * Inputs: 独立设备替身和显式可见性/激活。Outputs: 零原生调用与准确原因。
 * Logic: 查询和启用不发声，前提满足前不提交。
 * Constraints: 真实浏览器激活和MCP链路另有回归验证。
 */
test("默认关闭、后台和未激活请求不会提交语音", function testPrerequisites() {
  const {doc, win, engine, output} = fixture();
  assert.equal(output.describe().enabled, false);
  assert.equal(output.speak("text").reason, "disabled");
  enable(output);
  win.navigator.userActivation.hasBeenActive = false;
  assert.equal(output.speak("text").reason, "activation_required");
  win.navigator.userActivation.hasBeenActive = true;
  doc.visibilityState = "hidden";
  assert.equal(output.speak("text").reason, "page_hidden");
  assert.deepEqual(engine.requests, []);
  const unavailable = fixture(false);
  assert.equal(unavailable.output.speak("text").reason, "unsupported");
  assert.equal(unavailable.output.stop().reason, "unsupported");
});

/** Functionality: 验证提交、开始和结束是不同状态，繁忙时不排第二个请求。
 * Inputs: 明确文本/参数、受控原生事件和paused状态。
 * Outputs: 请求、状态、控件与隐私日志断言。
 * Logic: 使用真实speak与事件处理器。Constraints: completed不代表实际扬声器声音。
 */
test("提交不等于开始/完成且不会自动排队", function testLifecycle() {
  const {engine, output, logs} = fixture();
  enable(output);
  const submitted = output.speak(" private fixture text ", {rate: 1.3, volume: 0.4});
  assert.equal(submitted.accepted, true);
  assert.equal(submitted.state, "queued");
  assert.equal(submitted.audio_output_verified, false);
  const first = engine.requests[0];
  assert.equal(first.text, " private fixture text ");
  assert.equal(first.rate, 1.3);
  assert.equal(first.volume, 0.4);
  assert.equal(output.speak("second").reason, "busy");
  assert.equal(engine.requests.length, 1);
  engine.emit(first, "start");
  assert.equal(output.describe().state, "speaking");
  engine.emit(first, "end");
  assert.equal(output.describe().state, "completed");
  assert.equal(output.describe().active_request, false);
  assert.equal(output.playButton.disabled, false);
  engine.paused = true;
  assert.equal(output.speak("paused engine").reason, "busy");
  assert.equal(output.describe().engine_paused, true);
  assert.equal(engine.requests.length, 1);
  assert.doesNotMatch(JSON.stringify(logs), /private fixture text/);
  assert.doesNotMatch(JSON.stringify(output.describe()), /private fixture text/);
});

/** Functionality: 验证Unicode码点上限、空白、非有限/超范围参数不触达设备。
 * Inputs: 非法文本/参数与1000个astral字符边界。Outputs: 明确拒绝与保留原始正文。
 * Logic: 使用既定产品边界，而不修改阈值制造通过。
 * Constraints: 不验证引擎对长文本或特定语言的合成能力。
 */
test("文本和数值边界明确拒绝且Unicode计数一致", function testLimits() {
  const {engine, output} = fixture();
  enable(output);
  for (const text of ["", "  ", 123, "x".repeat(MAX_TEXT_LENGTH + 1)])
    assert.equal(output.speak(text).reason, "invalid_text");
  for (const options of [{rate: NaN}, {rate: Infinity}, {rate: 2.1}, {volume: -0.1},
    {volume: 1.1}, {rate: "1"}, {voice_id: 3}, {language: "unknown"}])
    assert.equal(output.speak("text", options).reason, "invalid_parameters");
  assert.equal(engine.requests.length, 0);
  const result = output.speak("😀".repeat(MAX_TEXT_LENGTH), {volume: 0});
  assert.equal(result.accepted, true);
  assert.equal(result.last_request.text_length, MAX_TEXT_LENGTH);
  assert.equal(engine.requests[0].volume, 0);
});

/** Functionality: 验证语音身份、异步列表、选项丢失及语言匹配。
 * Inputs: 固定本地/远程语音和明确voice_id。Outputs: 精确voice对象或显式拒绝。
 * Logic: 失效声音不切换为默认；原列表更新保留无效选择。
 * Constraints: 本地/远程是替身属性，不证明真实离线能力或网络行为。
 */
test("指定声音失效或歧义不会替换为默认声音", function testVoices() {
  const {engine, output} = fixture();
  enable(output);
  const listed = output.voiceList();
  assert.equal(listed.voices[1].local_service, false);
  output.voice.value = "local-zh";
  engine.voices = [];
  engine.dispatchEvent(new Event("voiceschanged"));
  assert.equal(output.voice.value, "local-zh");
  assert.equal(output.voice.children.at(-1).disabled, true);
  assert.equal(output.speak("text", {voice_id: "local-zh"}).reason, "voice_unavailable");
  engine.voices = [VOICES[0], VOICES[0]];
  assert.equal(output.speak("text", {voice_id: "local-zh"}).reason, "voice_ambiguous");
  engine.voices = [...VOICES];
  assert.equal(output.speak("text", {voice_id: "remote-en"}).reason, "voice_language_mismatch");
  const submitted = output.speak("text", {voice_id: "remote-en", language: "en-US"});
  assert.equal(submitted.accepted, true);
  assert.equal(engine.requests.length, 1);
  assert.equal(engine.requests[0].voice, VOICES[1]);
});

/** Functionality: 验证列表读取失败不会把缓存冒充成当前列表。
 * Inputs: 已加载列表后的getVoices失败。Outputs: 结构化错误、空返回及零speak。
 * Logic: 明确声音选择失败即拒绝。Constraints: 不自动重试或改用默认声音。
 */
test("语音列表查询失败保持可诊断且不替换声音", function testVoiceQuery() {
  const {engine, output} = fixture();
  enable(output);
  engine.failVoices = true;
  assert.equal(output.voiceList().reason, "voice_query_failed");
  assert.deepEqual(output.voiceList().voices, []);
  assert.equal(output.speak("text", {voice_id: "local-zh"}).reason, "voice_query_failed");
  assert.equal(engine.requests.length, 0);
});

/** Functionality: 验证取消异常保留请求，成功取消隔离旧事件和新请求。
 * Inputs: 受控cancel异常/同步取消事件/旧事件。Outputs: 取消计数与准确状态。
 * Logic: 不重试取消；后续显式停止后新请求不受旧回调影响。
 * Constraints: 不代表实际声音已停止，只检查应用状态与调用边界。
 */
test("取消失败保留状态，迟到旧事件不能结束新请求", function testCancellation() {
  const {engine, output} = fixture();
  enable(output);
  output.speak("first");
  const first = engine.requests[0];
  engine.failCancel = true;
  assert.equal(output.stop().reason, "cancel_failed");
  assert.equal(output.describe().active_request, true);
  assert.equal(engine.cancels, 1);
  engine.failCancel = false;
  assert.equal(output.stop().accepted, true);
  assert.equal(output.describe().state, "stopped");
  output.speak("second");
  const second = engine.requests[1];
  engine.emit(first, "end");
  assert.equal(output.describe().state, "queued");
  assert.equal(output.current, second);
  engine.emit(second, "start");
  assert.equal(output.describe().state, "speaking");
});

/** Functionality: 验证同步speak失败及异步错误均明确失败、不重试并过滤外部消息。
 * Inputs: 合成native抛错/事件error。Outputs: 单次请求、资源释放和安全诊断。
 * Logic: 控制器先记录请求身份，错误只记录标准码而不输出正文。
 * Constraints: 不验证真实服务网络或扬声器故障。
 */
test("同步及异步语音错误不会静默重试或泄露正文", function testNativeErrors() {
  const {engine, output, logs} = fixture();
  enable(output);
  engine.failSpeak = true;
  assert.equal(output.speak("private text").reason, "native_error");
  assert.equal(output.describe().last_request.id, 1);
  assert.equal(output.describe().active_request, false);
  engine.failSpeak = false;
  output.speak("other private text");
  engine.emit(engine.requests[1], "error", "not-allowed");
  assert.equal(output.describe().last_error, "not-allowed");
  assert.equal(output.describe().state, "error");
  output.speak("third private text");
  engine.emit(engine.requests[2], "error", "private arbitrary external diagnostic");
  assert.equal(output.describe().last_error, "unknown");
  assert.equal(engine.requests.length, 3);
  assert.doesNotMatch(JSON.stringify(logs), /private/);
});

/** Functionality: 验证关闭/隐藏/离页取消且返回前台不恢复朗读。
 * Inputs: 三个独立用户/生命周期事件。Outputs: 每次仅一个cancel，无新speak。
 * Logic: 使用真实注册回调。Constraints: 不是后台音频或手机锁屏验证。
 */
test("关闭、隐藏或离页停止，返回前台不自动继续", function testVisibility() {
  for (const kind of ["disabled", "hidden", "pagehide"]) {
    const {doc, win, engine, output} = fixture();
    enable(output);
    output.speak("text");
    if (kind === "disabled") {
      output.enabled.checked = false;
      output.enabled.dispatchEvent(new Event("change"));
    } else if (kind === "hidden") {
      doc.visibilityState = "hidden";
      doc.dispatchEvent(new Event("visibilitychange"));
    } else win.dispatchEvent(new Event("pagehide"));
    doc.visibilityState = "visible";
    doc.dispatchEvent(new Event("visibilitychange"));
    win.dispatchEvent(new Event("pagehide"));
    assert.equal(engine.cancels, 1);
    assert.equal(engine.requests.length, 1);
    assert.equal(output.describe().state, "stopped");
  }
});

/** Functionality: 验证MCP与手动接口共享一次原生调用和明确状态。
 * Inputs: 实际performSpeech与控制器；设备依赖为替身。
 * Outputs: 状态/列表查询无播放，默认关闭不被绕过，参数精确传递及停止。
 * Logic: 不操作DOM按钮来模拟完成，而委派到共用接口。
 * Constraints: 实际HTTP/WS/MCP transport另有真实浏览器回归。
 */
test("MCP语音接口不自动启用且与UI共享执行路径", function testMcp() {
  assert.equal(performSpeech("speech_status", {}, null).reason, "page_unavailable");
  const {engine, output} = fixture();
  assert.equal(performSpeech("speech_status", {}, output).state, "idle");
  assert.equal(performSpeech("speech_voices", {}, output).voices.length, 2);
  assert.equal(performSpeech("speech_speak", {text: "text"}, output).reason, "disabled");
  enable(output);
  assert.equal(performSpeech("speech_speak", {text: "text", rate: null}, output).reason, "invalid_parameters");
  const submitted = performSpeech("speech_speak", {text: "text", language: "en-US",
    voice_id: "remote-en", rate: 1.5, volume: 0.5}, output);
  assert.equal(submitted.accepted, true);
  assert.equal(engine.requests[0].rate, 1.5);
  assert.equal(engine.requests[0].voice, VOICES[1]);
  assert.equal(performSpeech("speech_stop", {}, output).state, "stopped");
  assert.equal(engine.requests.length, 1);
  assert.throws(() => performSpeech("speech_loop", {}, output), /Unknown speech command/);
});

/** Functionality: 验证静态UI及动态提示的中英词典完整性。
 * Inputs: 真实模块/HTML源码；测试文本作为用户内容保持原文。
 * Outputs: 所有界面字符串可翻译，动态请求摘要与原生错误码保留参数。
 * Logic: 排除明确的示例朗读正文；文本框与原生语音名称由浏览器检查保护。
 * Constraints: 不替代真实DOM翻译、布局或声音验证。
 */
test("语音UI和动态状态完整翻译，朗读正文不属于UI词典", async function testTranslations() {
  const source = await readFile(new URL("../speech-output.js", import.meta.url), "utf8");
  const html = await readFile(new URL("../index.html", import.meta.url), "utf8");
  const section = html.match(/<section id="speech-test"[\s\S]*?<\/section>/)[0];
  const protectedText = "这是 PRS 语音输出测试。";
  const strings = [...source.matchAll(/"([^"\n]*\p{Script=Han}[^"\n]*)"/gu),
    ...section.replace(/<textarea[\s\S]*?<\/textarea>/g, "").matchAll(/>([^<>]*\p{Script=Han}[^<>]*)</gu),
    ...section.matchAll(/aria-label="([^"]+)"/g)].map(match => match[1].trim());
  for (const text of strings) if (text !== protectedText)
    assert.doesNotMatch(translate(text, "en"), /\p{Script=Han}/u, text);
  assert.equal(translate("请求 1 · 4 个字符 · en-US · 语速 1.5× · 音量 50%", "en"),
    "Request 1 · 4 characters · en-US · Rate 1.5× · Volume 50%");
});
