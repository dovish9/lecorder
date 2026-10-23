import {requestJson} from './api.js';
import {frequencyBandLevel} from './selectors.js';

export function createLive(context) {
  const {app, actions, every} = context;
  let current = null;
  let previewBusy = false;
  const failures = new Map();
  const json = body => ({method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const base = id => `/api/recordings/${encodeURIComponent(id)}/live`;
  async function bounded(url, options) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 6000);
    try { return await requestJson(url, {...options, signal: controller.signal}); }
    finally { clearTimeout(timer); }
  }
  function disconnect(session) {
    session.node?.disconnect(); session.source?.disconnect();
    void session.audio?.close().catch(() => {});
  }
  function synchronize(session, live) {
    if (!live) return;
    if (session.generation !== live.input_generation || live.ai_paused) {
      session.queue = []; session.samples = 0;
      session.node?.port.postMessage({aiEnabled: !live.ai_paused, generation: live.input_generation});
    }
    session.generation = live.input_generation;
    session.aiPaused = live.ai_paused;
  }
  async function drain(session) {
    if (session.sending || !session.id) return;
    session.sending = true;
    try {
      while (current === session && !session.stopped && !session.aiPaused && app.aiControl?.enabled !== false && session.queue.length) {
        const item = session.queue.shift();
        session.samples -= item.pcm.length;
        // Explicit little-endian encoding, independent of the JS host architecture.
        const buffer = new ArrayBuffer(item.pcm.length * 2), view = new DataView(buffer);
        item.pcm.forEach((sample, index) => view.setInt16(index * 2, sample, true));
        try {
          await bounded(`${base(session.recordingId)}/${session.id}/pcm?sequence=${item.sequence}&start=${item.start}&generation=${item.generation ?? session.generation ?? 0}`,
            {method: 'PUT', headers: {'Content-Type': 'application/octet-stream'}, body: buffer});
          failures.delete(session.recordingId);
        } catch (error) {
          failures.set(session.recordingId, '실시간 음성 전송 지연 · 원음 녹음은 계속됩니다.');
          // Never block the durable MediaRecorder upload chain; the next sample offset exposes this gap.
        }
      }
    } finally { session.sending = false; }
  }
  async function startLive(recordingId, stream) {
    const session = {recordingId, id: null, queue: [], samples: 0, sequence: 0, stopped: false, paused: false};
    current = session; failures.delete(recordingId);
    try {
      session.audio = new AudioContext();
      await session.audio.audioWorklet.addModule('/static/dashboard/live-worklet.js');
      if (current !== session || session.stopped) { disconnect(session); return; }
      const elapsed = Math.max(0, (app.pausedAt || Date.now())-app.recordingStartedAt-(app.pausedDuration || 0));
      session.node = new AudioWorkletNode(session.audio, 'lecorder-live-pcm', {processorOptions: {offsetSamples: Math.round(elapsed*16)}});
      session.node.port.postMessage({paused: session.paused});
      session.source = session.audio.createMediaStreamSource(stream);
      session.node.port.onmessage = ({data}) => {
        if (current !== session || session.stopped) return;
        const sequence = session.sequence++;
        if (session.aiPaused || app.aiControl?.enabled === false ||
            (session.generation !== undefined && data.generation !== session.generation)) return;
        session.queue.push({...data, sequence, generation: session.generation});
        session.samples += data.pcm.length;
        while (session.samples > 60 * 16000) session.samples -= session.queue.shift().pcm.length;
        void drain(session);
      };
      session.source.connect(session.node);
      session.node.connect(session.audio.destination); // processor outputs silence
      await session.audio.resume();
      const result = await bounded(base(recordingId), json({}));
      session.id = result.live.session_id;
      synchronize(session, result.live);
      if (current !== session || session.stopped) {
        void bounded(`${base(recordingId)}/${session.id}/control`, json({stop: true, reason: "start_superseded"})).catch(() => {});
        disconnect(session); return;
      }
      void drain(session);
    } catch (error) {
      failures.set(recordingId, `실시간 음성 변환 시작 실패: ${error.message} · 원음 녹음은 계속됩니다.`);
      disconnect(session);
      if (session.id) void bounded(`${base(recordingId)}/${session.id}/control`, json({stop: true, reason: "start_failed"})).catch(() => {});
    }
  }
  function pauseLive(paused) {
    const session = current;
    if (!session) return;
    session.paused = paused;
    session.node?.port.postMessage({paused});
    if (session.id) void bounded(`${base(session.recordingId)}/${session.id}/control`, json({paused})).catch(() => {});
  }
  function stopLive(reason = "capture_stopped") {
    const session = current;
    current = null;
    if (!session) return;
    session.stopped = true; session.queue = []; disconnect(session);
    if (session.id) void bounded(`${base(session.recordingId)}/${session.id}/control`, json({stop: true, reason})).catch(() => {});
  }
  function livePreview(recording) {
    const panel = document.createElement('section'); panel.className = 'live-preview'; panel.dataset.liveRecording = recording.id;
    const header = document.createElement('header');
    const title = document.createElement('h3'); title.textContent = '음성 변환 내용 미리보기';
    const status = document.createElement('span'); status.className = 'live-preview-status';
    status.setAttribute('role', 'status'); status.textContent = '실시간 음성 변환 준비 중 · 초안';
    const body = document.createElement('div'); body.className = 'live-preview-text'; body.tabIndex = 0;
    body.setAttribute('aria-label', '실시간 음성 변환 초안');
    const viewport = document.createElement('div'); viewport.className = 'live-preview-viewport';
    const segments = document.createElement('div'); segments.className = 'live-preview-segments';
    const listening = document.createElement('div'); listening.className = 'live-preview-listening'; listening.hidden = true;
    const clock = document.createElement('time');
    const meter = document.createElement('span'); meter.className = 'live-preview-meter';
    meter.setAttribute('role', 'img'); meter.setAttribute('aria-label', '마이크 입력');
    const bars = Array.from({length: 25}, () => document.createElement('i'));
    meter.append(...bars); listening.append(clock, meter); body.append(segments, listening);
    const follow = document.createElement('button'); follow.type = 'button'; follow.className = 'live-preview-follow';
    follow.setAttribute('aria-label', '최신 내용으로'); follow.title = '최신 내용으로'; follow.hidden = true;
    follow.innerHTML = '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 5v14m-6-6 6 6 6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    let scrolling = 0;
    const cancelScroll = () => { cancelAnimationFrame(scrolling); scrolling = 0; };
    body.addEventListener('wheel', cancelScroll, {passive: true});
    body.addEventListener('touchstart', cancelScroll, {passive: true});
    body.addEventListener('keydown', cancelScroll);
    body.addEventListener('scroll', () => { follow.hidden = body.scrollHeight-body.scrollTop-body.clientHeight < 40; });
    follow.addEventListener('click', () => {
      cancelScroll();
      const from = body.scrollTop, began = performance.now();
      const duration = matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 180;
      const step = now => {
        if (!panel.isConnected) return;
        const progress = duration ? Math.min(1, (now-began)/duration) : 1;
        body.scrollTop = from + (body.scrollHeight-body.clientHeight-from)*(1-Math.pow(1-progress, 3));
        if (progress < 1) scrolling = requestAnimationFrame(step);
        else { scrolling = 0; follow.hidden = true; }
      };
      scrolling = requestAnimationFrame(step);
    });
    header.append(title, status); viewport.append(body, follow); panel.append(header, viewport);
    let levels = null;
    const draw = () => {
      if (!panel.isConnected) return;
      const active = app.recordingId === recording.id && ['recording', 'paused'].includes(app.recordingState);
      listening.hidden = !active;
      if (active) {
        const seconds = Math.floor(Math.max(0, (app.pausedAt || Date.now())-app.recordingStartedAt-(app.pausedDuration || 0))/1000);
        clock.textContent = `${Math.floor(seconds/60)}:${String(seconds%60).padStart(2, '0')}`;
        const analyser = app.audioAnalyser;
        const sounding = analyser && app.recordingState === 'recording';
        if (sounding) {
          if (levels?.length !== analyser.frequencyBinCount) levels = new Uint8Array(analyser.frequencyBinCount);
          analyser.getByteFrequencyData(levels);
        }
        bars.forEach((bar, index) => {
          const band = sounding ? frequencyBandLevel(levels, app.audioContext.sampleRate, analyser.fftSize, index, bars.length) : 0;
          bar.style.transform = `scaleY(${Math.max(.12, Math.min(1, Math.pow(Math.max(0, band-.025), .72)*1.3))})`;
        });
        meter.setAttribute('aria-label', sounding ? '마이크 입력' : '마이크 입력 일시정지');
      }
      requestAnimationFrame(draw);
    };
    requestAnimationFrame(draw);
    return panel;
  }
  async function refreshLivePreview() {
    const panel = document.querySelector('[data-live-recording]');
    if (!panel || previewBusy) return;
    previewBusy = true;
    try {
      const id = panel.dataset.liveRecording;
      const {live} = await bounded(base(id), {cache: 'no-store'});
      if (!panel.isConnected) return;
      const status = panel.querySelector('.live-preview-status'), body = panel.querySelector('.live-preview-text');
      const recording = app.recordings.find(item => item.id === id);
      const labels = {preparing: '실시간 음성 변환 준비 중 · 기존 요청 종료 및 모델 로드 대기', streaming: '실시간 음성 변환 중 · 초안', ai_paused: '실시간 받아쓰기 일시정지 · 원음 녹음 중', stopped: '최종 음성 변환 대기/진행 중 · 초안', failed: '실시간 음성 변환 실패 · 원음 보존'};
      status.textContent = failures.get(id) || live?.error || labels[live?.state] || '실시간 음성 변환 준비 중 · 초안';
      if (['preparing', 'streaming'].includes(live?.state) && (live.paused || (current?.recordingId === id && current.paused))) status.textContent = '녹음 일시정지 · 초안';
      if (['failed','cancelled','recoverable'].includes(recording?.status)) status.textContent = '최종 결과 없음 · 실시간 초안';
      if (recording?.status === 'recording' && (live?.ai_paused || (current?.recordingId === id && app.aiControl?.enabled === false))) status.textContent = '실시간 받아쓰기 일시정지 · 원음 녹음 중';
      if (live?.hint_warning) status.textContent += ` · ${live.hint_warning}`;
      if (live?.gaps?.length) status.textContent += ` · 초안 누락 ${live.gaps.length}구간 (원음 보존)`;
      if (!live) return;
      const segments = body.querySelector('.live-preview-segments');
      const fingerprint = `${live.session_id}:${live.version}`;
      if (panel.dataset.version === fingerprint) return;
      const follow = body.scrollHeight-body.scrollTop-body.clientHeight < 40;
      // Keep existing nodes and scroll position during append-only updates.
      if (panel.dataset.session !== live.session_id || segments.childElementCount > live.segments.length) segments.replaceChildren();
      panel.dataset.session = live.session_id;
      for (const segment of live.segments.slice(segments.childElementCount)) {
        const p = document.createElement('p'), time = document.createElement('time');
        time.textContent = `${Math.floor(segment.start/60)}:${String(Math.floor(segment.start%60)).padStart(2,'0')}`;
        p.append(time, document.createTextNode(segment.text)); segments.append(p);
      }
      panel.dataset.version = fingerprint;
      if (follow) body.scrollTop = body.scrollHeight;
      if (live.state === 'streaming') void bounded(`${base(id)}/${live.session_id}/control`, json({displayed_version: live.version})).catch(() => {});
    } catch (_) {} finally { previewBusy = false; }
  }
  every(() => {
    const session = current;
    if (session?.id && !session.stopped) {
      void bounded(`${base(session.recordingId)}/${session.id}/control`, json({paused: session.paused})).then(({live}) => {
        if (current !== session) return;
        synchronize(session, live);
        if (!['failed', 'stopped'].includes(live?.state)) return;
        session.stopped = true; session.queue = []; session.samples = 0;
        disconnect(session);
        failures.set(session.recordingId, live.error || '실시간 연결 종료 · 원음 녹음은 계속됩니다.');
      }).catch(() => {});
    }
  }, 1000);
  every(refreshLivePreview, 1000);
  return {startLive, stopLive, pauseLive, livePreview, refreshLivePreview};
}
