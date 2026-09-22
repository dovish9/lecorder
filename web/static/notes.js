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
function button(text, action) {
  const b = node("button", text);
  b.type = "button";
  b.onclick = async () => {
    b.disabled = true;
    try {
      await action();
    } catch (e) {
      alert(e.message);
    } finally {
      b.disabled = false;
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
      `${statuses[item.status]} · ${item.page_count || 0}쪽 · 학습 정리 ${statuses[item.study_status] || item.study_status}`,
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
            "PDF, 사진 또는 PowerPoint 자료를 추가하세요.",
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
  input.accept = ".pdf,.pptx,.png,.jpg,.jpeg,.heic";
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
    node("p", "PDF/PPTX 한 개 또는 이미지 여러 장 · 합계 100MB, 최대 500쪽"),
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

export async function openNote(id) {
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
  let current = "개요",
    data = null,
    pageNumber = 1,
    poll = null,
    fingerprint = "",
    etag = "",
    excluded = new Set(),
    dirty = false;
  for (const name of ["개요", "페이지별 해설", "용어집", "원문"])
    tabs.append(
      button(name, () => {
        current = name;
        render();
        body.scrollTop = 0;
      }),
    );
  actions.append(
    button("다시 분석", async () => {
      await api(`/api/notes/${id}/reanalyze`, { method: "POST" });
      await refresh();
    }),
    button("분석 중단", async () => {
      await api(`/api/notes/${id}/cancel`, { method: "POST" });
      await refresh();
    }),
    button("목록에서 제거", async () => {
      await api(`/api/notes/${id}`, { method: "DELETE" });
      d.close();
      await showCourseNotes(activeCourse);
    }),
  );
  function render() {
    if (!data) return;
    body.replaceChildren();
    footer.replaceChildren();
    footer.hidden = current !== "용어집";
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
          "포함된 키워드 중 적정량을 자동 선택합니다. 직접 인식 힌트는 입력하지 않습니다.",
        ),
      );
      const list = node("div", null, "note-keywords");
      for (const k of note.keywords) {
        const label = node("label");
        const check = node("input");
        check.type = "checkbox";
        check.checked = !excluded.has(k.id);
        check.onchange = () => {
          dirty = true;
          check.checked ? excluded.delete(k.id) : excluded.add(k.id);
        };
        label.append(check, node("span", k.term));
        const row = node("div");
        row.append(label);
        const sources = [...new Set(k.pages)];
        const jump = node("select");
        jump.setAttribute("aria-label", `${k.term} 출처 페이지`);
        const placeholder = new Option(`출처 ${sources.length}곳`, "");
        placeholder.disabled = true;
        placeholder.selected = true;
        jump.append(placeholder);
        for (const page of sources) jump.append(new Option(`${page}쪽`, page));
        jump.onchange = () => {
          showPage(Number(jump.value));
        };
        row.append(jump);
        list.append(row);
      }
      body.append(list);
      footer.append(
        node("span", "선택한 키워드를 전사 인식에 활용합니다.", "note-muted"),
        button("키워드 선택 저장", async () => {
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
        if (group.missing_pages?.length)
          overview.append(
            node(
              "p",
              `아직 분석되지 않은 페이지: ${group.missing_pages.join(", ")}쪽`,
              "note-error",
            ),
          );
        const sources = node("div", null, "note-sources");
        for (const number of group.pages || [])
          sources.append(button(`${number}쪽`, () => showPage(number)));
        overview.append(sources);
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
    const navigation = node("select");
    navigation.setAttribute("aria-label", "강의노트 페이지");
    for (const p of pages)
      navigation.append(
        new Option(
          `${p.number}쪽${p.needs_review ? " · 확인 필요" : ""}`,
          p.number,
        ),
      );
    navigation.value = String(pageNumber);
    navigation.onchange = () => {
      navigatePage(Number(navigation.value));
    };
    const pagebar = node("div", null, "note-pagebar");
    const previous = button("‹", () => {
      navigatePage(
        pages[Math.max(0, pages.findIndex((p) => p.number === pageNumber) - 1)]
          ?.number,
      );
    });
    previous.setAttribute("aria-label", "이전 페이지");
    previous.disabled = pages[0]?.number === pageNumber;
    const next = button("›", () => {
      navigatePage(
        pages[
          Math.min(
            pages.length - 1,
            pages.findIndex((p) => p.number === pageNumber) + 1,
          )
        ]?.number,
      );
    });
    next.setAttribute("aria-label", "다음 페이지");
    next.disabled = pages.at(-1)?.number === pageNumber;
    pagebar.append(
      previous,
      navigation,
      next,
      node("span", `전체 ${pages.length}쪽`, "note-muted"),
    );
    body.append(pagebar);
    const page = pages.find((p) => p.number === pageNumber) || pages[0];
    if (!page) {
      body.append(node("p", "페이지를 추출하고 있습니다."));
      return;
    }
    const split = node("div", null, "note-split");
    const image = node("img");
    image.alt = `${page.number}쪽 원문`;
    if (page.image)
      image.src = `/api/notes/${id}/pages/${page.number}/image?revision=${note.revision_id}`;
    const text = node(
      "div",
      null,
      current === "원문" ? "note-extracted" : "note-study",
    );
    if (current === "원문") {
      text.append(
        node("h3", "추출한 원문 · 디버깅"),
        node("pre", page.text || page.error || "추출한 텍스트가 없습니다."),
      );
      if (page.needs_review)
        text.append(
          node(
            "p",
            "OCR 또는 수식 판독 확인이 필요한 페이지입니다.",
            "note-muted",
          ),
        );
    } else {
      const explanation = page.detail || page.analysis;
      if (explanation?.source_excerpt)
        text.append(
          node(
            "p",
            "이전 원문 발췌 결과입니다. 다시 분석하면 이미지 기반 학습 정리를 생성합니다.",
            "note-muted",
          ),
        );
      else if (explanation) {
        text.append(
          markdown(
            explanation.markdown ||
              [
                explanation.summary,
                ...(explanation.points || []).map((x) => `- ${x}`),
              ].join("\n\n"),
          ),
        );
        if (explanation.uncertainties?.length) {
          const warning = node("div", null, "note-error");
          warning.append(node("strong", "확인 필요"));
          for (const item of explanation.uncertainties)
            warning.append(node("p", item));
          text.append(warning);
        }
      } else
        text.append(
          node("p", "이 페이지의 학습 정리를 준비하고 있습니다.", "note-muted"),
        );
      if (page.analysis_error)
        text.append(node("p", page.analysis_error, "note-error"));
      text.append(
        button("상세 해설 요청", async () => {
          await api(`/api/notes/${id}/pages/${page.number}/explain`, {
            method: "POST",
          });
          fingerprint = "";
          await refresh();
        }),
      );
      if (page.detail_status && page.detail_status !== "completed")
        text.append(
          node(
            "p",
            `상세 해설: ${statuses[page.detail_status] || page.detail_status}`,
            "note-muted",
          ),
        );
    }
    const preview = node("div", null, "note-preview");
    preview.append(image);
    split.append(preview, text);
    body.append(split);
  }
  function showPage(number) {
    current = "페이지별 해설";
    navigatePage(number);
  }
  function navigatePage(number) {
    if (!number) return;
    pageNumber = number;
    render();
    body.scrollTop = 0;
    body.querySelector("select")?.focus({ preventScroll: true });
  }
  async function refresh() {
    if (!d.open) return;
    try {
      const response = await fetch(`/api/notes/${id}`, {
        headers: etag ? { "If-None-Match": etag } : {},
      });
      if (response.status === 304) {
        if (data)
          state.textContent = `${statuses[data.note.status]} · 학습 정리 ${statuses[data.note.study_status]} ${data.note.error || ""}`;
        clearTimeout(poll);
        poll = setTimeout(refresh, 5000);
        return;
      }
      const next = await response.json();
      if (!response.ok)
        throw new Error(next.error || "노트를 읽지 못했습니다.");
      etag = response.headers.get("ETag") || "";
      const fp = JSON.stringify(next);
      data = next;
      d.querySelector("h2").textContent = data.note.title;
      state.textContent = `${statuses[data.note.status]} · 학습 정리 ${statuses[data.note.study_status]} ${data.note.error || ""}`;
      actions.children[1].hidden = !(
        ["queued", "extracting"].includes(data.note.status) ||
        ["pending", "analyzing"].includes(data.note.study_status)
      );
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
      showCourseNotes(activeCourse);
    },
    { once: true },
  );
  d.showModal();
  await refresh();
}
