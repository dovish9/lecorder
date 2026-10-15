import test from 'node:test';
import assert from 'node:assert/strict';
let Processor;
globalThis.sampleRate = 16000;
globalThis.AudioWorkletProcessor = class { constructor() { this.sent=[]; this.port={postMessage: value => this.sent.push(value)}; } };
globalThis.registerProcessor = (_, value) => { Processor=value; };
await import('../../web/static/dashboard/live-worklet.js');

test('AI pause drops partial PCM, advances the source clock, and tags the new generation', () => {
  const worker=new Processor({});
  const feed=n => worker.process([[new Float32Array(n).fill(.1)]]);
  feed(8000);
  worker.port.onmessage({data:{aiEnabled:false,generation:1}});
  feed(32000);
  assert.equal(worker.used,0); assert.equal(worker.sent.length,0);
  const resume=worker.position;
  worker.port.onmessage({data:{aiEnabled:true,generation:2}});
  feed(16001);
  assert.equal(worker.sent.length,1);
  assert.equal(worker.sent[0].start,resume);
  assert.equal(worker.sent[0].generation,2);
  assert.equal(worker.sent[0].pcm.length,16000);
});

test('AI control never resumes a user-paused recording clock', () => {
  const worker=new Processor({});
  worker.port.onmessage({data:{paused:true}});
  worker.port.onmessage({data:{aiEnabled:false,generation:1}});
  worker.port.onmessage({data:{aiEnabled:true,generation:2}});
  worker.process([[new Float32Array(16000)]]);
  assert.equal(worker.position,0); assert.equal(worker.sent.length,0);
});
