"""Persistent lecture-note revisions with independent extraction and study workers."""
from web.backend.runtime_config import get as runtime
from web.backend.ai_providers import require_llm
from web.backend.ai_settings import defaults

import hashlib
from web.backend.result_codec import dumps as result_dumps
import json
import shutil
import uuid
from pathlib import Path
from web.backend.notes.note_extract import SUPPORTED_NOTES, IMAGE_EXTENSIONS
from web.backend.speech.transcription import CancellationToken

from web.backend.notes.note_repository import ANALYZER_VERSION


class NoteIntake:
    def register(self, course_id, files, title=""):
        self.store.courses.get_course(course_id)
        if not files or len(files) > runtime('LECORDER_NOTE_MAX_FILES'):
            raise ValueError(f"강의노트 파일을 선택하세요. 최대 {runtime('LECORDER_NOTE_MAX_FILES')}개입니다.")
        suffixes = [Path(name).suffix.lower() for name, _ in files]
        if any(s not in SUPPORTED_NOTES for s in suffixes) or (
            len(files) > 1 and any(s not in IMAGE_EXTENSIONS for s in suffixes)
        ):
            raise ValueError(
                "PDF 한 개 또는 PNG·JPEG·HEIC 이미지 묶음을 선택하세요."
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
                        if size > runtime('LECORDER_NOTE_MAX_MB') * 1024 * 1024:
                            raise ValueError(
                                f"강의노트 원본 합계는 {runtime('LECORDER_NOTE_MAX_MB')}MB 이하여야 합니다."
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
            with self.lock, self.store.transaction() as db:
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
            with self.store.transaction() as db:
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
            with self.store.transaction() as db:
                db.execute(
                    "INSERT INTO note_revisions(id,note_id,analyzer,status,created_at,ai_settings) VALUES (?,?,?,?,?,?)",
                    (
                        revision_id,
                        note_id,
                        ANALYZER_VERSION,
                        "queued",
                        self.store.now(),
                        json.dumps(self.store.ai.snapshot()),
                    ),
                )
                db.execute(
                    "UPDATE lecture_notes SET revision_id=? WHERE id=?",
                    (revision_id, note_id),
                )
            self.tokens[revision_id] = CancellationToken()
            try:
                require_llm((json.loads(self._revision(revision_id)["ai_settings"]) or defaults())["llm"])
            except RuntimeError as error:
                self._update(revision_id, status="failed", study_status="failed", error=str(error))
                return self.get(note_id)
            self.extract_queue.put(revision_id)
            return self.get(note_id)


    def regenerate_glossary(self, note_id):
        with self.lock:
            note = self.get(note_id)
            if note['deleted'] or note['status'] not in {'ready','partial'} or note['study_status'] in {'pending','analyzing'}:
                raise ValueError('원문 분석이 끝난 후 용어집을 다시 생성하세요.')
            if any(p.get('detail_status') in {'pending','analyzing'} for p in self.pages(note['revision_id'])):
                raise ValueError('상세 해설이 끝난 후 용어집을 다시 생성하세요.')
            snapshot = self.store.ai.snapshot()
            require_llm(snapshot['llm'])
            # Clone without changing a previously bound revision or its keywords.
            self._publish_ranking(note['revision_id'], note['keywords'])
            revision = self.get(note_id)['revision_id']
            data = json.loads(self._revision(revision)['glossary_data'])
            data.update(mode='glossary',stage='glossary', published=data.get('published', not bool(data.get('version'))))
            with self.store.transaction() as db:
                db.execute('UPDATE note_revisions SET glossary_data=?, study_status=?, error=?, ai_settings=? WHERE id=?',
                           (result_dumps(data,'glossary','glossary'),'pending','',json.dumps(snapshot),revision))
            self.tokens[revision] = CancellationToken()
            self.study_queue.put((revision,None))
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
            with self.store.transaction() as db:
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
                                result_dumps(payload,"note-detail","page"),
                                revision,
                                row["number"],
                            ),
                        )
        return self.get(note_id)


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
            snapshot = self.store.ai.snapshot()
            try:
                require_llm(snapshot["llm"])
            except RuntimeError as error:
                self._detail(note["revision_id"], number, {"detail_status": "failed", "analysis_error": str(error)})
                return
            self._detail(note["revision_id"], number, {"detail_status": "pending", "ai_settings": snapshot})
            self.study_queue.put((note["revision_id"], number))
