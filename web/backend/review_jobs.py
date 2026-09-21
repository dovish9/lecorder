"""Review completed transcripts without withholding Whisper output."""

import json
import queue
import shutil
import threading
import uuid
from dataclasses import replace, asdict
from pathlib import Path
from .markdown import render_note, transcript_text
from .transcription import Options, CancellationToken


def relevant_context(snapshot, text):
    words = text.casefold()
    keywords = [k for k in snapshot.get("keywords", []) if k.get("included", True)]
    scored = []
    for page in snapshot.get("pages", []):
        terms = [k["term"] for k in keywords if page["number"] in k["pages"]]
        score = sum(term.casefold() in words for term in terms)
        if score:
            scored.append((score, page, terms))
    scored.sort(key=lambda item: (-item[0], item[1]["number"]))
    return "\n\n".join(
        f"[노트 {page['number']}쪽] 용어: {', '.join(terms[:20])}\n{page['text'][:1600]}"
        for _, page, terms in scored[:3]
    )[:6000]


class ReviewQueue:
    def __init__(self, store, engine, lock, start_worker=True):
        self.store, self.engine, self.lock = store, engine, lock
        self.queue = queue.Queue()
        self.pending = set()
        if start_worker:
            with store._connect() as db:
                interrupted = db.execute(
                    "SELECT id FROM recordings WHERE review_status IN ('processing','failed','queued')"
                ).fetchall()
            for row in interrupted:
                try:
                    self._restore_backup(store.get_recording(row["id"]))
                except OSError as error:
                    store.update_recording(
                        row["id"],
                        review_status="failed",
                        review_error=f"기존 Markdown 복구 실패: {error}",
                    )
            with store._connect() as db:
                db.execute(
                    "UPDATE review_runs SET status='failed',completed_at=?,error='앱 종료로 검수가 중단됐습니다.' WHERE completed_at=''",
                    (store.now(),),
                )
                db.execute(
                    "UPDATE recordings SET review_status='failed',review_error='앱 종료로 검수가 중단됐습니다.' WHERE review_status='processing'"
                )
                rows = db.execute(
                    "SELECT id FROM recordings WHERE status='completed' AND review_status='queued'"
                ).fetchall()
            for row in rows:
                self.enqueue(row["id"])
            threading.Thread(
                target=self._run, name="transcript-review", daemon=True
            ).start()

    @staticmethod
    def _restore_backup(row):
        if not row.note_path:
            return
        note = Path(row.note_path)
        backup = note.with_name(f".{row.id}-review-backup.md")
        if backup.is_file():
            backup.replace(note)

    def enqueue(self, recording_id):
        with self.lock:
            if recording_id in self.pending:
                return
            self.pending.add(recording_id)
            self.queue.put(recording_id)

    def retry(self, recording_id):
        with self.lock:
            row = self.store.get_recording(recording_id)
            if row.status != "completed" or row.review_status in {
                "queued",
                "processing",
            }:
                raise ValueError("완료된 전사의 검수만 다시 시작할 수 있습니다.")
            self._restore_backup(row)
            self.store.update_recording(
                recording_id, review_status="queued", review_error=""
            )
            self.enqueue(recording_id)

    def process(self, recording_id):
        backup = temporary = None
        published = False
        restore_required = False
        run_id = None
        stats = None
        try:
            with self.lock:
                row = self.store.get_recording(recording_id)
                if row.status != "completed" or row.review_status != "queued":
                    return
                self._restore_backup(row)
                run_id = uuid.uuid4().hex
                settings = {
                    "language": row.language,
                    "model": row.llm_model,
                    "format_text": row.format_transcript,
                    "note_revision_id": row.note_revision_id,
                }
                with self.store._connect() as db:
                    db.execute(
                        "INSERT INTO review_runs(id,recording_id,started_at,status,settings) VALUES (?,?,?,?,?)",
                        (
                            run_id,
                            recording_id,
                            self.store.now(),
                            "processing",
                            json.dumps(settings),
                        ),
                    )
                self.store.update_recording(recording_id, review_status="processing")
            segments = json.loads(row.segments_json)
            snapshot = json.loads(row.note_snapshot_json)
            options = Options(
                language=row.language,
                course_name=row.course_name,
                review_context=json.dumps(snapshot, ensure_ascii=False),
                format_text=row.format_transcript,
                llm_model=row.llm_model,
            )
            total, failed, suggestions, breaks, stats = self.engine._review_items(
                [s["text"] for s in segments],
                options,
                progress=None,
                confidence_map={
                    f"s{index:05d}": segment["avg_logprob"]
                    for index, segment in enumerate(segments, 1)
                    if isinstance(segment.get("avg_logprob"), (int, float))
                },
                cancellation=CancellationToken(),
            )
            if failed:
                raise ValueError(
                    f"Qwen 검수 {failed}/{total}구간 실패. 전사 원문은 보존했습니다."
                )
            indices = {int(s.removeprefix("s")) for s in breaks}
            with self.lock:
                current = self.store.get_recording(recording_id)
                if current.review_status != "processing":
                    return
                updated = replace(
                    current,
                    review_status="completed",
                    review_error="",
                    breaks_json=json.dumps(sorted(indices)),
                    transcript_text=transcript_text(segments, indices),
                )
                note = Path(current.note_path)
                temporary = note.with_name(f".{recording_id}-review.md")
                backup = note.with_name(f".{recording_id}-review-backup.md")
                shutil.copy2(note, backup)
                temporary.write_text(
                    render_note(updated, segments, indices), encoding="utf-8"
                )

                def publish():
                    nonlocal published, restore_required
                    temporary.replace(note)
                    published = True
                    restore_required = True

                try:
                    self.store.commit_result(
                        updated, [s.public() for s in suggestions], publish
                    )
                    restore_required = False
                except Exception:
                    if published:
                        backup.replace(note)
                        restore_required = False
                    raise
        except Exception as error:
            try:
                self.store.update_recording(
                    recording_id, review_status="failed", review_error=str(error)[:1000]
                )
            except KeyError:
                pass
        finally:
            if run_id:
                try:
                    current = self.store.get_recording(recording_id)
                    with self.store._connect() as db:
                        db.execute(
                            "UPDATE review_runs SET status=?,completed_at=?,metrics=?,error=? WHERE id=?",
                            (
                                current.review_status,
                                self.store.now(),
                                json.dumps(asdict(stats) if stats else {}),
                                current.review_error,
                                run_id,
                            ),
                        )
                except Exception as error:
                    print(f"검수 실행 기록 저장 실패: {error}")
            for path in (temporary, backup):
                if path and not (path == backup and restore_required):
                    try:
                        path.unlink(missing_ok=True)
                    except OSError as error:
                        print(f"검수 임시 파일 정리 실패: {error}")
            with self.lock:
                self.pending.discard(recording_id)

    def _run(self):
        while True:
            item = self.queue.get()
            try:
                self.process(item)
            except Exception as error:
                # A storage outage must not permanently kill the review worker.
                print(f"검수 작업 처리 실패 ({item}): {error}")
            finally:
                self.queue.task_done()
