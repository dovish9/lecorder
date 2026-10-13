#!/usr/bin/env python3
"""Startup guidance from saved AI settings; never loads models or reads API keys."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from web.backend.ai_settings import settings


def describe(configs):
    names = {'whisper_cpp': 'whisper.cpp', 'llama_cpp': 'llama.cpp',
             'openai': 'OpenAI', 'claude': 'Claude', 'gemini': 'Gemini'}
    lines = []
    for area, label in [('speech', '음성 전사'), ('llm', 'LLM 검수·강의노트 분석')]:
        config = configs[area]
        provider = config['provider']
        managed = config.get('managed', False)
        mode = '로컬 자동 관리' if managed else ('사용자 지정 서버' if provider in {'whisper_cpp', 'llama_cpp'} else '외부 API')
        lines.append(f"• {label}: {names.get(provider, provider)} · {config['model']} ({mode})")
        if managed:
            lines.append(f"  요청 시 로드 · 연속 작업 재사용 · 유휴 {config.get('idle_seconds', 60)}초 후 해제")
            fields = {'binary': '서버 실행 파일', 'model_path': '모델 파일'}
            fields.update({'vad_path': 'VAD 모델'} if area == 'speech' else {'projector_path': '이미지 projector'})
            for field, title in fields.items():
                if not Path(config.get(field, '')).is_file():
                    lines.append(f'  ⚠️ {title}이 없습니다. 환경설정에서 파일 경로와 설치 상태를 확인하세요.')
        elif provider in {'whisper_cpp', 'llama_cpp'}:
            lines.append('  서버는 직접 실행해야 합니다. Lecorder가 시작·종료하지 않습니다.')
        else:
            lines.append('  작업 자료가 선택한 제공자로 전송되며 API 사용 요금이 발생할 수 있습니다.')
    lines.append('ℹ️ 연결 상태는 환경설정의 연결 확인으로 검사하세요. 시작 안내는 연결 성공을 의미하지 않습니다.')
    return lines


if __name__ == '__main__':
    for line in describe(settings.snapshot()):
        print(line)
