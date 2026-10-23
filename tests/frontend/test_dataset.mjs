import test from 'node:test';
import assert from 'node:assert/strict';

test('browser recovery uses a dataset-specific database and never opens the old store',async t=>{
 const {configureDataset,recoveryDatabaseName,recordingSessions}=await import('../../web/static/recording-store.js?dataset-test');
 const previous=globalThis.window;const opened=[];
 globalThis.window={indexedDB:{open(name){
  opened.push(name);
  const connection={transaction(){return {objectStore(){return {getAll(){const r={result:[]};r.addEventListener=(event,cb)=>{if(event==='success')queueMicrotask(cb);};return r;}};}};}};
  const request={result:connection,addEventListener(event,cb){if(event==='success')queueMicrotask(cb);}};
  return request;
 }}};
 t.after(()=>{globalThis.window=previous;});
 await assert.rejects(recordingSessions(),/환경/);
 configureDataset('a'.repeat(32));assert.deepEqual(await recordingSessions(),[]);
 assert.deepEqual(opened,[recoveryDatabaseName('a'.repeat(32))]);
 assert.notEqual(recoveryDatabaseName('a'.repeat(32)),recoveryDatabaseName('b'.repeat(32)));
 configureDataset('a'.repeat(32));
 assert.throws(()=>configureDataset('b'.repeat(32)),/변경/);
});

test('mutations carry the bound dataset and a changed server cannot silently rebind',async t=>{
 const {bindDataset,requestJson}=await import('../../web/static/dashboard/api.js?dataset-test');
 const previous=globalThis.fetch;const requests=[];
 globalThis.fetch=async(url,options)=>{requests.push(options);return new Response('{}',{headers:{'Content-Type':'application/json'}});};
 t.after(()=>{globalThis.fetch=previous;});
 bindDataset('a'.repeat(32));await requestJson('/api/test',{method:'POST'});await requestJson('/api/status');
 assert.equal(requests[0].headers.get('X-Lecorder-Dataset'),'a'.repeat(32));
 assert.equal(requests[1].headers.has('X-Lecorder-Dataset'),false);
 assert.throws(()=>bindDataset('b'.repeat(32)),/환경/);
});
