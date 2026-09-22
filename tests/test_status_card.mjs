import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';

const source = readFileSync(new URL('../web/static/dashboard.js', import.meta.url), 'utf8');
const workflow = source.slice(source.indexOf('function recordingWorkflowStatus('),source.indexOf('function recordingCategory('));
const functions = workflow + source.slice(source.indexOf('function noteQueueProgress('), source.indexOf('function createStagePipeline('));
const activity = new Function('phaseLabels', `${functions}; return statusCardActivity;`)({idle:'대기 중', polishing:'검수 중'});
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
  assert.equal(activity({},[{...note,stage:'extracting',extracted_pages:47}]).message,'전사에 사용할 용어집 생성 중');
  assert.equal(activity({},[{...note,stage:'detail',number:5}]).message,'5쪽 상세 해설 생성 중');
  assert.match(activity({},[{...note,analyzed_pages:47}]).message,/개요 정리 중/);
});

test('saved transcript stays active through independent review', () => {
 for (const review_status of ['queued','processing']) {
 const card=activity({phase:'complete',title:'전사'},[],[{status:'completed',review_status,title:'검수할 녹음'}]);
 assert.equal(card.title,'검수할 녹음'); assert.notEqual(card.phase,'complete');
 }
});
