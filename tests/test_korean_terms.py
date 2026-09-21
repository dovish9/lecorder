import unittest
from unittest.mock import patch
from web.backend.korean_terms import _phrases, noun_terms
from web.backend.note_analysis import candidates


class KoreanTermsTests(unittest.TestCase):
    def test_utf16_offsets_compound_and_particles(self):
        text = "😀 합동표본분산의"
        tokens = [
            {"start": 3, "length": 2, "word": 1, "tag": "NNG"},
            {"start": 5, "length": 2, "word": 1, "tag": "NNG"},
            {"start": 7, "length": 2, "word": 1, "tag": "NNG"},
            {"start": 9, "length": 1, "word": 1, "tag": "JKG"},
        ]
        self.assertEqual(_phrases(text, tokens), ["합동표본분산"])

    def test_candidates_use_nouns_and_preserve_source_pages(self):
        with patch("web.backend.note_analysis.noun_terms", return_value=[["모집단", "다음"], ["모집단"]]):
            result = candidates([{"number": 1, "text": "모집단의 다음과"}, {"number": 2, "text": "모집단을"}])
        self.assertEqual([x["term"] for x in result], ["모집단"])
        self.assertEqual(result[0]["pages"], [1, 2])

    def test_english_only_does_not_require_kiwi(self):
        with patch("web.backend.korean_terms.subprocess.run") as run:
            self.assertEqual(noun_terms(["electric field"]), [[]])
        run.assert_not_called()
