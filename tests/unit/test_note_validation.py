import json
import unittest
from unittest.mock import Mock, patch
from web.backend.notes.note_validation import code_issues
from web.backend.notes.note_review import finding, user_messages, validate_finding
from web.backend.notes.note_study import _generate, _semantic_issues
from web.backend.speech.transcription import CancellationToken, TranscriptionCancelled


def response(markdown='## 개념\n원문에 근거한 설명입니다.'):
    return Mock(json=lambda: {'message': {'content': json.dumps({'summary':'개념 요약', 'markdown':markdown, 'uncertainties':[]})}})


class ValidationTests(unittest.TestCase):
    def test_math_and_truncated_sentence(self):
        for text in [r'$\text{\sqrt{x}}$', '$\nabla x', '용량은 다음']:
            self.assertTrue(code_issues({'markdown':text}))
        self.assertEqual(code_issues({'markdown':r'$$\frac{x}{2}$$'}), [])

    @patch('web.backend.notes.note_study.require_llm')
    def test_failed_semantics_regenerates_once_and_keeps_warning(self, ready):
        with patch('web.backend.notes.note_study._request', side_effect=[response().json(),response().json()]) as post, patch('web.backend.notes.note_study._semantic_issues', return_value={'findings': [finding('원문의 핵심 공식이 누락되었습니다.', '핵심 공식을 확인하세요.')], 'checks': []}) as check:
            result=_generate('지시','공식 원문',CancellationToken())
        self.assertEqual(post.call_count,2)
        self.assertEqual(check.call_count,2)
        self.assertEqual(result['validation']['status'],'needs_review')
        self.assertEqual(result['validation']['generation_attempts'],2)
        self.assertTrue(result['markdown'])

    @patch('web.backend.notes.note_study.require_llm')
    def test_code_errors_skip_semantics_until_repaired(self, ready):
        with patch('web.backend.notes.note_study._request', side_effect=[response('용량은 다음').json(),response().json()]) as post, patch('web.backend.notes.note_study._semantic_issues', return_value={'findings': [], 'checks': []}) as check:
            result=_generate('지시','원문',CancellationToken())
        self.assertEqual(post.call_count,2)
        self.assertEqual(check.call_count,1)
        self.assertEqual(result['validation']['status'],'passed')

    @patch('web.backend.notes.note_study.require_llm')
    def test_validator_failure_cannot_pass_or_trigger_unnecessary_regeneration(self, ready):
        with patch('web.backend.notes.note_study._request', return_value=response().json()) as post, patch('web.backend.notes.note_study._semantic_issues', side_effect=ValueError('bad JSON')):
            result=_generate('지시','원문',CancellationToken())
        self.assertEqual(post.call_count,1)
        self.assertEqual(result['validation']['status'],'needs_review')

    @patch('web.backend.notes.note_study.require_llm')
    def test_cancellation_is_not_a_validation_warning(self, ready):
        with patch('web.backend.notes.note_study._request', return_value=response().json()), patch('web.backend.notes.note_study._semantic_issues', side_effect=TranscriptionCancelled()):
            with self.assertRaises(TranscriptionCancelled):
                _generate('지시', '원문', CancellationToken())

    def test_heading_requires_content(self):
        self.assertEqual(code_issues({'markdown': '## 제목\n본문입니다.'}), [])
        self.assertTrue(code_issues({'markdown': '## 빈 제목\n\n## 다음 제목\n내용'}))
        self.assertEqual(code_issues({'markdown': '## 내용 설명\n\n### 도체\n본문입니다.\n### 절연체\n본문입니다.'}), [])
        self.assertTrue(code_issues({'markdown': '## 내용 설명\n### 빈 하위 제목\n## 다음 제목\n내용'}))
        self.assertTrue(code_issues({'markdown': '## 내용 설명\n### 빈 하위 제목'}))

    @patch('web.backend.notes.note_study.require_llm')
    def test_semantic_check_only_sees_visible_draft(self, ready):
        reply = Mock(json=lambda: {'message': {'content': json.dumps({'issues': [], 'checks': [{'source_evidence':'원문', 'draft_evidence':'빈약한 본문', 'issue':None}]})}})
        draft = {'summary': '본문에 없는 공식을 담은 내부 요약', 'markdown': '빈약한 본문', 'uncertainties': []}
        with patch('web.backend.notes.note_study._request', return_value=reply.json()) as post:
            _semantic_issues(draft, '원문', None, CancellationToken())
        sent = json.loads(post.call_args.args[0]['messages'][1]['content'])
        self.assertEqual(sent['draft'], {'markdown': '빈약한 본문', 'symbols': [], 'uncertainties': []})

    @patch('web.backend.notes.note_study.require_llm')
    def test_missing_or_invented_evidence_cannot_pass(self, ready):
        for quote in ['', '본문에 없는 인용']:
            reply = Mock(json=lambda: {'message': {'content': json.dumps({'issues': [], 'checks': [{'source_evidence':'필수 공식', 'draft_evidence':quote, 'issue':None}]})}})
            with patch('web.backend.notes.note_study._request', return_value=reply.json()):
                self.assertTrue(_semantic_issues({'markdown':'개념 제목'}, '원문', None, CancellationToken())['findings'])

    @patch('web.backend.notes.note_study.require_llm')
    def test_diagnostics_do_not_become_user_warnings_or_retries(self, ready):
        records = [finding('기호 문맥 표기 참고', '', 'symbols', 'info'),
                   finding('표현 차이는 오류가 아님', '', 'explanation', 'info')]
        body = response().json()
        draft = json.loads(body['message']['content']); draft['uncertainties']=['생성기의 장황한 추측']
        body['message']['content']=json.dumps(draft)
        with patch('web.backend.notes.note_study._request', return_value=body) as request, patch('web.backend.notes.note_study._semantic_issues', return_value={'findings':records,'checks':[]}):
            result=_generate('지시','원문',CancellationToken())
        self.assertEqual(request.call_count,1)
        self.assertEqual(result['uncertainties'],[])
        self.assertEqual(result['validation']['status'],'passed')
        self.assertEqual(result['validation']['findings'],records)
        self.assertEqual(result['validation']['history'][0]['generation_uncertainties'],draft['uncertainties'])

    @patch('web.backend.notes.note_study.require_llm')
    def test_symbol_errors_retry_but_do_not_flag_explanation(self, ready):
        record=finding('원문에 없는 기호', '기호를 확인하세요.', 'symbols')
        with patch('web.backend.notes.note_study._request', return_value=response().json()) as request, patch('web.backend.notes.note_study._semantic_issues', return_value={'findings':[record],'checks':[]}):
            result=_generate('지시','원문',CancellationToken())
        self.assertEqual(request.call_count,2)
        self.assertEqual(result['uncertainties'],[])
        self.assertEqual(result['validation']['findings'],[record])

    @patch('web.backend.notes.note_study.require_llm')
    def test_repaired_error_remains_in_history_only(self, ready):
        record=finding('잘못된 분모', '신뢰구간의 분모를 원문과 비교하세요.')
        with patch('web.backend.notes.note_study._request', return_value=response().json()), patch('web.backend.notes.note_study._semantic_issues', side_effect=[{'findings':[record],'checks':[]},{'findings':[],'checks':[]}]):
            result=_generate('지시','원문',CancellationToken())
        self.assertEqual(result['uncertainties'],[])
        self.assertEqual(result['validation']['history'][0]['findings'],[record])

    def test_structured_findings_require_short_user_copy_and_known_categories(self):
        for record in [finding('상세',''),finding('상세','a'*161),finding('상세','확인',target='other')]:
            with self.assertRaises(ValueError): validate_finding(record)
        self.assertEqual(user_messages([finding('내부','짧은 설명'),finding('내부','짧은 설명')]),['짧은 설명'])

    @patch('web.backend.notes.note_study.require_llm')
    def test_info_does_not_bypass_evidence_check(self, ready):
        verdict={'issues': [], 'checks': [{'source_evidence':'공식', 'draft_evidence':'', 'issue':finding('사소한 표현', '', severity='info')}]}
        with patch('web.backend.notes.note_study._request', return_value={'message':{'content':json.dumps(verdict)}}):
            report=_semantic_issues({'markdown':'제목'},'원문',None,CancellationToken())
        self.assertTrue(user_messages(report['findings']))
