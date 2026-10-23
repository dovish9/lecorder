from web.backend.runtime_config import get as runtime
from web.backend.ai_settings import using, defaults
from web.backend.execution_metrics import collect, current_trace
"""Review completed transcripts without withholding Whisper output."""
from web.backend.ai_providers import require_llm

from web.backend.result_codec import dumps as result_dumps
from web.backend.atomic import publish_file
import json
import queue
import shutil
from web.backend.execution_context import start_thread
import threading
import time
import uuid
from dataclasses import replace, asdict
from pathlib import Path
from web.backend.config import DEFAULT_LLM_MODEL
from web.backend.files import clear_hidden_flag
from web.backend.markdown import render_note, transcript_text
from web.backend.speech.transcription import Options, CancellationToken, TranscriptionCancelled


def relevant_context(snapshot, text):
    words = text.casefold()
    keywords = [k for k in snapshot.get("keywords", []) if k.get("included", True)]
    scored = []
    for page in snapshot.get("pages", []):
        terms = [form for k in keywords if page["number"] in k["pages"] for form in [k["term"], *k.get("spoken_forms",[])]]
        score = sum(term.casefold() in words for term in terms)
        if score:
            scored.append((score, page, terms))
    scored.sort(key=lambda item: (-item[0], item[1]["number"]))
    glossary = [{key:k.get(key) for key in ('term','meaning','spoken_forms')} for k in keywords]
    mapping = ('용어집: '+json.dumps(glossary,ensure_ascii=False)+'\n') if glossary else ''
    return mapping + "\n\n".join(
        f"[노트 {page['number']}쪽] 용어: {', '.join(terms[:20])}\n{page['text'][:1600]}"
        for _, page, terms in scored[:3]
    )[:runtime('LECORDER_REVIEW_CONTEXT_CHARS')]


class ReviewQueue:
    def __init__(self, store, engine, lock, start_worker=True):
        self.store, self.engine, self.lock = store, engine, lock
        self.on_completed = None
        self.queue = queue.Queue()
        self.pending = set()
        self.cancellations = {}
        self._threads = []
        self._stopping = threading.Event()
        if start_worker:
            self.start()

    def start(self):
        if self._threads or self._stopping.is_set():
            return
        with self.store.transaction() as db:
            interrupted = db.execute(
                "SELECT id FROM recordings WHERE review_status IN ('processing','failed','queued')"
            ).fetchall()
        for row in interrupted:
            try:
                self._restore_backup(self.store.get_recording(row["id"]))
            except OSError as error:
                self.store.update_recording(
                    row["id"],
                    review_status="failed",
                    review_error=f"기존 Markdown 복구 실패: {error}",
                )
        with self.store.transaction() as db:
            db.execute(
                "UPDATE review_runs SET status='interrupted',completed_at=?,error='앱 종료로 검수가 중단됐습니다. 완료 요청을 재사용하여 재개합니다.' WHERE completed_at=''",
                (self.store.now(),),
            )
            db.execute(
                "UPDATE recordings SET review_status='queued',review_error='앱 종료로 중단된 검수를 복구합니다.' WHERE review_status='processing'"
            )
            rows = db.execute(
                "SELECT id FROM recordings WHERE status='completed' AND review_status='queued'"
            ).fetchall()
        for row in rows:
            self.enqueue(row["id"])
        self._threads.append(start_thread(self._run, "transcript-review"))

    def prepare_shutdown(self):
        self._stopping.set()

    def close(self):
        self._stopping.set()
        with self.lock:
            for token in list(self.cancellations.values()):
                token.cancel()
        for thread in self._threads:
            if thread is not threading.current_thread():
                thread.join(timeout=10)

    @staticmethod
    def _restore_backup(row):
        if not row.note_path:
            return
        note = Path(row.note_path)
        backup = note.with_name(f".{row.id}-review-backup.md")
        if backup.is_file():
            publish_file(backup, note)

    def enqueue(self, recording_id):
        with self.lock:
            if self._stopping.is_set() or recording_id in self.pending:
                return
            try:
                require_llm((json.loads(self.store.get_recording(recording_id).ai_settings_json) or defaults())["llm"])
            except RuntimeError as error:
                self.store.update_recording(recording_id, review_status="failed", review_error=str(error))
                return
            self.pending.add(recording_id)
            self.queue.put(recording_id)

    def retry(self, recording_id):
        snapshot = self.store.ai.snapshot()
        with self.lock:
            row = self.store.get_recording(recording_id)
            if recording_id in self.pending:
                raise ValueError("이전 검수 작업을 정리 중입니다. 잠시 후 다시 시도하세요.")
            if row.status != "completed" or row.review_status in {
                "queued",
                "processing",
            }:
                raise ValueError("완료된 음성 변환의 검수만 다시 시작할 수 있습니다.")
            self._restore_backup(row)
            self.store.update_recording(
                recording_id, review_status="queued", review_error="", llm_model=snapshot["llm"]["model"], ai_settings_json=json.dumps(snapshot), llm_enabled=True, format_transcript=True, last_operation="review"
            )
            self.enqueue(recording_id)

    def cancel(self, recording_id):
        with self.lock:
            row = self.store.get_recording(recording_id)
            if row.review_status not in {"queued", "processing"}:
                return row
            token = self.cancellations.get(recording_id)
            if token:
                token.cancel()
            return self.store.update_recording(
                recording_id, review_status="cancelled", review_error=""
            )

    def process(self, recording_id):
        try:
            row = self.store.get_recording(recording_id)
        except KeyError:
            return
        from web.backend.request_history import execution
        with using(json.loads(row.ai_settings_json) or defaults()), collect(), execution(recording_id, 'review') as finish:
            result = self._process(recording_id)
            if not self._stopping.is_set():
                finish(self.store.get_recording(recording_id).review_status)
            return result

    def _process(self, recording_id):
        backup = temporary = None
        published = False
        restore_required = False
        run_id = None
        stats = None
        started = None
        review_seconds = 0.0
        saving_seconds = 0.0
        segment_count = 0
        suggestion_count = 0
        try:
            with self.lock:
                row = self.store.get_recording(recording_id)
                if self._stopping.is_set() or row.status != "completed" or row.review_status != "queued":
                    return
                self._restore_backup(row)
                run_id = uuid.uuid4().hex
                settings = {
                    "language": row.language,
                    "model": row.llm_model,
                    "ai_settings": json.loads(row.ai_settings_json),
                    "format_text": row.format_transcript,
                    "note_revision_id": row.note_revision_id,
                }
                with self.store.transaction() as db:
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
                row = self.store.update_recording(recording_id, review_status="processing", llm_enabled=True, format_transcript=True)
                cancellation = CancellationToken()
                self.cancellations[recording_id] = cancellation
                started = time.monotonic()
            require_llm((json.loads(self.store.get_recording(recording_id).ai_settings_json) or defaults())["llm"])
            segments = json.loads(row.segments_json)
            segment_count = len(segments)
            snapshot = json.loads(row.note_snapshot_json)
            options = Options(
                language=row.language,
                course_name=row.course_name,
                review_context=json.dumps(snapshot, ensure_ascii=False),
                format_text=row.format_transcript,
                llm_model=row.llm_model,
                ai_settings=json.loads(row.ai_settings_json) or defaults(),
            )
            total, failed, suggestions, breaks, stats = self.engine.review(
                [s["text"] for s in segments],
                options,
                progress=None,
                confidence_map={
                    f"s{index:05d}": segment["avg_logprob"]
                    for index, segment in enumerate(segments, 1)
                    if isinstance(segment.get("avg_logprob"), (int, float))
                },
                cancellation=cancellation,
            )
            cancellation.check()
            review_seconds = max(0, time.monotonic() - started - max(stats.waiting_seconds, current_trace()["waiting_seconds"]))+current_trace().get('reused_seconds',0)
            if failed:
                raise ValueError(
                    f"LLM 검수 {failed}/{total}구간 실패. 음성 변환 원문은 보존했습니다."
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
                    publish_file(temporary, note)
                    published = True
                    restore_required = True

                try:
                    saving_started = time.monotonic()
                    self.store.commit_result(
                        updated, [s.public() for s in suggestions], publish, output_paths=(note,)
                    )
                    saving_seconds = time.monotonic() - saving_started
                    suggestion_count = len(suggestions)
                    restore_required = False
                    if self.on_completed:
                        self.on_completed(updated)
                except Exception:
                    if published:
                        publish_file(backup, note)
                        restore_required = False
                    raise
        except TranscriptionCancelled:
            current = self.store.get_recording(recording_id)
            self.store.update_recording(recording_id,
                review_status='queued' if self._stopping.is_set() and current.review_status != 'cancelled' else 'cancelled', review_error="")
        except Exception as error:
            try:
                with self.lock:
                    if self.store.get_recording(recording_id).review_status != "cancelled":
                        self.store.update_recording(
                            recording_id, review_status="failed", review_error=str(error)[:1000]
                        )
            except KeyError:
                pass
        finally:
            if run_id:
                try:
                    current = self.store.get_recording(recording_id)
                    elapsed = time.monotonic() - started if started is not None else 0.0
                    metrics = asdict(stats) if stats else {}
                    metrics.update({
                        "pipeline_version": 2,
                        "provider_calls": current_trace()["provider_calls"],
                        "ai_control_events": current_trace().get("ai_control_events", []),
                        "llm_seconds": round(review_seconds or max(0, elapsed - current_trace()["waiting_seconds"] - saving_seconds), 3),
                        "saving_seconds": round(saving_seconds, 3),
                        "total_seconds": round(max(0, elapsed - max(stats.waiting_seconds if stats else 0, current_trace()["waiting_seconds"]))+current_trace().get('reused_seconds',0), 3),
                        "waiting_seconds": round(max(stats.waiting_seconds if stats else 0, current_trace()["waiting_seconds"]), 3),
                        "segment_count": segment_count,
                        "suggestion_count": suggestion_count,
                        "stages": [
                            {"name": "LLM 검수", "seconds": round(review_seconds or max(0, elapsed - current_trace()["waiting_seconds"] - saving_seconds), 3), "detail": f"{segment_count}개 발화 · 제안 {suggestion_count}개"},
                            {"name": "검수 결과 반영", "seconds": round(saving_seconds, 3), "detail": "Markdown과 수정 제안 저장"},
                        ],
                    })
                    with self.store.transaction() as db:
                        db.execute(
                            "UPDATE review_runs SET status=?,completed_at=?,metrics=?,error=? WHERE id=?",
                            (
                                current.review_status,
                                self.store.now(),
                                result_dumps(metrics,"metrics","review"),
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
                self.cancellations.pop(recording_id, None)
                self.pending.discard(recording_id)

    def _run(self):
        while not self._stopping.is_set():
            try:
                item = self.queue.get(timeout=.25)
            except queue.Empty:
                continue
            try:
                self.process(item)
            except Exception as error:
                # A storage outage must not permanently kill the review worker.
                print(f"검수 작업 처리 실패 ({item}): {error}")
            finally:
                self.queue.task_done()
