"""Settings API: capability probes are bound to the exact submitted configuration."""
import hashlib
import json
import threading
import time
from urllib.parse import urlsplit
from flask import Blueprint, jsonify, request
from .ai_providers import check_connection
from .ai_settings import defaults


def ai_routes(store):
    api = Blueprint('ai_settings', __name__)
    verified = {}
    lock = threading.RLock()

    @api.before_request
    def same_origin():
        if request.method in {'POST', 'PUT', 'DELETE'}:
            origin = request.headers.get('Origin')
            if origin and urlsplit(origin).netloc != request.host:
                return jsonify(error='다른 사이트에서 설정을 변경할 수 없습니다.'), 403
            if not request.is_json:
                return jsonify(error='JSON 요청이 필요합니다.'), 415

    def config_and_key(area):
        data = request.get_json() or {}
        config = store.clean(area, data.get('config', {}))
        key = data.get('api_key') or store.credentials.get(config)
        if not isinstance(key, str) or len(key) > 4096:
            raise ValueError('API 키 형식 오류')
        fingerprint = hashlib.sha256((json.dumps(config, sort_keys=True) + key).encode()).hexdigest()
        return config, key, fingerprint

    @api.get('/api/ai-settings')
    def get():
        return jsonify(store.public())

    @api.post('/api/ai-settings/<area>/check')
    def check(area):
        try:
            config, key, fingerprint = config_and_key(area)
            result = check_connection(area, config, key)
            with lock:
                now = time.monotonic()
                for expired in [k for k, checked in verified.items() if now - checked >= 600]:
                    del verified[expired]
                verified[fingerprint] = now
            return jsonify(ok=True, result=result)
        except Exception as error:
            # Provider errors are already sanitized; 보안 저장소/backend failures are not.
            from .ai_transport import ProviderError
            message = str(error) if isinstance(error, (ValueError, ProviderError)) else '연결 확인 실패. 서버·모델·보안 저장소 접근을 확인하세요.'
            return jsonify(error=message), 400

    @api.put('/api/ai-settings/<area>')
    def save(area):
        try:
            config, key, fingerprint = config_and_key(area)
            with lock:
                checked = time.monotonic() - verified.get(fingerprint, -1000) < 600
            if not checked:
                raise ValueError('변경한 설정으로 연결 확인을 먼저 실행하세요.')
            return jsonify(ok=True, **store.save(area, config, (request.get_json() or {}).get('api_key')))
        except ValueError as error:
            return jsonify(error=str(error)), 400
        except Exception:
            return jsonify(error='설정 또는 API 키 저장 실패. 보안 저장소 접근을 확인하세요.'), 400

    @api.delete('/api/ai-settings/<area>/key')
    def delete_key(area):
        try:
            config = store.clean(area, (request.get_json() or {}).get('config', {}))
            store.credentials.delete(config)
            return jsonify(ok=True)
        except Exception:
            return jsonify(error='API 키 삭제 실패. 보안 저장소 접근을 확인하세요.'), 400

    return api
