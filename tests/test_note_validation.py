import json
import unittest
from unittest.mock import Mock, patch
from web.backend.note_validation import code_issues
from web.backend.note_study import _generate, _semantic_issues
from web.backend.transcription import CancellationToken, TranscriptionCancelled


def response(markdown='## 개념\n원문에 근거한 설명입니다.'):
    return Mock(json=lambda: {'message': {'content': json.dumps({'summary':'개념 요약', 'markdown':markdown, 'uncertainties':[]})}})


class ValidationTests(unittest.TestCase):
    def test_math_and_truncated_sentence(self):
        for text in [r'$\text{\sqrt{x}}$', '$\nabla x', '용량은 다음']:
            self.assertTrue(code_issues({'markdown':text}))
        self.assertEqual(code_issues({'markdown':r'$$\frac{x}{2}$$'}), [])

    @patch('web.backend.note_study.require_ollama')
    def test_failed_semantics_regenerates_once_and_keeps_warning(self, ready):
        with patch('web.backend.note_study.requests.post', side_effect=[response(),response()]) as post, patch('web.backend.note_study._semantic_issues', return_value=['원문의 핵심 공식이 누락되었습니다.']) as check:
            result=_generate('지시','공식 원문',CancellationToken())
        self.assertEqual(post.call_count,2)
        self.assertEqual(check.call_count,2)
        self.assertEqual(result['validation']['status'],'needs_review')
        self.assertEqual(result['validation']['generation_attempts'],2)
        self.assertTrue(result['markdown'])

    @patch('web.backend.note_study.require_ollama')
    def test_code_errors_skip_semantics_until_repaired(self, ready):
        with patch('web.backend.note_study.requests.post', side_effect=[response('용량은 다음'),response()]) as post, patch('web.backend.note_study._semantic_issues', return_value=[]) as check:
            result=_generate('지시','원문',CancellationToken())
        self.assertEqual(post.call_count,2)
        self.assertEqual(check.call_count,1)
        self.assertEqual(result['validation']['status'],'passed')

    @patch('web.backend.note_study.require_ollama')
    def test_validator_failure_cannot_pass_or_trigger_unnecessary_regeneration(self, ready):
        with patch('web.backend.note_study.requests.post', return_value=response()) as post, patch('web.backend.note_study._semantic_issues', side_effect=ValueError('bad JSON')):
            result=_generate('지시','원문',CancellationToken())
        self.assertEqual(post.call_count,1)
        self.assertEqual(result['validation']['status'],'needs_review')

    @patch('web.backend.note_study.require_ollama')
    def test_cancellation_is_not_a_validation_warning(self, ready):
        with patch('web.backend.note_study.requests.post', return_value=response()), patch('web.backend.note_study._semantic_issues', side_effect=TranscriptionCancelled()):
            with self.assertRaises(TranscriptionCancelled):
                _generate('지시', '원문', CancellationToken())

    def test_heading_requires_content(self):
        self.assertEqual(code_issues({'markdown': '## 제목\n본문입니다.'}), [])
        self.assertTrue(code_issues({'markdown': '## 빈 제목\n\n## 다음 제목\n내용'}))

    @patch('web.backend.note_study.require_ollama')
    def test_semantic_check_only_sees_visible_draft(self, ready):
        reply = Mock(json=lambda: {'message': {'content': json.dumps({'issues': [], 'checks': [{'source_evidence':'원문', 'draft_evidence':'빈약한 본문', 'issue':''}]})}})
        draft = {'summary': '본문에 없는 공식을 담은 내부 요약', 'markdown': '빈약한 본문', 'uncertainties': []}
        with patch('web.backend.note_study.requests.post', return_value=reply) as post:
            _semantic_issues(draft, '원문', None, CancellationToken())
        sent = json.loads(post.call_args.kwargs['json']['messages'][1]['content'])
        self.assertEqual(sent['draft'], {'markdown': '빈약한 본문'})

    @patch('web.backend.note_study.require_ollama')
    def test_missing_or_invented_evidence_cannot_pass(self, ready):
        for quote in ['', '본문에 없는 인용']:
            reply = Mock(json=lambda: {'message': {'content': json.dumps({'issues': [], 'checks': [{'source_evidence':'필수 공식', 'draft_evidence':quote, 'issue':''}]})}})
            with patch('web.backend.note_study.requests.post', return_value=reply):
                self.assertTrue(_semantic_issues({'markdown':'개념 제목'}, '원문', None, CancellationToken()))
