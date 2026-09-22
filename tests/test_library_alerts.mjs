import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
const source = readFileSync(new URL('../web/static/dashboard.js', import.meta.url), 'utf8');
const counts = new Function(`${source.slice(source.indexOf('function libraryAlertCounts('), source.indexOf('function notificationBadge('))}; return libraryAlertCounts;`)();
test('counts one alert per recording across courses, independent of the selected course', () => {
  const entries = [1,1,1,2].map(course_id => ({category:'review',recording:{course_id,suggestion_counts:{pending:5}}}));
  entries.push({category:'completed',recording:{course_id:1}});
  assert.equal(counts(entries).total,4);
  assert.deepEqual([...counts(entries).byCourse],[[1,3],[2,1]]);
  entries[0].category='completed';
  assert.equal(counts(entries).total,3);
  assert.equal(counts([{category:'problem',recording:{course_id:null}}]).byCourse.get(0),1);
});
