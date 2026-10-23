import {createCaptureRecovery} from './capture-recovery.js';
import {acquireRecordingOwnership} from "../recording-ownership.js";
import * as recoveryStore from "../recording-store.js";
import {requestJson} from "./api.js";
import {clockText, frequencyBandLevel, recordingExtension} from "./selectors.js";

export function createCapture(context) {
  const {deletePendingChunk, deleteRecordingRecovery, pendingChunks, putPendingChunk, putRecordingSession, recordingSessions, updateRecordingSession} = context.recovery || recoveryStore;
  const {app, $, $$, actions, listen, every} = context;
  const ui = context.ui || {
    commandDock: $("#commandDock"),
    dockCoursePicker: $("#dockCoursePicker"),
    dockPauseButton: $("#dockPauseButton"),
    dockRecordButton: $("#dockRecordButton"),
    dockRecordClock: $("#dockRecordClock"),
    dockRecordLabel: $("#dockRecordLabel"),
    dockRecordingSession: $("#dockRecordingSession"),
    dockRecordingTitle: $("#dockRecordingTitle"),
    dockSoundMeter: $("#dockSoundMeter"),
  };
  if (!context.ui) {
    for (let index=0;index<13;index++) ui.dockSoundMeter.append(document.createElement('i'));
    ui.soundBars=[...ui.dockSoundMeter.children];
  }
  const acquireOwnership = context.acquireOwnership || acquireRecordingOwnership;
  let releaseOwnership = null;
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
    if (state === "idle" && app.recordingState !== "idle") {
      app.recordingNoteSaved = "";
      $("#dockNoteSelect").replaceChildren(new Option("사용 안 함", ""));
      renderNotePicker();
    }
    app.recordingState = state;
    ui.commandDock.dataset.recordState = state;
    const active = ["recording", "paused"].includes(state);
    const finalizing = state === "finalizing";
    $("#dockNoteSelect").disabled = !["idle", "recording", "paused"].includes(state);
    if (!active && state !== "idle") $("#dockNotePicker").removeAttribute("open");
    ui.dockRecordLabel.textContent = active ? "종료" : finalizing ? "저장 중" : state === "starting" ? "연결 중" : "녹음";
    ui.dockRecordButton.setAttribute("aria-label", active ? "녹음 종료 후 음성 변환" : finalizing ? "녹음 저장 중" : state === "starting" ? "마이크 연결 중" : "녹음 시작");
    ui.dockRecordButton.disabled = ["starting", "finalizing"].includes(state);
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
    actions.renderCaptureControls();
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
    actions.stopLive?.();
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
      actions.renderRecordings([result.recording, ...(app.recordings || []).filter((item) => item.id !== result.recording.id)]);
      await actions.refreshStatus();
      await actions.refreshStorage();
      actions.showToast("녹음을 안전하게 저장하고 음성 변환 대기열에 추가했습니다.", "success", {
        label: "보관함에서 보기",
        run: () => { window.location.hash = `#/recordings/job/${encodeURIComponent(result.recording.id)}`; },
      });
      return result;
    } finally {
      app.finalizingRecordingId = null;
    }
  }

  const {uploadChunk,queueRecordingChunk,retryFailedChunks,restoreRecordingSession,recoverRecordingSessions}=createCaptureRecovery({
    app,ui,actions,store:context.recovery || recoveryStore,acquireOwnership,requestJsonBeforeDeadline,
  });

  let recordingNoteSave = Promise.resolve();
  app.recordingNoteSaved = "";
  let recordingNoteLoad = 0;

  function captureSettingsEditable() {
    return !app.captureSettingsSaving && ["idle", "recording", "paused"].includes(app.recordingState);
  }
  function renderNotePicker() {
    const select = $("#dockNoteSelect");
    const label = $("#dockNoteLabel");
    label.textContent = select.selectedOptions[0]?.textContent || "사용 안 함";
    label.title = label.textContent;
    const menu = $("#dockNoteMenu");
    menu.replaceChildren();
    for (const option of select.options) {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "dock-course-option";
      item.setAttribute("role", "option");
      item.setAttribute("aria-selected", String(option.selected));
      item.disabled = option.disabled;
      const check = document.createElement("span");
      check.className = "dock-course-check";
      check.textContent = option.selected ? "✓" : "";
      const text = document.createElement("span");
      text.textContent = option.textContent;
      item.append(check, text);
      item.addEventListener("click", () => {
        if (!captureSettingsEditable()) return;
        $("#dockNotePicker").removeAttribute("open");
        select.value = option.value;
        select.dispatchEvent(new Event("change"));
      });
      menu.append(item);
    }
  }
  async function loadRecordingNotes() {
    const serial = ++recordingNoteLoad;
    const courseId = app.recordingState === "idle" ? app.activeCourseId : app.recordingCourseId;
    if (!courseId) return;
    const select = $("#dockNoteSelect");
    try {
      const {notes} = await requestJson(`/api/courses/${courseId}/notes`);
      if (serial !== recordingNoteLoad || courseId !== (app.recordingState === "idle" ? app.activeCourseId : app.recordingCourseId)) return;
      const selected = app.recordingNoteSaved;
      const existingLabel = select.selectedOptions[0]?.textContent;
      select.replaceChildren(new Option("사용 안 함", ""));
      for (const note of notes) {
        const ready = ["ready", "partial"].includes(note.status);
        const option = new Option(`${note.title}${ready ? "" : " · 준비되지 않음"}`, note.id);
        option.disabled = !ready;
        select.append(option);
      }
      if (selected && !notes.some(note => note.id === selected)) select.append(new Option(existingLabel || "기존에 선택한 노트", selected));
      select.value = selected;
      renderNotePicker();
    } catch (error) { actions.showToast(error.message, "error"); }
  }
  $("#dockNotePicker").addEventListener("toggle", () => {
    if ($("#dockNotePicker").open) {
      ui.dockCoursePicker.removeAttribute("open");
      renderNotePicker();
      void loadRecordingNotes();
    }
  });
  $("#dockNoteButton").addEventListener("click", event => {
    if (!captureSettingsEditable() || !(app.recordingState === "idle" ? app.activeCourseId : app.recordingCourseId)) event.preventDefault();
  });
  $("#dockNotePicker").addEventListener("keydown", event => {
    if (event.key === "Escape") { $("#dockNotePicker").removeAttribute("open"); $("#dockNoteButton").focus(); }
    if (event.key === "ArrowDown" && event.target === $("#dockNoteButton") && captureSettingsEditable() && (app.recordingState === "idle" ? app.activeCourseId : app.recordingCourseId)) {
      event.preventDefault(); $("#dockNotePicker").setAttribute("open", "");
      $("#dockNoteMenu button")?.focus();
    }
  });
  $("#dockNoteSelect").addEventListener("change", () => {
    const select = $("#dockNoteSelect");
    const selected = select.value;
    if (app.recordingState === "idle") { app.recordingNoteSaved = selected; renderNotePicker(); return; }
    if (!captureSettingsEditable()) return;
    const recordingId = app.recordingId;
    app.captureSettingsSaving = true;
    actions.renderCaptureControls();
    recordingNoteSave = recordingNoteSave.then(async () => {
      try {
        await requestJson(`/api/recordings/${recordingId}/lecture-note`, {
          method: "PATCH", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({lecture_note_id: selected || null}),
        });
        app.recordingNoteSaved = selected;
        await actions.refreshStatus();
      } catch (error) { select.value = app.recordingNoteSaved; actions.showToast(error.message, "error"); }
      finally { app.captureSettingsSaving = false; renderNotePicker(); actions.renderCaptureControls(); }
    });
  });
  listen(ui.dockCoursePicker, "toggle", () => {
    if (ui.dockCoursePicker.open) $("#dockNotePicker").removeAttribute("open");
  });

  async function selectDockCourse(courseId) {
    if (!captureSettingsEditable()) return;
    if (app.recordingState === "idle") {
      await actions.selectCourse(courseId, {updateRoute: app.activeView === "courses"});
      actions.renderDashboard();
      return;
    }
    if (courseId === app.recordingCourseId) return;
    const recordingId = app.recordingId;
    app.captureSettingsSaving = true;
    actions.renderCaptureControls();
    recordingNoteSave = recordingNoteSave.then(async () => {
      try {
        await actions.flushCourseSave({requireSaved: true});
        const data = await requestJson(`/api/recordings/${recordingId}/course`, {
          method: "PATCH", headers: {"Content-Type":"application/json"}, body: JSON.stringify({course_id:courseId}),
        });
        app.recordingCourseId = data.recording.course_id || 0;
        actions.renderLibrary();
        app.recordingTitle = data.recording.title;
        ui.dockRecordingTitle.textContent = app.recordingTitle;
        app.recordingNoteSaved = "";
        $("#dockNoteSelect").replaceChildren(new Option("사용 안 함", ""));
        renderNotePicker();
        await updateRecordingSession(recordingId, {title:app.recordingTitle}).catch(() => {});
        await loadRecordingNotes();
        await actions.refreshStatus();
      } catch (error) { actions.showToast(`강의 변경 실패: ${error.message}`, "error"); }
      finally { app.captureSettingsSaving = false; actions.renderCaptureControls(); }
    });
    await recordingNoteSave;
  }

  async function startDockRecording() {
    if (app.recordingState !== "idle") return;
    ui.dockCoursePicker.removeAttribute("open");
    setRecorderState("starting");
    try {
      if (!await recoverRecordingSessions(app.recordings || [])) {
        throw new Error("이전 녹음 저장을 완료하지 못했습니다. 연결을 확인한 뒤 다시 시작해 주세요.");
      }
      await actions.flushCourseSave({requireSaved: true});

    } catch (error) {
      setRecorderState("idle");
      actions.showToast(error.message, "error");
      return;
    }
    app.recordingTitle = actions.automaticTitle();
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
        body: JSON.stringify({title: app.recordingTitle, extension, lecture_note_id: app.recordingNoteSaved || null}),
      });
      app.recordingId = prepared.recording.id;
      releaseOwnership = await acquireOwnership(app.recordingId);
      if (!releaseOwnership) throw new Error("녹음 소유권을 확보하지 못했습니다. 다른 탭을 확인해 주세요.");
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
        actions.showToast("브라우저 영구 저장소를 사용할 수 없어, 서버 중단 시 탭을 유지해야 합니다.", "error");
      }
      app.mediaRecorder.addEventListener("dataavailable", (event) => queueRecordingChunk(event.data));
      app.mediaRecorder.addEventListener("stop", async () => {
        stopMediaStream();
        setRecorderState("finalizing");
        try {
          await finalizeRecording();
        } catch (error) {
          actions.showToast(`${error.message} · 보관함에서 남은 녹음을 복구할 수 있습니다.`, "error");
          app.recordingId = null;
        } finally {
          releaseOwnership?.(); releaseOwnership = null;
          app.mediaRecorder = null;
          app.recordingTitle = "";
          app.recordingCourseId = null;
          setRecorderState("idle");
        }
      }, {once: true});
      app.mediaRecorder.start(1000);
      startSoundMeter(app.mediaStream);
      app.recordingStartedAt = Date.now();
      app.pausedAt = 0;
      app.pausedDuration = 0;
      ui.dockRecordingTitle.textContent = app.recordingTitle;
      updateClock();
      app.clockTimer = window.setInterval(updateClock, 250);
      setRecorderState("recording");
      void actions.startLive?.(app.recordingId, app.mediaStream);
      renderNotePicker();
      void loadRecordingNotes();
    } catch (error) {
      stopMediaStream();
      if (app.recordingId) {
        const abandoned = app.recordingId;
        app.recordingId = null;
        void fetch(`/api/recordings/${abandoned}`, {method: "DELETE"});
        void deleteRecordingRecovery(abandoned).catch(() => {});
      }
      releaseOwnership?.(); releaseOwnership = null;
      app.mediaRecorder = null;
      app.recordingTitle = "";
      app.recordingCourseId = null;
      setRecorderState("idle");
      actions.showToast(error.name === "NotAllowedError" ? "마이크 권한이 필요합니다." : `녹음을 시작하지 못했습니다: ${error.message}`, "error");
    }
  }

  function stopDockRecording() {
    if (!app.mediaRecorder || app.mediaRecorder.state === "inactive") return;
    if (!app.pausedAt) app.pausedAt = Date.now();
    ui.dockPauseButton.disabled = true;
    setRecorderState("finalizing");
    app.mediaRecorder.stop();
  }

  listen(ui.dockRecordButton, "click", () => {
    if (["recording", "paused"].includes(app.recordingState)) stopDockRecording();
    else void startDockRecording();
  });

  listen(ui.dockPauseButton, "click", () => {
    if (!app.mediaRecorder) return;
    if (app.mediaRecorder.state === "recording") {
      app.mediaRecorder.pause();
      actions.pauseLive?.(true);
      app.pausedAt = Date.now();
      setRecorderState("paused");
    } else if (app.mediaRecorder.state === "paused") {
      app.pausedDuration += Date.now() - app.pausedAt;
      app.pausedAt = 0;
      app.mediaRecorder.resume();
      actions.pauseLive?.(false);
      setRecorderState("recording");
    }
  });

  listen(window, "beforeunload", (event) => {
    if (!app.mediaRecorder || app.mediaRecorder.state === "inactive") return;
    event.preventDefault();
    event.returnValue = "";
  });

  // Unload cannot await IndexedDB/network work. Stop best-effort here; the next
  // page must finish durable recovery before it may create another recording.
  listen(window, "pagehide", () => {
    stopDockRecording();
  });

  listen(document, "visibilitychange", () => {
    if (document.visibilityState === "hidden" && app.mediaRecorder?.state === "recording") {
      try { app.mediaRecorder.requestData(); } catch (_) {}
    }
  });

  function preferredRecordingMimeType() {
    return [
      "audio/mp4;codecs=mp4a.40.2",
      "audio/mp4",
      "audio/webm;codecs=opus",
      "audio/webm",
    ].find((type) => MediaRecorder.isTypeSupported(type)) || "";
  }
  return {captureSettingsEditable, queueRecordingChunk, recoverRecordingSessions, renderNotePicker, requestJsonBeforeDeadline, restoreRecordingSession, retryFailedChunks, selectDockCourse, startDockRecording};
}
