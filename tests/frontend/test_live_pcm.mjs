import assert from 'node:assert/strict';
import {test} from 'node:test';
import {PCMResampler} from '../../web/static/dashboard/live-pcm.js';
for (const rate of [16000,44100,48000]) {
  test(`PCM ${rate} Hz: chunk boundaries preserve samples, phase and duration`,()=>{
    const data=Float32Array.from({length:rate},(_,i)=>.5*Math.sin(2*Math.PI*440*i/rate));
    const full=new PCMResampler(rate).push(data), stream=new PCMResampler(rate), parts=[];
    for(let i=0;i<data.length;i+=128) parts.push(...stream.push(data.subarray(i,i+128)));
    assert.deepEqual(parts,full);
    assert.ok(Math.abs(full.length-16000)<=1);
    assert.ok(full.every(Number.isFinite));
  });
}
test('downsampling suppresses frequencies above the output Nyquist frequency',()=>{
  const power=f=>{
    const data=Float32Array.from({length:48000},(_,i)=>.5*Math.sin(2*Math.PI*f*i/48000));
    const output=new PCMResampler(48000).push(data).slice(100);
    return Math.sqrt(output.reduce((a,v)=>a+v*v,0)/output.length);
  };
  assert.ok(power(12000)<power(1000)*.02);
});
