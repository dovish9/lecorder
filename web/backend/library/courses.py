"""Course persistence and selection; no recording or AI execution ownership."""
from __future__ import annotations
import sqlite3
from typing import Any
from web.backend.config import LANGUAGES, text
from web.backend.library.records import Course


class CourseRepository:
    COURSE_FIELDS = {"name", "language"}

    def __init__(self, store):
        self.store = store

    @staticmethod
    def _course(row: sqlite3.Row) -> Course:
        return Course(
            id=int(row["id"]),
            name=str(row["name"]),
            language=str(row["language"]),
            updated_at=str(row["updated_at"]),
        )


    def list_courses(self) -> list[Course]:
        with self.store.transaction() as db:
            rows = db.execute(
                "SELECT * FROM courses ORDER BY name COLLATE NOCASE, id"
            ).fetchall()
        return [self._course(row) for row in rows]


    def get_course(self, course_id: int) -> Course:
        with self.store.transaction() as db:
            row = db.execute("SELECT * FROM courses WHERE id = ?", (course_id,)).fetchone()
        if row is None:
            raise KeyError("강의를 찾지 못했습니다.")
        return self._course(row)


    def active_course_id(self) -> int:
        with self.store.transaction() as db:
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
                return 0
            course_id = int(fallback[0])
            db.execute(
                "INSERT OR REPLACE INTO app_state(key, value) VALUES ('active_course_id', ?)",
                (str(course_id),),
            )
            return course_id


    def selection_course(self, course_id: int) -> Course:
        return Course(0, "선택 안 함", language="auto") if course_id == 0 else self.get_course(course_id)


    def select_course(self, course_id: int) -> Course:
        course = self.selection_course(course_id)
        with self.store.transaction() as db:
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
            with self.store.transaction() as db:
                cursor = db.execute(
                    "INSERT INTO courses(name, updated_at) VALUES (?, ?)",
                    (clean, self.store.now()),
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
            values[key] = value
        if not values:
            return current
        values["updated_at"] = self.store.now()
        assignments = ", ".join(f"{key} = ?" for key in values)
        try:
            with self.store.transaction() as db:
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
        with self.store.transaction() as db:
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


