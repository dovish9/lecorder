"""Settings API: capability probes are bound to the exact submitted configuration."""
from web.backend.runtime_config import get as runtime
import threading
import time
from uuid import UUID
from web.backend.processes import CancellationToken
from urllib.parse import urlsplit
from flask import Blueprint, jsonify, request
from web.backend.ai_providers import check_connection


def ai_routes(store):
    api = Blueprint('ai_settings', __name__)
    checks = {}
    checks_lock = threading.Lock()

    def check_token(area):
        identifier = (request.get_json() or {}).get('check_id')
        if identifier is None:
            return CancellationToken()
        identifier = str(UUID(identifier))
        with checks_lock:
            now = time.monotonic()
            for key, (_, expires) in list(checks.items()):
                if expires < now:
                    del checks[key]
            key = (area, identifier)
            if key not in checks:
                checks[key] = (CancellationToken(), now + 120)
            return checks[key][0]

    @api.post('/api/ai-settings/<area>/check/cancel')
    def cancel_check(area):
        try:
            token = check_token(area)
            token.cancel()
            return jsonify(ok=True)
        except (ValueError, TypeError, AttributeError):
            return jsonify(error='잘못된 검증 요청 ID'), 400


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
        return config, key

    @api.get('/api/ai-settings')
    def get():
        return jsonify(store.public())

    @api.post('/api/ai-settings/<area>/check')
    def check(area):
        try:
            config, key = config_and_key(area)
            from web.backend.processes import CancellationToken, TranscriptionCancelled
            token = check_token(area)
            timer = threading.Timer(runtime('LECORDER_SETTINGS_CHECK_SECONDS'), token.cancel)
            timer.daemon = True
            timer.start()
            try:
                token.check()
                result = check_connection(area, config, key, cancellation=token)
                token.check()
            except TranscriptionCancelled:
                raise ValueError('연결 검증이 취소되었거나 시간이 초과되었습니다.')
            finally:
                timer.cancel()
            return jsonify(ok=True, result=result)
        except Exception as error:
            # Provider errors are already sanitized; 보안 저장소/backend failures are not.
            from web.backend.ai_transport import ProviderError
            message = str(error) if isinstance(error, (ValueError, ProviderError)) else '연결 확인 실패. 서버·모델·보안 저장소 접근을 확인하세요.'
            return jsonify(error=message), 400

    @api.put('/api/ai-settings/<area>')
    def save(area):
        try:
            config, key = config_and_key(area)
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
