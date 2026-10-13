import {requestJson} from "./api.js";
import {durationText} from "./selectors.js";

export function createReview(context) {
  const {app, ui, $, $$, actions, listen, every} = context;
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
      actions.showToast(error.message, "error");
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
      actions.showToast(error.message, "error");
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
        for (const [action, label] of [["reject", "거절"], ["accept", "승인하고 반영"]]) {
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
                if (app.activeCourseId === data.course.id) actions.renderCourse(data.course);
              }
              renderReviewItems(data.suggestions);
              await actions.refreshStatus();
            } catch (error) {
              actions.showToast(error.message, "error");
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


  return {appendHighlightedReviewContext, openReview, playAudioExcerpt, renderReviewItems, resetReviewAudioButton, setReviewListenLabel, stopReviewAudio};
}
