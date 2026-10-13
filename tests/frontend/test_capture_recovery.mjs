import assert from 'node:assert/strict';
import {test} from 'node:test';
import {createCapture} from '../../web/static/dashboard/capture.js';

function harness(t, {storageFailure=false}={}) {
  const writes=[],deleted=[],uploads=[],stored=[];
  const node={dataset:{},setAttribute(){},addEventListener(){}};
  const app={recordingId:'test',chunkIndex:0,recordingStartedAt:Date.now(),pausedDuration:0,failedChunks:[],chunkUploadChain:Promise.resolve()};
  t.mock.method(globalThis,'fetch',async (url,options)=>{uploads.push(url);return new Response(JSON.stringify({ok:true}),{headers:{'content-type':'application/json'}});});
  const oldWindow=globalThis.window,oldDocument=globalThis.document;
  globalThis.window={setTimeout,clearTimeout};globalThis.document={};
  t.after(()=>{globalThis.window=oldWindow;globalThis.document=oldDocument;});
  const recovery={
    async putPendingChunk(item){writes.push(item.index);if(storageFailure)throw Error('quota');stored.push(item);},
    async updateRecordingSession(){},
    async pendingChunks(){return stored;},
    async deletePendingChunk(id,index){deleted.push(index);},
  };
  const capture=createCapture({app,ui:{dockRecordingSession:node},$:()=>node,actions:{},listen(){},recovery});
  return {app,capture,writes,deleted,uploads};
}

test('chunks are durably stored even while an earlier upload is blocked',async t=>{
  const h=harness(t); let unblock;
  h.app.chunkUploadChain=new Promise(resolve=>{unblock=resolve;});
  h.capture.queueRecordingChunk(new Blob(['one']));h.capture.queueRecordingChunk(new Blob(['two']));
  await Promise.resolve();
  assert.deepEqual(h.writes,[0,1]);assert.equal(h.uploads.length,0);
  unblock();await h.app.chunkUploadChain;
  assert.deepEqual(h.deleted,[0,1]);assert.equal(h.uploads.length,2);
});

test('failed upload preserves memory fallback until a successful retry',async t=>{
  const h=harness(t,{storageFailure:true});
  globalThis.fetch=async()=>{throw Error('offline');};
  h.capture.queueRecordingChunk(new Blob(['one']));await h.app.chunkUploadChain;
  assert.equal(h.app.failedChunks.length,1);assert.deepEqual(h.deleted,[]);
  globalThis.fetch=async()=>(new Response(JSON.stringify({ok:true}),{headers:{'content-type':'application/json'}}));
  await h.capture.retryFailedChunks('test');
  assert.deepEqual(h.app.failedChunks,[]);assert.deepEqual(h.deleted,[0]);
});
