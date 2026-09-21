import {showCourseNotes, chooseNote, openNote} from "./notes.js?v=20260921-note-queue";
import {
  deletePendingChunk,
  deleteRecordingRecovery,
  pendingChunks,
  putPendingChunk,
  putRecordingSession,
  recordingSessions,
  updateRecordingSession,
} from "./recording-store.js?v=20260910-1";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

const systemDarkMode = window.matchMedia("(prefers-color-scheme: dark)");
function syncThemeColor() {
  $("meta[name='theme-color']").content = systemDarkMode.matches ? "#1e1f22" : "#ecedef";
}
syncThemeColor();
systemDarkMode.addEventListener?.("change", syncThemeColor);

const ui = {
  commandDock: $("#commandDock"),
  dockCourseSelect: $("#dockCourseSelect"),
  dockCoursePicker: $("#dockCoursePicker"),
  dockCourseButton: $("#dockCourseButton"),
  dockCourseLabel: $("#dockCourseLabel"),
  dockCourseMenu: $("#dockCourseMenu"),
  dockRecordButton: $("#dockRecordButton"),
  dockRecordLabel: $("#dockRecordLabel"),
  dockPauseButton: $("#dockPauseButton"),
  dockRecordingSession: $("#dockRecordingSession"),
  dockRecordingTitle: $("#dockRecordingTitle"),
  dockRecordClock: $("#dockRecordClock"),
  dockSoundMeter: $("#dockSoundMeter"),
  soundBars: [],
  dockUploadButton: $("#dockUploadButton"),
  courseSearch: $("#courseSearch"),
  dashboardDate: $("#dashboardDate"),
  dashboardCourseName: $("#dashboardCourseName"),
  dashboardCourseLink: $("#dashboardCourseLink"),
  dashboardQueue: $("#dashboardQueue"),
  dashboardAttention: $("#dashboardAttention"),
  dashboardRecent: $("#dashboardRecent"),
  libraryBadge: $("#libraryBadge"),
  libraryBrowser: $("#libraryBrowser"),
  librarySearch: $("#librarySearch"),
  libraryFilter: $("#libraryFilter"),
  librarySort: $("#librarySort"),
  libraryList: $("#libraryList"),
  libraryDetail: $("#libraryDetail"),
  courseList: $("#courseList"),
  courseCount: $("#courseCount"),
  newCourseForm: $("#newCourseForm"),
  newCourseName: $("#newCourseName"),
  courseName: $("#courseName"),
  formatTranscript: $("#formatTranscript"),
  llmEnabled: $("#llmEnabled"),
  saveState: $("#saveState"),
  uploadDialog: $("#uploadDialog"),
  uploadForm: $("#uploadForm"),
  uploadCourseSelect: $("#uploadCourseSelect"),
  uploadCoursePicker: $("#uploadCoursePicker"),
  uploadCourseButton: $("#uploadCourseButton"),
  uploadCourseLabel: $("#uploadCourseLabel"),
  uploadCourseMenu: $("#uploadCourseMenu"),
  closeUploadButton: $("#closeUploadButton"),
  cancelUploadButton: $("#cancelUploadButton"),
  uploadFile: $("#uploadFile"),
  uploadTitle: $("#uploadTitle"),
  uploadButton: $("#uploadButton"),
  uploadMessage: $("#uploadMessage"),
  dropZone: $("#dropZone"),
  dropTitle: $("#dropTitle"),
  dropDescription: $("#dropDescription"),
  serverPill: $("#serverPill"),
  serverText: $("#serverText"),
  progressCard: $("#progressCard"),
  progressKicker: $("#progressKicker"),
  progressTitle: $("#progressTitle"),
  progressMessage: $("#progressMessage"),
  suggestionPanel: $("#suggestionPanel"),
  suggestionSummary: $("#suggestionSummary"),
  suggestionList: $("#suggestionList"),
  queueSummary: $("#queueSummary"),
  queuePanel: $("#queuePanel"),
  queueList: $("#queueList"),
  workspaceTitle: $("#workspaceTitle"),
  workspaceDescription: $("#workspaceDescription"),
  workspaceTabs: $$("[data-view]"),
  workspacePanels: $$('[data-view-panel]'),
  confirmDialog: $("#confirmDialog"),
  confirmTitle: $("#confirmTitle"),
  confirmMessage: $("#confirmMessage"),
  confirmAccept: $("#confirmAccept"),
  toastRegion: $("#toastRegion"),
  reviewDialog: $("#reviewDialog"),
  reviewDialogTitle: $("#reviewDialogTitle"),
  reviewRecordingName: $("#reviewRecordingName"),
  reviewSubtitle: $("#reviewSubtitle"),
  reviewList: $("#reviewList"),
  closeReviewButton: $("#closeReviewButton"),
  pipelineDialog: $("#pipelineDialog"),
  pipelineDialogSubtitle: $("#pipelineDialogSubtitle"),
  pipelineDialogBody: $("#pipelineDialogBody"),
  closePipelineButton: $("#closePipelineButton"),
  guideButton: $("#guideButton"),
  guideDialog: $("#guideDialog"),
  guideFrame: $("#guideFrame"),
  closeGuideButton: $("#closeGuideButton"),
  systemButton: $("#systemButton"),
  systemDialog: $("#systemDialog"),
  systemForm: $("#systemForm"),
  closeSystemButton: $("#closeSystemButton"),
  projectDir: $("#projectDir"),
  outputDir: $("#outputDir"),
  projectCheck: $("#projectCheck"),
  whisperCheck: $("#whisperCheck"),
  outputCheck: $("#outputCheck"),
  systemMessage: $("#systemMessage"),
  saveSystemButton: $("#saveSystemButton"),
};

for (let index = 0; index < 13; index += 1) ui.dockSoundMeter.append(document.createElement("i"));
ui.soundBars = [...ui.dockSoundMeter.children];

const app = {
  courses: [],
  recordings: [],
  activeCourseId: null,
  environment: null,
  selectedFile: null,
  uploadTitleAutomatic: true,
  uploadInProgress: false,
  hydrating: false,
  saveTimer: null,
  saveChain: Promise.resolve(),
  mediaRecorder: null,
  mediaStream: null,
  recordingId: null,
  finalizingRecordingId: null,
  chunkIndex: 0,
  chunkUploadChain: Promise.resolve(),
  failedChunks: [],
  recordingStartedAt: 0,
  recordingState: "idle",
  recordingTitle: "",
  recordingCourseId: null,
  pausedAt: 0,
  pausedDuration: 0,
  clockTimer: null,
  audioContext: null,
  audioSource: null,
  audioAnalyser: null,
  meterFrame: null,
  activeView: "dashboard",
  reviewingRecordingId: null,
  storageItems: [],
  storageFingerprint: "",
  recordingsFingerprint: "",
  reviewAudio: null,
  reviewAudioButton: null,
  recoverySyncing: false,
  recoveryAttempted: new Set(),
  recoveryTasks: new Map(),
  libraryEntries: [],
  selectedLibraryKey: null,
  previewAudioPlayer: null,
  previewEntryFingerprint: null,
  pipelineLogInvoker: null,
  job: {},
};

const VIEW_STORAGE_KEY = "lecorder-view";

ui.closeReviewButton.addEventListener("click", () => {
  stopReviewAudio();
  app.reviewingRecordingId = null;
  ui.reviewDialog.hidden = true;
  const entry = app.libraryEntries.find((item) => item.key === app.selectedLibraryKey);
  if (entry) renderLibraryDetail(entry);
});
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape" || !app.reviewingRecordingId || app.activeView !== "recordings") return;
  if (document.querySelector("dialog[open]")) return;
  event.preventDefault();
  ui.closeReviewButton.click();
});
ui.guideButton.addEventListener("click", (event) => {
  event.preventDefault();
  ui.guideFrame.src = "/guide?embedded=1";
  ui.guideDialog.showModal();
});
ui.closeGuideButton.addEventListener("click", () => ui.guideDialog.close());
ui.guideDialog.addEventListener("click", (event) => {
  if (event.target === ui.guideDialog) ui.guideDialog.close();
});
ui.guideDialog.addEventListener("close", () => ui.guideFrame.removeAttribute("src"));
ui.closePipelineButton.addEventListener("click", () => ui.pipelineDialog.close());
ui.pipelineDialog.addEventListener("click", (event) => {
  if (event.target === ui.pipelineDialog) ui.pipelineDialog.close();
});
ui.pipelineDialog.addEventListener("close", () => {
  ui.pipelineDialogBody.replaceChildren();
  const invoker = app.pipelineLogInvoker;
  app.pipelineLogInvoker = null;
  if (invoker?.isConnected) invoker.focus({preventScroll: true});
});
document.addEventListener("click", (event) => {
  $$(".detail-menu[open]").forEach((menu) => {
    if (!menu.contains(event.target)) menu.removeAttribute("open");
  });
  if (ui.dockCoursePicker.open && !ui.dockCoursePicker.contains(event.target)) {
    ui.dockCoursePicker.removeAttribute("open");
  }
});

function showToast(message, kind = "info", action = null) {
  const toast = document.createElement("div");
  toast.className = `toast ${kind}`;
  const copy = document.createElement("span");
  copy.textContent = message;
  toast.append(copy);
  if (action?.label && typeof action.run === "function") {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = action.label;
    button.addEventListener("click", () => {
      action.run();
      toast.remove();
    });
    toast.append(button);
  }
  ui.toastRegion.append(toast);
  window.setTimeout(() => toast.remove(), 4200);
}

function confirmAction(message, title = "확인", acceptLabel = "확인") {
  ui.confirmTitle.textContent = title;
  ui.confirmMessage.textContent = message;
  ui.confirmAccept.textContent = acceptLabel;
  ui.confirmDialog.returnValue = "";
  ui.confirmDialog.showModal();
  return new Promise((resolve) => {
    ui.confirmDialog.addEventListener("close", () => {
      resolve(ui.confirmDialog.returnValue === "confirm");
    }, {once: true});
  });
}

const weekdays = ["일", "월", "화", "수", "목", "금", "토"];

function activeCourse() {
  return app.courses.find((course) => course.id === app.activeCourseId);
}

const viewCopy = {
  dashboard: ["대시보드", "녹음과 전사 작업의 현재 상태를 확인합니다."],
  courses: ["강의", "강의별 음성 인식 설정과 강의노트를 관리합니다."],
  recordings: ["보관함", "원음, 결과 파일, 수정 검토와 처리 기록을 함께 봅니다."],
};

function switchWorkspace(view, persist = true) {
  if (!viewCopy[view]) return;
  if (view !== "recordings") stopReviewAudio();
  app.activeView = view;
  ui.workspaceTabs.forEach((tab) => {
    const active = tab.dataset.view === view;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-current", active ? "page" : "false");
  });
  ui.workspacePanels.forEach((panel) => panel.hidden = panel.dataset.viewPanel !== view);
  [ui.workspaceTitle.textContent, ui.workspaceDescription.textContent] = viewCopy[view];
  if (persist) window.localStorage.setItem(VIEW_STORAGE_KEY, view);
  if (["dashboard", "recordings"].includes(view)) void refreshStorage();
}

function courseRoute(courseId = app.activeCourseId) {
  return courseId ? `#/courses/${encodeURIComponent(courseId)}` : "#/courses";
}

function parseRoute() {
  const raw = window.location.hash || "#/dashboard";
  const [path, query = ""] = raw.slice(1).split("?");
  return {path: path || "/dashboard", parts: (path || "/dashboard").split("/").filter(Boolean), params: new URLSearchParams(query)};
}

async function applyRoute() {
  const {parts} = parseRoute();
  if (parts[0] === "today" && parts.length === 1) {
    window.location.replace("#/dashboard");
    return;
  }
  if (parts[0] === "dashboard" && parts.length === 1) {
    switchWorkspace("dashboard");
    renderDashboard();
    return;
  }
  if (parts[0] === "courses") {
    const requested = Number(parts[1]);
    if (parts[1] && !app.courses.some((course) => course.id === requested)) {
      window.location.replace(courseRoute());
      return;
    }
    if (requested && requested !== app.activeCourseId) await selectCourse(requested, {updateRoute: false});
    switchWorkspace("courses");
    return;
  }
  if (parts[0] === "recordings") {
    switchWorkspace("recordings");
    if (parts.length === 1) {
      app.selectedLibraryKey = null;
      renderLibrary();
      return;
    }
    const key = parts[1] === "job" ? `job:${parts[2]}` : parts[1] === "file" ? `file:${decodeURIComponent(parts[2] || "")}` : "";
    if (!key || !app.libraryEntries.some((entry) => entry.key === key)) {
      window.location.replace("#/recordings");
      return;
    }
    app.selectedLibraryKey = key;
    renderLibrary();
    return;
  }
  if (parts[0] === "capture") {
    window.location.replace("#/dashboard");
    return;
  }
  window.location.replace("#/dashboard");
}

ui.workspaceTabs.forEach((tab) => tab.addEventListener("click", () => {
  if (tab.dataset.view === "courses") tab.href = courseRoute();
}));
window.addEventListener("hashchange", () => void applyRoute());

function dateSuffix() {
  const now = new Date();
  return `${now.getMonth() + 1}월${now.getDate()}일-${weekdays[now.getDay()]}`;
}

function automaticTitle() {
  const course = activeCourse();
  const base = `${course?.name || "강의"}-${dateSuffix()}`;
  const occupied = new Set([
    ...app.recordings.map((recording) => recording.title),
    ...app.storageItems.map((item) => item.name),
  ].filter(Boolean));
  if (!occupied.has(base)) return base;
  let number = 2;
  while (occupied.has(`${base} (${number})`)) number += 1;
  return `${base} (${number})`;
}

function setSaveState(kind, label) {
  ui.saveState.className = `save-state has-tooltip${kind ? ` ${kind}` : ""}`;
  ui.saveState.lastChild.textContent = label;
}

function renderCourseList() {
  ui.courseCount.textContent = `${app.courses.length}개`;
  ui.courseList.replaceChildren();
  const query = ui.courseSearch.value.trim().toLocaleLowerCase("ko");
  const visibleCourses = app.courses.filter((course) => !query || course.name.toLocaleLowerCase("ko").includes(query));
  for (const course of visibleCourses) {
    const entry = document.createElement("div");
    entry.className = `course-entry${course.id === app.activeCourseId ? " active" : ""}`;
    entry.dataset.courseId = String(course.id);

    const button = document.createElement("button");
    button.type = "button";
    button.className = "course-item";
    button.setAttribute("aria-current", course.id === app.activeCourseId ? "true" : "false");
    button.setAttribute("aria-label", `${course.name} 강의 선택`);
    const name = document.createElement("span");
    name.textContent = course.name;
    const language = document.createElement("small");
    language.textContent = {ko: "한국어", en: "English", auto: "자동"}[course.language] || course.language;
    button.append(name, language);
    button.addEventListener("click", () => { window.location.hash = courseRoute(course.id); });

    const menu = document.createElement("details");
    menu.className = "course-menu";
    const summary = document.createElement("summary");
    summary.textContent = "⋯";
    summary.setAttribute("aria-label", `${course.name} 메뉴`);
    const actions = document.createElement("div");
    actions.className = "course-actions";
    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "course-action";
    edit.textContent = "수정";
    edit.title = `${course.name} 이름 수정`;
    edit.setAttribute("aria-label", `${course.name} 강의 이름 수정`);
    edit.addEventListener("click", () => beginCourseRename(course, entry));
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "course-action remove";
    remove.textContent = "삭제";
    remove.title = `${course.name} 삭제`;
    remove.setAttribute("aria-label", `${course.name} 강의 삭제`);
    remove.addEventListener("click", () => deleteCourseById(course.id));
    actions.append(edit, remove);
    menu.append(summary, actions);
    entry.append(button, menu);
    ui.courseList.append(entry);
  }
  renderCaptureControls();
}

function renderCaptureControls() {
  ui.dockCourseSelect.replaceChildren();
  ui.uploadCourseSelect.replaceChildren();
  ui.dockCourseMenu.replaceChildren();
  ui.uploadCourseMenu.replaceChildren();
  for (const course of app.courses) {
    for (const select of [ui.dockCourseSelect, ui.uploadCourseSelect]) {
      const option = document.createElement("option");
      option.value = String(course.id);
      option.textContent = course.name;
      option.selected = course.id === app.activeCourseId;
      select.append(option);
    }
    const item = document.createElement("button");
    item.type = "button";
    item.className = "dock-course-option";
    item.dataset.courseId = String(course.id);
    item.setAttribute("role", "option");
    item.setAttribute("aria-selected", String(course.id === app.activeCourseId));
    const check = document.createElement("span");
    check.className = "dock-course-check";
    check.setAttribute("aria-hidden", "true");
    check.textContent = course.id === app.activeCourseId ? "✓" : "";
    const name = document.createElement("span");
    name.textContent = course.name;
    item.append(check, name);
    item.addEventListener("click", async () => {
      if (app.recordingState !== "idle") return;
      ui.dockCoursePicker.removeAttribute("open");
      if (course.id !== app.activeCourseId) {
        await selectCourse(course.id, {updateRoute: app.activeView === "courses"});
        renderDashboard();
      }
      ui.dockCourseButton.focus({preventScroll: true});
    });
    ui.dockCourseMenu.append(item);

    const uploadItem = document.createElement("button");
    uploadItem.type = "button";
    uploadItem.className = "upload-course-option";
    uploadItem.dataset.courseId = String(course.id);
    uploadItem.setAttribute("role", "option");
    uploadItem.setAttribute("aria-selected", String(course.id === app.activeCourseId));
    const uploadCheck = document.createElement("span");
    uploadCheck.className = "upload-course-check";
    uploadCheck.setAttribute("aria-hidden", "true");
    uploadCheck.textContent = course.id === app.activeCourseId ? "✓" : "";
    const uploadName = document.createElement("span");
    uploadName.textContent = course.name;
    uploadItem.append(uploadCheck, uploadName);
    uploadItem.addEventListener("click", () => void selectUploadCourse(course.id));
    ui.uploadCourseMenu.append(uploadItem);
  }
  if (!app.courses.length) {
    for (const select of [ui.dockCourseSelect, ui.uploadCourseSelect]) {
      const option = document.createElement("option");
      option.textContent = "강의를 추가하세요";
      select.append(option);
    }
  }
  const locked = app.recordingState !== "idle";
  const selectedCourse = locked
    ? app.courses.find((course) => course.id === app.recordingCourseId)
    : activeCourse();
  ui.dockCourseLabel.textContent = selectedCourse?.name || "강의를 추가하세요";
  ui.uploadCourseLabel.textContent = activeCourse()?.name || "강의를 선택하세요";
  ui.dockCourseButton.setAttribute("aria-disabled", String(!app.courses.length || locked));
  ui.dockCourseButton.tabIndex = app.courses.length ? 0 : -1;
  ui.dockCourseSelect.disabled = !app.courses.length || locked;
  ui.uploadCourseSelect.disabled = !app.courses.length || app.uploadInProgress;
  ui.uploadCourseButton.setAttribute("aria-disabled", String(!app.courses.length || app.uploadInProgress));
  ui.uploadCourseButton.tabIndex = app.courses.length && !app.uploadInProgress ? 0 : -1;
  if (app.uploadInProgress) ui.uploadCoursePicker.removeAttribute("open");
  ui.dockRecordButton.disabled = !app.courses.length || app.uploadInProgress || app.recordingState === "starting" || app.recordingState === "finalizing";
  ui.dockUploadButton.disabled = !app.courses.length || app.uploadInProgress || locked;
}

ui.courseSearch.addEventListener("input", renderCourseList);
ui.dockCourseSelect.addEventListener("change", async () => {
  if (app.recordingState !== "idle") return;
  const courseId = Number(ui.dockCourseSelect.value);
  await selectCourse(courseId, {updateRoute: app.activeView === "courses"});
  renderDashboard();
});
ui.dockCourseButton.addEventListener("click", (event) => {
  if (app.courses.length && app.recordingState === "idle") return;
  event.preventDefault();
});
ui.dockCourseButton.addEventListener("keydown", (event) => {
  if (event.key !== "ArrowDown" || !app.courses.length || app.recordingState !== "idle") return;
  event.preventDefault();
  ui.dockCoursePicker.setAttribute("open", "");
  ui.dockCourseMenu.querySelector('[aria-selected="true"]')?.focus();
});
ui.dockCoursePicker.addEventListener("keydown", (event) => {
  if (event.key !== "Escape" || !ui.dockCoursePicker.open) return;
  event.preventDefault();
  ui.dockCoursePicker.removeAttribute("open");
  ui.dockCourseButton.focus();
});

async function selectUploadCourse(courseId) {
  if (app.uploadInProgress || !app.courses.some((course) => course.id === courseId)) return;
  ui.uploadCoursePicker.removeAttribute("open");
  ui.uploadCourseButton.setAttribute("aria-busy", "true");
  if (courseId !== app.activeCourseId) {
    await selectCourse(courseId, {updateRoute: app.activeView === "courses"});
    renderDashboard();
  }
  ui.uploadCourseSelect.value = String(app.activeCourseId);
  setUploadTitlePreset();
  ui.uploadCourseButton.removeAttribute("aria-busy");
  ui.uploadCourseButton.focus({preventScroll: true});
}

ui.uploadCourseButton.addEventListener("click", (event) => {
  if (app.courses.length && !app.uploadInProgress) return;
  event.preventDefault();
});
ui.uploadCoursePicker.addEventListener("toggle", () => {
  ui.uploadCourseButton.setAttribute("aria-expanded", String(ui.uploadCoursePicker.open));
});
ui.uploadCoursePicker.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && ui.uploadCoursePicker.open) {
    event.preventDefault();
    ui.uploadCoursePicker.removeAttribute("open");
    ui.uploadCourseButton.focus();
    return;
  }
  if (event.target === ui.uploadCourseButton && ["ArrowDown", "ArrowUp"].includes(event.key)) {
    event.preventDefault();
    ui.uploadCoursePicker.setAttribute("open", "");
    const selected = ui.uploadCourseMenu.querySelector('[aria-selected="true"]');
    (selected || ui.uploadCourseMenu.querySelector("button"))?.focus();
    return;
  }
  if (!event.target.matches(".upload-course-option")) return;
  const options = [...ui.uploadCourseMenu.querySelectorAll(".upload-course-option")];
  const index = options.indexOf(event.target);
  const targetIndex = event.key === "ArrowDown" ? Math.min(options.length - 1, index + 1)
    : event.key === "ArrowUp" ? Math.max(0, index - 1)
    : event.key === "Home" ? 0
    : event.key === "End" ? options.length - 1
    : -1;
  if (targetIndex >= 0) {
    event.preventDefault();
    options[targetIndex].focus();
  }
});

function beginCourseRename(course, entry) {
  if (entry.querySelector(".course-rename-form")) return;
  const form = document.createElement("form");
  form.className = "course-rename-form";
  const input = document.createElement("input");
  input.type = "text";
  input.maxLength = 80;
  input.value = course.name;
  input.setAttribute("aria-label", `${course.name}의 새 이름`);
  const save = document.createElement("button");
  save.type = "submit";
  save.textContent = "저장";
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.textContent = "취소";
  cancel.addEventListener("click", renderCourseList);
  input.addEventListener("input", () => input.setCustomValidity(""));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = input.value.trim();
    if (!name) {
      input.setCustomValidity("강의 이름을 입력해 주세요.");
      input.reportValidity();
      return;
    }
    await flushCourseSave();
    try {
      const data = await requestJson(`/api/courses/${course.id}`, {
        method: "PATCH",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({name}),
      });
      const index = app.courses.findIndex((item) => item.id === course.id);
      if (index >= 0) app.courses[index] = data.course;
      if (app.activeCourseId === course.id) {
        ui.courseName.value = data.course.name;
      }
      renderCourseList();
      setSaveState("", "저장됨");
    } catch (error) {
      input.setCustomValidity(error.message);
      input.reportValidity();
    }
  });
  form.append(input, save, cancel);
  entry.replaceChildren(form);
  input.focus();
  input.select();
}

async function deleteCourseById(courseId) {
  const course = app.courses.find((item) => item.id === courseId);
  if (!course || !await confirmAction(`‘${course.name}’ 강의와 저장된 설정을 삭제할까요? 녹음과 Markdown 파일은 삭제되지 않습니다.`, "강의 삭제")) return;
  await flushCourseSave();
  try {
    const wasActive = course.id === app.activeCourseId;
    const data = await requestJson(`/api/courses/${course.id}`, {method: "DELETE"});
    app.courses = app.courses.filter((item) => item.id !== course.id);
    app.activeCourseId = data.active_course_id;
    renderCourseList();
    if (wasActive) renderCourse(activeCourse());
    window.location.hash = courseRoute();
  } catch (error) {
    setSaveState("error", error.message);
  }
}

function renderCourse(course) {
  if (!course) {
    app.hydrating = true;
    ui.courseName.value = "강의를 추가하세요";
    ui.formatTranscript.checked = false;
    ui.llmEnabled.checked = false;
    ui.dashboardCourseName.textContent = "강의 없음";
    ui.dashboardCourseLink.href = "#/courses";
    app.hydrating = false;
    return;
  }
  app.hydrating = true;
  ui.courseName.value = course.name;
  void showCourseNotes(course.id);
  ui.formatTranscript.checked = course.format_transcript;
  ui.llmEnabled.checked = course.llm_enabled;
  const language = $(`input[name="language"][value="${course.language}"]`);
  if (language) language.checked = true;
  ui.dashboardCourseName.textContent = course.name;
  ui.dashboardCourseLink.href = courseRoute(course.id);
  app.hydrating = false;
  setSaveState("", "저장됨");
}

function coursePayload() {
  return {
    name: ui.courseName.value.trim(),
    language: $('input[name="language"]:checked')?.value || "ko",
    format_transcript: ui.formatTranscript.checked,
    llm_enabled: ui.llmEnabled.checked,
  };
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  const contentType = response.headers.get("content-type") || "";
  let data;
  if (contentType.includes("application/json")) {
    data = await response.json();
  } else {
    const text = (await response.text()).trim();
    const looksLikeHtml = /<\s*(?:!doctype|html|head|body|title|h1)\b/i.test(text);
    data = {error: looksLikeHtml ? "" : text};
  }
  if (!response.ok) {
    if (response.status === 405) {
      throw new Error("현재 실행 중인 앱에 새 기능이 적용되지 않았습니다. Lecorder를 다시 시작한 뒤 다시 시도해 주세요.");
    }
    throw new Error(data.error || `요청을 처리하지 못했습니다. (${response.status})`);
  }
  return data;
}

function saveCourseNow() {
  if (app.hydrating || !app.activeCourseId) return app.saveChain;
  window.clearTimeout(app.saveTimer);
  app.saveTimer = null;
  const courseId = app.activeCourseId;
  const payload = coursePayload();
  setSaveState("saving", "저장 중");
  app.saveChain = app.saveChain.then(async () => {
    try {
      const data = await requestJsonBeforeDeadline(`/api/courses/${courseId}`, {
        method: "PATCH",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload),
      });
      const index = app.courses.findIndex((course) => course.id === courseId);
      if (index >= 0) app.courses[index] = data.course;
      if (app.activeCourseId === courseId) {
        renderCourseList();
        setSaveState("", "저장됨");
      }
    } catch (error) {
      if (app.activeCourseId === courseId) setSaveState("error", error.message);
    }
  });
  return app.saveChain;
}

function scheduleCourseSave(delay = 450) {
  if (app.hydrating) return;
  window.clearTimeout(app.saveTimer);
  app.saveTimer = window.setTimeout(saveCourseNow, delay);
  setSaveState("saving", "입력 중");
}

async function flushCourseSave({requireSaved = false} = {}) {
  if (app.saveTimer) saveCourseNow();
  await app.saveChain;
  if (requireSaved && ui.saveState.classList.contains("error")) {
    throw new Error("강의 설정을 저장하지 못했습니다. 설정 저장 후 다시 시작하세요.");
  }
}

async function selectCourse(courseId, {updateRoute = false} = {}) {
  if (courseId === app.activeCourseId) return;
  await flushCourseSave();
  try {
    const data = await requestJson(`/api/courses/${courseId}/select`, {method: "POST"});
    app.activeCourseId = data.course.id;
    app.reviewingRecordingId = null;
    const index = app.courses.findIndex((course) => course.id === data.course.id);
    if (index >= 0) app.courses[index] = data.course;
    renderCourseList();
    renderCourse(data.course);
    if (updateRoute) window.location.hash = courseRoute(data.course.id);
  } catch (error) {
    setSaveState("error", error.message);
  }
}

ui.newCourseForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const name = ui.newCourseName.value.trim();
  if (!name) return;
  await flushCourseSave();
  try {
    const data = await requestJson("/api/courses", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({name}),
    });
    app.courses.push(data.course);
    app.activeCourseId = data.course.id;
    app.reviewingRecordingId = null;
    ui.newCourseName.value = "";
    renderCourseList();
    renderCourse(data.course);
    window.location.hash = courseRoute(data.course.id);
  } catch (error) {
    ui.newCourseName.setCustomValidity(error.message);
    ui.newCourseName.reportValidity();
  }
});

ui.newCourseName.addEventListener("input", () => ui.newCourseName.setCustomValidity(""));

ui.courseName.addEventListener("input", () => {
  const course = activeCourse();
  if (course) {
    course.name = ui.courseName.value;
    const label = ui.courseList.querySelector(`[data-course-id="${course.id}"] .course-item span`);
    if (label) label.textContent = course.name || "이름 없는 강의";
    ui.dashboardCourseName.textContent = course.name || "선택한 강의";
  }
  scheduleCourseSave();
});
$$('input[name="language"]').forEach((input) => input.addEventListener("change", () => {
  const course = activeCourse();
  if (course) course.language = input.value;
  renderCourseList();
  scheduleCourseSave(0);
}));
[ui.formatTranscript, ui.llmEnabled].forEach((input) => input.addEventListener("change", () => scheduleCourseSave(0)));

ui.uploadTitle.addEventListener("input", () => {
  app.uploadTitleAutomatic = false;
  ui.uploadTitle.setCustomValidity("");
});

function clockText(milliseconds) {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  const hours = String(Math.floor(seconds / 3600)).padStart(2, "0");
  const minutes = String(Math.floor((seconds % 3600) / 60)).padStart(2, "0");
  return `${hours}:${minutes}:${String(seconds % 60).padStart(2, "0")}`;
}

function timeoutError(message) {
  const error = new Error(message);
  error.name = "TimeoutError";
  return error;
}

async function acquireMicrophone(timeoutMs = 12000) {
  let expired = false;
  let timer;
  const request = navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: {ideal: 1},
      sampleRate: {ideal: 48000},
      echoCancellation: {ideal: true},
      noiseSuppression: {ideal: true},
      autoGainControl: {ideal: true},
    },
  }).then((stream) => {
    if (expired || app.recordingState !== "starting") {
      stream.getTracks().forEach((track) => track.stop());
      throw timeoutError("마이크 연결이 취소되었습니다.");
    }
    return stream;
  });
  const timeout = new Promise((_, reject) => {
    timer = window.setTimeout(() => {
      expired = true;
      reject(timeoutError("마이크 응답이 없습니다. 브라우저의 마이크 권한을 확인해 주세요."));
    }, timeoutMs);
  });
  try {
    return await Promise.race([request, timeout]);
  } finally {
    window.clearTimeout(timer);
  }
}

async function requestJsonBeforeDeadline(url, options = {}, timeoutMs = 12000) {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await requestJson(url, {...options, signal: controller.signal});
  } catch (error) {
    if (error.name === "AbortError") throw timeoutError("녹음 준비가 지연되고 있습니다. 잠시 후 다시 시도해 주세요.");
    throw error;
  } finally {
    window.clearTimeout(timer);
  }
}

function updateClock() {
  const now = app.pausedAt || Date.now();
  const elapsed = Math.max(0, now - app.recordingStartedAt - app.pausedDuration);
  ui.dockRecordClock.textContent = clockText(elapsed);
  ui.dockRecordClock.dateTime = `PT${Math.floor(elapsed / 1000)}S`;
}

function resetSoundBars() {
  ui.soundBars.forEach((bar) => bar.style.removeProperty("--meter-level"));
}

function setRecorderState(state) {
  app.recordingState = state;
  ui.commandDock.dataset.recordState = state;
  const active = ["recording", "paused"].includes(state);
  const finalizing = state === "finalizing";
  $("#dockNotePicker").hidden = !active && !finalizing;
  $("#dockNoteSelect").disabled = !active;
  $("#dockNoteRefresh").disabled = !active;
  ui.dockRecordLabel.textContent = active ? "종료" : finalizing ? "저장 중" : state === "starting" ? "연결 중" : "녹음";
  ui.dockRecordButton.setAttribute("aria-label", active ? "녹음 종료 후 전사" : finalizing ? "녹음 저장 중" : state === "starting" ? "마이크 연결 중" : "녹음 시작");
  ui.dockRecordButton.disabled = !app.courses.length || ["starting", "finalizing"].includes(state);
  ui.dockPauseButton.disabled = !active;
  ui.dockPauseButton.setAttribute("aria-label", state === "paused" ? "녹음 계속" : "녹음 일시정지");
  ui.dockPauseButton.setAttribute("aria-pressed", String(state === "paused"));
  ui.dockPauseButton.parentElement.setAttribute("aria-hidden", String(!active));
  if (state !== "recording") resetSoundBars();
  if (["idle", "finalizing"].includes(state)) {
    ui.dockRecordingTitle.textContent = "";
    ui.dockRecordClock.textContent = "00:00:00";
    ui.dockRecordClock.dateTime = "PT0S";
  }
  renderCaptureControls();
}

function frequencyBandLevel(levels, sampleRate, fftSize, index, count) {
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

function stopSoundMeter() {
  if (app.meterFrame) window.cancelAnimationFrame(app.meterFrame);
  app.meterFrame = null;
  try { app.audioSource?.disconnect(); } catch (_) { /* Already disconnected. */ }
  if (app.audioContext && app.audioContext.state !== "closed") void app.audioContext.close();
  app.audioContext = null;
  app.audioSource = null;
  app.audioAnalyser = null;
  ui.commandDock.classList.remove("meter-fallback");
  resetSoundBars();
}

function startSoundMeter(stream) {
  stopSoundMeter();
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextClass) {
    ui.commandDock.classList.add("meter-fallback");
    return;
  }
  try {
    app.audioContext = new AudioContextClass();
    app.audioSource = app.audioContext.createMediaStreamSource(stream);
    app.audioAnalyser = app.audioContext.createAnalyser();
    app.audioAnalyser.fftSize = 512;
    app.audioAnalyser.smoothingTimeConstant = .72;
    app.audioSource.connect(app.audioAnalyser);
    if (app.audioContext.state === "suspended") void app.audioContext.resume();
    const levels = new Uint8Array(app.audioAnalyser.frequencyBinCount);
    const draw = () => {
      if (!app.audioAnalyser) return;
      app.audioAnalyser.getByteFrequencyData(levels);
      if (app.recordingState === "recording") {
        ui.soundBars.forEach((bar, index) => {
          const band = frequencyBandLevel(
            levels, app.audioContext.sampleRate, app.audioAnalyser.fftSize,
            index, ui.soundBars.length,
          );
          const energy = Math.max(0, band - .025);
          bar.style.setProperty("--meter-level", String(Math.min(1, .08 + Math.pow(energy, .72) * 1.08)));
        });
      }
      app.meterFrame = window.requestAnimationFrame(draw);
    };
    draw();
  } catch (_) {
    stopSoundMeter();
    ui.commandDock.classList.add("meter-fallback");
  }
}

function stopMediaStream() {
  app.mediaStream?.getTracks().forEach((track) => track.stop());
  app.mediaStream = null;
  window.clearInterval(app.clockTimer);
  app.clockTimer = null;
  stopSoundMeter();
}

function recordingExtension(mimeType) {
  if (mimeType.includes("mp4")) return ".mp4";
  if (mimeType.includes("ogg")) return ".ogg";
  return ".webm";
}

function preferredRecordingMimeType() {
  return [
    "audio/mp4;codecs=mp4a.40.2",
    "audio/mp4",
    "audio/webm;codecs=opus",
    "audio/webm",
  ].find((type) => MediaRecorder.isTypeSupported(type)) || "";
}

async function uploadChunk(recordingId, index, blob, {attempts = 3, timeoutMs = 5000} = {}) {
  let lastError;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
    try {
      return await requestJson(`/api/recordings/${recordingId}/chunks/${index}`, {
        method: "PUT",
        headers: {"Content-Type": blob.type || "application/octet-stream"},
        body: blob,
        signal: controller.signal,
      });
    } catch (error) {
      lastError = error.name === "AbortError"
        ? new Error("서버 응답 시간이 초과됐습니다.")
        : error;
      if (attempt < attempts) await new Promise((resolve) => window.setTimeout(resolve, attempt * 500));
    } finally {
      window.clearTimeout(timeout);
    }
  }
  throw lastError;
}

function queueRecordingChunk(blob) {
  if (!blob.size || !app.recordingId) return;
  const item = {recordingId: app.recordingId, index: app.chunkIndex, blob};
  app.chunkIndex += 1;
  // Persist immediately. A stalled upload must never hold up later chunks from
  // reaching durable browser storage.
  const persistence = (async () => {
    try {
      await putPendingChunk(item);
      await updateRecordingSession(item.recordingId, {
        durationSeconds: Math.max(0, Date.now() - app.recordingStartedAt - app.pausedDuration) / 1000,
        nextChunkIndex: app.chunkIndex,
      });
      return true;
    } catch (_) {
      if (!app.failedChunks.some((chunk) => chunk.recordingId === item.recordingId && chunk.index === item.index)) {
        app.failedChunks.push(item);
      }
      return false;
    }
  })();
  app.chunkUploadChain = app.chunkUploadChain.then(async () => {
    const persisted = await persistence;
    try {
      await uploadChunk(item.recordingId, item.index, item.blob, {attempts: 1, timeoutMs: 4000});
      if (persisted) await deletePendingChunk(item.recordingId, item.index).catch(() => {});
      app.failedChunks = app.failedChunks.filter(
        (chunk) => chunk.recordingId !== item.recordingId || chunk.index !== item.index
      );
      ui.dockRecordingSession.dataset.sync = "saved";
      ui.dockRecordingSession.setAttribute("aria-label", `녹음 중 · ${app.chunkIndex}개 조각 저장됨`);
    } catch (error) {
      ui.dockRecordingSession.dataset.sync = "error";
      const message = persisted
        ? "서버 연결 끊김 · 녹음 조각은 브라우저에 보관 중입니다."
        : "서버 연결 끊김 · 일부 조각을 메모리에 보관 중이므로 탭을 닫지 마세요.";
      ui.dockRecordingSession.setAttribute("aria-label", message);
      ui.dockRecordingSession.title = message;
    }
  });
}

async function retryFailedChunks(recordingId) {
  let durable = [];
  try {
    durable = await pendingChunks(recordingId);
  } catch (_) {
    // Memory fallback below remains intact until each upload succeeds.
  }
  const combined = new Map();
  for (const item of durable) combined.set(item.index, item);
  for (const item of app.failedChunks) {
    if (item.recordingId === recordingId) combined.set(item.index, item);
  }
  for (const item of [...combined.values()].sort((left, right) => left.index - right.index)) {
    await uploadChunk(recordingId, item.index, item.blob);
    await deletePendingChunk(recordingId, item.index).catch(() => {});
    app.failedChunks = app.failedChunks.filter(
      (chunk) => chunk.recordingId !== recordingId || chunk.index !== item.index
    );
  }
}

async function finalizeRecording() {
  const recordingId = app.recordingId;
  app.finalizingRecordingId = recordingId;
  try {
    await recordingNoteSave;
    await app.chunkUploadChain;
    const now = app.pausedAt || Date.now();
    const duration = Math.max(0, now - app.recordingStartedAt - app.pausedDuration) / 1000;
    await updateRecordingSession(recordingId, {durationSeconds: duration}).catch(() => {});
    await retryFailedChunks(recordingId);
    const result = await requestJson(`/api/recordings/${recordingId}/finalize`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({duration_seconds: duration}),
    });
    await deleteRecordingRecovery(recordingId).catch(() => {});
    app.recordingId = null;
    renderRecordings([result.recording, ...(app.recordings || []).filter((item) => item.id !== result.recording.id)]);
    await refreshStatus();
    await refreshStorage();
    showToast("녹음을 안전하게 저장하고 전사 대기열에 추가했습니다.", "success", {
      label: "보관함에서 보기",
      run: () => { window.location.hash = `#/recordings/job/${encodeURIComponent(result.recording.id)}`; },
    });
    return result;
  } finally {
    app.finalizingRecordingId = null;
  }
}

function restoreRecordingSession(session) {
  const existing = app.recoveryTasks.get(session.recordingId);
  if (existing) return existing;
  const task = (async () => {
    await retryFailedChunks(session.recordingId);
    const result = await requestJson(`/api/recordings/${session.recordingId}/finalize`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({duration_seconds: Number(session.durationSeconds) || 0}),
    });
    await deleteRecordingRecovery(session.recordingId).catch(() => {});
    return result;
  })();
  app.recoveryTasks.set(session.recordingId, task);
  void task.finally(() => app.recoveryTasks.delete(session.recordingId)).catch(() => {});
  return task;
}

async function recoverRecordingSessions(recordings) {
  if (app.recoverySyncing) return;
  app.recoverySyncing = true;
  try {
    let sessions = [];
    try {
      sessions = await recordingSessions();
    } catch (_) {
      return;
    }
    for (const session of sessions) {
      const recording = recordings.find((item) => item.id === session.recordingId);
      if (!recording || ["queued", "processing", "cancelling", "cancelled", "completed", "failed"].includes(recording.status)) {
        await deleteRecordingRecovery(session.recordingId).catch(() => {});
        continue;
      }
      const activelyRecording = session.recordingId === app.recordingId
        && app.mediaRecorder
        && app.mediaRecorder.state !== "inactive";
      if (session.recordingId === app.finalizingRecordingId) continue;
      if (activelyRecording) {
        app.chunkUploadChain = app.chunkUploadChain.then(
          () => retryFailedChunks(session.recordingId)
        );
        try {
          await app.chunkUploadChain;
          ui.dockRecordingSession.dataset.sync = "saved";
        } catch (_) {
          // The durable chunks stay in IndexedDB for the next status refresh.
        }
        continue;
      }
      if (!["recording", "recoverable"].includes(recording.status)
          || app.recoveryAttempted.has(session.recordingId)) continue;
      app.recoveryAttempted.add(session.recordingId);
      try {
        const result = await restoreRecordingSession(session);
        renderRecordings([
          result.recording,
          ...app.recordings.filter((item) => item.id !== session.recordingId),
        ]);
        showToast(`중단됐던 ‘${recording.title}’ 녹음을 복구해 전사 대기열에 추가했습니다.`, "success");
      } catch (error) {
        showToast(`녹음 자동 복구 실패: ${error.message}`, "error");
        window.setTimeout(() => app.recoveryAttempted.delete(session.recordingId), 10000);
      }
    }
  } finally {
    app.recoverySyncing = false;
  }
}

let recordingNoteSave = Promise.resolve();
let recordingNoteSaved = "";
let recordingNoteLoad = 0;
async function loadRecordingNotes() {
  const serial = ++recordingNoteLoad;
  const recordingId = app.recordingId;
  const select = $("#dockNoteSelect");
  const status = $("#dockNoteStatus");
  status.textContent = "불러오는 중";
  try {
    const {notes} = await requestJson(`/api/courses/${app.recordingCourseId}/notes`);
    if (serial !== recordingNoteLoad || recordingId !== app.recordingId) return;
    const selected = select.value;
    select.replaceChildren(new Option("사용 안 함", ""));
    for (const note of notes) {
      const ready = ["ready", "partial"].includes(note.status);
      const option = new Option(`${note.title}${ready ? "" : " · 준비되지 않음"}`, note.id);
      option.disabled = !ready;
      select.append(option);
    }
    // A saved snapshot remains valid even when its note is hidden later.
    if (selected && !notes.some(note => note.id === selected)) {
      select.append(new Option("기존에 선택한 노트", selected));
    }
    select.value = selected;
    status.textContent = notes.length ? "" : "등록된 노트 없음";
  } catch (error) {
    if (recordingId !== app.recordingId) return;
    status.textContent = "목록을 불러오지 못함";
    showToast(error.message, "error");
  }
}
$("#dockNoteRefresh").addEventListener("click", () => void loadRecordingNotes());
$("#dockNoteSelect").addEventListener("change", () => {
  const select = $("#dockNoteSelect");
  const recordingId = app.recordingId;
  const selected = select.value;
  $("#dockNoteStatus").textContent = "저장 중";
  select.disabled = true;
  recordingNoteSave = recordingNoteSave.then(async () => {
    try {
      await requestJson(`/api/recordings/${recordingId}/lecture-note`, {
        method: "PATCH", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({lecture_note_id: selected || null}),
      });
      recordingNoteSaved = selected;
      $("#dockNoteStatus").textContent = "저장됨";
    } catch (error) {
      select.value = recordingNoteSaved;
      $("#dockNoteStatus").textContent = "저장 실패";
      showToast(`강의노트 선택을 저장하지 못했습니다: ${error.message}`, "error");
    } finally {
      select.disabled = !["recording", "paused"].includes(app.recordingState);
    }
  });
});

async function startDockRecording() {
  if (!app.courses.length || app.recordingState !== "idle") return;
  ui.dockCoursePicker.removeAttribute("open");
  setRecorderState("starting");
  try {
    await flushCourseSave({requireSaved: true});

  } catch (error) {
    setRecorderState("idle");
    showToast(error.message, "error");
    return;
  }
  app.recordingTitle = automaticTitle();
  app.recordingCourseId = app.activeCourseId;
  ui.dockRecordingTitle.textContent = app.recordingTitle;
  ui.dockRecordingSession.dataset.sync = "saved";
  ui.dockRecordingSession.removeAttribute("title");
  setRecorderState("starting");
  try {
    app.mediaStream = await acquireMicrophone();
    const preferred = preferredRecordingMimeType();
    const recorderOptions = {audioBitsPerSecond: 128000};
    if (preferred) recorderOptions.mimeType = preferred;
    app.mediaRecorder = new MediaRecorder(app.mediaStream, recorderOptions);
    const mimeType = app.mediaRecorder.mimeType || preferred || "audio/webm";
    const extension = recordingExtension(mimeType);
    const prepared = await requestJsonBeforeDeadline("/api/recordings", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({title: app.recordingTitle, extension}),
    });
    app.recordingId = prepared.recording.id;
    app.recordingTitle = prepared.recording.title;
    app.chunkIndex = 0;
    app.chunkUploadChain = Promise.resolve();
    app.failedChunks = [];
    try {
      await putRecordingSession({
        recordingId: app.recordingId,
        title: app.recordingTitle,
        extension,
        durationSeconds: 0,
        nextChunkIndex: 0,
      });
    } catch (_) {
      showToast("브라우저 영구 저장소를 사용할 수 없어, 서버 중단 시 탭을 유지해야 합니다.", "error");
    }
    app.mediaRecorder.addEventListener("dataavailable", (event) => queueRecordingChunk(event.data));
    app.mediaRecorder.addEventListener("stop", async () => {
      stopMediaStream();
      setRecorderState("finalizing");
      try {
        await finalizeRecording();
      } catch (error) {
        showToast(`${error.message} · 보관함에서 남은 녹음을 복구할 수 있습니다.`, "error");
        app.recordingId = null;
      } finally {
        app.mediaRecorder = null;
        app.recordingTitle = "";
        app.recordingCourseId = null;
        setRecorderState("idle");
      }
    }, {once: true});
    app.mediaRecorder.start(5000);
    startSoundMeter(app.mediaStream);
    app.recordingStartedAt = Date.now();
    app.pausedAt = 0;
    app.pausedDuration = 0;
    ui.dockRecordingTitle.textContent = app.recordingTitle;
    updateClock();
    app.clockTimer = window.setInterval(updateClock, 250);
    setRecorderState("recording");
    recordingNoteSaved = "";
    $("#dockNoteSelect").replaceChildren(new Option("사용 안 함", ""));
    void loadRecordingNotes();
  } catch (error) {
    stopMediaStream();
    if (app.recordingId) {
      const abandoned = app.recordingId;
      app.recordingId = null;
      void fetch(`/api/recordings/${abandoned}`, {method: "DELETE"});
      void deleteRecordingRecovery(abandoned).catch(() => {});
    }
    app.mediaRecorder = null;
    app.recordingTitle = "";
    app.recordingCourseId = null;
    setRecorderState("idle");
    showToast(error.name === "NotAllowedError" ? "마이크 권한이 필요합니다." : `녹음을 시작하지 못했습니다: ${error.message}`, "error");
  }
}

function stopDockRecording() {
  if (!app.mediaRecorder || app.mediaRecorder.state === "inactive") return;
  ui.dockPauseButton.disabled = true;
  setRecorderState("finalizing");
  app.mediaRecorder.stop();
}

ui.dockRecordButton.addEventListener("click", () => {
  if (["recording", "paused"].includes(app.recordingState)) stopDockRecording();
  else void startDockRecording();
});

ui.dockPauseButton.addEventListener("click", () => {
  if (!app.mediaRecorder) return;
  if (app.mediaRecorder.state === "recording") {
    app.mediaRecorder.pause();
    app.pausedAt = Date.now();
    setRecorderState("paused");
  } else if (app.mediaRecorder.state === "paused") {
    app.pausedDuration += Date.now() - app.pausedAt;
    app.pausedAt = 0;
    app.mediaRecorder.resume();
    setRecorderState("recording");
  }
});

window.addEventListener("beforeunload", (event) => {
  if (!app.mediaRecorder || app.mediaRecorder.state === "inactive") return;
  event.preventDefault();
  event.returnValue = "";
});

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden" && app.mediaRecorder?.state === "recording") {
    try { app.mediaRecorder.requestData(); } catch (_) {}
  }
});

function formatFileSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function isInternalStorageFile(name) {
  const value = String(name || "");
  return value.startsWith(".") || /\.playback-[a-f0-9]{8,}(?:\.[^.]+)?$/i.test(value);
}

function visibleStorageItems(items) {
  return (items || []).map((item) => {
    const files = (item.files || []).filter((name) => !isInternalStorageFile(name));
    const audioFiles = (item.audio_files || []).filter((name) => !isInternalStorageFile(name));
    const markdownFiles = (item.markdown_files || []).filter((name) => !isInternalStorageFile(name));
    if (!files.length) return null;
    return {...item, files, audio_files: audioFiles, markdown_files: markdownFiles};
  }).filter(Boolean);
}

function renderStorage(data) {
  const items = visibleStorageItems(data.items);
  const fingerprint = JSON.stringify({exists: data.exists, items});
  if (fingerprint === app.storageFingerprint) return;
  app.storageFingerprint = fingerprint;
  app.storageItems = items;
  renderLibrary();
  renderDashboard();
}

function storageFileUrl(name) {
  return `/api/storage/file?name=${encodeURIComponent(name)}`;
}

async function revealStorageFile(name) {
  try {
    await requestJson("/api/storage/reveal", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({name}),
    });
    showToast("Finder에서 파일을 표시했습니다.", "success");
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function refreshStorage() {
  try {
    renderStorage(await requestJson(`/api/storage?time=${Date.now()}`, {cache: "no-store"}));
  } catch (error) {
    app.storageItems = [];
    app.storageFingerprint = "";
    renderLibrary();
    renderDashboard();
    if (app.activeView === "recordings") showToast(error.message, "error");
  }
}

function fileName(path) {
  return String(path || "").split(/[\\/]/).pop() || "";
}

function storageCoverage(storage) {
  return {
    hasAudio: Boolean(storage?.audio_files?.length),
    hasMarkdown: Boolean(storage?.markdown_files?.length),
  };
}

function recordingCategory(recording, storage) {
  const counts = recording.suggestion_counts || {};
  if (["recording", "recoverable", "queued", "processing", "cancelling"].includes(recording.status)) return "active";
  if (recording.status === "completed" && recording.error) return "problem";
  const coverage = storageCoverage(storage);
  if (recording.status === "completed" && (!coverage.hasAudio || !coverage.hasMarkdown)) return "problem";
  if (recording.status === "completed" && Number(counts.pending) > 0) return "review";
  if (["failed", "cancelled"].includes(recording.status)) return "problem";
  return "completed";
}

function deriveLibraryEntries() {
  const storageByFile = new Map();
  app.storageItems.forEach((item) => item.files.forEach((name) => storageByFile.set(name, item)));
  const matched = new Set();
  const entries = app.recordings.map((recording) => {
    const storage = storageByFile.get(fileName(recording.audio_path)) || storageByFile.get(fileName(recording.note_path)) || null;
    if (storage) matched.add(storage);
    return {
      key: `job:${recording.id}`,
      kind: "job",
      recording,
      storage,
      category: recordingCategory(recording, storage),
      timestamp: recording.completed_at || recording.created_at || "",
    };
  });
  app.storageItems.forEach((storage) => {
    if (!matched.has(storage)) entries.push({
      key: `file:${storage.name}`,
      kind: "file",
      storage,
      recording: null,
      category: "problem",
      timestamp: storage.modified_at || "",
    });
  });
  return entries;
}

const libraryNameCollator = new Intl.Collator("ko", {numeric: true, sensitivity: "base"});

function libraryEntryName(entry) {
  return String(entry.recording?.title || entry.storage?.name || "");
}

function sortLibraryEntries(entries, order = "name-asc") {
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

function libraryRoute(entry) {
  return entry.kind === "job"
    ? `#/recordings/job/${encodeURIComponent(entry.recording.id)}`
    : `#/recordings/file/${encodeURIComponent(entry.storage.name)}`;
}

function libraryState(entry) {
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

function renderLibrary() {
  app.libraryEntries = sortLibraryEntries(deriveLibraryEntries(), ui.librarySort.value);
  const query = ui.librarySearch.value.trim().toLocaleLowerCase("ko");
  const filter = ui.libraryFilter.value;
  const visible = app.libraryEntries.filter((entry) => {
    const recording = entry.recording;
    const text = `${recording?.title || entry.storage.name} ${recording?.course_name || ""}`.toLocaleLowerCase("ko");
    return (!query || text.includes(query)) && (filter === "all" || entry.category === filter);
  });
  ui.libraryList.replaceChildren();
  const attention = app.libraryEntries.filter((entry) => ["review", "problem"].includes(entry.category)).length;
  ui.libraryBadge.hidden = attention === 0;
  ui.libraryBadge.textContent = String(attention);
  if (!visible.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = app.libraryEntries.length ? "조건에 맞는 녹음이 없습니다." : "아직 보관된 녹음이 없습니다.";
    ui.libraryList.append(empty);
  }
  visible.forEach((entry) => {
    const link = document.createElement("a");
    link.className = `library-row${entry.key === app.selectedLibraryKey ? " active" : ""}`;
    link.href = libraryRoute(entry);
    const symbol = document.createElement("span");
    symbol.className = "library-symbol";
    const coverage = storageCoverage(entry.storage);
    symbol.textContent = entry.category === "active" ? "···" : coverage.hasAudio && coverage.hasMarkdown ? "SET" : coverage.hasAudio ? "A" : coverage.hasMarkdown ? "MD" : "REC";
    symbol.setAttribute("aria-label", coverage.hasAudio && coverage.hasMarkdown ? "오디오와 Markdown" : coverage.hasAudio ? "오디오" : coverage.hasMarkdown ? "Markdown" : "작업 기록");
    const copy = document.createElement("span");
    copy.className = "library-copy";
    const title = document.createElement("strong");
    title.textContent = entry.recording?.title || entry.storage.name;
    const meta = document.createElement("small");
    meta.textContent = `${entry.recording?.course_name || "강의 기록 없음"} · ${shortDate(entry.timestamp)}`;
    copy.append(title, meta);
    const state = document.createElement("span");
    state.className = "library-state";
    state.textContent = libraryState(entry);
    link.append(symbol, copy, state);
    ui.libraryList.append(link);
  });
  const selected = app.libraryEntries.find((entry) => entry.key === app.selectedLibraryKey);
  if (app.selectedLibraryKey && !selected) {
    app.selectedLibraryKey = null;
    if (app.activeView === "recordings") window.history.replaceState(null, "", "#/recordings");
  }
  ui.workspacePanels.find((panel) => panel.dataset.viewPanel === "recordings")?.classList.toggle("detail-open", Boolean(selected));
  // Polls for other recordings must not replace the open document or audio player.
  if (selected && app.previewEntryFingerprint === JSON.stringify(selected)) return;
  if (selected) renderLibraryDetail(selected);
  else renderLibraryEmpty();
}

function renderLibraryEmpty() {
  app.previewAudioPlayer?.destroy();
  app.previewAudioPlayer = null;
  app.previewEntryFingerprint = null;
  ui.libraryDetail.innerHTML = '<div class="detail-empty"><svg viewBox="0 0 32 32" aria-hidden="true"><path d="M5 9h8l2 3h12v14H5zM5 12h22"/></svg><h2>녹음을 선택하세요</h2><p>원음, Markdown, 수정 제안과 처리 기록을 한곳에서 볼 수 있습니다.</p></div>';
}

function detailSection(title) {
  const section = document.createElement("section");
  section.className = "detail-section";
  const heading = document.createElement("h3");
  heading.textContent = title;
  section.append(heading);
  return section;
}

function playerTime(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const remainder = total % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
}

function createAudioPlayer(source, label = "녹음", expectedDuration = 0, storageName = "") {
  const audio = document.createElement("audio");
  audio.preload = "auto";
  audio.playsInline = true;
  audio.preservesPitch = true;

  const player = document.createElement("section");
  player.className = "audio-player";
  player.tabIndex = 0;
  player.setAttribute("aria-label", `${label} 오디오 플레이어`);
  const play = document.createElement("button");
  play.type = "button";
  play.className = "audio-play";
  play.innerHTML = '<svg class="audio-play-symbol" viewBox="0 0 24 24" aria-hidden="true"><path d="m9 7 8 5-8 5z"/></svg>';
  play.setAttribute("aria-label", "재생");
  const rewind = document.createElement("button");
  rewind.type = "button";
  rewind.className = "audio-skip";
  rewind.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7.2 4.5v4h-4M7 8.3a7.5 7.5 0 1 1-2 7.7"/><text x="12" y="12">5</text></svg>';
  rewind.setAttribute("aria-label", "5초 뒤로");
  const forward = document.createElement("button");
  forward.type = "button";
  forward.className = "audio-skip";
  forward.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M16.8 4.5v4h4M17 8.3a7.5 7.5 0 1 0 2 7.7"/><text x="12" y="12">5</text></svg>';
  forward.setAttribute("aria-label", "5초 앞으로");
  const status = document.createElement("span");
  status.className = "audio-load-state sr-only";
  status.textContent = "불러오는 중";
  status.setAttribute("role", "status");

  const timeline = document.createElement("div");
  timeline.className = "audio-timeline";
  const elapsed = document.createElement("time");
  elapsed.textContent = "00:00:00";
  const scrubber = document.createElement("div");
  scrubber.className = "audio-scrubber";
  const buffered = document.createElement("span");
  buffered.className = "audio-buffered";
  const played = document.createElement("span");
  played.className = "audio-played";
  const range = document.createElement("input");
  range.type = "range";
  range.min = "0";
  range.max = "1";
  range.step = "0.05";
  range.value = "0";
  range.setAttribute("aria-label", "재생 위치");
  const duration = document.createElement("time");
  duration.textContent = "––:––:––";
  scrubber.append(buffered, played, range);
  timeline.append(play, elapsed, scrubber, duration, rewind, forward);
  player.append(audio, status, timeline);

  let pendingSeek = null;
  let pendingPlay = false;
  let loadError = false;
  let destroyed = false;
  let sourceReady = false;

  const knownDuration = () => Number.isFinite(audio.duration) && audio.duration > 0
    ? audio.duration
    : Math.max(0, Number(expectedDuration) || 0);
  const updateTimeline = () => {
    const total = knownDuration();
    const current = Math.max(0, Number(audio.currentTime) || pendingSeek || 0);
    const maximum = total || Math.max(current, pendingSeek || 0, 1);
    range.max = String(maximum);
    range.value = String(Math.min(current, maximum));
    elapsed.textContent = playerTime(current);
    duration.textContent = total ? playerTime(total) : "––:––:––";
    played.style.width = `${Math.min(100, current / maximum * 100)}%`;
    let bufferedEnd = 0;
    if (audio.buffered.length) bufferedEnd = audio.buffered.end(audio.buffered.length - 1);
    buffered.style.width = `${Math.min(100, bufferedEnd / maximum * 100)}%`;
  };
  const setPlaybackState = () => {
    const playing = !audio.paused && !audio.ended;
    play.innerHTML = playing
      ? '<svg class="audio-pause-symbol" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 7v10M15 7v10"/></svg>'
      : '<svg class="audio-play-symbol" viewBox="0 0 24 24" aria-hidden="true"><path d="m9 7 8 5-8 5z"/></svg>';
    play.setAttribute("aria-label", playing ? "일시정지" : "재생");
    player.classList.toggle("playing", playing);
  };
  const commitPendingSeek = () => {
    if (pendingSeek === null || audio.readyState < HTMLMediaElement.HAVE_METADATA) return false;
    const total = knownDuration();
    const target = total ? Math.min(pendingSeek, total) : pendingSeek;
    try {
      audio.currentTime = Math.max(0, target);
      pendingSeek = null;
      updateTimeline();
      return true;
    } catch (_) {
      return false;
    }
  };
  const requestPlay = () => {
    pendingPlay = true;
    status.textContent = "재생 준비 중";
    if (!sourceReady) return;
    void audio.play().catch((error) => {
      if (error?.name === "AbortError") return;
      pendingPlay = false;
      status.textContent = "재생할 수 없음";
      showToast("오디오를 재생하지 못했습니다. 파일 형식이나 파일 상태를 확인해 주세요.", "error");
    });
  };
  const seekTo = (seconds, {autoplay = false, reveal = false} = {}) => {
    pendingSeek = Math.max(0, Number(seconds) || 0);
    commitPendingSeek();
    updateTimeline();
    if (autoplay) requestPlay();
    if (sourceReady && audio.readyState === HTMLMediaElement.HAVE_NOTHING) audio.load();
    if (reveal) player.scrollIntoView({block: "nearest", behavior: "smooth"});
  };
  const skip = (difference) => {
    const origin = pendingSeek ?? audio.currentTime ?? 0;
    seekTo(origin + difference, {autoplay: !audio.paused || pendingPlay});
  };

  play.addEventListener("click", () => {
    if (audio.paused || audio.ended) requestPlay();
    else audio.pause();
  });
  rewind.addEventListener("click", () => skip(-5));
  forward.addEventListener("click", () => skip(5));
  range.addEventListener("input", () => seekTo(Number(range.value)));
  player.addEventListener("keydown", (event) => {
    if (["INPUT", "BUTTON", "SELECT"].includes(event.target.tagName)) return;
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      skip(event.key === "ArrowLeft" ? -5 : 5);
    }
  });
  ["loadedmetadata", "durationchange", "progress", "canplay"].forEach((name) => {
    audio.addEventListener(name, () => {
      commitPendingSeek();
      updateTimeline();
      if (name === "canplay" && pendingPlay && audio.paused) requestPlay();
      if (!loadError && audio.readyState >= HTMLMediaElement.HAVE_FUTURE_DATA) {
        status.textContent = audio.ended ? "재생 완료" : audio.paused ? "준비됨" : "재생 중";
      }
    });
  });
  audio.addEventListener("timeupdate", updateTimeline);
  audio.addEventListener("seeking", () => { status.textContent = "이동 중"; });
  audio.addEventListener("seeked", () => { status.textContent = audio.paused ? "준비됨" : "재생 중"; });
  audio.addEventListener("waiting", () => { status.textContent = "버퍼링 중"; player.classList.add("buffering"); });
  audio.addEventListener("playing", () => {
    pendingPlay = false;
    status.textContent = "재생 중";
    player.classList.remove("buffering");
    setPlaybackState();
  });
  audio.addEventListener("pause", () => {
    if (!loadError && !audio.ended) status.textContent = "일시정지";
    setPlaybackState();
  });
  audio.addEventListener("ended", () => { status.textContent = "재생 완료"; setPlaybackState(); });
  audio.addEventListener("error", () => {
    loadError = true;
    pendingPlay = false;
    status.textContent = "오디오 오류";
    player.classList.add("error");
    play.disabled = true;
    rewind.disabled = true;
    forward.disabled = true;
    range.disabled = true;
  });
  updateTimeline();
  void (async () => {
    if (storageName) {
      status.textContent = "재생 정보 확인 중";
      try {
        const prepared = await requestJson("/api/storage/audio/prepare", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({name: storageName}),
        });
        if (prepared.duration > 0 && !expectedDuration) expectedDuration = prepared.duration;
      } catch (_) {
        // Older servers and files that cannot be remuxed still use the original source.
      }
    }
    if (destroyed) return;
    audio.src = source;
    sourceReady = true;
    status.textContent = "불러오는 중";
    updateTimeline();
    audio.load();
    if (pendingPlay) requestPlay();
  })();

  return {
    audio,
    element: player,
    seekTo,
    destroy() {
      destroyed = true;
      audio.pause();
      audio.removeAttribute("src");
      audio.load();
    },
  };
}

function markdownTimestamp(target) {
  const match = String(target || "").match(/#t=(\d+(?:\.\d+)?)/i);
  return match ? Number(match[1]) : null;
}

function seekPreviewAudio(player, seconds) {
  if (!player) {
    showToast("연결된 오디오 파일이 없어 이 타임스탬프를 재생할 수 없습니다.", "error");
    return;
  }
  player.seekTo(seconds, {autoplay: true, reveal: false});
}

function appendMarkdownInline(container, source, audio) {
  const pattern = /(\[([^\]]+)\]\(([^)]+)\)|\*\*([^*]+)\*\*|`([^`]+)`|\*([^*]+)\*)/g;
  let cursor = 0;
  for (const match of source.matchAll(pattern)) {
    container.append(document.createTextNode(source.slice(cursor, match.index)));
    if (match[2] !== undefined) {
      const target = match[3].trim();
      const seconds = markdownTimestamp(target);
      const localAudio = /(?:^|\/)[^/]+\.(?:aac|aiff?|caf|flac|m4a|mp3|mp4|ogg|opus|wav|webm)(?:#|$)/i.test(target);
      if (seconds !== null || localAudio) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = seconds !== null ? "markdown-timestamp" : "markdown-audio-link";
        button.textContent = match[2];
        button.setAttribute("aria-label", seconds !== null ? `${match[2]}부터 오디오 재생` : "연결된 오디오 처음부터 재생");
        button.disabled = !audio;
        if (!audio) button.title = "연결된 오디오 파일이 없습니다.";
        button.addEventListener("click", () => seekPreviewAudio(audio, seconds ?? 0));
        container.append(button);
      } else if (/^https?:\/\//i.test(target)) {
        const link = document.createElement("a");
        link.href = target;
        link.target = "_blank";
        link.rel = "noreferrer";
        link.textContent = match[2];
        container.append(link);
      } else {
        container.append(document.createTextNode(match[2]));
      }
    } else {
      const element = document.createElement(match[4] !== undefined ? "strong" : match[5] !== undefined ? "code" : "em");
      element.textContent = match[4] ?? match[5] ?? match[6];
      container.append(element);
    }
    cursor = match.index + match[0].length;
  }
  container.append(document.createTextNode(source.slice(cursor)));
}

function markdownMetadata(frontmatter) {
  const labels = {
    course: "강의",
    recorded_at: "녹음 시각",
    duration: "길이",
    language: "언어",
    speech_model: "음성 인식",
    review_model: "문맥 검수",
    audio: "오디오",
  };
  const entries = frontmatter.split(/\r?\n/).map((line) => {
    const match = line.match(/^([\w-]+):\s*(.*)$/);
    if (!match || !labels[match[1]] || !match[2]) return null;
    let value = match[2];
    if (value.startsWith('"') && value.endsWith('"')) {
      try { value = JSON.parse(value); } catch (_) { value = value.slice(1, -1); }
    }
    return [labels[match[1]], value];
  }).filter(Boolean);
  if (!entries.length) return null;
  const details = document.createElement("details");
  details.className = "markdown-metadata";
  const summary = document.createElement("summary");
  summary.textContent = "문서 정보";
  const list = document.createElement("dl");
  entries.forEach(([label, value]) => {
    const term = document.createElement("dt");
    term.textContent = label;
    const description = document.createElement("dd");
    description.textContent = value;
    list.append(term, description);
  });
  details.append(summary, list);
  return details;
}

function renderMarkdownInto(container, source, audio) {
  container.replaceChildren();
  let markdown = String(source || "").replace(/\r\n/g, "\n");
  const frontmatter = markdown.match(/^---\n([\s\S]*?)\n---(?:\n|$)/);
  if (frontmatter) {
    const metadata = markdownMetadata(frontmatter[1]);
    if (metadata) container.append(metadata);
    markdown = markdown.slice(frontmatter[0].length);
  }
  const lines = markdown.split("\n");
  for (let index = 0; index < lines.length;) {
    const line = lines[index];
    if (!line.trim()) { index += 1; continue; }
    const fence = line.match(/^```\s*([\w-]*)/);
    if (fence) {
      const code = [];
      index += 1;
      while (index < lines.length && !lines[index].startsWith("```")) code.push(lines[index++]);
      index += 1;
      const pre = document.createElement("pre");
      const element = document.createElement("code");
      element.textContent = code.join("\n");
      if (fence[1]) element.dataset.language = fence[1];
      pre.append(element);
      container.append(pre);
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      const element = document.createElement(`h${Math.min(heading[1].length + 1, 6)}`);
      appendMarkdownInline(element, heading[2], audio);
      container.append(element);
      index += 1;
      continue;
    }
    const listMatch = line.match(/^\s*(?:[-*+]|(\d+)\.)\s+(.+)$/);
    if (listMatch) {
      const ordered = Boolean(listMatch[1]);
      const list = document.createElement(ordered ? "ol" : "ul");
      while (index < lines.length) {
        const item = lines[index].match(/^\s*(?:[-*+]|(\d+)\.)\s+(.+)$/);
        if (!item || Boolean(item[1]) !== ordered) break;
        const listItem = document.createElement("li");
        appendMarkdownInline(listItem, item[2], audio);
        list.append(listItem);
        index += 1;
      }
      container.append(list);
      continue;
    }
    if (/^\s*>/.test(line)) {
      const quote = document.createElement("blockquote");
      const content = [];
      while (index < lines.length && /^\s*>/.test(lines[index])) content.push(lines[index++].replace(/^\s*>\s?/, ""));
      appendMarkdownInline(quote, content.join(" "), audio);
      container.append(quote);
      continue;
    }
    if (/^\s*(?:-{3,}|\*{3,})\s*$/.test(line)) {
      container.append(document.createElement("hr"));
      index += 1;
      continue;
    }
    const paragraph = [];
    while (index < lines.length && lines[index].trim() && !/^(?:#{1,6}\s|```|\s*(?:[-*+] |\d+\. |>|-{3,}\s*$|\*{3,}\s*$))/.test(lines[index])) {
      paragraph.push(lines[index++].trim());
    }
    if (!paragraph.length) { index += 1; continue; }
    const element = document.createElement("p");
    appendMarkdownInline(element, paragraph.join(" "), audio);
    container.append(element);
  }
}

function markdownDisclosureSummary(hasAudio) {
  const summary = document.createElement("summary");
  const icon = document.createElement("span");
  icon.className = "markdown-disclosure-icon";
  icon.setAttribute("aria-hidden", "true");
  icon.textContent = "›";
  const copy = document.createElement("span");
  const title = document.createElement("strong");
  title.textContent = "전사 내용 미리보기";
  const description = document.createElement("small");
  description.textContent = hasAudio
    ? "타임스탬프를 눌러 해당 위치부터 재생할 수 있습니다."
    : "오디오 파일이 없어 타임스탬프 재생은 사용할 수 없습니다.";
  copy.append(title, description);
  summary.append(icon, copy);
  return summary;
}

function storageFileRow(kind, names) {
  const row = document.createElement("div");
  row.className = `storage-file-row${names.length ? "" : " missing"}`;
  const badge = document.createElement("span");
  badge.className = "storage-file-kind";
  badge.textContent = kind === "audio" ? "AUDIO" : "MD";
  const copy = document.createElement("span");
  copy.className = "storage-file-copy";
  const label = document.createElement("strong");
  label.textContent = kind === "audio" ? "오디오 원본" : "Markdown 결과";
  const description = document.createElement("small");
  description.textContent = names.length ? names.join(", ") : "파일 없음";
  copy.append(label, description);
  const actions = document.createElement("span");
  actions.className = "storage-file-actions";
  names.forEach((name) => {
    const reveal = document.createElement("button");
    reveal.type = "button";
    reveal.textContent = "Finder";
    reveal.setAttribute("aria-label", `${name} Finder에서 보기`);
    reveal.addEventListener("click", () => revealStorageFile(name));
    actions.append(reveal);
  });
  row.append(badge, copy, actions);
  return row;
}

function detailMenuButton(label, action, {danger = false} = {}) {
  const button = document.createElement("button");
  button.type = "button";
  button.setAttribute("role", "menuitem");
  button.textContent = label;
  if (danger) button.className = "danger";
  button.addEventListener("click", (event) => {
    event.currentTarget.closest("details")?.removeAttribute("open");
    action(button);
  });
  return button;
}

function beginEntryTitleEdit(entry, title, menu) {
  const recording = entry.recording;
  const form = document.createElement("form");
  form.className = "detail-title-editor";
  const input = document.createElement("input");
  input.type = "text";
  input.maxLength = 160;
  input.value = recording?.title || entry.storage.name;
  input.setAttribute("aria-label", recording ? "녹음 제목" : "파일 제목");
  const save = document.createElement("button");
  save.type = "submit";
  save.textContent = "저장";
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.textContent = "취소";
  cancel.addEventListener("click", () => {
    form.replaceWith(title);
    menu.querySelector("summary")?.focus();
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const nextTitle = input.value.trim();
    if (!nextTitle) {
      input.setCustomValidity("녹음 제목을 입력해 주세요.");
      input.reportValidity();
      return;
    }
    input.setCustomValidity("");
    save.disabled = true;
    try {
      if (recording) {
        const data = await requestJson(`/api/recordings/${encodeURIComponent(recording.id)}`, {
          method: "PATCH",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({title: nextTitle}),
        });
        renderRecordings(app.recordings.map((item) => item.id === recording.id ? data.recording : item));
        app.storageFingerprint = "";
        await refreshStorage();
      } else {
        const data = await requestJson("/api/storage/files", {
          method: "PATCH",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({title: nextTitle, names: entry.storage.files}),
        });
        app.selectedLibraryKey = `file:${data.name}`;
        app.storageFingerprint = "";
        await refreshStorage();
        window.history.replaceState(null, "", `#/recordings/file/${encodeURIComponent(data.name)}`);
      }
      showToast(recording ? "녹음 제목과 파일 이름을 수정했습니다." : "파일 제목을 수정했습니다.", "success");
    } catch (error) {
      save.disabled = false;
      input.setCustomValidity(error.message);
      input.reportValidity();
    }
  });
  form.append(input, save, cancel);
  title.replaceWith(form);
  input.focus();
  input.select();
}

async function deleteEntryWithFiles(entry, button) {
  const title = entry.recording?.title || entry.storage?.name || "선택한 항목";
  const fileCount = entry.storage?.files?.length || [entry.recording?.audio_path, entry.recording?.note_path].filter(Boolean).length;
  const confirmed = await confirmAction(
    `‘${title}’의 ${fileCount ? `오디오·Markdown 파일 ${fileCount}개와 ` : ""}${entry.recording ? "작업 기록을 " : "파일을 "}완전히 삭제할까요? 이 작업은 되돌릴 수 없습니다.`,
    "파일까지 삭제",
    "모두 삭제",
  );
  if (!confirmed) return;
  button.disabled = true;
  try {
    if (entry.recording) {
      await requestJson(`/api/recordings/${encodeURIComponent(entry.recording.id)}/with-files`, {method: "DELETE"});
      if (app.reviewingRecordingId === entry.recording.id) app.reviewingRecordingId = null;
      renderRecordings(app.recordings.filter((item) => item.id !== entry.recording.id));
    } else {
      await requestJson("/api/storage/files", {
        method: "DELETE",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({names: entry.storage.files}),
      });
      app.storageFingerprint = "";
      await refreshStorage();
    }
    app.selectedLibraryKey = null;
    window.location.hash = "#/recordings";
    showToast(entry.recording ? "작업 기록과 연결된 파일을 삭제했습니다." : "저장 파일을 삭제했습니다.", "success");
  } catch (error) {
    button.disabled = false;
    showToast(error.message, "error");
  }
}

function createDetailMenu(entry, title) {
  const menu = document.createElement("details");
  menu.className = "detail-menu";
  const trigger = document.createElement("summary");
  trigger.textContent = "•••";
  trigger.setAttribute("aria-label", `${entry.recording?.title || entry.storage.name} 작업 메뉴`);
  const panel = document.createElement("div");
  panel.className = "detail-menu-panel";
  panel.setAttribute("role", "menu");
  const recording = entry.recording;
  const final = recording && ["completed", "failed", "cancelled"].includes(recording.status);
  const interrupted = recording && ["recording", "recoverable"].includes(recording.status) && recording.id !== app.recordingId;
  if (final || !recording) panel.append(detailMenuButton("수정", () => beginEntryTitleEdit(entry, title, menu)));
  if (hasPipelineLog(recording)) {
    panel.append(detailMenuButton("로그 보기", (button) => openPipelineLog(recording, button)));
  }
  if (recording && ["queued", "processing"].includes(recording.status)) {
    panel.append(detailMenuButton("처리 중단", (button) => cancelTranscription(recording, button)));
  }
  if (recording?.status === "completed" && !["queued","processing"].includes(recording.review_status)) {
    panel.append(detailMenuButton("검수 다시 하기", async () => {
      await requestJson(`/api/recordings/${recording.id}/review`, {method:"POST"});
      await refreshStatus();
    }));
  }
  if (final) {
    panel.append(detailMenuButton("다시 전사하기", (button) => retryRecording(recording, button)));
  } else if (interrupted) {
    panel.append(detailMenuButton("녹음 복구", (button) => retryRecording(recording, button)));
  }
  if (final || interrupted) panel.append(detailMenuButton("기록 삭제", (button) => deleteRecordingHistory(recording, button), {danger: true}));
  if (final || (!recording && entry.storage?.files?.length)) {
    panel.append(detailMenuButton("파일까지 삭제", (button) => deleteEntryWithFiles(entry, button), {danger: true}));
  }
  menu.append(trigger, panel);
  return menu;
}

function renderLibraryDetail(entry) {
  stopReviewAudio();
  app.previewAudioPlayer?.destroy();
  app.previewAudioPlayer = null;
  app.previewEntryFingerprint = JSON.stringify(entry);
  ui.libraryDetail.replaceChildren();
  const back = document.createElement("a");
  back.className = "detail-mobile-back";
  back.href = "#/recordings";
  back.textContent = "‹ 보관함";
  const header = document.createElement("header");
  header.className = "detail-header";
  const title = document.createElement("h2");
  title.textContent = entry.recording?.title || entry.storage.name;
  const titleRow = document.createElement("div");
  titleRow.className = "detail-title-row";
  titleRow.append(title, createDetailMenu(entry, title));
  const meta = document.createElement("p");
  meta.textContent = entry.recording
    ? `${entry.recording.course_name} · ${workTimeText(entry.recording)}`
    : `${shortDate(entry.storage.modified_at)} · DB 작업 기록 없이 저장 폴더에서 발견됨`;
  const status = document.createElement("span");
  status.className = "detail-status";
  status.textContent = libraryState(entry);
  header.append(titleRow, meta, status);
  if (entry.recording?.review_status && entry.recording.review_status !== "none") {
    const review = document.createElement("p");
    review.textContent = ({queued:"검수 대기",processing:"검수 중",completed:"검수 완료",failed:"검수 실패"}[entry.recording.review_status] || "") + (entry.recording.review_error ? ` · ${entry.recording.review_error}` : "");
    header.append(review);
  }
  const hintWarning = entry.recording?.quality?.settings?.recognition_hint_warning;
  if (hintWarning) {
    const warning = document.createElement("p");
    warning.textContent = hintWarning;
    header.append(warning);
  }
  ui.libraryDetail.append(back, header);

  const files = detailSection("원음과 결과 파일");
  const fileList = document.createElement("div");
  fileList.className = "storage-file-list";
  fileList.append(
    storageFileRow("audio", entry.storage?.audio_files || []),
    storageFileRow("markdown", entry.storage?.markdown_files || []),
  );
  files.append(fileList);
  let detailAudioPlayer = null;
  if (entry.storage) {
    if (entry.storage.audio_files.length) {
      detailAudioPlayer = createAudioPlayer(
        storageFileUrl(entry.storage.audio_files[0]),
        entry.recording?.title || entry.storage.name,
        entry.recording?.duration_seconds || 0,
        entry.storage.audio_files[0],
      );
      app.previewAudioPlayer = detailAudioPlayer;
      files.append(detailAudioPlayer.element);
    }
    if (entry.storage.markdown_files.length) {
      const note = document.createElement("details");
      note.className = "detail-markdown";
      const summary = markdownDisclosureSummary(Boolean(detailAudioPlayer));
      const previewText = document.createElement("article");
      previewText.className = "rendered-markdown";
      previewText.textContent = "Markdown을 불러오는 중입니다.";
      note.append(summary, previewText);
      note.addEventListener("toggle", async () => {
        if (!note.open || note.dataset.loaded) return;
        note.dataset.loaded = "true";
        try {
          const response = await fetch(storageFileUrl(entry.storage.markdown_files[0]), {cache: "no-store"});
          if (!response.ok) throw new Error("Markdown을 불러오지 못했습니다.");
          renderMarkdownInto(previewText, await response.text(), detailAudioPlayer);
        } catch (error) { previewText.textContent = error.message; }
      });
      files.append(note);
    }
  } else {
    const missing = document.createElement("p");
    missing.className = "detail-note problem";
    missing.textContent = entry.recording?.status === "failed" && entry.recording.source_path
      ? "결과 파일은 없지만 재처리용 원본 음성은 앱 내부에 안전하게 보존돼 있습니다."
      : entry.recording && ["queued", "processing", "recording", "recoverable"].includes(entry.recording.status)
      ? "처리가 끝나면 결과 파일이 여기에 나타납니다."
      : "기록은 남아 있지만 저장 폴더에서 결과 파일을 찾지 못했습니다.";
    files.append(missing);
  }
  ui.libraryDetail.append(files);

  if (!entry.recording) return;
  const recording = entry.recording;
  if (recording.error && recording.status !== "completed") {
    const failure = detailSection(recording.status === "failed" ? "실패 원인" : "작업 기록");
    const message = document.createElement("p");
    message.className = "detail-note problem";
    message.textContent = recording.error;
    failure.append(message);
    ui.libraryDetail.append(failure);
  }
  const counts = recording.suggestion_counts || {};
  if (recording.status === "completed" && (Number(counts.pending) + Number(counts.accepted) + Number(counts.rejected) > 0 || recording.error)) {
    ui.reviewDialog.hidden = false;
    ui.libraryDetail.append(ui.reviewDialog);
    void openReview(recording.id, {focus: false});
  }
}

function dashboardRow(entry, trailing) {
  const link = document.createElement("a");
  link.className = "dashboard-row";
  link.href = libraryRoute(entry);
  const copy = document.createElement("span");
  const title = document.createElement("strong"); title.textContent = entry.recording?.title || entry.storage.name;
  const meta = document.createElement("small"); meta.textContent = entry.recording?.course_name || "파일만 존재";
  copy.append(title, meta);
  const state = document.createElement("span"); state.textContent = trailing;
  link.append(copy, state);
  return link;
}

function renderDashboard() {
  ui.dashboardDate.textContent = new Intl.DateTimeFormat("ko-KR", {year: "numeric", month: "long", day: "numeric", weekday: "long"}).format(new Date());
  const course = activeCourse();
  ui.dashboardCourseName.textContent = course?.name || "강의 없음";
  ui.dashboardCourseLink.href = courseRoute();
  const entries = deriveLibraryEntries();
  const activeOrder = {processing: 0, cancelling: 0, queued: 1, recoverable: 2, recording: 2};
  const active = entries.filter((entry) => entry.category === "active")
    .sort((left, right) => (activeOrder[left.recording.status] ?? 9) - (activeOrder[right.recording.status] ?? 9));
  const attention = entries.filter((entry) => ["review", "problem"].includes(entry.category))
    .sort((left, right) => {
      const priority = (entry) => entry.recording?.status === "failed" ? 0 : entry.recording?.status === "recoverable" ? 1 : entry.category === "review" ? 2 : 3;
      return priority(left) - priority(right);
    });
  const recent = sortLibraryEntries(
    entries.filter((entry) => entry.category === "completed"), "date-desc"
  ).slice(0, 5);
  const fill = (container, items, emptyText) => {
    container.replaceChildren();
    if (!items.length) {
      const empty = document.createElement("div"); empty.className = "empty-state"; empty.textContent = emptyText; container.append(empty); return;
    }
    items.forEach((entry) => container.append(dashboardRow(entry, libraryState(entry))));
  };
  fill(ui.dashboardQueue, active, app.courses.length ? "진행 중인 작업이 없습니다. 하단 도구에서 녹음하거나 파일을 가져올 수 있습니다." : "강의를 추가하면 하단 도구에서 녹음을 시작할 수 있습니다.");
  fill(ui.dashboardAttention, attention, "확인이 필요한 항목이 없습니다.");
  fill(ui.dashboardRecent, recent, "완성된 녹음이 아직 없습니다.");
}

ui.librarySearch.addEventListener("input", renderLibrary);
ui.libraryFilter.addEventListener("change", renderLibrary);
ui.librarySort.addEventListener("change", renderLibrary);

const SUPPORTED_UPLOAD_EXTENSIONS = new Set([
  ".3g2", ".3gp", ".aac", ".aif", ".aiff", ".alac", ".amr", ".caf", ".flac",
  ".m4a", ".mka", ".mov", ".mp3", ".mp4", ".ogg", ".opus", ".wav", ".webm", ".wma",
]);

function uploadExtension(file) {
  const position = file.name.lastIndexOf(".");
  return position >= 0 ? file.name.slice(position).toLocaleLowerCase("en") : "";
}

function uploadStem(file) {
  const extension = uploadExtension(file);
  const stem = extension ? file.name.slice(0, -extension.length) : file.name;
  return stem.replace(/[\\/:*?"<>|\u0000-\u001f]/g, "-").replace(/\s+/g, " ").trim().replace(/[. ]+$/g, "").slice(0, 160);
}

function setUploadTitlePreset() {
  if (!app.uploadTitleAutomatic) return;
  ui.uploadTitle.value = automaticTitle();
  ui.uploadTitle.setCustomValidity("");
}

function selectUploadFile(file) {
  if (!file || app.uploadInProgress) return;
  if (!SUPPORTED_UPLOAD_EXTENSIONS.has(uploadExtension(file))) {
    app.selectedFile = null;
    ui.uploadFile.value = "";
    ui.dropTitle.textContent = file.name;
    ui.dropDescription.textContent = "지원하지 않는 형식";
    ui.uploadMessage.className = "upload-message error";
    ui.uploadMessage.textContent = "지원하지 않는 오디오·동영상 형식입니다.";
    return;
  }
  app.selectedFile = file;
  ui.dropTitle.textContent = file.name;
  ui.dropDescription.textContent = `${formatFileSize(file.size)} · 클릭해서 변경`;
  if (app.uploadTitleAutomatic && !ui.uploadTitle.value.trim()) {
    ui.uploadTitle.value = uploadStem(file) || automaticTitle();
  }
  ui.uploadMessage.className = "upload-message";
  ui.uploadMessage.textContent = "";
}

function resetUploadSelection() {
  app.selectedFile = null;
  app.uploadTitleAutomatic = true;
  ui.uploadFile.value = "";
  ui.uploadTitle.value = "";
  ui.uploadTitle.setCustomValidity("");
  ui.dropTitle.textContent = "파일을 선택하거나 끌어놓으세요";
  ui.dropDescription.textContent = "오디오·동영상 파일";
  ui.dropZone.classList.remove("dragging");
  ui.uploadMessage.className = "upload-message";
  ui.uploadMessage.textContent = "";
}

function setUploadBusy(busy) {
  app.uploadInProgress = busy;
  for (const control of [ui.closeUploadButton, ui.cancelUploadButton, ui.uploadCourseSelect, ui.uploadFile, ui.dropZone, ui.uploadTitle, ui.uploadButton]) {
    control.disabled = busy;
  }
  renderCaptureControls();
}

function openUploadDialog() {
  if (!app.courses.length) {
    showToast("먼저 파일에 연결할 강의를 추가하세요.", "error");
    window.location.hash = "#/courses";
    return;
  }
  if (app.recordingState !== "idle") return;
  resetUploadSelection();
  renderCaptureControls();
  ui.uploadCourseSelect.value = String(app.activeCourseId);
  setUploadTitlePreset();
  ui.uploadDialog.showModal();
}

function closeUploadDialog() {
  if (!app.uploadInProgress && ui.uploadDialog.open) ui.uploadDialog.close();
}

ui.dockUploadButton.addEventListener("click", openUploadDialog);
ui.closeUploadButton.addEventListener("click", closeUploadDialog);
ui.cancelUploadButton.addEventListener("click", closeUploadDialog);
ui.uploadDialog.addEventListener("cancel", (event) => {
  if (app.uploadInProgress) event.preventDefault();
});
ui.uploadDialog.addEventListener("click", (event) => {
  if (event.target === ui.uploadDialog) closeUploadDialog();
});
ui.uploadDialog.addEventListener("close", () => {
  resetUploadSelection();
  ui.dockUploadButton.focus({preventScroll: true});
});
ui.uploadCourseSelect.addEventListener("change", async () => {
  const courseId = Number(ui.uploadCourseSelect.value);
  if (courseId) await selectUploadCourse(courseId);
});
ui.dropZone.addEventListener("click", () => ui.uploadFile.click());
ui.uploadFile.addEventListener("change", () => selectUploadFile(ui.uploadFile.files[0]));
["dragenter", "dragover"].forEach((name) => ui.dropZone.addEventListener(name, (event) => {
  event.preventDefault();
  ui.dropZone.classList.add("dragging");
}));
["dragleave", "drop"].forEach((name) => ui.dropZone.addEventListener(name, (event) => {
  event.preventDefault();
  ui.dropZone.classList.remove("dragging");
}));
ui.dropZone.addEventListener("drop", (event) => selectUploadFile(event.dataTransfer.files[0]));

ui.uploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (app.uploadInProgress) return;
  if (!app.selectedFile) {
    ui.uploadMessage.className = "upload-message error";
    ui.uploadMessage.textContent = "먼저 녹음 파일을 선택하세요.";
    return;
  }
  if (!ui.uploadTitle.value.trim()) {
    ui.uploadTitle.setCustomValidity("저장할 파일 이름을 입력하세요.");
    ui.uploadTitle.reportValidity();
    ui.uploadMessage.className = "upload-message error";
    ui.uploadMessage.textContent = "저장할 파일 이름을 입력하세요.";
    return;
  }
  let lectureNoteId;
  try {
    await flushCourseSave({requireSaved: true});
    lectureNoteId = await chooseNote(app.activeCourseId);
    if (lectureNoteId === undefined) return;
  } catch (error) {
    ui.uploadMessage.className = "upload-message error";
    ui.uploadMessage.textContent = error.message;
    return;
  }
  setUploadBusy(true);
  ui.uploadMessage.className = "upload-message";
  ui.uploadMessage.textContent = "파일을 안전하게 저장하는 중입니다.";
  const data = new FormData();
  data.append("file", app.selectedFile, app.selectedFile.name);
  data.append("title", ui.uploadTitle.value);
  if (lectureNoteId) data.append("lecture_note_id", lectureNoteId);
  try {
    const result = await requestJson("/api/upload", {method: "POST", body: data});
    renderRecordings([result.recording, ...app.recordings.filter((item) => item.id !== result.recording.id)]);
    await refreshStatus();
    await refreshStorage();
    setUploadBusy(false);
    ui.uploadDialog.close();
    showToast("파일을 전사 대기열에 추가했습니다.", "success", {
      label: "보관함에서 보기",
      run: () => { window.location.hash = `#/recordings/job/${encodeURIComponent(result.recording.id)}`; },
    });
  } catch (error) {
    ui.uploadMessage.className = "upload-message error";
    ui.uploadMessage.textContent = error.message;
  } finally {
    if (ui.uploadDialog.open) setUploadBusy(false);
  }
});

function renderEnvironment(environment) {
  app.environment = environment;
  ui.projectDir.value = environment.project_dir;
  ui.outputDir.value = environment.output_dir;
  const checks = [
    [ui.projectCheck, environment.checks.project, "프로젝트 확인됨", "app.py를 찾지 못함"],
    [ui.whisperCheck, environment.checks.whisper && environment.checks.model && environment.checks.vad_model, "서버·large-v3·VAD 확인됨", "빌드 또는 모델 확인 필요"],
    [ui.outputCheck, environment.checks.output, "폴더 확인됨", "폴더를 찾지 못함"],
  ];
  for (const [element, ok, yes, no] of checks) {
    element.className = ok ? "ok" : "bad";
    element.textContent = ok ? yes : no;
  }
}

ui.systemButton.addEventListener("click", () => {
  renderEnvironment(app.environment);
  ui.systemMessage.textContent = "";
  ui.systemDialog.showModal();
});
ui.closeSystemButton.addEventListener("click", () => ui.systemDialog.close());
ui.systemDialog.addEventListener("click", (event) => {
  if (event.target === ui.systemDialog) ui.systemDialog.close();
});

async function waitForServerRestart() {
  await new Promise((resolve) => window.setTimeout(resolve, 1200));
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`/api/bootstrap?restart=${Date.now()}`, {cache: "no-store"});
      if (response.ok) {
        window.location.reload();
        return;
      }
    } catch (_) {
      // A connection failure is expected while the managed servers restart.
    }
    await new Promise((resolve) => window.setTimeout(resolve, 500));
  }
  ui.systemMessage.className = "error";
  ui.systemMessage.textContent = "자동 재시작을 확인하지 못했습니다. start.command를 다시 실행해 주세요.";
  ui.saveSystemButton.disabled = false;
}

ui.systemForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  ui.saveSystemButton.disabled = true;
  ui.systemMessage.className = "";
  ui.systemMessage.textContent = "경로를 확인하는 중";
  try {
    const data = await requestJson("/api/environment", {
      method: "PUT",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        project_dir: ui.projectDir.value,
        output_dir: ui.outputDir.value,
      }),
    });
    renderEnvironment(data.environment);
    if (data.restart_scheduled) {
      ui.systemMessage.textContent = "저장됨 · 서버를 다시 시작하는 중";
      void waitForServerRestart();
      return;
    }
    ui.systemMessage.textContent = "저장됨 · start.command로 실행해야 자동 재시작됩니다.";
  } catch (error) {
    ui.systemMessage.className = "error";
    ui.systemMessage.textContent = error.message;
  } finally {
    if (!ui.systemMessage.textContent.includes("다시 시작하는 중")) ui.saveSystemButton.disabled = false;
  }
});

const phaseLabels = {idle: "대기 중", queued: "대기열 등록", processing: "Whisper 전사 중", refining: "Whisper 정밀 확인 중", polishing: "Qwen3 제안 검수 중", saving: "파일 정리 중", cancelling: "중단 처리 중", cancelled: "중단", complete: "완료", warning: "확인 필요", error: "오류"};
const recordingStatusLabels = {recording: "녹음 중", recoverable: "복구 가능", queued: "대기 중", processing: "처리 중", cancelling: "중단 중", cancelled: "중단", completed: "완료", failed: "실패"};

function shortDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("ko-KR", {month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit"}).format(date);
}

function durationText(seconds) {
  if (!seconds) return "시간 미확인";
  return clockText(Number(seconds) * 1000);
}

function elapsedSince(value) {
  const started = new Date(value).getTime();
  if (Number.isNaN(started)) return 0;
  return Math.max(0, (Date.now() - started) / 1000);
}

function workTimeText(recording) {
  const startedAt = recording.started_at || recording.created_at;
  if (["processing", "cancelling"].includes(recording.status)) {
    return `작업 시작 ${shortDate(startedAt)} · 경과 ${durationText(elapsedSince(startedAt))}`;
  }
  if (recording.completed_at) {
    return `작업 ${shortDate(startedAt)} ~ ${shortDate(recording.completed_at)}`;
  }
  return `등록 ${shortDate(recording.created_at)}`;
}

async function cancelTranscription(recording, button) {
  const confirmed = await confirmAction(
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
    showToast(
      result.recording.status === "cancelled"
        ? "전사를 중단하고 원본 음성만 저장했습니다."
        : "중단 요청을 보냈습니다.",
      "success",
    );
  } catch (error) {
    button.disabled = false;
    showToast(error.message, "error");
  }
}

async function retryRecording(recording, button) {
  button.disabled = true;
  try {
    await flushCourseSave({requireSaved: true});
    let session = null;
    if (["recording", "recoverable"].includes(recording.status)) {
      const sessions = await recordingSessions().catch(() => []);
      session = sessions.find((item) => item.recordingId === recording.id) || null;
    }
    if (session) {
      await restoreRecordingSession(session);
      showToast("브라우저에 보관된 조각까지 전송해 녹음을 복구했습니다.", "success");
    } else {
      const retranscribe = ["completed", "failed", "cancelled"].includes(recording.status);
      const action = retranscribe ? "retranscribe" : "retry";
      const note = retranscribe ? await chooseNote(recording.course_id, recording.lecture_note_id, recording.title) : null;
      if (note === undefined) return;
      await requestJson(`/api/recordings/${encodeURIComponent(recording.id)}/${action}`, {
        method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({lecture_note_id:note})});
      if (retranscribe) showToast("다시 전사할 작업을 대기열에 추가했습니다.", "success");
    }
    await refreshStatus();
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

async function deleteRecordingHistory(recording, button) {
  const interrupted = ["recording", "recoverable"].includes(recording.status);
  const confirmed = await confirmAction(
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
    showToast(interrupted ? "복구 작업과 남은 녹음 조각을 삭제했습니다." : "작업 기록을 삭제했습니다.", "success");
  } catch (error) {
    button.disabled = false;
    showToast(error.message, "error");
  }
}

function stageTime(seconds) {
  return Number(seconds) > 0 ? durationText(Number(seconds)) : "—";
}

function hasPipelineLog(recording) {
  return Boolean(recording && (
    recording.error
    || Number(recording.quality?.pipeline_version) >= 1
    || Array.isArray(recording.quality?.attempts)
  ));
}

function openPipelineLog(recording, invoker) {
  if (!hasPipelineLog(recording)) return;
  app.pipelineLogInvoker = invoker || null;
  ui.pipelineDialogSubtitle.textContent = recording.title;
  const pipeline = createPipelineDetail(recording);
  pipeline.hidden = false;
  ui.pipelineDialogBody.replaceChildren(pipeline);
  ui.pipelineDialog.showModal();
}

function createPipelineDetail(recording) {
  const quality = recording.quality || {};
  const section = document.createElement("section");
  section.className = "pipeline-detail";
  section.hidden = true;
  section.setAttribute("aria-label", `${recording.title} 파이프라인 기록`);

  const head = document.createElement("header");
  const heading = document.createElement("strong");
  heading.textContent = "처리 요약";
  const rate = recording.duration_seconds > 0
    ? Number(quality.total_seconds || recording.processing_seconds || 0) / recording.duration_seconds
    : 0;
  const summary = document.createElement("span");
  summary.textContent = recording.status === "failed"
    ? `${recording.language.toUpperCase()} · 실패 기록${rate ? ` · 실시간 대비 ${rate.toFixed(2)}×` : ""}`
    : quality.cancelled
    ? `${recording.language.toUpperCase()} · 중단 기록${rate ? ` · 실시간 대비 ${rate.toFixed(2)}×` : ""}`
    : `${recording.language.toUpperCase()} · ${recording.llm_model}${rate ? ` · 실시간 대비 ${rate.toFixed(2)}×` : ""}`;
  head.append(heading, summary);
  section.append(head);

  const stages = recording.status === "failed" ? [
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
    ["Qwen3 검수", quality.qwen_seconds, recording.llm_enabled
      ? `${quality.qwen_batches || 0}묶음 · 요청 ${quality.qwen_requests || 0}회 · 분할 재시도 ${quality.qwen_split_retries || 0}회 · 입력 ${quality.qwen_prompt_tokens || 0} / 출력 ${quality.qwen_output_tokens || 0}토큰 · 제안 ${quality.suggestion_count || 0}개`
      : "사용 안 함"],
    ["파일 저장", quality.saving_seconds, `문단 ${quality.paragraph_break_count || 0}개 · 오디오 + Markdown`],
  ];
  const accounted = stages.reduce((total, stage) => total + (Number(stage[1]) || 0), 0);
  const totalSeconds = Number(quality.total_seconds || recording.processing_seconds || 0);
  const remainder = Math.max(0, totalSeconds - accounted);
  if (remainder >= 0.5) {
    stages.push(["기타 정리", remainder, "교정 규칙 적용 · 텍스트 구성 · 작업 상태 갱신"]);
  }

  const list = document.createElement("div");
  list.className = "pipeline-stages";
  for (const [label, seconds, result] of stages) {
    const line = document.createElement("div");
    line.className = "pipeline-stage";
    const name = document.createElement("span");
    name.textContent = label;
    const timing = document.createElement("strong");
    timing.textContent = stageTime(seconds);
    const outcome = document.createElement("small");
    outcome.textContent = result;
    line.append(name, timing, outcome);
    list.append(line);
  }
  section.append(list);
  const attempts = Array.isArray(quality.attempts) ? quality.attempts : [];
  if (attempts.length) {
    const history = document.createElement("div");
    history.className = "pipeline-attempts";
    const historyTitle = document.createElement("strong");
    historyTitle.textContent = "실패 이력";
    history.append(historyTitle);
    attempts.slice().reverse().forEach((attempt, index) => {
      const item = document.createElement("p");
      const when = attempt.failed_at ? shortDate(attempt.failed_at) : "시각 없음";
      const stage = phaseLabels[attempt.stage] || attempt.stage || "처리 중";
      item.textContent = `${attempts.length - index}차 · ${when} · ${stage} · ${stageTime(attempt.elapsed_seconds)}\n${attempt.error || "원인 기록 없음"}`;
      history.append(item);
    });
    section.append(history);
  }
  if (quality.cancelled || recording.status === "failed") return section;
  const settings = quality.settings || {};
  const settingsLine = document.createElement("p");
  settingsLine.className = "pipeline-settings";
  settingsLine.textContent = [
    `Whisper large-v3`,
    `entropy ${settings.entropy_thold || "—"}`,
    `이전 문맥 ${settings.no_context === "true" ? "끔" : "켬"}`,
    `VAD ${settings.vad_threshold || "—"}`,
    `저신뢰 기준 ${settings.low_confidence_threshold || "—"}`,
    `재확인 최대 ${settings.retry_max_groups || "—"}묶음/${settings.retry_max_seconds || "—"}초`,
    `Qwen3 교정 ${settings.qwen_review_policy || "—"}`,
    `제안 확신도 ${settings.qwen_edit_confidence || "—"}`,
  ].join(" · ");
  section.append(settingsLine);
  return section;
}

function renderQueue(recordings) {
  const jobs = [...recordings, ...(app.noteJobs || []).map(note => ({...note, kind: "note"}))];
  const active = jobs.filter((item) => ["processing", "cancelling"].includes(item.status));
  const queued = jobs
    .filter((item) => item.status === "queued")
    .sort((left, right) => new Date(left.created_at) - new Date(right.created_at));
  ui.queueSummary.textContent = active.length
    ? `${active.length}개 처리 중${queued.length ? ` · ${queued.length}개 대기` : ""}`
    : queued.length ? `${queued.length}개 대기` : "비어 있음";
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
      void openNote(recording.note_id).catch(error => showToast(error.message, "error"));
    });
    const marker = document.createElement("i");
    marker.setAttribute("aria-hidden", "true");
    const copy = document.createElement("span");
    copy.className = "queue-item-copy";
    const title = document.createElement("strong");
    title.textContent = recording.title;
    const meta = document.createElement("small");
    const position = isQueued ? "전사 대기" : recordingStatusLabels[recording.status] || "처리 중";
    const noteStage = {extracting: "원문 추출", study: "학습 정리", detail: `${recording.number}쪽 상세 해설`}[recording.stage];
    const completed = recording.stage === "extracting" ? recording.extracted_pages : recording.analyzed_pages;
    const progress = !isQueued && recording.page_count ? ` · ${completed}/${recording.page_count}쪽 처리` : "";
    meta.textContent = isNote
      ? `${recording.course_name || "강의노트"} · ${noteStage}${progress}`
      : `${recording.course_name} · ${position}`;
    copy.append(title, meta);
    const state = document.createElement("b");
    state.textContent = isQueued ? "대기" : isNote ? "분석 중" : recording.status === "cancelling" ? "중단 중" : "처리 중";
    link.append(marker, copy, state);
    if (!isNote && recording.status === "processing" && app.job?.job_id === recording.id) {
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
    ...(recording.llm_enabled ? [{phase: "polishing", label: "Qwen3"}] : []),
    {phase: "saving", label: "저장"},
  ];
  const currentIndex = Math.max(0, stages.findIndex((stage) => stage.phase === phase));
  const pipeline = document.createElement("section");
  pipeline.className = "queue-pipeline";
  pipeline.style.setProperty("--queue-stage-count", stages.length);
  pipeline.setAttribute("aria-label", `현재 처리 단계: ${phaseLabels[phase] || phase}`);
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
  message.textContent = job.message || phaseLabels[phase] || "처리 중";
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
  renderLibrary();
  renderDashboard();
}

async function openReview(recordingId, {focus = true} = {}) {
  try {
    const data = await requestJson(`/api/recordings/${recordingId}`, {cache: "no-store"});
    app.reviewingRecordingId = recordingId;
    ui.reviewRecordingName.textContent = data.recording.title;
    ui.reviewDialogTitle.textContent = "수정 제안";
    ui.reviewSubtitle.textContent = "승인하면 현재 전사문과 Markdown에 반영됩니다.";
    ui.closeReviewButton.hidden = false;
    renderReviewItems(data.suggestions);
    if (focus) ui.closeReviewButton.focus({preventScroll: true});
    if (window.matchMedia("(max-width: 760px)").matches) {
      ui.reviewDialog.scrollIntoView({behavior: "smooth", block: "start"});
    }
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function playAudioExcerpt(suggestion, button) {
  if (app.reviewAudio && app.reviewAudioButton === button && !app.reviewAudio.paused) {
    stopReviewAudio();
    return;
  }
  stopReviewAudio();
  const audio = new Audio(`/api/recordings/${encodeURIComponent(suggestion.recording_id)}/audio`);
  const start = Math.max(0, Number(suggestion.start) || 0);
  const end = Math.max(start + 1, Number(suggestion.end) || start + 12);
  button.dataset.idleLabel ||= button.textContent;
  button.disabled = true;
  setReviewListenLabel(button, "원음 불러오는 중");
  try {
    await new Promise((resolve, reject) => {
      audio.addEventListener("loadedmetadata", resolve, {once: true});
      audio.addEventListener("error", () => reject(new Error("해당 녹음을 불러오지 못했습니다.")), {once: true});
      audio.load();
    });
    audio.currentTime = start;
    app.reviewAudio = audio;
    app.reviewAudioButton = button;
    button.disabled = false;
    button.classList.add("playing");
    button.setAttribute("aria-pressed", "true");
    setReviewListenLabel(button, "원음 멈추기", true);
    audio.addEventListener("timeupdate", () => {
      if (audio.currentTime >= end && app.reviewAudio === audio) stopReviewAudio();
    });
    audio.addEventListener("ended", () => {
      if (app.reviewAudio === audio) stopReviewAudio();
    }, {once: true});
    await audio.play();
  } catch (error) {
    if (app.reviewAudio === audio) stopReviewAudio();
    showToast(error.message, "error");
  } finally {
    button.disabled = false;
    if (app.reviewAudio !== audio) resetReviewAudioButton(button);
  }
}

function setReviewListenLabel(button, label, playing = false) {
  const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  icon.setAttribute("viewBox", "0 0 20 20");
  icon.setAttribute("aria-hidden", "true");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", playing ? "M7 5.5v9M13 5.5v9" : "m7 5 8 5-8 5z");
  icon.append(path);
  const copy = document.createElement("span");
  copy.textContent = label;
  button.replaceChildren(icon, copy);
}

function resetReviewAudioButton(button) {
  if (!button) return;
  button.disabled = false;
  button.classList.remove("playing");
  button.setAttribute("aria-pressed", "false");
  setReviewListenLabel(button, button.dataset.idleLabel || "원음 듣기");
}

function stopReviewAudio() {
  const audio = app.reviewAudio;
  const button = app.reviewAudioButton;
  app.reviewAudio = null;
  app.reviewAudioButton = null;
  if (audio) {
    audio.pause();
    audio.removeAttribute("src");
    audio.load();
  }
  resetReviewAudioButton(button);
}

function appendHighlightedReviewContext(container, context, original) {
  const source = String(context || "원문 문맥을 불러오지 못했습니다.");
  const needle = String(original || "").trim();
  const index = needle ? source.toLocaleLowerCase().indexOf(needle.toLocaleLowerCase()) : -1;
  if (index < 0) {
    container.textContent = source;
    return;
  }
  container.append(document.createTextNode(source.slice(0, index)));
  const mark = document.createElement("mark");
  mark.textContent = source.slice(index, index + needle.length);
  container.append(mark, document.createTextNode(source.slice(index + needle.length)));
}

function renderReviewItems(suggestions) {
  stopReviewAudio();
  ui.reviewList.replaceChildren();
  if (!suggestions.length) {
    const empty = document.createElement("p");
    empty.className = "review-empty";
    empty.textContent = "검토할 수정 제안이 없습니다.";
    ui.reviewList.append(empty);
    return;
  }

  const resolved = suggestions.filter((suggestion) => suggestion.status !== "pending").length;
  const overview = document.createElement("section");
  overview.className = "review-overview";
  const overviewCopy = document.createElement("div");
  const overviewTitle = document.createElement("strong");
  overviewTitle.textContent = resolved === suggestions.length ? "검토 완료" : `${suggestions.length - resolved}개 남음`;
  const overviewDescription = document.createElement("span");
  overviewDescription.textContent = "원음을 확인한 뒤 제안 표현을 반영할지 선택하세요.";
  overviewCopy.append(overviewTitle, overviewDescription);
  const overviewProgress = document.createElement("div");
  overviewProgress.className = "review-progress";
  const progressLabel = document.createElement("span");
  progressLabel.textContent = `${resolved} / ${suggestions.length}`;
  const progress = document.createElement("progress");
  progress.max = suggestions.length;
  progress.value = resolved;
  progress.setAttribute("aria-label", `수정 제안 ${suggestions.length}개 중 ${resolved}개 검토`);
  overviewProgress.append(progressLabel, progress);
  overview.append(overviewCopy, overviewProgress);
  ui.reviewList.append(overview);

  suggestions.forEach((suggestion, suggestionIndex) => {
    const item = document.createElement("article");
    item.className = "review-item";
    item.dataset.status = suggestion.status;

    const itemHeader = document.createElement("header");
    itemHeader.className = "review-item-header";
    const itemNumber = document.createElement("strong");
    itemNumber.textContent = `제안 ${suggestionIndex + 1}`;
    const confidence = document.createElement("span");
    confidence.className = "review-confidence";
    const confidenceValue = Math.round(Number(suggestion.confidence || 0) * 100);
    confidence.textContent = `${confidenceValue}% 확신`;
    confidence.style.setProperty("--confidence", `${confidenceValue}%`);
    itemHeader.append(itemNumber, confidence);
    if (suggestion.status !== "pending") {
      const decision = document.createElement("span");
      decision.className = `review-decision ${suggestion.status}`;
      decision.textContent = suggestion.status === "accepted" ? "승인됨" : "거절됨";
      itemHeader.append(decision);
    }

    const comparison = document.createElement("div");
    comparison.className = "review-comparison";
    const original = document.createElement("div");
    original.className = "review-term original";
    const originalLabel = document.createElement("span");
    originalLabel.textContent = "들린 표현";
    const originalText = document.createElement("del");
    originalText.textContent = suggestion.original;
    original.append(originalLabel, originalText);
    const arrow = document.createElement("span");
    arrow.className = "review-arrow";
    arrow.setAttribute("aria-hidden", "true");
    arrow.innerHTML = '<svg viewBox="0 0 20 20"><path d="M4 10h11m-4-4 4 4-4 4"/></svg>';
    const replacement = document.createElement("div");
    replacement.className = "review-term replacement";
    const replacementLabel = document.createElement("span");
    replacementLabel.textContent = "제안 표현";
    const replacementText = document.createElement("ins");
    replacementText.textContent = suggestion.replacement;
    replacement.append(replacementLabel, replacementText);
    comparison.append(original, arrow, replacement);

    const evidence = document.createElement("section");
    evidence.className = "review-evidence";
    const contextLabel = document.createElement("span");
    contextLabel.textContent = "해당 문장";
    const context = document.createElement("blockquote");
    appendHighlightedReviewContext(context, suggestion.context, suggestion.original);
    const reason = document.createElement("p");
    reason.className = "review-reason";
    reason.textContent = suggestion.reason || "문맥 기반 수정 제안";
    evidence.append(contextLabel, context, reason);

    const footer = document.createElement("footer");
    footer.className = "review-item-footer";
    if (suggestion.audio_name) {
      const listen = document.createElement("button");
      listen.type = "button";
      listen.className = "review-listen";
      listen.dataset.idleLabel = `${durationText(suggestion.start)} 원음 듣기`;
      setReviewListenLabel(listen, listen.dataset.idleLabel);
      listen.setAttribute("aria-pressed", "false");
      listen.addEventListener("click", () => playAudioExcerpt(suggestion, listen));
      footer.append(listen);
    }
    if (suggestion.status === "pending") {
      const actions = document.createElement("div");
      actions.className = "review-actions";
      for (const [action, label] of [["reject", "거절"], ["accept", "승인하고 학습"]]) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = action === "accept" ? "accept" : "reject";
        button.textContent = label;
        button.addEventListener("click", async () => {
          [...actions.children].forEach((control) => control.disabled = true);
          try {
            const data = await requestJson(`/api/suggestions/${suggestion.id}/decision`, {
              method: "POST",
              headers: {"Content-Type": "application/json"},
              body: JSON.stringify({action}),
            });
            if (data.course) {
              const courseIndex = app.courses.findIndex((course) => course.id === data.course.id);
              if (courseIndex >= 0) app.courses[courseIndex] = data.course;
              if (app.activeCourseId === data.course.id) renderCourse(data.course);
            }
            renderReviewItems(data.suggestions);
            await refreshStatus();
          } catch (error) {
            showToast(error.message, "error");
            [...actions.children].forEach((control) => control.disabled = false);
          }
        });
        actions.append(button);
      }
      footer.append(actions);
    }
    item.append(itemHeader, comparison, evidence, footer);
    ui.reviewList.append(item);
  });
}

function renderStatus(data) {
  const needsLlm = data.settings.llm_enabled !== false;
  if (!data.whisper_ready && data.whisper_state === "idle") {
    ui.serverPill.className = "server-status has-tooltip ready";
    ui.serverText.textContent = "Whisper 대기 · 작업 시 자동 로드";
  } else if (data.whisper_state === "loading") {
    ui.serverPill.className = "server-status has-tooltip warning";
    ui.serverText.textContent = "Whisper 모델 로드 중";
  } else if (!data.whisper_ready) {
    ui.serverPill.className = "server-status has-tooltip offline";
    ui.serverText.textContent = "Whisper 연결 안 됨";
  } else if (needsLlm && !data.ollama_model_ready) {
    ui.serverPill.className = "server-status has-tooltip warning";
    ui.serverText.textContent = "Whisper 준비 · Qwen3 확인 필요";
  } else {
    ui.serverPill.className = "server-status has-tooltip ready";
    ui.serverText.textContent = needsLlm ? "Whisper + Qwen3 준비됨" : "Whisper 준비됨";
  }
  const phase = data.job.phase || "idle";
  app.job = data.job || {};
  ui.progressCard.dataset.phase = phase;
  ui.progressKicker.textContent = phaseLabels[phase] || phase;
  ui.progressTitle.textContent = data.job.title || "준비되어 있습니다";
  ui.progressMessage.textContent = data.job.message || "강의를 선택하고 작업을 시작하세요.";
  renderSuggestions(data.job.suggestions || []);
  app.noteJobs = data.note_jobs || [];
  renderRecordings(data.recordings || []);
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
    void recoverRecordingSessions(data.recordings || []);
  } catch (_) {
    ui.serverPill.className = "server-status has-tooltip offline";
    ui.serverText.textContent = "Lecorder 연결 안 됨";
  }
}

async function initialize() {
  try {
    const data = await requestJson("/api/bootstrap", {cache: "no-store"});
    app.courses = data.courses;
    app.activeCourseId = data.active_course_id;
    renderCourseList();
    renderCourse(activeCourse());
    renderEnvironment(data.environment);
    renderStatus(data);
    void recoverRecordingSessions(data.recordings || []);
    await refreshStorage();
    if (!window.location.hash) {
      const restored = window.localStorage.getItem(VIEW_STORAGE_KEY);
      window.location.hash = restored === "courses" ? courseRoute() : restored === "recordings" ? "#/recordings" : "#/dashboard";
    }
    await applyRoute();
    window.setInterval(refreshStatus, 1800);
    window.setInterval(() => {
      if (["dashboard", "recordings"].includes(app.activeView)) void refreshStorage();
    }, 5000);
  } catch (error) {
    ui.courseList.textContent = `대시보드를 불러오지 못했습니다: ${error.message}`;
    setSaveState("error", "연결 오류");
  }
}

initialize();
