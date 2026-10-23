export function markdownMetadata(frontmatter) {
  const labels = {
    course: "강의",
    recorded_at: "녹음 시각",
    duration: "길이",
    language: "언어",
    speech_model: "음성 인식",
    review_model: "문맥 검수",
    audio: "오디오",
  };
  const entries = frontmatter.split(/\r?\n/).map((line) => {
    const match = line.match(/^([\w-]+):\s*(.*)$/);
    if (!match || !labels[match[1]] || !match[2]) return null;
    let value = match[2];
    if (value.startsWith('"') && value.endsWith('"')) {
      try { value = JSON.parse(value); } catch (_) { value = value.slice(1, -1); }
    }
    return [labels[match[1]], value];
  }).filter(Boolean);
  if (!entries.length) return null;
  const details = document.createElement("details");
  details.className = "markdown-metadata";
  const summary = document.createElement("summary");
  summary.textContent = "문서 정보";
  const list = document.createElement("dl");
  entries.forEach(([label, value]) => {
    const term = document.createElement("dt");
    term.textContent = label;
    const description = document.createElement("dd");
    description.textContent = value;
    list.append(term, description);
  });
  details.append(summary, list);
  return details;
}

export function markdownDisclosureSummary(hasAudio) {
  const summary = document.createElement("summary");
  const icon = document.createElement("span");
  icon.className = "markdown-disclosure-icon";
  icon.setAttribute("aria-hidden", "true");
  icon.textContent = "›";
  const copy = document.createElement("span");
  const title = document.createElement("strong");
  title.textContent = "음성 변환 내용 미리보기";
  const description = document.createElement("small");
  description.textContent = hasAudio
    ? "타임스탬프를 눌러 해당 위치부터 재생할 수 있습니다."
    : "오디오 파일이 없어 타임스탬프 재생은 사용할 수 없습니다.";
  copy.append(title, description);
  summary.append(icon, copy);
  return summary;
}
