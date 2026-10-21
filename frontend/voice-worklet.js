/* Responsibilities: Capture bounded mono PCM in the audio render thread.
 * Implementation: Batch 2048 samples, flush on stop, cap at 20s and signal limit.
 * Related Modules: voice-input.js; registered through AudioContext.audioWorklet.
 * Declaration Index: VoiceCapture; VoiceCapture.constructor; VoiceCapture.flush; VoiceCapture.process.
 * Variable Index: None; samples/offset/total/stopped are per-processor state; sampleRate is host-owned. */

/* Functionality: Transfer input samples without audible output.
 * Logic: Mono capture, bounded buffers and an explicit flush handshake.
 * Constraints: No network, storage, logging or DSP inference on the audio thread. */
class VoiceCapture extends AudioWorkletProcessor {
  /* Functionality: Initialize capture. Inputs: host port/sampleRate; Outputs: processor.
   * Logic: Flush before acknowledging stop; Constraints: one recorder per processor. */
  constructor() {
    super();
    this.samples = new Float32Array(2048);
    this.offset = 0;
    this.total = 0;
    this.stopped = false;
    this.port.onmessage = () => {
      this.stopped = true;
      this.flush();
      this.port.postMessage({type: "stopped"});
    };
  }
  /* Functionality: Send captured samples. Inputs: buffered state; Outputs: copied PCM batch.
   * Logic: Send only filled portion; Constraints: no empty batches. */
  flush() {
    if (this.offset) this.port.postMessage({type: "samples", samples: this.samples.slice(0, this.offset)});
    this.offset = 0;
  }
  /* Functionality: Collect audio. Inputs: audio graph inputs; Outputs: silent graph, PCM messages.
   * Logic: Stop at exactly 20s, notify UI once; Constraints: never monitors microphone to speaker. */
  process(inputs) {
    if (this.stopped || !inputs[0]?.[0]) return true;
    for (const sample of inputs[0][0]) {
      this.samples[this.offset++] = sample;
      this.total++;
      if (this.offset === this.samples.length) this.flush();
      if (this.total >= sampleRate * 20) {
        this.stopped = true;
        this.flush();
        this.port.postMessage({type: "limit"});
        break;
      }
    }
    return true;
  }
}
registerProcessor("prs-voice-capture", VoiceCapture);
