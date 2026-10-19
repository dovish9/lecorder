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

function recoveryHarness(t, {ownedElsewhere=false}={}) {
  const handlers = new Map(), calls = [], notices = [];
  const oldOption=globalThis.Option;globalThis.Option=class {};
  t.after(()=>{globalThis.Option=oldOption;});
  const node = {options:[],selectedOptions:[],dataset:{},style:{removeProperty(){}},setAttribute(){},removeAttribute(){},addEventListener(){},replaceChildren(){},parentElement:{setAttribute(){}}};
  const app = {recordingState:'idle',recordingId:null,recordings:[],recoveryTasks:new Map(),recoveryAttempted:new Set(),failedChunks:[],chunkUploadChain:Promise.resolve()};
  const oldWindow=globalThis.window,oldDocument=globalThis.document;
  globalThis.window={setTimeout,clearTimeout};globalThis.document={};
  t.after(()=>{globalThis.window=oldWindow;globalThis.document=oldDocument;});
  const recovery={async recordingSessions(){return [{recordingId:'old',durationSeconds:10}];},async pendingChunks(){return [];},async deleteRecordingRecovery(){calls.push('deleted');}};
  const actions={renderCaptureControls(){},renderRecordings(){},showToast(message){notices.push(message);},async flushCourseSave(){calls.push('start');throw Error('test stops before microphone');}};
  const ui=new Proxy({soundBars:[]},{get(target,key){return target[key] || node;}});
  const capture=createCapture({acquireOwnership:async()=>ownedElsewhere ? null : ()=>{},app,ui,$:()=>node,actions,listen(target,event,handler){handlers.set(event,handler);},recovery});
  return {app,capture,calls,notices,handlers};
}

test('new recording waits for the same in-flight recovery and never creates a competing session',async t=>{
  const h=recoveryHarness(t);let release;
  t.mock.method(globalThis,'fetch',async url=>{
    h.calls.push(url);
    if(url.endsWith('/finalize')) await new Promise(resolve=>{release=resolve;});
    return new Response(JSON.stringify({recording:{id:'old',status:url.endsWith('/finalize')?'queued':'recording'}}));
  });
  const recovery=h.capture.recoverRecordingSessions([{id:'old',status:'recording'}]);
  const starting=h.capture.startDockRecording();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(h.calls.includes('start'),false);
  assert.equal(h.app.recordingState,'starting');
  release();await Promise.all([recovery,starting]);
  assert.deepEqual(h.calls,['/api/recordings/old/finalize','deleted','start']);
});

test('failed recovery blocks new capture without deleting original recovery data',async t=>{
  const h=recoveryHarness(t);
  h.app.recordings=[{id:'old',status:'recording'}];
  t.mock.method(globalThis,'fetch',async()=>new Response(JSON.stringify({error:'offline'}),{status:503}));
  // Do not leave the retry timer running in this test.
  globalThis.window.setTimeout=()=>0;
  await h.capture.startDockRecording();
  assert.deepEqual(h.calls,[]);
  assert.equal(h.app.recordingState,'idle');
  assert.ok(h.notices.some(value=>value.includes('이전 녹음 저장')));
});

test('status recovery does not finalize a recording still preparing its MediaRecorder',async t=>{
  const h=recoveryHarness(t);
  h.app.recordingId='old';h.app.recordingState='starting';
  t.mock.method(globalThis,'fetch',async()=>{throw Error('must not finalize');});
  assert.equal(await h.capture.recoverRecordingSessions([{id:'old',status:'recording'}]),true);
  assert.deepEqual(h.calls,[]);
});

test('pagehide stops active recording, while hiding a tab does not stop it',async t=>{
  const h=recoveryHarness(t);let stops=0,flushes=0;
  h.app.recordingState='recording';
  h.app.mediaRecorder={state:'recording',stop(){stops++;},requestData(){flushes++;}};
  globalThis.document.visibilityState='hidden';
  h.handlers.get('visibilitychange')();
  assert.equal(stops,0);assert.equal(flushes,1);
  h.handlers.get('pagehide')();
  assert.equal(stops,1);assert.equal(h.app.recordingState,'finalizing');
});


test('another tab cannot recover or finalize a recording held by its owner', async t=>{
  const h=recoveryHarness(t,{ownedElsewhere:true});
  t.mock.method(globalThis,'fetch',async()=>{throw Error('must not touch live recording');});
  assert.equal(await h.capture.recoverRecordingSessions([{id:'old',status:'recording'}]),true);
  assert.deepEqual(h.calls,[]);
  assert.equal(h.app.recoveryAttempted.has('old'),false);
});
