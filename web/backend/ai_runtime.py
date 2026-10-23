"""Owned model processes; external endpoints are never started or killed."""
from web.backend.runtime_config import get as setting
import atexit
import hashlib
import json
import socket
import subprocess
import time
import threading
from pathlib import Path
from urllib.parse import urlsplit
import requests
from web.backend.config import DATA_DIR
from web.backend.managed_runtime import ManagedRuntime
from web.backend.platforms import host


class ModelRuntime(ManagedRuntime):
    def __init__(self, config, *, log_dir=None, peers=None, platform=None):
        self.config = dict(config)
        super().__init__(url=config['base_url'], idle_seconds=config.get('idle_seconds', 60), external=not config.get('managed', False))
        self.log_dir = Path(log_dir) if log_dir else DATA_DIR
        self.peers = peers or (lambda: [])
        self.platform = platform or host

    def release_idle(self):
        with self._lock:
            if self._users == 0:
                if self._timer:
                    self._timer.cancel()
                    self._timer = None
                self._stop()

    def _start(self, cancellation):
        if self.external:
            return
        if self._process is not None and self._process.poll() is None:
            return
        # Called under the shared inference lease. Never unload an in-use process.
        for runtime in self.peers():
            if runtime is not self:
                runtime.release_idle()
        config = self.config
        endpoint = urlsplit(self.url)
        port = endpoint.port
        if endpoint.hostname != '127.0.0.1' or not port:
            raise RuntimeError('자동 관리 서버에는 127.0.0.1과 명시적 포트가 필요합니다.')
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=.3):
                raise RuntimeError(f'포트 {port}를 다른 서버가 사용 중입니다. 해당 프로세스는 종료하지 않았습니다.')
        except OSError:
            pass
        speech = config['provider'] == 'whisper_cpp'
        fields = ('binary', 'model_path', 'vad_path' if speech else 'projector_path')
        for field in fields:
            if not Path(config.get(field, '')).is_file():
                raise RuntimeError(f'{field} 파일이 없습니다. 로컬 모델 설치를 확인하세요.')
        command = [config['binary'], '-m', config['model_path'], '--host', '127.0.0.1', '--port', str(port)]
        threads = setting('LECORDER_SPEECH_THREADS' if speech else 'LECORDER_LLM_THREADS')
        if threads:command += ['-t', str(threads)]
        if speech:
            command += ['-vm', config['vad_path']]
        else:
            command += ['--mmproj', config['projector_path'], '--alias', config['model'],
                        '-c', str(setting('LECORDER_LLM_CONTEXT_TOKENS')), '-ngl', str(setting('LECORDER_LLM_GPU_LAYERS')), '--parallel', '1', '--jinja', '-b', str(setting('LECORDER_LLM_BATCH_SIZE')), '-ub', str(setting('LECORDER_LLM_UBATCH_SIZE'))]
        log = self.log_dir / ('whisper-server.log' if speech else 'llama-server.log')
        log.parent.mkdir(parents=True, exist_ok=True)
        self._state = 'loading'
        try:
            with log.open('ab') as stream:
                binary_dir = Path(config['binary']).parent
                self._process = subprocess.Popen(
                    command, stdout=stream, stderr=stream,
                    env=self.platform.library_environment([binary_dir, binary_dir.parent / 'lib']),
                    **self.platform.process_options(),
                )
            deadline = time.monotonic() + setting('LECORDER_MODEL_LOAD_SECONDS')
            while time.monotonic() < deadline:
                if cancellation:
                    cancellation.check()
                if self._closed or self._process.poll() is not None:
                    raise RuntimeError(f'로컬 모델 로드 실패: {log.name}')
                try:
                    response = requests.get(self.url + '/health', timeout=(.3, .5))
                    if response.ok:
                        self._state = 'ready'
                        return
                except requests.RequestException:
                    pass
                time.sleep(.1)
            raise RuntimeError('로컬 모델 로드 제한 시간을 초과했습니다.')
        except BaseException:
            self._stop()
            raise


class RuntimeRegistry:
    def __init__(self, log_dir=None, platform=None):
        self.log_dir, self.platform = log_dir, platform
        self._runtimes = {}
        self._lock = threading.RLock()

    def runtimes(self):
        with self._lock:
            return list(self._runtimes.values())

    def get(self, config):
        key = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
        with self._lock:
            if key not in self._runtimes:
                self._runtimes[key] = ModelRuntime(config, log_dir=self.log_dir,
                    platform=self.platform, peers=self.runtimes)
            return self._runtimes[key]

    def close(self):
        with self._lock:
            runtimes = list(self._runtimes.values())
            self._runtimes.clear()
        for runtime in runtimes:
            runtime.close()


registry = RuntimeRegistry()


def runtime_for(config):
    from web.backend.execution_context import execution_resources
    services = execution_resources()
    return (services.runtimes if services else registry).get(config)


def close_all():
    registry.close()


atexit.register(close_all)
