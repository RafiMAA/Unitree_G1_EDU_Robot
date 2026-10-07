export function decodePcm(encoded) {
  const raw = atob(encoded);
  if (!raw.length || raw.length % 2) throw new Error('Invalid live audio packet');
  const bytes = Uint8Array.from(raw, char => char.charCodeAt(0));
  const view = new DataView(bytes.buffer);
  return Float32Array.from({ length: bytes.length / 2 }, (_, i) => view.getInt16(i * 2, true) / 32768);
}

export class LiveAudio {
  constructor(onAudio, onPlayback) {
    this.onAudio = onAudio;
    this.onPlayback = onPlayback;
    this.sources = new Set();
    this.nextTime = 0;
    this.muted = false;
    this.closed = false;
    this.playbackEpoch = 0;
    this.decodeQueue = Promise.resolve();
  }
  async open() {
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    try { this.context = new AudioContext({ sampleRate: 16000 }); }
    catch { this.context = new AudioContext(); }
    await this.context.resume();
    this.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    if (this.closed) { this.stream.getTracks().forEach(track => track.stop()); return; }
    await this.context.audioWorklet.addModule('/live-microphone.js');
    if (this.closed) return;
    this.input = this.context.createMediaStreamSource(this.stream);
    this.capture = new AudioWorkletNode(this.context, 'live-microphone');
    this.capture.port.onmessage = event => { if (!this.closed && !this.muted) this.onAudio(event.data); };
    // A connected zero-gain output keeps the worklet running without mic feedback.
    this.silent = this.context.createGain(); this.silent.gain.value = 0;
    this.input.connect(this.capture); this.capture.connect(this.silent); this.silent.connect(this.context.destination);
  }
  play(data, rate = 24000) {
    if (this.closed || !this.context) return;
    const pcm = decodePcm(data);
    const buffer = this.context.createBuffer(1, pcm.length, rate);
    buffer.copyToChannel(pcm, 0);
    this.enqueueBuffer(buffer);
  }
  enqueueBuffer(buffer) {
    if (this.closed) return;
    const source = this.context.createBufferSource(); source.buffer = buffer;
    source.connect(this.context.destination);
    const now = this.context.currentTime;
    if (this.nextTime - now > 15) throw new Error('Audio playback fell behind. End and restart the conversation.');
    this.nextTime = Math.max(now + .02, this.nextTime);
    this.sources.add(source); this.onPlayback(true);
    source.onended = () => { this.sources.delete(source); source.disconnect(); if (!this.sources.size && !this.closed) this.onPlayback(false); };
    source.start(this.nextTime); this.nextTime += buffer.duration;
  }
  playFile(data) {
    const epoch = this.playbackEpoch;
    this.decodeQueue = this.decodeQueue.catch(() => {}).then(async () => {
      if (this.closed || epoch !== this.playbackEpoch) return;
      const bytes = Uint8Array.from(atob(data), char => char.charCodeAt(0));
      const buffer = await this.context.decodeAudioData(bytes.buffer);
      if (!this.closed && epoch === this.playbackEpoch) this.enqueueBuffer(buffer);
    });
    return this.decodeQueue;
  }
  interrupt() {
    this.playbackEpoch += 1;
    for (const source of this.sources) { source.onended = null; try { source.stop(); } catch {} source.disconnect(); }
    this.sources.clear(); this.nextTime = 0; this.onPlayback(false);
  }
  mute(value) {
    this.muted = value;
    this.stream?.getAudioTracks().forEach(track => { track.enabled = !value; });
  }
  close() {
    this.closed = true;
    this.interrupt();
    if (this.capture) { this.capture.port.onmessage = null; this.capture.disconnect(); }
    this.input?.disconnect(); this.silent?.disconnect();
    this.stream?.getTracks().forEach(track => track.stop());
    this.context?.close().catch(() => {});
  }
}
