"""Persistent lecture-note revisions with independent extraction and study workers."""
from .ollama_status import require_ollama

import hashlib
import json
import queue
import shutil
import threading
import time
import uuid
from pathlib import Path
from .note_extract import SUPPORTED_NOTES, IMAGE_EXTENSIONS, prepare_pages, extract_page
from .note_analysis import candidates
from .note_study import analyze_visual_page as analyze_page, build_overview
from .transcription import CancellationToken, TranscriptionCancelled

ANALYZER_VERSION = "notes-v3-vision-kiwi"


class NoteLibrary:
    def __init__(self, store, folder, start_worker=True):
        self.store, self.folder = store, Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.tokens = {}
        self.extract_queue, self.study_queue = queue.Queue(), queue.Queue()
        with store._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS lecture_notes (
                    id TEXT PRIMARY KEY, course_id INTEGER REFERENCES courses(id) ON DELETE SET NULL,
                    title TEXT NOT NULL, fingerprint TEXT NOT NULL, originals TEXT NOT NULL,
                    revision_id TEXT, deleted INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS note_revisions (
                    id TEXT PRIMARY KEY, note_id TEXT NOT NULL REFERENCES lecture_notes(id),
                    analyzer TEXT NOT NULL, status TEXT NOT NULL, study_status TEXT NOT NULL DEFAULT 'pending',
                    keywords TEXT NOT NULL DEFAULT '[]', page_count INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, seconds REAL NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS note_pages (
                    revision_id TEXT NOT NULL REFERENCES note_revisions(id), number INTEGER NOT NULL,
                    payload TEXT NOT NULL, PRIMARY KEY(revision_id, number));
                CREATE TABLE IF NOT EXISTS note_details (
                    revision_id TEXT NOT NULL REFERENCES note_revisions(id), number INTEGER NOT NULL,
                    payload TEXT NOT NULL, PRIMARY KEY(revision_id, number));
            """
            )
        with store._connect() as db:
            columns = {
                row[1] for row in db.execute("PRAGMA table_info(note_revisions)")
            }
            if "overview" not in columns:
                db.execute(
                    "ALTER TABLE note_revisions ADD COLUMN overview TEXT NOT NULL DEFAULT '[]'"
                )
        if start_worker:
            with store._connect() as db:
                db.execute(
                    "UPDATE note_revisions SET status='queued' WHERE status='extracting'"
                )
                db.execute(
                    "UPDATE note_revisions SET study_status='pending' WHERE study_status='analyzing'"
                )
                rows = db.execute(
                    "SELECT id,status,study_status FROM note_revisions"
                ).fetchall()
                details = db.execute(
                    "SELECT revision_id,number,payload FROM note_details"
                ).fetchall()
            for row in rows:
                if row["status"] == "queued":
                    self.extract_queue.put(row["id"])
                elif (
                    row["status"] in {"ready", "partial"}
                    and row["study_status"] == "pending"
                ):
                    self.study_queue.put((row["id"], None))
            for detail in details:
                if json.loads(detail["payload"]).get("detail_status") in {
                    "pending",
                    "analyzing",
                }:
                    self.study_queue.put((detail["revision_id"], detail["number"]))
            for name, target in [
                ("note-extraction", self._extract_worker),
                ("note-study", self._study_worker),
            ]:
                threading.Thread(target=target, name=name, daemon=True).start()

    def _revision(self, revision_id):
        with self.store._connect() as db:
            row = db.execute(
                "SELECT * FROM note_revisions WHERE id=?", (revision_id,)
            ).fetchone()
        if not row:
            raise KeyError("노트 분석 버전을 찾지 못했습니다.")
        return dict(row)

    def get(self, note_id):
        with self.store._connect() as db:
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
            note["needs_reanalysis"] = revision["analyzer"] != ANALYZER_VERSION
        return note

    def list(self, course_id):
        with self.store._connect() as db:
            rows = db.execute(
                "SELECT id FROM lecture_notes WHERE course_id=? AND deleted=0 ORDER BY created_at DESC",
                (course_id,),
            ).fetchall()
        return [self.get(row["id"]) for row in rows]

    def queue_items(self):
        """Small status payload; never send document text through status polling."""
        with self.store._connect() as db:
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
        with self.store._connect() as db:
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
        with self.store._connect() as db:
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
        with self.store._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO note_details VALUES (?,?,?)",
                (revision_id, number, json.dumps(payload, ensure_ascii=False)),
            )

    def _page(self, revision_id, page):
        with self.store._connect() as db:
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
        with self.store._connect() as db:
            db.execute(
                "UPDATE note_revisions SET "
                + ",".join(f"{k}=?" for k in changes)
                + " WHERE id=?",
                (*changes.values(), revision_id),
            )

    def register(self, course_id, files, title=""):
        self.store.get_course(course_id)
        if not files or len(files) > 500:
            raise ValueError("강의노트 파일을 선택하세요. 최대 500개입니다.")
        suffixes = [Path(name).suffix.lower() for name, _ in files]
        if any(s not in SUPPORTED_NOTES for s in suffixes) or (
            len(files) > 1 and any(s not in IMAGE_EXTENSIONS for s in suffixes)
        ):
            raise ValueError(
                "PDF/PPTX 한 개 또는 PNG·JPEG·HEIC 이미지 묶음을 선택하세요."
            )
        note_id = uuid.uuid4().hex
        folder = self.folder / note_id
        folder.mkdir()
        digest, size, originals = hashlib.sha256(), 0, []
        try:
            for i, ((name, stream), suffix) in enumerate(zip(files, suffixes)):
                target = folder / f"original-{i}{suffix}"
                digest.update(f"{i}:{suffix}:".encode())
                with target.open("wb") as output:
                    while data := stream.read(1024 * 1024):
                        size += len(data)
                        if size > 100 * 1024 * 1024:
                            raise ValueError(
                                "강의노트 원본 합계는 100MB 이하여야 합니다."
                            )
                        digest.update(data)
                        output.write(data)
                if target.stat().st_size == 0:
                    raise ValueError("빈 파일은 등록할 수 없습니다.")
                with target.open("rb") as source:
                    header = source.read(32)
                valid = (
                    suffix == ".pdf"
                    and header.startswith(b"%PDF-")
                    or suffix == ".pptx"
                    and header.startswith(b"PK")
                    or suffix == ".png"
                    and header.startswith(b"\x89PNG\r\n\x1a\n")
                    or suffix in {".jpg", ".jpeg"}
                    and header.startswith(b"\xff\xd8")
                    or suffix == ".heic"
                    and b"ftyp" in header
                )
                if not valid:
                    raise ValueError("파일 내용과 확장자가 일치하지 않습니다.")
                originals.append(target.name)
            with self.lock, self.store._connect() as db:
                existing = db.execute(
                    "SELECT id FROM lecture_notes WHERE course_id=? AND fingerprint=? AND deleted=0",
                    (course_id, digest.hexdigest()),
                ).fetchone()
                if existing:
                    shutil.rmtree(folder)
                    previous = self.get(existing["id"])
                    if (
                        previous["needs_reanalysis"]
                        and previous["status"] not in {"queued", "extracting"}
                        and previous["study_status"] not in {"pending", "analyzing"}
                    ):
                        return self.reanalyze(previous["id"])
                    return previous
                db.execute(
                    "INSERT INTO lecture_notes(id,course_id,title,fingerprint,originals,created_at) VALUES (?,?,?,?,?,?)",
                    (
                        note_id,
                        course_id,
                        (title.strip() or Path(files[0][0]).stem)[:160],
                        digest.hexdigest(),
                        json.dumps(originals),
                        self.store.now(),
                    ),
                )
            return self.reanalyze(note_id)
        except Exception:
            with self.store._connect() as db:
                persisted = db.execute(
                    "SELECT 1 FROM lecture_notes WHERE id=?", (note_id,)
                ).fetchone()
            if not persisted:
                shutil.rmtree(folder, ignore_errors=True)
            raise

    def reanalyze(self, note_id):
        with self.lock:
            note = self.get(note_id)
            if note.get("status") in {"queued", "extracting"} or note.get(
                "study_status"
            ) in {"pending", "analyzing"}:
                raise ValueError("현재 분석이 끝난 후 다시 분석하세요.")
            revision_id = uuid.uuid4().hex
            with self.store._connect() as db:
                db.execute(
                    "INSERT INTO note_revisions(id,note_id,analyzer,status,created_at) VALUES (?,?,?,?,?)",
                    (
                        revision_id,
                        note_id,
                        ANALYZER_VERSION,
                        "queued",
                        self.store.now(),
                    ),
                )
                db.execute(
                    "UPDATE lecture_notes SET revision_id=? WHERE id=?",
                    (revision_id, note_id),
                )
            self.tokens[revision_id] = CancellationToken()
            try:
                require_ollama()
            except RuntimeError as error:
                self._update(revision_id, status="failed", study_status="failed", error=str(error))
                return self.get(note_id)
            self.extract_queue.put(revision_id)
            return self.get(note_id)

    def cancel(self, note_id):
        with self.lock:
            note = self.get(note_id)
            revision = note["revision_id"]
            self.tokens.setdefault(revision, CancellationToken()).cancel()
            if note["status"] in {"queued", "extracting"}:
                self._update(revision, status="cancelled", study_status="cancelled")
            elif note["study_status"] in {"pending", "analyzing"}:
                self._update(revision, study_status="cancelled")
            with self.store._connect() as db:
                rows = db.execute(
                    "SELECT number,payload FROM note_details WHERE revision_id=?",
                    (revision,),
                ).fetchall()
                for row in rows:
                    payload = json.loads(row["payload"])
                    if payload.get("detail_status") in {"pending", "analyzing"}:
                        payload["detail_status"] = "cancelled"
                        db.execute(
                            "UPDATE note_details SET payload=? WHERE revision_id=? AND number=?",
                            (
                                json.dumps(payload, ensure_ascii=False),
                                revision,
                                row["number"],
                            ),
                        )
        return self.get(note_id)

    def hide(self, note_id):
        self.cancel(note_id)
        with self.store._connect() as db:
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
            with self.store._connect() as db:
                db.execute(
                    "INSERT INTO note_revisions(id,note_id,analyzer,status,study_status,keywords,page_count,error,created_at,seconds,overview) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
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

    def request_detail(self, note_id, number):
        with self.lock:
            note = self.get(note_id)
            if note["deleted"]:
                raise ValueError("제거한 강의노트입니다.")
            page = next(
                (p for p in self.pages(note["revision_id"]) if p["number"] == number),
                None,
            )
            if not page or not page["text"]:
                raise ValueError("해설할 원문이 없습니다.")
            if page.get("detail_status") in {"pending", "analyzing", "completed"}:
                return
            token = self.tokens.get(note["revision_id"])
            if token and token.cancelled:
                self.tokens[note["revision_id"]] = CancellationToken()
            try:
                require_ollama()
            except RuntimeError as error:
                self._detail(note["revision_id"], number, {"detail_status": "failed", "analysis_error": str(error)})
                return
            self._detail(note["revision_id"], number, {"detail_status": "pending"})
            self.study_queue.put((note["revision_id"], number))

    def extract(self, revision_id):
        revision = self._revision(revision_id)
        token = self.tokens.setdefault(revision_id, CancellationToken())
        started = time.monotonic()
        try:
            token.check()
            require_ollama()
            self._update(revision_id, status="extracting", error="")
            with self.store._connect() as db:
                row = db.execute(
                    "SELECT originals FROM lecture_notes WHERE id=?",
                    (revision["note_id"],),
                ).fetchone()
            folder = self.folder / revision["note_id"] / revision_id
            folder.mkdir(exist_ok=True)
            originals = [
                self.folder / revision["note_id"] / name for name in json.loads(row[0])
            ]
            sources = prepare_pages(originals, folder, token)
            self._update(revision_id, page_count=len(sources))
            existing = {
                p["number"]: p for p in self.pages(revision_id) if not p.get("error")
            }
            for number, (source, index) in enumerate(sources, 1):
                token.check()
                if number in existing:
                    continue
                try:
                    page = extract_page(source, index, folder, number, token)
                    page["image"] = f'{revision_id}/{page["image"]}'
                except TranscriptionCancelled:
                    raise
                except Exception as error:
                    page = {
                        "number": number,
                        "text": "",
                        "embedded": "",
                        "ocr": [],
                        "image": "",
                        "needs_review": True,
                        "error": str(error),
                        "analysis": None,
                        "detail": None,
                    }
                self._page(revision_id, page)
            pages = self.pages(revision_id)
            if not any(p["text"].strip() for p in pages):
                raise ValueError(
                    "읽을 수 있는 텍스트가 없습니다. 추출 오류를 확인하세요."
                )
            failed = sum(bool(p["error"]) for p in pages)
            self._update(
                revision_id,
                status="partial" if failed else "ready",
                keywords=json.dumps(candidates(pages), ensure_ascii=False),
                error=f"{failed}페이지 추출 실패" if failed else "",
                seconds=time.monotonic() - started,
            )
            try:
                require_ollama()
            except RuntimeError as error:
                self._update(revision_id, study_status="failed", error=str(error))
                return
            self.study_queue.put((revision_id, None))
        except TranscriptionCancelled:
            self._update(revision_id, status="cancelled", study_status="cancelled")
        except Exception as error:
            self._update(
                revision_id,
                status="failed",
                study_status="failed",
                error=str(error),
                seconds=time.monotonic() - started,
            )

    def study(self, revision_id, number=None):
        revision = self._revision(revision_id)
        token = self.tokens.setdefault(revision_id, CancellationToken())
        try:
            token.check()
            require_ollama()
            if number is None:
                self._update(revision_id, study_status="analyzing")
            else:
                self._detail(revision_id, number, {"detail_status": "analyzing"})
            failures = 0
            for page in self.pages(revision_id):
                if (
                    not page.get("image")
                    or (number is not None and page["number"] != number)
                    or (number is None and page.get("analysis"))
                ):
                    continue
                token.check()
                require_ollama()
                try:
                    result = analyze_page(
                        page,
                        json.loads(revision["keywords"]),
                        token,
                        detailed=number is not None,
                        image_path=self.folder / revision["note_id"] / page["image"],
                    )
                    page.pop("analysis_error", None)
                    page["detail" if number is not None else "analysis"] = result
                    page[
                        "detail_status" if number is not None else "analysis_status"
                    ] = "completed"
                except TranscriptionCancelled:
                    raise
                except Exception as error:
                    failures += 1
                    page[
                        "detail_status" if number is not None else "analysis_status"
                    ] = "failed"
                    page["analysis_error"] = str(error)
                if number is None:
                    self._page(revision_id, page)
                else:
                    self._detail(
                        revision_id,
                        number,
                        {
                            k: page[k]
                            for k in ("detail", "detail_status", "analysis_error")
                            if k in page
                        },
                    )
            if number is None:
                pages = self.pages(revision_id)
                try:
                    overview = build_overview(pages, token)
                except TranscriptionCancelled:
                    raise
                except Exception as error:
                    failures += 1
                    overview = [{"pages": [], "error": str(error)}]
                with self.lock:
                    token.check()
                    self._update(
                        revision_id,
                        study_status="failed" if failures else "completed",
                        overview=json.dumps(overview, ensure_ascii=False),
                    )
                    # Publish ranking as a new revision, leaving previously bound keyword versions intact.
                    keywords = json.loads(revision["keywords"])
                    selected = [
                        key
                        for p in pages
                        if p.get("analysis")
                        for key in p["analysis"]["keywords"]
                    ]
                    for keyword in keywords:
                        keyword["score"] += selected.count(keyword["id"]) * 5
                    keywords.sort(key=lambda k: (-k["score"], k["term"]))
                    self._publish_ranking(revision_id, keywords)

        except TranscriptionCancelled:
            if number is None:
                self._update(revision_id, study_status="cancelled")
            else:
                self._detail(revision_id, number, {"detail_status": "cancelled"})
        except Exception as error:
            if number is None:
                self._update(revision_id, study_status="failed", error=str(error))
            else:
                self._detail(
                    revision_id,
                    number,
                    {"detail_status": "failed", "analysis_error": str(error)},
                )

    def _publish_ranking(self, previous, keywords):
        revision = self._revision(previous)
        next_id = uuid.uuid4().hex
        with self.lock, self.store._connect() as db:
            current = db.execute(
                "SELECT revision_id FROM lecture_notes WHERE id=?",
                (revision["note_id"],),
            ).fetchone()
            if not current or current[0] != previous:
                return
            db.execute(
                "INSERT INTO note_revisions(id,note_id,analyzer,status,study_status,keywords,page_count,error,created_at,seconds,overview) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
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

    def _extract_worker(self):
        while True:
            revision = self.extract_queue.get()
            try:
                self.extract(revision)
            except Exception as error:
                print(f"Note worker error: {error}")
            finally:
                self.extract_queue.task_done()

    def _study_worker(self):
        while True:
            revision, number = self.study_queue.get()
            try:
                self.study(revision, number)
            except Exception as error:
                print(f"Note study worker error: {error}")
            finally:
                self.study_queue.task_done()
