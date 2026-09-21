#!/usr/bin/env python3
"""Compare note study models on a cases.json fixture, without modifying live notes.

qwen3:8b is a text-only baseline. Other models receive the same text plus images.
Uses production generation/validation/retry and overview paths; saves raw attempts.
"""
import argparse
import json
import sys
import time
from pathlib import Path
import requests
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from web.backend import note_study as study
from web.backend.transcription import CancellationToken


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    parser.add_argument('--url', default='http://127.0.0.1:11435')
    args = parser.parse_args()
    cases = json.loads((args.folder / 'cases.json').read_text())
    study.OLLAMA_URL = args.url + '/api/chat'
    original = requests.post
    for model in ('qwen3:8b-q4_K_M', 'qwen3.5:9b', 'qwen3-vl:8b'):
        folder = args.folder / model.replace(':', '-')
        folder.mkdir(exist_ok=True)
        study.NOTE_VISION_MODEL = model
        pages = []
        try:
            for case in [*cases, {'id': 'overview'}]:
                output = folder / (case['id'] + '.json')
                if output.exists():
                    result = json.loads(output.read_text())
                else:
                    attempts = []
                    def capture(url, **kwargs):
                        payload = kwargs['json']
                        if model == 'qwen3:8b-q4_K_M':
                            for message in payload['messages']:
                                message.pop('images', None)
                                message['content'] = message['content'].replace('이미지와 텍스트를 함께 읽고', '추출 텍스트만 읽고').replace('첨부 이미지는 실제 강의노트 페이지이며 함께 주어진 텍스트는 OCR/추출 보조자료다.', '이미지는 제공되지 않는다. 추출 텍스트에서 불명확한 수식이나 도표는 추측하지 않는다.')
                        started = time.monotonic()
                        response = original(url, **kwargs)
                        attempts.append({'seconds': time.monotonic()-started, 'options': payload['options'], 'status': response.status_code, 'body': response.json()})
                        return response
                    study.requests.post = capture
                    start = time.monotonic()
                    try:
                        if case['id'] == 'overview':
                            value = study.build_overview(pages, CancellationToken())
                        else:
                            value = study.analyze_visual_page(case, [], CancellationToken(), image_path=case['image'])
                        result = {'output': value}
                    except Exception as error:
                        result = {'error': str(error)}
                    finally:
                        study.requests.post = original
                    result.update(seconds=time.monotonic()-start, attempts=attempts, model=model, input_mode='text' if model=='qwen3:8b-q4_K_M' else 'text+image')
                    output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
                if case['id'] != 'overview':
                    pages.append({'number':case['number'], 'analysis':result.get('output')})
                print(json.dumps({'model':model, 'case':case['id'], 'seconds':result['seconds'], 'error':result.get('error')}, ensure_ascii=False), flush=True)
        finally:
            original(args.url+'/api/generate', json={'model':model,'keep_alive':0},timeout=120).raise_for_status()

if __name__ == '__main__':
    main()
