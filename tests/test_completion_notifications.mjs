import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
const source=readFileSync(new URL('../web/static/dashboard.js',import.meta.url),'utf8');
const workflow=source.slice(source.indexOf('function recordingWorkflowStatus('),source.indexOf('function recordingCategory('));
const code=workflow+source.slice(source.indexOf('function notifyCompletion('),source.indexOf('\nui.closeReviewButton'));
function harness(permission='granted',fail=false){
 const sent=[],errors=[];
 class Notification {constructor(title,options){if(fail)throw Error('blocked');sent.push({title,...options});} addEventListener(){} }
 const detect=new Function('notificationPermission','Notification','showToast',`let completionNotificationStates={}; function saveCompletionNotificationStates(){}; ${code}; return detectCompletionNotifications;`)(()=>permission,Notification,message=>errors.push(message));
 return {detect,sent,errors};
}
test('completion sends once after an observed transition, not on initial completed data',()=>{
 const h=harness(); const data=status=>({recordings:[{id:'one',title:'녹음',course_name:'통계학',status}]});
 h.detect(data('completed'));assert.equal(h.sent.length,0);
 h.detect(data('processing'));h.detect(data('completed'));h.detect(data('completed'));assert.equal(h.sent.length,1);
});
test('permission and browser failures do not break status rendering',()=>{
 for(const [permission,fail] of [['denied',false],['granted',true]]){
 const h=harness(permission,fail);h.detect({recordings:[{id:'one',status:'processing'}]});
 assert.doesNotThrow(()=>h.detect({recordings:[{id:'one',status:'completed'}]}));assert.equal(h.sent.length,0);
 assert.equal(h.errors.length,fail?1:0);
 }
});

test('completion waits for review and final publication',()=>{
 const h=harness();
 for(const review_status of ['queued','processing']) h.detect({recordings:[{id:'review',status:'completed',review_status}]});
 assert.equal(h.sent.length,0);
 h.detect({recordings:[{id:'review',status:'completed',review_status:'completed'}]});
 assert.equal(h.sent.length,1);
});
