import {requestJson} from "./api.js";
import {formatFileSize} from "./selectors.js";

export function createUpload(context) {
  const {app, ui, $, $$, actions, listen, every} = context;
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
    ui.uploadTitle.value = actions.automaticTitle();
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
      ui.uploadTitle.value = uploadStem(file) || actions.automaticTitle();
    }
    ui.uploadMessage.className = "upload-message";
    ui.uploadMessage.textContent = "";
  }

  let uploadNoteRequest = 0;
  async function loadUploadNotes(selected = "") {
    const serial = ++uploadNoteRequest;
    app.uploadNoteId = "";
    app.uploadNotesLoading = true;
    app.uploadNotesError = "";
    $("#uploadNoteLabel").textContent = "불러오는 중…";
    $("#uploadNoteMenu").replaceChildren();
    try {
      const {notes} = app.activeCourseId ? await requestJson(`/api/courses/${app.activeCourseId}/notes`) : {notes:[]};
      if (serial !== uploadNoteRequest) return;
      const options = [{id:"", title:"사용 안 함", status:"ready"}, ...notes];
      app.uploadNoteId = options.some(n => n.id === selected && ["ready","partial","queued","extracting"].includes(n.status)) ? selected : "";
      const render = () => {
        const chosen = options.find(n => n.id === app.uploadNoteId);
        $("#uploadNoteLabel").textContent = chosen?.title || "사용 안 함";
        $("#uploadNoteLabel").title = chosen?.title || "사용 안 함";
        const pending = chosen && (["queued","extracting"].includes(chosen.status) || ["pending","analyzing"].includes(chosen.study_status));
        $("#uploadNoteHint").textContent = !app.activeCourseId ? "강의를 선택하지 않아 인식 힌트 없이 전사합니다." : pending ? "분석 완료 후 전사합니다. 분석 실패·중단 시 전사를 시작하지 않습니다." : "선택한 노트의 키워드와 문맥을 전사에 활용합니다.";
        $("#uploadNoteMenu").replaceChildren();
        for (const note of options) {
          const button = document.createElement("button"); button.type="button"; button.className="upload-course-option";
          button.setAttribute("role","option"); button.setAttribute("aria-selected",String(note.id === app.uploadNoteId));
          button.disabled = !(app.retranscribeRecording ? ["ready","partial","queued","extracting"] : ["ready","partial"]).includes(note.status);
          const check = document.createElement("span"); check.className="upload-course-check"; check.textContent=note.id === app.uploadNoteId ? "✓" : "";
          const label=document.createElement("span"); label.textContent=note.title + (button.disabled ? " · 준비되지 않음" : "");
          button.append(check,label);
          button.addEventListener("click",()=>{if(app.uploadInProgress)return; app.uploadNoteId=note.id; render(); $("#uploadNotePicker").open=false; $("#uploadNoteButton").focus();});
          $("#uploadNoteMenu").append(button);
        }
      };
      render();
    } catch(error) {
      if(serial!==uploadNoteRequest)return;
      app.uploadNotesError = error.message;
      $("#uploadNoteLabel").textContent="목록 다시 불러오기";
      $("#uploadNoteHint").textContent=`목록을 불러오지 못했습니다: ${error.message}`;
    } finally { if(serial===uploadNoteRequest)app.uploadNotesLoading=false; }
  }
  $("#uploadNoteButton").addEventListener("click",event=>{if(app.uploadInProgress || app.uploadNotesLoading)event.preventDefault(); else if(app.uploadNotesError){event.preventDefault();void loadUploadNotes();}});
  $("#uploadNotePicker").addEventListener("toggle",()=>{
    $("#uploadNoteButton").setAttribute("aria-expanded",String($("#uploadNotePicker").open));
    if($("#uploadNotePicker").open)ui.uploadCoursePicker.open=false;
  });
  listen(ui.uploadCoursePicker, "toggle",()=>{if(ui.uploadCoursePicker.open)$("#uploadNotePicker").open=false;});
  $("#uploadNotePicker").addEventListener("keydown",event=>{
    if(event.key==="Escape") {event.preventDefault();event.stopPropagation();$("#uploadNotePicker").open=false;$("#uploadNoteButton").focus();}
    if(["ArrowDown","ArrowUp"].includes(event.key)) {
      event.preventDefault(); if(app.uploadInProgress || app.uploadNotesLoading)return;
      $("#uploadNotePicker").open=true;
      const buttons=[...$("#uploadNoteMenu").querySelectorAll("button:not(:disabled)")];
      const index=buttons.indexOf(document.activeElement);
      buttons[(index+(event.key==="ArrowDown"?1:-1)+buttons.length)%buttons.length]?.focus();
    }
  });
  // Only a click that starts and ends outside a dialog dismisses its backdrop.
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
    ui.dropZone.disabled = busy || Boolean(app.retranscribeRecording);
    $("#uploadNoteButton").setAttribute("aria-disabled", String(busy));
    if(busy) $("#uploadNotePicker").open=false;
    actions.renderCaptureControls();
  }

  async function openUploadDialog(recording = null) {
    if (app.recordingState !== "idle" && !recording) return;
    resetUploadSelection();
    app.retranscribeRecording = recording;
    ui.uploadDialog.dataset.mode = recording ? "retranscribe" : "upload";
    $("#uploadNotePicker").open=false;
    ui.uploadCoursePicker.open=false;
    if (recording && recording.course_id !== app.activeCourseId) await actions.selectCourse(recording.course_id || 0);
    $("#uploadDialogTitle").textContent = recording ? "다시 전사" : "파일 가져오기";
    $("#uploadDialog header p").textContent = recording ? "기존 음성으로 다시 전사합니다. 이름과 강의, 강의노트를 변경할 수 있습니다." : "오디오나 동영상을 전사 대기열에 추가합니다.";
    ui.uploadButton.textContent = recording ? "다시 전사" : "대기열에 추가";
    ui.closeUploadButton.setAttribute("aria-label", recording ? "다시 전사 닫기" : "파일 가져오기 닫기");
    ui.dropZone.disabled = Boolean(recording);
    if (recording) {
      app.uploadTitleAutomatic = false;
      ui.uploadTitle.value = recording.title;
      ui.dropTitle.textContent = recording.audio_name || recording.title;
      ui.dropDescription.textContent = "기존 음성 유지";
    }
    void loadUploadNotes(recording?.lecture_note_id || "");
    actions.renderCaptureControls();
    ui.uploadCourseSelect.value = String(app.activeCourseId);
    setUploadTitlePreset();
    ui.uploadDialog.showModal();
  }

  function closeUploadDialog() {
    if (!app.uploadInProgress && ui.uploadDialog.open) ui.uploadDialog.close();
  }

  listen(ui.dockUploadButton, "click", () => void openUploadDialog());
  listen(ui.closeUploadButton, "click", closeUploadDialog);
  listen(ui.cancelUploadButton, "click", closeUploadDialog);
  listen(ui.uploadDialog, "cancel", (event) => {
    if (app.uploadInProgress) event.preventDefault();
  });
  listen(ui.uploadDialog, "click", (event) => {
    if (event.target === ui.uploadDialog) closeUploadDialog();
  });
  listen(ui.uploadDialog, "close", () => {
    resetUploadSelection();
    ui.dockUploadButton.focus({preventScroll: true});
  });
  listen(ui.uploadCourseSelect, "change", async () => {
    const courseId = Number(ui.uploadCourseSelect.value);
    await actions.selectUploadCourse(courseId);
  });
  listen(ui.dropZone, "click", () => ui.uploadFile.click());
  listen(ui.uploadFile, "change", () => selectUploadFile(ui.uploadFile.files[0]));
  ["dragenter", "dragover"].forEach((name) => listen(ui.dropZone, name, (event) => {
    event.preventDefault();
    ui.dropZone.classList.add("dragging");
  }));
  ["dragleave", "drop"].forEach((name) => listen(ui.dropZone, name, (event) => {
    event.preventDefault();
    ui.dropZone.classList.remove("dragging");
  }));
  listen(ui.dropZone, "drop", (event) => selectUploadFile(event.dataTransfer.files[0]));

  listen(ui.uploadForm, "submit", async (event) => {
    event.preventDefault();
    if (app.uploadInProgress) return;
    if (!app.selectedFile && !app.retranscribeRecording) {
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
    const lectureNoteId = app.uploadNoteId || null;
    try {
      await actions.flushCourseSave({requireSaved: true});
      if (app.uploadNotesError) throw new Error("강의노트 목록을 다시 불러와 주세요.");
      if (app.uploadNotesLoading) throw new Error("강의노트 목록을 불러오는 중입니다.");
    } catch (error) {
      ui.uploadMessage.className = "upload-message error";
      ui.uploadMessage.textContent = error.message;
      return;
    }
    if (app.uploadInProgress || !ui.uploadDialog.open) return;
    setUploadBusy(true);
    ui.uploadMessage.className = "upload-message";
    ui.uploadMessage.textContent = "파일을 안전하게 저장하는 중입니다.";
    const data = new FormData();
    if (app.selectedFile) data.append("file", app.selectedFile, app.selectedFile.name);
    data.append("title", ui.uploadTitle.value);
    if (lectureNoteId) data.append("lecture_note_id", lectureNoteId);
    try {
      const result = app.retranscribeRecording
        ? await requestJson(`/api/recordings/${encodeURIComponent(app.retranscribeRecording.id)}/retranscribe`, {
            method: "POST", headers: {"Content-Type":"application/json"},
            body: JSON.stringify({title:ui.uploadTitle.value, course_id:app.activeCourseId || 0, lecture_note_id:lectureNoteId}),
          })
        : await requestJson("/api/upload", {method: "POST", body: data});
      actions.renderRecordings([result.recording, ...app.recordings.filter((item) => item.id !== result.recording.id)]);
      await actions.refreshStatus();
      await actions.refreshStorage();
      setUploadBusy(false);
      ui.uploadDialog.close();
      actions.showToast("전사 작업을 대기열에 추가했습니다.", "success", {
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


  return {closeUploadDialog, loadUploadNotes, openUploadDialog, resetUploadSelection, selectUploadFile, setUploadBusy, setUploadTitlePreset, uploadExtension, uploadStem};
}
