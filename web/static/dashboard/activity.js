import {openNote} from "../notes.js";
import {deleteRecordingRecovery, recordingSessions} from "../recording-store.js";
import {requestJson} from "./api.js";
import {recordingWorkflowStatus, pendingReview, noteQueueProgress, shortDate, stageTime, hasPipelineLog, runSeconds, legacyStages, phaseLabels, recordingStatusLabels} from "./selectors.js";

export function createActivity(context) {
  const {app, ui, $, $$, actions, listen, every} = context;
  async function cancelTranscription(recording, button) {
    const confirmed = await actions.confirmAction(
      `‘${recording.title}’ 전사 작업을 중단할까요? 전사 결과와 임시 작업 파일은 삭제하고, 원본 음성은 현재 제목을 그대로 사용해 저장합니다.`,
      "전사 중단",
    );
    if (!confirmed) return;
    button.disabled = true;
    try {
      const result = await requestJson(
        `/api/recordings/${encodeURIComponent(recording.id)}/cancel`, {method: "POST"},
      );
      renderRecordings([
        result.recording,
        ...app.recordings.filter((item) => item.id !== recording.id),
      ]);
      actions.showToast(
        result.recording.status === "cancelled"
          ? "전사를 중단하고 원본 음성만 저장했습니다."
          : "중단 요청을 보냈습니다.",
        "success",
      );
    } catch (error) {
      button.disabled = false;
      actions.showToast(error.message, "error");
    }
  }

  async function retryRecording(recording, button) {
    button.disabled = true;
    try {
      await actions.flushCourseSave({requireSaved: true});
      let session = null;
      if (["recording", "recoverable"].includes(recording.status)) {
        const sessions = await recordingSessions().catch(() => []);
        session = sessions.find((item) => item.recordingId === recording.id) || null;
      }
      if (session) {
        await actions.restoreRecordingSession(session);
        actions.showToast("브라우저에 보관된 조각까지 전송해 녹음을 복구했습니다.", "success");
      } else {
        const retranscribe = ["completed", "failed", "cancelled"].includes(recording.status);
        const action = retranscribe ? "retranscribe" : "retry";
        if (retranscribe) { await actions.openUploadDialog(recording); return; }
        await requestJson(`/api/recordings/${encodeURIComponent(recording.id)}/${action}`, {method:"POST"});
      }
      await actions.refreshStatus();
    } catch (error) {
      actions.showToast(error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  async function deleteRecordingHistory(recording, button) {
    const interrupted = ["recording", "recoverable"].includes(recording.status);
    const confirmed = await actions.confirmAction(
      interrupted
        ? `‘${recording.title}’ 복구 작업을 삭제할까요? 서버와 이 브라우저에 남아 있는 녹음 조각도 함께 삭제되며 되돌릴 수 없습니다.`
        : `‘${recording.title}’을 보관함에서 삭제할까요? 저장소의 오디오와 Markdown 결과물은 삭제되지 않습니다.`,
      interrupted ? "복구 작업 삭제" : "작업 기록 삭제",
    );
    if (!confirmed) return;
    button.disabled = true;
    try {
      const endpoint = interrupted
        ? `/api/recordings/${encodeURIComponent(recording.id)}`
        : `/api/recordings/${encodeURIComponent(recording.id)}/history`;
      await requestJson(endpoint, {method: "DELETE"});
      if (interrupted) {
        await deleteRecordingRecovery(recording.id).catch(() => {});
        app.recoveryAttempted.delete(recording.id);
        app.recoveryTasks.delete(recording.id);
      }
      if (app.reviewingRecordingId === recording.id) app.reviewingRecordingId = null;
      renderRecordings(app.recordings.filter((item) => item.id !== recording.id));
      app.selectedLibraryKey = null;
      window.location.hash = "#/recordings";
      actions.showToast(interrupted ? "복구 작업과 남은 녹음 조각을 삭제했습니다." : "작업 기록을 삭제했습니다.", "success");
    } catch (error) {
      button.disabled = false;
      actions.showToast(error.message, "error");
    }
  }

  async function openPipelineLog(recording, invoker) {
    if (!hasPipelineLog(recording)) return;
    app.pipelineLogInvoker = invoker || null;
    ui.pipelineDialogSubtitle.textContent = recording.title;
    ui.pipelineDialogBody.replaceChildren(Object.assign(document.createElement("p"), {textContent: "실행 기록을 불러오는 중입니다."}));
    ui.pipelineDialog.showModal();
    try {
      const history = await requestJson(`/api/recordings/${encodeURIComponent(recording.id)}/runs`);
      const pipeline = createPipelineDetail(recording, history);
      pipeline.hidden = false;
      ui.pipelineDialogBody.replaceChildren(pipeline);
    } catch (error) {
      ui.pipelineDialogBody.replaceChildren(Object.assign(document.createElement("p"), {textContent: error.message}));
    }
  }

  function createPipelineDetail(recording, history = {}) {
    const section = document.createElement("section");
    section.className = "pipeline-detail";
    section.hidden = true;
    section.setAttribute("aria-label", `${recording.title} 파이프라인 기록`);
    for(const item of history.recovery || []) {
      section.append(Object.assign(document.createElement('p'),{className:'pipeline-settings',textContent:`복구 · ${new Date(item.created_at).toLocaleString()} · ${item.message}`}));
    }
    const runs = [...(history.runs || []), ...(history.review_runs || [])]
      .sort((a, b) => new Date(a.started_at) - new Date(b.started_at));
    if (!runs.length) runs.push({kind: "transcription", started_at: recording.started_at, completed_at: recording.completed_at, status: recording.status, metrics: {...(recording.quality || {}), stages: legacyStages(recording)}, settings: {llm_model: recording.llm_model}});
    let aggregate = 0;
    for (const run of runs) {
      const isReview = (history.review_runs || []).includes(run);
      const metrics = run.metrics || {};
      const stages = Array.isArray(metrics.stages) ? metrics.stages : (isReview ? [{name: "LLM 검수", seconds: runSeconds(run), detail: `제안 ${metrics.suggestion_count || 0}개`}] : legacyStages(recording).map(([name, seconds, detail]) => ({name, seconds, detail})));
      const runTotal = runSeconds(run) || stages.reduce((sum, stage) => sum + (Number(stage.seconds) || 0), 0);
      aggregate += runTotal;
      const block = document.createElement("section");
      block.className = "pipeline-run";
      const head = document.createElement("header");
      head.append(Object.assign(document.createElement("strong"), {textContent: isReview ? "LLM 검수 실행" : "전사 실행"}), Object.assign(document.createElement("span"), {textContent: `${shortDate(run.started_at)} → ${shortDate(run.completed_at) || "진행 중"}`}));
      block.append(head);
      const list = document.createElement("div");
      list.className = "pipeline-stages";
      for (const stage of stages) {
        const line = document.createElement("div");
        line.className = "pipeline-stage";
        line.append(Object.assign(document.createElement("span"), {textContent: stage.name}), Object.assign(document.createElement("strong"), {textContent: stageTime(stage.seconds)}), Object.assign(document.createElement("small"), {textContent: stage.detail || "완료"}));
        list.append(line);
      }
      if (run.error) list.append(Object.assign(document.createElement("p"), {className: "pipeline-error", textContent: run.error}));
      for (const event of metrics.ai_control_events || []) {
        if (event.status === 'completed') continue;
        const line = document.createElement('div'); line.className = 'pipeline-stage';
        const label = {paused:'AI 요청 일시정지 · 재요청 예정', resumed:'AI 처리 재개', paused_wait:'AI 재개 대기 종료', failed:'AI 요청 종료'}[event.status] || event.status;
        line.append(Object.assign(document.createElement('span'), {textContent:label}), Object.assign(document.createElement('strong'), {textContent:stageTime(event.waiting_seconds ?? event.seconds)}), Object.assign(document.createElement('small'), {textContent:event.waiting_seconds != null ? '대기 시간 · 총 처리 시간 제외' : '취소 전 실제 처리 시간'}));
        list.append(line);
      }
      block.append(list);
      const settings = run.settings || {};
      const ai = settings.ai_settings?.[isReview ? 'llm' : 'speech'];
      const modelInfo = `${ai?.provider || (isReview ? 'LLM' : 'Whisper')} · ${ai?.model || settings.model || settings.llm_model || recording.llm_model || '미확인'}`;
      const usageInfo = metrics.usage_available === false ? '사용량 미제공' : `입력 ${metrics.prompt_tokens ?? metrics.llm_prompt_tokens ?? '미제공'} / 출력 ${metrics.output_tokens ?? metrics.llm_output_tokens ?? '미제공'} 토큰`;
      block.append(Object.assign(document.createElement('p'), {className:'pipeline-settings',textContent: modelInfo + (isReview ? ` · 요청 ${metrics.requests ?? 0}회 · 제안 ${metrics.suggestion_count ?? 0}개 · ${usageInfo}` : '')}));
      section.append(block);
    }
    const events = history.live_events?.length ? history.live_events : (recording.quality?.live_events || []);
    if (events.length) {
      const details = document.createElement('details');
      details.className = 'pipeline-run';
      details.append(Object.assign(document.createElement('summary'), {textContent: '실시간 초안 · large-v3-turbo (최종 처리 시간에서 제외)'}));
      const names = {resource_ready: '자원 대기', model_ready: '모델 로드', segment_started: '구간 시작', segment: '초안 구간', displayed: '화면 반영 지연', gap: '초안 누락', failed: '실시간 실패', stopped: '모델 해제', heartbeat_expired: '브라우저 연결 끊김'};
      for (const event of events) {
        const line = document.createElement('div'); line.className = 'pipeline-stage';
        line.append(Object.assign(document.createElement('span'), {textContent: names[event.event] || event.event}), Object.assign(document.createElement('strong'), {textContent: stageTime(event.processing_seconds ?? event.waiting_seconds ?? event.latency_seconds)}), Object.assign(document.createElement('small'), {textContent: event.reason || (event.start != null ? `${event.start.toFixed(1)}초부터 · ${event.duration?.toFixed(1) || 0}초 구간` : '')}));
        details.append(line);
      }
      section.append(details);
    }
    const total = document.createElement("div");
    total.className = "pipeline-stage pipeline-total";
    total.append(Object.assign(document.createElement("span"), {textContent: "총 걸린 시간"}), Object.assign(document.createElement("strong"), {textContent: stageTime(aggregate)}), Object.assign(document.createElement("small"), {textContent: "대기열 시간 제외 · 전사·저장·LLM 검수 합계"}));
    section.append(total);
    return section;
  }

  function renderQueue(recordings) {
    const jobs = [...recordings.map(recording => ({...recording, reviewPending:pendingReview(recording), status:recordingWorkflowStatus(recording)})), ...(app.noteJobs || []).map(note => ({...note, kind: "note"}))];
    const active = jobs.filter((item) => ["processing", "cancelling"].includes(item.status));
    const queued = jobs
      .filter((item) => item.status === "queued")
      .sort((left, right) => new Date(left.created_at) - new Date(right.created_at));
    const pausedCount = active.filter(item => item.waiting_for_ai).length;
    const runningCount = active.length - pausedCount;
    const waitingCount = queued.length + pausedCount;
    ui.queueSummary.textContent = runningCount
      ? `${runningCount}개 처리 중${waitingCount ? ` · ${waitingCount}개 대기` : ""}`
      : waitingCount ? `${waitingCount}개 대기` : "비어 있음";
    ui.queueList.replaceChildren();
    const items = [...active, ...queued];
    if (!items.length) {
      const empty = document.createElement("p");
      empty.className = "queue-empty";
      empty.textContent = "대기 중인 작업이 없습니다.";
      ui.queueList.append(empty);
      return;
    }
    items.forEach((recording) => {
      const isQueued = recording.status === "queued";
      const isNote = recording.kind === "note";
      const link = document.createElement("a");
      link.className = `queue-item ${isQueued ? "queued" : "active"}`;
      link.href = isNote ? `#/courses` : `#/recordings/job/${encodeURIComponent(recording.id)}`;
      if (isNote) link.addEventListener("click", (event) => {
        event.preventDefault();
        void openNote(recording.note_id).catch(error => actions.showToast(error.message, "error"));
      });
      const marker = document.createElement("i");
      marker.setAttribute("aria-hidden", "true");
      const copy = document.createElement("span");
      copy.className = "queue-item-copy";
      const title = document.createElement("strong");
      title.textContent = recording.title;
      const meta = document.createElement("small");
      const position = recording.reviewPending ? (isQueued ? "검수 대기" : "검수 중") : isQueued ? (recording.waiting_for_note ? "강의노트 분석 완료 대기" : "전사 대기") : recordingStatusLabels[recording.status] || "처리 중";
      const noteStage = {extracting: "원문 추출", study: "해설 생성", detail: `${recording.number}쪽 상세 해설`}[recording.stage];
      const completed = recording.stage === "extracting" ? recording.extracted_pages : recording.analyzed_pages;
      const progress = !isQueued && recording.page_count ? ` · ${completed}/${recording.page_count}쪽 처리` : "";
      meta.textContent = isNote
        ? `${recording.course_name || "강의노트"} · ${noteStage}${progress}`
        : `${recording.course_name} · ${position}`;
      if (recording.waiting_for_live) meta.textContent = `${recording.course_name} · 실시간 전사 종료 대기`;
      if (recording.waiting_for_ai) meta.textContent = `${recording.course_name || '강의노트'} · AI 처리 재개 대기`;
      copy.append(title, meta);
      const state = document.createElement("b");
      state.textContent = isQueued ? "대기" : isNote ? "분석 중" : recording.status === "cancelling" ? "중단 중" : "처리 중";
      if (recording.waiting_for_ai) state.textContent = '일시정지';
      link.append(marker, copy, state);
      if (recording.waiting_for_ai) {
        // The paused label replaces a pipeline that would otherwise imply active inference.
      } else if (isNote && recording.status === "processing") {
        link.classList.add("has-pipeline");
        link.append(createNoteQueuePipeline(recording));
      } else if (!isNote && recording.reviewPending) {
        link.classList.add("has-pipeline");
        link.append(createQueuePipeline(recording, {phase:"polishing", message:isQueued ? "검수 대기 · 전사 원문은 저장되었습니다." : "LLM 검수 및 결과 저장 중"}));
      } else if (!isNote && recording.status === "processing" && app.job?.job_id === recording.id) {
        link.classList.add("has-pipeline");
        link.append(createQueuePipeline(recording, app.job));
      }
      ui.queueList.append(link);
    });
  }

  function createQueuePipeline(recording, job) {
    const phase = job.phase || "processing";
    const stages = [
      {phase: "processing", label: "변환·전사"},
      {phase: "refining", label: "품질 확인"},
      ...(recording.llm_enabled ? [{phase: "polishing", label: "검수"}] : []),
      {phase: "saving", label: "저장"},
    ];
    return createStagePipeline(stages, phase, job.message || phaseLabels[phase] || "처리 중");
  }

  function createNoteQueuePipeline(note) {
    if (note.stage === "detail") {
      return createStagePipeline([{phase: "detail", label: "상세 해설"}], "detail", `${note.number}쪽 상세 해설 생성 중`);
    }
    const stages = [
      {phase: "extracting", label: "원문 추출"},
      {phase: "keywords", label: "용어집 생성"},
      {phase: "study", label: "해설 생성"},
    ];
    const {phase, message} = noteQueueProgress(note);
    return createStagePipeline(stages, phase, message);
  }

  function createStagePipeline(stages, phase, messageText) {
    const currentIndex = Math.max(0, stages.findIndex((stage) => stage.phase === phase));
    const pipeline = document.createElement("section");
    pipeline.className = "queue-pipeline";
    pipeline.style.setProperty("--queue-stage-count", stages.length);
    pipeline.setAttribute("aria-label", `현재 처리 단계: ${stages[currentIndex].label}`);
    const list = document.createElement("ol");
    stages.forEach((stage, index) => {
      const item = document.createElement("li");
      item.className = index < currentIndex ? "complete" : index === currentIndex ? "current" : "pending";
      if (index === currentIndex) item.setAttribute("aria-current", "step");
      const marker = document.createElement("i");
      marker.setAttribute("aria-hidden", "true");
      const label = document.createElement("span");
      label.textContent = stage.label;
      item.append(marker, label);
      list.append(item);
    });
    const message = document.createElement("p");
    message.textContent = messageText;
    pipeline.append(list, message);
    return pipeline;
  }

  function renderRecordings(recordings) {
    const nextRecordings = recordings || [];
    const fingerprint = JSON.stringify(nextRecordings);
    app.recordings = nextRecordings;
    renderQueue(app.recordings);
    if (fingerprint === app.recordingsFingerprint) return;
    app.recordingsFingerprint = fingerprint;
    actions.renderLibrary();
    actions.renderDashboard();
  }


  return {cancelTranscription, createNoteQueuePipeline, createPipelineDetail, createQueuePipeline, createStagePipeline, deleteRecordingHistory, openPipelineLog, renderQueue, renderRecordings, retryRecording};
}
