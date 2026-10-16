import json
from unittest.mock import patch, Mock
from web.backend.note_extract import merge_text
from web.backend.note_study import build_overview, overview_groups
from web.backend.transcription import CancellationToken, Options
from web.backend.engine import Transcriber
from web.backend.review_jobs import relevant_context


from tests.note_support import NoteCase

class NoteContentTests(NoteCase):
    def test_tesseract_literal_quote_does_not_swallow_tsv_rows(self):
        from web.backend.note_extract import ocr
        import struct
        image = self.root / 'page.png'
        image.write_bytes(b'\x89PNG\r\n\x1a\n' + b'\0' * 8 + struct.pack('>II', 100, 100))
        header = 'level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n'
        rows = '5\t1\t1\t1\t1\t1\t1\t1\t10\t10\t90\t"\n5\t1\t1\t1\t2\t1\t1\t20\t10\t10\t90\t정규분포\n'
        with patch('web.backend.ocr.binary', return_value='tesseract'), patch('web.backend.ocr.run', return_value=header + rows):
            lines = ocr(image, CancellationToken(), 'tesseract')
        self.assertEqual([line['text'].strip() for line in lines], ['"', '정규분포'])


    def test_note_context_retrieval_is_bounded_and_page_linked(self):
        snapshot = {
            "keywords": [{"term": "Coulomb", "included": True, "pages": [2]}],
            "pages": [
                {"number": 1, "text": "statistics"},
                {"number": 2, "text": "Electric field " + ("x" * 8000)},
            ],
        }
        text = relevant_context(snapshot, "Coulomb law")
        self.assertIn("[노트 2쪽]", text)
        self.assertLessEqual(len(text), 6000)
        self.assertNotIn("statistics", text)
        self.assertIn("Coulomb", relevant_context(snapshot, "unrelated discussion"))


    def test_real_token_budget_and_missing_tokenizer(self):
        def post(url, payload, **kwargs):
            return {"tokens": len(payload["text"].split(", ")) * 40}

        with patch("web.backend.speech_processing.request_json", side_effect=post):
            options = Transcriber._with_note_hint(
                Options(
                    language="en",
                    note_keywords=("Coulomb", "potential", "charge", "field"),
                )
            )
        self.assertEqual(options.recognition_hint, "Coulomb, potential")
        from web.backend.ai_transport import ProviderError

        with patch(
            "web.backend.speech_processing.request_json", side_effect=ProviderError("offline")
        ):
            self.assertEqual(Transcriber._with_note_hint(options).recognition_hint, "")


    def test_merge_retains_ocr_only_handwritten_text(self):
        self.assertEqual(
            merge_text(
                "Electric field",
                [{"text": "Electric field"}, {"text": "필기: 시험 범위"}],
            ),
            "Electric field\n필기: 시험 범위",
        )


    def test_keyword_revision_preserves_study_overview(self):
        note = self.ready()
        overview = [{"pages": [1], "summary": "전기장", "points": []}]
        self.notes._update(note["revision_id"], overview=json.dumps(overview))
        revised = self.notes.select_keywords(note["id"], ["k1"])
        self.assertEqual(revised["overview"], overview)


    def test_cancel_during_overview_does_not_publish_completed_revision(self):
        note = self.ready()
        revision_id = note["revision_id"]
        page = self.notes.pages(revision_id)[0]
        page["analysis"] = {
            "summary": "전기장",
            "keywords": [],
            "points": [],
            "formulas": [],
        }
        self.notes._page(revision_id, page)

        def cancel(*args, **kwargs):
            self.notes.cancel(note["id"])
            self.notes.tokens[revision_id].check()

        with patch("web.backend.notes.build_overview", side_effect=cancel):
            self.notes.study(revision_id)
        result = self.notes.get(note["id"])
        self.assertEqual(result["study_status"], "cancelled")
        self.assertEqual(result["revision_id"], revision_id)


    def test_overview_sources_preserve_full_explanations(self):
        text = '앞부분 설명.\n\n' + ('중간 근거입니다.\n\n' * 700) + '마지막 공식과 적용 조건.'
        groups = list(overview_groups([{'pages':[1], 'summary':'요약', 'markdown':text}]))
        self.assertEqual(''.join(item['markdown'] for group in groups for item in group), text)
        self.assertTrue(all(len(json.dumps(group,ensure_ascii=False)) < 6100 for group in groups))
        self.assertTrue(all(item['pages'] == [1] for group in groups for item in group))

    def test_overview_reduction_keeps_all_source_pages(self):
        pages = [{"number": i, "analysis": {"summary": str(i), "markdown": "body", "keywords": []}} for i in range(1, 15)]
        def generate(*args, **kwargs):
            return {"summary": "summary", "markdown": "overview", "uncertainties": []}
        with patch("web.backend.note_study._generate", side_effect=generate) as call:
            result = build_overview(pages, CancellationToken())
        self.assertEqual(result[0]["pages"], list(range(1, 15)))
        self.assertEqual(call.call_count, 4)


    def test_ocr_position_merge_preserves_additional_annotation(self):
        boxes = [
            {"text": "Electric", "box": [0.1, 0.1, 0.15, 0.1]},
            {"text": "field", "box": [0.26, 0.1, 0.1, 0.1]},
        ]
        lines = [
            {"text": "Electric field", "box": [0.1, 0.1, 0.27, 0.1]},
            {"text": "Exam question", "box": [0.1, 0.4, 0.3, 0.1]},
        ]
        self.assertEqual(
            merge_text("Electric field", lines, boxes), "Electric field\nExam question"
        )
