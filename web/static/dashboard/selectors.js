const phaseLabels = {idle: "대기 중", queued: "대기열 등록", processing: "Whisper 전사 중", refining: "Whisper 정밀 확인 중", polishing: "LLM 검수 중", saving: "파일 정리 중", cancelling: "중단 처리 중", cancelled: "중단", complete: "완료", warning: "확인 필요", error: "오류"};

const recordingStatusLabels = {recording: "녹음 중", recoverable: "복구 가능", queued: "대기 중", processing: "처리 중", cancelling: "중단 중", cancelled: "중단", completed: "완료", failed: "실패"};

const libraryNameCollator = new Intl.Collator("ko", {numeric: true, sensitivity: "base"});


export function recordingWorkflowStatus(recording) {
  if (recording.status === "completed") {
    if (["queued", "processing"].includes(recording.review_status)) return recording.review_status;
    if (recording.review_status === "failed") return "failed";
  }
  return recording.status;
}

export function pendingReview(recording) {
  return recording.status === "completed" && ["queued", "processing"].includes(recording.review_status);
}

export function libraryAlertCounts(entries) {
  const byCourse = new Map();
  let total = 0;
  for (const entry of entries) {
    if (!["review", "problem"].includes(entry.category)) continue;
    total += 1;
    const courseId = entry.recording?.course_id || 0;
    byCourse.set(courseId, (byCourse.get(courseId) || 0) + 1);
  }
  return {total, byCourse};
}

export function noteQueueSummary(note) {
  if (note.status !== "queued") return noteQueueProgress(note).message;
  const stage = {extracting:"원문 추출", study:"해설 생성", overview:"개요 생성", glossary:"용어집 생성", detail:"상세 해설"}[note.stage] || "분석 준비";
  return `${stage} 대기`;
}

export function noteQueueProgress(note) {
  if (note.stage === "detail") return {phase:"detail", message:`${note.number ? `${note.number}쪽 ` : ""}상세 해설 생성 중`};
  if (note.stage === "glossary") return {phase:"glossary", message:"용어집 생성 · 의미와 읽는 말 검증 중"};
  if (note.stage === "overview") return {phase:"overview", message:"개요 생성 중"};
  if (!["extracting", "study"].includes(note.stage)) return {phase:"queued", message:"분석 준비 중"};
  const total = Number(note.page_count) || 0;
  const extracting = note.stage === "extracting";
  const done = Number(extracting ? note.extracted_pages : note.analyzed_pages) || 0;
  const phase = note.stage;
  const message = extracting && total > 0 && done >= total ? "원문 추출 마무리 중"
    : extracting ? (total ? `원문 추출 · ${done}/${total}쪽 처리` : "문서 변환·페이지 준비 중")
    : total && done >= total ? "페이지 분석 처리 종료 · 개요 정리 중"
    : `해설 생성 · ${done}/${total}쪽 처리`;
  return {phase, message};
}

export function statusCardActivity(job, notes, recordings = []) {
  const review = recordings.find(item => pendingReview(item) && item.review_status === "processing") || recordings.find(pendingReview);
  if (review && !["processing", "refining", "polishing", "saving", "cancelling"].includes(job.phase)) {
    return {phase:review.review_status === "queued" ? "queued" : "polishing", label:review.review_status === "queued" ? "검수 대기" : "LLM 검수 중", title:review.title, message:"전사 원문은 저장되었습니다. 검수 결과를 기다리고 있습니다."};
  }
  const phase = job.phase || "idle";
  const activeSpeech = ["processing", "refining", "polishing", "saving", "cancelling"].includes(phase);
  const note = activeSpeech ? null : notes.find(item => item.status === "processing")
    || notes.find(item => item.status === "queued");
  if (note) return {
    phase: note.status === "processing" ? "processing" : "queued",
    label: note.status === "processing" ? "강의노트 분석 중" : "강의노트 분석 대기",
    title: note.title,
    message: note.status === "processing" ? noteQueueProgress(note).message : "분석 작업이 시작되기를 기다리고 있습니다.",
  };
  return {phase, label:phaseLabels[phase] || phase, title:job.title || "준비되어 있습니다",
    message:job.message || "강의를 선택하고 작업을 시작하세요."};
}

export function clockText(milliseconds) {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  const hours = String(Math.floor(seconds / 3600)).padStart(2, "0");
  const minutes = String(Math.floor((seconds % 3600) / 60)).padStart(2, "0");
  return `${hours}:${minutes}:${String(seconds % 60).padStart(2, "0")}`;
}

export function formatFileSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function isInternalStorageFile(name) {
  const value = String(name || "");
  return value.startsWith(".") || /\.playback-[a-f0-9]{8,}(?:\.[^.]+)?$/i.test(value);
}

export function visibleStorageItems(items) {
  return (items || []).map((item) => {
    const files = (item.files || []).filter((name) => !isInternalStorageFile(name));
    const audioFiles = (item.audio_files || []).filter((name) => !isInternalStorageFile(name));
    const markdownFiles = (item.markdown_files || []).filter((name) => !isInternalStorageFile(name));
    if (!files.length) return null;
    return {...item, files, audio_files: audioFiles, markdown_files: markdownFiles};
  }).filter(Boolean);
}

export function storageFileUrl(name) {
  return `/api/storage/file?name=${encodeURIComponent(name)}`;
}

export function fileName(path) {
  return String(path || "").split(/[\\/]/).pop() || "";
}

export function storageCoverage(storage) {
  return {
    hasAudio: Boolean(storage?.audio_files?.length),
    hasMarkdown: Boolean(storage?.markdown_files?.length),
  };
}

export function recordingCategory(recording, storage) {
  if (pendingReview(recording)) return "active";
  if (recording.review_status === "failed") return "problem";
  const counts = recording.suggestion_counts || {};
  if (["recording", "recoverable", "queued", "processing", "cancelling"].includes(recording.status)) return "active";
  if (recording.status === "completed" && recording.error) return "problem";
  const coverage = storageCoverage(storage);
  if (recording.status === "completed" && (!coverage.hasAudio || !coverage.hasMarkdown)) return "problem";
  if (recording.status === "completed" && Number(counts.pending) > 0) return "review";
  if (["failed", "cancelled"].includes(recording.status)) return "problem";
  return "completed";
}

export function libraryEntryName(entry) {
  return String(entry.recording?.title || entry.storage?.name || "");
}

export function sortLibraryEntries(entries, order = "name-asc") {
  const direction = order.endsWith("-desc") ? -1 : 1;
  return [...entries].sort((left, right) => {
    if (order.startsWith("date-")) {
      const leftTime = Date.parse(left.recording?.created_at || left.storage?.modified_at || left.timestamp) || 0;
      const rightTime = Date.parse(right.recording?.created_at || right.storage?.modified_at || right.timestamp) || 0;
      const difference = (leftTime - rightTime) * direction;
      if (difference) return difference;
    }
    return libraryNameCollator.compare(libraryEntryName(left), libraryEntryName(right)) * direction;
  });
}

export function libraryRoute(entry) {
  return entry.kind === "job"
    ? `#/recordings/job/${encodeURIComponent(entry.recording.id)}`
    : `#/recordings/file/${encodeURIComponent(entry.storage.name)}`;
}

export function libraryState(entry) {
  if (entry.recording?.waiting_for_ai) return "AI 처리 재개 대기";
  if (entry.recording && pendingReview(entry.recording)) return entry.recording.review_status === "queued" ? "검수 대기" : "검수 중";
  if (entry.recording?.review_status === "failed") return "검수 실패";
  const {hasAudio, hasMarkdown} = storageCoverage(entry.storage);
  if (entry.kind === "file") {
    if (hasAudio && hasMarkdown) return "오디오 + Markdown · 작업 기록 없음";
    if (hasAudio) return "오디오만 존재 · Markdown 없음";
    return "Markdown만 존재 · 오디오 없음";
  }
  if (entry.recording.status === "completed") {
    if (!hasAudio && !hasMarkdown) return "오디오와 Markdown 없음";
    if (!hasAudio) return "오디오 파일 없음";
    if (!hasMarkdown) return "Markdown 파일 없음";
  }
  if (entry.recording.status === "cancelled" && hasAudio) return "중단됨 · 오디오만 보관";
  if (entry.category === "review") return "검토 필요";
  return recordingStatusLabels[entry.recording.status] || entry.recording.status;
}

export function shortDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("ko-KR", {month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit"}).format(date);
}

export function durationText(seconds) {
  if (!seconds) return "시간 미확인";
  return clockText(Number(seconds) * 1000);
}

export function elapsedSince(value) {
  const started = new Date(value).getTime();
  if (Number.isNaN(started)) return 0;
  return Math.max(0, (Date.now() - started) / 1000);
}

export function workTimeText(recording) {
  const startedAt = recording.started_at || recording.created_at;
  if (["processing", "cancelling"].includes(recording.status)) {
    return `작업 시작 ${shortDate(startedAt)} · 경과 ${durationText(elapsedSince(startedAt))}`;
  }
  if (recording.completed_at) {
    const day = new Intl.DateTimeFormat("ko-KR", {year: "numeric", month: "numeric", day: "numeric"}).format(new Date(recording.completed_at));
    return `작업 ${day} · 총 ${durationText(recording.total_processing_seconds || recording.processing_seconds)}`;
  }
  return `등록 ${shortDate(recording.created_at)}`;
}

export function stageTime(seconds) {
  return Number(seconds) > 0 ? durationText(Number(seconds)) : "—";
}

export function hasPipelineLog(recording) {
  return Boolean(recording && (
    recording.error
    || recording.has_live_draft
    || Number(recording.quality?.pipeline_version) >= 1
    || Array.isArray(recording.quality?.attempts)
  ));
}

export function runSeconds(run) {
  const explicit = Number(run.metrics?.total_seconds);
  if (explicit > 0) return explicit;
  const started = new Date(run.started_at).getTime(), completed = new Date(run.completed_at).getTime();
  return Number.isFinite(started) && Number.isFinite(completed) ? Math.max(0, (completed - started) / 1000) : 0;
}

export function legacyStages(recording) {
  const quality = recording.quality || {};
  if (Array.isArray(quality.stages)) return quality.stages;
  return recording.status === "failed" ? [
    ["처리 실패", quality.total_seconds || recording.processing_seconds,
      `${phaseLabels[quality.failed_stage] || quality.failed_stage || "처리 중"} · ${recording.error || "원인 기록 없음"}`],
  ] : quality.cancelled ? [
    ["전사 중단", quality.total_seconds, `${phaseLabels[quality.cancelled_stage] || quality.cancelled_stage || "처리 중"}에 요청`],
    ["파일 정리", 0, "전사 결과·임시 파일 삭제 · 원본 음성만 보존"],
  ] : [
    ["오디오 변환", quality.conversion_seconds, "16 kHz · mono WAV"],
    ["Whisper", quality.whisper_primary_seconds, `VAD 우선 · ${quality.segment_count || 0}개 발화`],
    ["VAD 보호", quality.vad_fallback_seconds, quality.vad_fallback ? "누락 감지 · 전체 음성 재전사" : "실행 안 함"],
    ["반복 보호", quality.repetition_fallback_seconds, quality.repetition_detected
      ? `붕괴 감지 · 반복률 ${(Number(quality.repetition_ratio || 0) * 100).toFixed(1)}% · 안전 재전사`
      : "실행 안 함"],
    ["저신뢰 확인", quality.retry_processing_seconds, `${quality.low_confidence_segments || 0}구간 · ${quality.retry_attempted_groups || 0}묶음 시도 · ${quality.retry_accepted_groups || 0}묶음 교체`],
    ["파일 저장", quality.saving_seconds, `문단 ${quality.paragraph_break_count || 0}개 · 오디오 + Markdown`],
  ];
}

export function playerTime(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const remainder = total % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
}

export function markdownTimestamp(target) {
  const match = String(target || "").match(/#t=(\d+(?:\.\d+)?)/i);
  return match ? Number(match[1]) : null;
}

export function frequencyBandLevel(levels, sampleRate, fftSize, index, count) {
  if (!levels.length || count <= 0) return 0;
  const nyquist = sampleRate / 2;
  const minimum = Math.min(80, nyquist);
  const maximum = Math.min(8000, nyquist);
  const lower = minimum * Math.pow(maximum / minimum, index / count);
  const upper = minimum * Math.pow(maximum / minimum, (index + 1) / count);
  const binWidth = sampleRate / fftSize;
  const start = Math.max(1, Math.floor(lower / binWidth));
  const end = Math.min(levels.length - 1, Math.max(start, Math.ceil(upper / binWidth)));
  let total = 0;
  for (let bin = start; bin <= end; bin += 1) total += levels[bin];
  return total / Math.max(1, end - start + 1) / 255;
}

export function recordingExtension(mimeType) {
  if (mimeType.includes("mp4")) return ".mp4";
  if (mimeType.includes("ogg")) return ".ogg";
  return ".webm";
}

export {phaseLabels, recordingStatusLabels};
