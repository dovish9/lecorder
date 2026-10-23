import test from 'node:test';
import assert from 'node:assert/strict';
import {usageSummary,usageSeries,compactNumber,createUsagePeriod} from '../../web/static/dashboard/usage.js';
test('speech includes live drafts; tokens retain unknown rather than zero',()=>{
 const rows=[{kind:'speech',requests:2,audio_seconds:60},{kind:'live_speech',requests:1,audio_seconds:20},{kind:'llm',requests:1,input_tokens:null,output_tokens:null,unknown_usage:1}];
 assert.equal(usageSummary(rows,'speech').value,80);assert.equal(usageSummary(rows,'speech').requests,3);
 assert.equal(usageSummary(rows,'llm').value,null);assert.equal(usageSummary(rows,'llm').unknown,1);
 assert.equal(usageSummary([],'llm').value,0);
});
test('daily gaps, unknown days and cumulative sums stay distinct',()=>{
 const series=usageSeries({start_date:'2026-10-01',end_date:'2026-10-04',daily:[{date:'2026-10-01',kind:'llm',value:10},{date:'2026-10-03',kind:'llm',value:null,unknown:1},{date:'2026-10-04',kind:'llm',value:20}]},'llm');
 assert.deepEqual(series.map(p=>p.value),[10,0,null,20]);assert.deepEqual(series.map(p=>p.cumulative),[10,10,10,30]);assert.equal(series[2].unknown,1);
});

test('token counts use K M B and keep unavailable distinct',()=>{
 assert.deepEqual([null,0,999,4800,1200000,2300000000].map(compactNumber),['미제공','0','999','4.8K','1.2M','2.3B']);
});

test('missing or invalid daily fields do not draw misleading points',()=>{
 assert.deepEqual(usageSeries({groups:[],days:1},'llm'),[]);
 assert.deepEqual(usageSeries({daily:[],start_date:'invalid',end_date:'invalid'},'speech'),[]);
});

test('detail periods are independent and late requests cannot overwrite the latest selection',async()=>{
 const pending=[], speechStates=[], llmStates=[];
 const load=(days,signal)=>new Promise((resolve,reject)=>pending.push({days,signal,resolve,reject}));
 const speech=createUsagePeriod(load,state=>speechStates.push(state));
 const llm=createUsagePeriod(load,state=>llmStates.push(state));
 const first=speech.select(7), other=llm.select(7);
 const latest=speech.select(30);
 assert.equal(pending[0].signal.aborted,true);
 assert.equal(pending[1].signal.aborted,false);
 pending[2].resolve({period:30});await latest;
 pending[0].resolve({period:7});await first;
 pending[1].resolve({period:7});await other;
 assert.deepEqual(speechStates.at(-1).data,{period:30});
 assert.deepEqual(llmStates.at(-1).data,{period:7});
 assert.equal(speechStates.filter(s=>s.data).length,1);
 const closing=llm.select(1);llm.close();
 pending[3].resolve({period:1});await closing;
 assert.equal(llmStates.filter(s=>s.data).length,1);
 assert.equal(pending[3].signal.aborted,true);
 const reopenedStates=[], reopened=createUsagePeriod(load,state=>reopenedStates.push(state));
 const reopenedLoad=reopened.select(7);pending[4].resolve({period:7});await reopenedLoad;
 assert.equal(reopenedStates.at(-1).days,7);
});
test('detail request errors can be retried without affecting another column',async()=>{
 let attempts=0;const states=[];
 const loader=createUsagePeriod(async()=>{if(++attempts===1)throw new Error('offline');return {groups:[]};},state=>states.push(state));
 await loader.select(7);assert.equal(states.at(-1).error.message,'offline');
 await loader.select(7);assert.deepEqual(states.at(-1).data,{groups:[]});
 loader.close();await loader.select(30);assert.equal(attempts,2);
});
