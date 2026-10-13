// Stateful low-pass filtering and resampling across AudioWorklet block boundaries.
export class PCMResampler {
  constructor(rate) {
    if (!Number.isFinite(rate) || rate <= 0) throw new Error('Invalid audio sample rate');
    this.step = rate / 16000; this.position = 0; this.offset = 0; this.previous = 0;
    const size = rate > 16000 ? 63 : 1;
    const cutoff = Math.min(.5, 7200 / rate);
    this.kernel = Array.from({length: size}, (_, i) => {
      const x = i - (size - 1) / 2;
      return (x ? Math.sin(2*Math.PI*cutoff*x)/(Math.PI*x) : 2*cutoff) * (.54-.46*Math.cos(2*Math.PI*i/Math.max(1,size-1)));
    });
    const sum = this.kernel.reduce((a,b)=>a+b,0);
    this.kernel = this.kernel.map(x=>x/sum);
    this.history = new Float64Array(size); this.cursor = 0;
  }
  push(input) {
    const filtered = new Float64Array(input.length), size = this.kernel.length;
    for (let i=0; i<input.length; i++) {
      this.history[this.cursor] = input[i];
      for (let tap=0; tap<size; tap++) filtered[i] += this.kernel[tap]*this.history[(this.cursor-tap+size)%size];
      this.cursor = (this.cursor+1)%size;
    }
    const result = [], limit = this.offset + filtered.length - 1;
    while (this.position < limit) {
      const local = this.position - this.offset, i = Math.floor(local), fraction = local-i;
      const a = i < 0 ? this.previous : filtered[i], b = filtered[i+1];
      result.push(Math.round(Math.max(-1, Math.min(1, a+(b-a)*fraction))*32767));
      this.position += this.step;
    }
    this.offset += filtered.length;
    if (filtered.length) this.previous = filtered[filtered.length-1];
    return result;
  }
}
