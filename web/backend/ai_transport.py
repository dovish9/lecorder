"""Provider HTTP policy; transport is a cancellable Python worker, never curl."""
import subprocess
import time
from .processes import worker, CancellationToken


class ProviderError(RuntimeError):
    def __init__(self, message, *, retryable=False):
        super().__init__(message)
        self.retryable = retryable


def request_json(url, payload=None, *, headers=None, cancellation=None, timeout=600, method='POST', multipart=None):
    from .request_history import tracking_request
    headers = dict(headers or {})
    if any('\n' in str(v) or '\r' in str(v) for pair in headers.items() for v in pair):
        raise ProviderError('잘못된 인증정보입니다.')
    token = cancellation or CancellationToken()
    spec = dict(url=url, payload=payload, headers=headers, timeout=timeout, method=method, multipart=multipart)
    for attempt in range(2):
        token.check()
        try:
            result = worker.call('http', spec, cancellation=token, timeout=timeout + 10)
        except (subprocess.SubprocessError, OSError, ValueError):
            raise ProviderError('AI 서버 연결이 끊겼거나 응답 시간이 초과되었습니다.', retryable=True) from None
        token.check()
        error = result.get('error')
        if error:
            messages = {'json': 'AI 서버가 올바른 JSON 응답을 반환하지 않았습니다.',
                        'timeout': 'AI 서버 응답 시간이 초과되었습니다.'}
            raise ProviderError(messages.get(error, 'AI 서버에 연결할 수 없습니다.'), retryable=error!='json')
        status = result.get('status', 0)
        if status in {429, 502, 503, 504} and attempt == 0:
            if tracking_request():
                raise ProviderError(f'일시적인 AI 서버 오류 (HTTP {status}).', retryable=True)
            until = time.monotonic() + 1
            while time.monotonic() < until:
                token.check()
                time.sleep(.05)
            continue
        if not 200 <= status < 300:
            label = {401: 'API 키 인증 실패', 403: 'API 접근 권한 없음', 404: '모델 또는 API 주소를 찾지 못함', 429: '요청 한도 초과'}.get(status, 'AI 요청 실패')
            raise ProviderError(f'{label} (HTTP {status}).')
        return result['body']
