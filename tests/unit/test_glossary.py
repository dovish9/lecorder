import json
import unittest
from unittest.mock import patch
from web.backend.glossary import (symbol_key, merge_symbols, rank_candidates, recognition_terms,
                                  validate_entries, build_glossary, evidence_excerpt, spoken_form_issues)
from web.backend.transcription import CancellationToken, TranscriptionCancelled, Options
from web.backend.engine import Transcriber
from web.backend.review_jobs import relevant_context

SOURCE = {'id':'s1','term':'μ₀','kind':'symbol','score':0,'pages':[1,2],
          'sources':[{'page':1,'excerpt':'H₀: μ = μ₀'}]}
ENTRY = {'term':'μ₀','kind':'symbol','meaning':'귀무가설에서 가정한 모평균','spoken_forms':['뮤 제로'], 'source_ids':['s1'],'pages':[1]}

class GlossaryTests(unittest.TestCase):
    def test_symbols_merge_equivalent_not_ambiguous_contexts(self):
        pages=[{'number':1,'analysis':{'symbols':[{'symbol':r'\mu_0','excerpt':'null mean'}]}},
               {'number':2,'analysis':{'symbols':[{'symbol':'μ₀','excerpt':'permeability'}]}}]
        result=merge_symbols(pages)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['pages'],[1,2])
        self.assertEqual(len(result[0]['sources']),2)
        self.assertEqual(symbol_key(r'\mu_{0}'),'μ₀')
        self.assertEqual(merge_symbols(pages[:1])[0]['pages'],[1])

    def test_score_uses_presence_once_per_page_and_is_idempotent(self):
        raw=[{'id':'k1','term':'mean','score':3,'base_score':3,'pages':[1]}]
        pages=[{'number':n,'analysis':{'markdown':'mean mean mean'}} for n in [1,2]]
        first=rank_candidates(raw,pages)
        self.assertEqual(first[0]['score'],13)
        self.assertEqual(rank_candidates(first,pages),first)

    def test_symbol_readings_cannot_repeat_a_single_subscript(self):
        self.assertTrue(spoken_form_issues(dict(ENTRY,spoken_forms=['뮤 영 영'])))
        self.assertTrue(spoken_form_issues(dict(ENTRY,term='H₁',spoken_forms=['H 일 영'])))
        self.assertEqual(spoken_form_issues(ENTRY),[])

    def test_remaining_code_reading_error_stays_excluded_even_if_llm_passes(self):
        bad={'result':{'entries':[dict(ENTRY,spoken_forms=['뮤 영 영'])]}}
        with patch('web.backend.glossary.generate',side_effect=[bad,bad,{'result':{'checks':[{'index':0,'valid':True,'reason':''}]}}]) as generate:
            entries,_=build_glossary([],[SOURCE],CancellationToken())
        self.assertEqual(generate.call_count,3)
        self.assertFalse(entries[0]['included'])
        self.assertEqual(entries[0]['validation']['status'],'needs_review')

    def test_short_evidence_keeps_term_at_end_of_context(self):
        self.assertIn('μ₀', evidence_excerpt('x'*100+'μ₀'+'y'*100,'μ₀',24))

    def test_alias_only_and_legacy_priority_selection(self):
        values=[{'term':'μ₀','spoken_forms':['뮤 제로','mu zero'],'priority':1},
                {'term':'excluded','included':False},{'term':'mean','priority':2},'mean']
        self.assertEqual(recognition_terms(values),['뮤 제로','mu zero','mean'])
        with patch('web.backend.speech_processing.request_json',side_effect=lambda url,payload,**kw:{'tokens':100 if 'mu zero' in payload['text'] else 8}):
            result=Transcriber._with_note_hint(Options(language='ko',note_keywords=tuple(values)))
        self.assertNotIn('μ₀',result.recognition_hint)
        self.assertIn('뮤 제로',result.recognition_hint)
        self.assertLessEqual(result.recognition_hint_tokens,96)

    def test_validation_rejects_unknown_sources_fabricated_terms_duplicates_and_overflow(self):
        for bad in [dict(ENTRY,source_ids=['missing']),dict(ENTRY,pages=[99]),dict(ENTRY,term='invented')]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):validate_entries({'entries':[bad]},[SOURCE])
        for forms in [['뮤 제로','mu zero'], ['뮤 제로','뮤 제로']]:
            with self.assertRaises(ValueError):
                validate_entries({'entries':[dict(ENTRY,spoken_forms=forms)]},[SOURCE])
        for forms in [[],[r'\mu_0']]:
            with self.assertRaises(ValueError):
                validate_entries({'entries':[dict(ENTRY,spoken_forms=forms)]},[SOURCE])
        self.assertEqual(validate_entries({'entries':[ENTRY]},[SOURCE])[0]['spoken_forms'],['뮤 제로'])
        self.assertEqual(len(validate_entries({'entries':[ENTRY]*2},[SOURCE])),1)
        for entries in [[ENTRY]*41]:
            with self.assertRaises(ValueError):validate_entries({'entries':entries},[SOURCE])

    def test_bounded_retry_flags_unresolved_and_preserves_exclusion(self):
        answer={'result':{'entries':[ENTRY]}}
        issue={'result':{'checks':[{'index':0,'valid':False,'reason':'근거 확인 필요'}]}}
        with patch('web.backend.glossary.generate',side_effect=[answer,issue,answer,issue]) as gen:
            entries,meta=build_glossary([], [SOURCE],CancellationToken())
        self.assertEqual(gen.call_count,4)
        self.assertFalse(entries[0]['included'])
        self.assertEqual(entries[0]['validation']['status'],'needs_review')
        self.assertEqual(len(meta['history']),2)
        with patch('web.backend.glossary.generate',side_effect=[answer,{'result':{'checks':[{'index':0,'valid':True,'reason':''}]}}]):
            entries,_=build_glossary([],[SOURCE],CancellationToken(),[dict(ENTRY,included=False)])
        self.assertFalse(entries[0]['included'])

    def test_invalid_whole_response_never_publishes(self):
        with patch('web.backend.glossary.generate',return_value={'result':{'entries':[dict(ENTRY,source_ids=['missing'])]}}) as gen:
            with self.assertRaises(ValueError):build_glossary([],[SOURCE],CancellationToken())
        self.assertEqual(gen.call_count,2)

    def test_cancel_stops_before_request_and_context_contains_mapping(self):
        token=CancellationToken();token.cancel()
        with patch('web.backend.glossary.generate') as gen:
            with self.assertRaises(TranscriptionCancelled):build_glossary([],[SOURCE],token)
        gen.assert_not_called()
        evidence=relevant_context({'keywords':[dict(ENTRY,included=True)],'pages':[{'number':1,'text':'H₀: μ = μ₀'}]},'뮤 제로보다 작다')
        self.assertIn('μ₀',evidence);self.assertIn('뮤 제로',evidence);self.assertIn('[노트 1쪽]',evidence)

    def test_contextual_selection_is_required_and_preserves_original_segments(self):
        from web.backend.review import glossary_candidates
        values=[ENTRY,dict(ENTRY,term='μ',spoken_forms=['뮤'])]
        text='뮤는 뮤 제로보다 작다. 뮤지컬도 본다.'
        candidates=glossary_candidates({'s00001':text},values)
        self.assertEqual([(c['original'],c['replacement']) for c in candidates],[('뮤 제로','μ₀'),('뮤','μ')])
        options=Options(language='ko',review_context=json.dumps({'keywords':values},ensure_ascii=False))
        for selected,expected in [([],0),([{'id':'g1','confidence':.95}],1),([{'id':'g1','confidence':.5}],0)]:
            with self.subTest(selected=selected),patch('web.backend.review_engine.generate',return_value={
                'result':{'edits':[],'break_after':[],'glossary_matches':selected},
                'input_tokens':1,'output_tokens':1,'seconds':.1,'waiting_seconds':0}):
                original,total,fallback,suggestions=Transcriber._review(text,options,None)
            self.assertEqual(original,text)
            self.assertEqual(len(suggestions),expected)
            if expected:
                self.assertEqual(suggestions[0].original,'뮤 제로')
                self.assertEqual(suggestions[0].replacement,'μ₀')

    def test_asr_correction_precedes_overlapping_symbol_formatting(self):
        values=[dict(ENTRY,term='μ',spoken_forms=['뮤']),dict(ENTRY,term='유의수준',spoken_forms=[])]
        options=Options(language='ko',review_context=json.dumps({'keywords':values},ensure_ascii=False))
        response={'result':{'edits':[{'sentence_id':'s00001','original':'뮤의 수준','replacement':'유의수준',
                      'error_type':'technical_term','reason':'발음이 유사한 유의수준으로 기각역의 넓이를 정하는 문맥','confidence':.95}],
                      'glossary_matches':[{'id':'g1','confidence':.95}],'break_after':[]},
                  'input_tokens':1,'output_tokens':1,'seconds':.1,'waiting_seconds':0}
        with patch('web.backend.review_engine.generate',return_value=response):
            _,_,_,suggestions=Transcriber._review('뮤의 수준을 정합니다.',options,None)
        self.assertEqual([(s.original,s.replacement) for s in suggestions],[('뮤의 수준','유의수준')])

    def test_explicit_symbol_formatting_preserves_subscript_changes(self):
        from web.backend.review import validated_suggestions
        for spoken,term in [('a max','a_max'),('E x','E_x')]:
            with self.subTest(term=term):
                entry=dict(ENTRY,term=term,spoken_forms=[spoken])
                edit={'sentence_id':'s1','original':spoken,'replacement':term,
                      'error_type':'glossary_term','reason':'용어집 기호 표기','confidence':.95}
                self.assertEqual(len(validated_suggestions({'s1':spoken},[edit],[entry])),1)
                self.assertEqual(validated_suggestions({'s1':spoken},[edit],[]),[])
                self.assertEqual(validated_suggestions({'s1':spoken},[dict(edit,error_type='technical_term')],[entry]),[])

    def test_semantic_validation_batches_only_referenced_sources(self):
        sources=[dict(SOURCE,id=f's{i}',term=f'term{i}') for i in range(200)]
        entries=[dict(ENTRY,term=f'term{i}',kind='term',spoken_forms=[],source_ids=[f's{i}']) for i in range(9)]
        seen=[]
        def generate(messages,schema,**kwargs):
            if 'ids' in schema['properties']:
                body=json.loads(messages[1]['content'])
                self.assertLess(len(messages[1]['content']),6500)
                return {'result':{'ids':[s['id'] for s in body['candidates'] if s['id'] in {f'c{i+1}' for i in range(9)}]}}
            if 'entries' in schema['properties']:
                body=json.loads(messages[1]['content'])
                self.assertEqual(len(body['selected_candidates']),9)
                return {'result':{'entries':entries}}
            body=json.loads(messages[1]['content']);seen.append(body)
            return {'result':{'checks':[{'index':i,'valid':True,'reason':''} for i in range(len(body['entries']))]}}
        with patch('web.backend.glossary.generate',side_effect=generate):
            result,_=build_glossary(sources,[],CancellationToken())
        self.assertEqual(len(result),9)
        self.assertEqual([len(b['entries']) for b in seen],[8,1])
        for body in seen:
            self.assertEqual({s['id'] for s in body['sources']},{i for e in body['entries'] for i in e['source_ids']})

    def test_large_symbol_selection_is_bounded_and_visits_every_candidate(self):
        sources=[dict(SOURCE,id=f's{i}',term=f'symbol{i}',pages=list(range(1,33)),
                      sources=[{'page':p,'excerpt':'근거 '*200} for p in range(1,33)]) for i in range(200)]
        seen=set();calls=[]
        def generate(messages,schema,**kwargs):
            body=json.loads(messages[1]['content']);calls.append(len(messages[1]['content']))
            if 'ids' in schema['properties']:
                batch=body['candidates'];seen.update(s['id'] for s in batch)
                limit=schema['properties']['ids']['maxItems']
                return {'result':{'ids':[s['id'] for s in batch[:limit]]}}
            self.assertLessEqual(len(body['selected_candidates']),40)
            return {'result':{'entries':[]}}
        with patch('web.backend.glossary.generate',side_effect=generate):
            build_glossary([],sources,CancellationToken())
        self.assertEqual(seen,{f'c{i+1}' for i in range(200)})
        self.assertLess(len(calls),40)
        self.assertLess(max(calls),14000)

    def test_generated_selection_restores_canonical_term_pages_and_stable_ids(self):
        selected={k:v for k,v in ENTRY.items() if k not in {'term','pages'}}
        selected['source_ids']=['c1']
        with patch('web.backend.glossary.generate',side_effect=[
            {'result':{'entries':[selected]}},
            {'result':{'checks':[{'index':0,'valid':True,'reason':''}]}}
        ]):
            entries,_=build_glossary([],[SOURCE],CancellationToken())
        self.assertEqual(entries[0]['term'],SOURCE['term'])
        self.assertEqual(entries[0]['pages'],SOURCE['pages'])
        self.assertEqual(entries[0]['source_ids'],[SOURCE['id']])

    def test_missing_symbol_reading_remains_excluded_after_one_regeneration(self):
        selected={'kind':'term','meaning':ENTRY['meaning'],'spoken_forms':[], 'source_ids':['c1']}
        with patch('web.backend.glossary.generate',side_effect=[
            {'result':{'entries':[selected]}}, {'result':{'entries':[selected]}},
            {'result':{'checks':[{'index':0,'valid':True,'reason':''}]}}
        ]):
            entries,metadata=build_glossary([],[SOURCE],CancellationToken())
        self.assertEqual(entries[0]['kind'],'symbol')
        self.assertFalse(entries[0]['included'])
        self.assertEqual(entries[0]['validation']['status'],'needs_review')
        self.assertEqual(len(metadata['history']),2)

    def test_whole_relation_cannot_be_a_single_variable_alias(self):
        for term,spoken in [('E = 0','E'),('ρ dV','dV'),('r² = x² + y²','r')]:
            self.assertTrue(spoken_form_issues(dict(ENTRY,term=term,spoken_forms=[spoken])))
        self.assertEqual(spoken_form_issues(dict(ENTRY,term='ρ',spoken_forms=['rho'])),[])
        self.assertTrue(spoken_form_issues(dict(ENTRY,term='θ',spoken_forms=['타우'])))
        self.assertEqual(spoken_form_issues(dict(ENTRY,term='θ',spoken_forms=['theta'])),[])

    def test_symbols_are_validated_without_other_entries(self):
        from web.backend.glossary import validation_batches
        items=[dict(ENTRY),dict(ENTRY,term='σ'),dict(ENTRY,kind='term'),dict(ENTRY,kind='term')]
        self.assertEqual([(offset,len(batch)) for offset,batch in validation_batches(items)],[(0,1),(1,1),(2,2)])

    def test_alias_normalization_has_only_explicit_glossary_exception(self):
        from web.backend.review import validated_suggestions
        sentence={'s1':'뮤 제로보다 작다'}
        edit={'sentence_id':'s1','original':'뮤 제로','replacement':'μ₀','error_type':'glossary_term','reason':'용어집 기호 표기','confidence':.95}
        self.assertEqual(len(validated_suggestions(sentence,[edit],[ENTRY])),1)
        self.assertEqual(validated_suggestions(sentence,[edit],[]),[])
        self.assertEqual(validated_suggestions(sentence,[edit],[dict(ENTRY,included=False)]),[])
        self.assertEqual(validated_suggestions(sentence,[dict(edit,replacement='μ₁')],[ENTRY]),[])
        self.assertEqual(validated_suggestions(sentence,[dict(edit,error_type='technical_term')],[ENTRY]),[])
