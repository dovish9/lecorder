import katex from "./vendor/katex/katex.mjs";

// Small, deliberately HTML-free Markdown renderer for locally generated notes.
// No model-provided HTML, URLs, styles or image requests are executed.
function inline(host, text) {
  const pattern =
    /(\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\]|\\\([\s\S]+?\\\)|\$[^$\n]+\$|`[^`]+`|\*\*[^*]+\*\*)/g;
  let offset = 0;
  for (const match of text.matchAll(pattern)) {
    host.append(document.createTextNode(text.slice(offset, match.index)));
    const token = match[0];
    const element = document.createElement(
      token.startsWith("**")
        ? "strong"
        : token.startsWith("`")
          ? "code"
          : "span",
    );
    if (token.startsWith("**")) inline(element, token.slice(2, -2));
    else if (token.startsWith("`")) element.textContent = token.slice(1, -1);
    else {
      const display = token.startsWith("$$") || token.startsWith("\\[");
      const trim = token.startsWith("\\") || token.startsWith("$$") ? 2 : 1;
      katex.render(token.slice(trim, -trim), element, {
        displayMode: display,
        throwOnError: false,
        trust: false,
        strict: "ignore",
        maxExpand: 200,
        maxSize: 20,
      });
    }
    host.append(element);
    offset = match.index + token.length;
  }
  host.append(document.createTextNode(text.slice(offset)));
}
export function markdown(text) {
  const host = document.createElement("article");
  host.className = "note-markdown";
  const lines = String(text || "")
    .replace(/\r\n/g, "\n")
    .split("\n");
  let paragraph = [],
    lists = [];
  function flush() {
    if (paragraph.length) {
      const p = document.createElement("p");
      inline(p, paragraph.join("\n"));
      host.append(p);
      paragraph = [];
    }
    lists = [];
  }
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.startsWith("```")) {
      flush();
      const content = [];
      while (++i < lines.length && !lines[i].startsWith("```"))
        content.push(lines[i]);
      const pre = document.createElement("pre"),
        code = document.createElement("code");
      code.textContent = content.join("\n");
      pre.append(code);
      host.append(pre);
      continue;
    }
    if (line.includes("|") && /^\s*\|?\s*:?-{3,}/.test(lines[i + 1] || "")) {
      flush();
      const table = document.createElement("table");
      const row = (value, tag) => {
        const tr = document.createElement("tr");
        value
          .trim()
          .replace(/^\||\|$/g, "")
          .split("|")
          .forEach((cell) => {
            const td = document.createElement(tag);
            inline(td, cell.trim());
            tr.append(td);
          });
        table.append(tr);
      };
      row(line, "th");
      i++;
      while (i + 1 < lines.length && lines[i + 1].includes("|"))
        row(lines[++i], "td");
      const wrapper = document.createElement("div");
      wrapper.className = "note-table";
      wrapper.append(table);
      host.append(wrapper);
      continue;
    }
    const heading = /^(#{1,6})\s+(.+)$/.exec(line);
    const item = /^(\s*)(?:([-*+])|(\d+)[.)])\s+(.+)$/.exec(line);
    if (heading) {
      flush();
      const h = document.createElement(
        `h${Math.min(heading[1].length + 1, 6)}`,
      );
      inline(h, heading[2]);
      host.append(h);
    } else if (item) {
      if (paragraph.length) flush();
      const type = item[2] ? "UL" : "OL";
      const indent = item[1].length;
      while (lists.length && lists.at(-1).indent > indent) lists.pop();
      if (
        lists.length &&
        lists.at(-1).indent === indent &&
        lists.at(-1).node.tagName !== type
      )
        lists.pop();
      if (!lists.length || lists.at(-1).indent < indent) {
        const list = document.createElement(type);
        const parent = lists.at(-1)?.node.lastElementChild || host;
        parent.append(list);
        lists.push({ indent, node: list });
      }
      const li = document.createElement("li");
      if (item[3]) li.value = Number(item[3]);
      inline(li, item[4]);
      lists.at(-1).node.append(li);
    } else if (!line.trim()) flush();
    else {
      lists = [];
      paragraph.push(line);
    }
  }
  flush();
  return host;
}
