import {requestJson} from "./api.js";
import {playerTime, markdownTimestamp} from "./selectors.js";
import {markdownMetadata} from "./dom_helpers.js";

export function createPlayer(context) {
  const {app, ui, $, $$, actions, listen, every} = context;
  function createAudioPlayer(source, label = "녹음", expectedDuration = 0, storageName = "") {
    const audio = document.createElement("audio");
    audio.preload = "auto";
    audio.playsInline = true;
    audio.preservesPitch = true;

    const player = document.createElement("section");
    player.className = "audio-player";
    player.tabIndex = 0;
    player.setAttribute("aria-label", `${label} 오디오 플레이어`);
    const play = document.createElement("button");
    play.type = "button";
    play.className = "audio-play";
    play.innerHTML = '<svg class="audio-play-symbol" viewBox="0 0 24 24" aria-hidden="true"><path d="m9 7 8 5-8 5z"/></svg>';
    play.setAttribute("aria-label", "재생");
    const rewind = document.createElement("button");
    rewind.type = "button";
    rewind.className = "audio-skip";
    rewind.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7.2 4.5v4h-4M7 8.3a7.5 7.5 0 1 1-2 7.7"/><text x="12" y="12">5</text></svg>';
    rewind.setAttribute("aria-label", "5초 뒤로");
    const forward = document.createElement("button");
    forward.type = "button";
    forward.className = "audio-skip";
    forward.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M16.8 4.5v4h4M17 8.3a7.5 7.5 0 1 0 2 7.7"/><text x="12" y="12">5</text></svg>';
    forward.setAttribute("aria-label", "5초 앞으로");
    const status = document.createElement("span");
    status.className = "audio-load-state sr-only";
    status.textContent = "불러오는 중";
    status.setAttribute("role", "status");

    const timeline = document.createElement("div");
    timeline.className = "audio-timeline";
    const elapsed = document.createElement("time");
    elapsed.textContent = "00:00:00";
    const scrubber = document.createElement("div");
    scrubber.className = "audio-scrubber";
    const buffered = document.createElement("span");
    buffered.className = "audio-buffered";
    const played = document.createElement("span");
    played.className = "audio-played";
    const range = document.createElement("input");
    range.type = "range";
    range.min = "0";
    range.max = "1";
    range.step = "0.05";
    range.value = "0";
    range.setAttribute("aria-label", "재생 위치");
    const duration = document.createElement("time");
    duration.textContent = "––:––:––";
    scrubber.append(buffered, played, range);
    timeline.append(play, elapsed, scrubber, duration, rewind, forward);
    player.append(audio, status, timeline);

    let pendingSeek = null;
    let pendingPlay = false;
    let loadError = false;
    let destroyed = false;
    let sourceReady = false;

    const knownDuration = () => Number.isFinite(audio.duration) && audio.duration > 0
      ? audio.duration
      : Math.max(0, Number(expectedDuration) || 0);
    const updateTimeline = () => {
      const total = knownDuration();
      const current = Math.max(0, Number(audio.currentTime) || pendingSeek || 0);
      const maximum = total || Math.max(current, pendingSeek || 0, 1);
      range.max = String(maximum);
      range.value = String(Math.min(current, maximum));
      elapsed.textContent = playerTime(current);
      duration.textContent = total ? playerTime(total) : "––:––:––";
      played.style.width = `${Math.min(100, current / maximum * 100)}%`;
      let bufferedEnd = 0;
      if (audio.buffered.length) bufferedEnd = audio.buffered.end(audio.buffered.length - 1);
      buffered.style.width = `${Math.min(100, bufferedEnd / maximum * 100)}%`;
    };
    const setPlaybackState = () => {
      const playing = !audio.paused && !audio.ended;
      play.innerHTML = playing
        ? '<svg class="audio-pause-symbol" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 7v10M15 7v10"/></svg>'
        : '<svg class="audio-play-symbol" viewBox="0 0 24 24" aria-hidden="true"><path d="m9 7 8 5-8 5z"/></svg>';
      play.setAttribute("aria-label", playing ? "일시정지" : "재생");
      player.classList.toggle("playing", playing);
    };
    const commitPendingSeek = () => {
      if (pendingSeek === null || audio.readyState < HTMLMediaElement.HAVE_METADATA) return false;
      const total = knownDuration();
      const target = total ? Math.min(pendingSeek, total) : pendingSeek;
      try {
        audio.currentTime = Math.max(0, target);
        pendingSeek = null;
        updateTimeline();
        return true;
      } catch (_) {
        return false;
      }
    };
    const requestPlay = () => {
      pendingPlay = true;
      status.textContent = "재생 준비 중";
      if (!sourceReady) return;
      void audio.play().catch((error) => {
        if (error?.name === "AbortError") return;
        pendingPlay = false;
        status.textContent = "재생할 수 없음";
        actions.showToast("오디오를 재생하지 못했습니다. 파일 형식이나 파일 상태를 확인해 주세요.", "error");
      });
    };
    const seekTo = (seconds, {autoplay = false, reveal = false} = {}) => {
      pendingSeek = Math.max(0, Number(seconds) || 0);
      commitPendingSeek();
      updateTimeline();
      if (autoplay) requestPlay();
      if (sourceReady && audio.readyState === HTMLMediaElement.HAVE_NOTHING) audio.load();
      if (reveal) player.scrollIntoView({block: "nearest", behavior: "smooth"});
    };
    const skip = (difference) => {
      const origin = pendingSeek ?? audio.currentTime ?? 0;
      seekTo(origin + difference, {autoplay: !audio.paused || pendingPlay});
    };

    play.addEventListener("click", () => {
      if (audio.paused || audio.ended) requestPlay();
      else audio.pause();
    });
    rewind.addEventListener("click", () => skip(-5));
    forward.addEventListener("click", () => skip(5));
    range.addEventListener("input", () => seekTo(Number(range.value)));
    player.addEventListener("keydown", (event) => {
      if (["INPUT", "BUTTON", "SELECT"].includes(event.target.tagName)) return;
      if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        event.preventDefault();
        skip(event.key === "ArrowLeft" ? -5 : 5);
      }
    });
    ["loadedmetadata", "durationchange", "progress", "canplay"].forEach((name) => {
      audio.addEventListener(name, () => {
        commitPendingSeek();
        updateTimeline();
        if (name === "canplay" && pendingPlay && audio.paused) requestPlay();
        if (!loadError && audio.readyState >= HTMLMediaElement.HAVE_FUTURE_DATA) {
          status.textContent = audio.ended ? "재생 완료" : audio.paused ? "준비됨" : "재생 중";
        }
      });
    });
    audio.addEventListener("timeupdate", updateTimeline);
    audio.addEventListener("seeking", () => { status.textContent = "이동 중"; });
    audio.addEventListener("seeked", () => { status.textContent = audio.paused ? "준비됨" : "재생 중"; });
    audio.addEventListener("waiting", () => { status.textContent = "버퍼링 중"; player.classList.add("buffering"); });
    audio.addEventListener("playing", () => {
      pendingPlay = false;
      status.textContent = "재생 중";
      player.classList.remove("buffering");
      setPlaybackState();
    });
    audio.addEventListener("pause", () => {
      if (!loadError && !audio.ended) status.textContent = "일시정지";
      setPlaybackState();
    });
    audio.addEventListener("ended", () => { status.textContent = "재생 완료"; setPlaybackState(); });
    audio.addEventListener("error", () => {
      loadError = true;
      pendingPlay = false;
      status.textContent = "오디오 오류";
      player.classList.add("error");
      play.disabled = true;
      rewind.disabled = true;
      forward.disabled = true;
      range.disabled = true;
    });
    updateTimeline();
    void (async () => {
      if (storageName) {
        status.textContent = "재생 정보 확인 중";
        try {
          const prepared = await requestJson("/api/storage/audio/prepare", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({name: storageName}),
          });
          if (prepared.duration > 0 && !expectedDuration) expectedDuration = prepared.duration;
        } catch (_) {
          // Older servers and files that cannot be remuxed still use the original source.
        }
      }
      if (destroyed) return;
      audio.src = source;
      sourceReady = true;
      status.textContent = "불러오는 중";
      updateTimeline();
      audio.load();
      if (pendingPlay) requestPlay();
    })();

    return {
      audio,
      element: player,
      seekTo,
      destroy() {
        destroyed = true;
        audio.pause();
        audio.removeAttribute("src");
        audio.load();
      },
    };
  }

  function seekPreviewAudio(player, seconds) {
    if (!player) {
      actions.showToast("연결된 오디오 파일이 없어 이 타임스탬프를 재생할 수 없습니다.", "error");
      return;
    }
    player.seekTo(seconds, {autoplay: true, reveal: false});
  }

  function appendMarkdownInline(container, source, audio) {
    const pattern = /(\[([^\]]+)\]\(([^)]+)\)|\*\*([^*]+)\*\*|`([^`]+)`|\*([^*]+)\*)/g;
    let cursor = 0;
    for (const match of source.matchAll(pattern)) {
      container.append(document.createTextNode(source.slice(cursor, match.index)));
      if (match[2] !== undefined) {
        const target = match[3].trim();
        const seconds = markdownTimestamp(target);
        const localAudio = /(?:^|\/)[^/]+\.(?:aac|aiff?|caf|flac|m4a|mp3|mp4|ogg|opus|wav|webm)(?:#|$)/i.test(target);
        if (seconds !== null || localAudio) {
          const button = document.createElement("button");
          button.type = "button";
          button.className = seconds !== null ? "markdown-timestamp" : "markdown-audio-link";
          if(seconds !== null)button.dataset.seconds=String(seconds);
          button.textContent = match[2];
          button.setAttribute("aria-label", seconds !== null ? `${match[2]}부터 오디오 재생` : "연결된 오디오 처음부터 재생");
          button.disabled = !audio;
          if (!audio) button.title = "연결된 오디오 파일이 없습니다.";
          button.addEventListener("click", () => seekPreviewAudio(audio, seconds ?? 0));
          container.append(button);
        } else if (/^https?:\/\//i.test(target)) {
          const link = document.createElement("a");
          link.href = target;
          link.target = "_blank";
          link.rel = "noreferrer";
          link.textContent = match[2];
          container.append(link);
        } else {
          container.append(document.createTextNode(match[2]));
        }
      } else {
        const element = document.createElement(match[4] !== undefined ? "strong" : match[5] !== undefined ? "code" : "em");
        element.textContent = match[4] ?? match[5] ?? match[6];
        container.append(element);
      }
      cursor = match.index + match[0].length;
    }
    container.append(document.createTextNode(source.slice(cursor)));
  }

  function renderMarkdownInto(container, source, audio) {
    container.replaceChildren();
    let markdown = String(source || "").replace(/\r\n/g, "\n");
    const frontmatter = markdown.match(/^---\n([\s\S]*?)\n---(?:\n|$)/);
    if (frontmatter) {
      const metadata = markdownMetadata(frontmatter[1]);
      if (metadata) container.append(metadata);
      markdown = markdown.slice(frontmatter[0].length);
    }
    const lines = markdown.split("\n");
    for (let index = 0; index < lines.length;) {
      const line = lines[index];
      if (!line.trim()) { index += 1; continue; }
      const fence = line.match(/^```\s*([\w-]*)/);
      if (fence) {
        const code = [];
        index += 1;
        while (index < lines.length && !lines[index].startsWith("```")) code.push(lines[index++]);
        index += 1;
        const pre = document.createElement("pre");
        const element = document.createElement("code");
        element.textContent = code.join("\n");
        if (fence[1]) element.dataset.language = fence[1];
        pre.append(element);
        container.append(pre);
        continue;
      }
      const heading = line.match(/^(#{1,6})\s+(.+)$/);
      if (heading) {
        const element = document.createElement(`h${Math.min(heading[1].length + 1, 6)}`);
        appendMarkdownInline(element, heading[2], audio);
        container.append(element);
        index += 1;
        continue;
      }
      const listMatch = line.match(/^\s*(?:[-*+]|(\d+)\.)\s+(.+)$/);
      if (listMatch) {
        const ordered = Boolean(listMatch[1]);
        const list = document.createElement(ordered ? "ol" : "ul");
        while (index < lines.length) {
          const item = lines[index].match(/^\s*(?:[-*+]|(\d+)\.)\s+(.+)$/);
          if (!item || Boolean(item[1]) !== ordered) break;
          const listItem = document.createElement("li");
          appendMarkdownInline(listItem, item[2], audio);
          list.append(listItem);
          index += 1;
        }
        container.append(list);
        continue;
      }
      if (/^\s*>/.test(line)) {
        const quote = document.createElement("blockquote");
        const content = [];
        while (index < lines.length && /^\s*>/.test(lines[index])) content.push(lines[index++].replace(/^\s*>\s?/, ""));
        appendMarkdownInline(quote, content.join(" "), audio);
        container.append(quote);
        continue;
      }
      if (/^\s*(?:-{3,}|\*{3,})\s*$/.test(line)) {
        container.append(document.createElement("hr"));
        index += 1;
        continue;
      }
      const paragraph = [];
      while (index < lines.length && lines[index].trim() && !/^(?:#{1,6}\s|```|\s*(?:[-*+] |\d+\. |>|-{3,}\s*$|\*{3,}\s*$))/.test(lines[index])) {
        paragraph.push(lines[index++].trim());
      }
      if (!paragraph.length) { index += 1; continue; }
      const element = document.createElement("p");
      appendMarkdownInline(element, paragraph.join(" "), audio);
      container.append(element);
    }
  }

  return {appendMarkdownInline, createAudioPlayer, renderMarkdownInto, seekPreviewAudio};
}
