import assert from 'node:assert/strict';
import {test} from 'node:test';

import {statusCardActivity as activity, noteQueueProgress} from '../../web/static/dashboard/selectors.js';
const note = {status:'processing', title:'통계학_4장', stage:'study', page_count:47, analyzed_pages:12};

test('active note replaces idle or stale finished speech status', () => {
  for (const phase of ['idle', 'complete', 'error', 'queued']) {
    const card = activity({phase,title:'이전 음성 변환'}, [note]);
    assert.equal(card.phase, 'processing');
    assert.equal(card.title, note.title);
    assert.equal(card.label, '강의노트 분석 중');
    assert.equal(card.message, '해설 생성 · 12/47쪽 처리');
  }
});
test('running transcription retains priority; finished notes restore the speech card', () => {
  assert.equal(activity({phase:'polishing',title:'현재 음성 변환'},[note]).title,'현재 음성 변환');
  assert.equal(activity({phase:'idle'},[]).label,'대기 중');
});
test('queued notes and extraction/detail progress are distinguished', () => {
  assert.equal(activity({},[{...note,status:'queued'}]).phase,'queued');
  assert.equal(activity({},[{...note,stage:'extracting',extracted_pages:47}]).message,'원문 추출 마무리 중');
  assert.equal(activity({},[{...note,stage:'detail',number:5}]).message,'5쪽 상세 해설 생성 중');
  assert.match(activity({},[{...note,analyzed_pages:47}]).message,/개요 정리 중/);
});

test('saved transcript stays active through independent review', () => {
 for (const review_status of ['queued','processing']) {
 const card=activity({phase:'complete',title:'음성 변환'},[],[{status:'completed',review_status,title:'검수할 녹음'}]);
 assert.equal(card.title,'검수할 녹음'); assert.notEqual(card.phase,'complete');
 }
});

test("note glossary and overview are distinct final phases", () => {
  assert.equal(noteQueueProgress({stage:"glossary"}).phase,"glossary");
  assert.equal(noteQueueProgress({stage:"overview"}).phase,"overview");
});

test('queue subtitle uses the same stage as its pipeline, without stale page counts', async () => {
 const {noteQueueSummary}=await import('../../web/static/dashboard/selectors.js');
 for(const [stage,label] of [['overview','개요 생성 중'],['glossary','용어집 생성 · 의미와 읽는 말 검증 중']]) {
  assert.equal(noteQueueSummary({stage,status:'processing',page_count:32,analyzed_pages:32}),label);
 }
 assert.equal(noteQueueSummary({stage:'study',status:'processing',page_count:32}), '해설 생성 · 0/32쪽 처리');
 assert.equal(noteQueueSummary({stage:'overview',status:'queued'}),'개요 생성 대기');
 assert.equal(noteQueueSummary({stage:'future',status:'processing'}),'분석 준비 중');
 assert.equal(noteQueueSummary({stage:'detail',status:'processing'}),'상세 해설 생성 중');
});

 test('term extraction stays within the first source extraction step', () => {
 const progress=noteQueueProgress({stage:'extracting',page_count:32,extracted_pages:32});
 assert.equal(progress.phase,'extracting');
 assert.equal(progress.message,'원문 추출 마무리 중');
 });
