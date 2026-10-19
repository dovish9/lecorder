import {requestJson} from "./api.js";
import {statusCardActivity} from "./selectors.js";

export function createStatus(context) {
  const {app, ui, $, $$, actions, listen, every} = context;
  listen($('#aiControlSwitch'), 'change', async event => {
    const toggle = event.target;
    toggle.disabled = true;
    $('#aiControlError').textContent = '';
    try {
      await requestJson('/api/ai/control', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({enabled: toggle.checked})});
      await refreshStatus();
    } catch (error) {
      toggle.checked = app.aiControl?.enabled ?? true;
      $('#aiControlError').textContent = error.message;
    } finally { toggle.disabled = false; }
  });
  const VIEW_STORAGE_KEY = "lecorder-view";
  function renderStatus(data) {
    actions.detectCompletionNotifications(data);
    const control = data.ai_control || {enabled: true, state: 'enabled', generation: 0};
    if (!app.aiControl || control.generation >= app.aiControl.generation) app.aiControl = control;
    const toggle = $('#aiControlSwitch');
    if (toggle) {
      toggle.checked = app.aiControl.enabled;
      $('#aiControlLabel').textContent = {enabled: 'AI 처리 켜짐', pausing: 'AI 처리 멈추는 중', paused: 'AI 처리 일시정지'}[app.aiControl.state];
    }
    const speech = data.speech || {}, llm = data.llm || {};
    const missing = !speech.ready || !llm.ready;
    const loading = speech.state === 'loading' || llm.state === 'loading';
    ui.serverPill.className = 'server-status has-tooltip ' + (missing || loading ? 'warning' : 'ready');
    ui.serverText.textContent = missing ? 'AI 연결 확인 필요' : loading ? '로컬 모델 로드 중' : '음성 전사 · LLM 준비됨';
    if (!app.aiControl.enabled) { ui.serverText.textContent = app.aiControl.state === 'pausing' ? 'AI 처리 멈추는 중' : 'AI 처리 일시정지'; ui.serverPill.className = 'server-status has-tooltip warning'; }
    app.job = data.job || {};
    app.noteJobs = data.note_jobs || [];
    app.liveRecordingId = data.live_recording_id;
    const activity = statusCardActivity(app.job, app.noteJobs, data.recordings || []);
    ui.progressCard.dataset.phase = activity.phase;
    ui.progressKicker.textContent = activity.label;
    ui.progressTitle.textContent = activity.title;
    ui.progressMessage.textContent = activity.message;
    if (!app.aiControl.enabled && [...(data.recordings || []), ...app.noteJobs].some(item => item.waiting_for_ai)) {
      ui.progressKicker.textContent = 'AI 처리 일시정지';
      ui.progressMessage.textContent = 'AI 처리 재개 대기';
    }
    renderSuggestions(app.job.suggestions || []);
    actions.renderRecordings(data.recordings || []);
  }

  function renderSuggestions(suggestions) {
    ui.suggestionList.replaceChildren();
    ui.suggestionPanel.hidden = suggestions.length === 0;
    ui.suggestionSummary.textContent = `검토할 수정 제안 ${suggestions.length}개`;
    for (const item of suggestions) {
      const entry = document.createElement("li");
      const change = document.createElement("strong");
      change.textContent = `${item.original} → ${item.replacement}`;
      const reason = document.createElement("small");
      reason.textContent = `${Math.round(item.confidence * 100)}% · ${item.reason}`;
      entry.append(change, reason);
      ui.suggestionList.append(entry);
    }
  }

  async function refreshStatus() {
    try {
      const data = await requestJson("/api/status", {cache: "no-store"});
      renderStatus(data);
      void actions.recoverRecordingSessions(data.recordings || []);
    } catch (_) {
      ui.serverPill.className = "server-status has-tooltip offline";
      ui.serverText.textContent = "Lecorder 연결 안 됨";
    }
  }

  async function initialize() {
    try {
      const data = await requestJson("/api/bootstrap", {cache: "no-store"});
      app.capabilities = await requestJson("/api/capabilities");
      app.courses = data.courses;
      app.activeCourseId = data.active_course_id || 0;
      actions.renderCourseList();
      actions.renderCourse(actions.activeCourse());
      actions.renderEnvironment(data.environment);
      renderStatus(data);
      void actions.recoverRecordingSessions(data.recordings || []);
      await actions.refreshStorage();
      if (!window.location.hash) {
        const restored = window.localStorage.getItem(VIEW_STORAGE_KEY);
        window.location.hash = restored === "courses" ? actions.courseRoute() : restored === "recordings" ? "#/recordings" : restored === "settings" ? "#/settings" : "#/dashboard";
      }
      await actions.applyRoute();
      every(refreshStatus, 1800);
      every(() => {
        if (["dashboard", "recordings"].includes(app.activeView)) void actions.refreshStorage();
      }, 5000);
    } catch (error) {
      ui.courseList.textContent = `대시보드를 불러오지 못했습니다: ${error.message}`;
      actions.setSaveState("error", "연결 오류");
    }
  }

  return {initialize, refreshStatus, renderStatus, renderSuggestions};
}
