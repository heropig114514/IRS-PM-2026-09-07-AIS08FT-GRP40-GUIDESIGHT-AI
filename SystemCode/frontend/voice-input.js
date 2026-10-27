/* Responsibilities: Explicit microphone capture and deterministic 16kHz PCM WAV encoding.
 * Implementation: Start permission-bound media calls synchronously from the user gesture, then use
 * AudioWorklet capture, a 20s bound, flush handshake and generation-based cancellation.
 * Related Modules: voice-worklet.js, agent-client.js, SystemCode/backend/core/agent/provider.py.
 * Declaration Index: encodeWav; VoiceInput; VoiceInput.constructor; VoiceInput.start;
 * VoiceInput.finish; VoiceInput.release; VoiceInput.cancel.
 * Variable Index: None; chunks/context/stream/node/generation are per-recorder state. */

/* Functionality: Encode captured mono samples into PCM16 WAV.
 * Inputs: Float32Array samples and native sample rate; Outputs: base64 WAV at 16000Hz.
 * Logic: Average native-rate sample intervals, clamp amplitudes, write little-endian RIFF.
 * Constraints: Input 0.1..20s and rate >=16kHz; no hidden format/provider substitution. */
export function encodeWav(samples, rate) {
  if (!Number.isFinite(rate) || rate < 16000 || samples.length < rate / 10 || samples.length > rate * 20)
    throw new Error("录音须为 0.1～20 秒。");
  const count = Math.floor(samples.length * 16000 / rate);
  const buffer = new ArrayBuffer(44 + count * 2);
  const view = new DataView(buffer);
  for (const [offset, word] of [[0, "RIFF"], [8, "WAVE"], [12, "fmt "], [36, "data"]])
    [...word].forEach((char, index) => view.setUint8(offset + index, char.charCodeAt(0)));
  view.setUint32(4, buffer.byteLength - 8, true);
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, 16000, true);
  view.setUint32(28, 32000, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  view.setUint32(40, count * 2, true);
  for (let index = 0; index < count; index++) {
    const from = Math.floor(index * rate / 16000), to = Math.floor((index + 1) * rate / 16000);
    let sum = 0;
    for (let position = from; position < to; position++) sum += samples[position];
    const value = Math.max(-1, Math.min(1, sum / (to - from)));
    view.setInt16(44 + index * 2, Math.round(value * (value < 0 ? 32768 : 32767)), true);
  }
  let binary = "";
  const bytes = new Uint8Array(buffer);
  for (let offset = 0; offset < bytes.length; offset += 8192)
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 8192));
  return btoa(binary);
}

/* Functionality: Own one explicit microphone recording.
 * Logic: Generation isolates late permission callbacks; cleanup releases all audio resources.
 * Constraints: No automatic listening/restart, browser SpeechRecognition or persistent audio. */
export class VoiceInput {
  /* Functionality: Initialize recorder. Inputs: onLimit callback; Outputs: inactive recorder.
   * Logic: Keep recording state local; Constraints: does not request permissions. */
  constructor(onLimit) {
    this.onLimit = onLimit;
    this.generation = 0;
    this.chunks = [];
  }
  /* Functionality: Start capture after a user gesture. Inputs: browser media/WebAudio APIs.
   * Outputs: live capture or explicit error; Logic: invoke microphone permission and Web Audio
   * resume synchronously before awaiting either, then load the worklet and connect a silent graph.
   * Constraints: Permission remains browser-owned; late granted streams are immediately released. */
  async start() {
    const generation = ++this.generation;
    if (!globalThis.isSecureContext) {
      const error = new Error("Microphone capture requires a secure HTTPS page.");
      error.name = "SecurityError";
      throw error;
    }
    if (!navigator.mediaDevices?.getUserMedia || !globalThis.AudioContext) {
      const error = new Error("This browser does not support microphone capture.");
      error.name = "NotSupportedError";
      throw error;
    }
    this.context = new AudioContext();
    let mediaRequest;
    let stream = null;
    try {
      mediaRequest = navigator.mediaDevices.getUserMedia({video: false,
        audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true}});
      const resumeRequest = this.context.resume();
      const [mediaResult, resumeResult] = await Promise.allSettled([mediaRequest, resumeRequest]);
      if (mediaResult.status === "rejected") throw mediaResult.reason;
      stream = mediaResult.value;
      if (generation !== this.generation) { stream.getTracks().forEach(track => track.stop()); return false; }
      this.stream = stream;
      if (resumeResult.status === "rejected") {
        const error = new Error("The browser did not start the audio capture context.");
        error.name = "AudioContextError";
        throw error;
      }
      const context = this.context;
      await context.audioWorklet.addModule(new URL("./voice-worklet.js", import.meta.url));
      if (generation !== this.generation) return false;
      this.chunks = [];
      this.node = new AudioWorkletNode(context, "prs-voice-capture");
      this.node.port.onmessage = ({data}) => {
        if (generation !== this.generation) return;
        if (data.type === "samples") this.chunks.push(data.samples);
        if (data.type === "limit") this.onLimit();
        if (data.type === "stopped") this.flushed?.();
      };
      this.source = context.createMediaStreamSource(stream);
      this.source.connect(this.node);
      this.node.connect(context.destination);
      return true;
    } catch (error) {
      if (mediaRequest && !stream)
        mediaRequest.then(late => late.getTracks().forEach(track => track.stop()), () => {});
      if (generation !== this.generation) return false;
      await this.cancel();
      throw error;
    }
  }
  /* Functionality: Stop and encode recording. Inputs: current recording; Outputs: WAV base64.
   * Logic: Flush worklet with a 1s handshake deadline then release devices in finally.
   * Constraints: No submission on failure or cancellation; no implicit retry. */
  async finish() {
    const generation = this.generation;
    if (!this.node) throw new Error("录音尚未开始，请先允许麦克风。");
    const rate = this.context.sampleRate;
    let timer;
    try {
      await new Promise((resolve, reject) => {
        timer = setTimeout(() => reject(new Error("录音结束超时，请重新录制。")), 1000);
        this.flushed = resolve;
        this.node.port.postMessage({type: "stop"});
      });
      if (generation !== this.generation) throw new Error("录音已取消。");
      const samples = new Float32Array(this.chunks.reduce((count, chunk) => count + chunk.length, 0));
      let offset = 0;
      for (const chunk of this.chunks) { samples.set(chunk, offset); offset += chunk.length; }
      return encodeWav(samples, rate);
    } finally {
      clearTimeout(timer);
      this.flushed = null;
      await this.release();
    }
  }
  /* Functionality: Release graph/device resources. Inputs: owned resources; Outputs: cleared state.
   * Logic: Stop tracks before closing context; Constraints: close errors propagate to caller. */
  async release() {
    this.stream?.getTracks().forEach(track => track.stop());
    this.node?.disconnect();
    this.source?.disconnect();
    const context = this.context;
    this.stream = this.node = this.source = this.context = null;
    this.chunks = [];
    if (context && context.state !== "closed") await context.close();
  }
  /* Functionality: Cancel pending/current recording. Inputs: generation/resources.
   * Outputs: released device; Logic: invalidate first; Constraints: never sends captured audio. */
  async cancel() { this.generation++; await this.release(); }
}
