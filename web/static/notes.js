import { pageNeedsReview } from "./note-review.js";
import { attachPageZoom } from "./note-zoom.js";
import { markdown } from "./note-markdown.js?v=20260921-math2";
/* Lecture-note UI. All model/document text is rendered as text, never HTML. */
const statuses = {
  queued: "대기 중",
  extracting: "원문 추출 중",
  ready: "전사에 사용 가능",
  partial: "일부 페이지 확인 필요",
  failed: "분석 실패",
  cancelled: "중단됨",
  pending: "대기 중",
  analyzing: "학습 정리 중",
  completed: "완료",
};
async function api(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "요청에 실패했습니다.");
  return data;
}
function node(tag, text, cls) {
  const e = document.createElement(tag);
  if (text) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}
function button(text, action, disabled = () => false) {
  const b = node("button", text);
  b.type = "button";
  b.onclick = async () => {
    b.disabled = true;
    try {
      await action();
    } catch (e) {
      alert(e.message);
    } finally {
      b.disabled = disabled();
    }
  };
  return b;
}
function modal(title) {
  const d = node("dialog", null, "note-dialog");
  const header = node("header");
  const titleId = `note-title-${crypto.randomUUID()}`;
  d.setAttribute("aria-labelledby", titleId);
  header.append(
    node("h2", title),
    button("닫기", () => d.close()),
  );
  header.querySelector("h2").id = titleId;
  d.append(header);
  document.body.append(d);
  d.addEventListener("close", () => d.remove(), { once: true });
  return d;
}

function createNoteRow(item) {
  const row = button("", () => openNote(item.id));
  row.className = "note-row";
  const icon = node("span", "", "note-document-icon");
  icon.setAttribute("aria-hidden", "true");
  const copy = node("span", null, "note-row-copy");
  copy.append(
    node("strong", item.title, "note-title"),
    node(
      "span",
      `${statuses[item.status]} · ${item.page_count || 0}쪽 · 학습 정리 ${item.needs_review ? "확인 필요" : statuses[item.study_status] || item.study_status}`,
      "note-muted",
    ),
  );
  const chevron = node("span", "›", "note-chevron");
  chevron.setAttribute("aria-hidden", "true");
  row.append(icon, copy, chevron);
  return row;
}

export async function showRecordingNote(host, noteId) {
  const list = node("div", null, "note-list");
  host.append(list);
  if (!noteId) {
    list.append(node("p", "사용 안 함", "note-muted"));
    return;
  }
  list.append(node("p", "강의노트를 불러오는 중입니다.", "note-muted"));
  try {
    const {note} = await api(`/api/notes/${encodeURIComponent(noteId)}`);
    if (!host.isConnected) return;
    list.replaceChildren(createNoteRow(note));
    if (note.deleted) list.append(node("p", "목록에서 제거된 강의노트입니다.", "note-muted"));
  } catch (error) {
    if (host.isConnected) list.replaceChildren(node("p", `강의노트를 불러오지 못했습니다. ${error.message}`, "note-muted"));
  }
}

let activeCourse = null,
  timer = null,
  requestSerial = 0;
export async function showCourseNotes(courseId) {
  activeCourse = courseId;
  clearTimeout(timer);
  const serial = ++requestSerial;
  const host = document.querySelector("#courseNotes");
  if (!host) return;
  host.replaceChildren();
  if (!courseId) return;
  const head = node("div", null, "note-toolbar");
  head.append(
    node("h3", "강의노트"),
    button("강의노트 추가", () => upload(courseId)),
  );
  const list = node("div", null, "note-list");
  host.append(head, list);
  async function refresh() {
    if (serial !== requestSerial) return;
    try {
      const { notes } = await api(`/api/courses/${courseId}/notes`);
      if (serial !== requestSerial) return;
      list.replaceChildren();
      if (!notes.length)
        list.append(
          node(
            "p",
            "PDF 또는 사진을 추가하세요.",
            "note-muted",
          ),
        );
      for (const item of notes) {
        list.append(createNoteRow(item));
      }
      if (
        notes.some(
          (n) =>
            ["queued", "extracting"].includes(n.status) ||
            ["pending", "analyzing"].includes(n.study_status),
        )
      )
        timer = setTimeout(refresh, 5000);
    } catch (e) {
      list.textContent = e.message;
    }
  }
  await refresh();
}

function upload(courseId) {
  const d = modal("강의노트 추가");
  d.classList.add("note-upload");
  const title = node("input");
  title.placeholder = "강의노트 이름";
  title.setAttribute("aria-label", "강의노트 이름");
  const input = node("input");
  input.type = "file";
  input.multiple = true;
  input.accept = ".pdf,.png,.jpg,.jpeg,.heic";
  input.setAttribute("aria-label", "강의노트 파일");
  const list = node("ol", null, "note-file-order");
  const status = node("p");
  let files = [];
  const render = () => {
    list.replaceChildren();
    files.forEach((f, i) => {
      const row = node("li");
      row.append(node("span", f.name));
      if (i)
        row.append(
          button("위로", () => {
            [files[i - 1], files[i]] = [files[i], files[i - 1]];
            render();
          }),
        );
      row.append(
        button("제외", () => {
          files.splice(i, 1);
          render();
        }),
      );
      list.append(row);
    });
  };
  input.onchange = () => {
    files = Array.from(input.files);
    render();
  };
  d.append(
    title,
    node("p", "PDF 한 개 또는 이미지 여러 장 · 합계 100MB, 최대 500쪽"),
    input,
    list,
    status,
    button("등록하고 분석", async () => {
      if (!files.length) throw new Error("파일을 선택하세요.");
      const data = new FormData();
      data.append("title", title.value);
      files.forEach((f) => data.append("files", f));
      status.textContent = "원본을 보존하는 중입니다.";
      await api(`/api/courses/${courseId}/notes`, {
        method: "POST",
        body: data,
      });
      d.close();
      await showCourseNotes(courseId);
    }),
  );
  d.showModal();
}

export async function openNote(id, initial = {}) {
  const d = modal("강의노트");
  d.classList.add("note-viewer");
  const state = node("p", null, "note-muted");
  const actions = node("nav", null, "note-toolbar");
  const tabs = node("nav", null, "note-tabs");
  const body = node("div", null, "note-body");
  const heading = node("div", null, "note-heading");
  const header = d.querySelector("header");
  heading.append(header.querySelector("h2"), state);
  header.prepend(heading);
  const controls = node("div", null, "note-controls");
  tabs.setAttribute("aria-label", "강의노트 보기");
  actions.setAttribute("aria-label", "강의노트 관리");
  controls.append(tabs, actions);
  const footer = node("footer", null, "note-footer");
  footer.hidden = true;
  d.append(controls, body, footer);
  let current = ({overview:'개요',pages:'페이지별 해설',keywords:'용어집',original:'원문'})[initial.tab] || "개요",
    data = null,
    pageNumber = Math.max(1, Number(initial.page) || 1),
    poll = null,
    refreshGeneration = 0,
    cancelling = false,
    fingerprint = "",
    etag = "",
    excluded = new Set(),
    dirty = false,
    zoom = 1,
    disposeZoom = () => {};
  for (const name of ["개요", "페이지별 해설", "용어집", "원문"])
    tabs.append(
      button(name, () => {
        current = name;
        render();
        body.scrollTop = 0;
      }),
    );
  const cancelDisabled = () => cancelling || !data || !(
    ["queued", "extracting"].includes(data.note.status) ||
    ["pending", "analyzing"].includes(data.note.study_status)
  );
  actions.append(
    button("다시 분석", async () => {
      await api(`/api/notes/${id}/reanalyze`, { method: "POST" });
      await refresh();
    }),
    button("분석 중단", async () => {
      cancelling = true;
      ++refreshGeneration; // Ignore a poll that began before cancellation.
      try {
        const result = await api(`/api/notes/${id}/cancel`, { method: "POST" });
        if (data) data.note = result.note;
        etag = "";
        await refresh();
      } finally {
        cancelling = false;
      }
    }, cancelDisabled),
    button("목록에서 제거", async () => {
      await api(`/api/notes/${id}`, { method: "DELETE" });
      d.close();
      await showCourseNotes(activeCourse);
    }),
  );
  actions.lastElementChild.classList.add("note-remove");
  function setCurrent(name) {
    current = name;
    render();
  }
  function pageIssue(page) {
    return pageNeedsReview(page, current === "원문");
  }

  function pageNavigation(pages) {
    const index = pages.findIndex((page) => page.number === pageNumber);
    const bar = node("div", null, "note-pagebar");
    const previous = button("‹", () => navigatePage(pages[Math.max(0, index - 1)]?.number));
    previous.setAttribute("aria-label", "이전 페이지");
    previous.disabled = index <= 0;
    const picker = node("details", null, "note-page-picker");
    const summary = node("summary", `${pageNumber}쪽`);
    summary.setAttribute("aria-label", "현재 페이지 선택");
    const choices = node("div", null, "note-page-options");
    choices.setAttribute("role", "listbox");
    pages.forEach((page) => {
      const choice = button(`${page.number}쪽${pageIssue(page) ? " · 확인 필요" : ""}`, () => {
        picker.open = false;
        navigatePage(page.number);
      });
      choice.setAttribute("role", "option");
      choice.setAttribute("aria-selected", String(page.number === pageNumber));
      choices.append(choice);
    });
    picker.append(summary, choices);
    const next = button("›", () => navigatePage(pages[Math.min(pages.length - 1, index + 1)]?.number));
    next.setAttribute("aria-label", "다음 페이지");
    next.disabled = index < 0 || index === pages.length - 1;
    const zoomOut = button("−", () => {});
    zoomOut.setAttribute("aria-label", "축소");
    const reset = button(`${Math.round(zoom * 100)}%`, () => {});
    reset.setAttribute("aria-label", "확대 비율, 클릭하면 100%로 초기화");
    const zoomIn = button("+", () => {});
    zoomIn.setAttribute("aria-label", "확대");
    bar.append(previous, picker, next, node("span", `전체 ${pages.length}쪽`, "note-muted"), node("span", null, "note-pagebar-spacer"), zoomOut, reset, zoomIn);
    return {bar, controls: {out:zoomOut, label:reset, in:zoomIn}};
  }
  function pageWorkspace(note, pages) {
    const page = pages.find((item) => item.number === pageNumber) || pages[0];
    if (!page) return node("p", "페이지를 추출하고 있습니다.", "note-muted");
    const workspace = node("div", null, "note-workspace");
    const left = node("section", null, "note-page-panel");
    const navigation = pageNavigation(pages);
    left.append(navigation.bar);
    const viewport = node("div", null, "note-page-viewport");
    const image = node("img");
    image.alt = `${page.number}쪽 원문`;
    image.style.width = `${zoom * 100}%`;
    if (page.image) image.src = `/api/notes/${id}/pages/${page.number}/image?revision=${note.revision_id}`;
    disposeZoom = attachPageZoom(viewport, image, navigation.controls, zoom, value => { zoom = value; });
    viewport.append(image); left.append(viewport);
    const right = node("section", null, "note-explanation-panel");
    const rightHead = node("header", null, "note-explanation-head");
    const title = node("div", null, "note-explanation-title");
    title.append(node("h3", current === "원문" ? "추출 원문" : `${page.number}쪽 해설`));
    if (pageIssue(page)) title.append(node("span", "확인 필요", "note-needs-review"));
    const pageActions = node("div", null, "note-toolbar");
    if (current === "페이지별 해설") {
      pageActions.append(button("상세 해설 요청", async () => { await api(`/api/notes/${id}/pages/${page.number}/explain`, {method:"POST"}); fingerprint = ""; await refresh(); }));
    }
    rightHead.append(title, pageActions); right.append(rightHead);
    const content = node("div", null, current === "원문" ? "note-extracted note-panel-content" : "note-study note-panel-content");
    if (current === "원문") {
      content.append(node("p", "OCR·내장 텍스트 추출 결과입니다. 원문 페이지와 비교해 확인하세요.", "note-muted"), node("pre", page.text || page.error || "추출한 텍스트가 없습니다."));
    } else {
      const explanation = page.detail || page.analysis;
      if (explanation?.source_excerpt) content.append(node("p", "이전 방식의 원문 발췌입니다. 다시 분석하면 이미지 근거 해설을 생성합니다.", "note-muted"));
      else if (explanation) content.append(markdown(explanation.markdown || [explanation.summary, ...(explanation.points || []).map((item) => `- ${item}`)].join("\n\n")));
      else content.append(node("p", "이 페이지의 학습 정리를 준비하고 있습니다.", "note-muted"));
      if (page.analysis_error) content.append(node("p", page.analysis_error, "note-error"));
      const structured = explanation?.validation?.version === "note-review-v2";
      if (structured && explanation.uncertainties?.length) {
        const warning = node("div", null, "note-error");
        warning.append(node("strong", "원문과 확인해 주세요"));
        explanation.uncertainties.forEach(message => warning.append(node("p", message)));
        content.append(warning);
      }
      const diagnosticMessages = structured
        ? [...new Set((explanation.validation.history || []).flatMap(attempt => [
            ...(attempt.generation_uncertainties || []),
            ...(attempt.findings || []).map(item => item.detail),
          ]))]
        : explanation?.uncertainties || [];
      if (diagnosticMessages.length) {
        const diagnostics = node("details", null, "note-diagnostics");
        diagnostics.append(node("summary", "검증 메모 보기"));
        const messages = node("div", null, "note-diagnostics-content");
        messages.append(node("p", "자동 검증 기록이며 수정 전의 지적도 포함합니다. 현재 확인할 내용은 위 안내를 참고하세요.", "note-muted"));
        diagnosticMessages.forEach(item => messages.append(node("p", item)));
        diagnostics.append(messages);
        content.append(diagnostics);
      }
      if (page.detail_status && page.detail_status !== "completed") content.append(node("p", `상세 해설: ${statuses[page.detail_status] || page.detail_status}`, "note-muted"));
    }
    right.append(content); workspace.append(left, right); return workspace;
  }
  function render() {
    if (!data) return;
    disposeZoom();
    body.replaceChildren();
    footer.replaceChildren();
    footer.hidden = current !== "용어집";
    body.classList.toggle("note-body-workspace", ["페이지별 해설", "원문"].includes(current));
    tabs
      .querySelectorAll("button")
      .forEach((b) =>
        b.setAttribute("aria-pressed", String(b.textContent === current)),
      );
    const { note, pages } = data;
    if (note.needs_reanalysis)
      body.append(
        node(
          "p",
          "이전 분석에는 잘못된 수식·설명이 있을 수 있습니다. ‘다시 분석’으로 원문 근거 검증을 적용하세요.",
          "note-error",
        ),
      );
    if (current === "용어집") {
      body.append(
        node(
          "p",
          "읽는 말은 음성 인식에, 정식 표기와 의미는 LLM 검수에 사용합니다.",
        ),
      );
      const busy = ["pending", "analyzing"].includes(note.study_status);
      const tools = node("div", null, "note-glossary-tools");
      const regenerate = button("용어집 다시 생성", async () => {
        await api(`/api/notes/${id}/glossary`, {method:"POST"});
        dirty = false; fingerprint = ""; await refresh();
      });
      regenerate.disabled = busy || dirty;
      tools.append(node("span", busy ? (note.keywords.length ? "용어집 준비 중 · 기존 확정 용어집을 표시합니다." : "용어집 준비 중") : "원문과 해설에 근거한 용어집", "note-muted"), regenerate);

      body.append(tools);
      if (note.error) body.append(node("p", note.error, "note-error"));
      const list = node("div", null, "note-keywords");
      for (const k of note.keywords) {
        const label = node("label");
        const check = node("input");
        check.type = "checkbox";
        check.disabled = busy;
        check.checked = !excluded.has(k.id);
        check.onchange = () => {
          dirty = true;
          regenerate.disabled = true;
          check.checked ? excluded.delete(k.id) : excluded.add(k.id);
          const count = footer.querySelector("span");
          if (count) count.textContent = `${note.keywords.length - excluded.size}개 선택 · 전사 인식에 활용합니다.`;
        };
        label.append(check, node("span", k.term));
        const row = node("div", null, "note-keyword-card");
        row.append(label);
        if (k.meaning) row.append(node("p", k.meaning, "note-term-meaning"));
        if (k.spoken_forms?.length) row.append(node("p", `읽는 말 · ${k.spoken_forms.join(" / ")}`, "note-term-spoken"));
        if (k.validation?.status === "needs_review") {
          row.append(node("span", "확인 필요", "note-needs-review"));
          for (const reason of k.validation.issues || []) row.append(node("p", reason, "note-term-spoken"));
        }
        const sources = [...new Set(k.pages)];
        const sourceList = node("div", null, "note-source-chips");
        sources.forEach((page) => sourceList.append(button(`${page}쪽`, () => showPage(page))));
        row.append(sourceList);
        list.append(row);
      }
      body.append(list);
      footer.append(
        node("span", `${note.keywords.length - excluded.size}개 선택 · 전사 인식에 활용합니다.`, "note-muted"),
        button("용어집 선택 저장", async () => {
          await api(`/api/notes/${id}/keywords`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ excluded: [...excluded] }),
          });
          dirty = false;
          fingerprint = "";
          await refresh();
        }),
      );
      footer.querySelector("button").disabled = busy;
      return;
    }
    if (current === "개요") {
      const overview = node("div", null, "note-overview");
      for (const group of note.overview || []) {
        if (group.error) {
          overview.append(node("p", group.error, "note-error"));
          continue;
        }
        overview.append(markdown(group.markdown || group.summary));
        for (const issue of group.uncertainties || []) overview.append(node("p", `확인 필요 · ${issue}`, "note-error"));
        if (group.missing_pages?.length)
          overview.append(
            node(
              "p",
              `아직 분석되지 않은 페이지: ${group.missing_pages.join(", ")}쪽`,
              "note-error",
            ),
          );
      }
      if (!note.overview?.length)
        overview.append(
          node(
            "p",
            "페이지별 학습 정리가 끝나면 전체 개요가 표시됩니다.",
            "note-muted",
          ),
        );
      body.append(overview);
      return;
    }
    body.append(pageWorkspace(note, pages));
  }
  function showPage(number) {
    current = "페이지별 해설";
    navigatePage(number);
  }
  function navigatePage(number) {
    if (!number) return;
    pageNumber = number;
    render();
    body.querySelector(".note-page-picker summary")?.focus({ preventScroll: true });
  }
  async function refresh() {
    if (!d.open) return;
    const generation = ++refreshGeneration;
    try {
      const response = await fetch(`/api/notes/${id}`, {
        headers: etag ? { "If-None-Match": etag } : {},
      });
      if (generation !== refreshGeneration || !d.open) return;
      if (response.status === 304) {
        if (data)
          state.textContent = `${statuses[data.note.status]} · 학습 정리 ${data.note.needs_review ? "확인 필요" : statuses[data.note.study_status]} ${data.note.error || ""}`;
        clearTimeout(poll);
        poll = setTimeout(refresh, 5000);
        return;
      }
      const next = await response.json();
      if (generation !== refreshGeneration || !d.open) return;
      if (!response.ok)
        throw new Error(next.error || "노트를 읽지 못했습니다.");
      etag = response.headers.get("ETag") || "";
      const fp = JSON.stringify(next);
      data = next;
      d.querySelector("h2").textContent = data.note.title;
      state.textContent = `${statuses[data.note.status]} · 학습 정리 ${data.note.needs_review ? "확인 필요" : statuses[data.note.study_status]} ${data.note.error || ""}`;
      actions.children[1].disabled = cancelDisabled();
      if (fp !== fingerprint && !dirty) {
        const position = body.scrollTop;
        fingerprint = fp;
        excluded = new Set(
          data.note.keywords.filter((k) => !k.included).map((k) => k.id),
        );
        render();
        body.scrollTop = position;
      }
    } catch (e) {
      state.textContent = e.message;
    }
    clearTimeout(poll);
    poll = setTimeout(refresh, 5000);
  }
  d.addEventListener(
    "close",
    () => {
      clearTimeout(poll);
      disposeZoom();
      showCourseNotes(activeCourse);
    },
    { once: true },
  );
  d.showModal();
  await refresh();
}
