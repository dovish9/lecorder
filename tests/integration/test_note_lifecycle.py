import io
import json
from pathlib import Path
from unittest.mock import patch, Mock
from web.backend.note_analysis import (
    candidates,
    hint_terms,
)
from web.backend.note_study import analyze_visual_page, parse_response
from web.backend.transcription import CancellationToken
from tests import support as fixtures
from web.backend.pipeline import RecordingPipeline
from web.backend.state import JobState


from tests.note_support import NoteCase

class NoteLifecycleTests(NoteCase):
    def test_pptx_is_rejected_before_creating_note_files(self):
        before = set(self.notes.folder.iterdir())
        with self.assertRaisesRegex(ValueError, "PDF"):
            self.notes.register(self.store.get().course_id, [("slides.PPTX", io.BytesIO(b"PK-test"))])
        self.assertEqual(set(self.notes.folder.iterdir()), before)
        from web.backend.note_extract import prepare_pages
        with self.assertRaisesRegex(ValueError, "지원하지 않는"):
            prepare_pages([self.root / "slides.pptx"], self.root, CancellationToken())


    def test_postprocessing_cannot_be_disabled(self):
        course = self.store.update_course(self.store.active_course_id(), llm_enabled=False, format_transcript=False)
        self.assertTrue(course.llm_enabled)
        self.assertTrue(course.format_transcript)


    def test_extracting_note_wait_does_not_block_other_jobs_and_can_cancel(self):
        note = self.register()
        engine = fixtures.FakeTranscriber()
        pipeline = RecordingPipeline(self.store, JobState(), engine, self.root / 'work', False)
        audio = self.output / 'original.wav'; audio.write_bytes(b'audio')
        row = self.store.create_recording('blocked', self.store.get(), 'blocked', 'upload', '.wav', '', 'completed')
        markdown = self.output / 'original.md'; markdown.write_text('previous')
        self.store.update_recording(row.id, audio_path=str(audio), note_path=str(markdown), transcript_text='previous')
        pipeline.retranscribe(row.id, note['id'], retain_note=False)
        other = pipeline.create_upload(self.store.get(), 'other', '.wav', io.BytesIO(b'audio'))
        pipeline.process_next()
        self.assertEqual(engine.calls, [])
        pipeline.process_next()
        self.assertEqual(self.store.get_recording(other.id).status, 'completed')
        pipeline.cancel(row.id)
        pipeline.process_next()
        self.assertEqual(len(engine.calls), 1)
        self.assertEqual(self.store.get_recording(row.id).status, 'cancelled')
        self.assertEqual(self.store.get_recording(row.id).transcript_text, 'previous')


    def test_completion_items_expose_only_current_note_state(self):
        note = self.ready()
        items = self.notes.completion_items()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["note_id"], note["id"])
        self.assertEqual(items[0]["revision_id"], note["revision_id"])
        self.assertEqual(items[0]["status"], "ready")
        self.assertEqual(items[0]["study_status"], "completed")
        self.assertEqual(items[0]["course_id"], self.store.active_course_id())
        self.assertEqual(items[0]["error"], "")


    def test_failed_note_dependency_preserves_previous_result(self):
        for terminal in ('failed', 'cancelled'):
            with self.subTest(terminal=terminal):
                note = self.ready()
                self.notes._update(note['revision_id'], study_status='pending')
                engine = fixtures.FakeTranscriber()
                pipeline = RecordingPipeline(self.store, JobState(), engine, self.root / ('work-' + terminal), False)
                audio = self.output / (terminal + '.wav'); audio.write_bytes(b'audio')
                row = self.store.create_recording(terminal, self.store.get(), terminal, 'upload', '.wav', '', 'completed')
                self.store.update_recording(row.id, audio_path=str(audio), transcript_text='previous')
                pipeline.retranscribe(row.id, note['id'], retain_note=False)
                self.notes._update(note['revision_id'], study_status=terminal)
                pipeline.process_next()
                saved = self.store.get_recording(row.id)
                self.assertEqual(saved.status, 'failed')
                self.assertEqual(saved.transcript_text, 'previous')
                self.assertEqual(audio.read_bytes(), b'audio')
                self.assertEqual(engine.calls, [])


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
        self.assertEqual(result["keywords"], [])
        self.assertTrue(json.loads(self.notes._revision(note["revision_id"])["glossary_data"])["candidates"])


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


    def test_reanalysis_rejects_pending_study(self):
        note = self.ready()
        self.notes._update(note["revision_id"], study_status="pending")
        with self.assertRaises(ValueError):
            self.notes.reanalyze(note["id"])


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
        with patch("web.backend.note_study._request", return_value=response.json.return_value) as post:
            result = analyze_visual_page({"number": 1, "text": "source"}, [], CancellationToken(), image_path=image)
        self.assertEqual(result["validation"]["status"], "needs_review")
        self.assertEqual(post.call_count, 2)
        self.assertIn("images", post.call_args.args[0]["messages"][1])


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
