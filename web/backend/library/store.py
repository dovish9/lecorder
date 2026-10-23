from __future__ import annotations

import sqlite3
import json
import threading
from contextlib import contextmanager, closing
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from web.backend.config import DB_FILE, EnvironmentStore
from web.backend.ai_settings import AISettings, SETTING_FILE
from web.backend.config import DATA_DIR
from web.backend.result_codec import COLUMNS, encode_column, loads as result_loads
from web.backend.library.records import ActiveSettings, Recording


class LectureStore:

    def __init__(
        self,
        path: Path = DB_FILE,
        environment: EnvironmentStore | None = None,
    ) -> None:
        from web.backend.library.courses import CourseRepository
        self.courses = CourseRepository(self)
        from web.backend.library.approvals import ApprovalRepository
        self.approvals = ApprovalRepository(self)
        self.path = path
        self.environment = environment or EnvironmentStore()
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.ai = AISettings(SETTING_FILE if self.path.parent.resolve()==DATA_DIR.resolve() else self.path.parent / 'ai-settings.json')
        from web.backend.migrations import DatasetLock, apply
        import weakref
        self._dataset_lock = DatasetLock(self.path)
        self._finalize_lock = weakref.finalize(self, self._dataset_lock.close)
        try:
            with closing(sqlite3.connect(self.path)) as db:
                apply(db, self.path)
                db.execute('PRAGMA journal_mode=WAL')
            with self._connect() as db:
                self.dataset_id = db.execute("SELECT value FROM app_state WHERE key='dataset_id'").fetchone()[0]
        except BaseException:
            self._finalize_lock()
            raise

    def close(self):
        self._finalize_lock()

    def transaction(self, *, raw=False):
        """One commit/rollback boundary; callers never own the DB connection."""
        return self._connect(raw=raw)

    @contextmanager
    def _connect(self, *, raw=False) -> Iterator[sqlite3.Connection]:
        self._lock.acquire()
        try:
            connection = sqlite3.connect(self.path, timeout=10)
        except Exception:
            self._lock.release()
            raise
        try:
            def document_row(cursor, values):
                values = list(values)
                for i, column in enumerate(cursor.description):
                    name = column[0]
                    if name in COLUMNS or name in {'payload','result'}:
                        expected = COLUMNS[name][0] if name in COLUMNS else None
                        values[i] = json.dumps(result_loads(values[i], expected), ensure_ascii=False)
                return sqlite3.Row(cursor, tuple(values))
            connection.row_factory = sqlite3.Row if raw else document_row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 10000")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
            self._lock.release()

    def recover_interrupted(self) -> None:
        """Recover abandoned jobs once, when the processing worker starts."""
        with self._lock, self._connect() as db:
            db.execute("UPDATE transcription_runs SET status='interrupted', completed_at=?, error='앱 종료로 중단되었습니다.' WHERE completed_at=''", (self.now(),))
            db.execute(
                "UPDATE recordings SET status = 'recoverable', "
                "error = '앱이 종료되어 작업이 중단되었습니다.' "
                "WHERE status = 'recording'"
            )
            db.execute(
                "UPDATE recordings SET status = 'queued', "
                "error = '앱이 종료되어 완료된 구간부터 복구합니다.' "
                "WHERE status = 'processing'"
            )
            db.execute("UPDATE recordings SET status='cancelled',error='중단 요청 후 앱이 종료되었습니다.' WHERE status='cancelling'")

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")










    def get(self) -> ActiveSettings:
        course = self.courses.selection_course(self.courses.active_course_id())
        return ActiveSettings(
            course_id=course.id or None,
            course_name=course.name,
            language=course.language,
            format_transcript=True,
            llm_enabled=True,
            output_dir=self.environment.get().output_dir,
        )

    @staticmethod
    def _recording(row: sqlite3.Row) -> Recording:
        data = dict(row)
        data["course_id"] = int(data["course_id"]) if data["course_id"] is not None else None
        data["format_transcript"] = bool(data["format_transcript"])
        data["llm_enabled"] = bool(data["llm_enabled"])
        data["duration_seconds"] = float(data["duration_seconds"])
        data["processing_seconds"] = float(data["processing_seconds"])
        return Recording(**data)


    def create_recording(self, recording_id: str, settings: ActiveSettings, title: str,
                         source_kind: str, extension: str, source_path: str = "",
                         status: str = "queued") -> Recording:
        now = self.now()
        ai = self.ai.snapshot()
        with self._lock, self._connect() as db:
            db.execute(
                """INSERT INTO recordings(
                    id, course_id, course_name, title, source_kind, extension, status,
                    language, format_transcript, llm_enabled,
                    llm_model, output_dir, source_path, created_at, ai_settings_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (recording_id, settings.course_id, settings.course_name, title, source_kind,
                 extension, status, settings.language,
                 1, 1, ai["llm"]["model"],
                 settings.output_dir, source_path, now, json.dumps(ai)),
            )
        return self.get_recording(recording_id)

    def get_recording(self, recording_id: str) -> Recording:
        with self._connect() as db:
            row = db.execute("SELECT * FROM recordings WHERE id = ?", (recording_id,)).fetchone()
        if row is None:
            raise KeyError("작업을 찾지 못했습니다.")
        return self._recording(row)

    def queue_retranscription(self, recording_id: str, source: Path, *, course_id=None, output_changes=None) -> Recording:
        """Snapshot the linked course's current settings for this new attempt."""
        ai = self.ai.snapshot()
        with self._lock, self._connect() as db:
            row = db.execute("SELECT * FROM recordings WHERE id = ?", (recording_id,)).fetchone()
            if row is None:
                raise KeyError("작업을 찾지 못했습니다.")
            if row["status"] not in {"completed", "failed", "cancelled"}:
                raise ValueError("완료·실패·중단된 작업만 다시 음성 변환할 수 있습니다.")
            course = db.execute("SELECT * FROM courses WHERE id = ?", (course_id if course_id is not None else row["course_id"],)).fetchone()
            if course_id not in (None, 0) and course is None:
                raise ValueError("강의를 찾지 못했습니다.")
            changes = dict(status="queued", source_path=str(source), error="", started_at="", completed_at="",
                           llm_model=ai["llm"]["model"], ai_settings_json=json.dumps(ai), last_operation="retranscribe")
            # Deleted courses retain the job's snapshot; never borrow the selected course.
            if course is not None:
                changes.update(course_id=course["id"], course_name=course["name"])
                changes["language"] = course["language"]
            if course_id == 0:
                changes.update(course_id=None, course_name="선택 안 함", language="auto", format_transcript=1, llm_enabled=1)
            changes.update(format_transcript=1, llm_enabled=1)
            changes.update(output_changes or {})
            assignments = ", ".join(f"{key} = ?" for key in changes)
            db.execute(f"UPDATE recordings SET {assignments} WHERE id = ?",
                       (*(encode_column(k,v) if k in COLUMNS else v for k,v in changes.items()), recording_id))
        return self.get_recording(recording_id)

    def update_recording(self, recording_id: str, **changes: Any) -> Recording:
        allowed = {
            "title", "status", "source_path", "audio_path", "note_path", "duration_seconds",
            "processing_seconds", "transcript_text", "segments_json", "breaks_json",
            "quality_json", "error", "started_at", "completed_at",
            "note_revision_id", "note_snapshot_json", "review_status", "review_error", "llm_model", "llm_enabled", "format_transcript",
            "last_operation", "ai_settings_json",
        }
        values = {key: encode_column(key,value) if key in COLUMNS else value for key,value in changes.items() if key in allowed}
        if not values:
            return self.get_recording(recording_id)
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self._lock, self._connect() as db:
            cursor = db.execute(
                f"UPDATE recordings SET {assignments} WHERE id = ?",
                (*values.values(), recording_id),
            )
            if cursor.rowcount != 1:
                raise KeyError("작업을 찾지 못했습니다.")
        return self.get_recording(recording_id)

    def list_recordings(self, limit: int = 30) -> list[Recording]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM recordings ORDER BY created_at DESC LIMIT ?",
                (max(1, min(int(limit), 100)),),
            ).fetchall()
        return [self._recording(row) for row in rows]

    def total_processing_seconds(self, recording: Recording) -> float:
        """Return the latest transcript plus its independent LLM review time.

        Queue time deliberately never contributes.  Old runs without metrics use
        their persisted start/end timestamps so their audit view remains useful.
        """
        try:
            quality = json.loads(recording.quality_json or "{}")
        except json.JSONDecodeError:
            quality = {}
        total = float(quality.get("total_seconds") or recording.processing_seconds or 0)
        with self._connect() as db:
            row = db.execute(
                "SELECT started_at, completed_at, metrics FROM review_runs "
                "WHERE recording_id=? AND started_at>=? AND completed_at<>'' "
                "ORDER BY started_at DESC LIMIT 1",
                (recording.id, recording.started_at),
            ).fetchone()
        if not row:
            return total
        try:
            metrics = json.loads(row["metrics"] or "{}")
        except json.JSONDecodeError:
            metrics = {}
        review_total = metrics.get("total_seconds")
        if review_total is None:
            try:
                review_total = max(0.0, (datetime.fromisoformat(row["completed_at"]) - datetime.fromisoformat(row["started_at"])).total_seconds())
            except (TypeError, ValueError):
                review_total = 0.0
        return total + float(review_total or 0)

    def recording_titles(self, output_dir: Path) -> set[str]:
        """Include unfinished jobs whose output files do not exist yet."""
        folder = output_dir.expanduser().resolve()
        with self._connect() as db:
            rows = db.execute("SELECT DISTINCT title, output_dir FROM recordings").fetchall()
        return {
            row["title"] for row in rows
            if Path(row["output_dir"]).expanduser().resolve() == folder
        }

    def queued_recordings(self) -> list[Recording]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM recordings WHERE status = 'queued' ORDER BY created_at"
            ).fetchall()
        return [self._recording(row) for row in rows]

    def recording_ids(self) -> set[str]:
        """Return every persisted work ID for conservative temp-folder cleanup."""
        with self._connect() as db:
            rows = db.execute("SELECT id FROM recordings").fetchall()
        return {str(row["id"]) for row in rows}

    def _delete_recording_in_states(
        self, recording_id: str, allowed: frozenset[str], invalid_message: str
    ) -> None:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT status FROM recordings WHERE id = ?", (recording_id,)
            ).fetchone()
            if row is None:
                raise KeyError("작업을 찾지 못했습니다.")
            if str(row["status"]) not in allowed:
                raise ValueError(invalid_message)
            db.execute("DELETE FROM recordings WHERE id = ?", (recording_id,))
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='recovery_sessions'").fetchone():
                db.execute('DELETE FROM recovery_sessions WHERE owner=?', (recording_id,))

    def discard_recording(self, recording_id: str) -> None:
        self._delete_recording_in_states(
            recording_id,
            frozenset({"recording", "recoverable"}),
            "진행 중이거나 복구 가능한 녹음만 폐기할 수 있습니다.",
        )

    def delete_recording_history(self, recording_id: str) -> None:
        self._delete_recording_in_states(
            recording_id,
            frozenset({"completed", "failed", "cancelled"}),
            "완료·실패·중단된 작업만 최근 작업에서 삭제할 수 있습니다.",
        )



    def commit_result(self, recording: Recording, suggestions: list[dict[str, Any]],
                      publish: Callable[[], None], *, output_paths=()) -> None:
        """Publish files while committing transcript and review data together."""
        values = asdict(recording)
        values.pop("id")
        values = {key:encode_column(key,value) if key in COLUMNS else value for key,value in values.items()}
        from web.backend.publication import Publication
        journal = Publication(self, recording, output_paths) if output_paths else None
        try:
            with self._lock, self._connect() as db:
                assignments = ", ".join(f"{key} = ?" for key in values)
                db.execute(f"UPDATE recordings SET {assignments} WHERE id = ?",
                           (*values.values(), recording.id))
                self.approvals._replace_suggestions(db, recording.id, suggestions)
                publish()
        except Exception:
            if journal:
                journal.rollback()
            raise
        if journal:
            journal.complete()






