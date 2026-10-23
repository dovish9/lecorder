from __future__ import annotations

import io
import json
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

from web.backend.config import DEFAULT_LLM_MODEL
from web.backend.recordings.pipeline import RecordingPipeline
from web.backend.routes import create_app
from web.backend.state import JobState
from web.backend.library.store import LectureStore
from web.backend.speech.transcription import CancellationToken, Result, TranscriptSegment, TranscriptionCancelled


from tests.support import RecorderCase, FakeTranscriber

class RecordingLifecycleTests(RecorderCase):
    def test_partial_llm_failure_is_saved_as_visible_warning(self) -> None:
        class PartialReviewTranscriber(FakeTranscriber):
            def transcribe(self, source, options, incoming=None, progress=None,
                           cancellation=None) -> Result:
                self.calls.append(options)
                return Result(
                    "Whisper 원문", llm_chunks=2, fallback_chunks=1,
                    segments=(TranscriptSegment(1, 0.0, 2.0, "Whisper 원문"),),
                    duration_seconds=2.0,
                )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, _ = self.environment(root)
            store = LectureStore(root / "web/data/lecorder.db", environment)
            state = JobState()
            engine = PartialReviewTranscriber()
            pipeline = RecordingPipeline(store, state, engine, root / "work", start_worker=False)
            recording = pipeline.create_upload(store.get(), "부분 검수", ".wav", io.BytesIO(b"audio"))
            pipeline.process_next()
            completed = store.get_recording(recording.id)
            self.assertEqual(completed.status, "completed")
            self.assertIn("LLM 검수 1/2구간 실패", completed.error)
            self.assertEqual(state.get()["phase"], "warning")


    def test_cancellation_token_terminates_active_process(self) -> None:
        token = CancellationToken()
        outcome: list[str] = []

        def run() -> None:
            try:
                token.run([sys.executable, "-c", "import time; time.sleep(30)"])
            except TranscriptionCancelled:
                outcome.append("cancelled")

        worker = threading.Thread(target=run)
        worker.start()
        time.sleep(.05)
        token.cancel()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(outcome, ["cancelled"])


    def test_queued_transcription_can_be_cancelled_and_keeps_title_matched_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, output = self.environment(root)
            store = LectureStore(root / "web/data/lecorder.db", environment)
            state = JobState()
            pipeline = RecordingPipeline(
                store, state, FakeTranscriber(), root / "work", start_worker=False
            )
            app = create_app(store, state, environment=environment, pipeline=pipeline)
            client = app.test_client()
            recording = pipeline.create_upload(
                store.get(), "통계학-9월9일-수", ".wav", io.BytesIO(b"original audio")
            )

            response = client.post(f"/api/recordings/{recording.id}/cancel")
            self.assertEqual(response.status_code, 200)
            cancelled = store.get_recording(recording.id)
            self.assertEqual(cancelled.status, "cancelled")
            self.assertEqual(cancelled.error, "사용자가 음성 변환 작업을 중단했습니다.")
            self.assertEqual(cancelled.source_path, "")
            self.assertEqual(cancelled.note_path, "")
            self.assertEqual(
                json.loads(cancelled.quality_json)["cancelled_stage"], "queued"
            )
            self.assertEqual(
                (output / "통계학-9월9일-수.wav").read_bytes(), b"original audio"
            )
            self.assertFalse((output / "통계학-9월9일-수.md").exists())
            self.assertFalse(pipeline.folder(recording.id).exists())
            pipeline.delete_history(recording.id)
            with self.assertRaises(KeyError):
                store.get_recording(recording.id)
            pipeline.process_next()
            self.assertTrue((output / "통계학-9월9일-수.wav").exists())


    def test_processing_transcription_can_be_cancelled_and_cleans_work_files(self) -> None:
        class BlockingTranscriber(FakeTranscriber):
            def __init__(self) -> None:
                super().__init__()
                self.started = threading.Event()

            def transcribe(self, source, options, incoming=None, progress=None,
                           cancellation=None) -> Result:
                self.started.set()
                while cancellation and not cancellation.cancelled:
                    time.sleep(.005)
                if cancellation:
                    cancellation.check()
                return super().transcribe(
                    source, options, incoming, progress, cancellation
                )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, output = self.environment(root)
            store = LectureStore(root / "web/data/lecorder.db", environment)
            state = JobState()
            engine = BlockingTranscriber()
            pipeline = RecordingPipeline(store, state, engine, root / "work", start_worker=False)
            app = create_app(store, state, engine, environment, pipeline)
            client = app.test_client()
            recording = pipeline.create_upload(
                store.get(), "물리학2-9월9일-수", ".webm", io.BytesIO(b"lecture audio")
            )
            worker = threading.Thread(target=pipeline.process_next)
            worker.start()
            self.assertTrue(engine.started.wait(timeout=1))

            response = client.post(f"/api/recordings/{recording.id}/cancel")
            self.assertEqual(response.status_code, 202)
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
            cancelled = store.get_recording(recording.id)
            self.assertEqual(cancelled.status, "cancelled")
            self.assertGreaterEqual(cancelled.processing_seconds, 0)
            self.assertEqual(json.loads(cancelled.quality_json)["cancelled_stage"], "processing")
            self.assertEqual(
                (output / "물리학2-9월9일-수.webm").read_bytes(), b"lecture audio"
            )
            self.assertEqual(list(output.glob("*.md")), [])
            self.assertFalse(pipeline.folder(recording.id).exists())
            self.assertEqual(state.get()["phase"], "cancelled")


    def test_failed_final_database_commit_keeps_audio_but_not_partial_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, output = self.environment(root)
            store = LectureStore(root / "web/data/lecorder.db", environment)
            pipeline = RecordingPipeline(
                store, JobState(), FakeTranscriber(), root / "work", start_worker=False
            )
            recording = pipeline.create_upload(
                store.get(), "원자적 저장", ".wav", io.BytesIO(b"audio")
            )
            def fail_final_update(recording, suggestions, publish, **kwargs):
                publish()
                raise RuntimeError("database commit failed")

            with patch.object(store, "commit_result", side_effect=fail_final_update):
                pipeline.process_next()

            failed = store.get_recording(recording.id)
            self.assertEqual(failed.status, "failed")
            self.assertEqual(Path(failed.audio_path), (output / "원자적 저장.wav").resolve())
            self.assertEqual((output / "원자적 저장.wav").read_bytes(), b"audio")
            self.assertFalse((output / "원자적 저장.md").exists())


    def test_retranscribe_terminal_jobs_replaces_existing_result(self) -> None:
        for status in ("completed", "failed", "cancelled"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                environment, output = self.environment(root)
                store = LectureStore(root / "test.db", environment)
                engine = FakeTranscriber()
                state = JobState()
                pipeline = RecordingPipeline(store, state, engine, root / "work", start_worker=False)
                audio = output / "original.wav"
                audio.write_bytes(b"original audio")
                note = output / "original.md"
                note.write_text("Previous transcript")
                old = store.create_recording("original", store.get(), "original", "upload", ".wav", "missing.wav", status)
                self.assertEqual(old.last_operation, "transcribe")
                old = store.update_recording(old.id, audio_path=str(audio), note_path=str(note),
                                             transcript_text="Previous transcript", quality_json='{"attempts":[{"error":"old"}]}')
                token = CancellationToken()
                token.cancel()
                pipeline._cancellations[old.id] = token
                client = create_app(store, state, engine, environment, pipeline).test_client()
                response = client.post(f"/api/recordings/{old.id}/retranscribe")
                self.assertEqual(response.status_code, 202)
                new_id = response.get_json()["recording"]["id"]
                self.assertEqual(new_id, old.id)
                self.assertEqual(store.get_recording(new_id).status, "queued")
                pipeline.process_next()
                new = store.get_recording(new_id)
                self.assertEqual(new.status, "completed")
                self.assertEqual(new.last_operation, "retranscribe")
                self.assertEqual(len(store.list_recordings()), 1)
                self.assertEqual(audio.read_bytes(), b"original audio")
                self.assertIn("정리된 전사문", note.read_text())
                self.assertEqual(new.note_path, str(note))
                self.assertEqual(set(item.name for item in output.iterdir()), {"original.wav", "original.md"})
                self.assertEqual(new.language, old.language)


    def test_retranscription_failure_and_cancel_preserve_previous_result(self) -> None:
        for mode in ("failure", "cancel", "publish_failure", "queued_cancel"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                environment, output = self.environment(root)
                store = LectureStore(root / "test.db", environment)
                engine = FakeTranscriber()
                pipeline = RecordingPipeline(store, JobState(), engine, root / "work", start_worker=False)
                audio, note = output / "original.wav", output / "original.md"
                audio.write_bytes(b"original audio")
                note.write_text("Previous transcript")
                old = store.create_recording("original", store.get(), "original", "upload", ".wav", "", "completed")
                store.update_recording(old.id, audio_path=str(audio), note_path=str(note), transcript_text="Previous transcript")
                store.approvals.replace_suggestions(old.id, [{"sentence_id":"s00001", "original":"old", "replacement":"new", "confidence":.9}])
                suggestions = store.approvals.list_suggestions(old.id)
                pipeline.retranscribe(old.id)
                if mode == "queued_cancel":
                    pipeline.cancel(old.id)
                    pipeline.process_next()
                elif mode == "publish_failure":
                    # Raise after successful publication, while the DB transaction is open.
                    original_commit = store.commit_result
                    def fail_commit(recording, items, publish):
                        def publish_then_fail():
                            publish()
                            raise OSError("commit failure")
                        original_commit(recording, items, publish_then_fail)
                    with patch.object(store, "commit_result", side_effect=fail_commit):
                        pipeline.process_next()
                else:
                    error = TranscriptionCancelled() if mode == "cancel" else RuntimeError("failed")
                    with patch.object(engine, "transcribe", side_effect=error):
                        pipeline.process_next()
                saved = store.get_recording(old.id)
                self.assertEqual(saved.status, "cancelled" if mode in {"cancel", "queued_cancel"} else "failed")
                self.assertEqual(saved.note_path, str(note))
                self.assertEqual(saved.transcript_text, "Previous transcript")
                self.assertEqual(store.approvals.list_suggestions(old.id), suggestions)
                self.assertEqual(note.read_text(), "Previous transcript")
                self.assertEqual(audio.read_bytes(), b"original audio")
                self.assertEqual(set(item.name for item in output.iterdir()), {"original.wav", "original.md"})


    def test_retranscribe_at_worker_completion_is_not_lost(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, output = self.environment(root)
            store = LectureStore(root / "test.db", environment)
            pipeline = RecordingPipeline(store, JobState(), FakeTranscriber(), root / "work", start_worker=False)
            audio = output / "original.wav"
            audio.write_bytes(b"audio")
            row = store.create_recording("original", store.get(), "original", "upload", ".wav", "", "completed")
            store.update_recording(row.id, audio_path=str(audio))
            # Previous attempt has published its status, but its worker has not cleaned up yet.
            pipeline._queued.add(row.id)
            pipeline.retranscribe(row.id)
            with pipeline._lock:
                pipeline._finish_queue_entry(row.id)
            pipeline.process_next()
            self.assertEqual(store.get_recording(row.id).status, "completed")
            self.assertTrue(pipeline._queue.empty())
            self.assertNotIn(row.id, pipeline._queued)
            self.assertEqual(set(path.name for path in output.iterdir()), {"original.wav", "original.md"})


    def test_retranscription_snapshots_latest_linked_course_settings(self) -> None:
        for status in ("completed", "failed", "cancelled"):
            for language in ("en", "ko", "auto"):
                with self.subTest(status=status, language=language), tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    environment, output = self.environment(root)
                    store = LectureStore(root / "test.db", environment)
                    engine = FakeTranscriber()
                    pipeline = RecordingPipeline(store, JobState(), engine, root / "work", start_worker=False)
                    old_language = "en" if language == "ko" else "ko"
                    course = store.courses.update_course(store.courses.active_course_id(), language=old_language)
                    audio = output / "original.wav"
                    audio.write_bytes(b"audio")
                    row = store.create_recording("original", store.get(), "original", "upload", ".wav", "", status)
                    store.update_recording(row.id, audio_path=str(audio), llm_model="legacy-model")
                    store.courses.update_course(course.id, name="updated course", language=language,
                                        prompt="new hint", corrections="old -> new",
                                        format_transcript=False, llm_enabled=False)
                    store.courses.create_course("unrelated selected course")
                    self.assertEqual(store.get_recording(row.id).language, old_language)
                    client = create_app(store, JobState(), engine, environment, pipeline).test_client()
                    response = client.post(f"/api/recordings/{row.id}/retranscribe")
                    self.assertEqual(response.status_code, 202)
                    self.assertEqual(response.get_json()["recording"]["language"], language)
                    # Later edits must not change an already queued attempt.
                    store.courses.update_course(course.id, language=old_language)
                    pipeline.process_next()
                    options = engine.calls[-1]
                    self.assertEqual(options.language, language)
                    self.assertEqual(options.course_name, "updated course")
                    self.assertEqual(options.llm_model, DEFAULT_LLM_MODEL)
                    self.assertEqual(options.recognition_hint, "")
                    self.assertFalse(hasattr(options, "corrections"))
                    self.assertFalse(options.format_text)
                    self.assertFalse(options.use_llm)
                    saved = store.get_recording(row.id)
                    self.assertEqual(saved.language, language)
                    self.assertEqual(saved.audio_path, str(audio))
                    self.assertEqual(saved.title, "original")


    def test_retranscription_with_deleted_course_keeps_original_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, output = self.environment(root)
            store = LectureStore(root / "test.db", environment)
            engine = FakeTranscriber()
            pipeline = RecordingPipeline(store, JobState(), engine, root / "work", start_worker=False)
            course = store.courses.update_course(store.courses.active_course_id(), language="en")
            audio = output / "original.wav"
            audio.write_bytes(b"audio")
            row = store.create_recording("original", store.get(), "original", "upload", ".wav", "", "failed")
            store.update_recording(row.id, audio_path=str(audio))
            store.courses.create_course("other")
            store.courses.delete_course(course.id)
            pipeline.retry(row.id)
            pipeline.process_next()
            self.assertEqual(engine.calls[-1].language, "en")
            self.assertEqual(engine.calls[-1].recognition_hint, "")
            self.assertIsNone(store.get_recording(row.id).course_id)


    def test_retranscribe_rejects_active_or_missing_source_without_new_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, output = self.environment(root)
            store = LectureStore(root / "test.db", environment)
            state = JobState()
            engine = FakeTranscriber()
            pipeline = RecordingPipeline(store, state, engine, root / "work", start_worker=False)
            old = store.create_recording("original", store.get(), "original", "upload", ".wav", "missing.wav", "processing")
            client = create_app(store, state, engine, environment, pipeline).test_client()
            self.assertEqual(client.post(f"/api/recordings/{old.id}/retranscribe").status_code, 400)
            store.update_recording(old.id, status="completed")
            response = client.post(f"/api/recordings/{old.id}/retranscribe")
            self.assertEqual(response.status_code, 400)
            self.assertIn("원본", response.get_json()["error"])
            self.assertEqual(len(store.list_recordings()), 1)
            self.assertEqual(client.post("/api/recordings/absent/retranscribe").status_code, 404)


    def test_failed_attempt_log_survives_retry_and_later_cancellation(self) -> None:
        class FailingTranscriber(FakeTranscriber):
            def transcribe(self, source, options, incoming=None, progress=None,
                           cancellation=None) -> Result:
                raise RuntimeError("반복 붕괴 안전 재변환도 실패했습니다.")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, output = self.environment(root)
            store = LectureStore(root / "web/data/lecorder.db", environment)
            state = JobState()
            pipeline = RecordingPipeline(
                store, state, FailingTranscriber(), root / "work", start_worker=False
            )
            recording = pipeline.create_upload(
                store.get(), "실패 로그", ".wav", io.BytesIO(b"original audio")
            )

            pipeline.process_next()
            failed = store.get_recording(recording.id)
            self.assertEqual(failed.status, "failed")
            self.assertEqual(failed.error, "반복 붕괴 안전 재변환도 실패했습니다.")
            failed_details = json.loads(failed.quality_json)
            self.assertEqual(failed_details["pipeline_version"], 1)
            self.assertTrue(failed_details["failed"])
            self.assertEqual(len(failed_details["attempts"]), 1)
            self.assertEqual(
                failed_details["attempts"][0]["error"],
                "반복 붕괴 안전 재변환도 실패했습니다.",
            )
            self.assertTrue(failed_details["audio_preserved"])
            self.assertEqual(Path(failed.audio_path).read_bytes(), b"original audio")
            self.assertTrue(Path(failed.source_path).is_file())
            self.assertTrue(failed.completed_at)

            retried = pipeline.retry(recording.id)
            self.assertEqual(retried.status, "queued")
            self.assertEqual(retried.error, "")
            self.assertEqual(len(json.loads(retried.quality_json)["attempts"]), 1)

            store.update_recording(recording.id, review_status="queued", review_error="stale review")
            pipeline.cancel(recording.id)
            cancelled = store.get_recording(recording.id)
            self.assertEqual(cancelled.status, "cancelled")
            self.assertEqual(cancelled.review_status, "none")
            self.assertEqual(cancelled.review_error, "")
            self.assertEqual(len(json.loads(cancelled.quality_json)["attempts"]), 1)
            self.assertEqual((output / "실패 로그.wav").read_bytes(), b"original audio")
