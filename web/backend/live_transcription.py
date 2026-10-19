"""Bounded, disposable live drafts. Original recording intake never waits on inference."""
from __future__ import annotations

import json
import queue
import shutil
import struct
import subprocess
import threading
import time
import uuid
import wave
from contextlib import nullcontext
from collections import deque
from pathlib import Path

from .ai_runtime import runtime_for
from .ai_settings import defaults, using
from .ai_transport import request_json
from .atomic import write_text
from .engine import Transcriber
from .inference import inference
from .processes import CancellationToken, TranscriptionCancelled
from .services import start_thread
from .transcription import Options

RATE = 16000


class VadWorker:
    def __init__(self, config, platform, folder):
        binary = platform.executable(Path(config['binary']).parent, 'lecorder-vad')
        self.log = (folder / 'live-vad.log').open('ab')
        try:
            self.process = subprocess.Popen([str(binary), config['vad_path']], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=self.log, env=platform.library_environment([binary.parent, binary.parent.parent / 'src', binary.parent.parent / 'lib']),
                **platform.process_options())
        except BaseException:
            self.log.close()
            raise
        self.responses = queue.Queue()
        self.reader = start_thread(self._read, 'live-vad-output')
        self.writer = None

    def _read(self):
        try:
            for line in self.process.stdout:
                self.responses.put(json.loads(line))
        except Exception:
            pass
        finally:
            self.responses.put(None)

    def probabilities(self, pcm, cancellation):
        cancellation.check()
        def send():
            try:
                self.process.stdin.write(struct.pack('<I', len(pcm)//2) + pcm)
                self.process.stdin.flush()
            except OSError:
                self.responses.put(None)
        self.writer = start_thread(send, 'live-vad-input')
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            cancellation.check()
            try:
                value = self.responses.get(timeout=.1)
                if value is None:
                    raise RuntimeError('실시간 발화 감지 도구가 종료되었습니다.')
                return value
            except queue.Empty:
                pass
        raise RuntimeError('실시간 발화 감지 시간 초과')

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        if self.writer:
            self.writer.join(timeout=2)
        try:
            self.process.stdin.close()
        except OSError:
            pass
        self.reader.join(timeout=2)
        self.process.stdout.close()
        self.log.close()


class LiveSession:
    def __init__(self, service, recording_id, config):
        self.service, self.recording_id, self.config = service, recording_id, config
        self.id = uuid.uuid4().hex
        self.folder = service.folder(recording_id)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Condition(threading.RLock())
        self.lifetime = CancellationToken()
        self.token = self.lifetime
        self.input_generation = 0
        self.ai_paused = False
        self.resume_gap = False
        self.state, self.error = 'preparing', ''
        self.segments, self.gaps = [], []
        self.events = []
        self.pending = deque()
        self.pending_samples = 0
        self.buffered_samples = 0
        self.sequence, self.next_sample = -1, 0
        self.last_seen = time.monotonic()
        self.paused = False
        self.flush = False
        self.accepting = True
        self.version = 0
        self.result_clock = {}
        self.thread = None
        self.stopped = threading.Event()
        self.hint_key, self.options = None, None
        self.helper = None
        self.runtime = None

    def event(self, kind, **values):
        with self.lock:
            entry = {'event': kind, 'at': time.time(), **values}
            with (self.folder / 'live-events.jsonl').open('a', encoding='utf-8') as log:
                log.write(json.dumps(entry, ensure_ascii=False) + '\n')
            self.events.append(entry)
            self.events = self.events[-200:]

    def public(self):
        with self.lock:
            return {'session_id': self.id, 'recording_id': self.recording_id,
                    'state': self.state, 'error': self.error, 'model': 'large-v3-turbo',
                    'paused': self.paused, 'ai_paused': self.ai_paused, 'input_generation': self.input_generation,
                    'segments': list(self.segments), 'gaps': list(self.gaps), 'version': self.version,
                    'hint_warning': self.options.recognition_hint_warning if self.options else ''}

    def persist(self):
        write_text(self.folder / 'live-draft.json', json.dumps(self.public(), ensure_ascii=False))

    def gap(self, start, end, reason):
        with self.lock:
            self.gaps.append({'start': start / RATE, 'end': end / RATE, 'reason': reason})
            self.version += 1
        self.event('gap', start=start/RATE, end=end/RATE, reason=reason)

    def push(self, sequence, start, pcm, generation=None):
        if type(sequence) is not int or type(start) is not int or sequence < 0 or start < 0:
            raise ValueError('잘못된 실시간 음성 순번입니다.')
        if not pcm or len(pcm) % 2 or len(pcm) > RATE * 2 * 2:
            raise ValueError('실시간 음성 조각은 2초 이내 PCM16이어야 합니다.')
        with self.lock:
            if not self.accepting:
                raise ValueError('실시간 음성을 받을 수 없는 세션입니다.')
            self.last_seen = time.monotonic()
            if self.ai_paused or (generation is not None and generation != self.input_generation):
                return
            if sequence <= self.sequence:
                return
            if start < self.next_sample:
                raise ValueError('실시간 음성 샘플 위치가 겹칩니다.')
            if start > self.next_sample:
                self.gap(self.next_sample, start, 'AI 처리 일시정지' if self.resume_gap else '실시간 시작 준비 구간' if self.sequence < 0 else '음성 전송 누락')
            self.resume_gap = False
            self.sequence, self.next_sample = sequence, start + len(pcm)//2
            self.pending.append((start, pcm, time.monotonic()))
            self.pending_samples += len(pcm)//2
            if self.paused:
                self.flush = True
            while self.pending_samples + self.buffered_samples > 60 * RATE and self.pending:
                skipped, data, _ = self.pending.popleft()
                self.pending_samples -= len(data)//2
                self.gap(skipped, skipped + len(data)//2, '실시간 처리 지연')
            self.lock.notify_all()

    def control(self, paused):
        with self.lock:
            self.last_seen = time.monotonic()
            if paused and not self.paused:
                self.flush = True
            self.paused = paused
            self.lock.notify_all()

    def displayed(self, version):
        with self.lock:
            ended = self.result_clock.pop(version, None)
            if ended is not None and not self.stopped.is_set():
                self.event('displayed', version=version, latency_seconds=time.monotonic()-ended)

    def stop(self, reason='unspecified'):
        with self.lock:
            if self.lifetime.cancelled:
                return
            self.event('stop_requested', reason=reason)
            self.accepting = False
            self.pending.clear()
            self.pending_samples = 0
            self.lifetime.cancel()
            self.token.cancel()
            self.lock.notify_all()

    def _options(self):
        row = self.service.workflow.store.get_recording(self.recording_id)
        key = (row.language, row.note_snapshot_json, row.course_name)
        if key != self.hint_key:
            snapshot = json.loads(row.note_snapshot_json or '{}')
            terms = snapshot.get('keywords', [])
            self.options = Transcriber._with_note_hint(Options(language=row.language,
                course_name=row.course_name, note_keywords=tuple(terms), use_llm=False,
                retry_low_confidence=False), self.token)
            if snapshot.get('waiting_for_analysis'):
                from dataclasses import replace
                self.options = replace(self.options, recognition_hint_warning='강의노트 분석 대기 · 인식 힌트 없이 실시간 전사')
            self.hint_key = key
        return self.options

    def _decode(self, pcm, start, received, overlap, speech_ended=None, forced_cut=False):
        self.token.check()
        options = self._options()
        path = self.folder / 'live-window.wav'
        try:
            with wave.open(str(path), 'wb') as audio:
                audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(RATE)
                audio.writeframes(pcm)
            started = time.monotonic()
            self.event('segment_started', start=start/RATE, duration=len(pcm)/2/RATE,
                       language=options.language, hint_tokens=options.recognition_hint_tokens)
            from .request_history import request as tracked
            body = tracked(lambda token: request_json(self.config['base_url'] + '/inference', multipart={
                'file': path, 'language': options.language, 'prompt': options.recognition_hint,
                'response_format': 'verbose_json', 'no_context': 'true', 'max_context': '224',
                'vad': 'true', 'vad_threshold': '0.5', 'vad_min_silence_duration_ms': '700',
                'vad_speech_pad_ms': '200', 'temperature': '0', 'temperature_inc': '0',
                'max_len': '240', 'split_on_word': 'true', 'suppress_nst': 'true',
                'entropy_thold': '2.8', 'logprob_thold': '-1.0', 'no_speech_thold': '0.6',
                'beam_size': '-1', 'best_of': '1', 'no_language_probabilities': 'true'},
                cancellation=token, timeout=45), self.config, 'live_speech', None,
                self.token, len(pcm)/2/RATE, self.recording_id)
            self.token.check()
            result = Transcriber._whisper_result(body)
            if Transcriber._repetition_profile(result.segments)[0]:
                self.gap(start, start + len(pcm)//2, '반복 출력으로 초안 생략')
                return
            gate = getattr(self.service, 'ai_control', None)
            with (gate.condition if gate else nullcontext()), self.lock:
                self.token.check()
                if not self.accepting:
                    return
                for segment in result.segments:
                    text = segment.text.strip()
                    if not text or segment.no_speech_prob > .8:
                        continue
                    begin = start / RATE + segment.start
                    end = min((start + len(pcm)//2) / RATE, start / RATE + segment.end)
                    if end < begin:
                        continue
                    # Only remove identical boundary text inside the actual overlap.
                    if overlap and self.segments and begin < self.segments[-1]['end']:
                        if text == self.segments[-1]['text']:
                            continue
                        previous = self.segments[-1]['text'].split()
                        words = text.split()
                        for count in range(min(len(previous), len(words)), 1, -1):
                            if previous[-count:] == words[:count]:
                                text = ' '.join(words[count:]); break
                        begin = max(begin, self.segments[-1]['end'])
                    if text and end >= begin:
                        self.segments.append({'start': begin, 'end': end, 'text': text})
                self.version += 1
                self.result_clock = {self.version: speech_ended or received}
            self.event('segment', start=start/RATE, duration=len(pcm)/2/RATE,
                       processing_seconds=time.monotonic()-started, delivery_seconds=time.monotonic()-received,
                       speech_end_latency_seconds=time.monotonic()-(speech_ended or received), forced_cut=forced_cut)
            self.persist()
        finally:
            path.unlink(missing_ok=True)

    def set_ai_enabled(self, enabled):
        with self.lock:
            self.ai_paused = not enabled
            self.input_generation += 1
            discarded = self.pending_samples + self.buffered_samples
            if discarded:
                self.gap(max(0, self.next_sample-discarded), self.next_sample, 'AI 처리 전환으로 초안 생략')
            self.pending.clear()
            self.pending_samples = self.buffered_samples = 0
            self.flush = False
            self.resume_gap = True
            self.state = 'preparing' if enabled else 'ai_paused'
            self.event('ai_resumed' if enabled else 'ai_paused')
            self.lock.notify_all()

    def _cycle(self, token):
        self.token = token
        try:
            token.check()
            inference.reserve(self.id)
            queued = time.monotonic()
            with inference.lease(speech=True, owner=self.id, cancellation=token):
                self.event('resource_ready', waiting_seconds=time.monotonic()-queued)
                self.runtime = runtime_for(self.config)
                started = time.monotonic()
                with self.runtime.session(token), using({'speech': self.config, 'llm': defaults()['llm']}):
                    self.event('model_ready', processing_seconds=time.monotonic()-started)
                    self.helper = self.service.vad_factory(self.config, self.service.platform, self.folder)
                    with self.lock:
                        token.check()
                        self.state = 'streaming'
                    self._consume()
        finally:
            try:
                if self.helper:
                    self.helper.close()
                    self.helper = None
            finally:
                try:
                    if self.runtime:
                        self.runtime.release_idle()
                finally:
                    inference.release(self.id)

    def run(self):
        try:
            control = getattr(self.service, 'ai_control', None)
            if control:
                control.run(self._cycle, self.lifetime, kind='live')
            else:
                self._cycle(self.lifetime)
        except TranscriptionCancelled:
            pass
        except Exception as error:
            with self.lock:
                self.error = str(error)
                self.state = 'failed'
            self.event('failed', reason=str(error))
        finally:
            try:
                with self.lock:
                    self.accepting = False
                    self.pending.clear()
                    self.pending_samples = 0
                    if self.state != 'failed':
                        self.state = 'stopped'
                self.event('stopped')
                self.persist()
                write_text(self.folder / 'live-log.json', json.dumps(self.events, ensure_ascii=False))
            finally:
                self.accepting = False
                inference.release(self.id)
                self.stopped.set()

    def _consume(self):
        buffer = b''
        origin = 0
        overlap = False
        received = time.monotonic()
        while True:
            self.token.check()
            with self.lock:
                self.buffered_samples = len(buffer)//2
                if not self.pending and not self.flush:
                    self.lock.wait(.25)
                    continue
                if self.pending:
                    start, pcm, received = self.pending.popleft()
                    self.pending_samples -= len(pcm)//2
                    if buffer and start != origin + len(buffer)//2:
                        self.gap(origin, origin+len(buffer)//2, '구간 단절로 초안 생략')
                        buffer = b''
                        overlap = False
                    if not buffer:
                        origin = start
                    buffer += pcm
                    self.buffered_samples = len(buffer)//2
                    force = False
                else:
                    self.flush = False
                    force = True
            if not buffer:
                continue
            # Consume at most 20 seconds even for irregular network packet sizes.
            window = buffer[:20*RATE*2]
            probs = self.helper.probabilities(window, self.token)
            speech = [i for i, probability in enumerate(probs) if probability >= .5]
            duration = len(window)/2/RATE
            if not speech:
                # Retain a short pre-roll, never feed silence alone to Whisper.
                keep = min(len(buffer), int(.2*RATE)*2)
                origin += (len(buffer)-keep)//2
                buffer = buffer[-keep:]
                overlap = False
                continue
            last = min(duration, (speech[-1]+1)*512/RATE)
            quiet = duration - last
            ready = force or (duration >= 3 and quiet >= .7) or quiet >= 2 or duration >= 20
            if not ready:
                continue
            forced_cut = duration >= 20 and quiet < .7 and not force
            end_samples = len(window)//2 if forced_cut else min(len(window)//2, int((last+.2)*RATE))
            await_start = max(0, int(speech[0]*512 - .2*RATE))
            await_start = min(await_start, end_samples)
            speech_ended = received - (len(buffer)/2/RATE - last)
            self._decode(window[await_start*2:end_samples*2], origin+await_start, received, overlap,
                         speech_ended, forced_cut)
            consumed = len(window)//2 - (RATE if forced_cut else min(int(.2*RATE), len(window)//2))
            buffer = buffer[consumed*2:]
            origin += consumed
            overlap = forced_cut


class LiveTranscription:
    def __init__(self, workflow, platform, *, config=None, vad_factory=VadWorker, ai_control=None):
        self.workflow, self.platform = workflow, platform
        self.config = config or {**defaults()['speech'], 'model': 'large-v3-turbo',
            'model_path': str(Path(defaults()['speech']['model_path']).with_name('ggml-large-v3-turbo.bin'))}
        self.vad_factory = vad_factory
        self.ai_control = ai_control
        if ai_control:
            ai_control.listeners.append(self.set_ai_enabled)
        self.lock = threading.RLock()
        self.active = None
        self.closed = threading.Event()
        self.watcher = None

    def set_ai_enabled(self, enabled):
        with self.lock:
            if self.active and not self.active.stopped.is_set():
                self.active.set_ai_enabled(enabled)

    def start(self):
        if self.watcher is None:
            self.watcher = start_thread(self._watch, 'live-heartbeat')

    def folder(self, recording_id):
        return self.workflow.folder(recording_id).parent / '.live' / recording_id

    def forget(self, recording_id):
        self.end(recording_id)
        shutil.rmtree(self.folder(recording_id), ignore_errors=True)

    def has_draft(self, row):
        if row.note_path and row.review_status in {'completed', 'none'}:
            shutil.rmtree(self.folder(row.id), ignore_errors=True)
            return False
        return (self.folder(row.id) / 'live-draft.json').is_file() or (
            self.active is not None and self.active.recording_id == row.id
            and not self.active.stopped.is_set())

    def log(self, recording_id):
        path = self.folder(recording_id) / 'live-events.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines() if line] if path.is_file() else []

    def begin(self, recording_id):
        row = self.workflow.store.get_recording(recording_id)
        if row.status != 'recording' or row.source_kind != 'browser':
            raise ValueError('현재 녹음 중인 항목만 실시간 전사를 시작할 수 있습니다.')
        with self.lock:
            if self.closed.is_set():
                raise ValueError('앱이 종료 중입니다.')
            if self.active and not self.active.stopped.is_set():
                if self.active.recording_id == recording_id:
                    return self.active.public()
                raise ValueError('다른 녹음에서 실시간 전사를 사용 중입니다.')
            session = LiveSession(self, recording_id, dict(self.config))
            if self.ai_control and not self.ai_control.public()['enabled']:
                session.set_ai_enabled(False)
            self.active = session
            self.start()
            session.thread = start_thread(session.run, 'live-transcription')
            return session.public()

    def get(self, recording_id, session_id):
        with self.lock:
            session = self.active
            if not session or session.recording_id != recording_id or session.id != session_id:
                raise ValueError('만료된 실시간 전사 세션입니다.')
            return session

    def end(self, recording_id, session_id=None, *, reason="server_stop"):
        with self.lock:
            session = self.active
            if not session or session.recording_id != recording_id:
                return
            if session_id is not None and session.id != session_id:
                raise ValueError('만료된 실시간 전사 세션입니다.')
            session.stop(reason)
        if not session.stopped.wait(12):
            raise RuntimeError('실시간 전사 종료를 기다리고 있습니다.')

    def _watch(self):
        while not self.closed.wait(2):
            with self.lock:
                session = self.active
                if session and not session.stopped.is_set() and time.monotonic()-session.last_seen > 30:
                    session.event('heartbeat_expired')
                    session.stop('heartbeat_expired')

    def draft(self, recording_id):
        path = self.folder(recording_id) / 'live-draft.json'
        if not self.has_draft(self.workflow.store.get_recording(recording_id)):
            return None
        with self.lock:
            if self.active and self.active.recording_id == recording_id:
                return self.active.public()
        if path.is_file():
            value = json.loads(path.read_text())
            if value['state'] in {'preparing', 'streaming'}:
                value['state'] = 'stopped'
            return value
        return None

    def close(self):
        self.closed.set()
        with self.lock:
            session = self.active
        if session:
            self.end(session.recording_id, reason='app_shutdown')
        if self.watcher:
            self.watcher.join(timeout=3)
