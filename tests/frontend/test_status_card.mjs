import assert from 'node:assert/strict';
import {test} from 'node:test';

import {statusCardActivity as activity, noteQueueProgress} from '../../web/static/dashboard/selectors.js';
const note = {status:'processing', title:'통계학_4장', stage:'study', page_count:47, analyzed_pages:12};

test('active note replaces idle or stale finished speech status', () => {
  for (const phase of ['idle', 'complete', 'error', 'queued']) {
    const card = activity({phase,title:'이전 전사'}, [note]);
    assert.equal(card.phase, 'processing');
    assert.equal(card.title, note.title);
    assert.equal(card.label, '강의노트 분석 중');
    assert.equal(card.message, '해설 생성 · 12/47쪽 처리');
  }
});
test('running transcription retains priority; finished notes restore the speech card', () => {
  assert.equal(activity({phase:'polishing',title:'현재 전사'},[note]).title,'현재 전사');
  assert.equal(activity({phase:'idle'},[]).label,'대기 중');
});
test('queued notes and extraction/detail progress are distinguished', () => {
  assert.equal(activity({},[{...note,status:'queued'}]).phase,'queued');
  assert.equal(activity({},[{...note,stage:'extracting',extracted_pages:47}]).message,'원문 용어 후보 추출 중');
  assert.equal(activity({},[{...note,stage:'detail',number:5}]).message,'5쪽 상세 해설 생성 중');
  assert.match(activity({},[{...note,analyzed_pages:47}]).message,/개요 정리 중/);
});

test('saved transcript stays active through independent review', () => {
 for (const review_status of ['queued','processing']) {
 const card=activity({phase:'complete',title:'전사'},[],[{status:'completed',review_status,title:'검수할 녹음'}]);
 assert.equal(card.title,'검수할 녹음'); assert.notEqual(card.phase,'complete');
 }
});

test("note glossary and overview are distinct final phases", () => {
  assert.equal(noteQueueProgress({stage:"glossary"}).phase,"glossary");
  assert.equal(noteQueueProgress({stage:"overview"}).phase,"overview");
});
