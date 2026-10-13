from __future__ import annotations

import io
import json
import tempfile
import threading
from pathlib import Path
from unittest.mock import Mock, patch

from web.backend.pipeline import RecordingPipeline
from web.backend.records import Recording
from web.backend.routes import create_app, schedule_managed_restart
from web.backend.state import JobState
from web.backend.store import LectureStore


from tests.support import RecorderCase, FakeTranscriber

class RecordingIntakeTests(RecorderCase):
    def test_opening_store_does_not_interrupt_live_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, _ = self.environment(root)
            store = LectureStore(root / "test.db", environment)
            states = ("recording", "processing", "cancelling", "queued", "completed")
            for status in states:
                store.create_recording(status, store.get(), status, "upload", ".wav", "", status)
            reader = LectureStore(store.path, environment)
            reader.get()
            for status in states:
                self.assertEqual(reader.get_recording(status).status, status)
            reader.recover_interrupted()
            for old, new in zip(states, ("recoverable", "failed", "failed", "queued", "completed")):
                self.assertEqual(reader.get_recording(old).status, new)


    def test_worker_start_recovers_abandoned_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, _ = self.environment(root)
            store = LectureStore(root / "test.db", environment)
            store.create_recording("interrupted", store.get(), "test", "upload", ".wav", "", "processing")
            with patch("web.backend.pipeline.threading.Thread") as worker:
                RecordingPipeline(store, JobState(), FakeTranscriber(), root / "work")
                self.assertEqual(worker.return_value.start.call_count, 4)
            self.assertEqual(store.get_recording("interrupted").status, "failed")


    def test_suggestion_counts_are_grouped_and_include_empty_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, _ = self.environment(root)
            store = LectureStore(root / "test.db", environment)
            for name in ("one", "two", "empty"):
                store.create_recording(name, store.get(), name, "upload", ".wav", "", "completed")
            store.replace_suggestions("one", [
                {"sentence_id": f"s{i:05d}", "original": "before", "replacement": "after", "confidence": .9}
                for i in range(1, 4)
            ])
            items = store.list_suggestions("one")
            store.decide_suggestion(items[0].id, "accepted")
            store.decide_suggestion(items[1].id, "rejected")
            store.replace_suggestions("two", [
                {"sentence_id": "s00001", "original": "before", "replacement": "after", "confidence": .9}
            ])
            self.assertEqual(store.suggestion_counts(["one", "empty"]), {
                "one": {"pending": 1, "accepted": 1, "rejected": 1},
                "empty": {"pending": 0, "accepted": 0, "rejected": 0},
            })
            self.assertEqual(store.suggestion_counts([]), {})


    def test_managed_restart_is_only_scheduled_under_start_command(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(schedule_managed_restart())
        with patch.dict("os.environ", {"LECORDER_MANAGED_RESTART": "1"}, clear=True), patch(
            "web.backend.routes.threading.Timer"
        ) as timer:
            self.assertTrue(schedule_managed_restart())
            timer.return_value.start.assert_called_once()


    def test_browser_webm_is_normalized_to_playback_safe_opus_frames(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.webm"
            destination = root / "normalized.webm"
            source.write_bytes(b"source")
            recording = Recording(
                id="browser-audio",
                course_id=None,
                course_name="테스트",
                title="브라우저 녹음",
                source_kind="browser",
                extension=".webm",
                status="completed",
                language="ko",
                prompt="",
                corrections="",
                format_transcript=True,
                llm_enabled=False,
                llm_model="",
                output_dir=str(root),
            )
            cancellation = Mock()

            def encode(operation, token, **kwargs):
                self.assertEqual(operation, "transcode")
                self.assertTrue(kwargs["playback"])
                self.assertIs(token, cancellation)
                destination.write_bytes(b"normalized")

            with patch("web.backend.recording_output.media_request", side_effect=encode):
                RecordingPipeline._store_playable_audio(recording, source, destination, cancellation)
            self.assertEqual(destination.read_bytes(), b"normalized")


    def test_intake_assigns_unique_titles_before_processing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, output = self.environment(root)
            store = LectureStore(root / "test.db", environment)
            pipeline = RecordingPipeline(store, JobState(), FakeTranscriber(), root / "work", start_worker=False)
            client = create_app(store, JobState(), FakeTranscriber(), environment, pipeline).test_client()
            (output / "Lecture.mp3").write_bytes(b"original")
            (output / "Lecture (2).md").write_text("original note")
            browser = client.post("/api/recordings", json={"title": "Lecture", "extension": ".webm"})
            self.assertEqual(browser.status_code, 201)
            self.assertEqual(browser.get_json()["recording"]["title"], "Lecture (3)")
            upload = client.post("/api/upload", data={"title": "Lecture", "file": (io.BytesIO(b"audio"), "lecture.wav")})
            self.assertEqual(upload.status_code, 202)
            recording = upload.get_json()["recording"]
            self.assertEqual(recording["title"], "Lecture (4)")
            self.assertEqual(pipeline.state.get()["title"], "Lecture (4)")
            pipeline.process_next()
            saved = store.get_recording(recording["id"])
            self.assertEqual(saved.title, "Lecture (4)")
            self.assertEqual(Path(saved.audio_path).name, "Lecture (4).wav")
            self.assertEqual(Path(saved.note_path).name, "Lecture (4).md")
            self.assertEqual((output / "Lecture.mp3").read_bytes(), b"original")
            self.assertEqual((output / "Lecture (2).md").read_text(), "original note")


    def test_concurrent_intake_reserves_distinct_titles(self) -> None:
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, output = self.environment(root)
            store = LectureStore(root / "test.db", environment)
            pipeline = RecordingPipeline(store, JobState(), FakeTranscriber(), root / "work", start_worker=False)
            settings = store.get()
            barrier = threading.Barrier(4)
            def begin(index):
                barrier.wait()
                if index % 2:
                    return pipeline.create_upload(settings, "Lecture", ".wav", io.BytesIO(b"audio")).title
                return pipeline.begin_chunked(settings, "Lecture", ".webm").title
            with ThreadPoolExecutor(max_workers=4) as workers:
                titles = list(workers.map(begin, range(4)))
            self.assertEqual(set(titles), {"Lecture", "Lecture (2)", "Lecture (3)", "Lecture (4)"})


    def test_dashboard_upload_is_the_only_transcription_route(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, output = self.environment(root)
            store = LectureStore(root / "web/data/lecorder.db", environment)
            store.update_course(store.active_course_id(), name="Physics", language="en", prompt="Coulomb law")
            engine = FakeTranscriber()
            state = JobState()
            pipeline = RecordingPipeline(store, state, engine, root / "work", start_worker=False)
            app = create_app(store, state, engine, environment, pipeline)
            client = app.test_client()

            dashboard = client.get("/")
            self.assertEqual(dashboard.status_code, 200)
            dashboard_html = dashboard.get_data(as_text=True)
            self.assertNotIn('target="_blank"', dashboard_html)
            self.assertIn('href="/guide"', dashboard_html)
            dashboard.close()
            guide = client.get("/guide")
            self.assertEqual(guide.status_code, 200)
            guide.close()
            self.assertEqual(client.post("/api/prepare").status_code, 404)
            self.assertEqual(client.post("/v1/audio/transcriptions").status_code, 404)
            uploaded = client.post(
                "/api/upload",
                data={"title": "Lecture", "file": (io.BytesIO(b"audio"), "source.wav")},
                content_type="multipart/form-data",
            )
            self.assertEqual(uploaded.status_code, 202)
            recording_id = uploaded.get_json()["recording"]["id"]
            self.assertEqual(
                client.delete(f"/api/recordings/{recording_id}/history").status_code, 400
            )
            pipeline.process_next()
            self.assertEqual(engine.calls[-1].course_name, "Physics")
            self.assertEqual(engine.calls[-1].recognition_hint, "")
            self.assertTrue((output / "Lecture.wav").exists())
            self.assertTrue((output / "Lecture.md").exists())
            self.assertTrue(
                'type: lecture-transcript' in (output / "Lecture.md").read_text(encoding="utf-8")
            )
            note = (output / "Lecture.md").read_text(encoding="utf-8")
            self.assertIn("[00:12](Lecture.wav#t=12) 정리된 전사문", note)
            self.assertNotIn("# Lecture\n", note)
            self.assertFalse((root / "work" / recording_id).exists())
            details = json.loads(store.get_recording(recording_id).quality_json)
            self.assertEqual(details["pipeline_version"], 2)
            self.assertEqual(details["segment_count"], 1)
            self.assertEqual(details["conversion_seconds"], .1)
            self.assertEqual(details["whisper_primary_seconds"], .2)
            self.assertEqual(details["llm_seconds"], .3)
            self.assertEqual(details["settings"]["entropy_thold"], "2.8")
            self.assertEqual(details["llm_requests"], 2)
            self.assertEqual(details["llm_prompt_tokens"], 120)
            self.assertEqual(details["llm_output_tokens"], 24)
            self.assertIn("saving_seconds", details)
            self.assertIn("total_seconds", details)
            removed = client.delete(f"/api/recordings/{recording_id}/history")
            self.assertEqual(removed.status_code, 400)  # queued review still owns this result
            store.update_recording(recording_id, review_status="completed")
            removed = client.delete(f"/api/recordings/{recording_id}/history")
            self.assertEqual(removed.status_code, 200)
            self.assertEqual(
                client.delete(f"/api/recordings/{recording_id}/history").status_code, 404
            )
            with self.assertRaises(KeyError):
                store.get_recording(recording_id)
            self.assertTrue((output / "Lecture.wav").exists())
            self.assertTrue((output / "Lecture.md").exists())


    def test_chunked_recording_queue_cleans_temporary_files_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, output = self.environment(root)
            store = LectureStore(root / "web/data/lecorder.db", environment)
            engine = FakeTranscriber()
            pipeline = RecordingPipeline(store, JobState(), engine, root / "work", start_worker=False)
            recording = pipeline.begin_chunked(store.get(), "긴 강의", ".webm")
            pipeline.save_chunk(recording.id, 0, io.BytesIO(b"first"))
            pipeline.save_chunk(recording.id, 1, io.BytesIO(b"second"))
            pipeline.finalize_chunked(recording.id, 18.0)
            pipeline.process_next()
            completed = store.get_recording(recording.id)
            self.assertEqual(completed.status, "completed")
            self.assertEqual((output / "긴 강의.webm").read_bytes(), b"firstsecond")
            self.assertFalse(pipeline.folder(recording.id).exists())


    def test_recoverable_recording_can_be_deleted_with_its_saved_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, _ = self.environment(root)
            store = LectureStore(root / "web/data/lecorder.db", environment)
            pipeline = RecordingPipeline(
                store, JobState(), FakeTranscriber(), root / "work", start_worker=False
            )
            recording = pipeline.begin_chunked(store.get(), "중단된 녹음", ".webm")
            pipeline.save_chunk(recording.id, 0, io.BytesIO(b"recoverable audio"))
            store.update_recording(
                recording.id,
                status="recoverable",
                error="앱이 종료되어 작업이 중단되었습니다.",
            )
            app = create_app(store, JobState(), FakeTranscriber(), environment, pipeline)
            client = app.test_client()

            deleted = client.delete(f"/api/recordings/{recording.id}")

            self.assertEqual(deleted.status_code, 200)
            self.assertFalse(pipeline.folder(recording.id).exists())
            with self.assertRaises(KeyError):
                store.get_recording(recording.id)


    def test_pipeline_startup_removes_only_untracked_work_folders(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, _ = self.environment(root)
            store = LectureStore(root / "web/data/lecorder.db", environment)
            work = root / "work"
            orphan = work / "orphan"
            orphan.mkdir(parents=True)
            (orphan / "source.wav").write_bytes(b"temporary")
            recording = store.create_recording(
                "persisted", store.get(), "복구 대상", "browser", ".webm",
                str(work / "persisted/source.webm"), "recording",
            )
            tracked = work / recording.id
            tracked.mkdir()
            (tracked / "source.webm").write_bytes(b"keep")

            pipeline = RecordingPipeline(store, JobState(), FakeTranscriber(), work, start_worker=False)
            self.assertTrue(orphan.exists())
            pipeline.cleanup_orphans()
            self.assertFalse(orphan.exists())
            self.assertTrue((tracked / "source.webm").exists())


    def test_interrupted_recording_recovers_contiguous_saved_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, output = self.environment(root)
            store = LectureStore(root / "web/data/lecorder.db", environment)
            pipeline = RecordingPipeline(store, JobState(), FakeTranscriber(), root / "work", start_worker=False)
            recording = pipeline.begin_chunked(store.get(), "복구", ".webm")
            pipeline.save_chunk(recording.id, 0, io.BytesIO(b"saved"))
            pipeline.save_chunk(recording.id, 2, io.BytesIO(b"after-gap"))
            pipeline.retry(recording.id)
            pipeline.process_next()
            self.assertEqual((output / "복구.webm").read_bytes(), b"saved")
            self.assertFalse(pipeline.folder(recording.id).exists())
