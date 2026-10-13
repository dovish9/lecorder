"""Persistent lecture-note revisions with independent extraction and study workers."""

import json
import uuid
from contextlib import contextmanager

ANALYZER_VERSION = "notes-v4-validated"


class NoteRepository:
    @contextmanager
    def access(self):
        """Serialize revision selection with note analysis publication."""
        with self.lock:
            yield

    def revision(self, revision_id):
        return self._revision(revision_id)


    def _revision(self, revision_id):
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT * FROM note_revisions WHERE id=?", (revision_id,)
            ).fetchone()
        if not row:
            raise KeyError("노트 분석 버전을 찾지 못했습니다.")
        return dict(row)


    def get(self, note_id):
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT * FROM lecture_notes WHERE id=?", (note_id,)
            ).fetchone()
        if not row:
            raise KeyError("강의노트를 찾지 못했습니다.")
        note = dict(row)
        note.pop("originals")
        if note["revision_id"]:
            revision = self._revision(note["revision_id"])
            note.update(
                {
                    k: revision[k]
                    for k in (
                        "status",
                        "study_status",
                        "error",
                        "page_count",
                        "seconds",
                    )
                }
            )
            note["keywords"] = json.loads(revision["keywords"])
            note["overview"] = json.loads(revision["overview"])
            with self.store.transaction() as db:
                flagged = db.execute(
                    "SELECT 1 FROM note_pages WHERE revision_id=? "
                    "AND json_extract(payload, '$.analysis.validation.status')='needs_review' "
                    "UNION ALL SELECT 1 FROM note_details WHERE revision_id=? "
                    "AND json_extract(payload, '$.detail.validation.status')='needs_review' LIMIT 1",
                    (revision['id'], revision['id']),
                ).fetchone()
            note['needs_review'] = bool(flagged or any(g.get('uncertainties') for g in note['overview']))
            note["needs_reanalysis"] = revision["analyzer"] != ANALYZER_VERSION
        return note


    def list(self, course_id):
        with self.store.transaction() as db:
            rows = db.execute(
                "SELECT id FROM lecture_notes WHERE course_id=? AND deleted=0 ORDER BY created_at DESC",
                (course_id,),
            ).fetchall()
        return [self.get(row["id"]) for row in rows]


    def queue_items(self):
        """Small status payload; never send document text through status polling."""
        with self.store.transaction() as db:
            rows = db.execute("""
                SELECT n.id AS note_id, n.title, c.name AS course_name,
                       r.id AS revision_id, r.status, r.study_status, r.page_count,
                       r.created_at,
                       (SELECT COUNT(*) FROM note_pages p WHERE p.revision_id=r.id)
                           AS extracted_pages,
                       (SELECT COUNT(*) FROM note_pages p WHERE p.revision_id=r.id
                        AND json_extract(p.payload, '$.analysis_status') IN ('completed','failed'))
                           AS analyzed_pages
                FROM lecture_notes n JOIN note_revisions r ON r.id=n.revision_id
                LEFT JOIN courses c ON c.id=n.course_id
                WHERE n.deleted=0 AND (r.status IN ('queued','extracting')
                    OR (r.status IN ('ready','partial') AND r.study_status IN ('pending','analyzing')))
                ORDER BY r.created_at
            """).fetchall()
            details = db.execute("""
                SELECT n.id AS note_id, n.title, c.name AS course_name,
                       d.revision_id, d.number, r.created_at,
                       json_extract(d.payload, '$.detail_status') AS status
                FROM note_details d JOIN lecture_notes n ON n.revision_id=d.revision_id
                JOIN note_revisions r ON r.id=d.revision_id
                LEFT JOIN courses c ON c.id=n.course_id
                WHERE n.deleted=0 AND json_extract(d.payload, '$.detail_status') IN ('pending','analyzing')
                ORDER BY r.created_at, d.number
            """).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            extracting = item['status'] in {'queued', 'extracting'}
            item['stage'] = 'extracting' if extracting else 'study'
            item['status'] = ('queued' if item['status'] == 'queued' else 'processing') if extracting else (
                'queued' if item['study_status'] == 'pending' else 'processing')
            items.append(item)
        for row in details:
            item = dict(row)
            item['stage'] = 'detail'
            item['status'] = 'queued' if item['status'] == 'pending' else 'processing'
            items.append(item)
        return items


    def completion_items(self):
        """Small, current note states used to detect newly completed analyses."""
        with self.store.transaction() as db:
            rows = db.execute("""
                SELECT n.id AS note_id, n.title, n.course_id, c.name AS course_name,
                       r.id AS revision_id, r.status, r.study_status, r.error
                FROM lecture_notes n
                JOIN note_revisions r ON r.id=n.revision_id
                LEFT JOIN courses c ON c.id=n.course_id
                WHERE n.deleted=0
                ORDER BY r.created_at DESC
            """).fetchall()
        return [dict(row) for row in rows]


    def pages(self, revision_id):
        with self.store.transaction() as db:
            rows = db.execute(
                "SELECT number,payload FROM note_pages WHERE revision_id=? ORDER BY number",
                (revision_id,),
            ).fetchall()
            details = {
                row["number"]: json.loads(row["payload"])
                for row in db.execute(
                    "SELECT number,payload FROM note_details WHERE revision_id=?",
                    (revision_id,),
                )
            }
        return [
            dict(json.loads(row["payload"]), **details.get(row["number"], {}))
            for row in rows
        ]


    def _detail(self, revision_id, number, payload):
        # On-demand explanations are separate from the published analysis revision.
        with self.store.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO note_details VALUES (?,?,?)",
                (revision_id, number, json.dumps(payload, ensure_ascii=False)),
            )

    def _page(self, revision_id, page):
        with self.store.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO note_pages VALUES (?,?,?)",
                (revision_id, page["number"], json.dumps(page, ensure_ascii=False)),
            )


    def _update(self, revision_id, **changes):
        allowed = {
            "status",
            "study_status",
            "keywords",
            "page_count",
            "error",
            "seconds",
            "overview",
        }
        if not set(changes) <= allowed:
            raise ValueError("Invalid revision change")
        with self.store.transaction() as db:
            db.execute(
                "UPDATE note_revisions SET "
                + ",".join(f"{k}=?" for k in changes)
                + " WHERE id=?",
                (*changes.values(), revision_id),
            )


    def hide(self, note_id):
        self.cancel(note_id)
        with self.store.transaction() as db:
            db.execute("UPDATE lecture_notes SET deleted=1 WHERE id=?", (note_id,))


    def select_keywords(self, note_id, excluded):
        with self.lock:
            note = self.get(note_id)
            if note["status"] not in {"ready", "partial"} or note["study_status"] in {
                "pending",
                "analyzing",
            }:
                raise ValueError("기본 분석이 끝난 후 키워드를 선택하세요.")
            if not isinstance(excluded, list) or any(
                not isinstance(v, str) for v in excluded
            ):
                raise ValueError("제외할 키워드 ID 목록이 필요합니다.")
            if not set(excluded) <= {k["id"] for k in note["keywords"]}:
                raise ValueError("알 수 없는 키워드입니다.")
            previous = note["revision_id"]
            revision_id = uuid.uuid4().hex
            keywords = [
                dict(k, included=k["id"] not in excluded) for k in note["keywords"]
            ]
            revision = self._revision(previous)
            with self.store.transaction() as db:
                db.execute(
                    "INSERT INTO note_revisions(id,note_id,analyzer,status,study_status,keywords,page_count,error,created_at,seconds,overview,ai_settings) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        revision_id,
                        note_id,
                        revision["analyzer"],
                        revision["status"],
                        revision["study_status"],
                        json.dumps(keywords, ensure_ascii=False),
                        revision["page_count"],
                        revision["error"],
                        self.store.now(),
                        revision["seconds"],
                        revision["overview"],
                        revision["ai_settings"],
                    ),
                )
                db.execute(
                    "INSERT INTO note_pages SELECT ?,number,payload FROM note_pages WHERE revision_id=?",
                    (revision_id, previous),
                )
                db.execute(
                    "INSERT INTO note_details SELECT ?,number,payload FROM note_details WHERE revision_id=? AND json_extract(payload,'$.detail_status')='completed'",
                    (revision_id, previous),
                )
                db.execute(
                    "UPDATE lecture_notes SET revision_id=? WHERE id=?",
                    (revision_id, note_id),
                )
            return self.get(note_id)


    def bind(self, note_id, course_id, *, allow_pending=False):
        if not note_id:
            return None
        note = self.get(note_id)
        if note["deleted"] or note["course_id"] != course_id:
            raise ValueError("선택한 강의에 속한 강의노트가 아닙니다.")
        if note["status"] not in ({"ready", "partial", "queued", "extracting"} if allow_pending else {"ready", "partial"}):
            raise ValueError("노트의 키워드 준비가 끝나지 않았습니다.")
        # Snapshot contains only published extraction, never mutable study output.
        return note["revision_id"]


    def snapshot(self, revision_id):
        if not revision_id:
            return {"keywords": [], "pages": []}
        revision = self._revision(revision_id)
        return {
            "keywords": json.loads(revision["keywords"]),
            "pages": [
                {"number": p["number"], "text": p["text"]}
                for p in self.pages(revision_id)
            ],
        }


    def _publish_ranking(self, previous, keywords):
        revision = self._revision(previous)
        next_id = uuid.uuid4().hex
        with self.lock, self.store.transaction() as db:
            current = db.execute(
                "SELECT revision_id FROM lecture_notes WHERE id=?",
                (revision["note_id"],),
            ).fetchone()
            if not current or current[0] != previous:
                return
            db.execute(
                "INSERT INTO note_revisions(id,note_id,analyzer,status,study_status,keywords,page_count,error,created_at,seconds,overview,ai_settings) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    next_id,
                    revision["note_id"],
                    revision["analyzer"],
                    revision["status"],
                    revision["study_status"],
                    json.dumps(keywords, ensure_ascii=False),
                    revision["page_count"],
                    revision["error"],
                    self.store.now(),
                    revision["seconds"],
                    revision["overview"],
                    revision["ai_settings"],
                ),
            )
            db.execute(
                "INSERT INTO note_pages SELECT ?,number,payload FROM note_pages WHERE revision_id=?",
                (next_id, previous),
            )
            db.execute(
                "INSERT INTO note_details SELECT ?,number,payload FROM note_details WHERE revision_id=? AND json_extract(payload,'$.detail_status')='completed'",
                (next_id, previous),
            )
            db.execute(
                "UPDATE lecture_notes SET revision_id=? WHERE id=?",
                (next_id, revision["note_id"]),
            )
