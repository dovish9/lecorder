import assert from 'node:assert/strict';
import {test} from 'node:test';
import {createNotifications} from '../../web/static/dashboard/notifications.js';
function harness(permission='granted',fail=false){
 const sent=[],errors=[];
 class Notification {static permission=permission; constructor(title,options){if(fail)throw Error('blocked');sent.push({title,...options});} addEventListener(){} }
 const browser={Notification,localStorage:{getItem(){return '{}';},setItem(){}},location:{},focus(){},setTimeout};
 const {detectCompletionNotifications: detect}=createNotifications({browser, actions:{showToast:message=>errors.push(message)}});
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
