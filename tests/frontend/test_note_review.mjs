import test from 'node:test';
import assert from 'node:assert/strict';
import {pageNeedsReview} from '../../web/static/note-review.js';

test('new explanation review does not inherit OCR flags',()=>{
 const page={needs_review:true,analysis:{uncertainties:[],validation:{version:'note-review-v2',status:'passed'}}};
 assert.equal(pageNeedsReview(page),false);
 assert.equal(pageNeedsReview(page,true),true);
 page.analysis.validation.status='needs_review';
 assert.equal(pageNeedsReview(page),true);
 page.needs_review=false;
 assert.equal(pageNeedsReview(page,true),false);
});
test('legacy flags, actual errors and detail warnings remain visible',()=>{
 assert.equal(pageNeedsReview({needs_review:true}),true);
 assert.equal(pageNeedsReview({analysis_error:'실패'}),true);
 assert.equal(pageNeedsReview({detail:{uncertainties:['확인'],validation:{version:'note-review-v2',status:'passed'}}}),true);
});
