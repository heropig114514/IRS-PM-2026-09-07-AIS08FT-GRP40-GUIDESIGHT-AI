/* Responsibilities: Shared voice/text assistant panel, progress and cross-page conversation.
 * Implementation: One bound WebSocket, ordered replay and explicit request ownership; the product
 * view uses press-and-hold voice capture with a prompt-preserving first permission request, while
 * the diagnostic view retains click-to-toggle capture. Safe DOM text rendering and in-memory audio;
 * only live assistant replies notify the speech integration; failures render as status, never dialogue.
 * Disconnect releases microphone.
 * No auto reconnect.
 * Related Modules: ai-control.js, voice-input.js, agent.css, integration/assist-mvp.js;
 * backend/core/agent/transport.
 * Declaration Index: label; recordingFailure; eventNotice; AgentPanel; AgentPanel.constructor; AgentPanel.connect;
 * AgentPanel.send; AgentPanel.submit; AgentPanel.record; AgentPanel.startRecording;
 * AgentPanel.finishRecording; AgentPanel.handlePressStart; AgentPanel.handlePressEnd;
 * AgentPanel.handlePressCancel; AgentPanel.cancel;
 * AgentPanel.receive; AgentPanel.append; AgentPanel.render; AgentPanel.fail.
 * Variable Index: COPY — bilingual interface and media setup text; TOOLS — discovery/action labels;
 * panel — singleton; AgentPanel.productMode selects the formal press-to-talk interaction;
 * holdActive/holdToken/activePointerId isolate the active pointer or keyboard press from stale events;
 * prs-agent-reply notifies speech integration for live replies only, never replayed history;
 * EVENT_NOTICES maps transport/runtime codes to localized, non-conversational status;
 * eventNotice resolves the visible notice from that map.
 */
import {VoiceInput} from "./voice-input.js";

const COPY = {
  title: ["语音助手", "Voice assistant"], help: ["说出或输入你想做的事，例如：开启导航、关闭摄像头。", "Say or type a request, such as start navigation or close the camera."],
  productHelp: ["按住按钮说话，松开后发送。首次使用请完成麦克风授权，再按住说话；也可以输入指令。", "Hold the button while speaking, then release to send. On first use, allow microphone access and hold again to speak. You can also type a request."],
  privacy: ["录音最长20秒；音频与对话发送 Qwen 处理，应用不保存录音。定位和设备权限仍由你授权。", "Up to 20 seconds. Audio and conversation are processed by Qwen; this app does not save recordings. Device permissions remain yours."],
  send: ["发送", "Send"], record: ["点击说话", "Speak"], finish: ["结束并发送", "Finish & send"],
  hold: ["按住说话", "Hold to speak"], release: ["松开发送", "Release to send"],
  recordingProduct: ["正在录音，松开按钮发送（最长20秒）", "Recording. Release to send within 20 seconds."],
  cancel: ["取消", "Cancel"], clear: ["清空对话", "Clear conversation"], input: ["输入指令或补充信息", "Enter a request or missing information"],
  connecting: ["正在连接助手…", "Connecting…"], ready: ["可以说话或输入指令", "Ready for your request"],
  thinking: ["正在处理指令…", "Processing your request…"], transcribing: ["正在识别语音…", "Transcribing…"],
  routing: ["正在识别操作意图…", "Identifying your request…"],
  system: ["已识别操作：", "Recognized operations: "], clarify: ["需要明确操作意图", "Operation needs clarification"],
  general: ["通用问答", "General question"], unsupported: ["未命中已启用的能力", "Outside enabled capabilities"],
  requestFailed: ["请求未完成，请稍后重试。", "The request did not complete. Try again later."],
  explanationUnavailable: ["助手暂时无法生成故障说明，请稍后重试。", "The assistant could not explain this failure. Try again later."],
  assistantUnavailable: ["助手服务暂时不可用。", "The assistant service is unavailable."],
  cancelledNotice: ["请求已取消。", "Request cancelled."],
  busyNotice: ["上一条指令仍在处理中。", "The previous request is still running."],
  recording: ["正在录音，点击结束并发送（最长20秒）", "Recording. Finish and send within 20 seconds."],
  permission: ["等待麦克风授权…", "Waiting for microphone permission…"],
  permissionReady: ["麦克风已获准，请再次按住说话。", "Microphone access was granted. Hold again to speak."],
  offline: ["连接已断开，请刷新页面；正在执行的操作可能仍在完成。", "Disconnected. Refresh the page; dispatched operations may still be completing."],
  trace: ["执行记录", "Activity"], you: ["你", "You"], assistant: ["助手", "Assistant"],
};
const TOOLS = {
  discover_tools: ["发现系统可用工具", "Discovering system tools"],
  places_categories: ["读取地图服务类别", "Discovering place categories"],
  places_nearby: ["查询附近地点", "Searching nearby places"],
  read_skill: ["读取操作流程", "Loading workflow"], ui_snapshot: ["查看页面状态", "Inspecting page"],
  ui_navigate: ["打开功能页面", "Opening page"], ui_action: ["操作页面控件", "Using page control"],
  maps_status: ["查看地图信息", "Inspecting map"], maps_locate: ["获取设备起点", "Getting device location"],
  places_search: ["搜索目的地", "Searching destination"], maps_plan: ["规划步行路线", "Planning walking route"],
  navigation_start: ["启动导航", "Starting navigation"], navigation_stop: ["停止导航", "Stopping navigation"],
  navigation_status: ["核实导航状态", "Checking navigation"], camera_start: ["开启摄像头", "Opening camera"],
  camera_stop: ["关闭摄像头", "Closing camera"], camera_status: ["核实摄像头状态", "Checking camera"],
  speech_status: ["查看语音输出", "Checking speech"], speech_voices: ["查看可用声音", "Listing voices"],
  speech_speak: ["提交朗读", "Requesting speech"], speech_stop: ["停止朗读", "Stopping speech"],
  vibration_status: ["查看震动支持", "Checking vibration"], vibration_test: ["提交震动", "Requesting vibration"],
  vibration_stop: ["停止震动", "Stopping vibration"],
};
const EVENT_NOTICES = {
  agent_explanation_unavailable: COPY.explanationUnavailable,
  agent_internal_error: COPY.assistantUnavailable,
  agent_timeout: COPY.assistantUnavailable,
  agent_not_configured: COPY.assistantUnavailable,
  qwen_network_error: COPY.assistantUnavailable,
  qwen_http_error: COPY.assistantUnavailable,
  qwen_response_limit: COPY.assistantUnavailable,
  qwen_invalid_response: COPY.assistantUnavailable,
  qwen_empty_response: COPY.assistantUnavailable,
  agent_busy: COPY.busyNotice,
  agent_capacity: [
    "语音助手会话已达到服务容量，请稍后刷新页面。",
    "The assistant session capacity is reached. Refresh the page later.",
  ],
};

/* Functionality: Select UI language. Inputs: bilingual tuple and document language.
 * Outputs: text; Logic: use page's existing language choice; Constraints: user/model text untouched. */
function label(pair) { return pair[document.documentElement.lang.startsWith("zh") ? 0 : 1]; }

/* Functionality: Localize a non-conversational event notice. Inputs: backend event. Outputs: UI text.
 * Logic: Translate the stable event code; never display backend detail as an assistant reply.
 * Constraints: Unknown codes produce generic status text and do not alter dialogue history. */
function eventNotice(event) {
  if (event.type === "cancelled") return label(COPY.cancelledNotice);
  return label(EVENT_NOTICES[event.code] || COPY.requestFailed);
}

/* Functionality: Explain bounded microphone failures in the selected interface language.
 * Inputs: Browser capture exception. Outputs: actionable localized guidance.
 * Logic: Distinguish permission, secure-context, missing-device, busy-device and unsupported cases.
 * Constraints: Native exception messages and implementation details are not shown to users. */
function recordingFailure(error) {
  const messages = {
    NotAllowedError: [
      "浏览器没有授予麦克风权限。若没有出现权限弹窗，请再次点按“按住说话”；若之前拒绝过，请在浏览器网站设置中允许麦克风。",
      "The browser did not grant microphone access. If no prompt appeared, tap and hold Speak again; if access was previously denied, allow microphone access in the browser's site settings.",
    ],
    SecurityError: [
      "麦克风需要安全的 HTTPS 页面；请在手机系统浏览器中打开安全链接。",
      "Microphone access requires a secure HTTPS page. Open the secure link in your phone's browser.",
    ],
    NotSupportedError: [
      "当前浏览器不支持麦克风录音，请使用手机系统浏览器。",
      "This browser does not support microphone recording. Use your phone's browser.",
    ],
    AudioContextError: [
      "麦克风已获准，但浏览器未能启动音频采集。请保持页面在前台并再次按住说话。",
      "Microphone access was granted, but the browser could not start audio capture. Keep the page in front and hold Speak again.",
    ],
    NotFoundError: ["没有检测到可用麦克风。", "No microphone was found on this device."],
    NotReadableError: [
      "麦克风暂时无法读取，请关闭其他正在使用麦克风的应用后重试。",
      "The microphone could not be read. Close other apps using it, then try again.",
    ],
  };
  return label(messages[error?.name] || [
    "无法启动麦克风录音。请检查设备和浏览器权限后重试。",
    "Microphone recording could not start. Check the device and browser permissions, then try again.",
  ]);
}

/* Functionality: Own assistant UI and one page-bound transport.
 * Logic: Correlated events reflect actual backend work; page navigation reattaches same identity.
 * State: productMode selects the formal hold gesture; busy/connected/recording/stage drive controls;
 * holdActive/holdToken/activePointerId isolate gestures; seen deduplicates replay; voice/socket/heartbeat own resources.
 * Constraints: No unsolicited microphone access or command replay; replayed replies never request speech. */
export class AgentPanel {
  /* Functionality: Build the page-specific assistant panel and bind explicit controls.
   * Inputs: document view marker, main element and browser event targets. Outputs: inserted panel/listeners.
   * Logic: Product mode places hold-to-send after the intro and uses text submission as an explicit
   * speech-activation gesture; other pages keep click-to-toggle capture.
   * Constraints: Static markup only; no microphone request until a user gesture. */
  constructor() {
    this.productMode = document.documentElement.dataset.assistView === "product";
    this.root = document.createElement("section");
    this.root.id = "agent-panel";
    this.root.className = "agent-panel";
    this.root.dataset.view = this.productMode ? "product" : "diagnostic";
    this.root.dataset.pressing = "false";
    this.root.setAttribute("data-language-original", "");
    this.root.innerHTML = `<div class="agent-heading"><h2 data-copy="title"></h2><span class="agent-badge">MVP</span></div>
      <p data-copy="help"></p><p id="agent-status" role="status"></p>
      <div id="agent-dialogue" class="agent-dialogue" role="log" aria-live="polite"></div>
      <form id="agent-form"><label for="agent-input" data-copy="input"></label>
      <textarea id="agent-input" rows="2" maxlength="2000"></textarea>
      <div class="agent-actions"><button id="agent-record" type="button"></button>
      <button id="agent-send" type="submit" data-copy="send"></button>
      <button id="agent-cancel" type="button" data-copy="cancel"></button>
      <button id="agent-clear" type="button" data-copy="clear"></button></div></form>
      <details><summary data-copy="trace"></summary><ol id="agent-activity"></ol></details>
      <p class="agent-privacy" data-copy="privacy"></p>`;
    const main = document.querySelector("main");
    const intro = this.productMode ? main.querySelector(".product-intro") : null;
    if (intro) intro.after(this.root);
    else main.prepend(this.root);
    if (this.productMode) {
      this.root.querySelector(".agent-badge").hidden = true;
      this.root.querySelector("#agent-clear").hidden = true;
      this.root.querySelector("details").hidden = true;
    }
    this.input = this.root.querySelector("#agent-input");
    this.status = this.root.querySelector("#agent-status");
    this.dialogue = this.root.querySelector("#agent-dialogue");
    this.activity = this.root.querySelector("#agent-activity");
    this.busy = false;
    this.connected = false;
    this.notice = null;
    this.recording = false;
    this.holdActive = false;
    this.holdToken = 0;
    this.activePointerId = null;
    this.seen = 0;
    this.stage = "connecting";
    this.voice = new VoiceInput(() => this.record());
    this.handlePressStart = this.handlePressStart.bind(this);
    this.handlePressEnd = this.handlePressEnd.bind(this);
    this.handlePressCancel = this.handlePressCancel.bind(this);
    this.root.querySelector("#agent-form").addEventListener("submit", event => {
      event.preventDefault();
      const text = this.input.value.trim();
      if (text && !this.busy && !this.recording) {
        if (this.productMode) window.dispatchEvent(new Event("prs-assist-voice-gesture"));
        this.submit({text}); this.input.value = "";
      }
    });
    const recordButton = this.root.querySelector("#agent-record");
    if (this.productMode) {
      recordButton.addEventListener("pointerdown", this.handlePressStart);
      recordButton.addEventListener("keydown", event => {
        if (!["Space", "Enter"].includes(event.code) || event.repeat) return;
        event.preventDefault(); this.handlePressStart(event);
      });
      recordButton.addEventListener("keyup", event => {
        if (!["Space", "Enter"].includes(event.code)) return;
        event.preventDefault(); this.handlePressEnd(event);
      });
      recordButton.addEventListener("contextmenu", event => event.preventDefault());
      window.addEventListener("pointerup", this.handlePressEnd);
      window.addEventListener("pointercancel", this.handlePressCancel);
    } else recordButton.addEventListener("click", () => this.record());
    this.root.querySelector("#agent-cancel").addEventListener("click", () => this.cancel());
    this.root.querySelector("#agent-clear").addEventListener("click", () => this.send({type: "clear"}));
    window.addEventListener("prs-ai-ready", () => this.connect());
    window.addEventListener("pagehide", () => {
      clearInterval(this.heartbeat);
      this.holdActive = false; this.holdToken++; this.activePointerId = null;
      this.socket?.close();
      this.voice.cancel().catch(error => console.error("Voice cleanup failed", error.name));
    });
    document.addEventListener("visibilitychange", () => {
      if (document.hidden && (this.holdActive || this.recording || this.stage === "permission")) this.cancel();
    });
    new MutationObserver(() => this.render()).observe(document.documentElement, {attributes: true, attributeFilter: ["lang"]});
    this.render();
    if (document.documentElement.dataset.aiConnected === "true") this.connect();
  }
  /* Functionality: Attach to backend after control registration. Inputs: tab credentials.
   * Outputs: one WebSocket; Logic: origin-relative URL/hello and audio cleanup on disconnect.
   * Constraints: no retries; pending permission callbacks cannot restart capture after close. */
  connect() {
    if (this.socket) return;
    const url = new URL("ws/agent/", new URL(document.documentElement.dataset.apiBase || "../", location.href));
    url.protocol = location.protocol === "https:" ? "wss:" : "ws:";
    this.socket = new WebSocket(url);
    this.socket.addEventListener("open", () => {
      this.send({type: "hello", session_id: sessionStorage.getItem("prs.ai.session"), token: sessionStorage.getItem("prs.agent.token")});
      this.heartbeat = setInterval(() => { if (this.connected) this.send({type: "ping"}); }, 15000);
    });
    this.socket.addEventListener("message", event => {
      try { this.receive(JSON.parse(event.data)); }
      catch (error) { this.fail("助手消息格式错误，请刷新页面。"); console.error("Agent event rejected", error.name); }
    });
    this.socket.addEventListener("close", () => {
      clearInterval(this.heartbeat);
      this.connected = this.recording = this.holdActive = false;
      this.holdToken++; this.activePointerId = null; this.root.dataset.pressing = "false";
      this.stage = "offline";
      this.voice.cancel().catch(error => console.error("Voice cleanup failed", error.name));
      this.render();
    });
    this.socket.addEventListener("error", () => console.error("Agent transport failed; manual refresh required"));
  }
  /* Functionality: Send once. Inputs: message object; Outputs: WS frame or visible failure.
   * Logic: Check readyState; Constraints: no queue or retry. */
  send(message) {
    if (this.socket?.readyState !== WebSocket.OPEN) { this.fail(label(COPY.offline)); return false; }
    this.socket.send(JSON.stringify(message));
    return true;
  }
  /* Functionality: Submit one explicit input. Inputs: {text} or {audio}; Outputs: pending UI.
   * Logic: Fresh request UUID; Constraints: never resubmit after disconnect or timeout. */
  submit(input) {
    if (this.send({type: "submit", request_id: crypto.randomUUID(), ...input})) {
      this.notice = null;
      this.busy = true; this.stage = input.audio ? "transcribing" : "thinking"; this.render();
    }
  }
  /* Functionality: Preserve click-to-toggle recording for diagnostics and finish capped captures.
   * Inputs: diagnostic click or the recorder's 20-second limit callback. Outputs: capture or submit.
   * Logic: Delegate permission, flush, encoding and error transitions to the shared lifecycle methods.
   * Constraints: Product recording begins and ends only through a held pointer/key; text is separate. */
  async record() {
    if (this.busy || !this.connected) return;
    if (this.recording) return this.finishRecording();
    if (this.stage === "permission") return;
    return this.startRecording();
  }
  /* Functionality: Request the microphone and begin one recorder generation.
   * Inputs: Optional holdToken from the product press that owns this capture. Outputs: recording state.
   * Logic: A released hold leaves the browser permission prompt open; if permission arrives after
   * release, stop the probe stream and ask the user to hold again. Explicit cancellation invalidates
   * the recorder generation. Constraints: No retries or fallback capture devices. */
  async startRecording(holdToken = null) {
    if (this.busy || !this.connected) return;
    this.stage = "permission"; this.render();
    try {
      const started = await this.voice.start();
      if (holdToken !== null && (holdToken !== this.holdToken || !this.holdActive)) {
        if (started) await this.voice.cancel();
        this.recording = false;
        if (this.stage === "permission") this.stage = this.connected ? "ready" : "offline";
        if (started) this.fail(label(COPY.permissionReady));
        else this.render();
        return;
      }
      this.recording = started;
      this.stage = !this.connected ? "offline" : this.recording ? "recording" : "ready";
      this.render();
    } catch (error) {
      this.busy = this.recording = false; this.stage = this.connected ? "ready" : "offline";
      this.fail(recordingFailure(error));
      console.error("Voice capture failed", {type: error.name});
    }
  }
  /* Functionality: Flush, encode and submit the current recording once.
   * Inputs: Active VoiceInput state and current WebSocket connection. Outputs: one audio request or error.
   * Logic: Release input state before encoding, then submit only while the socket remains connected.
   * Constraints: Short/invalid capture errors remain visible; no re-recording, queue or automatic retry. */
  async finishRecording() {
    if (!this.recording) return;
    this.holdActive = false; this.holdToken++; this.activePointerId = null;
    this.root.dataset.pressing = "false";
    this.recording = false; this.busy = true; this.stage = "transcribing"; this.render();
    try {
      const audio = await this.voice.finish();
      this.busy = false; this.stage = this.connected ? "ready" : "offline"; this.render();
      if (this.connected) this.submit({audio});
    } catch (error) {
      this.busy = this.recording = false; this.stage = this.connected ? "ready" : "offline";
      this.fail(`录音失败（${error.name}）。${error.message}`);
      console.error("Voice capture failed", {type: error.name});
    }
  }
  /* Functionality: Start formal-view capture on a primary pointer or keyboard press.
   * Inputs: Pointer/keydown event, connection and current request state. Outputs: one owned capture.
   * Logic: Capture pointer identity, expose pressed state and bind permission to this exact hold.
   * Constraints: Ignore nonprimary/repeated presses; microphone access is never requested at page load. */
  handlePressStart(event) {
    if (!this.productMode || this.busy || !this.connected || this.holdActive || this.stage === "permission") return;
    if (event.type === "pointerdown" && event.button !== 0) return;
    event.preventDefault();
    this.holdActive = true;
    this.activePointerId = Number.isInteger(event.pointerId) ? event.pointerId : null;
    const holdToken = ++this.holdToken;
    this.root.dataset.pressing = "true";
    this.render();
    void this.startRecording(holdToken);
  }
  /* Functionality: Finish a held recording on release while preserving a pending permission prompt.
   * Inputs: Matching pointerup/keyup event and current hold/recorder state. Outputs: submitted audio or an inactive permission probe.
   * Logic: Release ends active capture; during permission setup the browser request must continue
   * so a mobile tap can resolve its native prompt. A granted late stream is stopped by startRecording.
   * Constraints: Secondary pointers and unrelated key releases cannot finish this capture. */
  handlePressEnd(event) {
    if (!this.productMode || !this.holdActive) return;
    if (event.type === "pointerup" && this.activePointerId !== event.pointerId) return;
    if (event.type === "keyup" && this.activePointerId !== null) return;
    event.preventDefault();
    this.holdActive = false; this.activePointerId = null; this.holdToken++;
    this.root.dataset.pressing = "false";
    if (this.recording) {
      const finishing = this.finishRecording();
      window.dispatchEvent(new Event("prs-assist-voice-gesture"));
      void finishing;
      return;
    }
    if (this.stage === "permission") return;
    this.stage = this.connected ? "ready" : "offline";
    this.render();
  }
  /* Functionality: End hold ownership when the browser cancels an active pointer gesture.
   * Inputs: Matching pointercancel event. Outputs: released hold or microphone and ready/offline state.
   * Logic: Keep an open native permission prompt alive; otherwise cancel capture and never submit a
   * gesture the browser interrupted. A granted late stream is stopped by startRecording.
   * Constraints: An unrelated pointer cancellation cannot stop the active recording. */
  handlePressCancel(event) {
    if (!this.productMode || !this.holdActive || this.activePointerId !== event.pointerId) return;
    event.preventDefault();
    if (this.stage === "permission") {
      this.holdActive = false; this.holdToken++; this.activePointerId = null;
      this.root.dataset.pressing = "false"; this.render();
      return;
    }
    void this.cancel();
  }
  /* Functionality: Cancel a held capture or pending agent request. Inputs: button/visibility event.
   * Outputs: microphone cleanup or one backend cancellation. Logic: invalidate the active hold before async cleanup.
   * Constraints: backend cancellation cannot undo already dispatched device operations. */
  async cancel() {
    const local = this.holdActive || this.recording || this.stage === "permission";
    this.holdActive = false; this.holdToken++; this.activePointerId = null;
    this.root.dataset.pressing = "false";
    this.recording = false;
    try { await this.voice.cancel(); }
    catch (error) { console.error("Voice cleanup failed", error.name); }
    if (local) { this.stage = this.connected ? "ready" : "offline"; this.render(); }
    else this.send({type: "cancel"});
  }
  /* Functionality: Apply ordered backend events.
   * Inputs: Parsed event and replayed (defaults false) history flag. Outputs: UI state and live-reply notification.
   * Logic: Ignore replay duplicates; show configured intent labels before tool activity;
   * distinguish acknowledgement, reply, failure and idle.
   * Constraints: Replayed history never speaks; tool events show activity, never fabricated completion. */
  receive(event, replayed = false) {
    if (event.type === "ready") {
      for (const previous of event.events || []) this.receive(previous, true);
      this.connected = true; this.busy = event.busy; this.stage = event.busy ? "thinking" : "ready";
    }
    if (event.seq && event.seq <= this.seen) return;
    if (event.seq) this.seen = event.seq;
    if (event.type === "working") { this.busy = true; this.stage = event.stage; }
    let liveReply = null;
    if (event.type === "user" || event.type === "reply") {
      this.append(event.type === "user" ? "you" : "assistant", event.text);
      if (event.type === "reply" && !replayed) liveReply = event.text;
    }
    if (event.type === "intent") {
      const item = document.createElement("li");
      const route = event.route === "general" && event.unknown_policy === "reject" ? "unsupported" : event.route;
      item.textContent = label(COPY[route] || COPY.unsupported) + (route === "system"
        ? (event.labels || []).map(pair => label([pair.zh, pair.en])).join(" / ") : "");
      this.activity.append(item);
      while (this.activity.children.length > 60) this.activity.firstChild.remove();
    }
    if (event.type === "tool_start") {
      const item = document.createElement("li");
      item.textContent = label(TOOLS[event.name] || [event.name, event.name]);
      this.activity.append(item);
      while (this.activity.children.length > 60) this.activity.firstChild.remove();
      this.stage = "thinking";
    }
    if (["error", "cancelled", "rejected"].includes(event.type)) {
      this.notice = eventNotice(event);
      if (event.type === "rejected" && event.code !== "agent_busy") { this.busy = false; this.stage = "ready"; }
    }
    if (event.type === "idle") { this.busy = false; this.stage = "ready"; }
    if (event.type === "cleared") { this.notice = null; this.dialogue.replaceChildren(); this.activity.replaceChildren(); }
    this.root.dataset.busy = String(this.busy);
    this.render();
    if (liveReply) window.dispatchEvent(new CustomEvent("prs-agent-reply", {detail: {text: liveReply}}));
  }
  /* Functionality: Display dialogue as text. Inputs: role/text; Outputs: bounded log entry.
   * Logic: textContent prevents markup injection; Constraints: no dialogue storage. */
  append(role, text) {
    const entry = document.createElement("p");
    entry.className = `agent-message ${role}`;
    const name = document.createElement("strong");
    name.textContent = label(COPY[role]);
    entry.append(name, document.createTextNode(text));
    this.dialogue.append(entry);
    while (this.dialogue.children.length > 40) this.dialogue.firstChild.remove();
    this.dialogue.scrollTop = this.dialogue.scrollHeight;
  }
  /* Functionality: Render language, hold-to-send status and control availability.
   * Inputs: current panel mode, connection, request, press and recorder state. Outputs: accessible text/disabled flags.
   * Logic: Product mode reports release-to-send while recording; diagnostics preserve click labels.
   * Constraints: Rendering never requests media, submits a command or changes the user's text. */
  render() {
    for (const element of this.root.querySelectorAll("[data-copy]")) element.textContent = label(COPY[element.dataset.copy]);
    this.root.querySelector('[data-copy="help"]').textContent = label(this.productMode ? COPY.productHelp : COPY.help);
    const status = this.productMode && this.recording ? COPY.recordingProduct
      : COPY[this.connected ? this.stage : this.stage === "offline" ? "offline" : "connecting"] || COPY.ready;
    this.status.textContent = this.notice || label(status);
    this.input.disabled = !this.connected || this.busy || this.recording || this.holdActive || this.stage === "permission";
    this.root.querySelector("#agent-send").disabled = this.input.disabled;
    const record = this.root.querySelector("#agent-record");
    record.textContent = label(this.productMode
      ? this.recording ? COPY.release : this.stage === "permission" ? COPY.permission : COPY.hold
      : COPY[this.recording ? "finish" : "record"]);
    if (this.productMode) record.setAttribute("aria-pressed", String(this.holdActive));
    record.disabled = !this.connected || this.busy || !this.productMode && this.stage === "permission";
    this.root.querySelector("#agent-cancel").disabled = !this.connected ||
      (!this.busy && !this.recording && !this.holdActive && this.stage !== "permission");
    this.root.querySelector("#agent-clear").disabled = this.input.disabled;
    this.root.dataset.busy = String(this.busy);
    this.root.dataset.connected = String(this.connected);
  }
  /* Functionality: Show a local, non-conversational status. Inputs: UI-safe text; Outputs: status.
   * Logic: Keep client failures out of assistant dialogue; Constraints: no auto-resubmission. */
  fail(text) { this.notice = text; this.render(); }
}

const panel = typeof document !== "undefined" && document.querySelector("main") ? new AgentPanel() : null;
