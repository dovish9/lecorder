import {PCMResampler} from './live-pcm.js';
class LivePCM extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.resampler = new PCMResampler(sampleRate);
    this.samples = new Int16Array(16000);
    this.used = 0; this.position = options?.processorOptions?.offsetSamples || 0; this.paused = false;
    this.port.onmessage = ({data}) => {
      if (data.paused) this.flush();
      this.paused = Boolean(data.paused);
    };
  }
  flush() {
    if (!this.used) return;
    const pcm = this.samples.slice(0, this.used);
    this.port.postMessage({start: this.position, pcm}, [pcm.buffer]);
    this.position += this.used; this.used = 0;
  }
  process(inputs) {
    if (this.paused || !inputs[0]?.length) return true;
    const channels = inputs[0];
    const mono = new Float32Array(channels[0].length);
    for (const channel of channels) for (let i = 0; i < mono.length; ++i) mono[i] += channel[i] / channels.length;
    for (const value of this.resampler.push(mono)) {
      this.samples[this.used++] = value;
      if (this.used === 16000) this.flush();
    }
    return true;
  }
}
registerProcessor('lecorder-live-pcm', LivePCM);
