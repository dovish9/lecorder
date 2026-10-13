import unittest
from scripts.evaluate_transcript import evaluate, normalize


class EvaluationTests(unittest.TestCase):
    def test_known_alignment_counts(self):
        score = evaluate("a b c d", "a x c d extra")["wer"]
        self.assertEqual(score["substitutions"], 1)
        self.assertEqual(score["insertions"], 1)
        self.assertEqual(score["deletions"], 0)
        self.assertEqual(score["error_rate"], 0.5)

    def test_empty_hypothesis_is_complete_deletion(self):
        score = evaluate("표본 분포", "")["wer"]
        self.assertEqual(score["deletions"], 2)
        self.assertEqual(score["error_rate"], 1)

    def test_korean_spacing_affects_words_but_not_characters(self):
        score = evaluate("표본 평균", "표본평균")
        self.assertGreater(score["wer"]["error_rate"], 0)
        self.assertEqual(score["cer"]["error_rate"], 0)
        self.assertEqual(normalize("Ａ, B!"), "a b")

    def test_empty_reference_is_rejected(self):
        with self.assertRaises(ValueError):
            evaluate("...", "some words")
