import json
import unittest
from unittest.mock import patch
from web.backend.note_study import parse_overview, parse_response
from web.backend.transcription import CancellationToken


class StudyValidationTests(unittest.TestCase):
    def test_overview_rejects_invented_page_numbers(self):
        body = {"message": {"content": json.dumps({"summary": "개요", "markdown": "모평균 (99쪽)", "uncertainties": []})}}
        with self.assertRaises(ValueError):
            parse_overview(body)

    def test_invalid_transport_shape_is_a_validation_error(self):
        for body in ([], {"message": []}, {"message": None}):
            with self.assertRaises(ValueError):
                parse_response(body)
