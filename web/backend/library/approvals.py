"""Suggestion decisions and immutable approval evidence share one transaction."""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from typing import Any, Callable
from web.backend.library.records import Recording, StoredSuggestion
from web.backend.result_codec import encode_column, dumps as result_dumps


class ApprovalRepository:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def _suggestion(row: sqlite3.Row) -> StoredSuggestion:
        data = dict(row)
        data["id"] = int(data["id"])
        data["confidence"] = float(data["confidence"])
        return StoredSuggestion(**data)


    def replace_suggestions(self, recording_id: str, suggestions: list[dict[str, Any]]) -> None:
        with self.store.transaction() as db:
            self._replace_suggestions(db, recording_id, suggestions)


    def _replace_suggestions(self, db: sqlite3.Connection, recording_id: str,
                             suggestions: list[dict[str, Any]]) -> None:
        now = self.store.now()
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


    def suggestion_counts(self, recording_ids: list[str]) -> dict[str, dict[str, int]]:
        counts = {
            recording_id: dict.fromkeys(("pending", "accepted", "rejected"), 0)
            for recording_id in recording_ids
        }
        if not counts:
            return counts
        placeholders = ",".join("?" for _ in counts)
        with self.store.transaction() as db:
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
        with self.store.transaction() as db:
            rows = db.execute(
                "SELECT * FROM suggestions WHERE recording_id = ? ORDER BY id",
                (recording_id,),
            ).fetchall()
        return [self._suggestion(row) for row in rows]


    def get_suggestion(self, suggestion_id: int) -> StoredSuggestion:
        with self.store.transaction() as db:
            row = db.execute("SELECT * FROM suggestions WHERE id = ?", (suggestion_id,)).fetchone()
        if row is None:
            raise KeyError("수정 제안을 찾지 못했습니다.")
        return self._suggestion(row)


    def decide_suggestion(self, suggestion_id: int, status: str) -> StoredSuggestion:
        if status not in {"accepted", "rejected"}:
            raise ValueError("지원하지 않는 검토 결과입니다.")
        with self.store.transaction() as db:
            cursor = db.execute(
                "UPDATE suggestions SET status = ?, decided_at = ? WHERE id = ? AND status = 'pending'",
                (status, self.store.now(), suggestion_id),
            )
            if cursor.rowcount != 1:
                row = db.execute("SELECT 1 FROM suggestions WHERE id = ?", (suggestion_id,)).fetchone()
                if row is None:
                    raise KeyError("수정 제안을 찾지 못했습니다.")
                raise ValueError("이미 검토한 제안입니다.")
        return self.get_suggestion(suggestion_id)


    def accept_suggestion(self, suggestion_id: int, recording: Recording,
                          context: dict[str, Any], publish: Callable[[], None]) -> Recording:
        """Commit the edited transcript, decision and immutable evidence together."""
        import uuid
        from web.backend.publication import Publication
        journal = Publication(self.store, recording, [Path(recording.note_path)])
        try:
            with self.store.transaction() as db:
                suggestion = db.execute("SELECT * FROM suggestions WHERE id=? AND status='pending'", (suggestion_id,)).fetchone()
                if suggestion is None:
                    raise ValueError("이미 검토한 제안입니다.")
                current = db.execute("SELECT segments_json FROM recordings WHERE id=?", (recording.id,)).fetchone()
                if (suggestion['recording_id'] != recording.id or current is None or
                        json.loads(current['segments_json'])[context['index']]['text'] != context['text']):
                    raise ValueError("변환 텍스트가 변경됐습니다. 수정 제안을 다시 확인하세요.")
                now = self.store.now()
                runs = db.execute("""SELECT id, settings, started_at, 'review' AS kind FROM review_runs WHERE recording_id=? AND status='completed' AND started_at<=?
                    UNION ALL SELECT id, settings, started_at, 'transcription' AS kind FROM transcription_runs WHERE recording_id=? AND status='completed' AND started_at<=?
                    ORDER BY started_at DESC LIMIT 1""", (recording.id, suggestion['created_at'], recording.id, suggestion['created_at'])).fetchone()
                settings = json.loads(runs['settings']) if runs else {}
                llm = (settings.get('ai_settings') or {}).get('llm') or {}
                snapshot = {
                    'version': 1, 'recording_title': recording.title, 'course_name': recording.course_name,
                    'sentence_id': suggestion['sentence_id'], 'original': suggestion['original'],
                    'replacement': suggestion['replacement'], 'reason': suggestion['reason'],
                    'confidence': suggestion['confidence'], 'proposed_at': suggestion['created_at'],
                    'context_before': context['text'], 'context_after': json.loads(recording.segments_json)[context['index']]['text'],
                    'start': context.get('start'), 'end': context.get('end'),
                    'time_scope': 'utterance', 'run_id': runs['id'] if runs else None,
                    'model': llm.get('model') or settings.get('llm_model') or (settings.get('model') if runs and runs['kind']=='review' else None),
                    'audio_name': Path(recording.audio_path).name if recording.audio_path else None,
                    'language': recording.language,
                    'provider': llm.get('provider'),
                }
                db.execute("UPDATE recordings SET segments_json=?,transcript_text=? WHERE id=?",
                           (encode_column("segments_json",recording.segments_json), recording.transcript_text, recording.id))
                db.execute("UPDATE suggestions SET status='accepted',decided_at=? WHERE id=?", (now, suggestion_id))
                db.execute("INSERT INTO suggestion_history VALUES (?,?,?,?)",
                           (uuid.uuid4().hex, recording.id, now, result_dumps(snapshot,"approval","review")))
                publish()
        except Exception:
            journal.rollback()
            raise
        journal.complete()
        return recording


    def approval_history(self, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        with self.store.transaction() as db:
            total = db.execute("SELECT COUNT(*) FROM suggestion_history").fetchone()[0]
            rows = db.execute("SELECT * FROM suggestion_history ORDER BY approved_at DESC,id DESC LIMIT ? OFFSET ?",
                              (limit, offset)).fetchall()
        return {'total': total, 'offset': offset, 'items': [
            dict(id=row['id'], recording_id=row['recording_id'], approved_at=row['approved_at'],
                 **json.loads(row['payload'])) for row in rows]}


