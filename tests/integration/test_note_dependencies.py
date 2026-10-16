import threading
import io
import json
from pathlib import Path
from unittest.mock import patch, Mock
from web.backend.engine import Transcriber
from web.backend.review_jobs import ReviewQueue
from tests import support as fixtures
from web.backend.pipeline import RecordingPipeline
from web.backend.state import JobState


from tests.note_support import NoteCase

class NoteDependenciesTests(NoteCase):
    def test_offline_llm_fails_jobs_without_queuing_or_losing_outputs(self):
        pipeline = RecordingPipeline(self.store, JobState(), fixtures.FakeTranscriber(), self.root / 'work', False)
        row = self.store.create_recording('offline', self.store.get(), 'offline', 'upload', '.wav', '', 'queued')
        audio = self.output / 'preserved.wav'
        audio.write_bytes(b'original')
        self.store.update_recording(row.id, audio_path=str(audio))
        with patch('web.backend.pipeline.require_llm', side_effect=RuntimeError('offline')), patch('web.backend.review_jobs.require_llm', side_effect=RuntimeError('offline')), patch('web.backend.notes.require_llm', side_effect=RuntimeError('offline')), patch('web.backend.note_intake.require_llm', side_effect=RuntimeError('offline')):
            pipeline.enqueue(row.id)
            self.assertTrue(pipeline._queue.empty())
            self.assertEqual(self.store.get_recording(row.id).status, 'failed')
            self.assertEqual(audio.read_bytes(), b'original')
            self.store.update_recording(row.id, status='completed', review_status='queued', transcript_text='preserved')
            pipeline.reviews.enqueue(row.id)
            result = self.store.get_recording(row.id)
            self.assertEqual(result.review_status, 'failed')
            self.assertEqual(result.transcript_text, 'preserved')
            self.assertTrue(pipeline.reviews.queue.empty())
            note = self.notes.register(self.store.active_course_id(), [('offline.pdf', io.BytesIO(b'%PDF-1.7 original'))])
            self.assertEqual(note['status'], 'failed')
            self.assertTrue(self.notes.extract_queue.empty())


    def test_recording_course_change_preserves_chunks_and_updates_settings(self):
        note = self.ready()
        old = self.store.get()
        target = self.store.create_course('통계학')
        self.store.update_course(target.id, language='ko', llm_enabled=False)
        pipeline = RecordingPipeline(self.store, JobState(), fixtures.FakeTranscriber(), self.root / 'work', False)
        recording = pipeline.begin_chunked(old, f'{old.course_name}-9월22일-화', '.webm', note['id'])
        pipeline.save_chunk(recording.id, 0, io.BytesIO(b'preserved audio'))
        same = pipeline.select_recording_course(recording.id, old.course_id)
        self.assertEqual(same.note_revision_id, note['revision_id'])
        (self.output / '통계학-9월22일-화.mp4').write_bytes(b'existing')
        updated = pipeline.select_recording_course(recording.id, target.id)
        self.assertEqual(updated.title, '통계학-9월22일-화 (2)')
        self.assertEqual(updated.language, 'ko')
        self.assertTrue(updated.llm_enabled)
        self.assertIsNone(updated.note_revision_id)
        self.assertEqual(updated.source_path, recording.source_path)
        self.assertEqual((pipeline.folder(recording.id) / 'chunks/00000000.part').read_bytes(), b'preserved audio')
        with self.assertRaises(ValueError):
            pipeline.select_recording_note(recording.id, note['id'])
        self.store.update_recording(recording.id, status='completed')
        with self.assertRaises(ValueError):
            pipeline.select_recording_course(recording.id, old.course_id)


    def test_recording_course_change_preserves_custom_title(self):
        target = self.store.create_course('다른 강의')
        pipeline = RecordingPipeline(self.store, JobState(), fixtures.FakeTranscriber(), self.root / 'work', False)
        recording = pipeline.begin_chunked(self.store.get(), '직접 정한 제목', '.webm')
        self.assertEqual(pipeline.select_recording_course(recording.id, target.id).title, '직접 정한 제목')


    def test_retranscription_changes_course_title_without_duplicating_audio(self):
        old = self.store.get()
        target = self.store.create_course('물리학2')
        self.store.update_course(target.id, language='en', llm_enabled=False)
        note = self.ready()
        audio = self.output / 'old.wav'; audio.write_bytes(b'original audio')
        markdown = self.output / 'old.md'; markdown.write_text('[audio](old.wav) previous result')
        row = self.store.create_recording('rename-retry', old, 'old', 'upload', '.wav', '', 'completed')
        self.store.update_recording(row.id, audio_path=str(audio), note_path=str(markdown), transcript_text='previous result')
        pipeline = RecordingPipeline(self.store, JobState(), fixtures.FakeTranscriber(), self.root / 'work', False)
        queued = pipeline.retranscribe(row.id, note['id'], retain_note=False, course_id=target.id, title='new')
        self.assertEqual(queued.id, row.id)
        self.assertEqual(queued.course_id, target.id)
        self.assertEqual(queued.language, 'en')
        self.assertEqual(queued.transcript_text, 'previous result')
        self.assertEqual(Path(queued.audio_path).read_bytes(), b'original audio')
        self.assertIn('new.wav', Path(queued.note_path).read_text())
        self.assertFalse(audio.exists())
        pipeline.process_next()
        self.assertEqual(self.store.get_recording(row.id).status, 'completed')
        self.assertEqual(len(list(self.output.glob('*.wav'))), 1)


    def test_retranscription_rename_rolls_back_on_database_failure(self):
        audio = self.output / 'old.wav'; audio.write_bytes(b'original')
        row = self.store.create_recording('rollback', self.store.get(), 'old', 'upload', '.wav', '', 'completed')
        self.store.update_recording(row.id, audio_path=str(audio))
        pipeline = RecordingPipeline(self.store, JobState(), fixtures.FakeTranscriber(), self.root / 'work', False)
        with patch.object(self.store, 'queue_retranscription', side_effect=RuntimeError('db failure')):
            with self.assertRaises(RuntimeError):
                pipeline.retranscribe(row.id, title='new')
        self.assertEqual(audio.read_bytes(), b'original')
        self.assertFalse((self.output / 'new.wav').exists())
        self.assertEqual(self.store.get_recording(row.id).title, 'old')


    def test_unassigned_recording_uses_raw_settings_and_can_change_courses(self):
        note = self.ready()
        old = self.store.get()
        self.store.select_course(0)
        settings = self.store.get()
        self.assertIsNone(settings.course_id)
        self.assertEqual(settings.language, 'auto')
        self.assertTrue(settings.llm_enabled)
        self.assertTrue(settings.format_transcript)
        self.assertEqual(self.store.active_course_id(), 0)
        pipeline = RecordingPipeline(self.store, JobState(), fixtures.FakeTranscriber(), self.root / 'work', False)
        with self.assertRaises(ValueError):
            pipeline.begin_chunked(settings, '녹음', '.webm', note['id'])
        row = pipeline.begin_chunked(settings, '녹음-9월22일-화', '.webm')
        self.assertIsNone(row.note_revision_id)
        linked = pipeline.select_recording_course(row.id, old.course_id)
        self.assertEqual(linked.title, f'{old.course_name}-9월22일-화')
        raw = pipeline.select_recording_course(row.id, 0)
        self.assertIsNone(raw.course_id)
        self.assertEqual(raw.title, '녹음-9월22일-화')
        self.assertTrue(raw.llm_enabled)
        self.assertEqual(raw.language, 'auto')
        self.assertEqual(json.loads(raw.note_snapshot_json), {})


    def test_retranscription_can_remove_course_and_hints(self):
        note = self.ready()
        audio = self.output / 'raw.wav'; audio.write_bytes(b'audio')
        row = self.store.create_recording('unassigned', self.store.get(), 'raw', 'upload', '.wav', '', 'completed')
        self.store.update_recording(row.id, audio_path=str(audio), note_revision_id=note['revision_id'])
        engine = fixtures.FakeTranscriber()
        pipeline = RecordingPipeline(self.store, JobState(), engine, self.root / 'work', False)
        queued = pipeline.retranscribe(row.id, course_id=0)
        self.assertIsNone(queued.course_id)
        self.assertIsNone(queued.note_revision_id)
        self.assertTrue(queued.llm_enabled)
        pipeline.process_next()
        self.assertEqual(self.store.get_recording(row.id).status, 'completed')
        self.assertEqual(engine.calls[0].language, 'auto')
        self.assertFalse(engine.calls[0].note_keywords)


    def test_upload_and_recording_share_note_analysis_dependency(self):
        note = self.ready()
        revision = note['revision_id']
        self.notes._update(revision, study_status='pending')
        engine = fixtures.FakeTranscriber()
        pipeline = RecordingPipeline(self.store, JobState(), engine, self.root / 'work', False)
        upload = pipeline.create_upload(self.store.get(), 'upload', '.wav', io.BytesIO(b'audio'), note['id'])
        self.assertTrue(upload.public()['waiting_for_note'])
        pipeline.process_next()
        self.assertEqual(engine.calls, [])
        recording = pipeline.begin_chunked(self.store.get(), 'recording', '.webm')
        selected = pipeline.select_recording_note(recording.id, note['id'])
        self.assertTrue(json.loads(selected.note_snapshot_json)['waiting_for_analysis'])
        pipeline.select_recording_note(recording.id, None)
        self.assertFalse(json.loads(self.store.get_recording(recording.id).note_snapshot_json).get('waiting_for_analysis', False))
        # Old queued jobs without the new marker must also be gated at execution.
        self.store.update_recording(upload.id, note_snapshot_json=json.dumps(self.notes.snapshot(revision)))
        pipeline.process_next()
        self.assertTrue(self.store.get_recording(upload.id).public()['waiting_for_note'])
        self.assertEqual(engine.calls, [])
        self.notes._update(revision, study_status='completed')
        pipeline.process_next()
        self.assertEqual(len(engine.calls), 1)
        self.assertEqual(self.store.get_recording(upload.id).status, 'completed')


    def test_retranscription_waits_for_selected_revision_and_survives_reload(self):
        note = self.ready()
        revision = note['revision_id']
        self.notes._update(revision, study_status='pending')
        engine = fixtures.FakeTranscriber()
        pipeline = RecordingPipeline(self.store, JobState(), engine, self.root / 'work', False)
        audio = self.output / 'original.wav'
        audio.write_bytes(b'audio')
        row = self.store.create_recording('wait', self.store.get(), 'original', 'upload', '.wav', '', 'completed')
        self.store.update_recording(row.id, audio_path=str(audio), transcript_text='previous')
        queued = pipeline.retranscribe(row.id, note['id'], retain_note=False)
        self.assertTrue(queued.public()['waiting_for_note'])
        pipeline.process_next()
        self.assertEqual(engine.calls, [])
        self.assertEqual(self.store.get_recording(row.id).status, 'queued')
        # Another process can restore the dependency entirely from the database.
        pipeline = RecordingPipeline(self.store, JobState(), engine, self.root / 'work', False)
        pipeline.enqueue(row.id)
        self.notes._update(revision, study_status='analyzing')
        pipeline.process_next()
        self.assertEqual(engine.calls, [])
        self.notes._update(revision, study_status='completed', keywords='[{"term":"updated","included":true}]')
        pipeline.process_next()
        self.assertEqual(len(engine.calls), 1)
        self.assertEqual(engine.calls[0].note_keywords, ('updated',))
        self.assertEqual(self.store.get_recording(row.id).audio_path, str(audio))
        self.assertFalse(self.store.get_recording(row.id).public()['waiting_for_note'])


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


    def test_review_failure_does_not_change_completed_transcript(self):
        from unittest.mock import create_autospec
        from web.backend.transcription import ReviewStats

        engine = fixtures.FakeTranscriber()
        engine.review = create_autospec(
            Transcriber.review, return_value=(1, 1, [], set(), ReviewStats())
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


    def test_review_retry_uses_current_model_without_changing_old_runs(self):
        from web.backend.config import DEFAULT_LLM_MODEL
        row = self.store.create_recording("legacy", self.store.get(), "lecture", "upload", ".wav", "", "completed")
        self.store.update_recording(row.id, llm_model="legacy-model", review_status="failed")
        with self.store._connect() as db:
            db.execute("INSERT INTO review_runs(id,recording_id,started_at,status,settings) VALUES (?,?,?,?,?)",
                       ("old", row.id, self.store.now(), "failed", '{"model":"legacy-model"}'))
        worker = ReviewQueue(self.store, fixtures.FakeTranscriber(), threading.RLock(), False)
        worker.retry(row.id)
        self.assertEqual(self.store.get_recording(row.id).llm_model, DEFAULT_LLM_MODEL)
        self.assertEqual(self.store.get_recording(row.id).last_operation, "review")
        with self.store._connect() as db:
            self.assertEqual(json.loads(db.execute("SELECT settings FROM review_runs WHERE id='old'").fetchone()[0])["model"], "legacy-model")


    def test_cancel_queued_review_skips_engine_and_allows_retry_after_drain(self):
        row = self.store.create_recording("cancel-queued", self.store.get(), "lecture", "upload", ".wav", "", "completed")
        worker = ReviewQueue(self.store, Mock(), threading.RLock(), False)
        worker.retry(row.id)
        worker.cancel(row.id)
        worker.process(worker.queue.get_nowait())
        worker.engine.review.assert_not_called()
        self.assertEqual(self.store.get_recording(row.id).review_status, "cancelled")
        worker.retry(row.id)
        self.assertEqual(self.store.get_recording(row.id).review_status, "queued")


    def test_cancel_running_review_preserves_result_and_logs_cancellation(self):
        pipeline = RecordingPipeline(self.store, JobState(), fixtures.FakeTranscriber(), self.root / "work", False)
        row = pipeline.create_upload(self.store.get(), "cancel review", ".wav", io.BytesIO(b"audio"))
        pipeline.process_next()
        before = self.store.get_recording(row.id)
        original = Path(before.note_path).read_bytes()
        entered, release = threading.Event(), threading.Event()
        def reviewing(*args, cancellation, **kwargs):
            entered.set()
            self.assertTrue(release.wait(5))
            cancellation.check()
        with patch.object(pipeline.engine, "review", side_effect=reviewing, create=True):
            thread = threading.Thread(target=pipeline.reviews.process, args=(row.id,))
            thread.start()
            try:
                self.assertTrue(entered.wait(5))
                pipeline.reviews.cancel(row.id)
                self.assertTrue(pipeline.reviews.cancellations[row.id].cancelled)
                with self.assertRaises(ValueError):
                    pipeline.reviews.retry(row.id)
            finally:
                release.set()
                thread.join(5)
            self.assertFalse(thread.is_alive())
        after = self.store.get_recording(row.id)
        self.assertEqual(after.review_status, "cancelled")
        self.assertEqual(after.status, "completed")
        self.assertEqual(after.transcript_text, before.transcript_text)
        self.assertEqual(Path(after.note_path).read_bytes(), original)
        with self.store._connect() as db:
            run = db.execute("SELECT * FROM review_runs WHERE recording_id=?", (row.id,)).fetchone()
            self.assertEqual(run["status"], "cancelled")
            self.assertTrue(run["completed_at"])
        pipeline.reviews.retry(row.id)
        self.assertEqual(self.store.get_recording(row.id).review_status, "queued")


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
        self.assertEqual(self.store.get_recording(row.id).review_status, "queued")
        self.assertFalse(backup.exists())


    def test_review_success_uses_real_engine_signature_and_keeps_audio(self):
        from unittest.mock import create_autospec
        from web.backend.transcription import ReviewStats

        engine = fixtures.FakeTranscriber()
        engine.review = create_autospec(
            Transcriber.review,
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
            engine.review.call_args.kwargs["confidence_map"],
            {
                f"s{index:05d}": segment["avg_logprob"]
                for index, segment in enumerate(json.loads(before.segments_json), 1)
                if isinstance(segment.get("avg_logprob"), (int, float))
            },
        )
