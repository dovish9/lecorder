"""Pinned model assets; no application imports, settings writes, or server startup."""
import hashlib
import json
import os
import subprocess
from pathlib import Path
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
MODULES = ('flask', 'requests', 'keyring', 'jsonschema', 'tiktoken', 'av',
           'pypdfium2', 'PIL', 'pillow_heif', 'py_mini_racer', 'pdfplumber')


def dependencies():
    return Path(os.environ.get('LECORDER_DEPENDENCIES_DIR', ROOT / 'dependencies'))


def assets(group=None):
    deps = dependencies()
    for name in ('whisper', 'llama', 'kiwi', 'tokenizer'):
        if group and name != group:
            continue
        manifest = json.loads((ROOT / 'scripts' / name / 'models.json').read_text())
        for item in manifest['files']:
            item = dict(item)
            if name == 'llama':
                item['path'] = 'models/' + item['name']
                item['url'] = f"https://huggingface.co/{manifest['repo']}/resolve/{manifest['revision']}/{item['name']}"
            base = deps / {'whisper':'whisper.cpp', 'llama':'llama.cpp', 'kiwi':'kiwi', 'tokenizer':'tokenizers'}[name]
            yield base / item['path'], item


def valid(path, item):
    if not path.is_file() or ('size' in item and path.stat().st_size != item['size']):
        return False
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest() == item['sha256']


def install(target, item):
    if valid(target, item):
        print('검증된 파일 사용:', target.name, flush=True)
        return
    if target.exists():
        with target.open('rb') as stream:
            pointer = stream.read(128).startswith(b'version https://git-lfs.github.com/spec/v1\n')
        if not pointer:
            raise ValueError(f'체크섬 불일치: 기존 파일을 보존했습니다. 별도로 보관한 뒤 재시도하세요: {target}')
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, suffix='.download', delete=False) as stream:
            temporary = Path(stream.name)
            print('다운로드:', target.name, flush=True)
            with urllib.request.urlopen(item['url'], timeout=60) as response:
                size = 0
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if 'size' in item and size > item['size']:
                        raise ValueError(f'예상 크기 초과: {target.name}')
                    stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        if not valid(temporary, item):
            raise ValueError(f'다운로드 체크섬 불일치: {target.name}')
        temporary.replace(target)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def download(group=None):
    for path, item in assets(group):
        install(path, item)


def probe_executable(path):
    try:
        result = subprocess.run([str(path), '--help'], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                timeout=20, text=True, errors='replace')
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f'실행 검사 시간 초과: {path}') from exc
    except OSError as exc:
        raise ValueError(f'실행 불가: {path}: {exc}') from exc
    if result.returncode != 0:
        raise ValueError(f'실행 검사 실패: {path} (종료 코드 {result.returncode})\n{result.stdout[-2000:]}')
    print('실행 검사 통과:', path.name, flush=True)


def plan():
    for path, item in assets():
        status = '기존 파일: 설치 시 체크섬 검증' if path.is_file() else '다운로드 예정'
        print(f'{status}: {path}')


def check(packages_only=False):
    import importlib
    for name in MODULES:
        importlib.import_module(name)
    if packages_only:
        return
    deps = dependencies()
    for relative in ('whisper.cpp/build/bin/whisper-server', 'whisper.cpp/build/bin/lecorder-vad', 'llama.cpp/build/bin/llama-server',
                     'kiwi/bin/kiwi-helper', 'vision/bin/vision-ocr'):
        path = deps / relative
        if not path.is_file() or not os.access(path, os.X_OK):
            raise ValueError(f'실행 파일 누락: {path}')
        probe_executable(path)
    for path, item in assets():
        if not valid(path, item):
            raise ValueError(f'모델 누락 또는 체크섬 불일치: {path}')
