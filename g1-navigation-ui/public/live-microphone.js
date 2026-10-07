// Batch microphone samples into 40 ms PCM packets without main-thread capture.
class LiveMicrophone extends AudioWorkletProcessor {
  constructor() {
    super();
    this.samples = new Float32Array(Math.round(sampleRate * .04));
    this.offset = 0;
  }
  process(inputs) {
    const channel = inputs[0]?.[0];
    if (!channel) return true;
    for (const sample of channel) {
      this.samples[this.offset++] = sample;
      if (this.offset === this.samples.length) {
        const pcm = new ArrayBuffer(this.samples.length * 2);
        const view = new DataView(pcm);
        for (let i = 0; i < this.samples.length; i++) {
          const value = Math.max(-1, Math.min(1, this.samples[i]));
          view.setInt16(i * 2, Math.round(value * (value < 0 ? 32768 : 32767)), true);
        }
        this.port.postMessage(pcm, [pcm]);
        this.offset = 0;
      }
    }
    return true;
  }
}
registerProcessor('live-microphone', LiveMicrophone);
