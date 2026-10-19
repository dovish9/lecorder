from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path
from typing import BinaryIO


from .files import OutputFiles, safe_name
from .output_files import rename_output_files
from .records import ActiveSettings, Recording
from .transcription import CancellationToken


class RecordingIntake:
    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex[:12]


    def folder(self, recording_id: str) -> Path:
        return self.work_root / recording_id


    def cleanup_orphans(self) -> None:
        """Remove only work directories that have no corresponding database job."""
        persisted = self.store.recording_ids()
        for item in self.work_root.iterdir():
            if item.name == '.live':
                for draft in item.iterdir():
                    if draft.is_dir() and draft.name not in persisted:
                        shutil.rmtree(draft, ignore_errors=True)
                continue
            if item.is_dir() and item.name not in persisted:
                shutil.rmtree(item, ignore_errors=True)


    def _create_named_recording(self, recording_id: str, settings: ActiveSettings,
                                title: str, source_kind: str, extension: str,
                                source: Path, status: str) -> Recording:
        # Reserve the title in the DB before another intake can choose it.
        with self._lock:
            files = OutputFiles(Path(settings.output_dir))
            title = files.available_title(title, self.store.recording_titles(files.folder))
            return self.store.create_recording(
                recording_id, settings, title, source_kind, extension, str(source), status
            )


    def _preserve_intake(self, recording):
        path, error = self._preserve_failed_audio(recording)
        if not path:
            self.store.update_recording(recording.id, status="failed", error=error)
            raise ValueError(f"원본 보존 실패: {error}")
        return self.store.update_recording(recording.id, audio_path=path)


    def create_upload(self, settings: ActiveSettings, title: str, extension: str,
                      stream: BinaryIO, lecture_note_id: str | None = None) -> Recording:
        revision = self.notes.bind(lecture_note_id, settings.course_id)
        recording_id = self.new_id()
        folder = self.folder(recording_id)
        folder.mkdir(parents=True, exist_ok=False)
        source = folder / f"source{extension}"
        try:
            with source.open("wb") as destination:
                shutil.copyfileobj(stream, destination, length=1024 * 1024)
            if source.stat().st_size == 0:
                raise ValueError("녹음 파일이 비어 있습니다.")
            recording = self._create_named_recording(
                recording_id, settings, title, "upload", extension, source, "queued"
            )
            recording = self._bind_note(recording, revision)
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        recording = self._preserve_intake(recording)
        self.state.set("queued", f"{recording.title} 작업을 대기열에 추가했습니다.",
                       job_id=recording.id, title=recording.title, course_id=settings.course_id)
        self.enqueue(recording.id)
        return recording


    def begin_chunked(self, settings: ActiveSettings, title: str, extension: str, lecture_note_id: str | None = None) -> Recording:
        revision = self.notes.bind(lecture_note_id, settings.course_id)
        recording_id = self.new_id()
        folder = self.folder(recording_id)
        (folder / "chunks").mkdir(parents=True, exist_ok=False)
        source = folder / f"source{extension}"
        try:
            recording = self._create_named_recording(
                recording_id, settings, title, "browser", extension, source, "recording"
            )
            return self._bind_note(recording, revision)
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise


    @staticmethod
    def _note_analysis_pending(revision):
        return revision['status'] in {'queued', 'extracting'} or revision['study_status'] in {'pending', 'analyzing'}


    def _bind_note(self, recording, revision):
        with self.notes.access():
            waiting = revision and self._note_analysis_pending(self.notes.revision(revision))
            snapshot = ({"waiting_for_analysis": True, "keywords": [], "pages": []}
                        if waiting else self.notes.snapshot(revision))
            return self.store.update_recording(recording.id, note_revision_id=revision,
                note_snapshot_json=json.dumps(snapshot, ensure_ascii=False))


    def select_recording_course(self, recording_id: str, course_id: int):
        with self._lock:
            recording = self.store.get_recording(recording_id)
            if recording.status != "recording" or recording.source_kind != "browser":
                raise ValueError("녹음 중인 작업의 강의만 변경할 수 있습니다.")
            course = self.store.selection_course(course_id)
            if (recording.course_id or 0) == course.id:
                return recording
            title = recording.title
            automatic = re.fullmatch(re.escape(safe_name(recording.course_name if recording.course_id else "녹음")) + r"-(\d{1,2}월\d{1,2}일-[월화수목금토일])(?: \(\d+\))?", title)
            if automatic:
                base = safe_name(f"{course.name if course.id else '녹음'}-{automatic.group(1)}")
                reserved = self.store.recording_titles(Path(recording.output_dir)) - {recording.title}
                title = OutputFiles(Path(recording.output_dir)).available_title(base, reserved)
            with self.store.transaction() as db:
                db.execute("""UPDATE recordings SET course_id=?,course_name=?,title=?,language=?,
                    format_transcript=?,llm_enabled=?,note_revision_id=NULL,note_snapshot_json='{}'
                    WHERE id=?""", (course.id or None, course.name, title, course.language,
                    1, 1, recording_id))
            return self.store.get_recording(recording_id)


    def select_recording_note(self, recording_id: str, lecture_note_id: str | None):
        with self._lock:
            recording = self.store.get_recording(recording_id)
            if recording.status not in {"recording", "recoverable"}:
                raise ValueError("녹음 중인 작업의 강의노트만 변경할 수 있습니다.")
            revision = self.notes.bind(lecture_note_id, recording.course_id)
            return self._bind_note(recording, revision)


    def save_chunk(self, recording_id: str, index: int, stream: BinaryIO) -> int:
        with self._lock:
            return self._save_chunk(recording_id,index,stream)

    def _save_chunk(self, recording_id: str, index: int, stream: BinaryIO) -> int:
        import hashlib
        recording = self.store.get_recording(recording_id)
        if recording.status not in {"recording", "recoverable"}:
            raise ValueError("더 이상 녹음 조각을 받을 수 없는 작업입니다.")
        if index < 0 or index > 100000:
            raise ValueError("잘못된 녹음 조각 번호입니다.")
        chunks = self.folder(recording_id) / "chunks"
        chunks.mkdir(parents=True, exist_ok=True)
        destination = chunks / f"{index:08d}.part"
        temporary = chunks / f".{index:08d}.upload"
        size = 0
        try:
            with temporary.open("wb") as output:
                while True:
                    block = stream.read(1024 * 1024)
                    if not block:
                        break
                    size += len(block)
                    if size > 32 * 1024 * 1024:
                        raise ValueError("녹음 조각 하나가 32MB를 초과했습니다.")
                    output.write(block)
            if size == 0:
                raise ValueError("빈 녹음 조각입니다.")
            with temporary.open('rb') as data:
                checksum=hashlib.file_digest(data,'sha256').hexdigest()
            if destination.exists():
                with destination.open('rb') as data:
                    if hashlib.file_digest(data,'sha256').hexdigest()!=checksum:
                        raise ValueError('이미 저장된 녹음 조각과 내용이 다릅니다. 원본은 유지했습니다.')
            else:
                temporary.replace(destination)
            with self.store.transaction() as db:
                db.execute('INSERT OR REPLACE INTO recording_chunks VALUES (?,?,?,?)',(recording_id,index,size,checksum))
            return size
        finally:
            temporary.unlink(missing_ok=True)


    def finalize_chunked(self, recording_id: str, duration_seconds: float = 0.0,
                         allow_partial: bool = False) -> Recording:
        with self._lock:
            return self._finalize_chunked(recording_id, duration_seconds, allow_partial)

    def _finalize_chunked(self, recording_id, duration_seconds, allow_partial):
        recording = self.store.get_recording(recording_id)
        # A reload can lose the successful response and repeat finalization.
        # Never assemble or enqueue an already committed browser recording again.
        if (recording.source_kind == "browser"
                and recording.status in {"queued", "processing", "completed", "failed"}
                and any(value and Path(value).is_file() for value in (recording.source_path, recording.audio_path))):
            return recording
        if recording.status not in {"recording", "recoverable"}:
            raise ValueError("완료할 수 없는 녹음 작업입니다.")
        chunks = sorted((self.folder(recording_id) / "chunks").glob("*.part"))
        if not chunks:
            raise ValueError("저장된 녹음 조각이 없습니다.")
        indices = [int(path.stem) for path in chunks]
        if indices != list(range(indices[-1] + 1)) and not allow_partial:
            raise ValueError("일부 녹음 조각이 누락되어 복구가 필요합니다.")
        if allow_partial:
            contiguous: list[Path] = []
            for expected, chunk in enumerate(chunks):
                if int(chunk.stem) != expected:
                    break
                contiguous.append(chunk)
            chunks = contiguous
            if not chunks:
                raise ValueError("처음부터 이어지는 녹음 조각을 찾지 못했습니다.")
        if getattr(self, "live", None):
            self.live.end(recording_id, reason='recording_finalized')
        source = Path(recording.source_path)
        temporary = source.with_suffix(source.suffix + ".assembling")
        try:
            with temporary.open("wb") as output:
                for chunk in chunks:
                    with chunk.open("rb") as item:
                        shutil.copyfileobj(item, output, length=1024 * 1024)
            temporary.replace(source)
        finally:
            temporary.unlink(missing_ok=True)
        recording = self.store.update_recording(
            recording_id, status="queued", duration_seconds=max(0.0, float(duration_seconds)), error=""
        )
        recording = self._preserve_intake(recording)
        self.state.set("queued", f"{recording.title} 녹음을 안전하게 저장하고 대기열에 추가했습니다.",
                       job_id=recording.id, title=recording.title, course_id=recording.course_id)
        self.enqueue(recording_id, priority=0)
        return recording


    def discard(self, recording_id: str) -> None:
        if getattr(self, "live", None):
            self.live.forget(recording_id)
        self.store.discard_recording(recording_id)
        shutil.rmtree(self.folder(recording_id), ignore_errors=True)


    def delete_history(self, recording_id: str) -> None:
        with self._lock:
            recording = self.store.get_recording(recording_id)
            if recording.status != "cancelled" and recording.review_status in {"queued", "processing"}:
                raise ValueError("검수가 끝난 후 작업을 삭제하세요.")
            self.store.delete_recording_history(recording_id)
            shutil.rmtree(self.folder(recording_id), ignore_errors=True)
            if getattr(self, 'live', None):
                self.live.forget(recording_id)


    def retranscribe(self, recording_id: str, lecture_note_id: str | None = None, *, retain_note=True, course_id=None, title=None) -> Recording:
        """Re-run the same job, preserving its output until replacement succeeds."""
        with self._lock:
            previous = self.store.get_recording(recording_id)
            if previous.status not in {"completed", "failed", "cancelled"}:
                raise ValueError("완료·실패·중단된 작업만 다시 전사할 수 있습니다.")
            if previous.review_status in {"queued", "processing"}:
                raise ValueError("검수가 끝난 후 다시 전사하세요.")
            if recording_id in self.reviews.pending:
                raise ValueError("중단한 검수 작업을 정리 중입니다. 잠시 후 다시 전사하세요.")
            target_course = previous.course_id if course_id is None else course_id
            if course_id is not None:
                if isinstance(course_id, bool) or not isinstance(course_id, int):
                    raise ValueError("강의를 선택하세요.")
                self.store.selection_course(course_id)
            if retain_note and target_course == previous.course_id and previous.note_revision_id:
                lecture_note_id = self.notes.revision(previous.note_revision_id)['note_id']
            revision = self.notes.bind(lecture_note_id, target_course or None, allow_pending=True)
            source = next((Path(value) for value in (previous.source_path, previous.audio_path)
                           if value and Path(value).is_file()), None)
            if source is None:
                raise ValueError("다시 전사할 원본 음성 파일을 찾지 못했습니다.")
            folder = self.folder(recording_id)
            folder.mkdir(parents=True, exist_ok=True)
            working = folder / f"source{previous.extension}"
            try:
                if source.resolve() != working.resolve():
                    shutil.copy2(source, working)
            except OSError as error:
                raise ValueError("원본 음성 파일을 읽거나 작업용 사본을 만들 수 없습니다.") from error
            changes = {}
            rollback = lambda: None
            if title is not None:
                if not isinstance(title, str) or not title.strip():
                    raise ValueError("저장할 파일 이름을 입력해 주세요.")
                title = safe_name(title.strip()[:160])
                if title != previous.title:
                    reserved = self.store.recording_titles(Path(previous.output_dir))
                    if title in reserved:
                        raise ValueError("같은 이름의 작업이 이미 있습니다.")
                    fields, targets = [], []
                    root = Path(previous.output_dir).resolve()
                    for field in ("audio_path", "note_path"):
                        value = getattr(previous, field)
                        if value and Path(value).is_file():
                            path = Path(value).resolve()
                            if path.parent != root:
                                raise ValueError("저장 폴더 밖의 파일은 이름을 수정할 수 없습니다.")
                            fields.append(field)
                            targets.append(path)
                    destinations, rollback = rename_output_files(targets, title)
                    changes.update(zip(fields, map(str, destinations)))
                    changes["title"] = title
            try:
                recording = self.store.queue_retranscription(recording_id, working,
                    course_id=course_id, output_changes=changes)
            except Exception:
                rollback()
                raise
            recording = self._bind_note(recording, revision)
            self._cancellations[recording_id] = CancellationToken()
            self._cancel_stages.pop(recording_id, None)
            self.enqueue(recording_id)
            self.state.set("queued", f"{recording.title} 다시 전사 대기 중",
                           job_id=recording.id, title=recording.title, course_id=recording.course_id)
            return recording


    def retry(self, recording_id: str) -> Recording:
        recording = self.store.get_recording(recording_id)
        if recording.status in {"recording", "recoverable"} and recording.source_kind == "browser":
            return self.finalize_chunked(recording_id, recording.duration_seconds, allow_partial=True)
        if recording.status != "failed":
            raise ValueError("실패한 작업만 다시 처리할 수 있습니다.")
        return self.retranscribe(recording_id)
