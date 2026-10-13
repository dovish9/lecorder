from __future__ import annotations

import io
import tempfile
from pathlib import Path
from unittest.mock import patch

from web.backend.files import OutputFiles
from web.backend.markdown import markdown_target
from web.backend.output_files import rename_output_files
from web.backend.pipeline import RecordingPipeline
from web.backend.routes import create_app
from web.backend.state import JobState
from web.backend.store import LectureStore
from web.backend.transcription import Result, Suggestion, TranscriptSegment


from tests.support import RecorderCase, FakeTranscriber

class OutputsTests(RecorderCase):
    def test_rename_rewrites_audio_references_once_and_can_roll_back(self) -> None:
        for name in ("lecture", "강의 자료"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                audio = root / f"{name}.wav"
                note = root / f"{name}.md"
                audio.write_bytes(b"audio")
                original = f'audio: "{audio.name}"\n[00:01]({markdown_target(audio.name)}#t=1)'
                note.write_text(original)
                destinations, rollback = rename_output_files([audio, note], f"new prefix-{name}")
                self.assertEqual(destinations[0].read_bytes(), b"audio")
                self.assertEqual(destinations[1].read_text(),
                                 f'audio: "new prefix-{audio.name}"\n[00:01]({markdown_target("new prefix-" + audio.name)}#t=1)')
                rollback()
                self.assertEqual(note.read_text(), original)
                self.assertTrue(audio.exists())


    def test_rename_failure_rolls_back_only_files_actually_moved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audio, note = root / "old.wav", root / "old.md"
            audio.write_bytes(b"original audio")
            note.write_text("original note")
            original_rename = Path.rename
            def fail_second(source, destination):
                if source == note:
                    destination.write_text("unrelated concurrent file")
                    raise OSError("simulated rename failure")
                return original_rename(source, destination)
            with patch.object(Path, "rename", fail_second), self.assertRaises(OSError):
                rename_output_files([audio, note], "new")
            self.assertEqual(audio.read_bytes(), b"original audio")
            self.assertEqual(note.read_text(), "original note")
            self.assertEqual((root / "new.md").read_text(), "unrelated concurrent file")


    def test_output_names_never_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            files = OutputFiles(output)
            (output / "Lecture.webm").write_bytes(b"existing")
            audio, note = files.pair("Lecture", ".webm")
            self.assertEqual(audio.name, "Lecture (2).webm")
            self.assertEqual(note.name, "Lecture (2).md")


    def test_storage_endpoint_reads_output_folder_live_without_database_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, output = self.environment(root)
            store = LectureStore(root / "web/data/lecorder.db", environment)
            pipeline = RecordingPipeline(
                store, JobState(), FakeTranscriber(), root / "work", start_worker=False
            )
            app = create_app(store, JobState(), FakeTranscriber(), environment, pipeline)
            client = app.test_client()
            (output / "강의.md").write_text("transcript", encoding="utf-8")
            ((output / "강의.wav").resolve()).write_bytes(b"audio")
            (output / "ignore.txt").write_text("ignore", encoding="utf-8")
            (output / ".강의.playback-0123456789abcdef.webm").write_bytes(b"temporary")
            (output / ".강의.webm.playback-0123456789abcdef.tmp").write_bytes(b"temporary")

            first = client.get("/api/storage").get_json()
            self.assertEqual({item["name"] for item in first["files"]}, {"강의.md", "강의.wav"})
            self.assertEqual(len(first["items"]), 1)
            self.assertEqual(first["items"][0]["name"], "강의")
            self.assertEqual(first["items"][0]["kind"], "pair")
            self.assertEqual(first["paired_count"], 1)
            (output / "새 녹음.webm").write_bytes(b"new")
            (output / "AIFF 수업.aiff").write_bytes(b"aiff")
            second = client.get("/api/storage").get_json()
            self.assertIn("새 녹음.webm", {item["name"] for item in second["files"]})
            unpaired = {(item["name"], item["kind"]) for item in second["items"]}
            self.assertIn(("새 녹음", "audio"), unpaired)
            self.assertIn(("AIFF 수업", "audio"), unpaired)
            served = client.get("/api/storage/file?name=강의.md")
            self.assertEqual(served.status_code, 200)
            self.assertEqual(served.get_data(as_text=True), "transcript")
            served.close()
            audio_range = client.get(
                "/api/storage/file?name=강의.wav", headers={"Range": "bytes=1-3"}
            )
            self.assertEqual(audio_range.status_code, 206)
            self.assertEqual(audio_range.get_data(), b"udi")
            self.assertEqual(audio_range.headers["Content-Range"], "bytes 1-3/5")
            audio_range.close()
            prepared_wav = client.post(
                "/api/storage/audio/prepare", json={"name": "강의.wav"}
            )
            self.assertEqual(prepared_wav.status_code, 200)
            self.assertFalse(prepared_wav.get_json()["prepared"])

            needs_index = output / "인덱스 없음.webm"
            needs_index.write_bytes(b"original")
            probe_results = iter([0, 12.5])

            def fake_media_command(operation, **kwargs):
                if operation == "probe":
                    return {"duration": next(probe_results), "short_frames": False}
                Path(kwargs["destination"]).write_bytes(b"optimized")
                return {}

            with patch("web.backend.output_files.media_request", side_effect=fake_media_command):
                prepared_webm = client.post(
                    "/api/storage/audio/prepare", json={"name": needs_index.name}
                )
            self.assertEqual(prepared_webm.status_code, 200)
            self.assertTrue(prepared_webm.get_json()["prepared"])
            self.assertEqual(prepared_webm.get_json()["duration"], 12.5)
            self.assertEqual(needs_index.read_bytes(), b"optimized")

            short_frames = output / "짧은 프레임.webm"
            short_frames.write_bytes(b"short-opus")
            short_probe_results = iter([2.75, 2.78])
            media_commands = []

            def fake_short_frame_command(operation, **kwargs):
                media_commands.append((operation, kwargs))
                if operation == "probe":
                    return {"duration": next(short_probe_results), "short_frames": True}
                Path(kwargs["destination"]).write_bytes(b"normalized-opus")
                return {}

            with patch("web.backend.output_files.media_request", side_effect=fake_short_frame_command):
                normalized_webm = client.post(
                    "/api/storage/audio/prepare", json={"name": short_frames.name}
                )
            self.assertEqual(normalized_webm.status_code, 200)
            self.assertTrue(normalized_webm.get_json()["prepared"])
            self.assertEqual(short_frames.read_bytes(), b"normalized-opus")
            encode = next(args for op, args in media_commands if op == "transcode")
            self.assertTrue(encode["playback"])
            self.assertTrue(str(encode["destination"]).endswith(".tmp"))
            self.assertEqual(client.get("/api/storage/file?name=.강의.playback-0123456789abcdef.webm").status_code, 400)
            self.assertEqual(client.get("/api/storage/file?name=../강의.md").status_code, 400)
            self.assertEqual(client.delete("/api/storage/file?name=../강의.md").status_code, 400)
            wrong_method = client.post("/api/storage/file")
            self.assertEqual(wrong_method.status_code, 405)
            self.assertEqual(wrong_method.get_json()["error"], "현재 실행 중인 앱이 이 요청 방식을 지원하지 않습니다.")
            with patch("web.backend.platforms.host.reveal") as opened:
                revealed = client.post("/api/storage/reveal", json={"name": "강의.wav"})
            self.assertEqual(revealed.status_code, 200)
            self.assertEqual(opened.call_args.args[0], (output / "강의.wav").resolve())

            deleted = client.delete("/api/storage/file?name=강의.md")
            self.assertEqual(deleted.status_code, 200)
            self.assertFalse((output / "강의.md").exists())
            self.assertTrue(((output / "강의.wav").resolve()).exists())
            after_markdown_delete = client.get("/api/storage").get_json()
            lecture = next(item for item in after_markdown_delete["items"] if item["name"] == "강의")
            self.assertEqual(lecture["kind"], "audio")
            self.assertEqual(lecture["markdown_files"], [])
            self.assertEqual(client.delete("/api/storage/file?name=강의.md").status_code, 404)

            deleted = client.delete("/api/storage/file?name=강의.wav")
            self.assertEqual(deleted.status_code, 200)
            self.assertFalse(((output / "강의.wav").resolve()).exists())

            with store._connect() as db:
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertNotIn("storage", tables)
            self.assertNotIn("files", tables)


    def test_recording_title_and_file_deletion_menu_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, output = self.environment(root)
            store = LectureStore(root / "web/data/lecorder.db", environment)
            pipeline = RecordingPipeline(
                store, JobState(), FakeTranscriber(), root / "work", start_worker=False
            )
            app = create_app(store, JobState(), FakeTranscriber(), environment, pipeline)
            client = app.test_client()
            audio = output / "이전 제목.wav"
            note = output / "이전 제목.md"
            audio.write_bytes(b"audio")
            note.write_text(
                'audio: "이전 제목.wav"\n[00:01](이전%20제목.wav#t=1) note',
                encoding="utf-8",
            )
            recording = store.create_recording(
                "menu-actions", store.get(), "이전 제목", "upload", ".wav", status="completed"
            )
            store.update_recording(
                recording.id, audio_path=str(audio), note_path=str(note), completed_at=store.now()
            )

            occupied = output / "사용 중.wav"
            occupied.write_bytes(b"occupied")
            collision = client.patch(
                f"/api/recordings/{recording.id}", json={"title": "사용 중"}
            )
            self.assertEqual(collision.status_code, 400)
            self.assertTrue(audio.exists())
            self.assertTrue(note.exists())
            self.assertEqual(store.get_recording(recording.id).title, "이전 제목")

            renamed = client.patch(
                f"/api/recordings/{recording.id}", json={"title": "새 제목"}
            )
            self.assertEqual(renamed.status_code, 200)
            self.assertEqual(renamed.get_json()["recording"]["title"], "새 제목")
            self.assertEqual(set(renamed.get_json()["renamed_files"]), {"새 제목.wav", "새 제목.md"})
            saved = store.get_recording(recording.id)
            self.assertEqual(saved.title, "새 제목")
            self.assertEqual(Path(saved.audio_path).name, "새 제목.wav")
            self.assertEqual(Path(saved.note_path).name, "새 제목.md")
            audio = output / "새 제목.wav"
            note = output / "새 제목.md"
            self.assertTrue(audio.exists())
            self.assertTrue(note.exists())
            self.assertFalse((output / "이전 제목.wav").exists())
            self.assertFalse((output / "이전 제목.md").exists())
            renamed_note = note.read_text(encoding="utf-8")
            self.assertIn('audio: "새 제목.wav"', renamed_note)
            self.assertIn("새%20제목.wav#t=1", renamed_note)

            deleted = client.delete(f"/api/recordings/{recording.id}/with-files")
            self.assertEqual(deleted.status_code, 200)
            self.assertEqual(set(deleted.get_json()["deleted_files"]), {audio.name, note.name})
            self.assertFalse(audio.exists())
            self.assertFalse(note.exists())
            with self.assertRaises(KeyError):
                store.get_recording(recording.id)

            cancelled_audio = output / "중단됨.wav"
            cancelled_audio.write_bytes(b"cancelled")
            cancelled = store.create_recording(
                "cancelled-menu-action", store.get(), "중단됨", "upload", ".wav", status="cancelled"
            )
            store.update_recording(
                cancelled.id, audio_path=str(cancelled_audio), review_status="processing",
                review_error="이전 검수 작업", completed_at=store.now(),
            )
            deleted_cancelled = client.delete(f"/api/recordings/{cancelled.id}/with-files")
            self.assertEqual(deleted_cancelled.status_code, 200)
            self.assertFalse(cancelled_audio.exists())
            with self.assertRaises(KeyError):
                store.get_recording(cancelled.id)

            orphan_audio = output / "파일만.wav"
            orphan_note = output / "파일만.md"
            orphan_audio.write_bytes(b"audio")
            orphan_note.write_text("[00:01](파일만.wav#t=1) note", encoding="utf-8")
            renamed_files = client.patch(
                "/api/storage/files",
                json={"names": [orphan_audio.name, orphan_note.name], "title": "바뀐 파일"},
            )
            self.assertEqual(renamed_files.status_code, 200)
            self.assertEqual(
                set(renamed_files.get_json()["files"]), {"바뀐 파일.wav", "바뀐 파일.md"}
            )
            orphan_audio = output / "바뀐 파일.wav"
            orphan_note = output / "바뀐 파일.md"
            self.assertIn("바뀐%20파일.wav#t=1", orphan_note.read_text(encoding="utf-8"))
            batch = client.delete(
                "/api/storage/files", json={"names": [orphan_audio.name, orphan_note.name]}
            )
            self.assertEqual(batch.status_code, 200)
            self.assertFalse(orphan_audio.exists())
            self.assertFalse(orphan_note.exists())


    def test_accepting_suggestion_updates_note_without_learning_corrections(self) -> None:
        class SuggestedTranscriber(FakeTranscriber):
            def transcribe(self, source, options, incoming=None, progress=None,
                           cancellation=None) -> Result:
                self.calls.append(options)
                return Result(
                    "이 표정을 사용합니다.",
                    suggestions=(Suggestion("s00001", "표정을", "표본을", "통계 문맥", .96),),
                    segments=(TranscriptSegment(1, 3.0, 8.0, "이 표정을 사용합니다."),),
                    duration_seconds=8.0,
                )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, output = self.environment(root)
            store = LectureStore(root / "web/data/lecorder.db", environment)
            store.update_course(store.active_course_id(), name="통계학")
            engine = SuggestedTranscriber()
            pipeline = RecordingPipeline(store, JobState(), engine, root / "work", start_worker=False)
            recording = pipeline.create_upload(store.get(), "검토", ".wav", io.BytesIO(b"audio"))
            pipeline.process_next()
            store.update_recording(recording.id, review_status="completed")
            suggestion = store.list_suggestions(recording.id)[0]
            self.assertEqual(suggestion.status, "pending")
            app = create_app(store, JobState(), engine, environment, pipeline)
            client = app.test_client()
            review = client.get(f"/api/recordings/{recording.id}").get_json()
            self.assertEqual(review["suggestions"][0]["start"], 3.0)
            self.assertEqual(review["suggestions"][0]["end"], 8.0)
            self.assertEqual(review["suggestions"][0]["audio_name"], "검토.wav")
            audio_response = client.get(f"/api/recordings/{recording.id}/audio")
            self.assertEqual(audio_response.get_data(), b"audio")
            audio_response.close()
            pipeline.decide_suggestion(suggestion.id, "accept")
            self.assertEqual(store.get_course(store.active_course_id()).corrections, "")
            self.assertIn("표본을 사용합니다", (output / "검토.md").read_text(encoding="utf-8"))
            self.assertEqual(store.get_suggestion(suggestion.id).status, "accepted")
