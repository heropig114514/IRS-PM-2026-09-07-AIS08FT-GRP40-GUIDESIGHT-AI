/* Responsibilities: Shared voice/text assistant panel, progress and cross-page conversation.
 * Implementation: One bound WebSocket, ordered replay, visible intent routing and tool activity,
 * explicit submission/cancel/reset;
 * safe DOM text rendering and in-memory audio; disconnect releases microphone. No auto reconnect.
 * Related Modules: ai-control.js, voice-input.js, agent.css; backend/core/agent/transport.
 * Declaration Index: label; AgentPanel; AgentPanel.constructor; AgentPanel.connect;
 * AgentPanel.send; AgentPanel.submit; AgentPanel.record; AgentPanel.cancel;
 * AgentPanel.receive; AgentPanel.append; AgentPanel.render; AgentPanel.fail.
 * Variable Index: COPY — bilingual interface text; TOOLS — discovery/action labels;
 * panel — singleton.
 */
import {VoiceInput} from "./voice-input.js";

const COPY = {
  title: ["语音助手", "Voice assistant"], help: ["说出或输入你想做的事，例如：开启导航、关闭摄像头。", "Say or type a request, such as start navigation or close the camera."],
  privacy: ["录音最长20秒；音频与对话发送 Qwen 处理，应用不保存录音。定位和设备权限仍由你授权。", "Up to 20 seconds. Audio and conversation are processed by Qwen; this app does not save recordings. Device permissions remain yours."],
  send: ["发送", "Send"], record: ["点击说话", "Speak"], finish: ["结束并发送", "Finish & send"],
  cancel: ["取消", "Cancel"], clear: ["清空对话", "Clear conversation"], input: ["输入指令或补充信息", "Enter a request or missing information"],
  connecting: ["正在连接助手…", "Connecting…"], ready: ["可以说话或输入指令", "Ready for your request"],
  thinking: ["正在处理指令…", "Processing your request…"], transcribing: ["正在识别语音…", "Transcribing…"],
  routing: ["正在识别操作意图…", "Identifying your request…"],
  system: ["已识别操作：", "Recognized operations: "], clarify: ["需要明确操作意图", "Operation needs clarification"],
  general: ["通用问答", "General question"], unsupported: ["未命中已启用的能力", "Outside enabled capabilities"],
  recording: ["正在录音，点击结束并发送（最长20秒）", "Recording. Finish and send within 20 seconds."],
  permission: ["等待麦克风授权…", "Waiting for microphone permission…"],
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

/* Functionality: Select UI language. Inputs: bilingual tuple and document language.
 * Outputs: text; Logic: use page's existing language choice; Constraints: user/model text untouched. */
function label(pair) { return pair[document.documentElement.lang.startsWith("zh") ? 0 : 1]; }

/* Functionality: Own assistant UI and one page-bound transport.
 * Logic: Correlated events reflect actual backend work; page navigation reattaches same identity.
 * State: busy/connected/recording/stage drive controls; seen deduplicates replay;
 * voice/socket/heartbeat own resources; root/input/status/dialogue/activity reference the DOM.
 * Constraints: No unsolicited microphone access, automatic reply speech or command replay. */
export class AgentPanel {
  /* Functionality: Build panel and bind explicit user controls. Inputs: page DOM.
   * Outputs: UI/listeners; Logic: static markup then safe text; Constraints: no model HTML. */
  constructor() {
    this.root = document.createElement("section");
    this.root.id = "agent-panel";
    this.root.className = "agent-panel";
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
    document.querySelector("main").prepend(this.root);
    this.input = this.root.querySelector("#agent-input");
    this.status = this.root.querySelector("#agent-status");
    this.dialogue = this.root.querySelector("#agent-dialogue");
    this.activity = this.root.querySelector("#agent-activity");
    this.busy = false;
    this.connected = false;
    this.recording = false;
    this.seen = 0;
    this.stage = "connecting";
    this.voice = new VoiceInput(() => this.record());
    this.root.querySelector("#agent-form").addEventListener("submit", event => {
      event.preventDefault();
      const text = this.input.value.trim();
      if (text && !this.busy && !this.recording) { this.submit({text}); this.input.value = ""; }
    });
    this.root.querySelector("#agent-record").addEventListener("click", () => this.record());
    this.root.querySelector("#agent-cancel").addEventListener("click", () => this.cancel());
    this.root.querySelector("#agent-clear").addEventListener("click", () => this.send({type: "clear"}));
    window.addEventListener("prs-ai-ready", () => this.connect());
    window.addEventListener("pagehide", () => {
      clearInterval(this.heartbeat);
      this.socket?.close();
      this.voice.cancel().catch(error => console.error("Voice cleanup failed", error.name));
    });
    document.addEventListener("visibilitychange", () => {
      if (document.hidden && (this.recording || this.stage === "permission")) this.cancel();
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
      this.connected = this.recording = false; this.stage = "offline";
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
      this.busy = true; this.stage = input.audio ? "transcribing" : "thinking"; this.render();
    }
  }
  /* Functionality: Toggle user recording. Inputs: click or explicit 20s cap callback.
   * Outputs: capture or WAV submission; Logic: flush before submit, show native failure.
   * Constraints: Text input remains a separate explicit option, never an automatic fallback. */
  async record() {
    if (this.busy || !this.connected) return;
    try {
      if (this.recording) {
        this.recording = false; this.busy = true; this.stage = "transcribing"; this.render();
        const audio = await this.voice.finish();
        if (this.connected) this.submit({audio});
      } else {
        this.stage = "permission"; this.render();
        this.recording = await this.voice.start();
        this.stage = !this.connected ? "offline" : this.recording ? "recording" : "ready"; this.render();
      }
    } catch (error) {
      this.busy = this.recording = false; this.stage = this.connected ? "ready" : "offline";
      this.fail(`录音失败（${error.name}）。${error.name === "NotAllowedError" ? "请允许麦克风权限。" : error.message}`);
      console.error("Voice capture failed", {type: error.name});
    }
  }
  /* Functionality: Cancel recording/future agent steps. Inputs: button/visibility event.
   * Outputs: cleanup and optional cancel; Logic: microphone generation invalidated first.
   * Constraints: backend cancellation cannot undo already dispatched device operations. */
  async cancel() {
    const local = this.recording || this.stage === "permission";
    this.recording = false;
    try { await this.voice.cancel(); }
    catch (error) { console.error("Voice cleanup failed", error.name); }
    if (local) { this.stage = this.connected ? "ready" : "offline"; this.render(); }
    else this.send({type: "cancel"});
  }
  /* Functionality: Apply ordered backend events. Inputs: parsed event; Outputs: UI state.
   * Logic: Ignore replay duplicates; show configured intent labels before tool activity;
   * distinguish acknowledgement, reply, failure and idle.
   * Constraints: Tool events show activity, never fabricated completion. */
  receive(event) {
    if (event.type === "ready") {
      for (const previous of event.events || []) this.receive(previous);
      this.connected = true; this.busy = event.busy; this.stage = event.busy ? "thinking" : "ready";
    }
    if (event.seq && event.seq <= this.seen) return;
    if (event.seq) this.seen = event.seq;
    if (event.type === "working") { this.busy = true; this.stage = event.stage; }
    if (event.type === "user" || event.type === "reply") this.append(event.type === "user" ? "you" : "assistant", event.text);
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
      this.append("assistant", event.detail);
      if (event.type === "rejected" && event.code !== "agent_busy") { this.busy = false; this.stage = "ready"; }
    }
    if (event.type === "idle") { this.busy = false; this.stage = "ready"; }
    if (event.type === "cleared") { this.dialogue.replaceChildren(); this.activity.replaceChildren(); }
    this.root.dataset.busy = String(this.busy);
    this.render();
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
  /* Functionality: Render language/status/control availability. Inputs: current panel state.
   * Outputs: accessible text/disabled flags; Logic: no device work; Constraints: preserve input. */
  render() {
    for (const element of this.root.querySelectorAll("[data-copy]")) element.textContent = label(COPY[element.dataset.copy]);
    this.status.textContent = label(COPY[this.connected ? this.stage : this.stage === "offline" ? "offline" : "connecting"] || COPY.ready);
    this.input.disabled = !this.connected || this.busy || this.recording || this.stage === "permission";
    this.root.querySelector("#agent-send").disabled = this.input.disabled;
    const record = this.root.querySelector("#agent-record");
    record.textContent = label(COPY[this.recording ? "finish" : "record"]);
    record.disabled = !this.connected || this.busy || this.stage === "permission";
    this.root.querySelector("#agent-cancel").disabled = !this.connected || (!this.busy && !this.recording && this.stage !== "permission");
    this.root.querySelector("#agent-clear").disabled = this.input.disabled;
    this.root.dataset.busy = String(this.busy);
    this.root.dataset.connected = String(this.connected);
  }
  /* Functionality: Show a local error. Inputs: safe diagnostic text; Outputs: visible log.
   * Logic: Preserve error after status rerender; Constraints: no auto-resubmission. */
  fail(text) { this.append("assistant", text); this.render(); }
}

const panel = typeof document !== "undefined" && document.querySelector("main") ? new AgentPanel() : null;
