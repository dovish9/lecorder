"""Persistent lecture-note revisions with independent extraction and study workers."""
from .ai_providers import require_llm
from .ai_settings import using, defaults

import json
import queue
from .services import start_thread
import threading
import time
from pathlib import Path
from .note_extract import prepare_pages, extract_page
from .note_analysis import candidates
from .glossary import merge_symbols, rank_candidates, build_glossary, extract_symbols, VERSION
from .note_study import analyze_visual_page as analyze_page, build_overview
from .transcription import CancellationToken, TranscriptionCancelled

from .note_repository import ANALYZER_VERSION


from .note_repository import NoteRepository
from .note_intake import NoteIntake

class NoteLibrary(NoteRepository, NoteIntake):
    def __init__(self, store, folder, start_worker=True):
        self.store, self.folder = store, Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.tokens = {}
        self.extract_queue, self.study_queue = queue.Queue(), queue.Queue()
        with store.transaction() as db:
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
        with store.transaction() as db:
            columns = {
                row[1] for row in db.execute("PRAGMA table_info(note_revisions)")
            }
            if 'glossary_data' not in columns:
                db.execute("ALTER TABLE note_revisions ADD COLUMN glossary_data TEXT NOT NULL DEFAULT '{}'")
            if 'ai_settings' not in columns:
                db.execute("ALTER TABLE note_revisions ADD COLUMN ai_settings TEXT NOT NULL DEFAULT '{}'")
            if "overview" not in columns:
                db.execute(
                    "ALTER TABLE note_revisions ADD COLUMN overview TEXT NOT NULL DEFAULT '[]'"
                )
        self._threads = []
        self._stopping = threading.Event()
        if start_worker:
            self.start()

    def start(self):
        if self._threads or self._stopping.is_set():
            return
        with self.store.transaction() as db:
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
            self._threads.append(start_thread(target, name))

    def prepare_shutdown(self):
        self._stopping.set()

    def close(self):
        self._stopping.set()
        with self.lock:
            for token in list(self.tokens.values()):
                token.cancel()
        for thread in self._threads:
            if thread is not threading.current_thread():
                thread.join(timeout=10)


    def extract(self, revision_id):
        snapshot = json.loads(self._revision(revision_id)['ai_settings']) or defaults()
        with using(snapshot):
            return self._extract(revision_id)

    def _extract(self, revision_id):
        revision = self._revision(revision_id)
        with self.lock:
            if self._stopping.is_set():
                return
            token = self.tokens.setdefault(revision_id, CancellationToken())
        started = time.monotonic()
        try:
            token.check()
            require_llm()
            self._update(revision_id, status="extracting", error="")
            with self.store.transaction() as db:
                row = db.execute(
                    "SELECT originals FROM lecture_notes WHERE id=?",
                    (revision["note_id"],),
                ).fetchone()
            folder = self.folder / revision["note_id"] / "pages-v1"
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
                    page["image"] = f'{folder.name}/{page["image"]}'
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
            raw_candidates = candidates(pages, token)
            self._update(
                revision_id,
                glossary_data=json.dumps({"version":VERSION,"candidates":raw_candidates}),
                status="partial" if failed else "ready",
                keywords=json.dumps(raw_candidates, ensure_ascii=False),
                error=f"{failed}페이지 추출 실패" if failed else "",
                seconds=time.monotonic() - started,
            )
            try:
                require_llm((json.loads(self._revision(revision_id)["ai_settings"]) or defaults())["llm"])
            except RuntimeError as error:
                self._update(revision_id, study_status="failed", error=str(error))
                return
            self.study_queue.put((revision_id, None))
        except TranscriptionCancelled:
            self._update(revision_id, status='queued' if self._stopping.is_set() else 'cancelled',
                         study_status='pending' if self._stopping.is_set() else 'cancelled')
        except Exception as error:
            self._update(
                revision_id,
                status="failed",
                study_status="failed",
                error=str(error),
                seconds=time.monotonic() - started,
            )

    def study(self, revision_id, number=None):
        snapshot = json.loads(self._revision(revision_id)['ai_settings']) or defaults()
        if number is not None:
            with self.store.transaction() as db:
                row = db.execute('SELECT payload FROM note_details WHERE revision_id=? AND number=?', (revision_id, number)).fetchone()
            if row:
                snapshot = json.loads(row['payload']).get('ai_settings') or snapshot
        from .request_history import execution
        with using(snapshot), execution(revision_id, f'note:{number or "overview"}') as finish:
            result = self._study(revision_id, number)
            if not self._stopping.is_set():
                if number is None:
                    finish(self._revision(revision_id)['study_status'])
                else:
                    with self.store.transaction() as db:
                        detail = db.execute('SELECT payload FROM note_details WHERE revision_id=? AND number=?', (revision_id, number)).fetchone()
                    finish(json.loads(detail['payload']).get('detail_status', 'failed') if detail else 'failed')
            return result

    def _study(self, revision_id, number=None):
        revision = self._revision(revision_id)
        with self.lock:
            if self._stopping.is_set():
                return
            token = self.tokens.setdefault(revision_id, CancellationToken())
        try:
            token.check()
            require_llm()
            if number is None:
                self._update(revision_id, study_status="analyzing")
            else:
                self._detail(revision_id, number, {"detail_status": "analyzing"})
            failures = 0
            data = json.loads(revision["glossary_data"])
            glossary_only = number is None and data.get("mode") == "glossary"
            raw_candidates = data.get("candidates")
            if raw_candidates is None:
                raw_candidates = json.loads(revision["keywords"]) if number is not None else candidates(self.pages(revision_id), token)
                data["candidates"] = raw_candidates
                self._update(revision_id, glossary_data=json.dumps(data, ensure_ascii=False))
            for page in self.pages(revision_id):
                if (
                    glossary_only
                    or not page.get("image")
                    or (number is not None and page["number"] != number)
                    or (number is None and page.get("analysis"))
                ):
                    continue
                token.check()
                require_llm()
                try:
                    result = analyze_page(
                        page,
                        raw_candidates,
                        token,
                        detailed=number is not None,
                        image_path=self.folder / revision["note_id"] / page["image"],
                    )
                    page.pop("analysis_error", None)
                    if result.get("validation", {}).get("status") == "needs_review":
                        page["needs_review"] = True
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
                if not glossary_only:
                    data['stage'] = 'overview'
                    self._update(revision_id, glossary_data=json.dumps(data,ensure_ascii=False))
                    overview = build_overview(pages, token)
                    self._update(revision_id, overview=json.dumps(overview,ensure_ascii=False))
                if failures:
                    raise ValueError('페이지 해설 실패: 용어집을 게시하지 않았습니다.')
                data['stage'] = 'glossary'
                self._update(revision_id, glossary_data=json.dumps(data,ensure_ascii=False))
                for page in pages:
                    analysis = page.get('analysis') or {}
                    if 'symbols' not in analysis:
                        symbols = extract_symbols(page, token, self.folder / revision['note_id'] / page['image'] if page.get('image') else None)
                        page['analysis'] = dict(analysis, symbols=symbols)
                        # Never copy on-demand details into the base page record.
                        self._page(revision_id, {k:v for k,v in page.items() if k not in {'detail','detail_status','ai_settings'}})
                data['symbols'] = merge_symbols(pages)
                self._update(revision_id, glossary_data=json.dumps(data,ensure_ascii=False))
                ranked = rank_candidates(raw_candidates,pages)
                keywords, validation = build_glossary(ranked,data['symbols'],token,json.loads(revision['keywords']))
                data.update(version=VERSION, validation=validation, stage='completed', published=True)
                with self.lock:
                    token.check()
                    if self._stopping.is_set(): raise TranscriptionCancelled()
                    self._update(revision_id, glossary_data=json.dumps(data,ensure_ascii=False),
                                 keywords=json.dumps(keywords,ensure_ascii=False),study_status='completed',error='')
                    self._publish_ranking(revision_id, keywords)

        except TranscriptionCancelled:
            if number is None:
                self._update(revision_id, study_status='pending' if self._stopping.is_set() else 'cancelled')
            else:
                self._detail(revision_id, number, {"detail_status": 'pending' if self._stopping.is_set() else 'cancelled'})
        except Exception as error:
            if number is None:
                self._update(revision_id, study_status="failed", error=str(error))
            else:
                self._detail(
                    revision_id,
                    number,
                    {"detail_status": "failed", "analysis_error": str(error)},
                )


    def _extract_worker(self):
        while not self._stopping.is_set():
            try:
                revision = self.extract_queue.get(timeout=.25)
            except queue.Empty:
                continue
            try:
                self.extract(revision)
            except Exception as error:
                print(f"Note worker error: {error}")
            finally:
                self.extract_queue.task_done()

    def _study_worker(self):
        while not self._stopping.is_set():
            try:
                revision, number = self.study_queue.get(timeout=.25)
            except queue.Empty:
                continue
            try:
                self.study(revision, number)
            except Exception as error:
                print(f"Note study worker error: {error}")
            finally:
                self.study_queue.task_done()
