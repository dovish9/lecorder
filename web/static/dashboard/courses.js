import {showCourseNotes} from "../notes.js";
import {requestJson} from "./api.js";

export function createCourses(context) {
  const {app, ui, $, $$, actions, listen, every} = context;
  const weekdays = ["일", "월", "화", "수", "목", "금", "토"];
  function dateSuffix() {
    const now = new Date();
    return `${now.getMonth() + 1}월${now.getDate()}일-${weekdays[now.getDay()]}`;
  }

  function automaticTitle() {
    const course = actions.activeCourse();
    const base = `${course?.name || "녹음"}-${dateSuffix()}`;
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
    if (!query || "선택 안 함".includes(query)) {
      const entry=document.createElement("div");
      entry.className=`course-entry${!app.activeCourseId ? " active" : ""}`;
      const button=document.createElement("button"); button.type="button"; button.className="course-item";
      button.setAttribute("aria-current", String(!app.activeCourseId));
      const label=document.createElement("span");label.textContent="선택 안 함";
      const detail=document.createElement("small");detail.textContent="인식 힌트 없이 전사";
      button.append(label,detail);
      button.addEventListener("click",()=>void selectCourse(0,{updateRoute:true}));
      entry.append(button);ui.courseList.append(entry);
    }
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
      button.addEventListener("click", () => { window.location.hash = actions.courseRoute(course.id); });

      const menu = document.createElement("details");
      menu.className = "course-menu";
      const summary = document.createElement("summary");
      summary.textContent = "⋯";
      summary.setAttribute("aria-label", `${course.name} 메뉴`);
      const menuActions = document.createElement("div");
      menuActions.className = "course-actions";
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
      menuActions.append(edit, remove);
      menu.append(summary, menuActions);
      entry.append(button, menu);
      ui.courseList.append(entry);
    }
    renderCaptureControls();
  }

  function renderCaptureControls() {
    const captureCourseId = app.recordingState === "idle" ? app.activeCourseId : app.recordingCourseId;
    ui.dockCourseSelect.replaceChildren();
    ui.uploadCourseSelect.replaceChildren();
    ui.dockCourseMenu.replaceChildren();
    ui.uploadCourseMenu.replaceChildren();
    for (const course of [{id:0,name:"선택 안 함"}, ...app.courses]) {
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
      item.setAttribute("aria-selected", String(course.id === captureCourseId));
      const check = document.createElement("span");
      check.className = "dock-course-check";
      check.setAttribute("aria-hidden", "true");
      check.textContent = course.id === captureCourseId ? "✓" : "";
      const name = document.createElement("span");
      name.textContent = course.name;
      item.append(check, name);
      item.addEventListener("click", async () => {
        ui.dockCoursePicker.removeAttribute("open");
        await actions.selectDockCourse(course.id);
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
    const locked = app.recordingState !== "idle";
    const selectedCourse = locked
      ? app.courses.find((course) => course.id === app.recordingCourseId)
      : actions.activeCourse();
    ui.dockCourseLabel.textContent = selectedCourse?.name || "선택 안 함";
    ui.uploadCourseLabel.textContent = actions.activeCourse()?.name || "선택 안 함";
    ui.dockCourseButton.setAttribute("aria-disabled", String(!actions.captureSettingsEditable()));
    ui.dockCourseButton.tabIndex = 0;
    ui.dockCourseSelect.disabled = !actions.captureSettingsEditable();
    ui.dockCourseLabel.title = selectedCourse?.name || "";
    $("#dockNoteButton").setAttribute("aria-disabled", String(!actions.captureSettingsEditable() || !captureCourseId));
    ui.uploadCourseSelect.disabled = app.uploadInProgress;
    ui.uploadCourseButton.setAttribute("aria-disabled", String(app.uploadInProgress));
    ui.uploadCourseButton.tabIndex = !app.uploadInProgress ? 0 : -1;
    if (app.uploadInProgress) ui.uploadCoursePicker.removeAttribute("open");
    ui.dockRecordButton.disabled = app.uploadInProgress || app.recordingState === "starting" || app.recordingState === "finalizing";
    ui.dockUploadButton.disabled = app.uploadInProgress || locked;
    actions.renderCourseAlertBadges();
  }

  listen(ui.courseSearch, "input", renderCourseList);
  listen(ui.dockCourseSelect, "change", async () => {
    const courseId = Number(ui.dockCourseSelect.value);
    await actions.selectDockCourse(courseId);
    actions.renderDashboard();
  });
  listen(ui.dockCourseButton, "click", (event) => {
    if (actions.captureSettingsEditable()) return;
    event.preventDefault();
  });
  listen(ui.dockCourseButton, "keydown", (event) => {
    if (event.key !== "ArrowDown" || !actions.captureSettingsEditable()) return;
    event.preventDefault();
    ui.dockCoursePicker.setAttribute("open", "");
    ui.dockCourseMenu.querySelector('[aria-selected="true"]')?.focus();
  });
  listen(ui.dockCoursePicker, "keydown", (event) => {
    if (event.key !== "Escape" || !ui.dockCoursePicker.open) return;
    event.preventDefault();
    ui.dockCoursePicker.removeAttribute("open");
    ui.dockCourseButton.focus();
  });

  async function selectUploadCourse(courseId) {
    if (app.uploadInProgress || (courseId !== 0 && !app.courses.some((course) => course.id === courseId))) return;
    ui.uploadCoursePicker.removeAttribute("open");
    ui.uploadCourseButton.setAttribute("aria-busy", "true");
    if (courseId !== app.activeCourseId) {
      await selectCourse(courseId, {updateRoute: app.activeView === "courses"});
      actions.renderDashboard();
    }
    ui.uploadCourseSelect.value = String(app.activeCourseId);
    actions.setUploadTitlePreset();
    void actions.loadUploadNotes();
    ui.uploadCourseButton.removeAttribute("aria-busy");
    ui.uploadCourseButton.focus({preventScroll: true});
  }

  listen(ui.uploadCourseButton, "click", (event) => {
    if (!app.uploadInProgress) return;
    event.preventDefault();
  });
  listen(ui.uploadCoursePicker, "toggle", () => {
    ui.uploadCourseButton.setAttribute("aria-expanded", String(ui.uploadCoursePicker.open));
  });
  listen(ui.uploadCoursePicker, "keydown", (event) => {
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
    if (!course || !await actions.confirmAction(`‘${course.name}’ 강의와 저장된 설정을 삭제할까요? 녹음과 Markdown 파일은 삭제되지 않습니다.`, "강의 삭제")) return;
    await flushCourseSave();
    try {
      const wasActive = course.id === app.activeCourseId;
      const data = await requestJson(`/api/courses/${course.id}`, {method: "DELETE"});
      app.courses = app.courses.filter((item) => item.id !== course.id);
      app.activeCourseId = data.active_course_id || 0;
      renderCourseList();
      if (wasActive) renderCourse(actions.activeCourse());
      window.location.hash = actions.courseRoute();
    } catch (error) {
      setSaveState("error", error.message);
    }
  }

  function renderCourse(course) {
    for (const input of [ui.courseName, ...$$('input[name="language"]')]) input.disabled = !course;
    if (!course) {
      app.hydrating = true;
      ui.courseName.value = "선택 안 함";
      void showCourseNotes(null);
      $('input[name="language"][value="auto"]').checked = true;
      setSaveState("", "인식 힌트 없이 전사");
      app.hydrating = false;
      return;
    }
    app.hydrating = true;
    ui.courseName.value = course.name;
    void showCourseNotes(course.id);
    const language = $(`input[name="language"][value="${course.language}"]`);
    if (language) language.checked = true;
    app.hydrating = false;
    setSaveState("", "저장됨");
  }

  function coursePayload() {
    return {
      name: ui.courseName.value.trim(),
      language: $('input[name="language"]:checked')?.value || "ko",
    };
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
        const data = await actions.requestJsonBeforeDeadline(`/api/courses/${courseId}`, {
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
      if (app.recordingState === "idle") {
        app.recordingNoteSaved = "";
        $("#dockNoteSelect").replaceChildren(new Option("사용 안 함", ""));
        actions.renderNotePicker();
      }
      app.reviewingRecordingId = null;
      const index = app.courses.findIndex((course) => course.id === data.course.id);
      if (index >= 0) app.courses[index] = data.course;
      renderCourseList();
      renderCourse(data.course.id ? data.course : null);
      actions.renderLibrary();
      if (updateRoute) window.location.hash = actions.courseRoute(data.course.id);
    } catch (error) {
      setSaveState("error", error.message);
    }
  }

  listen(ui.newCourseForm, "submit", async (event) => {
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
      if (app.recordingState === "idle") {
        app.recordingNoteSaved = "";
        $("#dockNoteSelect").replaceChildren(new Option("사용 안 함", ""));
        actions.renderNotePicker();
      }
      app.reviewingRecordingId = null;
      ui.newCourseName.value = "";
      renderCourseList();
      renderCourse(data.course);
      window.location.hash = actions.courseRoute(data.course.id);
    } catch (error) {
      ui.newCourseName.setCustomValidity(error.message);
      ui.newCourseName.reportValidity();
    }
  });

  listen(ui.newCourseName, "input", () => ui.newCourseName.setCustomValidity(""));

  listen(ui.courseName, "input", () => {
    const course = actions.activeCourse();
    if (course) {
      course.name = ui.courseName.value;
      const label = ui.courseList.querySelector(`[data-course-id="${course.id}"] .course-item span`);
      if (label) label.textContent = course.name || "이름 없는 강의";
    }
    scheduleCourseSave();
  });
  $$('input[name="language"]').forEach((input) => input.addEventListener("change", () => {
    const course = actions.activeCourse();
    if (course) course.language = input.value;
    renderCourseList();
    scheduleCourseSave(0);
  }));

  listen(ui.uploadTitle, "input", () => {
    app.uploadTitleAutomatic = false;
    ui.uploadTitle.setCustomValidity("");
  });

  return {automaticTitle, beginCourseRename, coursePayload, dateSuffix, deleteCourseById, flushCourseSave, renderCaptureControls, renderCourse, renderCourseList, saveCourseNow, scheduleCourseSave, selectCourse, selectUploadCourse, setSaveState};
}
