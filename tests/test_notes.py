import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from web.backend.notes import NoteLibrary
from web.backend.note_extract import merge_text
from web.backend.note_analysis import (
    candidates,
    hint_terms,
)
from web.backend.note_study import analyze_visual_page, parse_response, build_overview
from web.backend.transcription import CancellationToken, Options
from web.backend.engine import Transcriber
from web.backend.review_jobs import relevant_context, ReviewQueue
import test_recorder as fixtures
from web.backend.store import LectureStore
from web.backend.pipeline import RecordingPipeline
from web.backend.state import JobState


class NoteTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.env, self.output = fixtures.RecorderTests.environment(self.root)
        self.store = LectureStore(self.root / "test.db", self.env)
        self.notes = NoteLibrary(self.store, self.root / "notes", False)

    def tearDown(self):
        self.temporary.cleanup()

    def test_queue_tracks_analysis_stages_and_hides_terminal_jobs(self):
        note = self.register()
        revision = note['revision_id']
        self.assertEqual(self.notes.queue_items()[0]['status'], 'queued')
        self.notes._update(revision, status='extracting', page_count=2)
        self.notes._page(revision, {'number': 1, 'text': 'private source', 'analysis_status': 'completed'})
        item = self.notes.queue_items()[0]
        self.assertEqual((item['stage'], item['status'], item['extracted_pages']), ('extracting', 'processing', 1))
        self.assertNotIn('private source', json.dumps(item))
        self.notes._update(revision, status='ready', study_status='pending')
        self.assertEqual(self.notes.queue_items()[0]['stage'], 'study')
        self.assertEqual(self.notes.queue_items()[0]['status'], 'queued')
        self.notes._update(revision, study_status='analyzing')
        self.assertEqual(self.notes.queue_items()[0]['analyzed_pages'], 1)
        for status in ('completed', 'failed', 'cancelled'):
            self.notes._update(revision, study_status=status)
            self.assertEqual(self.notes.queue_items(), [])
        self.notes._detail(revision, 1, {'detail_status': 'pending'})
        self.assertEqual(self.notes.queue_items()[0]['stage'], 'detail')
        self.notes._detail(revision, 1, {'detail_status': 'analyzing'})
        self.assertEqual(self.notes.queue_items()[0]['status'], 'processing')
        self.notes._detail(revision, 1, {'detail_status': 'completed'})
        self.assertEqual(self.notes.queue_items(), [])

    def register(self):
        return self.notes.register(
            self.store.active_course_id(),
            [("slides.pdf", io.BytesIO(b"%PDF-1.7\nfixture"))],
        )

    def ready(self):
        note = self.register()
        page = {
            "number": 1,
            "text": "Electric field and Coulomb force",
            "image": "page-1.png",
            "error": "",
            "analysis": None,
        }
        self.notes._page(note["revision_id"], page)
        self.notes._update(
            note["revision_id"],
            status="ready",
            study_status="completed",
            page_count=1,
            keywords=json.dumps(
                [
                    {
                        "id": "k1",
                        "term": "Coulomb",
                        "pages": [1],
                        "score": 1,
                        "included": True,
                    }
                ]
            ),
        )
        return self.notes.get(note["id"])

    def test_recording_note_can_be_selected_after_start_and_survives_finalize(self):
        note = self.ready()
        pipeline = RecordingPipeline(self.store, JobState(), fixtures.FakeTranscriber(), self.root / "work", False)
        pipeline.notes = self.notes
        row = pipeline.begin_chunked(self.store.get(), "recording", ".wav")
        self.assertFalse(row.note_revision_id)
        row = pipeline.select_recording_note(row.id, note["id"])
        self.assertEqual(row.note_revision_id, note["revision_id"])
        self.notes.select_keywords(note["id"], ["k1"])
        self.assertTrue(json.loads(self.store.get_recording(row.id).note_snapshot_json)["keywords"][0]["included"])
        pipeline.save_chunk(row.id, 0, io.BytesIO(b"audio"))
        pipeline.finalize_chunked(row.id, 1)
        self.assertEqual(self.store.get_recording(row.id).note_revision_id, note["revision_id"])
        with self.assertRaises(ValueError):
            pipeline.select_recording_note(row.id, None)

    def test_recording_note_rejects_unready_and_other_course_and_can_clear(self):
        note = self.ready()
        pipeline = RecordingPipeline(self.store, JobState(), fixtures.FakeTranscriber(), self.root / "work", False)
        pipeline.notes = self.notes
        row = pipeline.begin_chunked(self.store.get(), "recording", ".wav")
        pipeline.select_recording_note(row.id, note["id"])
        pipeline.select_recording_note(row.id, None)
        self.assertFalse(self.store.get_recording(row.id).note_revision_id)
        self.notes._update(note["revision_id"], status="extracting")
        with self.assertRaises(ValueError):
            pipeline.select_recording_note(row.id, note["id"])
        self.notes._update(note["revision_id"], status="ready")
        other = self.store.create_course("other")
        with self.store._connect() as db:
            db.execute("UPDATE lecture_notes SET course_id=? WHERE id=?", (other.id, note["id"]))
        with self.assertRaises(ValueError):
            pipeline.select_recording_note(row.id, note["id"])
        self.assertFalse(self.store.get_recording(row.id).note_revision_id)

    def test_invalid_files_do_not_leave_records(self):
        with self.assertRaises(ValueError):
            self.notes.register(
                self.store.active_course_id(), [("bad.pdf", io.BytesIO(b"not a pdf"))]
            )
        self.assertEqual(self.notes.list(self.store.active_course_id()), [])
        self.assertEqual(list(self.notes.folder.iterdir()), [])

    def test_old_analyzer_upload_creates_revision_and_preserves_snapshot(self):
        note = self.ready()
        with self.store._connect() as db:
            db.execute(
                "UPDATE note_revisions SET analyzer='notes-v1' WHERE id=?",
                (note["revision_id"],),
            )
        original = self.notes.snapshot(note["revision_id"])
        self.assertTrue(self.notes.get(note["id"])["needs_reanalysis"])
        updated = self.register()
        self.assertEqual(updated["id"], note["id"])
        self.assertNotEqual(updated["revision_id"], note["revision_id"])
        self.assertFalse(updated["needs_reanalysis"])
        self.assertEqual(self.notes.snapshot(note["revision_id"]), original)

    def test_keyword_versions_keep_original_analyzer_and_reanalysis_warning(self):
        note = self.ready()
        with self.store._connect() as db:
            db.execute(
                "UPDATE note_revisions SET analyzer='notes-v1' WHERE id=?",
                (note["revision_id"],),
            )
        revised = self.notes.select_keywords(note["id"], ["k1"])
        self.assertTrue(revised["needs_reanalysis"])
        self.assertEqual(
            self.notes._revision(revised["revision_id"])["analyzer"], "notes-v1"
        )
        self.notes._publish_ranking(revised["revision_id"], revised["keywords"])
        self.assertTrue(self.notes.get(note["id"])["needs_reanalysis"])

    def test_duplicate_upload_reuses_note(self):
        first = self.register()
        second = self.register()
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(list(self.notes.folder.iterdir())), 1)

    def test_binding_is_course_scoped_and_requires_ready_revision(self):
        note = self.register()
        with self.assertRaises(ValueError):
            self.notes.bind(note["id"], note["course_id"])
        self.notes._update(note["revision_id"], status="ready")
        other = self.store.create_course("other")
        with self.assertRaises(ValueError):
            self.notes.bind(note["id"], other.id)
        self.assertEqual(
            self.notes.bind(note["id"], note["course_id"]), note["revision_id"]
        )

    def test_keyword_change_and_deletion_preserve_previous_snapshot(self):
        note = self.ready()
        old = self.notes.snapshot(note["revision_id"])
        revised = self.notes.select_keywords(note["id"], ["k1"])
        self.assertNotEqual(note["revision_id"], revised["revision_id"])
        self.assertFalse(revised["keywords"][0]["included"])
        self.assertEqual(self.notes.snapshot(note["revision_id"]), old)
        self.notes.hide(note["id"])
        self.assertEqual(self.notes.snapshot(note["revision_id"]), old)
        with self.assertRaises(ValueError):
            self.notes.bind(note["id"], note["course_id"])

    def test_partial_extraction_persists_all_page_outcomes(self):
        note = self.register()

        def extract(source, index, folder, number, token):
            if number == 2:
                raise ValueError("broken page")
            return {
                "number": number,
                "text": "Coulomb electric field",
                "image": "page.png",
                "error": "",
                "analysis": None,
            }

        with patch(
            "web.backend.notes.prepare_pages",
            return_value=[(Path("a"), 1), (Path("a"), 2)],
        ), patch("web.backend.notes.extract_page", side_effect=extract):
            self.notes.extract(note["revision_id"])
        result = self.notes.get(note["id"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(self.notes.pages(note["revision_id"])), 2)
        self.assertTrue(result["keywords"])

    def test_note_context_retrieval_is_bounded_and_page_linked(self):
        snapshot = {
            "keywords": [{"term": "Coulomb", "included": True, "pages": [2]}],
            "pages": [
                {"number": 1, "text": "statistics"},
                {"number": 2, "text": "Electric field " + ("x" * 8000)},
            ],
        }
        text = relevant_context(snapshot, "Coulomb law")
        self.assertTrue(text.startswith("[노트 2쪽]"))
        self.assertLessEqual(len(text), 6000)
        self.assertNotIn("statistics", text)
        self.assertEqual(relevant_context(snapshot, "unrelated discussion"), "")

    def test_real_token_budget_and_missing_tokenizer(self):
        def post(url, json, timeout):
            r = Mock()
            r.json.return_value = {"tokens": len(json["text"].split(", ")) * 40}
            return r

        with patch("web.backend.engine.requests.post", side_effect=post):
            options = Transcriber._with_note_hint(
                Options(
                    language="en",
                    note_keywords=("Coulomb", "potential", "charge", "field"),
                )
            )
        self.assertEqual(options.recognition_hint, "Coulomb, potential")
        import requests

        with patch(
            "web.backend.engine.requests.post", side_effect=requests.ConnectionError()
        ):
            self.assertEqual(Transcriber._with_note_hint(options).recognition_hint, "")

    def test_hint_terms_filter_filler_and_duplicate_particle_forms(self):
        self.assertEqual(
            hint_terms(
                [
                    "where",
                    "the axis",
                    "표본의",
                    "표본",
                    "Coulomb",
                    "coulomb",
                    "potential",
                ]
            ),
            ["표본", "Coulomb", "potential"],
        )

    def test_visual_analysis_requires_image_and_validates_response(self):
        with self.assertRaises(ValueError):
            analyze_visual_page({"number": 1, "text": "source"}, [], CancellationToken())
        answer = {"summary": "모평균", "markdown": "## 모평균\n분포의 균형점이다.", "uncertainties": []}
        self.assertEqual(parse_response({"message": {"content": "", "thinking": json.dumps(answer)}}), answer)
        with self.assertRaises(ValueError):
            parse_response({"message": {"content": "", "thinking": "internal free text"}})
        with self.assertRaises(ValueError):
            parse_response({"done_reason": "length", "message": {"content": json.dumps(answer)}})

    def test_merge_retains_ocr_only_handwritten_text(self):
        self.assertEqual(
            merge_text(
                "Electric field",
                [{"text": "Electric field"}, {"text": "필기: 시험 범위"}],
            ),
            "Electric field\n필기: 시험 범위",
        )

    def test_review_failure_does_not_change_completed_transcript(self):
        from unittest.mock import create_autospec
        from web.backend.transcription import ReviewStats

        engine = fixtures.FakeTranscriber()
        engine._review_items = create_autospec(
            Transcriber._review_items, return_value=(1, 1, [], set(), ReviewStats())
        )
        pipeline = RecordingPipeline(
            self.store, JobState(), engine, self.root / "work", False
        )
        row = pipeline.create_upload(
            self.store.get(), "lecture", ".wav", io.BytesIO(b"audio")
        )
        self.assertTrue(Path(row.audio_path).is_file())
        pipeline.process_next()
        before = self.store.get_recording(row.id)
        self.assertEqual(before.status, "completed")
        self.assertEqual(before.review_status, "queued")
        note = Path(before.note_path).read_bytes()
        pipeline.reviews.process(row.id)
        after = self.store.get_recording(row.id)
        self.assertEqual(after.status, "completed")
        self.assertEqual(after.review_status, "failed")
        self.assertEqual(Path(after.note_path).read_bytes(), note)
        self.assertEqual(after.transcript_text, before.transcript_text)

    def test_keyword_revision_preserves_study_overview(self):
        note = self.ready()
        overview = [{"pages": [1], "summary": "전기장", "points": []}]
        self.notes._update(note["revision_id"], overview=json.dumps(overview))
        revised = self.notes.select_keywords(note["id"], ["k1"])
        self.assertEqual(revised["overview"], overview)

    def test_review_restart_restores_markdown_before_uncommitted_publish(self):
        pipeline = RecordingPipeline(
            self.store,
            JobState(),
            fixtures.FakeTranscriber(),
            self.root / "work",
            False,
        )
        row = pipeline.create_upload(
            self.store.get(), "interrupted review", ".wav", io.BytesIO(b"audio")
        )
        pipeline.process_next()
        row = self.store.get_recording(row.id)
        note = Path(row.note_path)
        original = note.read_bytes()
        backup = note.with_name(f".{row.id}-review-backup.md")
        backup.write_bytes(original)
        note.write_text("uncommitted review output")
        self.store.update_recording(row.id, review_status="processing")
        with patch("web.backend.review_jobs.threading.Thread"):
            ReviewQueue(self.store, pipeline.engine, pipeline._lock, True)
        self.assertEqual(note.read_bytes(), original)
        self.assertEqual(self.store.get_recording(row.id).review_status, "failed")
        self.assertFalse(backup.exists())

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

    def test_reanalysis_rejects_pending_study(self):
        note = self.ready()
        self.notes._update(note["revision_id"], study_status="pending")
        with self.assertRaises(ValueError):
            self.notes.reanalyze(note["id"])

    def test_review_success_uses_real_engine_signature_and_keeps_audio(self):
        from unittest.mock import create_autospec
        from web.backend.transcription import ReviewStats

        engine = fixtures.FakeTranscriber()
        engine._review_items = create_autospec(
            Transcriber._review_items,
            return_value=(1, 0, [], {"s00002"}, ReviewStats()),
        )
        pipeline = RecordingPipeline(
            self.store, JobState(), engine, self.root / "work", False
        )
        row = pipeline.create_upload(
            self.store.get(), "review success", ".wav", io.BytesIO(b"audio")
        )
        pipeline.process_next()
        before = self.store.get_recording(row.id)
        audio = Path(before.audio_path).read_bytes()
        pipeline.reviews.process(row.id)
        after = self.store.get_recording(row.id)
        self.assertEqual(after.review_status, "completed", after.review_error)
        self.assertEqual(after.status, "completed")
        self.assertEqual(Path(after.audio_path).read_bytes(), audio)
        self.assertEqual(after.id, before.id)
        self.assertTrue(Path(after.note_path).is_file())
        self.assertEqual(
            engine._review_items.call_args.kwargs["confidence_map"],
            {
                f"s{index:05d}": segment["avg_logprob"]
                for index, segment in enumerate(json.loads(before.segments_json), 1)
                if isinstance(segment.get("avg_logprob"), (int, float))
            },
        )

    def test_source_pages_are_unique_across_capitalization(self):
        result = candidates([{"number": 1, "text": "Coulomb coulomb Coulomb"}])
        term = next(k for k in result if k["term"].casefold() == "coulomb")
        self.assertEqual(term["pages"], [1])

    def test_conditional_note_response_changes_after_keyword_revision(self):
        from flask import Flask
        from web.backend.note_routes import note_routes

        note = self.ready()
        app = Flask(__name__)
        app.register_blueprint(note_routes(self.notes, Mock()))
        client = app.test_client()
        url = f"/api/notes/{note['id']}"
        first = client.get(url)
        self.assertEqual(first.status_code, 200)
        headers = {"If-None-Match": first.headers["ETag"]}
        self.assertEqual(client.get(url, headers=headers).status_code, 304)
        self.notes.select_keywords(note["id"], ["k1"])
        self.assertEqual(client.get(url, headers=headers).status_code, 200)

    def test_schema_type_error_retries_only_once(self):
        image = self.root / "page.png"
        image.write_bytes(b"fixture")
        invalid = {"summary": "summary", "markdown": "body", "uncertainties": "not an array"}
        response = Mock()
        response.json.return_value = {"message": {"content": json.dumps(invalid)}}
        with patch("web.backend.note_study.requests.post", return_value=response) as post:
            with self.assertRaises(ValueError):
                analyze_visual_page({"number": 1, "text": "source"}, [], CancellationToken(), image_path=image)
        self.assertEqual(post.call_count, 2)
        self.assertIn("images", post.call_args.kwargs["json"]["messages"][1])

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

    def test_detail_is_separate_from_published_page_and_requests_are_deduplicated(self):
        note = self.ready()
        with self.store._connect() as db:
            before = db.execute(
                "SELECT payload FROM note_pages WHERE revision_id=?",
                (note["revision_id"],),
            ).fetchone()[0]
        self.notes.request_detail(note["id"], 1)
        self.notes.request_detail(note["id"], 1)
        self.assertEqual(self.notes.study_queue.qsize(), 1)
        answer = {"summary": "해설", "points": [], "keywords": [], "formulas": []}
        with patch("web.backend.notes.analyze_page", return_value=answer):
            self.notes.study(note["revision_id"], 1)
        self.assertEqual(self.notes.pages(note["revision_id"])[0]["detail"], answer)
        with self.store._connect() as db:
            after = db.execute(
                "SELECT payload FROM note_pages WHERE revision_id=?",
                (note["revision_id"],),
            ).fetchone()[0]
        self.assertEqual(before, after)

    def test_cancel_completed_note_preserves_completed_analysis(self):
        note = self.ready()
        cancelled = self.notes.cancel(note["id"])
        self.assertEqual(cancelled["status"], "ready")
        self.assertEqual(cancelled["study_status"], "completed")
