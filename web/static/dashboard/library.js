import {showRecordingNote, openNote} from "../notes.js";
import {requestJson} from "./api.js";
import {libraryAlertCounts, visibleStorageItems, storageFileUrl, fileName, storageCoverage, recordingCategory, sortLibraryEntries, libraryRoute, libraryState, shortDate, workTimeText, durationText, hasPipelineLog} from "./selectors.js";
import {markdownDisclosureSummary} from "./dom_helpers.js";

export function createLibrary(context) {
  const {app, ui, $, $$, actions, listen, every} = context;
  function renderStorage(data) {
    const items = visibleStorageItems(data.items);
    const fingerprint = JSON.stringify({exists: data.exists, items});
    if (fingerprint === app.storageFingerprint) return;
    app.storageFingerprint = fingerprint;
    app.storageItems = items;
    renderLibrary();
    renderDashboard();
  }

  async function revealStorageFile(name) {
    if (!app.capabilities.reveal_file) { actions.showToast("이 운영체제에서는 파일 위치 열기를 지원하지 않습니다.", "error"); return; }
    try {
      await requestJson("/api/storage/reveal", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({name}),
      });
      actions.showToast("파일 위치를 열었습니다.", "success");
    } catch (error) {
      actions.showToast(error.message, "error");
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
      if (app.activeView === "recordings") actions.showToast(error.message, "error");
    }
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

  function notificationBadge(count) {
    const badge = document.createElement("span");
    badge.className = "notification-count";
    badge.textContent = String(count);
    badge.setAttribute("aria-label", `확인할 알림 ${count}개`);
    return badge;
  }

  function renderCourseAlertBadges() {
    const {byCourse} = libraryAlertCounts(app.libraryEntries || []);
    const selectedCourseId = (app.recordingState === "idle" ? app.activeCourseId : app.recordingCourseId) || 0;
    const title = ui.dockCourseLabel.parentElement;
    title.querySelector(".notification-count")?.remove();
    const selectedCount = byCourse.get(selectedCourseId) || 0;
    if (selectedCount) title.append(notificationBadge(selectedCount));
    for (const option of ui.dockCourseMenu.querySelectorAll("[data-course-id]")) {
      option.querySelector(".notification-count")?.remove();
      const count = byCourse.get(Number(option.dataset.courseId)) || 0;
      option.classList.toggle("has-alert", count > 0);
      if (count) option.append(notificationBadge(count));
    }
  }

  function renderLibrary() {
    app.libraryEntries = sortLibraryEntries(deriveLibraryEntries(), ui.librarySort.value);
    const query = ui.librarySearch.value.trim().toLocaleLowerCase("ko");
    const filter = ui.libraryFilter.value;
    const selectedCourseId = (app.recordingState === "idle" ? app.activeCourseId : app.recordingCourseId) || 0;
    const courseEntries = app.libraryEntries.filter(entry => (entry.recording?.course_id || 0) === selectedCourseId);
    const visible = courseEntries.filter((entry) => {
      const recording = entry.recording;
      const text = `${recording?.title || entry.storage.name} ${recording?.course_name || ""}`.toLocaleLowerCase("ko");
      return (!query || text.includes(query)) && (filter === "all" || entry.category === filter);
    });
    ui.libraryList.replaceChildren();
    const attention = libraryAlertCounts(app.libraryEntries).total;
    renderCourseAlertBadges();
    ui.libraryBadge.hidden = attention === 0;
    ui.libraryBadge.textContent = String(attention);
    if (!visible.length) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.textContent = "녹음이 없습니다.";
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
      const nameRow = document.createElement("span");
      nameRow.className = "library-name-row";
      nameRow.append(title);
      if (["review", "problem"].includes(entry.category)) nameRow.append(notificationBadge(1));
      copy.append(nameRow, meta);
      const state = document.createElement("span");
      state.className = "library-state";
      state.textContent = libraryState(entry);
      link.append(symbol, copy, state);
      ui.libraryList.append(link);
    });
    const selected = courseEntries.find((entry) => entry.key === app.selectedLibraryKey);
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
      reveal.textContent = "파일 위치";
      reveal.setAttribute("aria-label", `${name} 파일 위치 열기`);
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
          actions.renderRecordings(app.recordings.map((item) => item.id === recording.id ? data.recording : item));
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
        actions.showToast(recording ? "녹음 제목과 파일 이름을 수정했습니다." : "파일 제목을 수정했습니다.", "success");
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
    const confirmed = await actions.confirmAction(
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
        actions.renderRecordings(app.recordings.filter((item) => item.id !== entry.recording.id));
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
      actions.showToast(entry.recording ? "작업 기록과 연결된 파일을 삭제했습니다." : "저장 파일을 삭제했습니다.", "success");
    } catch (error) {
      button.disabled = false;
      actions.showToast(error.message, "error");
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
    if (final || !recording) panel.append(detailMenuButton("이름 수정", () => beginEntryTitleEdit(entry, title, menu)));
    if (hasPipelineLog(recording)) {
      panel.append(detailMenuButton("로그 확인", (button) => actions.openPipelineLog(recording, button)));
    }
    if (recording && ["queued", "processing"].includes(recording.status)) {
      panel.append(detailMenuButton("처리 중단", (button) => actions.cancelTranscription(recording, button)));
    }
    if (final) {
      panel.append(detailMenuButton("다시 전사", (button) => actions.retryRecording(recording, button)));
    } else if (interrupted) {
      panel.append(detailMenuButton("녹음 복구", (button) => actions.retryRecording(recording, button)));
    }
    if (recording?.status === "completed" && !["queued","processing"].includes(recording.review_status)) {
      panel.append(detailMenuButton("다시 검수", async () => {
        await requestJson(`/api/recordings/${recording.id}/review`, {method:"POST"});
        await actions.refreshStatus();
      }));
    }
    if (recording && ["queued", "processing"].includes(recording.review_status)) {
      panel.append(detailMenuButton("검수 중단", async (button) => {
        button.disabled = true;
        try {
          await requestJson(`/api/recordings/${encodeURIComponent(recording.id)}/review/cancel`, {method: "POST"});
          await actions.refreshStatus();
          actions.showToast("검수를 중단했습니다. 기존 전사 결과는 유지됩니다.", "success");
        } catch (error) {
          actions.showToast(error.message, "error");
        } finally {
          button.disabled = false;
        }
      }));
    }
    if (final || interrupted) panel.append(detailMenuButton("기록 삭제", (button) => actions.deleteRecordingHistory(recording, button), {danger: true}));
    if (final || (!recording && entry.storage?.files?.length)) {
      panel.append(detailMenuButton("파일까지 삭제", (button) => deleteEntryWithFiles(entry, button), {danger: true}));
    }
    menu.append(trigger, panel);
    return menu;
  }

  function renderLibraryDetail(entry) {
    actions.stopReviewAudio();
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
      review.textContent = ({queued:"검수 대기",processing:"검수 중",completed:"검수 완료",failed:"검수 실패",cancelled:"검수 중단"}[entry.recording.review_status] || "") + (entry.recording.review_error ? ` · ${entry.recording.review_error}` : "");
      header.append(review);
    }
    const hintWarning = entry.recording?.quality?.settings?.recognition_hint_warning;
    if (hintWarning) {
      const warning = document.createElement("p");
      warning.textContent = hintWarning;
      header.append(warning);
    }
    ui.libraryDetail.append(back, header);

    if (entry.recording) {
      const noteSection = detailSection("선택한 강의노트");
      ui.libraryDetail.append(noteSection);
      void showRecordingNote(noteSection, entry.recording.lecture_note_id);
    }

    if (entry.recording?.source_kind === "browser" && (entry.recording.has_live_draft || entry.recording.status === 'recording')) {
      ui.libraryDetail.append(actions.livePreview(entry.recording));
      void actions.refreshLivePreview();
    }
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
        detailAudioPlayer = actions.createAudioPlayer(
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
        const searchPosition=new URLSearchParams(window.location.hash.split('?')[1]||'').get('time');
        note.addEventListener("toggle", async () => {
          if (!note.open || note.dataset.loaded) return;
          note.dataset.loaded = "true";
          try {
            const response = await fetch(storageFileUrl(entry.storage.markdown_files[0]), {cache: "no-store"});
            if (!response.ok) throw new Error("Markdown을 불러오지 못했습니다.");
            actions.renderMarkdownInto(previewText, await response.text(), detailAudioPlayer);
            if(searchPosition!==null) {
              const stamps=[...previewText.querySelectorAll('.markdown-timestamp[data-seconds]')];
              const match=stamps.filter(button=>Number(button.dataset.seconds)<=Number(searchPosition)).at(-1)||stamps[0];
              match?.scrollIntoView({block:'center',behavior:'smooth'});
            }
          } catch (error) { previewText.textContent = error.message; }
        });
        files.append(note);
        if(searchPosition!==null)note.open=true;
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
      void actions.openReview(recording.id, {focus: false});
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
    const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    icon.setAttribute("viewBox", "0 0 24 24");
    icon.setAttribute("aria-hidden", "true");
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", "M6 3h8l4 4v14H6z M14 3v5h4 M9 12h6 M9 16h6");
    icon.append(path);
    link.append(icon, copy, state);
    return link;
  }

  function dashboardNoteRow(note) {
    const link = document.createElement("a");
    link.className = "dashboard-row";
    link.href = "#/courses";
    link.addEventListener("click", (event) => {
      event.preventDefault();
      void openNote(note.note_id).catch((error) => actions.showToast(error.message, "error"));
    });
    const copy = document.createElement("span");
    const title = document.createElement("strong");
    title.textContent = note.title;
    const meta = document.createElement("small");
    const pages = Number(note.page_count) || 0;
    const completed = note.stage === "extracting" ? note.extracted_pages : note.analyzed_pages;
    const stage = {extracting: "원문 추출", study: "해설 생성", detail: `${note.number}쪽 상세 해설`}[note.stage] || "분석 준비";
    meta.textContent = `${note.course_name || "강의노트"} · ${stage}${pages ? ` · ${completed || 0}/${pages}쪽` : ""}`;
    copy.append(title, meta);
    const state = document.createElement("span");
    state.textContent = note.status === "queued" ? "대기" : "분석 중";
    const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    icon.setAttribute("viewBox", "0 0 24 24");
    icon.setAttribute("aria-hidden", "true");
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", "M6 3h8l4 4v14H6z M14 3v5h4 M9 12h6 M9 16h6");
    icon.append(path);
    link.append(icon, copy, state);
    return link;
  }

  function renderDashboard() {
    ui.dashboardDate.textContent = new Intl.DateTimeFormat("ko-KR", {year: "numeric", month: "long", day: "numeric", weekday: "long"}).format(new Date());
    const entries = deriveLibraryEntries();
    const activeOrder = {processing: 0, cancelling: 0, queued: 1, recoverable: 2, recording: 2};
    const active = entries.filter((entry) => entry.category === "active")
      .sort((left, right) => (activeOrder[left.recording.status] ?? 9) - (activeOrder[right.recording.status] ?? 9));
    const attention = entries.filter((entry) => ["review", "problem"].includes(entry.category))
      .sort((left, right) => {
        const priority = (entry) => entry.recording?.status === "failed" ? 0 : entry.recording?.status === "recoverable" ? 1 : entry.category === "review" ? 2 : 3;
        return priority(left) - priority(right);
      });
    const noteJobs = (app.noteJobs || []).filter((note) => ["processing", "queued"].includes(note.status));
    const today = new Date();
    const completedToday = entries.filter((entry) => {
      if (entry.category !== "completed") return false;
      const value = new Date(entry.recording?.completed_at || entry.timestamp);
      return !Number.isNaN(value.getTime()) && value.toDateString() === today.toDateString();
    });
    const processingSeconds = completedToday.reduce((sum, entry) => sum + Number(entry.recording?.total_processing_seconds || entry.recording?.processing_seconds || 0), 0);
    ui.dashboardCompletedCount.textContent = String(completedToday.length);
    ui.dashboardWorkTime.textContent = processingSeconds ? durationText(processingSeconds) : "—";
    ui.dashboardAttentionMetric.textContent = String(attention.length);
    ui.dashboardAttentionMore.textContent = attention.length > 4 ? `검토함 ${attention.length}개 모두 보기` : "검토함 열기";
    $("#dashboardQueueCount").textContent = active.length + noteJobs.length;
    $("#dashboardAttentionCount").textContent = attention.length;
    const fill = (container, items, emptyText) => {
      container.replaceChildren();
      if (!items.length) {
        const empty = document.createElement("div"); empty.className = "empty-state"; empty.textContent = emptyText; container.append(empty); return;
      }
      items.forEach((entry) => container.append(dashboardRow(entry, libraryState(entry))));
    };
    ui.dashboardQueue.replaceChildren();
    const activeItems = [...active, ...noteJobs];
    if (!activeItems.length) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.textContent = app.courses.length ? "진행 중인 작업이 없습니다. 하단 도구에서 녹음하거나 파일을 가져올 수 있습니다." : "강의를 추가하면 하단 도구에서 녹음을 시작할 수 있습니다.";
      ui.dashboardQueue.append(empty);
    } else {
      active.forEach((entry) => ui.dashboardQueue.append(dashboardRow(entry, libraryState(entry))));
      noteJobs.forEach((note) => ui.dashboardQueue.append(dashboardNoteRow(note)));
    }
    fill(ui.dashboardAttention, attention.slice(0, 4), "확인이 필요한 항목이 없습니다.");
  }

  listen(ui.librarySearch, "input", renderLibrary);
  listen(ui.libraryFilter, "change", renderLibrary);
  listen(ui.librarySort, "change", renderLibrary);


  return {beginEntryTitleEdit, createDetailMenu, dashboardRow, deleteEntryWithFiles, deriveLibraryEntries, detailMenuButton, detailSection, notificationBadge, refreshStorage, renderCourseAlertBadges, renderDashboard, renderLibrary, renderLibraryDetail, renderLibraryEmpty, renderStorage, revealStorageFile, storageFileRow};
}
