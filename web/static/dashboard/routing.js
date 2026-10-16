import {libraryRoute} from "./selectors.js";

export function createRouting(context) {
  const {app, ui, $, $$, actions, listen, every} = context;
  const VIEW_STORAGE_KEY = "lecorder-view";
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
    if (view !== "recordings") actions.stopReviewAudio();
    app.activeView = view;
    ui.workspaceTabs.forEach((tab) => {
      const active = tab.dataset.view === view;
      tab.classList.toggle("active", active);
      tab.setAttribute("aria-current", active ? "page" : "false");
    });
    ui.workspacePanels.forEach((panel) => panel.hidden = panel.dataset.viewPanel !== view);
    [ui.workspaceTitle.textContent, ui.workspaceDescription.textContent] = viewCopy[view];
    if (persist) window.localStorage.setItem(VIEW_STORAGE_KEY, view);
    if (["dashboard", "recordings"].includes(view)) void actions.refreshStorage();
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
    const {parts, params} = parseRoute();
    if (parts[0] === "today" && parts.length === 1) {
      window.location.replace("#/dashboard");
      return;
    }
    if (parts[0] === "dashboard" && parts.length === 1) {
      switchWorkspace("dashboard");
      actions.renderDashboard();
      return;
    }
    if (parts[0] === "courses") {
      const requested = Number(parts[1]);
      if (parts[1] && !app.courses.some((course) => course.id === requested)) {
        window.location.replace(courseRoute());
        return;
      }
      if (requested && requested !== app.activeCourseId) await actions.selectCourse(requested, {updateRoute: false});
      switchWorkspace("courses");
      return;
    }
    if (parts[0] === "recordings") {
      switchWorkspace("recordings");
      if (parts.length === 1) {
        app.selectedLibraryKey = null;
        actions.renderLibrary();
        return;
      }
      const key = parts[1] === "job" ? `job:${parts[2]}` : parts[1] === "file" ? `file:${decodeURIComponent(parts[2] || "")}` : "";
      if (!key || !app.libraryEntries.some((entry) => entry.key === key)) {
        window.location.replace("#/recordings");
        return;
      }
      const targetEntry = app.libraryEntries.find(entry => entry.key === key);
      const targetCourseId = targetEntry?.recording?.course_id || 0;
      if (app.recordingState === "idle" && targetCourseId !== app.activeCourseId) {
        await actions.selectCourse(targetCourseId, {updateRoute:false});
        window.history.replaceState(null, "", libraryRoute(targetEntry)+(params.has('time')?'?'+params.toString():''));
      }
      app.selectedLibraryKey = key;
      actions.renderLibrary();
      if(params.has('time')) {
        const seconds=Number(params.get('time'));
        if(Number.isFinite(seconds)&&seconds>=0) app.previewAudioPlayer?.seekTo(seconds,{autoplay:false,reveal:true});
      }
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
  listen(window, "hashchange", () => void applyRoute());


  return {activeCourse, applyRoute, courseRoute, parseRoute, switchWorkspace};
}
