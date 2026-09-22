import json
import unittest
from unittest.mock import patch
from web.backend.note_study import parse_overview, parse_response, normalize_markdown, _generate
from web.backend.transcription import CancellationToken


class StudyValidationTests(unittest.TestCase):
    def setUp(self):
        availability = patch("web.backend.ollama_status.ollama_ready", return_value=(True, True))
        availability.start()
        self.addCleanup(availability.stop)

    def test_overview_rejects_invented_page_numbers(self):
        body = {"message": {"content": json.dumps({"summary": "개요", "markdown": "모평균 (99쪽)", "uncertainties": []})}}
        with self.assertRaises(ValueError):
            parse_overview(body)

    def test_invalid_transport_shape_is_a_validation_error(self):
        for body in ([], {"message": []}, {"message": None}):
            with self.assertRaises(ValueError):
                parse_response(body)

    def test_escaped_math_and_paragraphs_preserve_tex_commands(self):
        self.assertEqual(normalize_markdown(r"제목\n\n$\\mu + \nu + \nabla f$"), "제목\n\n" + r"$\mu + \nu + \nabla f$")
        matrix = r"$$\begin{aligned}a&=b\\c&=d\end{aligned}$$"
        self.assertEqual(normalize_markdown(matrix), matrix)

    def test_truncated_retry_has_more_output_room(self):
        from unittest.mock import Mock
        good = {"summary": "요약", "markdown": "본문", "uncertainties": []}
        responses = [Mock(json=lambda: {"message": {}, "done_reason": "length"}),
                     Mock(json=lambda: {"message": {"content": json.dumps(good)}})]
        with patch("web.backend.note_study.requests.post", side_effect=responses) as post:
            _generate("지시", "원문", CancellationToken())
        self.assertEqual([c.kwargs['json']['options']['num_predict'] for c in post.call_args_list], [2048, 4096])

    def test_json_control_characters_in_math_are_repaired(self):
        damaged = "$" + "\b" + "ar{X} + " + "\f" + "rac{1}{2}$"
        self.assertEqual(normalize_markdown(damaged), r"$\bar{X} + \frac{1}{2}$")
