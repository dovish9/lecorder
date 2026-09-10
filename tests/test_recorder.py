from __future__ import annotations

import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from web.backend.config import EnvironmentStore
from web.backend.engine import Transcriber
from web.backend.files import OutputFiles
from web.backend.markdown import render_note
from web.backend.pipeline import RecordingPipeline
from web.backend.records import Recording
from web.backend.review import (
    combined_review_prompt,
    reviewer_prompt,
    validated_suggestions,
)
from web.backend.routes import create_app, schedule_managed_restart
from web.backend.state import JobState
from web.backend.store import LectureStore
from web.backend.transcription import (
    CancellationToken,
    Options,
    Result,
    Suggestion,
    TranscriptSegment,
    TranscriptionCancelled,
    WhisperResult,
)


class FakeTranscriber:
    def __init__(self) -> None:
        self.calls: list[Options] = []

    def transcribe(self, source, options, incoming=None, progress=None,
                   cancellation=None) -> Result:
        self.calls.append(options)
        return Result(
            "정리된 전사문", segments=(TranscriptSegment(1, 12.0, 18.0, "정리된 전사문"),),
            duration_seconds=18.0,
            conversion_seconds=.1, whisper_primary_seconds=.2, qwen_seconds=.3,
            pipeline_settings={"entropy_thold": "2.8", "no_context": "true"},
            qwen_requests=2, qwen_prompt_tokens=120, qwen_output_tokens=24,
        )


class RecorderTests(unittest.TestCase):
    @staticmethod
    def environment(root: Path) -> tuple[EnvironmentStore, Path]:
        project = root / "lecorder"
        whisper = root / "whisper.cpp"
        output = root / "output"
        project.mkdir()
        whisper.mkdir()
        output.mkdir()
        (project / "app.py").write_text("", encoding="utf-8")
        (project / "web/backend").mkdir(parents=True)
        (whisper / "build/bin").mkdir(parents=True)
        (whisper / "build/bin/whisper-server").write_text("", encoding="utf-8")
        (whisper / "models").mkdir()
        (whisper / "models/ggml-large-v3.bin").write_text("", encoding="utf-8")
        (whisper / "models/ggml-silero-v6.2.0.bin").write_text("", encoding="utf-8")
        environment = EnvironmentStore(root / ".env", project)
        environment.update(project_dir=str(project), whisper_cpp_dir=str(whisper), output_dir=str(output))
        return environment, output

    def test_managed_restart_is_only_scheduled_under_start_command(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(schedule_managed_restart())
        with patch.dict("os.environ", {"LECORDER_MANAGED_RESTART": "1"}, clear=True), patch(
            "web.backend.routes.threading.Timer"
        ) as timer:
            self.assertTrue(schedule_managed_restart())
            timer.return_value.start.assert_called_once()

    def test_browser_recording_chunks_are_durable_before_upload(self) -> None:
        script = (Path(__file__).parents[1] / "web/static/dashboard.js").read_text(encoding="utf-8")
        storage_script = (Path(__file__).parents[1] / "web/static/recording-store.js").read_text(encoding="utf-8")
        queue_start = script.index("function queueRecordingChunk(blob)")
        queue_end = script.index("async function retryFailedChunks", queue_start)
        queue_source = script[queue_start:queue_end]

        self.assertIn('const DATABASE_NAME = "lecorder-browser-recovery"', storage_script)
        self.assertIn("window.indexedDB.open(DATABASE_NAME, DATABASE_VERSION)", storage_script)
        self.assertLess(queue_source.index("putPendingChunk(item)"), queue_source.index("app.chunkUploadChain ="))
        self.assertIn("function preferredRecordingMimeType()", script)
        self.assertLess(
            script.index('"audio/mp4;codecs=mp4a.40.2"'),
            script.index('"audio/webm;codecs=opus"'),
        )
        self.assertIn("const recorderOptions = {audioBitsPerSecond: 128000}", script)
        self.assertIn("sampleRate: {ideal: 48000}", script)
        self.assertIn("noiseSuppression: {ideal: true}", script)
        self.assertLess(queue_source.index("putPendingChunk(item)"), queue_source.index("uploadChunk(item.recordingId"))
        self.assertIn("{attempts: 1, timeoutMs: 4000}", queue_source)
        self.assertIn("await deletePendingChunk(item.recordingId, item.index)", queue_source)
        retry_source = script[
            script.index("async function retryFailedChunks"):
            script.index("async function finalizeRecording")
        ]
        self.assertNotIn("app.failedChunks = [];", retry_source)
        self.assertIn("async function recoverRecordingSessions(recordings)", script)
        self.assertIn("function restoreRecordingSession(session)", script)
        manual_retry = script[
            script.index("async function retryRecording(recording, button)"):
            script.index("async function deleteRecordingHistory", script.index("async function retryRecording"))
        ]
        self.assertLess(manual_retry.index("restoreRecordingSession(session)"), manual_retry.index("refreshStatus()"))
        self.assertIn('window.addEventListener("beforeunload"', script)

    def test_browser_webm_is_normalized_to_playback_safe_opus_frames(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.webm"
            destination = root / "normalized.webm"
            source.write_bytes(b"source")
            recording = Recording(
                id="browser-audio",
                course_id=None,
                course_name="테스트",
                title="브라우저 녹음",
                source_kind="browser",
                extension=".webm",
                status="completed",
                language="ko",
                prompt="",
                corrections="",
                format_transcript=True,
                llm_enabled=False,
                llm_model="",
                output_dir=str(root),
            )
            cancellation = Mock()

            def encode(command, **_kwargs):
                destination.write_bytes(b"normalized")
                return subprocess.CompletedProcess(command, 0)

            cancellation.run.side_effect = encode
            with patch("web.backend.pipeline.shutil.which", return_value="/usr/bin/ffmpeg"):
                RecordingPipeline._store_playable_audio(
                    recording, source, destination, cancellation,
                )

            command = cancellation.run.call_args.args[0]
            self.assertIn("aresample=async=1:first_pts=0", command)
            self.assertIn("libopus", command)
            self.assertEqual(command[command.index("-frame_duration") + 1], "20")
            self.assertEqual(command[command.index("-ar") + 1], "48000")
            self.assertNotIn("copy", command)

    def test_dashboard_unifies_recordings_and_keeps_review_audio_controls(self) -> None:
        root = Path(__file__).parents[1]
        markup = (root / "web/static/dashboard.html").read_text(encoding="utf-8")
        script = (root / "web/static/dashboard.js").read_text(encoding="utf-8")
        dashboard_css = (root / "web/static/dashboard.css").read_text(encoding="utf-8")

        self.assertEqual(markup.count('data-view="dashboard"'), 1)
        self.assertEqual(markup.count('data-view="courses"'), 1)
        self.assertEqual(markup.count('data-view="recordings"'), 1)
        self.assertNotIn('id="newRecordingButton"', markup)
        self.assertNotIn('id="startLiveButton"', markup)
        self.assertNotIn('id="startUploadButton"', markup)
        self.assertIn('id="commandDock"', markup)
        self.assertIn('id="queuePanel"', markup)
        self.assertIn('id="queueList" aria-label="현재 대기열"', markup)
        self.assertIn('function renderQueue(recordings)', script)
        self.assertIn('function createQueuePipeline(recording, job)', script)
        self.assertIn('recording.status === "processing" && app.job?.job_id === recording.id', script)
        self.assertIn('label: "변환·전사"', script)
        self.assertIn('label: "품질 확인"', script)
        self.assertIn('label: "Qwen3"', script)
        self.assertIn('.queue-pipeline li.complete i:before', dashboard_css)
        self.assertIn('.queue-pipeline li.current i', dashboard_css)
        self.assertIn('className = `queue-item ${isQueued ? "queued" : "active"}`', script)
        self.assertIn('.queue-panel[open]>summary svg', dashboard_css)
        self.assertIn('.queue-panel[open]>.queue-list{', dashboard_css)
        self.assertIn('bottom:100%', dashboard_css)
        self.assertIn('box-shadow:none', dashboard_css)
        self.assertIn('.dock-course-picker>.dock-course-menu{', dashboard_css)
        self.assertIn('border-radius:14px 14px 0 0', dashboard_css)
        self.assertIn('id="dockCourseSelect"', markup)
        self.assertIn('id="dockCoursePicker"', markup)
        self.assertIn('id="dockCourseMenu" role="listbox"', markup)
        self.assertIn('className = "dock-course-option"', script)
        self.assertIn('id="dockRecordButton"', markup)
        self.assertIn('id="dockUploadButton"', markup)
        self.assertIn('id="dockRecordingSession"', markup)
        self.assertIn('id="dockSoundMeter"', markup)
        self.assertIn('id="dockPauseButton"', markup)
        self.assertIn('data-record-state="idle"', markup)
        self.assertIn('id="uploadDialog" aria-labelledby="uploadDialogTitle"', markup)
        self.assertIn('id="uploadCoursePicker"', markup)
        self.assertIn('id="uploadCourseMenu" role="listbox"', markup)
        self.assertIn('id="uploadCourseLabel"', markup)
        self.assertIn('id="dropZone"', markup)
        self.assertLess(markup.index('for="uploadTitle"'), markup.index('id="dropZone"'))
        self.assertNotIn('data-view="storage"', markup)
        self.assertNotIn('data-view="history"', markup)
        self.assertNotIn('data-view-panel="capture"', markup)
        self.assertNotIn('class="capture-workspace"', markup)
        self.assertIn('id="libraryList"', markup)
        self.assertIn('id="libraryDetail"', markup)
        self.assertIn('class="course-detail-scroll"', markup)
        self.assertIn('id="librarySort"', markup)
        self.assertIn('<option value="name-asc" selected>이름순</option>', markup)
        self.assertIn('<option value="date-desc">최근 등록순</option>', markup)
        self.assertIn('id="reviewDialog"', markup)
        self.assertIn('id="guideDialog" aria-label="Lecorder 사용 안내"', markup)
        self.assertNotIn('id="guideDialogTitle"', markup)
        self.assertNotIn('<div class="guide-frame"><header>', markup)
        self.assertIn('.help-content h2:focus', (root / "web/static/guide.css").read_text(encoding="utf-8"))
        self.assertIn('function deriveLibraryEntries()', script)
        self.assertIn('function sortLibraryEntries(entries, order = "name-asc")', script)
        self.assertIn('new Intl.Collator("ko", {numeric: true, sensitivity: "base"})', script)
        self.assertIn('function storageCoverage(storage)', script)
        self.assertIn('function isInternalStorageFile(name)', script)
        self.assertIn('function visibleStorageItems(items)', script)
        self.assertIn('value.startsWith(".")', script)
        self.assertIn('/\\.playback-[a-f0-9]{8,}', script)
        self.assertIn('return "오디오만 존재 · Markdown 없음"', script)
        self.assertIn('return "Markdown만 존재 · 오디오 없음"', script)
        self.assertIn('function storageFileRow(kind, names)', script)
        self.assertIn('function createDetailMenu(entry, title)', script)
        self.assertIn('function beginEntryTitleEdit(entry, title, menu)', script)
        self.assertIn('detailMenuButton("수정"', script)
        self.assertIn('detailMenuButton("로그 보기"', script)
        self.assertIn('recording.error', script[script.index("function hasPipelineLog"):script.index("function openPipelineLog")])
        self.assertIn('historyTitle.textContent = "실패 이력"', script)
        self.assertIn('detailSection(recording.status === "failed" ? "실패 원인"', script)
        self.assertLess(script.index('detailMenuButton("수정"'), script.index('detailMenuButton("로그 보기"'))
        self.assertIn('id="pipelineDialog"', markup)
        self.assertIn('aria-labelledby="pipelineDialogTitle"', markup)
        self.assertIn('ui.pipelineDialog.showModal()', script)
        self.assertNotIn('ui.libraryDetail.append(pipeline)', script)
        self.assertIn('detailMenuButton("기록 삭제"', script)
        self.assertIn('const interrupted = recording && ["recording", "recoverable"].includes(recording.status)', script)
        self.assertIn('await deleteRecordingRecovery(recording.id)', script)
        self.assertIn('detailMenuButton("파일까지 삭제"', script)
        self.assertNotIn('detailSection("작업 관리")', script)
        self.assertIn('method: "DELETE"', script)
        self.assertIn('function renderMarkdownInto(container, source, audio)', script)
        self.assertIn('function markdownTimestamp(target)', script)
        self.assertIn(
            'function createAudioPlayer(source, label = "녹음", expectedDuration = 0, storageName = "")',
            script,
        )
        self.assertIn('audio.preload = "auto"', script)
        self.assertIn('"/api/storage/audio/prepare"', script)
        self.assertIn('audio.currentTime = Math.max(0, target)', script)
        self.assertIn('player.seekTo(seconds, {autoplay: true, reveal: true})', script)
        self.assertIn('rewind.innerHTML = \'<svg', script)
        self.assertIn('forward.innerHTML = \'<svg', script)
        self.assertIn('<text x="12" y="12">5</text>', script)
        self.assertIn('status.className = "audio-load-state sr-only"', script)
        self.assertNotIn('controls.append(status)', script)
        self.assertIn('timeline.append(play, elapsed, scrubber, duration, rewind, forward)', script)
        self.assertIn('String(hours).padStart(2, "0")', script)
        self.assertNotIn('detailAudio.controls = true', script)
        self.assertIn('self._store_playable_audio(recording, source, hidden_audio, cancellation)', (root / "web/backend/pipeline.py").read_text(encoding="utf-8"))
        self.assertNotIn('id="previewDialog"', markup)
        self.assertNotIn('function openStoragePreview', script)
        self.assertIn('.language-control{display:grid;width:min(100%,420px)', dashboard_css)
        self.assertIn('.language-control input:focus-visible+span', dashboard_css)
        self.assertIn('.course-detail-scroll{min-width:0;min-height:0;overflow-y:auto', dashboard_css)
        self.assertIn('.course-list{display:grid;min-height:0;align-content:start;gap:2px;overflow-y:auto', dashboard_css)
        self.assertIn('.dashboard-grid{grid-template-columns:repeat(2,minmax(0,1fr))}', dashboard_css)
        self.assertIn('.recordings-workspace{grid-template-columns:repeat(2,minmax(0,1fr))}', dashboard_css)
        self.assertIn('.command-dock{\n  min-height:68px;', dashboard_css)
        self.assertIn('border-radius:999px', dashboard_css)
        self.assertIn('.dock-recording-session{', dashboard_css)
        self.assertIn('width:max-content;\n  max-width:0;', dashboard_css)
        self.assertIn('.dock-recording-copy strong{max-width:min(340px,30vw)', dashboard_css)
        self.assertIn('@keyframes droplet-bridge', dashboard_css)
        self.assertIn('@keyframes pause-drop-arrive', dashboard_css)
        self.assertIn('animation:pause-drop-arrive .46s cubic-bezier(.2,.8,.2,1) .08s both', dashboard_css)
        self.assertIn('.upload-drop-zone{', dashboard_css)
        self.assertIn('.upload-course-picker>summary{', dashboard_css)
        self.assertIn('.upload-course-option[aria-selected=true]', dashboard_css)
        self.assertIn('async function selectUploadCourse(courseId)', script)
        self.assertIn('uploadItem.className = "upload-course-option"', script)
        self.assertIn('function setUploadTitlePreset()', script)
        self.assertIn('ui.uploadTitle.value = automaticTitle()', script)
        self.assertIn('function startDockRecording()', script)
        self.assertIn('function stopDockRecording()', script)
        self.assertIn('function frequencyBandLevel(', script)
        self.assertIn('async function acquireMicrophone(timeoutMs = 12000)', script)
        self.assertIn('async function requestJsonBeforeDeadline(', script)
        self.assertNotIn('setRecorderState("starting");\n  await flushCourseSave();', script)
        self.assertIn('const SUPPORTED_UPLOAD_EXTENSIONS = new Set([', script)
        self.assertIn('if (parts[0] === "capture")', script)
        self.assertIn('ui.reviewDialogTitle.textContent = "수정 제안"', script)
        self.assertIn('현재 노트와 ${courseName} 강의의 확정 교정 규칙', script)
        self.assertIn('function applyRoute()', script)
        self.assertIn('window.location.replace("#/dashboard")', script)
        self.assertIn('function renderDashboard()', script)
        self.assertIn('`#/recordings/job/${encodeURIComponent(entry.recording.id)}`', script)
        self.assertIn('`#/recordings/file/${encodeURIComponent(entry.storage.name)}`', script)
        self.assertIn('function stopReviewAudio()', script)
        self.assertIn('setReviewListenLabel(button, "원음 멈추기", true)', script)
        self.assertIn('button.setAttribute("aria-pressed", "true")', script)
        self.assertIn('className = "review-comparison"', script)
        self.assertIn('function appendHighlightedReviewContext', script)
        self.assertIn('progress.setAttribute("aria-label"', script)
        self.assertIn('"승인하고 학습"', script)
        self.assertIn('.review-comparison{', dashboard_css)
        self.assertIn('@container (max-width:600px)', dashboard_css)
        self.assertIn('["반복 보호", quality.repetition_fallback_seconds', script)
        self.assertIn('recordingsFingerprint: ""', script)
        self.assertIn('if (fingerprint === app.recordingsFingerprint) return', script)
        self.assertNotIn('class="legacy-bridge"', markup)
        self.assertNotIn('id="historySearch"', markup)
        self.assertNotIn('id="storageList"', markup)
        self.assertNotIn('function renderStorageItems()', script)
        self.assertNotIn('function renderReviewOverview()', script)

    def test_macos_frontend_keeps_fallbacks_theme_and_help_search(self) -> None:
        root = Path(__file__).parents[1]
        dashboard = (root / "web/static/dashboard.html").read_text(encoding="utf-8")
        dashboard_css = (root / "web/static/dashboard.css").read_text(encoding="utf-8")
        guide = (root / "web/static/guide.html").read_text(encoding="utf-8")
        guide_css = (root / "web/static/guide.css").read_text(encoding="utf-8")
        guide_script = (root / "web/static/guide.js").read_text(encoding="utf-8")

        self.assertIn('id="guideButton" href="/guide"', dashboard)
        self.assertIn('name="color-scheme" content="light dark"', dashboard)
        self.assertRegex(dashboard_css, r'@media\s*\(prefers-color-scheme:\s*dark\)')
        self.assertRegex(dashboard_css, r'@media\s*\(prefers-reduced-motion:\s*reduce\)')
        self.assertIn('id="guideSearch"', guide)
        self.assertIn('id="guideSearchEmpty"', guide)
        self.assertIn('id="guideSearchResults"', guide)
        self.assertIn('id="guidePrevious"', guide)
        self.assertIn('id="guideNext"', guide)
        self.assertIn('class="topic-sidebar"', guide)
        self.assertIn('@media (prefers-color-scheme: dark)', guide_css)
        self.assertIn('function searchGuide()', guide_script)
        self.assertIn('function showTopic(', guide_script)
        self.assertIn('event.key !== "Escape"', guide_script)

    def test_courses_hints_and_output_path_are_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, output = self.environment(root)
            store = LectureStore(root / "web/data/lecorder.db", environment)
            course = store.create_course("통계학")
            store.update_course(course.id, prompt="population, sample", corrections="표정=표본")
            active = LectureStore(store.path, environment).get()
            self.assertEqual(active.prompt, "population, sample")
            self.assertEqual(active.corrections, "표정=표본")
            self.assertEqual(Path(active.output_dir).resolve(), output.resolve())

    def test_environment_resolves_relative_paths_from_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "lecorder"
            project.mkdir()
            environment_file = project / ".env"
            environment_file.write_text(
                "LECORDER_DIR=.\n"
                "WHISPER_CPP_DIR=../whisper.cpp\n"
                "LECORDER_OUTPUT_DIR=output\n",
                encoding="utf-8",
            )

            environment = EnvironmentStore(environment_file, project).get()

            self.assertEqual(Path(environment.project_dir), project.resolve())
            self.assertEqual(Path(environment.whisper_cpp_dir), (root / "whisper.cpp").resolve())
            self.assertEqual(Path(environment.output_dir), (project / "output").resolve())

    def test_path_update_preserves_advanced_environment_options(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, output = self.environment(root)
            with environment.path.open("a", encoding="utf-8") as target:
                target.write(
                    "WHISPER_RETRY_MAX_GROUPS=6\n"
                    "WHISPER_LOW_CONFIDENCE=-0.7\n"
                    "OLLAMA_MODEL=custom-review:latest\n"
                )
            environment.update(output_dir=str(output))
            saved = environment.path.read_text(encoding="utf-8")
            self.assertIn("WHISPER_RETRY_MAX_GROUPS=6", saved)
            self.assertIn("WHISPER_LOW_CONFIDENCE=-0.7", saved)
            self.assertIn("OLLAMA_MODEL=custom-review:latest", saved)

    def test_start_command_prepares_whisper_before_queue_worker(self) -> None:
        script = (Path(__file__).parents[1] / "start.command").read_text(encoding="utf-8")
        self.assertLess(script.index('echo "▶️ Whisper 시작'), script.index('echo "▶️ 대시보드와 대기열 시작"'))
        self.assertIn("compatible_whisper_server", script)
        self.assertIn('[[ "$EXISTING_WHISPER_COMMAND" == *"$WHISPER_VAD_MODEL"* ]]', script)
        self.assertIn('[ -x "$SCRIPT_ROOT/.venv/bin/python" ]', script)
        self.assertIn('command -v python3', script)
        self.assertIn('WHISPER_PORT="${WHISPER_PORT:-8080}"', script)
        self.assertIn('--host 127.0.0.1 --port "$WHISPER_PORT"', script)
        self.assertNotIn("PYENV_ROOT", script)

    def test_reviewer_requests_suggestions_and_applies_only_verifiable_edits(self) -> None:
        options = Options(language="ko", course_name="통계학", prompt="모집단, 표본", llm_model="test")
        self.assertIn("과목명: 통계학", reviewer_prompt(options))
        self.assertIn("제안 누락이 잘못된 제안보다 낫다", reviewer_prompt(options))
        self.assertIn("population, 파퓰레이션, 모집단은 서로 교체하지 않는다", reviewer_prompt(options))
        self.assertIn("들어갈→포함될, 예측→추정", reviewer_prompt(options))
        absolute_phonetic_rule = "뜻·문맥·철자·전문용어 여부·confidence와 관계없이 무조건 제안하지 않는다"
        self.assertIn(absolute_phonetic_rule, reviewer_prompt(options))
        self.assertIn(absolute_phonetic_rule, combined_review_prompt(options))
        self.assertIn("technical_term도 발음 조건을 예외로 면제하지 않는다", reviewer_prompt(options))
        self.assertIn("technical_term도 발음 조건을 예외로 면제하지 않는다", combined_review_prompt(options))
        self.assertIn("글자 수나 발화 수를 맞추기 위해 나누지 않고", combined_review_prompt(options))
        self.assertIn("JSON의 edits와 break_after만 반환한다", combined_review_prompt(options))
        review_response = Mock()
        review_response.raise_for_status.return_value = None
        review_response.json.return_value = {"message": {"content": json.dumps({
            "edits": [
                {"sentence_id": "s00001", "original": "표정을", "replacement": "표본을",
                 "error_type": "technical_term", "reason": "힌트의 표본과 발음이 유사함", "confidence": .96},
                {"sentence_id": "s00002", "original": "없는 말", "replacement": "조작",
                 "error_type": "phonetic_asr", "reason": "발음이 유사함", "confidence": .99},
            ],
            "break_after": ["s00001"],
        }, ensure_ascii=False)}}
        with patch("web.backend.engine.ollama_ready", return_value=(True, True)), patch(
            "web.backend.engine.requests.post", return_value=review_response
        ) as post:
            value, total, fallback, suggestions = Transcriber._review(
                "이 표정을 이용합니다. 다음 설명입니다.", options, None
            )
        self.assertEqual((total, fallback), (1, 0))
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(value, "이 표정을 이용합니다.\n\n다음 설명입니다.")
        self.assertEqual(post.call_count, 1)
        review_payload = post.call_args.kwargs["json"]
        self.assertIn("edits", review_payload["format"]["properties"])
        self.assertIn("break_after", review_payload["format"]["properties"])
        self.assertFalse(review_payload["think"])
        self.assertIn("앞 문맥(후보 단어·경계 확인 전용, 수정 금지)", review_payload["messages"][1]["content"])

    def test_suggestion_validator_rejects_copyediting_and_clause_rewrites(self) -> None:
        sentence_map = {
            "s00001": "그래서 이런 식으로 되어야지.",
            "s00002": "그럼 1000명 늘잖아요.",
            "s00003": "공주형 자료가 있어요.",
            "s00004": "우리에게 가장 중요한 거는 숫자가 아니다.",
        }
        candidates = [
            {"sentence_id": "s00001", "original": "되어야지", "replacement": "되어야 해",
             "error_type": "phonetic_asr", "reason": "말투 조정", "confidence": .99},
            {"sentence_id": "s00002", "original": "1000명", "replacement": "1000명이",
             "error_type": "phonetic_asr", "reason": "조사 보완", "confidence": .99},
            {"sentence_id": "s00004", "original": "중요한 거는 숫자가 아니다",
             "replacement": "중요한 건 분석 방법이다", "error_type": "technical_term",
             "reason": "강의 핵심을 명확히 함", "confidence": .99},
            {"sentence_id": "s00003", "original": "공주형 자료", "replacement": "범주형 자료",
             "error_type": "technical_term", "reason": "통계 용어와 발음이 유사함", "confidence": .97},
        ]
        suggestions = validated_suggestions(sentence_map, candidates)
        self.assertEqual([(item.original, item.replacement) for item in suggestions], [
            ("공주형 자료", "범주형 자료"),
        ])
        unhinted_term = validated_suggestions({"s00001": "공주형 자료가 있어요."}, [{
            "sentence_id": "s00001", "original": "공주형 자료", "replacement": "범주형 자료",
            "error_type": "technical_term", "reason": "통계 용어와 발음이 유사함",
            "confidence": .97,
        }])
        self.assertEqual([(item.original, item.replacement) for item in unhinted_term], [
            ("공주형 자료", "범주형 자료"),
        ])
        unsupported = validated_suggestions({"s00001": "아무것도 운영하고 있어."}, [{
            "sentence_id": "s00001", "original": "운영", "replacement": "관리",
            "error_type": "technical_term", "reason": "통계 용어 후보", "confidence": .98,
        }])
        self.assertEqual(unsupported, [])
        phonetic = validated_suggestions({"s00001": "설리해서 뽑거든."}, [{
            "sentence_id": "s00001", "original": "설리해서", "replacement": "설계해서",
            "error_type": "phonetic_asr", "reason": "두 표현의 발음이 유사함", "confidence": .96,
        }])
        self.assertEqual([(item.original, item.replacement) for item in phonetic], [
            ("설리해서", "설계해서"),
        ])
        threshold = validated_suggestions({"s00001": "공주형 자료가 있어요."}, [
            {
                "sentence_id": "s00001", "original": "공주형 자료", "replacement": "범주형 자료",
                "error_type": "technical_term", "reason": "통계 용어와 발음이 유사함",
                "confidence": .80,
            },
        ])
        self.assertEqual(len(threshold), 1)
        below_threshold = validated_suggestions({"s00001": "공주형 자료가 있어요."}, [
            {
                "sentence_id": "s00001", "original": "공주형 자료", "replacement": "범주형 자료",
                "error_type": "technical_term", "reason": "통계 용어와 발음이 유사함",
                "confidence": .799,
            },
        ])
        self.assertEqual(below_threshold, [])

    def test_suggestion_validator_rejects_observed_semantic_and_spelling_edits(self) -> None:
        observed = [
            ("파퓰레이션", "모집단", "technical_term", "통계학 용어 모집단이 오인식됨"),
            ("들어갈 확률이", "포함될 확률이", "technical_term", "포함될 확률이 전문용어로 사용됨"),
            ("몰수가", "모집단", "technical_term", "강의 힌트에 모집단이 사용됨"),
            ("유머리컬", "범주형", "technical_term", "범주형 자료가 정확한 표현"),
            ("모집단을 예측하는 그런", "모집단을 추정하는 그런", "technical_term", "통계학에서 추정을 의미"),
            ("100명만 있으면 돼", "100명만 있으면 되", "phonetic_asr", "발음이 가까움"),
            ("KT에", "추적", "phonetic_asr", "발음상 추적과 유사함"),
        ]
        sentence_map = {
            f"s{index:05d}": f"앞 문맥 {original} 뒤 문맥"
            for index, (original, _, _, _) in enumerate(observed, 1)
        }
        candidates = [
            {
                "sentence_id": f"s{index:05d}", "original": original,
                "replacement": replacement, "error_type": error_type,
                "reason": reason, "confidence": .99,
            }
            for index, (original, replacement, error_type, reason) in enumerate(observed, 1)
        ]
        suggestions = validated_suggestions(sentence_map, candidates)
        self.assertEqual(suggestions, [])

    def test_reviewer_passes_whisper_confidence_only_to_word_review(self) -> None:
        options = Options(language="ko", course_name="통계학", llm_model="test", format_text=False)
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"message": {"content": json.dumps({"edits": []})}}
        with patch("web.backend.engine.ollama_ready", return_value=(True, True)), patch(
            "web.backend.engine.requests.post", return_value=response
        ) as post:
            Transcriber._review_items(
                ["공주형 자료가 있어요."], options, None, {"s00001": -.8234}
            )
        content = post.call_args.kwargs["json"]["messages"][1]["content"]
        self.assertIn("[s00001 avg_logprob=-0.823]", content)
        self.assertEqual(post.call_count, 1)

    def test_reviewer_does_not_force_length_based_paragraphs(self) -> None:
        options = Options(language="ko", course_name="강의", llm_model="test")
        source = " ".join(f"같은 주제의 설명 {index}." for index in range(80))
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "message": {"content": json.dumps({"edits": [], "break_after": []})}
        }
        with patch("web.backend.engine.ollama_ready", return_value=(True, True)), patch(
            "web.backend.engine.requests.post", return_value=response
        ):
            value, _, fallback, _ = Transcriber._review(source, options, None)
        self.assertEqual(fallback, 0)
        self.assertNotIn("\n\n", value)

    def test_reviewer_splits_and_retries_truncated_json(self) -> None:
        options = Options(language="ko", course_name="통계학", prompt="모집단, 표본", llm_model="test")
        truncated = Mock()
        truncated.raise_for_status.return_value = None
        truncated.json.return_value = {
            "done_reason": "length", "message": {"content": '{"edits":['}
        }
        corrected = Mock()
        corrected.raise_for_status.return_value = None
        corrected.json.return_value = {"message": {"content": json.dumps({
            "edits": [{
                "sentence_id": "s00001", "original": "표정을", "replacement": "표본을",
                "error_type": "technical_term", "reason": "힌트의 표본과 발음이 유사함",
                "confidence": .96,
            }],
            "break_after": ["s00001"],
        }, ensure_ascii=False)}}
        untouched = Mock()
        untouched.raise_for_status.return_value = None
        untouched.json.return_value = {"message": {"content": json.dumps({
            "edits": [],
            "break_after": [],
        })}}
        with patch("web.backend.engine.ollama_ready", return_value=(True, True)), patch(
            "web.backend.engine.requests.post", side_effect=[truncated, corrected, untouched]
        ) as post:
            value, total, fallback, suggestions = Transcriber._review(
                "이 표정을 이용합니다. 다음 설명입니다.", options, None
            )
        self.assertEqual(post.call_count, 3)
        self.assertEqual((total, fallback), (1, 0))
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(value, "이 표정을 이용합니다.\n\n다음 설명입니다.")

    def test_partial_llm_failure_is_saved_as_visible_warning(self) -> None:
        class PartialReviewTranscriber(FakeTranscriber):
            def transcribe(self, source, options, incoming=None, progress=None,
                           cancellation=None) -> Result:
                self.calls.append(options)
                return Result(
                    "Whisper 원문", llm_chunks=2, fallback_chunks=1,
                    segments=(TranscriptSegment(1, 0.0, 2.0, "Whisper 원문"),),
                    duration_seconds=2.0,
                )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, _ = self.environment(root)
            store = LectureStore(root / "web/data/lecorder.db", environment)
            state = JobState()
            engine = PartialReviewTranscriber()
            pipeline = RecordingPipeline(store, state, engine, root / "work", start_worker=False)
            recording = pipeline.create_upload(store.get(), "부분 검수", ".wav", io.BytesIO(b"audio"))
            pipeline.process_next()
            completed = store.get_recording(recording.id)
            self.assertEqual(completed.status, "completed")
            self.assertIn("Qwen3 검수 1/2구간 실패", completed.error)
            self.assertEqual(state.get()["phase"], "warning")

    def test_whisper_verbose_json_preserves_segment_timestamps(self) -> None:
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "text": "첫 문장. 둘째 문장.", "duration": 9.25,
            "segments": [
                {"start": 0.0, "end": 4.0, "text": "첫 문장.", "avg_logprob": -.42,
                 "no_speech_prob": .01},
                {"start": 4.0, "end": 9.25, "text": "둘째 문장.", "avg_logprob": -.71,
                 "no_speech_prob": .02},
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            wav = Path(temporary) / "audio.wav"
            wav.write_bytes(b"wav")
            with patch("web.backend.engine.requests.post", return_value=response) as post:
                result = Transcriber._whisper(wav, Options(language="ko"), None)
        self.assertEqual(result.duration_seconds, 9.25)
        self.assertEqual(result.segments[1].start, 4.0)
        self.assertEqual(result.segments[1].avg_logprob, -.71)
        self.assertEqual(post.call_args.kwargs["data"]["response_format"], "verbose_json")
        self.assertEqual(post.call_args.kwargs["data"]["entropy_thold"], "2.8")
        self.assertEqual(post.call_args.kwargs["data"]["no_context"], "false")
        self.assertEqual(post.call_args.kwargs["data"]["vad"], "true")
        self.assertEqual(post.call_args.kwargs["data"]["vad_min_silence_duration_ms"], "300")
        with tempfile.TemporaryDirectory() as temporary:
            wav = Path(temporary) / "audio.wav"
            wav.write_bytes(b"wav")
            with patch("web.backend.engine.requests.post", return_value=response) as post:
                Transcriber._whisper(wav, Options(language="en"), None)
        self.assertEqual(post.call_args.kwargs["data"]["no_context"], "false")

    def test_vad_result_with_implausible_early_cutoff_falls_back_safely(self) -> None:
        dropped = Mock()
        dropped.raise_for_status.return_value = None
        dropped.json.return_value = {
            "text": "앞부분은 충분히 길게 전사됐지만 후반부가 누락되었습니다. " * 20,
            "duration": 180,
            "segments": [{"start": 0, "end": 60, "text": "앞부분 전사", "avg_logprob": -.4}],
        }
        complete = Mock()
        complete.raise_for_status.return_value = None
        complete.json.return_value = {
            "text": "전체 강의 내용 " * 50, "duration": 180,
            "segments": [{"start": 0, "end": 178, "text": "전체 강의 내용", "avg_logprob": -.5}],
        }
        progress = []
        with tempfile.TemporaryDirectory() as temporary:
            wav = Path(temporary) / "audio.wav"
            wav.write_bytes(b"wav")
            with patch("web.backend.engine.requests.post", side_effect=[dropped, complete]) as post:
                result = Transcriber._whisper(
                    wav, Options(language="ko"), None,
                    lambda stage, index, total: progress.append((stage, index, total)),
                )
        self.assertTrue(result.vad_fallback)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args_list[1].kwargs["data"]["vad"], "false")
        self.assertEqual(progress, [("vad_fallback", 1, 1)])

    def test_primary_repetition_collapse_uses_unbiased_full_audio_fallback(self) -> None:
        repeated_segments = [
            {
                "start": index * 2, "end": index * 2 + 2,
                "text": (
                    "And then I'm going to ask the PAs to come in and do a little bit of a test."
                    if index % 2 else "Yeah."
                ),
                "avg_logprob": -.01,
            }
            for index in range(60)
        ]
        collapsed = Mock()
        collapsed.raise_for_status.return_value = None
        collapsed.json.return_value = {
            "text": " ".join(item["text"] for item in repeated_segments),
            "duration": 120,
            "segments": repeated_segments,
        }
        recovered = Mock()
        recovered.raise_for_status.return_value = None
        recovered.json.return_value = {
            "text": "Now we return to the diagram and calculate the electric field.",
            "duration": 120,
            "segments": [{
                "start": 0, "end": 118,
                "text": "Now we return to the diagram and calculate the electric field.",
                "avg_logprob": -.3,
            }],
        }
        progress = []
        with tempfile.TemporaryDirectory() as temporary:
            wav = Path(temporary) / "audio.wav"
            wav.write_bytes(b"wav")
            with patch(
                "web.backend.engine.requests.post", side_effect=[collapsed, recovered]
            ) as post:
                result = Transcriber._whisper(
                    wav, Options(language="en", prompt="electric field"), None,
                    lambda stage, index, total: progress.append((stage, index, total)),
                )

        self.assertTrue(result.repetition_detected)
        self.assertGreater(result.repetition_ratio, .9)
        self.assertFalse(result.vad_fallback)
        self.assertEqual(result.text, recovered.json.return_value["text"])
        self.assertEqual(progress, [("repetition_fallback", 1, 1)])
        fallback_data = post.call_args_list[1].kwargs["data"]
        self.assertEqual(fallback_data["vad"], "true")
        self.assertEqual(fallback_data["temperature_inc"], "0.0")
        self.assertEqual(fallback_data["beam_size"], "-1")
        self.assertEqual(fallback_data["no_context"], "false")
        self.assertEqual(fallback_data["suppress_nst"], "false")
        self.assertNotIn("prompt", fallback_data)

    def test_repetition_collapse_never_saves_a_second_collapsed_result(self) -> None:
        segments = [
            {"start": index, "end": index + 1, "text": "The same long hallucinated sentence.",
             "avg_logprob": 0.0}
            for index in range(40)
        ]
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "text": " ".join(item["text"] for item in segments),
            "duration": 40,
            "segments": segments,
        }
        with tempfile.TemporaryDirectory() as temporary:
            wav = Path(temporary) / "audio.wav"
            wav.write_bytes(b"wav")
            with patch("web.backend.engine.requests.post", return_value=response):
                with self.assertRaisesRegex(RuntimeError, "동일 발화를 반복 생성"):
                    Transcriber._whisper(wav, Options(language="en"), None)

    def test_repetition_profile_allows_ordinary_lecture_restatements(self) -> None:
        segments = tuple(
            TranscriptSegment(
                index, index * 2, index * 2 + 2,
                "Yes." if index in {4, 11, 19, 28} else f"Distinct lecture point number {index}.",
                -.3,
            )
            for index in range(40)
        )
        collapsed, ratio = Transcriber._repetition_profile(segments)
        self.assertFalse(collapsed)
        self.assertLess(ratio, .2)

    def test_low_confidence_groups_are_bounded_and_adjacent(self) -> None:
        items = (
            TranscriptSegment(1, 0, 3, "good", -.2),
            TranscriptSegment(2, 3, 7, "weak one", -.8),
            TranscriptSegment(3, 7, 11, "weak two", -.9),
            TranscriptSegment(4, 11, 15, "good", -.3),
            TranscriptSegment(5, 15, 50, "long weak", -.8),
        )
        self.assertEqual(Transcriber._low_confidence_groups(items, -.65), [(1, 2), (4, 4)])

    def test_low_confidence_retry_replaces_only_a_better_candidate(self) -> None:
        original = WhisperResult(
            "wrong term", 8.0,
            (TranscriptSegment(1, 2.0, 6.0, "wrong term", -.9, .01),),
        )
        better = WhisperResult(
            "correct term", 6.5,
            (TranscriptSegment(1, 1.25, 5.25, "correct term", -.3, .01),),
        )
        options = Options(language="en", prompt="correct term")
        progress = []
        with patch("web.backend.engine.subprocess.run"), patch.object(
            Transcriber, "_whisper", return_value=better
        ):
            result = Transcriber._retry_low_confidence(
                Path("/tmp/audio.wav"), options, original, None,
                lambda stage, index, total: progress.append((stage, index, total)),
            )
        self.assertEqual(result.text, "correct term")
        self.assertTrue(result.segments[0].retried)
        self.assertEqual(result.low_confidence_segments, 1)
        self.assertEqual(result.retry_attempted_groups, 1)
        self.assertEqual(result.retry_accepted_groups, 1)
        self.assertEqual(progress, [("retry", 1, 1)])

    def test_low_confidence_retry_limit_can_disable_extra_work(self) -> None:
        original = WhisperResult(
            "weak", 4.0, (TranscriptSegment(1, 0.0, 4.0, "weak", -.9, .01),)
        )
        with patch.dict("os.environ", {
            "WHISPER_RETRY_MAX_GROUPS": "0", "WHISPER_LOW_CONFIDENCE": "not-a-number",
        }), patch.object(
            Transcriber, "_whisper"
        ) as whisper:
            result = Transcriber._retry_low_confidence(
                Path("/tmp/audio.wav"), Options(language="en"), original, None
            )
        whisper.assert_not_called()
        self.assertEqual(result.low_confidence_segments, 1)
        self.assertEqual(result.retry_attempted_groups, 0)

    def test_hint_hits_count_distinct_course_terms(self) -> None:
        self.assertEqual(
            Transcriber._hint_hits("표본에서 모집단을 추론한다", "통계학, 모집단, 표본, 표본"),
            2,
        )

    def test_output_names_never_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            files = OutputFiles(output)
            (output / "Lecture.webm").write_bytes(b"existing")
            audio, note = files.pair("Lecture", ".webm")
            self.assertEqual(audio.name, "Lecture (2).webm")
            self.assertEqual(note.name, "Lecture (2).md")

    def test_cancellation_token_terminates_active_process(self) -> None:
        token = CancellationToken()
        outcome: list[str] = []

        def run() -> None:
            try:
                token.run([sys.executable, "-c", "import time; time.sleep(30)"])
            except TranscriptionCancelled:
                outcome.append("cancelled")

        worker = threading.Thread(target=run)
        worker.start()
        time.sleep(.05)
        token.cancel()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(outcome, ["cancelled"])

    def test_active_whisper_and_qwen_requests_use_cancellable_client_processes(self) -> None:
        token = CancellationToken()
        whisper_payload = {
            "text": "전사 내용", "duration": 2,
            "segments": [{"start": 0, "end": 2, "text": "전사 내용"}],
        }
        with patch("web.backend.engine.shutil.which", return_value="/usr/bin/curl"), patch.object(
            token, "run", return_value=subprocess.CompletedProcess(
                [], 0, json.dumps(whisper_payload).encode(), b""
            )
        ) as run:
            with tempfile.TemporaryDirectory() as temporary:
                wav = Path(temporary) / "audio.wav"
                wav.write_bytes(b"wav")
                result = Transcriber._whisper(
                    wav, Options(language="ko"), None, cancellation=token
                )
        self.assertEqual(result.text, "전사 내용")
        self.assertIn("--form", run.call_args.args[0])

        qwen_body = {"message": {"content": json.dumps({"edits": []})}}
        with patch("web.backend.engine.ollama_ready", return_value=(True, True)), patch(
            "web.backend.engine.shutil.which", return_value="/usr/bin/curl"
        ), patch.object(
            token, "run", return_value=subprocess.CompletedProcess(
                [], 0, json.dumps(qwen_body).encode(), b""
            )
        ) as run:
            total, fallback, suggestions, _, stats = Transcriber._review_items(
                ["전사 내용"], Options(language="ko", format_text=False), None, None, token
            )
        self.assertEqual((total, fallback, suggestions, stats.requests), (1, 0, [], 1))
        self.assertIn("--data-binary", run.call_args.args[0])
        self.assertIsNotNone(run.call_args.kwargs["input_data"])

    def test_queued_transcription_can_be_cancelled_and_keeps_title_matched_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, output = self.environment(root)
            store = LectureStore(root / "web/data/lecorder.db", environment)
            state = JobState()
            pipeline = RecordingPipeline(
                store, state, FakeTranscriber(), root / "work", start_worker=False
            )
            app = create_app(store, state, environment=environment, pipeline=pipeline)
            client = app.test_client()
            recording = pipeline.create_upload(
                store.get(), "통계학-9월9일-수", ".wav", io.BytesIO(b"original audio")
            )

            response = client.post(f"/api/recordings/{recording.id}/cancel")
            self.assertEqual(response.status_code, 200)
            cancelled = store.get_recording(recording.id)
            self.assertEqual(cancelled.status, "cancelled")
            self.assertEqual(cancelled.error, "사용자가 전사 작업을 중단했습니다.")
            self.assertEqual(cancelled.source_path, "")
            self.assertEqual(cancelled.note_path, "")
            self.assertEqual(
                json.loads(cancelled.quality_json)["cancelled_stage"], "queued"
            )
            self.assertEqual(
                (output / "통계학-9월9일-수.wav").read_bytes(), b"original audio"
            )
            self.assertFalse((output / "통계학-9월9일-수.md").exists())
            self.assertFalse(pipeline.folder(recording.id).exists())
            pipeline.delete_history(recording.id)
            with self.assertRaises(KeyError):
                store.get_recording(recording.id)
            pipeline.process_next()
            self.assertTrue((output / "통계학-9월9일-수.wav").exists())

    def test_processing_transcription_can_be_cancelled_and_cleans_work_files(self) -> None:
        class BlockingTranscriber(FakeTranscriber):
            def __init__(self) -> None:
                super().__init__()
                self.started = threading.Event()

            def transcribe(self, source, options, incoming=None, progress=None,
                           cancellation=None) -> Result:
                self.started.set()
                while cancellation and not cancellation.cancelled:
                    time.sleep(.005)
                if cancellation:
                    cancellation.check()
                return super().transcribe(
                    source, options, incoming, progress, cancellation
                )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, output = self.environment(root)
            store = LectureStore(root / "web/data/lecorder.db", environment)
            state = JobState()
            engine = BlockingTranscriber()
            pipeline = RecordingPipeline(store, state, engine, root / "work", start_worker=False)
            app = create_app(store, state, engine, environment, pipeline)
            client = app.test_client()
            recording = pipeline.create_upload(
                store.get(), "물리학2-9월9일-수", ".webm", io.BytesIO(b"lecture audio")
            )
            worker = threading.Thread(target=pipeline.process_next)
            worker.start()
            self.assertTrue(engine.started.wait(timeout=1))

            response = client.post(f"/api/recordings/{recording.id}/cancel")
            self.assertEqual(response.status_code, 202)
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
            cancelled = store.get_recording(recording.id)
            self.assertEqual(cancelled.status, "cancelled")
            self.assertGreaterEqual(cancelled.processing_seconds, 0)
            self.assertEqual(json.loads(cancelled.quality_json)["cancelled_stage"], "processing")
            self.assertEqual(
                (output / "물리학2-9월9일-수.webm").read_bytes(), b"lecture audio"
            )
            self.assertEqual(list(output.glob("*.md")), [])
            self.assertFalse(pipeline.folder(recording.id).exists())
            self.assertEqual(state.get()["phase"], "cancelled")

    def test_dashboard_upload_is_the_only_transcription_route(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, output = self.environment(root)
            store = LectureStore(root / "web/data/lecorder.db", environment)
            store.update_course(store.active_course_id(), name="Physics", language="en", prompt="Coulomb law")
            engine = FakeTranscriber()
            state = JobState()
            pipeline = RecordingPipeline(store, state, engine, root / "work", start_worker=False)
            app = create_app(store, state, engine, environment, pipeline)
            client = app.test_client()

            dashboard = client.get("/")
            self.assertEqual(dashboard.status_code, 200)
            dashboard_html = dashboard.get_data(as_text=True)
            self.assertNotIn('target="_blank"', dashboard_html)
            self.assertIn('href="/guide"', dashboard_html)
            dashboard.close()
            guide = client.get("/guide")
            self.assertEqual(guide.status_code, 200)
            guide.close()
            self.assertEqual(client.post("/api/prepare").status_code, 404)
            self.assertEqual(client.post("/v1/audio/transcriptions").status_code, 404)
            uploaded = client.post(
                "/api/upload",
                data={"title": "Lecture", "file": (io.BytesIO(b"audio"), "source.wav")},
                content_type="multipart/form-data",
            )
            self.assertEqual(uploaded.status_code, 202)
            recording_id = uploaded.get_json()["recording"]["id"]
            self.assertEqual(
                client.delete(f"/api/recordings/{recording_id}/history").status_code, 400
            )
            pipeline.process_next()
            self.assertEqual(engine.calls[-1].course_name, "Physics")
            self.assertEqual(engine.calls[-1].prompt, "Coulomb law")
            self.assertTrue((output / "Lecture.wav").exists())
            self.assertTrue((output / "Lecture.md").exists())
            self.assertTrue(
                'type: lecture-transcript' in (output / "Lecture.md").read_text(encoding="utf-8")
            )
            note = (output / "Lecture.md").read_text(encoding="utf-8")
            self.assertIn("[00:12](Lecture.wav#t=12) 정리된 전사문", note)
            self.assertNotIn("# Lecture\n", note)
            self.assertFalse((root / "work" / recording_id).exists())
            details = json.loads(store.get_recording(recording_id).quality_json)
            self.assertEqual(details["pipeline_version"], 1)
            self.assertEqual(details["segment_count"], 1)
            self.assertEqual(details["conversion_seconds"], .1)
            self.assertEqual(details["whisper_primary_seconds"], .2)
            self.assertEqual(details["qwen_seconds"], .3)
            self.assertEqual(details["settings"]["entropy_thold"], "2.8")
            self.assertEqual(details["qwen_requests"], 2)
            self.assertEqual(details["qwen_prompt_tokens"], 120)
            self.assertEqual(details["qwen_output_tokens"], 24)
            self.assertIn("saving_seconds", details)
            self.assertIn("total_seconds", details)
            removed = client.delete(f"/api/recordings/{recording_id}/history")
            self.assertEqual(removed.status_code, 200)
            self.assertEqual(
                client.delete(f"/api/recordings/{recording_id}/history").status_code, 404
            )
            with self.assertRaises(KeyError):
                store.get_recording(recording_id)
            self.assertTrue((output / "Lecture.wav").exists())
            self.assertTrue((output / "Lecture.md").exists())

    def test_failed_final_database_commit_keeps_audio_but_not_partial_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, output = self.environment(root)
            store = LectureStore(root / "web/data/lecorder.db", environment)
            pipeline = RecordingPipeline(
                store, JobState(), FakeTranscriber(), root / "work", start_worker=False
            )
            recording = pipeline.create_upload(
                store.get(), "원자적 저장", ".wav", io.BytesIO(b"audio")
            )
            update = store.update_recording

            def fail_final_update(recording_id: str, **changes):
                if changes.get("status") == "completed":
                    raise RuntimeError("database commit failed")
                return update(recording_id, **changes)

            with patch.object(store, "update_recording", side_effect=fail_final_update):
                pipeline.process_next()

            failed = store.get_recording(recording.id)
            self.assertEqual(failed.status, "failed")
            self.assertEqual(Path(failed.audio_path), (output / "원자적 저장.wav").resolve())
            self.assertEqual((output / "원자적 저장.wav").read_bytes(), b"audio")
            self.assertFalse((output / "원자적 저장.md").exists())

    def test_failed_attempt_log_survives_retry_and_later_cancellation(self) -> None:
        class FailingTranscriber(FakeTranscriber):
            def transcribe(self, source, options, incoming=None, progress=None,
                           cancellation=None) -> Result:
                raise RuntimeError("반복 붕괴 안전 재전사도 실패했습니다.")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, output = self.environment(root)
            store = LectureStore(root / "web/data/lecorder.db", environment)
            state = JobState()
            pipeline = RecordingPipeline(
                store, state, FailingTranscriber(), root / "work", start_worker=False
            )
            recording = pipeline.create_upload(
                store.get(), "실패 로그", ".wav", io.BytesIO(b"original audio")
            )

            pipeline.process_next()
            failed = store.get_recording(recording.id)
            self.assertEqual(failed.status, "failed")
            self.assertEqual(failed.error, "반복 붕괴 안전 재전사도 실패했습니다.")
            failed_details = json.loads(failed.quality_json)
            self.assertEqual(failed_details["pipeline_version"], 1)
            self.assertTrue(failed_details["failed"])
            self.assertEqual(len(failed_details["attempts"]), 1)
            self.assertEqual(
                failed_details["attempts"][0]["error"],
                "반복 붕괴 안전 재전사도 실패했습니다.",
            )
            self.assertTrue(failed_details["audio_preserved"])
            self.assertEqual(Path(failed.audio_path).read_bytes(), b"original audio")
            self.assertTrue(Path(failed.source_path).is_file())
            self.assertTrue(failed.completed_at)

            retried = pipeline.retry(recording.id)
            self.assertEqual(retried.status, "queued")
            self.assertEqual(retried.error, "")
            self.assertEqual(len(json.loads(retried.quality_json)["attempts"]), 1)

            pipeline.cancel(recording.id)
            cancelled = store.get_recording(recording.id)
            self.assertEqual(cancelled.status, "cancelled")
            self.assertEqual(len(json.loads(cancelled.quality_json)["attempts"]), 1)
            self.assertEqual((output / "실패 로그.wav").read_bytes(), b"original audio")

    def test_markdown_keeps_korean_audio_links_readable(self) -> None:
        recording = Recording(
            id="unicode-link",
            course_id=1,
            course_name="통계학",
            title="통계학-9월9일-수",
            source_kind="upload",
            language="ko",
            prompt="",
            corrections="",
            format_transcript=True,
            llm_enabled=False,
            llm_model="",
            output_dir="/tmp",
            extension=".webm",
            status="completed",
            created_at="2026-09-08T20:18:05+00:00",
            audio_path="/tmp/통계학-9월9일-수.webm",
            duration_seconds=8.54,
        )
        note = render_note(
            recording,
            [{"start": 2.0, "end": 3.0, "text": "표본입니다."}],
            set(),
        )
        self.assertIn("[녹음 듣기](통계학-9월9일-수.webm)", note)
        self.assertIn("[00:02](통계학-9월9일-수.webm#t=2)", note)
        self.assertNotIn("%ED", note)
        self.assertNotIn("# 통계학-9월9일-수", note)

        spaced = Recording(**{**recording.__dict__, "audio_path": "/tmp/통계학 강의 (2).webm"})
        spaced_note = render_note(spaced, [], set())
        self.assertIn("(통계학%20강의%20%282%29.webm)", spaced_note)

    def test_markdown_adds_one_timestamp_per_paragraph(self) -> None:
        recording = Recording(
            id="paragraph-links", course_id=1, course_name="통계학", title="강의",
            source_kind="upload", language="ko", prompt="", corrections="",
            format_transcript=True, llm_enabled=False, llm_model="", output_dir="/tmp",
            extension=".wav", status="completed", audio_path="/tmp/Lecture.wav",
        )
        note = render_note(recording, [
            {"start": 2.0, "text": "첫 문장입니다."},
            {"start": 6.0, "text": "둘째 문장입니다."},
            {"start": 10.0, "text": "다음 문단입니다."},
        ], {2})
        self.assertIn("[00:02](Lecture.wav#t=2) 첫 문장입니다. 둘째 문장입니다.", note)
        self.assertIn("[00:10](Lecture.wav#t=10) 다음 문단입니다.", note)
        self.assertEqual(note.count("#t="), 2)

    def test_storage_endpoint_reads_output_folder_live_without_database_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, output = self.environment(root)
            store = LectureStore(root / "web/data/lecorder.db", environment)
            pipeline = RecordingPipeline(
                store, JobState(), FakeTranscriber(), root / "work", start_worker=False
            )
            app = create_app(store, JobState(), FakeTranscriber(), environment, pipeline)
            client = app.test_client()
            (output / "강의.md").write_text("transcript", encoding="utf-8")
            (output / "강의.wav").write_bytes(b"audio")
            (output / "ignore.txt").write_text("ignore", encoding="utf-8")
            (output / ".강의.playback-0123456789abcdef.webm").write_bytes(b"temporary")
            (output / ".강의.webm.playback-0123456789abcdef.tmp").write_bytes(b"temporary")

            first = client.get("/api/storage").get_json()
            self.assertEqual({item["name"] for item in first["files"]}, {"강의.md", "강의.wav"})
            self.assertEqual(len(first["items"]), 1)
            self.assertEqual(first["items"][0]["name"], "강의")
            self.assertEqual(first["items"][0]["kind"], "pair")
            self.assertEqual(first["paired_count"], 1)
            (output / "새 녹음.webm").write_bytes(b"new")
            (output / "AIFF 수업.aiff").write_bytes(b"aiff")
            second = client.get("/api/storage").get_json()
            self.assertIn("새 녹음.webm", {item["name"] for item in second["files"]})
            unpaired = {(item["name"], item["kind"]) for item in second["items"]}
            self.assertIn(("새 녹음", "audio"), unpaired)
            self.assertIn(("AIFF 수업", "audio"), unpaired)
            served = client.get("/api/storage/file?name=강의.md")
            self.assertEqual(served.status_code, 200)
            self.assertEqual(served.get_data(as_text=True), "transcript")
            served.close()
            audio_range = client.get(
                "/api/storage/file?name=강의.wav", headers={"Range": "bytes=1-3"}
            )
            self.assertEqual(audio_range.status_code, 206)
            self.assertEqual(audio_range.get_data(), b"udi")
            self.assertEqual(audio_range.headers["Content-Range"], "bytes 1-3/5")
            audio_range.close()
            prepared_wav = client.post(
                "/api/storage/audio/prepare", json={"name": "강의.wav"}
            )
            self.assertEqual(prepared_wav.status_code, 200)
            self.assertFalse(prepared_wav.get_json()["prepared"])

            needs_index = output / "인덱스 없음.webm"
            needs_index.write_bytes(b"original")
            probe_results = iter(["", "12.5\n"])

            def fake_media_command(command, **_kwargs):
                if "ffprobe" in command[0]:
                    return subprocess.CompletedProcess(command, 0, next(probe_results), "")
                Path(command[-1]).write_bytes(b"optimized")
                return subprocess.CompletedProcess(command, 0, b"", b"")

            with patch(
                "web.backend.output_files.shutil.which", side_effect=lambda name: f"/usr/bin/{name}"
            ), patch("web.backend.output_files.subprocess.run", side_effect=fake_media_command):
                prepared_webm = client.post(
                    "/api/storage/audio/prepare", json={"name": needs_index.name}
                )
            self.assertEqual(prepared_webm.status_code, 200)
            self.assertTrue(prepared_webm.get_json()["prepared"])
            self.assertEqual(prepared_webm.get_json()["duration"], 12.5)
            self.assertEqual(needs_index.read_bytes(), b"optimized")

            short_frames = output / "짧은 프레임.webm"
            short_frames.write_bytes(b"short-opus")
            short_probe_results = iter([
                "2.75\n",
                "0.0025,\n0.0025,\n0.0025,\n0.0025,\n",
                "2.78\n",
            ])
            media_commands = []

            def fake_short_frame_command(command, **_kwargs):
                media_commands.append(command)
                if "ffprobe" in command[0]:
                    return subprocess.CompletedProcess(
                        command, 0, next(short_probe_results), "",
                    )
                Path(command[-1]).write_bytes(b"normalized-opus")
                return subprocess.CompletedProcess(command, 0, b"", b"")

            with patch(
                "web.backend.output_files.shutil.which", side_effect=lambda name: f"/usr/bin/{name}"
            ), patch(
                "web.backend.output_files.subprocess.run", side_effect=fake_short_frame_command
            ):
                normalized_webm = client.post(
                    "/api/storage/audio/prepare", json={"name": short_frames.name}
                )
            self.assertEqual(normalized_webm.status_code, 200)
            self.assertTrue(normalized_webm.get_json()["prepared"])
            self.assertEqual(short_frames.read_bytes(), b"normalized-opus")
            encode_command = next(command for command in media_commands if "ffmpeg" in command[0])
            self.assertIn("libopus", encode_command)
            self.assertEqual(
                encode_command[encode_command.index("-frame_duration") + 1], "20"
            )
            self.assertEqual(encode_command[encode_command.index("-f") + 1], "webm")
            self.assertTrue(str(encode_command[-1]).endswith(".tmp"))
            self.assertEqual(client.get("/api/storage/file?name=.강의.playback-0123456789abcdef.webm").status_code, 400)
            self.assertEqual(client.get("/api/storage/file?name=../강의.md").status_code, 400)
            self.assertEqual(client.delete("/api/storage/file?name=../강의.md").status_code, 400)
            wrong_method = client.post("/api/storage/file")
            self.assertEqual(wrong_method.status_code, 405)
            self.assertEqual(wrong_method.get_json()["error"], "현재 실행 중인 앱이 이 요청 방식을 지원하지 않습니다.")
            with patch("web.backend.routes.subprocess.Popen") as opened:
                revealed = client.post("/api/storage/reveal", json={"name": "강의.wav"})
            self.assertEqual(revealed.status_code, 200)
            self.assertEqual(opened.call_args.args[0][:2], ["open", "-R"])

            deleted = client.delete("/api/storage/file?name=강의.md")
            self.assertEqual(deleted.status_code, 200)
            self.assertFalse((output / "강의.md").exists())
            self.assertTrue((output / "강의.wav").exists())
            after_markdown_delete = client.get("/api/storage").get_json()
            lecture = next(item for item in after_markdown_delete["items"] if item["name"] == "강의")
            self.assertEqual(lecture["kind"], "audio")
            self.assertEqual(lecture["markdown_files"], [])
            self.assertEqual(client.delete("/api/storage/file?name=강의.md").status_code, 404)

            deleted = client.delete("/api/storage/file?name=강의.wav")
            self.assertEqual(deleted.status_code, 200)
            self.assertFalse((output / "강의.wav").exists())

            with sqlite3.connect(store.path) as db:
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertNotIn("storage", tables)
            self.assertNotIn("files", tables)

    def test_recording_title_and_file_deletion_menu_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, output = self.environment(root)
            store = LectureStore(root / "web/data/lecorder.db", environment)
            pipeline = RecordingPipeline(
                store, JobState(), FakeTranscriber(), root / "work", start_worker=False
            )
            app = create_app(store, JobState(), FakeTranscriber(), environment, pipeline)
            client = app.test_client()
            audio = output / "이전 제목.wav"
            note = output / "이전 제목.md"
            audio.write_bytes(b"audio")
            note.write_text(
                'audio: "이전 제목.wav"\n[00:01](이전%20제목.wav#t=1) note',
                encoding="utf-8",
            )
            recording = store.create_recording(
                "menu-actions", store.get(), "이전 제목", "upload", ".wav", status="completed"
            )
            store.update_recording(
                recording.id, audio_path=str(audio), note_path=str(note), completed_at=store.now()
            )

            occupied = output / "사용 중.wav"
            occupied.write_bytes(b"occupied")
            collision = client.patch(
                f"/api/recordings/{recording.id}", json={"title": "사용 중"}
            )
            self.assertEqual(collision.status_code, 400)
            self.assertTrue(audio.exists())
            self.assertTrue(note.exists())
            self.assertEqual(store.get_recording(recording.id).title, "이전 제목")

            renamed = client.patch(
                f"/api/recordings/{recording.id}", json={"title": "새 제목"}
            )
            self.assertEqual(renamed.status_code, 200)
            self.assertEqual(renamed.get_json()["recording"]["title"], "새 제목")
            self.assertEqual(set(renamed.get_json()["renamed_files"]), {"새 제목.wav", "새 제목.md"})
            saved = store.get_recording(recording.id)
            self.assertEqual(saved.title, "새 제목")
            self.assertEqual(Path(saved.audio_path).name, "새 제목.wav")
            self.assertEqual(Path(saved.note_path).name, "새 제목.md")
            audio = output / "새 제목.wav"
            note = output / "새 제목.md"
            self.assertTrue(audio.exists())
            self.assertTrue(note.exists())
            self.assertFalse((output / "이전 제목.wav").exists())
            self.assertFalse((output / "이전 제목.md").exists())
            renamed_note = note.read_text(encoding="utf-8")
            self.assertIn('audio: "새 제목.wav"', renamed_note)
            self.assertIn("새%20제목.wav#t=1", renamed_note)

            deleted = client.delete(f"/api/recordings/{recording.id}/with-files")
            self.assertEqual(deleted.status_code, 200)
            self.assertEqual(set(deleted.get_json()["deleted_files"]), {audio.name, note.name})
            self.assertFalse(audio.exists())
            self.assertFalse(note.exists())
            with self.assertRaises(KeyError):
                store.get_recording(recording.id)

            orphan_audio = output / "파일만.wav"
            orphan_note = output / "파일만.md"
            orphan_audio.write_bytes(b"audio")
            orphan_note.write_text("[00:01](파일만.wav#t=1) note", encoding="utf-8")
            renamed_files = client.patch(
                "/api/storage/files",
                json={"names": [orphan_audio.name, orphan_note.name], "title": "바뀐 파일"},
            )
            self.assertEqual(renamed_files.status_code, 200)
            self.assertEqual(
                set(renamed_files.get_json()["files"]), {"바뀐 파일.wav", "바뀐 파일.md"}
            )
            orphan_audio = output / "바뀐 파일.wav"
            orphan_note = output / "바뀐 파일.md"
            self.assertIn("바뀐%20파일.wav#t=1", orphan_note.read_text(encoding="utf-8"))
            batch = client.delete(
                "/api/storage/files", json={"names": [orphan_audio.name, orphan_note.name]}
            )
            self.assertEqual(batch.status_code, 200)
            self.assertFalse(orphan_audio.exists())
            self.assertFalse(orphan_note.exists())

    def test_chunked_recording_queue_cleans_temporary_files_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, output = self.environment(root)
            store = LectureStore(root / "web/data/lecorder.db", environment)
            engine = FakeTranscriber()
            pipeline = RecordingPipeline(store, JobState(), engine, root / "work", start_worker=False)
            recording = pipeline.begin_chunked(store.get(), "긴 강의", ".webm")
            pipeline.save_chunk(recording.id, 0, io.BytesIO(b"first"))
            pipeline.save_chunk(recording.id, 1, io.BytesIO(b"second"))
            pipeline.finalize_chunked(recording.id, 18.0)
            pipeline.process_next()
            completed = store.get_recording(recording.id)
            self.assertEqual(completed.status, "completed")
            self.assertEqual((output / "긴 강의.webm").read_bytes(), b"firstsecond")
            self.assertFalse(pipeline.folder(recording.id).exists())

    def test_recoverable_recording_can_be_deleted_with_its_saved_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, _ = self.environment(root)
            store = LectureStore(root / "web/data/lecorder.db", environment)
            pipeline = RecordingPipeline(
                store, JobState(), FakeTranscriber(), root / "work", start_worker=False
            )
            recording = pipeline.begin_chunked(store.get(), "중단된 녹음", ".webm")
            pipeline.save_chunk(recording.id, 0, io.BytesIO(b"recoverable audio"))
            store.update_recording(
                recording.id,
                status="recoverable",
                error="앱이 종료되어 작업이 중단되었습니다.",
            )
            app = create_app(store, JobState(), FakeTranscriber(), environment, pipeline)
            client = app.test_client()

            deleted = client.delete(f"/api/recordings/{recording.id}")

            self.assertEqual(deleted.status_code, 200)
            self.assertFalse(pipeline.folder(recording.id).exists())
            with self.assertRaises(KeyError):
                store.get_recording(recording.id)

    def test_pipeline_startup_removes_only_untracked_work_folders(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, _ = self.environment(root)
            store = LectureStore(root / "web/data/lecorder.db", environment)
            work = root / "work"
            orphan = work / "orphan"
            orphan.mkdir(parents=True)
            (orphan / "source.wav").write_bytes(b"temporary")
            recording = store.create_recording(
                "persisted", store.get(), "복구 대상", "browser", ".webm",
                str(work / "persisted/source.webm"), "recording",
            )
            tracked = work / recording.id
            tracked.mkdir()
            (tracked / "source.webm").write_bytes(b"keep")

            RecordingPipeline(store, JobState(), FakeTranscriber(), work, start_worker=False)

            self.assertFalse(orphan.exists())
            self.assertTrue((tracked / "source.webm").exists())

    def test_interrupted_recording_recovers_contiguous_saved_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, output = self.environment(root)
            store = LectureStore(root / "web/data/lecorder.db", environment)
            pipeline = RecordingPipeline(store, JobState(), FakeTranscriber(), root / "work", start_worker=False)
            recording = pipeline.begin_chunked(store.get(), "복구", ".webm")
            pipeline.save_chunk(recording.id, 0, io.BytesIO(b"saved"))
            pipeline.save_chunk(recording.id, 2, io.BytesIO(b"after-gap"))
            pipeline.retry(recording.id)
            pipeline.process_next()
            self.assertEqual((output / "복구.webm").read_bytes(), b"saved")
            self.assertFalse(pipeline.folder(recording.id).exists())

    def test_accepting_suggestion_updates_note_and_course_corrections(self) -> None:
        class SuggestedTranscriber(FakeTranscriber):
            def transcribe(self, source, options, incoming=None, progress=None,
                           cancellation=None) -> Result:
                self.calls.append(options)
                return Result(
                    "이 표정을 사용합니다.",
                    suggestions=(Suggestion("s00001", "표정을", "표본을", "통계 문맥", .96),),
                    segments=(TranscriptSegment(1, 3.0, 8.0, "이 표정을 사용합니다."),),
                    duration_seconds=8.0,
                )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, output = self.environment(root)
            store = LectureStore(root / "web/data/lecorder.db", environment)
            store.update_course(store.active_course_id(), name="통계학")
            engine = SuggestedTranscriber()
            pipeline = RecordingPipeline(store, JobState(), engine, root / "work", start_worker=False)
            recording = pipeline.create_upload(store.get(), "검토", ".wav", io.BytesIO(b"audio"))
            pipeline.process_next()
            suggestion = store.list_suggestions(recording.id)[0]
            self.assertEqual(suggestion.status, "pending")
            app = create_app(store, JobState(), engine, environment, pipeline)
            client = app.test_client()
            review = client.get(f"/api/recordings/{recording.id}").get_json()
            self.assertEqual(review["suggestions"][0]["start"], 3.0)
            self.assertEqual(review["suggestions"][0]["end"], 8.0)
            self.assertEqual(review["suggestions"][0]["audio_name"], "검토.wav")
            audio_response = client.get(f"/api/recordings/{recording.id}/audio")
            self.assertEqual(audio_response.get_data(), b"audio")
            audio_response.close()
            pipeline.decide_suggestion(suggestion.id, "accept")
            self.assertIn("표정을=표본을", store.get_course(store.active_course_id()).corrections)
            self.assertIn("표본을 사용합니다", (output / "검토.md").read_text(encoding="utf-8"))
            self.assertEqual(store.get_suggestion(suggestion.id).status, "accepted")


if __name__ == "__main__":
    unittest.main()
