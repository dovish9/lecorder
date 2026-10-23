"""Versioned AI settings. Credentials live in the host secure credential store."""
from __future__ import annotations
import copy
import hashlib
import json
import os
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from urllib.parse import urlsplit
from web.backend.config import ROOT, DATA_DIR, DEPENDENCIES_DIR, ENV_FILE, _read_env, write_env, resolve_path
from web.backend.runtime_config import get as runtime, parse as parse_env
from web.backend.platforms import host
from web.backend.atomic import write_text

SETTING_FILE = resolve_path(runtime('LECORDER_AI_SETTINGS'), ROOT) if runtime('LECORDER_AI_SETTINGS') else DATA_DIR / 'ai-settings.json'
ENV_FIELDS = {
    'speech': {field: 'LECORDER_SPEECH_'+field.upper() for field in ('provider','managed','base_url','model','binary','model_path','vad_path')},
    'llm': {field: 'LECORDER_LLM_'+field.upper() for field in ('provider','managed','base_url','model','binary','model_path','projector_path','idle_seconds')},
}
ENV_FIELDS['speech'].update(idle_seconds='WHISPER_IDLE_SECONDS', **{field:'LECORDER_'+field.upper() for field in ('live_base_url','live_model','live_model_path','live_vad_path')})

def env_config(area, config, values):
    result = dict(config)
    for field,name in ENV_FIELDS[area].items():
        if name not in values:continue
        if values[name] == '':
            if field in {'binary','model_path','vad_path','projector_path','live_model_path','live_vad_path'}:
                result[field]=defaults(environment=False)[area][field]
            continue
        result[field] = parse_env(name, values[name])
        if field in {'base_url','live_base_url'}:result[field]=result[field].rstrip('/')
        if field in {'binary','model_path','vad_path','projector_path','live_model_path','live_vad_path'}:
            result[field] = str(resolve_path(result[field], ROOT))
    if result['provider'] not in ({'whisper_cpp','openai'} if area=='speech' else {'llama_cpp','openai','claude','gemini'}):
        raise ValueError(f'{area}: 지원하지 않는 제공자입니다.')
    result['managed'] = result.get('managed',False) and result['provider'] in {'whisper_cpp','llama_cpp'}
    return result
_context = ContextVar('ai_settings', default=None)


def defaults(*, environment=True):
    whisper = DEPENDENCIES_DIR / 'whisper.cpp'
    llama = DEPENDENCIES_DIR / 'llama.cpp'
    builtin = {
        'speech': {'provider': 'whisper_cpp', 'managed': True, 'base_url': 'http://127.0.0.1:8080',
                   'model': 'large-v3', 'model_path': str(whisper / 'models/ggml-large-v3.bin'),
                   'binary': str(host.executable(whisper / 'build/bin', 'whisper-server')),
                   'vad_path': str(whisper / 'models/ggml-silero-v6.2.0.bin'), 'idle_seconds': 60,
                   'live_base_url': 'http://127.0.0.1:8082',
                   'live_model': 'large-v3-turbo',
                   'live_model_path': str(whisper / 'models/ggml-large-v3-turbo.bin'),
                   'live_vad_path': str(whisper / 'models/ggml-silero-v6.2.0.bin')},
        'llm': {'provider': 'llama_cpp', 'managed': True, 'base_url': 'http://127.0.0.1:8081',
                'model': 'Qwen3.5-9B', 'model_path': str(llama / 'models/Qwen3.5-9B-Q4_K_M.gguf'),
                'projector_path': str(llama / 'models/mmproj-F16.gguf'),
                'binary': str(host.executable(llama / 'build/bin', 'llama-server')), 'idle_seconds': 60},
    }

    return {area:env_config(area,config,os.environ) for area,config in builtin.items()} if environment else builtin

def live_configuration(speech):
    """Freeze local live settings independently of the final transcription provider."""
    config = defaults()['speech']
    config['binary'] = speech.get('binary', config['binary'])
    config['base_url'] = speech.get('live_base_url', config['live_base_url'])
    config.update(model=speech.get('live_model', config['live_model']),
                  model_path=speech.get('live_model_path', config['live_model_path']),
                  vad_path=speech.get('live_vad_path', config['live_vad_path']))
    return config


class Credentials:
    service = 'Lecorder AI'

    @staticmethod
    def reference(config):
        return hashlib.sha256((config['provider'] + ':' + config['base_url']).encode()).hexdigest()

    def __init__(self, platform=None):
        self.platform = platform or host
        self.env_path = None

    @staticmethod
    def key_env(config):
        speech = config['provider']=='whisper_cpp' or (config['provider']=='openai' and config.get('model')=='whisper-1')
        return 'LECORDER_SPEECH_API_KEY' if speech else 'LECORDER_LLM_API_KEY'

    def backend(self):
        return self.platform.credentials()

    def get(self, config):
        if config['provider'] in {'whisper_cpp', 'llama_cpp'} and config.get('managed'):
            return ''
        name = self.key_env(config)
        if name in {'LECORDER_SPEECH_API_KEY','LECORDER_LLM_API_KEY'}:
            values = {**os.environ, **(_read_env(self.env_path) if self.env_path else {})}
            if values.get(name):return values[name]
        try:
            return self.backend().get_password(self.service, self.reference(config)) or ''
        except Exception:
            raise RuntimeError('보안 저장소에서 API 키를 읽을 수 없습니다.') from None

    def _clear_env_key(self, config):
        name = self.key_env(config)
        if name in {'LECORDER_SPEECH_API_KEY','LECORDER_LLM_API_KEY'}:
            if self.env_path:write_env(self.env_path, {name:''})
            os.environ.pop(name, None)

    def set(self, config, value):
        self.backend().set_password(self.service, self.reference(config), value)
        self._clear_env_key(config)

    def delete(self, config):
        if self.backend().get_password(self.service, self.reference(config)):
            self.backend().delete_password(self.service, self.reference(config))
        self._clear_env_key(config)


class AISettings:
    def __init__(self, path=SETTING_FILE, credentials=None, *, env_path=None):
        self.path = Path(path)
        self.env_path = Path(env_path) if env_path else (ENV_FILE if self.path.resolve()==SETTING_FILE.resolve() else None)
        self.credentials = credentials or Credentials()
        if isinstance(self.credentials, Credentials):self.credentials.env_path = self.env_path
        self.lock = threading.RLock()
        self._active = None

    def freeze(self):
        self._active = self.saved_snapshot()

    def snapshot(self):
        return copy.deepcopy(self._active) if self._active is not None else self.saved_snapshot()

    def saved_snapshot(self):
        with self.lock:
            settings = defaults()
            if self.path.exists():
                saved = json.loads(self.path.read_text(encoding='utf-8'))['settings']
                for area in settings:
                    settings[area].update(saved.get(area, {}))
            if self.env_path:
                values = {**os.environ, **_read_env(self.env_path)}
                settings = {area:env_config(area,config,values) for area,config in settings.items()}
            return settings

    def public(self):
        settings = self.saved_snapshot()
        for config in settings.values():
            try:
                config['key_present'] = bool(self.credentials.get(config))
            except Exception:
                config['key_present'] = False
        return {'settings': settings, 'defaults': defaults(environment=False), 'restart_required': self._active is not None and self.saved_snapshot() != self._active}

    def clean(self, area, supplied):
        if area not in {'speech', 'llm'} or not isinstance(supplied, dict):
            raise ValueError('올바른 AI 설정 영역이 필요합니다.')
        allowed = {'provider', 'managed', 'base_url', 'model', 'model_path', 'binary', 'vad_path', 'projector_path', 'idle_seconds'}
        if area == 'speech':
            allowed.update({'live_base_url', 'live_model', 'live_model_path', 'live_vad_path'})
        config = {k: v for k, v in supplied.items() if k in allowed}
        providers = {'whisper_cpp', 'openai'} if area == 'speech' else {'llama_cpp', 'openai', 'claude', 'gemini'}
        if config.get('provider') not in providers:
            raise ValueError('지원하지 않는 제공자입니다.')
        config['managed'] = config.get('managed') is True and config['provider'] in {'whisper_cpp', 'llama_cpp'}
        url = str(config.get('base_url', '')).strip().rstrip('/')
        parsed = urlsplit(url)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('인증정보·쿼리를 포함하지 않는 서버 URL을 입력하세요.')
        if parsed.scheme != 'https' and parsed.hostname not in {'localhost', '127.0.0.1', '::1'}:
            raise ValueError('외부 서버 연결에는 HTTPS가 필요합니다.')
        if config['managed'] and (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port or parsed.path):
            raise ValueError('자동 관리 서버는 127.0.0.1의 기본 주소를 사용해야 합니다.')
        config['base_url'] = url
        config['credential_ref'] = Credentials.reference(config)
        config['model'] = str(config.get('model', '')).strip()
        if not config['model'] or len(config['model']) > 200:
            raise ValueError('모델 ID를 입력하세요.')
        if area == 'speech' and config['provider'] == 'openai' and config['model'] != 'whisper-1':
            raise ValueError('현재 외부 음성 변환은 타임스탬프를 지원하는 whisper-1만 사용할 수 있습니다.')
        config['idle_seconds'] = max(0, min(3600, int(config.get('idle_seconds', 60))))
        if config['managed']:
            for field in ('binary', 'model_path', 'vad_path' if area == 'speech' else 'projector_path'):
                path = Path(str(config.get(field, ''))).expanduser()
                if not path.is_file():
                    raise ValueError(f'{field}: 파일을 찾을 수 없습니다.')
                config[field] = str(path.absolute())
        if area == 'speech':
            live_url = str(config.get('live_base_url', defaults()['speech']['live_base_url'])).strip().rstrip('/')
            live = urlsplit(live_url)
            if live.scheme != 'http' or live.hostname != '127.0.0.1' or not live.port or live.path or live.query or live.fragment or live.username or live.password:
                raise ValueError('실시간 서버 URL은 http://127.0.0.1:포트 형식이어야 합니다.')
            if parsed.hostname in {'localhost', '127.0.0.1', '::1'} and parsed.port == live.port:
                raise ValueError('실시간 음성 변환과 최종 음성 변환은 서로 다른 포트를 사용해야 합니다.')
            config['live_base_url'] = live_url
            binary = Path(str(config.get('binary', defaults()['speech']['binary']))).expanduser()
            if binary != Path(defaults()['speech']['binary']) and not binary.is_file():
                raise ValueError('binary: 파일을 찾을 수 없습니다.')
            config['binary'] = str(binary.absolute())
            for field in ('live_model', 'live_model_path', 'live_vad_path'):
                value = str(config.get(field, defaults()['speech'][field])).strip()
                if not value or len(value) > (200 if field == 'live_model' else 4096):
                    raise ValueError(f'{field}: 값을 확인하세요.')
                if field != 'live_model':
                    path = Path(value).expanduser()
                    # Missing default turbo must not prevent configuring final/external transcription.
                    if path.resolve() != Path(defaults()['speech'][field]).resolve() and not path.is_file():
                        raise ValueError(f'{field}: 파일을 찾을 수 없습니다.')
                    value = str(path.absolute())
                config[field] = value
        return config

    def save(self, area, config, key=None):
        config = self.clean(area, config)
        with self.lock:
            settings = self.saved_snapshot()
            speech = config if area == 'speech' else settings['speech']
            llm = config if area == 'llm' else settings['llm']
            live_port = urlsplit(speech.get('live_base_url', defaults()['speech']['live_base_url'])).port
            llm_url = urlsplit(llm.get('base_url', ''))
            if llm_url.hostname in {'localhost', '127.0.0.1', '::1'} and llm_url.port == live_port:
                raise ValueError('실시간 음성 변환과 LLM 서버는 서로 다른 포트를 사용해야 합니다.')
            if key:
                self.credentials.set(config, key)
            settings[area] = config
            previous = self.path.read_text(encoding='utf-8') if self.path.exists() else None
            write_text(self.path, json.dumps({'version': 1, 'settings': settings}, ensure_ascii=False, indent=2))
            try:
                if self.env_path:
                    values = {name:str(config[field]).lower() if isinstance(config[field],bool) else str(config[field])
                              for field,name in ENV_FIELDS[area].items() if field in config}
                    write_env(self.env_path, values)
            except Exception:
                if previous is None:self.path.unlink(missing_ok=True)
                else:write_text(self.path, previous)
                raise
        return self.public()


settings = AISettings()


def current(area):
    return copy.deepcopy((_context.get() or configured_settings().snapshot())[area])


@contextmanager
def using(snapshot):
    token = _context.set(snapshot or configured_settings().snapshot())
    try:
        yield
    finally:
        _context.reset(token)


def configured_settings():
    from web.backend.execution_context import execution_resources
    services = execution_resources()
    return services.ai if services else settings
