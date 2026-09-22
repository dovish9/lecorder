from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import DEFAULT_LLM_MODEL, DB_FILE, LANGUAGES, EnvironmentStore, flag, text
from .records import ActiveSettings, Course, Recording, StoredSuggestion


class LectureStore:
    COURSE_FIELDS = {
        "name", "language",
    }

    def __init__(
        self,
        path: Path = DB_FILE,
        environment: EnvironmentStore | None = None,
    ) -> None:
        self.path = path
        self.environment = environment or EnvironmentStore()
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._setup()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _setup(self) -> None:
        if self.path.exists() and self.path.stat().st_size:
            with self._connect() as source:
                columns = {row[1] for row in source.execute("PRAGMA table_info(recordings)")}
                if columns and "note_revision_id" not in columns:
                    backup = self.path.with_name(self.path.stem + "-before-notes-" + datetime.now().strftime("%Y%m%d%H%M%S") + ".db")
                    target = sqlite3.connect(backup)
                    try:
                        source.backup(target)
                    finally:
                        target.close()
        with self._lock, self._connect() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS courses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    language TEXT NOT NULL DEFAULT 'ko',
                    prompt TEXT NOT NULL DEFAULT '',
                    corrections TEXT NOT NULL DEFAULT '',
                    format_transcript INTEGER NOT NULL DEFAULT 1,
                    llm_enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS app_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS recordings (
                    id TEXT PRIMARY KEY,
                    course_id INTEGER REFERENCES courses(id) ON DELETE SET NULL,
                    course_name TEXT NOT NULL,
                    title TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    extension TEXT NOT NULL,
                    status TEXT NOT NULL,
                    language TEXT NOT NULL,
                    prompt TEXT NOT NULL DEFAULT '',
                    corrections TEXT NOT NULL DEFAULT '',
                    format_transcript INTEGER NOT NULL DEFAULT 1,
                    llm_enabled INTEGER NOT NULL DEFAULT 1,
                    llm_model TEXT NOT NULL,
                    output_dir TEXT NOT NULL,
                    source_path TEXT NOT NULL DEFAULT '',
                    audio_path TEXT NOT NULL DEFAULT '',
                    note_path TEXT NOT NULL DEFAULT '',
                    duration_seconds REAL NOT NULL DEFAULT 0,
                    processing_seconds REAL NOT NULL DEFAULT 0,
                    transcript_text TEXT NOT NULL DEFAULT '',
                    segments_json TEXT NOT NULL DEFAULT '[]',
                    breaks_json TEXT NOT NULL DEFAULT '[]',
                    quality_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    started_at TEXT NOT NULL DEFAULT '',
                    completed_at TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS recordings_created_idx
                    ON recordings(created_at DESC);
                CREATE INDEX IF NOT EXISTS recordings_status_idx
                    ON recordings(status, created_at);
                CREATE TABLE IF NOT EXISTS suggestions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recording_id TEXT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
                    sentence_id TEXT NOT NULL,
                    original TEXT NOT NULL,
                    replacement TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    decided_at TEXT NOT NULL DEFAULT '',
                    UNIQUE(recording_id, sentence_id, original, replacement)
                );
                CREATE INDEX IF NOT EXISTS suggestions_recording_idx
                    ON suggestions(recording_id, status, id);
                """
            )
            db.execute("""CREATE TABLE IF NOT EXISTS transcription_runs (
                id TEXT PRIMARY KEY, recording_id TEXT NOT NULL, started_at TEXT NOT NULL,
                completed_at TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
                settings TEXT NOT NULL, metrics TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '')""")
            db.execute("""CREATE TABLE IF NOT EXISTS review_runs (
                id TEXT PRIMARY KEY, recording_id TEXT NOT NULL, started_at TEXT NOT NULL,
                completed_at TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
                settings TEXT NOT NULL, metrics TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '')""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(recordings)")}
            for name, definition in {
                "note_revision_id": "TEXT", "note_snapshot_json": "TEXT NOT NULL DEFAULT '{}'",
                "review_status": "TEXT NOT NULL DEFAULT 'none'", "review_error": "TEXT NOT NULL DEFAULT ''",
            }.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE recordings ADD COLUMN {name} {definition}")
            db.execute("PRAGMA user_version = 1")
            count = db.execute("SELECT COUNT(*) FROM courses").fetchone()[0]
            if count == 0:
                now = self.now()
                cursor = db.execute(
                    "INSERT INTO courses(name, updated_at) VALUES (?, ?)",
                    ("새 강의", now),
                )
                db.execute(
                    "INSERT OR REPLACE INTO app_state(key, value) VALUES ('active_course_id', ?)",
                    (str(cursor.lastrowid),),
                )

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
                "UPDATE recordings SET status = 'failed', "
                "error = '앱이 종료되어 처리 작업이 중단되었습니다.' "
                "WHERE status IN ('processing', 'cancelling')"
            )

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _course(row: sqlite3.Row) -> Course:
        return Course(
            id=int(row["id"]),
            name=str(row["name"]),
            language=str(row["language"]),
            prompt=str(row["prompt"]),
            corrections=str(row["corrections"]),
            format_transcript=True,
            llm_enabled=True,
            updated_at=str(row["updated_at"]),
        )

    def list_courses(self) -> list[Course]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM courses ORDER BY name COLLATE NOCASE, id"
            ).fetchall()
        return [self._course(row) for row in rows]

    def get_course(self, course_id: int) -> Course:
        with self._connect() as db:
            row = db.execute("SELECT * FROM courses WHERE id = ?", (course_id,)).fetchone()
        if row is None:
            raise KeyError("강의를 찾지 못했습니다.")
        return self._course(row)

    def active_course_id(self) -> int:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT value FROM app_state WHERE key = 'active_course_id'"
            ).fetchone()
            course_id = int(row[0]) if row and row[0].isdigit() else 0
            if row and row[0] == "0":
                return 0
            exists = db.execute("SELECT 1 FROM courses WHERE id = ?", (course_id,)).fetchone()
            if exists:
                return course_id
            fallback = db.execute("SELECT id FROM courses ORDER BY id LIMIT 1").fetchone()
            if fallback is None:
                raise RuntimeError("저장된 강의가 없습니다.")
            course_id = int(fallback[0])
            db.execute(
                "INSERT OR REPLACE INTO app_state(key, value) VALUES ('active_course_id', ?)",
                (str(course_id),),
            )
            return course_id

    def selection_course(self, course_id: int) -> Course:
        return Course(0, "선택 안 함", language="auto", format_transcript=True, llm_enabled=True) if course_id == 0 else self.get_course(course_id)

    def select_course(self, course_id: int) -> Course:
        course = self.selection_course(course_id)
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO app_state(key, value) VALUES ('active_course_id', ?)",
                (str(course.id),),
            )
        return course

    def create_course(self, name: str) -> Course:
        clean = text(name, 80)
        if not clean:
            raise ValueError("강의 이름을 입력해 주세요.")
        try:
            with self._lock, self._connect() as db:
                cursor = db.execute(
                    "INSERT INTO courses(name, updated_at) VALUES (?, ?)",
                    (clean, self.now()),
                )
                course_id = int(cursor.lastrowid)
        except sqlite3.IntegrityError as error:
            raise ValueError("같은 이름의 강의가 이미 있습니다.") from error
        return self.select_course(course_id)

    def update_course(self, course_id: int, **changes: Any) -> Course:
        current = self.get_course(course_id)
        values: dict[str, Any] = {}
        for key, raw in changes.items():
            if key not in self.COURSE_FIELDS:
                continue
            if key == "name":
                value = text(raw, 80)
                if not value:
                    raise ValueError("강의 이름을 비워 둘 수 없습니다.")
            elif key == "language":
                value = text(raw, 10)
                if value not in LANGUAGES:
                    raise ValueError("지원하지 않는 언어입니다.")
            else:
                value = int(flag(raw, getattr(current, key)))
            values[key] = value
        if not values:
            return current
        values["updated_at"] = self.now()
        assignments = ", ".join(f"{key} = ?" for key in values)
        try:
            with self._lock, self._connect() as db:
                cursor = db.execute(
                    f"UPDATE courses SET {assignments} WHERE id = ?",
                    (*values.values(), course_id),
                )
                if cursor.rowcount != 1:
                    raise KeyError("강의를 찾지 못했습니다.")
        except sqlite3.IntegrityError as error:
            raise ValueError("같은 이름의 강의가 이미 있습니다.") from error
        return self.get_course(course_id)

    def delete_course(self, course_id: int) -> int:
        with self._lock, self._connect() as db:
            count = db.execute("SELECT COUNT(*) FROM courses").fetchone()[0]
            if count <= 1:
                raise ValueError("강의는 최소 하나가 필요합니다.")
            cursor = db.execute("DELETE FROM courses WHERE id = ?", (course_id,))
            if cursor.rowcount != 1:
                raise KeyError("강의를 찾지 못했습니다.")
            fallback = db.execute("SELECT id FROM courses ORDER BY name LIMIT 1").fetchone()
            active_row = db.execute(
                "SELECT value FROM app_state WHERE key = 'active_course_id'"
            ).fetchone()
            active = int(active_row[0]) if active_row and active_row[0].isdigit() else course_id
            if active == course_id:
                active = int(fallback[0])
                db.execute(
                    "INSERT OR REPLACE INTO app_state(key, value) VALUES ('active_course_id', ?)",
                    (str(active),),
                )
        return active

    def get(self) -> ActiveSettings:
        course = self.selection_course(self.active_course_id())
        return ActiveSettings(
            course_id=course.id or None,
            course_name=course.name,
            language=course.language,
            prompt="",
            corrections="",
            format_transcript=course.format_transcript,
            llm_enabled=course.llm_enabled,
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

    @staticmethod
    def _suggestion(row: sqlite3.Row) -> StoredSuggestion:
        data = dict(row)
        data["id"] = int(data["id"])
        data["confidence"] = float(data["confidence"])
        return StoredSuggestion(**data)

    def create_recording(self, recording_id: str, settings: ActiveSettings, title: str,
                         source_kind: str, extension: str, source_path: str = "",
                         status: str = "queued") -> Recording:
        now = self.now()
        with self._lock, self._connect() as db:
            db.execute(
                """INSERT INTO recordings(
                    id, course_id, course_name, title, source_kind, extension, status,
                    language, prompt, corrections, format_transcript, llm_enabled,
                    llm_model, output_dir, source_path, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (recording_id, settings.course_id, settings.course_name, title, source_kind,
                 extension, status, settings.language, settings.prompt, settings.corrections,
                 1, 1, settings.llm_model,
                 settings.output_dir, source_path, now),
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
        with self._lock, self._connect() as db:
            row = db.execute("SELECT * FROM recordings WHERE id = ?", (recording_id,)).fetchone()
            if row is None:
                raise KeyError("작업을 찾지 못했습니다.")
            if row["status"] not in {"completed", "failed", "cancelled"}:
                raise ValueError("완료·실패·중단된 작업만 다시 전사할 수 있습니다.")
            course = db.execute("SELECT * FROM courses WHERE id = ?", (course_id if course_id is not None else row["course_id"],)).fetchone()
            if course_id not in (None, 0) and course is None:
                raise ValueError("강의를 찾지 못했습니다.")
            changes = dict(status="queued", source_path=str(source), error="", started_at="", completed_at="",
                           llm_model=DEFAULT_LLM_MODEL)
            # Deleted courses retain the job's snapshot; never borrow the selected course.
            if course is not None:
                changes.update(course_id=course["id"], course_name=course["name"])
                for name in ("language", "format_transcript", "llm_enabled"):
                    changes[name] = course[name]
            if course_id == 0:
                changes.update(course_id=None, course_name="선택 안 함", language="auto", format_transcript=1, llm_enabled=1)
            changes.update(format_transcript=1, llm_enabled=1)
            changes.update(output_changes or {})
            assignments = ", ".join(f"{key} = ?" for key in changes)
            db.execute(f"UPDATE recordings SET {assignments} WHERE id = ?",
                       (*changes.values(), recording_id))
        return self.get_recording(recording_id)

    def update_recording(self, recording_id: str, **changes: Any) -> Recording:
        allowed = {
            "title", "status", "source_path", "audio_path", "note_path", "duration_seconds",
            "processing_seconds", "transcript_text", "segments_json", "breaks_json",
            "quality_json", "error", "started_at", "completed_at",
            "note_revision_id", "note_snapshot_json", "review_status", "review_error", "llm_model", "llm_enabled", "format_transcript",
        }
        values = {key: value for key, value in changes.items() if key in allowed}
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

    def replace_suggestions(self, recording_id: str, suggestions: list[dict[str, Any]]) -> None:
        with self._lock, self._connect() as db:
            self._replace_suggestions(db, recording_id, suggestions)

    def _replace_suggestions(self, db: sqlite3.Connection, recording_id: str,
                             suggestions: list[dict[str, Any]]) -> None:
        now = self.now()
        db.execute("DELETE FROM suggestions WHERE recording_id = ?", (recording_id,))
        db.executemany(
            """INSERT INTO suggestions(
                recording_id, sentence_id, original, replacement, reason,
                confidence, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)""",
            [
                (recording_id, item["sentence_id"], item["original"], item["replacement"],
                 item.get("reason", ""), float(item["confidence"]), now)
                for item in suggestions
            ],
        )

    def commit_result(self, recording: Recording, suggestions: list[dict[str, Any]],
                      publish: Callable[[], None]) -> None:
        """Publish files while committing transcript and review data together."""
        values = asdict(recording)
        values.pop("id")
        with self._lock, self._connect() as db:
            assignments = ", ".join(f"{key} = ?" for key in values)
            db.execute(f"UPDATE recordings SET {assignments} WHERE id = ?",
                       (*values.values(), recording.id))
            self._replace_suggestions(db, recording.id, suggestions)
            publish()

    def suggestion_counts(self, recording_ids: list[str]) -> dict[str, dict[str, int]]:
        counts = {
            recording_id: dict.fromkeys(("pending", "accepted", "rejected"), 0)
            for recording_id in recording_ids
        }
        if not counts:
            return counts
        placeholders = ",".join("?" for _ in counts)
        with self._connect() as db:
            rows = db.execute(
                f"SELECT recording_id, status, COUNT(*) AS count FROM suggestions "
                f"WHERE recording_id IN ({placeholders}) GROUP BY recording_id, status",
                list(counts),
            ).fetchall()
        for row in rows:
            if row["status"] in counts[row["recording_id"]]:
                counts[row["recording_id"]][row["status"]] = int(row["count"])
        return counts

    def list_suggestions(self, recording_id: str) -> list[StoredSuggestion]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM suggestions WHERE recording_id = ? ORDER BY id",
                (recording_id,),
            ).fetchall()
        return [self._suggestion(row) for row in rows]

    def get_suggestion(self, suggestion_id: int) -> StoredSuggestion:
        with self._connect() as db:
            row = db.execute("SELECT * FROM suggestions WHERE id = ?", (suggestion_id,)).fetchone()
        if row is None:
            raise KeyError("수정 제안을 찾지 못했습니다.")
        return self._suggestion(row)

    def decide_suggestion(self, suggestion_id: int, status: str) -> StoredSuggestion:
        if status not in {"accepted", "rejected"}:
            raise ValueError("지원하지 않는 검토 결과입니다.")
        with self._lock, self._connect() as db:
            cursor = db.execute(
                "UPDATE suggestions SET status = ?, decided_at = ? WHERE id = ? AND status = 'pending'",
                (status, self.now(), suggestion_id),
            )
            if cursor.rowcount != 1:
                row = db.execute("SELECT 1 FROM suggestions WHERE id = ?", (suggestion_id,)).fetchone()
                if row is None:
                    raise KeyError("수정 제안을 찾지 못했습니다.")
                raise ValueError("이미 검토한 제안입니다.")
        return self.get_suggestion(suggestion_id)
