/* Responsibilities: Shared voice/text assistant panel, progress and cross-page conversation.
 * Implementation: One bound WebSocket, ordered replay and explicit request ownership; both views
 * use click-to-toggle voice capture with a prompt-preserving first permission request. Safe DOM text rendering and in-memory audio;
 * live replies and a matching pending reply restored after reconnect notify speech integration.
 * Failures render as status, never dialogue.
 * Disconnect releases microphone. A visible page reconnects with bounded backoff without resubmitting
 * an in-flight command; only a reply matching the page's pending request may speak once after replay.
 * Related Modules: ai-control.js, voice-input.js, agent.css, integration/assist-mvp.js;
 * SystemCode/backend/core/agent/transport.
 * Declaration Index: label; recordingFailure; eventNotice; AgentPanel; AgentPanel.constructor; AgentPanel.connect;
 * AgentPanel.handleDisconnect; AgentPanel.scheduleReconnect; AgentPanel.send; AgentPanel.submit;
 * AgentPanel.record; AgentPanel.startRecording;
 * AgentPanel.finishRecording; AgentPanel.handleRecordClick; AgentPanel.cancel;
 * AgentPanel.receive; AgentPanel.append; AgentPanel.render; AgentPanel.fail.
 * Variable Index: COPY — bilingual interface and media setup text; TOOLS — discovery/action labels;
 * panel — singleton; AgentPanel.productMode selects the formal tap-to-toggle interaction;
 * recording/root.dataset.recording expose active capture; the product finish click dispatches
 * prs-assist-voice-gesture after requesting capture flush, preserving browser speech activation;
 * prs-agent-reply notifies speech integration for live replies and one matching in-flight replay;
 * RECONNECT_BASE_MS/RECONNECT_MAX_MS/CONNECTION_TIMEOUT_MS bound transport recovery;
 * pendingRequestId/pendingReplyNotified retain request ownership across a socket handoff;
 * pingPending/heartbeatTimeout detect a half-open socket after the backend keepalive interval;
 * EVENT_NOTICES maps transport/runtime codes to localized, non-conversational status;
 * eventNotice resolves the visible notice from that map.
 */
import {VoiceInput} from "./voice-input.js";

const COPY = {
  title: ["语音助手", "Voice assistant"], help: ["说出或输入你想做的事，例如：开启导航、关闭摄像头。", "Say or type a request, such as start navigation or close the camera."],
  productHelp: ["点按一次开始录音，再点按结束并发送。首次使用请在弹窗中允许麦克风；也可以输入指令。", "Tap once to start recording, then tap again to finish and send. Allow microphone access when prompted. You can also type a request."],
  privacy: ["录音最长20秒；音频与对话发送 Qwen 处理，应用不保存录音。定位和设备权限仍由你授权。", "Up to 20 seconds. Audio and conversation are processed by Qwen; this app does not save recordings. Device permissions remain yours."],
  send: ["发送", "Send"], record: ["点击说话", "Speak"], startRecording: ["开始录音", "Start recording"],
  finish: ["结束并发送", "Finish & send"],
  recordingProduct: ["正在录音，再次点按结束并发送（最长20秒）", "Recording. Tap again to finish and send within 20 seconds."],
  cancel: ["取消", "Cancel"], clear: ["清空对话", "Clear conversation"], input: ["输入指令或补充信息", "Enter a request or missing information"],
  connecting: ["正在连接助手…", "Connecting…"], ready: ["可以说话或输入指令", "Ready for your request"],
  thinking: ["正在处理指令…", "Processing your request…"], transcribing: ["正在识别语音…", "Transcribing…"],
  routing: ["正在识别操作意图…", "Identifying your request…"],
  system: ["已识别操作：", "Recognized operations: "], clarify: ["需要明确操作意图", "Operation needs clarification"],
  general: ["通用问答", "General question"], unsupported: ["未命中已启用的能力", "Outside enabled capabilities"],
  requestFailed: ["请求未完成，请稍后重试。", "The request did not complete. Try again later."],
  contextLimit: ["对话上下文已达到上限，请清空对话后重试。", "The conversation reached its context limit. Clear the conversation and retry."],
  explanationUnavailable: ["助手暂时无法生成故障说明，请稍后重试。", "The assistant could not explain this failure. Try again later."],
  assistantUnavailable: ["助手服务暂时不可用。", "The assistant service is unavailable."],
  cancelledNotice: ["请求已取消。", "Request cancelled."],
  busyNotice: ["上一条指令仍在处理中。", "The previous request is still running."],
  recording: ["正在录音，点击结束并发送（最长20秒）", "Recording. Finish and send within 20 seconds."],
  permission: ["等待麦克风授权…", "Waiting for microphone permission…"],
  offline: ["连接已断开，正在尝试恢复；正在执行的操作可能仍在完成。", "Connection lost. Reconnecting; dispatched operations may still be completing."],
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
  context_limit: COPY.contextLimit,
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
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 15000;
const CONNECTION_TIMEOUT_MS = 12000;

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
      "浏览器没有授予麦克风权限。若没有出现权限弹窗，请再次点按语音按钮；若之前拒绝过，请在浏览器网站设置中允许麦克风。",
      "The browser did not grant microphone access. If no prompt appeared, tap the voice button again; if access was previously denied, allow microphone access in the browser's site settings.",
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
      "麦克风已获准，但浏览器未能启动音频采集。请保持页面在前台并再次点按语音按钮。",
      "Microphone access was granted, but the browser could not start audio capture. Keep the page in front and tap the voice button again.",
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
 * State: productMode selects formal tap-to-toggle labels; busy/connected/recording/stage drive controls;
 * seen deduplicates replay; voice/socket/heartbeat own resources.
 * Constraints: No unsolicited microphone access or command replay; unrelated replayed replies never request speech. */
export class AgentPanel {
  /* Functionality: Build the page-specific assistant panel and bind explicit controls.
   * Inputs: document view marker, main element and browser event targets. Outputs: inserted panel/listeners.
   * Logic: Product mode places tap-to-toggle recording after the intro; recording-button and text
 * finish-recording and text-submission clicks remain explicit speech-activation gestures.
   * Constraints: Static markup only; no microphone request until a user gesture. */
  constructor() {
    this.productMode = document.documentElement.dataset.assistView === "product";
    this.root = document.createElement("section");
    this.root.id = "agent-panel";
    this.root.className = "agent-panel";
    this.root.dataset.view = this.productMode ? "product" : "diagnostic";
    this.root.dataset.recording = "false";
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
    this.seen = 0;
    this.stage = "connecting";
    this.socket = null;
    this.heartbeat = null;
    this.heartbeatTimeout = null;
    this.pingPending = false;
    this.connectionTimer = null;
    this.reconnectTimer = null;
    this.reconnectAttempt = 0;
    this.connectionGeneration = 0;
    this.leaving = false;
    this.pendingRequestId = null;
    this.pendingReplyNotified = false;
    this.voice = new VoiceInput(() => this.record());
    this.root.querySelector("#agent-form").addEventListener("submit", event => {
      event.preventDefault();
      const text = this.input.value.trim();
      if (text && !this.busy && !this.recording) {
        if (this.productMode) window.dispatchEvent(new Event("prs-assist-voice-gesture"));
        this.submit({text}); this.input.value = "";
      }
    });
    const recordButton = this.root.querySelector("#agent-record");
    recordButton.addEventListener("click", () => this.handleRecordClick());
    this.root.querySelector("#agent-cancel").addEventListener("click", () => this.cancel());
    this.root.querySelector("#agent-clear").addEventListener("click", () => this.send({type: "clear"}));
    window.addEventListener("prs-ai-ready", () => this.connect());
    window.addEventListener("pagehide", () => {
      this.leaving = true;
      clearTimeout(this.reconnectTimer); this.reconnectTimer = null;
      clearTimeout(this.connectionTimer); this.connectionTimer = null;
      clearInterval(this.heartbeat);
      this.heartbeat = null;
      clearTimeout(this.heartbeatTimeout); this.heartbeatTimeout = null; this.pingPending = false;
      this.socket?.close();
      this.voice.cancel().catch(error => console.error("Voice cleanup failed", error.name));
    });
    window.addEventListener("pageshow", () => {
      this.leaving = false;
      if (this.stage === "offline") this.connect();
    });
    document.addEventListener("visibilitychange", () => {
      if (document.hidden && (this.recording || this.stage === "permission")) this.cancel();
      if (!document.hidden && this.stage === "offline") this.scheduleReconnect();
    });
    new MutationObserver(() => this.render()).observe(document.documentElement, {attributes: true, attributeFilter: ["lang"]});
    this.render();
    if (document.documentElement.dataset.aiConnected === "true") this.connect();
  }
  /* Functionality: Attach to backend after control registration and recover visible transports.
   * Inputs: tab credentials and current page lifecycle. Outputs: one WebSocket and heartbeat.
   * Logic: each socket owns a generation; stale callbacks cannot mutate a newer connection. A retry
   * sends only hello and replays retained state, never a previously dispatched command.
   * Constraints: pagehide disables recovery; pending permission callbacks cannot restart capture. */
  connect() {
    if (this.leaving || this.reconnectTimer !== null) return;
    if (this.socket && (this.socket.readyState === undefined ||
        this.socket.readyState === WebSocket.CONNECTING || this.socket.readyState === WebSocket.OPEN)) return;
    const url = new URL("ws/agent/", new URL(document.documentElement.dataset.apiBase || "../", location.href));
    url.protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(url), generation = ++this.connectionGeneration;
    this.socket = socket; this.stage = "connecting"; this.render();
    const current = () => this.socket === socket && this.connectionGeneration === generation;
    clearTimeout(this.connectionTimer);
    this.connectionTimer = setTimeout(() => {
      if (current() && !this.connected) socket.close();
    }, CONNECTION_TIMEOUT_MS);
    socket.addEventListener("open", () => {
      if (!current()) return;
      clearTimeout(this.connectionTimer); this.connectionTimer = null;
      this.connectionTimer = setTimeout(() => {
        if (current() && !this.connected) socket.close();
      }, CONNECTION_TIMEOUT_MS);
      this.pingPending = false;
      if (!this.send({type: "hello", session_id: sessionStorage.getItem("prs.ai.session"), token: sessionStorage.getItem("prs.agent.token")})) return;
      clearInterval(this.heartbeat);
      this.heartbeat = setInterval(() => {
        if (!current() || !this.connected || this.pingPending) return;
        if (this.send({type: "ping"})) {
          this.pingPending = true;
          this.heartbeatTimeout = setTimeout(() => {
            if (current() && this.pingPending) socket.close();
          }, 30000);
        }
      }, 15000);
    });
    socket.addEventListener("message", event => {
      if (!current()) return;
      try { this.receive(JSON.parse(event.data)); }
      catch (error) { this.fail("助手消息格式错误，请刷新页面。"); console.error("Agent event rejected", error.name); }
    });
    socket.addEventListener("close", () => this.handleDisconnect(socket, generation));
    socket.addEventListener("error", () => {
      if (current()) console.error("Agent transport failed; waiting for reconnect");
    });
  }
  /* Functionality: Release the exact closed or failed socket and its local resources.
   * Inputs: socket identity and connection generation. Outputs: offline state and at most one retry.
   * Logic: Ignore stale sockets, clear all connection timers, cancel capture and schedule recovery.
   * Constraints: Never resubmit work; pagehide and hidden-page gates suppress reconnection. */
  handleDisconnect(socket, generation) {
    if (this.socket !== socket || this.connectionGeneration !== generation) return;
    this.socket = null;
    clearTimeout(this.connectionTimer); this.connectionTimer = null;
    clearInterval(this.heartbeat); this.heartbeat = null;
    clearTimeout(this.heartbeatTimeout); this.heartbeatTimeout = null; this.pingPending = false;
    this.connected = this.recording = false;
    this.stage = "offline";
    this.voice.cancel().catch(error => console.error("Voice cleanup failed", error.name));
    this.render(); this.scheduleReconnect();
  }
  /* Functionality: Schedule one visible-page transport recovery attempt.
   * Inputs: current socket state and page visibility. Outputs: one bounded retry timer.
   * Logic: increase delay exponentially to a fixed cap; connect replays session events without
   * resubmitting work. Constraints: no command, recording or synthesis retry is performed. */
  scheduleReconnect() {
    if (this.leaving || this.reconnectTimer !== null || this.connected || document.hidden) return;
    const delay = Math.min(RECONNECT_MAX_MS, RECONNECT_BASE_MS * 2 ** Math.min(this.reconnectAttempt, 4));
    this.reconnectAttempt++;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      if (!document.hidden && !this.leaving) this.connect();
    }, delay);
  }
  /* Functionality: Send once. Inputs: message object; Outputs: WS frame or visible failure.
   * Logic: Check the current socket's readyState and classify synchronous browser transport failures;
   * closing the affected socket lets the normal bounded reconnect path recover the conversation.
   * Constraints: controls are never queued or retried. */
  send(message) {
    if (this.socket?.readyState !== WebSocket.OPEN) { this.fail(label(COPY.offline)); return false; }
    try {
      this.socket.send(JSON.stringify(message));
      return true;
    } catch (error) {
      console.error("Agent frame send failed", {type: message.type, error_type: error?.name});
      this.fail(label(COPY.offline));
      const socket = this.socket, generation = this.connectionGeneration;
      try { socket.close(); } catch (closeError) {
        console.error("Agent transport close failed", {error_type: closeError?.name});
      }
      this.handleDisconnect(socket, generation);
      return false;
    }
  }
  /* Functionality: Submit one explicit input. Inputs: {text} or {audio}; Outputs: pending UI.
   * Logic: Retain the request UUID so matching replay can complete the active page request.
   * Constraints: never resubmit after disconnect or timeout. */
  submit(input) {
    const requestId = crypto.randomUUID();
    if (this.send({type: "submit", request_id: requestId, ...input})) {
      this.pendingRequestId = requestId; this.pendingReplyNotified = false;
      this.notice = null;
      this.busy = true; this.stage = input.audio ? "transcribing" : "thinking"; this.render();
    }
  }
  /* Functionality: Toggle recording for button clicks and finish capped captures.
   * Inputs: current connection, busy, recording and permission state; VoiceInput calls it at the 20-second cap.
   * Outputs: capture start, finish or no-op. Logic: Delegate media work and transitions to shared lifecycle methods.
   * Constraints: Capture begins only after an explicit user action; text is submitted separately. */
  async record() {
    if (this.busy || !this.connected) return;
    if (this.recording) return this.finishRecording();
    if (this.stage === "permission") return;
    return this.startRecording();
  }
  /* Functionality: Handle the assistant recording control's click-to-toggle action.
   * Inputs: Native button click, page mode and current recording state. Outputs: capture transition and,
   * on the product page's finish click, a synchronous speech-activation event. Logic: Request the
   * recorder flush before activating speech, then dispatch activation within the same click stack.
   * Constraints: Capture still starts only after an explicit click. */
  handleRecordClick() {
    if (this.productMode && this.recording) {
      const finishing = this.record();
      window.dispatchEvent(new Event("prs-assist-voice-gesture"));
      void finishing;
      return;
    }
    void this.record();
  }
  /* Functionality: Request the microphone and begin one recorder generation.
   * Inputs: Current connection and browser capture APIs. Outputs: recording state.
   * Logic: Await permission and worklet setup; cancellation invalidates the recorder generation and
   * releases any late-granted stream. Constraints: No retries or fallback capture devices. */
  async startRecording() {
    if (this.busy || !this.connected) return;
    this.stage = "permission"; this.render();
    try {
      const started = await this.voice.start();
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
  /* Functionality: Cancel a capture or pending agent request. Inputs: current capture/stage/request state.
   * Outputs: microphone cleanup or one backend cancellation. Logic: invalidate local capture before async cleanup.
   * Constraints: backend cancellation cannot undo already dispatched device operations. */
  async cancel() {
    const local = this.recording || this.stage === "permission";
    this.recording = false;
    try { await this.voice.cancel(); }
    catch (error) { console.error("Voice cleanup failed", error.name); }
    if (local) { this.stage = this.connected ? "ready" : "offline"; this.render(); }
    else this.send({type: "cancel"});
  }
  /* Functionality: Apply ordered backend events.
   * Inputs: Parsed event and replayed (defaults false) history flag. Outputs: UI state and live-reply notification.
   * Logic: Ignore replay duplicates; show configured intent labels before tool activity;
   * distinguish acknowledgement, reply, failure and idle. A replayed reply is speakable only
   * when it matches this page's still-pending request and has not already been announced.
   * Constraints: unrelated history never speaks; tool events show activity, never fabricated completion. */
  receive(event, replayed = false) {
    if (event.type === "pong") {
      this.pingPending = false;
      clearTimeout(this.heartbeatTimeout); this.heartbeatTimeout = null;
      return;
    }
    if (event.type === "ready") {
      clearTimeout(this.connectionTimer); this.connectionTimer = null;
      this.reconnectAttempt = 0;
      this.connected = true; this.busy = event.busy; this.stage = event.busy ? "thinking" : "ready";
      for (const previous of event.events || []) this.receive(previous, true);
      if (!event.busy && this.pendingRequestId) {
        this.pendingRequestId = null; this.pendingReplyNotified = false;
      }
    }
    if (event.seq && event.seq <= this.seen) return;
    if (event.seq) this.seen = event.seq;
    if (event.type === "working") { this.busy = true; this.stage = event.stage; }
    let liveReply = null;
    if (event.type === "user" || event.type === "reply") {
      this.append(event.type === "user" ? "you" : "assistant", event.text);
      if (event.type === "reply" && (!replayed ||
          event.request_id === this.pendingRequestId && !this.pendingReplyNotified)) {
        liveReply = event.text;
        if (event.request_id === this.pendingRequestId) this.pendingReplyNotified = true;
      }
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
    if (event.type === "idle") {
      this.busy = false; this.stage = "ready";
      if (!this.pendingRequestId || event.request_id === this.pendingRequestId) {
        this.pendingRequestId = null; this.pendingReplyNotified = false;
      }
    }
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
  /* Functionality: Render language, recording status and control availability.
   * Inputs: current panel mode, connection, request and recorder state. Outputs: accessible text/disabled flags.
   * Logic: Product mode reports tap-to-send while recording; diagnostics preserve their click labels.
   * Constraints: Rendering never requests media, submits a command or changes the user's text. */
  render() {
    for (const element of this.root.querySelectorAll("[data-copy]")) element.textContent = label(COPY[element.dataset.copy]);
    this.root.querySelector('[data-copy="help"]').textContent = label(this.productMode ? COPY.productHelp : COPY.help);
    const status = this.productMode && this.recording ? COPY.recordingProduct
      : COPY[this.connected ? this.stage : this.stage === "offline" ? "offline" : "connecting"] || COPY.ready;
    this.status.textContent = this.notice || label(status);
    this.input.disabled = !this.connected || this.busy || this.recording || this.stage === "permission";
    this.root.querySelector("#agent-send").disabled = this.input.disabled;
    const record = this.root.querySelector("#agent-record");
    record.textContent = label(this.productMode
      ? this.recording ? COPY.finish : this.stage === "permission" ? COPY.permission : COPY.startRecording
      : COPY[this.recording ? "finish" : "record"]);
    this.root.dataset.recording = String(this.recording);
    record.disabled = !this.connected || this.busy || this.stage === "permission";
    this.root.querySelector("#agent-cancel").disabled = !this.connected ||
      (!this.busy && !this.recording && this.stage !== "permission");
    this.root.querySelector("#agent-clear").disabled = this.input.disabled;
    this.root.dataset.busy = String(this.busy);
    this.root.dataset.connected = String(this.connected);
  }
  /* Functionality: Show a local, non-conversational status. Inputs: UI-safe text; Outputs: status.
   * Logic: Keep client failures out of assistant dialogue; Constraints: no auto-resubmission. */
  fail(text) { this.notice = text; this.render(); }
}

const panel = typeof document !== "undefined" && document.querySelector("main") ? new AgentPanel() : null;
