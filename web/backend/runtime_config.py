"""Documented operational settings shared by .env and runtime consumers."""
import math
import os

# name: (default, minimum, maximum, description). Strings use None bounds.
GROUPS = {
    '앱과 저장 경로 (상대 경로는 프로젝트 기준)': {
        'LECORDER_ENV_FILE': ('.env', None, None, '시작할 환경설정 파일. 다른 환경을 쓰려면 실행 전에 지정'),
        'LECORDER_DIR': ('.', None, None, '프로젝트 폴더'),
        'LECORDER_OUTPUT_DIR': ('output', None, None, '원본 음성과 Markdown 결과 폴더'),
        'LECORDER_DATA_DIR': ('web/data', None, None, 'DB·녹음 조각·노트·실행 로그 폴더'),
        'LECORDER_AI_SETTINGS': ('', None, None, 'AI 설정 JSON 저장 파일. 비우면 DATA_DIR/ai-settings.json'),
        'LECORDER_DEPENDENCIES_DIR': ('dependencies', None, None, '로컬 도구·모델·토크나이저 폴더'),
        'LECORDER_NO_BROWSER': (0, 0, 1, '1이면 start.command가 브라우저를 자동으로 열지 않음'),
        'LECORDER_PORT': (5055, 1, 65535, '대시보드 포트 (로컬 연결)'),
    },
    '설치와 빌드': {
        'LECORDER_PYTHON_VERSION': ('3.13', None, None, 'pyenv에서 사용할 Python minor 버전 (3.11 이상)'),
        'LECORDER_BUILD_JOBS': (4, 1, 128, '네이티브 도구 빌드 병렬 작업 수'),
        'LECORDER_LLAMA_DIR': ('', None, None, 'llama.cpp 실제 소스 폴더. 비우면 기존 링크 또는 ~/Local Storage/llama.cpp'),
    },
    '최종 음성 변환': {
        'LECORDER_SPEECH_PROVIDER': ('whisper_cpp', None, None, 'whisper_cpp / openai'),
        'LECORDER_SPEECH_MANAGED': (True, None, None, '앱이 로컬 서버 시작·종료 관리'),
        'LECORDER_SPEECH_BASE_URL': ('http://127.0.0.1:8080', None, None, '서버 URL'),
        'LECORDER_SPEECH_MODEL': ('large-v3', None, None, '외부 OpenAI 음성 변환은 whisper-1'),
        'LECORDER_SPEECH_BINARY': ('', None, None, '비우면 dependencies의 whisper-server'),
        'LECORDER_SPEECH_MODEL_PATH': ('', None, None, '비우면 기본 large-v3 모델'),
        'LECORDER_SPEECH_VAD_PATH': ('', None, None, '비우면 기본 Silero 모델'),
        'LECORDER_SPEECH_THREADS': (0, 0, 256, 'Whisper CPU 스레드 수. 0이면 서버 기본값'),
        'WHISPER_IDLE_SECONDS': (60, 0, 3600, '로컬 음성 모델 유휴 해제 시간'),
    },
    '실시간 음성 변환 (로컬 자동 관리, 실행 파일은 최종 음성과 공유)': {
        'LECORDER_LIVE_BASE_URL': ('http://127.0.0.1:8082', None, None, '최종 음성·LLM과 다른 포트'),
        'LECORDER_LIVE_MODEL': ('large-v3-turbo', None, None, '실시간 모델 ID'),
        'LECORDER_LIVE_MODEL_PATH': ('', None, None, '비우면 기본 turbo 모델'),
        'LECORDER_LIVE_VAD_PATH': ('', None, None, '비우면 기본 Silero 모델'),
        'LECORDER_LIVE_BUFFER_SECONDS': (60, 20, 600, '미처리 PCM 최대 보관 시간'),
        'LECORDER_LIVE_MIN_SECONDS': (3.0, .1, 20, '일반 발화 최소 누적 시간'),
        'LECORDER_LIVE_MAX_SECONDS': (20.0, 3, 60, '연속 발화 최대 구간'),
        'LECORDER_LIVE_SILENCE_SECONDS': (.7, .1, 5, '발화 종료 무음'),
        'LECORDER_LIVE_SHORT_SILENCE_SECONDS': (2.0, .1, 10, '짧은 발화 종료 무음'),
        'LECORDER_LIVE_PADDING_SECONDS': (.2, .01, 1, '발화 앞뒤 여유'),
        'LECORDER_LIVE_OVERLAP_SECONDS': (1.0, 0, 3, '최대 길이 절단 시 겹침'),
        'LECORDER_LIVE_VAD_THRESHOLD': (.5, 0, 1, '발화 확률 임계값'),
        'LECORDER_LIVE_HEARTBEAT_SECONDS': (30.0, 10, 300, '브라우저 생존 신호 만료 시간'),
        'LECORDER_LIVE_REQUEST_SECONDS': (45.0, 5, 600, '실시간 음성 변환 요청 제한 시간'),
        'LECORDER_LIVE_VAD_TIMEOUT_SECONDS': (10.0, 1, 60, 'VAD 요청 제한 시간'),
    },
    'LLM 제공자와 로컬 서버': {
        'LECORDER_LLM_PROVIDER': ('llama_cpp', None, None, 'llama_cpp / openai / claude / gemini'),
        'LECORDER_LLM_MANAGED': (True, None, None, '앱이 로컬 서버 관리'),
        'LECORDER_LLM_BASE_URL': ('http://127.0.0.1:8081', None, None, '서버 URL'),
        'LECORDER_LLM_MODEL': ('Qwen3.5-9B', None, None, 'LLM 모델 ID'),
        'LECORDER_LLM_BINARY': ('', None, None, '비우면 dependencies의 llama-server'),
        'LECORDER_LLM_MODEL_PATH': ('', None, None, '비우면 기본 Qwen GGUF'),
        'LECORDER_LLM_PROJECTOR_PATH': ('', None, None, '비우면 기본 이미지 projector'),
        'LECORDER_LLM_IDLE_SECONDS': (60, 0, 3600, 'LLM 유휴 해제 시간'),
        'LECORDER_LLM_CONTEXT_TOKENS': (16384, 2048, 262144, '로컬 모델 컨텍스트. 늘리면 메모리 사용 증가'),
        'LECORDER_LLM_THREADS': (0, 0, 256, 'LLM CPU 스레드 수. 0이면 서버 기본값'),
        'LECORDER_LLM_GPU_LAYERS': (99, -1, 999, 'GPU로 올릴 레이어 수'),
        'LECORDER_LLM_BATCH_SIZE': (512, 1, 8192, '프롬프트 처리 배치'),
        'LECORDER_LLM_UBATCH_SIZE': (128, 1, 8192, '물리 마이크로 배치'),
    },
    '외부 API 키 (선택, 비우면 운영체제 보안 저장소 사용)': {
        'LECORDER_SPEECH_API_KEY': ('', None, None, '외부 음성 API 키. 실제 키를 예제 파일에 넣지 마세요'),
        'LECORDER_LLM_API_KEY': ('', None, None, '선택한 LLM 제공자의 API 키'),
    },
    '연결·모델 수명주기': {
        'LECORDER_AI_REQUEST_SECONDS': (600.0, 5, 7200, '일반 AI 요청 제한 시간'),
        'LECORDER_MODEL_LOAD_SECONDS': (180.0, 1, 1800, '로컬 모델 로드 제한 시간'),
        'LECORDER_MODEL_STOP_SECONDS': (5.0, .1, 60, '정상 종료 대기 시간'),
        'LECORDER_AI_RETRIES': (1, 0, 5, '일시적인 API 오류의 최대 재시도 횟수'),
        'LECORDER_AI_RETRY_DELAY_SECONDS': (1.0, 0, 60, 'API 재시도 전 대기 시간'),
        'LECORDER_SETTINGS_CHECK_SECONDS': (55.0, 1, 1800, '설정 저장 전 연결 검증 제한 시간'),
    },
    '음성 변환 구간·인식 힌트·품질 재시도': {
        'LECORDER_SPEECH_CHUNK_SECONDS': (120.0, 20, 1800, '최종 음성 변환 목표 구간 길이'),
        'LECORDER_SPEECH_CUT_SEARCH_SECONDS': (12.0, 0, 60, '목표 경계 주변 무음 탐색'),
        'LECORDER_SPEECH_OVERLAP_SECONDS': (2.0, 0, 10, '무음 경계가 없을 때 앞뒤 겹침'),
        'LECORDER_HINT_MAX_TOKENS': (96, 0, 224, '인식 힌트 토큰 상한'),
        'LECORDER_SPEECH_LOW_CONFIDENCE': (-.65, -10, 0, '저신뢰 음성 변환 기준 log probability'),
        'LECORDER_SPEECH_RETRY_MAX_GROUPS': (4, 0, 100, '품질 재변환 최대 묶음 수'),
        'LECORDER_SPEECH_RETRY_MAX_SECONDS': (90.0, 0, 3600, '품질 재변환 음성 시간 예산'),
        'LECORDER_SPEECH_TEMPERATURE': (0.0, 0, 1, '초기 디코딩 온도'),
        'LECORDER_SPEECH_TEMPERATURE_INC': (.2, 0, 1, 'Whisper 온도 증가량'),
        'LECORDER_SPEECH_ENTROPY_THRESHOLD': (2.8, 0, 10, 'Whisper 엔트로피 임계값'),
        'LECORDER_SPEECH_LOGPROB_THRESHOLD': (-1.0, -10, 0, 'Whisper 디코딩 log probability 임계값'),
        'LECORDER_SPEECH_NO_SPEECH_THRESHOLD': (.6, 0, 1, '무음 판정 확률 임계값'),
        'LECORDER_SPEECH_MAX_LENGTH': (240, 1, 2000, 'Whisper 세그먼트 텍스트 최대 길이'),
        'LECORDER_SPEECH_MAX_CONTEXT': (224, 0, 224, 'Whisper 디코더 문맥 토큰 상한'),
        'LECORDER_SPEECH_BEAM_SIZE': (-1, -1, 32, '빔 탐색 크기, -1은 서버 기본값'),
        'LECORDER_SPEECH_BEST_OF': (2, 1, 32, 'Whisper 디코딩 후보 수'),
        'LECORDER_SPEECH_VAD_OVERLAP': (.2, 0, 2, 'VAD 구간 겹침'),
        'LECORDER_SPEECH_RETRY_BEAM_SIZE': (5, 1, 32, '저신뢰 재변환 빔 탐색 크기'),
        'LECORDER_SPEECH_VAD_THRESHOLD': (.5, 0, 1, '최종 음성 VAD 임계값'),
        'LECORDER_SPEECH_VAD_MIN_SPEECH_MS': (250, 1, 5000, '최소 발화 길이'),
        'LECORDER_SPEECH_VAD_MIN_SILENCE_MS': (300, 1, 5000, '최소 무음 길이'),
        'LECORDER_SPEECH_VAD_PADDING_MS': (150, 0, 2000, 'VAD 여유 길이'),
    },
    '문서·OCR·업로드': {
        'LECORDER_OCR_ENGINE': ('vision', None, None, 'vision / tesseract'),
        'LECORDER_NOTE_MAX_FILES': (500, 1, 5000, '노트 업로드 파일 수'),
        'LECORDER_NOTE_MAX_PAGES': (500, 1, 5000, '노트 최대 페이지 수'),
        'LECORDER_NOTE_MAX_MB': (100, 1, 4096, '노트 원본 합계 MB'),
        'LECORDER_RECORDING_CHUNK_MAX_MB': (32, 1, 512, '녹음 조각 한 개의 최대 MB'),
    },
    'LLM 검수와 백업': {
        'LECORDER_REVIEW_BATCH_CHARS': (1800, 200, 16000, '검수 묶음 문자 수'),
        'LECORDER_REVIEW_CONTEXT_CHARS': (6000, 500, 32000, '검수에 전달할 노트 문맥'),
        'LECORDER_REVIEW_EDIT_TOKENS': (768, 128, 16384, '단어 수정 검수 출력 예산'),
        'LECORDER_REVIEW_COMBINED_TOKENS': (1024, 128, 16384, '통합 검수 출력 예산'),
        'LECORDER_REVIEW_MIN_CONFIDENCE': (.8, 0, 1, '수정 제안 최소 신뢰도'),
        'LECORDER_BACKUP_QUIESCE_SECONDS': (60.0, 1, 600, '백업 전 AI 요청 정리 제한 시간'),
    },
    '노트 해설·개요 생성': {
        'LECORDER_NOTE_PAGE_TOKENS': (2048, 256, 32768, '첫 페이지 해설 생성 예산'),
        'LECORDER_NOTE_DETAIL_TOKENS': (3072, 256, 32768, '상세 해설 생성 예산'),
        'LECORDER_NOTE_RETRY_TOKENS': (4096, 256, 32768, '재생성 예산'),
        'LECORDER_NOTE_VALIDATION_TOKENS': (4096, 256, 32768, '페이지 의미 검증 예산'),
        'LECORDER_NOTE_TEMPERATURE': (.2, 0, 2, '노트 생성 온도'),
        'LECORDER_OVERVIEW_GROUP_ITEMS': (4, 2, 32, '개요 묶음 최대 항목 수'),
        'LECORDER_OVERVIEW_GROUP_CHARS': (12000, 4000, 64000, '개요 묶음 최대 문자 수'),
        'LECORDER_OVERVIEW_TOKENS': (1536, 256, 16384, '개요 생성 예산'),
        'LECORDER_OVERVIEW_VALIDATION_TOKENS': (1024, 256, 16384, '개요 의미 검증 예산'),
    },
    '용어집 선별·생성·검증': {
        'LECORDER_GLOSSARY_BATCH_CHARS': (6000, 1000, 24000, '후보 선별 묶음 문자 수'),
        'LECORDER_GLOSSARY_SELECTION_TOKENS': (1024, 256, 8192, '후보 선별 출력 예산'),
        'LECORDER_GLOSSARY_TOKENS': (8192, 512, 32768, '최종 용어집 생성 출력 예산'),
        'LECORDER_GLOSSARY_VALIDATION_TOKENS': (3072, 256, 16384, '용어집 의미 검증 출력 예산'),
    },
}
CATALOG = {name: spec for group in GROUPS.values() for name, spec in group.items()}


def parse(name, value):
    default, minimum, maximum, _ = CATALOG[name]
    if isinstance(default, str):
        return str(value).strip()
    try:
        if isinstance(default, bool):
            if str(value).lower() not in {'true','false','1','0','yes','no','on','off'}:
                raise ValueError()
            return str(value).lower() in {'true','1','yes','on'}
        parsed = int(value) if isinstance(default, int) else float(value)
        if not math.isfinite(parsed) or not minimum <= parsed <= maximum:
            raise ValueError()
        return parsed
    except (ValueError, TypeError, OverflowError):
        raise ValueError(f'{name}: 유효한 범위의 값을 입력하세요 ({minimum}~{maximum}).') from None


def get(name):
    return parse(name, os.environ.get(name, CATALOG[name][0]))


def validate():
    for name in CATALOG:
        get(name)
    for name in ('LECORDER_DIR','LECORDER_OUTPUT_DIR','LECORDER_DATA_DIR','LECORDER_DEPENDENCIES_DIR','LECORDER_SPEECH_MODEL','LECORDER_LIVE_MODEL','LECORDER_LLM_MODEL'):
        if not get(name):raise ValueError(f'{name}: 값을 비울 수 없습니다.')
    import re
    if not re.fullmatch(r'3\.(?:1[1-9]|[2-9][0-9])',get('LECORDER_PYTHON_VERSION')):
        raise ValueError('LECORDER_PYTHON_VERSION에는 3.13처럼 3.11 이상의 minor 버전을 지정하세요.')
    for name, allowed in {'LECORDER_SPEECH_PROVIDER':{'whisper_cpp','openai'},'LECORDER_LLM_PROVIDER':{'llama_cpp','openai','claude','gemini'},'LECORDER_OCR_ENGINE':{'vision','tesseract'}}.items():
        if get(name) not in allowed:raise ValueError(f'{name}: 지원하는 값은 '+', '.join(sorted(allowed)))
    if get('LECORDER_LIVE_OVERLAP_SECONDS') >= get('LECORDER_LIVE_MAX_SECONDS'):
        raise ValueError('실시간 겹침은 최대 발화 길이보다 작아야 합니다.')
    if get('LECORDER_LIVE_MIN_SECONDS') > get('LECORDER_LIVE_MAX_SECONDS'):
        raise ValueError('실시간 최소 발화 길이는 최대 길이 이하여야 합니다.')
    if get('LECORDER_LIVE_BUFFER_SECONDS') < get('LECORDER_LIVE_MAX_SECONDS'):
        raise ValueError('실시간 버퍼는 최대 발화 길이 이상이어야 합니다.')
    from urllib.parse import urlsplit
    for name in ('LECORDER_SPEECH_BASE_URL','LECORDER_LLM_BASE_URL','LECORDER_LIVE_BASE_URL'):
        url=urlsplit(get(name).rstrip('/'))
        try:port=url.port
        except ValueError:raise ValueError(f'{name}: 포트 번호를 확인하세요.') from None
        if url.scheme not in {'http','https'} or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError(f'{name}: 인증정보 없는 HTTP(S) 서버 URL을 지정하세요.')
        if url.scheme=='http' and url.hostname not in {'127.0.0.1','localhost','::1'}:
            raise ValueError(f'{name}: 외부 서버에는 HTTPS를 사용하세요.')
        managed = name=='LECORDER_LIVE_BASE_URL' or (name=='LECORDER_SPEECH_BASE_URL' and get('LECORDER_SPEECH_MANAGED') and get('LECORDER_SPEECH_PROVIDER')=='whisper_cpp') or (name=='LECORDER_LLM_BASE_URL' and get('LECORDER_LLM_MANAGED') and get('LECORDER_LLM_PROVIDER')=='llama_cpp')
        if managed and (url.scheme!='http' or url.hostname!='127.0.0.1' or not port or url.path):
            raise ValueError(f'{name}: 자동 관리 서버에는 http://127.0.0.1:포트 형식이 필요합니다.')
    if get('LECORDER_LLM_PROVIDER')=='llama_cpp' and get('LECORDER_LLM_MANAGED'):
        for name in ('LECORDER_GLOSSARY_TOKENS','LECORDER_NOTE_PAGE_TOKENS','LECORDER_NOTE_DETAIL_TOKENS','LECORDER_NOTE_RETRY_TOKENS','LECORDER_NOTE_VALIDATION_TOKENS','LECORDER_OVERVIEW_TOKENS','LECORDER_OVERVIEW_VALIDATION_TOKENS','LECORDER_REVIEW_EDIT_TOKENS','LECORDER_REVIEW_COMBINED_TOKENS'):
            if get(name)>=get('LECORDER_LLM_CONTEXT_TOKENS'):
                raise ValueError(f'{name}: 출력 예산은 LECORDER_LLM_CONTEXT_TOKENS보다 작아야 합니다.')
    if get('LECORDER_LLM_UBATCH_SIZE') > get('LECORDER_LLM_BATCH_SIZE'):
        raise ValueError('LLM 마이크로 배치는 배치 크기 이하여야 합니다.')


def template(overrides=None):
    """Render all supported settings, grouped identically in real/example files."""
    import shlex
    values = overrides or {}
    lines = [
        '# Lecorder 환경설정 — 상대 경로는 프로젝트 폴더 기준입니다.',
        '# 변경 후 서버를 재시작하세요. 화면에서 저장한 AI·경로 설정도 여기에 반영됩니다.',
        '# 빈 파일 경로는 기본 설치 경로를 자동으로 사용합니다.',
        '# API 키를 비우면 보안 저장소를 사용합니다. 실제 키는 .env.example에 넣지 마세요.',
    ]
    for title, fields in GROUPS.items():
        lines.extend(['', '# ── '+title+' ──'])
        for name, (default, minimum, maximum, description) in fields.items():
            value = values.get(name, default)
            if isinstance(value, bool):value = str(value).lower()
            lines.extend(['# '+description, name+'='+shlex.quote(str(value))])
    extras={key:value for key,value in values.items() if key not in CATALOG}
    if extras:
        lines.extend(['', '# ── 기존 추가 설정 ──', *(key+'='+shlex.quote(str(value)) for key,value in extras.items())])
    return '\n'.join(lines)+'\n'
