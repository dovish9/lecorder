import test from 'node:test';
import assert from 'node:assert/strict';
import {usageSummary,usageSeries,compactNumber} from '../../web/static/dashboard/usage.js';
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

test('older servers without daily fields keep usage details usable',()=>{
 assert.deepEqual(usageSeries({groups:[],days:1},'llm'),[]);
 assert.deepEqual(usageSeries({daily:[],start_date:'invalid',end_date:'invalid'},'speech'),[]);
});
