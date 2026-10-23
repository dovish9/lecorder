#!/usr/bin/env python3
"""Replay a real 16 kHz mono PCM16 WAV at microphone speed, isolated from app data."""
import argparse
import json
import math
import hashlib
import sys
import tempfile
import time
import wave
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from web.backend.ai_settings import defaults
from web.backend.config import EnvironmentStore, ROOT
from web.backend.inference import inference
from web.backend.routes import create_app
from web.backend.services import Services
from web.backend.library.store import LectureStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--language', choices=('ko', 'en', 'auto'), default='auto')
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--final', action='store_true', help='Also run full-source transcription and LLM review')
    parser.add_argument('--port', type=int, default=18080)
    args = parser.parse_args()
    with wave.open(str(args.source), 'rb') as source:
        if (source.getframerate(), source.getnchannels(), source.getsampwidth()) != (16000, 1, 2):
            parser.error('Input must be 16 kHz mono PCM16 WAV')
        pcm = source.readframes(source.getnframes())
    with tempfile.TemporaryDirectory(prefix='lecorder-live-check-') as temporary:
        root = Path(temporary); (root/'output').mkdir()
        env = EnvironmentStore(root/'.env', ROOT)
        env.update(project_dir=str(ROOT), output_dir=str(root/'output'))
        config = defaults()
        config['speech']['base_url'] = f'http://127.0.0.1:{args.port}'
        config['speech']['live_base_url'] = f'http://127.0.0.1:{args.port+2}'
        config['llm']['base_url'] = f'http://127.0.0.1:{args.port+1}'
        (root/'ai-settings.json').write_text(json.dumps({'settings': config}))
        services = Services.create(root)
        store = LectureStore(root/'test.db', env)
        create_app(store=store, services=services)
        try:
            with services.scope():
                row = services.workflow.begin_chunked(replace(store.get(), language=args.language), 'Live verification', '.wav')
                with args.source.open('rb') as original:
                    services.workflow.save_chunk(row.id, 0, original)
                services.live.begin(row.id)
                session = services.live.active
                deadline = time.monotonic()+180
                while session.state == 'preparing' and time.monotonic() < deadline:
                    time.sleep(.1)
                assert session.state == 'streaming', session.public()
                pid = session.runtime._process.pid
                stream = pcm + b'\0'*16000*2*3
                began = time.monotonic()
                for sequence, start in enumerate(range(0, len(stream)//2, 16000)):
                    time.sleep(max(0, began+sequence+1-time.monotonic()))
                    session.push(sequence, start, stream[start*2:(start+16000)*2])
                session.control(True)
                deadline = time.monotonic()+15
                while session.pending_samples and time.monotonic()<deadline: time.sleep(.1)
                time.sleep(3)
                assert session.runtime._process.pid == pid, 'Live model was reloaded during recording'
                report = session.public()
                services.live.end(row.id)
                assert services.live.draft(row.id)['state'] not in {'preparing','streaming'}, 'Persisted live draft could not be reopened'
                report['events'] = services.live.log(row.id)
                latencies = sorted(e['speech_end_latency_seconds'] for e in report['events'] if e['event']=='segment' and not e['forced_cut'])
                report['server_result_latency_p95_seconds'] = latencies[math.ceil(.95*len(latencies))-1] if latencies else None
                assert not inference.reserved and session.runtime.state == 'idle'
                assert report['segments'], 'No speech was recognized'
                assert all(s['start'] <= s['end'] <= len(stream)/32000 for s in report['segments'])
                if args.final:
                    services.workflow.finalize_chunked(row.id, len(pcm)/32000)
                    services.workflow.process_next()
                    completed = store.get_recording(row.id)
                    assert completed.status == 'completed', completed.error
                    services.workflow.reviews.process(row.id)
                    completed = store.get_recording(row.id)
                    assert completed.review_status == 'completed', completed.review_error
                    final_segments = json.loads(completed.segments_json)
                    assert final_segments and all(0 <= s['start'] <= s['end'] <= len(pcm)/32000+.1 for s in final_segments)
                    assert hashlib.sha256(Path(completed.audio_path).read_bytes()).digest() == hashlib.sha256(args.source.read_bytes()).digest(), 'Original WAV changed'
                    assert not services.live.has_draft(completed), 'Draft was not removed after final success'
                    report['final'] = completed.public()
                    report['final_segments'] = final_segments
                    with store.transaction() as db:
                        report['review_runs'] = [dict(r) for r in db.execute('SELECT status,metrics,error FROM review_runs WHERE recording_id=?', (row.id,))]
                    report['final_markdown'] = Path(completed.note_path).read_text()
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2))
                print(json.dumps({'segments': len(report['segments']), 'gaps': len(report['gaps']), 'p95_server_result_seconds': report['server_result_latency_p95_seconds'], 'final_verified': args.final}))
        finally:
            services.close()


if __name__ == '__main__':
    main()
