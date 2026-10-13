import {requestJson} from './api.js';

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
  async function drain(session) {
    if (session.sending || !session.id) return;
    session.sending = true;
    try {
      while (current === session && !session.stopped && session.queue.length) {
        const item = session.queue.shift();
        session.samples -= item.pcm.length;
        // Explicit little-endian encoding, independent of the JS host architecture.
        const buffer = new ArrayBuffer(item.pcm.length * 2), view = new DataView(buffer);
        item.pcm.forEach((sample, index) => view.setInt16(index * 2, sample, true));
        try {
          await bounded(`${base(session.recordingId)}/${session.id}/pcm?sequence=${item.sequence}&start=${item.start}`,
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
        session.queue.push({...data, sequence: session.sequence++});
        session.samples += data.pcm.length;
        while (session.samples > 60 * 16000) session.samples -= session.queue.shift().pcm.length;
        void drain(session);
      };
      session.source.connect(session.node);
      session.node.connect(session.audio.destination); // processor outputs silence
      await session.audio.resume();
      const result = await bounded(base(recordingId), json({}));
      session.id = result.live.session_id;
      if (current !== session || session.stopped) {
        void bounded(`${base(recordingId)}/${session.id}/control`, json({stop: true})).catch(() => {});
        disconnect(session); return;
      }
      void drain(session);
    } catch (error) {
      failures.set(recordingId, `실시간 전사 시작 실패: ${error.message} · 원음 녹음은 계속됩니다.`);
      disconnect(session);
      if (session.id) void bounded(`${base(recordingId)}/${session.id}/control`, json({stop: true})).catch(() => {});
    }
  }
  function pauseLive(paused) {
    const session = current;
    if (!session) return;
    session.paused = paused;
    session.node?.port.postMessage({paused});
    if (session.id) void bounded(`${base(session.recordingId)}/${session.id}/control`, json({paused})).catch(() => {});
  }
  function stopLive() {
    const session = current;
    current = null;
    if (!session) return;
    session.stopped = true; session.queue = []; disconnect(session);
    if (session.id) void bounded(`${base(session.recordingId)}/${session.id}/control`, json({stop: true})).catch(() => {});
  }
  function livePreview(recording) {
    const panel = document.createElement('section'); panel.className = 'live-preview'; panel.dataset.liveRecording = recording.id;
    const header = document.createElement('header');
    const title = document.createElement('h3'); title.textContent = '전사 내용 미리보기';
    const status = document.createElement('span'); status.className = 'live-preview-status';
    status.setAttribute('role', 'status'); status.textContent = '실시간 전사 준비 중 · 초안';
    const body = document.createElement('div'); body.className = 'live-preview-text'; body.tabIndex = 0;
    body.setAttribute('aria-label', '실시간 전사 초안');
    const follow = document.createElement('button'); follow.type = 'button'; follow.textContent = '최신 내용으로'; follow.hidden = true;
    body.addEventListener('scroll', () => { follow.hidden = body.scrollHeight-body.scrollTop-body.clientHeight < 40; });
    follow.addEventListener('click', () => { body.scrollTop = body.scrollHeight; follow.hidden = true; });
    header.append(title, status); panel.append(header, body, follow);
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
      const labels = {preparing: '실시간 전사 준비 중 · 기존 요청 종료 및 모델 로드 대기', streaming: '실시간 전사 중 · 초안', stopped: '최종 전사 대기/진행 중 · 초안', failed: '실시간 전사 실패 · 원음 보존'};
      status.textContent = failures.get(id) || live?.error || labels[live?.state] || '실시간 전사 준비 중 · 초안';
      if (['preparing', 'streaming'].includes(live?.state) && (live.paused || (current?.recordingId === id && current.paused))) status.textContent = '녹음 일시정지 · 초안';
      if (['failed','cancelled','recoverable'].includes(recording?.status)) status.textContent = '최종 결과 없음 · 실시간 초안';
      if (live?.hint_warning) status.textContent += ` · ${live.hint_warning}`;
      if (live?.gaps?.length) status.textContent += ` · 초안 누락 ${live.gaps.length}구간 (원음 보존)`;
      if (!live) return;
      const fingerprint = `${live.session_id}:${live.version}`;
      if (panel.dataset.version === fingerprint) return;
      const follow = body.scrollHeight-body.scrollTop-body.clientHeight < 40;
      // Keep existing nodes and scroll position during append-only updates.
      if (panel.dataset.session !== live.session_id || body.childElementCount > live.segments.length) body.replaceChildren();
      panel.dataset.session = live.session_id;
      for (const segment of live.segments.slice(body.childElementCount)) {
        const p = document.createElement('p'), time = document.createElement('time');
        time.textContent = `${Math.floor(segment.start/60)}:${String(Math.floor(segment.start%60)).padStart(2,'0')}`;
        p.append(time, document.createTextNode(segment.text)); body.append(p);
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
        if (current !== session || !['failed', 'stopped'].includes(live?.state)) return;
        session.stopped = true; session.queue = []; session.samples = 0;
        disconnect(session);
        failures.set(session.recordingId, live.error || '실시간 연결 종료 · 원음 녹음은 계속됩니다.');
      }).catch(() => {});
    }
  }, 5000);
  every(refreshLivePreview, 1000);
  return {startLive, stopLive, pauseLive, livePreview, refreshLivePreview};
}
