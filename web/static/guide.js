(() => {
  const parameters = new URLSearchParams(window.location.search);
  const embedded = parameters.has("embedded");
  if (embedded) document.documentElement.classList.add("embedded");

  const systemDarkMode = window.matchMedia("(prefers-color-scheme: dark)");
  function syncThemeColor() {
    document.querySelector("meta[name='theme-color']").content = systemDarkMode.matches ? "#1e1f22" : "#ecedef";
  }
  syncThemeColor();
  systemDarkMode.addEventListener?.("change", syncThemeColor);

  const search = document.querySelector("#guideSearch");
  const empty = document.querySelector("#guideSearchEmpty");
  const results = document.querySelector("#guideSearchResults");
  const sidebar = document.querySelector("#topicSidebar");
  const topicToggle = document.querySelector("#topicToggle");
  const links = [...document.querySelectorAll('.topic-sidebar a[href^="#"]')];
  const hero = document.querySelector(".hero");
  const notice = document.querySelector(".notice");
  const previous = document.querySelector("#guidePrevious");
  const next = document.querySelector("#guideNext");
  const sections = links.map((link) => ({
    id: link.hash.slice(1),
    title: link.textContent.trim(),
    link,
    section: document.querySelector(link.hash),
  })).filter((item) => item.section);

  if (embedded) sidebar.prepend(search.closest(".help-search"));

  function normalize(value) {
    return String(value || "").normalize("NFKC").toLocaleLowerCase("ko").replace(/\s+/g, " ").trim();
  }

  function closeTopics() {
    sidebar.classList.remove("open");
    topicToggle.setAttribute("aria-expanded", "false");
  }

  function activeId() {
    const requested = decodeURIComponent((window.location.hash || "#overview").slice(1));
    return sections.some((item) => item.id === requested) ? requested : "overview";
  }

  function showTopic(id, {focus = false} = {}) {
    const index = Math.max(0, sections.findIndex((item) => item.id === id));
    const active = sections[index];
    sections.forEach((item) => {
      const selected = item === active;
      item.section.hidden = !selected;
      item.link.classList.toggle("active", selected);
      if (selected) item.link.setAttribute("aria-current", "page");
      else item.link.removeAttribute("aria-current");
    });
    hero.hidden = active.id !== "overview";
    notice.hidden = active.id !== "overview";
    previous.hidden = index === 0;
    previous.href = index > 0 ? `#${sections[index - 1].id}` : "#overview";
    previous.textContent = index > 0 ? `‹ ${sections[index - 1].title}` : "";
    next.hidden = index === sections.length - 1;
    next.href = index < sections.length - 1 ? `#${sections[index + 1].id}` : `#${active.id}`;
    next.textContent = index < sections.length - 1 ? `${sections[index + 1].title} ›` : "";
    document.title = `${active.title} · Lecorder 도움말`;
    document.querySelector(".help-content").scrollTop = 0;
    closeTopics();
    if (focus) {
      const heading = active.section.querySelector("h2");
      if (heading) {
        heading.tabIndex = -1;
        heading.focus({preventScroll: true});
      }
    }
  }

  function snippetFor(section, query) {
    const value = normalize(section.textContent);
    const at = value.indexOf(query);
    const start = Math.max(0, at - 44);
    const end = Math.min(value.length, at + query.length + 70);
    return `${start ? "…" : ""}${value.slice(start, end)}${end < value.length ? "…" : ""}`;
  }

  function searchGuide() {
    const query = normalize(search.value);
    results.replaceChildren();
    results.hidden = !query;
    document.querySelector(".topic-sidebar nav").hidden = Boolean(query);
    if (!query) {
      empty.hidden = true;
      return;
    }
    const matches = sections.filter((item) => normalize(`${item.title} ${item.section.textContent}`).includes(query));
    empty.hidden = matches.length > 0;
    matches.forEach((item) => {
      const result = document.createElement("a");
      result.href = `#${item.id}`;
      result.className = "search-result";
      const title = document.createElement("strong");
      title.textContent = item.title;
      const snippet = document.createElement("span");
      snippet.textContent = snippetFor(item.section, query);
      result.append(title, snippet);
      result.addEventListener("click", () => {
        search.value = "";
        searchGuide();
      });
      results.append(result);
    });
  }

  links.forEach((link) => link.addEventListener("click", closeTopics));
  window.addEventListener("hashchange", () => showTopic(activeId(), {focus: true}));
  topicToggle.addEventListener("click", () => {
    const opening = !sidebar.classList.contains("open");
    sidebar.classList.toggle("open", opening);
    topicToggle.setAttribute("aria-expanded", String(opening));
  });
  search.addEventListener("input", searchGuide);
  search.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    search.value = "";
    searchGuide();
    search.blur();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !sidebar.classList.contains("open")) return;
    closeTopics();
    topicToggle.focus();
  });

  if (!sections.some((item) => `#${item.id}` === window.location.hash) && window.location.hash) {
    history.replaceState(null, "", "#overview");
  }
  showTopic(activeId());
  searchGuide();
})();
