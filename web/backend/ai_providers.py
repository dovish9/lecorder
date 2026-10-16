"""Provider adapters shared by transcript review and image-grounded note analysis."""
from __future__ import annotations
import base64
import json
import time
from pathlib import Path
import requests
from jsonschema import Draft202012Validator
from .ai_settings import current, settings, configured_settings
from .ai_runtime import runtime_for
from .ai_transport import request_json, ProviderError
from .inference import inference
from .execution_metrics import current_trace
from .transcription import TranscriptionCancelled


def require_llm(config=None):
    """Validate availability without loading a model merely to show status."""
    config = config or current('llm')
    if config.get('managed'):
        for key in ('binary', 'model_path', 'projector_path'):
            if not Path(config.get(key, '')).is_file():
                raise ProviderError('로컬 LLM 파일이 없습니다. 설정에서 llama.cpp 설치를 확인하세요.')
    elif config['provider'] != 'llama_cpp' and not configured_settings().credentials.get(config):
        raise ProviderError('LLM API 키가 없습니다. 설정에서 등록하세요.')
    elif config['provider'] == 'llama_cpp':
        try:
            key = configured_settings().credentials.get(config)
            response = requests.get(config['base_url'].removesuffix('/v1') + '/health', headers={'Authorization':'Bearer '+key} if key else {}, timeout=(1, 2))
            if not response.ok:
                raise ProviderError('LLM 서버를 사용할 수 없습니다.')
        except requests.RequestException:
            raise ProviderError('LLM 서버가 꺼져 있거나 연결할 수 없습니다.') from None


def provider_status(area):
    config = current(area)
    if config.get('managed'):
        fields = ['binary', 'model_path', 'vad_path' if area == 'speech' else 'projector_path']
        installed = all(Path(config.get(k, '')).is_file() for k in fields)
        return {'provider': config['provider'], 'model': config['model'], 'ready': installed,
                'state': runtime_for(config).state if installed else 'missing'}
    try:
        ready = bool(configured_settings().credentials.get(config)) or config['provider'] in {'whisper_cpp', 'llama_cpp'}
    except Exception:
        ready = False
    return {'provider': config['provider'], 'model': config['model'], 'ready': ready, 'state': 'external' if ready else 'missing'}


def api_url(base, path):
    return base.rstrip('/') + path if base.rstrip('/').endswith('/v1') else base.rstrip('/') + '/v1' + path


def generate(messages, schema, **kwargs):
    config = kwargs.get('config') or current('llm')
    trace = current_trace()
    waiting = trace['waiting_seconds']
    started = time.monotonic()
    event = {'provider': config['provider'], 'model': config['model'], 'status': 'failed'}
    try:
        from .ai_control import run
        parent = kwargs.get('cancellation')
        from .request_history import request as tracked
        identity = [messages, schema, {k: v for k, v in kwargs.items() if k not in {'cancellation', 'api_key', 'config'}}]
        result = run(lambda token: tracked(
            lambda child: _generate(messages, schema, **{**kwargs, 'cancellation': child}),
            config, 'llm', identity, token), parent, kind='llm')
        result['waiting_seconds'] = max(0, trace['waiting_seconds'] - waiting)
        result['seconds'] = max(0, time.monotonic() - started - result['waiting_seconds'])
        event.update(status='completed', input_tokens=result['input_tokens'], output_tokens=result['output_tokens'],reused=bool(result.get('reused')))
        return result
    except TranscriptionCancelled:
        event['status'] = 'cancelled'
        raise
    finally:
        event['waiting_seconds'] = max(0, trace['waiting_seconds'] - waiting)
        event['seconds'] = max(0, time.monotonic() - started - event['waiting_seconds'])
        trace['provider_calls'].append(event)


def _generate(messages, schema, *, max_tokens=2048, temperature=0, cancellation=None, config=None, api_key=None):
    config = config or current('llm')
    provider = config['provider']
    key = configured_settings().credentials.get(config) if api_key is None else api_key
    if provider != 'llama_cpp' and not key:
        raise ProviderError('API 키를 입력하세요.')
    headers = {}
    content_messages = []
    for item in messages:
        content = [{'type': 'text', 'text': item['content']}]
        for data in item.get('images', []):
            content.append({'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + data}})
        content_messages.append({'role': item['role'], 'content': content})
    if provider in {'llama_cpp', 'openai'}:
        url = api_url(config['base_url'], '/chat/completions')
        if key:
            headers['Authorization'] = 'Bearer ' + key
        payload = {'model': config['model'], 'messages': content_messages,
                   'response_format': {'type': 'json_schema', 'json_schema': {'name': 'lecorder', 'strict': True, 'schema': schema}}}
        if provider == 'llama_cpp':
            payload.update(max_tokens=max_tokens, temperature=temperature,
                           chat_template_kwargs={'enable_thinking': False}, stream=False)
            if any(item.get('images') for item in messages):
                # Recurrent multimodal KV positions cannot safely reuse a text prefix.
                payload['cache_prompt'] = False
        else:
            payload['max_completion_tokens'] = max_tokens
    elif provider == 'claude':
        url = api_url(config['base_url'], '/messages')
        headers = {'x-api-key': key, 'anthropic-version': '2023-06-01'}
        payload = {'model': config['model'], 'max_tokens': max_tokens, 'temperature': temperature,
                   'system': '\n'.join(m['content'] for m in messages if m['role'] == 'system'),
                   'messages': [], 'output_config': {'format': {'type': 'json_schema', 'schema': schema}}}
        for item in messages:
            if item['role'] == 'system':
                continue
            content = [{'type': 'text', 'text': item['content']}]
            content += [{'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': data}} for data in item.get('images', [])]
            payload['messages'].append({'role': item['role'], 'content': content})
    elif provider == 'gemini':
        from urllib.parse import quote
        base = config['base_url'].rstrip('/')
        if not base.endswith('/v1beta'):
            base += '/v1beta'
        url = base + '/models/' + quote(config['model'], safe='') + ':generateContent'
        headers = {'x-goog-api-key': key}
        payload = {'systemInstruction': {'parts': [{'text': '\n'.join(m['content'] for m in messages if m['role'] == 'system')}]},
                   'contents': [], 'generationConfig': {'temperature': temperature, 'maxOutputTokens': max_tokens,
                   'responseMimeType': 'application/json', 'responseJsonSchema': schema}}
        for item in messages:
            if item['role'] == 'system':
                continue
            parts = [{'text': item['content']}]
            parts += [{'inlineData': {'mimeType': 'image/png', 'data': data}} for data in item.get('images', [])]
            payload['contents'].append({'role': 'model' if item['role'] == 'assistant' else 'user', 'parts': parts})
    else:
        raise ProviderError('지원하지 않는 LLM 제공자입니다.')
    queued = time.monotonic()
    with inference.lease(cancellation=cancellation, local=config.get("managed", False)):
        waiting = time.monotonic() - queued
        start = time.monotonic()
        if provider == 'llama_cpp':
            runtime = runtime_for(config)
            try:
                with runtime.session(cancellation):
                    body = request_json(url, payload, headers=headers, cancellation=cancellation)
            except ProviderError:
                runtime.release_idle()
                raise
        else:
            body = request_json(url, payload, headers=headers, cancellation=cancellation)
        elapsed = time.monotonic() - start
    if cancellation:
        cancellation.check()
    try:
        if provider in {'llama_cpp', 'openai'}:
            choice = body['choices'][0]
            if choice.get('finish_reason') == 'length':
                raise ValueError('LLM 출력 길이 제한 도달')
            raw = choice['message'].get('content') or ''
            usage = body.get('usage', {})
            input_tokens, output_tokens = usage.get('prompt_tokens'), usage.get('completion_tokens')
        elif provider == 'claude':
            if body.get('stop_reason') == 'max_tokens':
                raise ValueError('LLM 출력 길이 제한 도달')
            raw = ''.join(part.get('text', '') for part in body['content'] if part['type'] == 'text')
            usage = body.get('usage', {})
            input_tokens, output_tokens = usage.get('input_tokens'), usage.get('output_tokens')
        else:
            candidate = body['candidates'][0]
            if candidate.get('finishReason') == 'MAX_TOKENS':
                raise ValueError('LLM 출력 길이 제한 도달')
            raw = ''.join(p.get('text', '') for p in candidate['content']['parts'] if not p.get('thought'))
            usage = body.get('usageMetadata', {})
            input_tokens, output_tokens = usage.get('promptTokenCount'), usage.get('candidatesTokenCount')
        result = json.loads(raw)
        errors = list(Draft202012Validator(schema).iter_errors(result))
        if errors:
            # ValidationError.message can contain source text. Keep diagnostics structural.
            raise ValueError('LLM JSON 스키마 불일치: ' + str(list(errors[0].absolute_path)))
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        raise ValueError('LLM JSON 응답이 없거나 잘렸습니다.') from None
    return {'result': result, 'model': config['model'], 'provider': provider, 'seconds': elapsed,
            'waiting_seconds': waiting, 'input_tokens': input_tokens, 'output_tokens': output_tokens}


def check_connection(area, config, key=None):
    if area == 'llm':
        # A tiny red PNG generated from fixed bytes; no user document is uploaded.
        import struct, zlib
        def chunk(kind, data):
            return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data) & 0xffffffff)
        png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', 32, 32, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress((b'\0' + b'\xff\0\0' * 32) * 32)) + chunk(b'IEND', b'')
        schema = {'type': 'object', 'properties': {'color': {'type': 'string'}}, 'required': ['color'], 'additionalProperties': False}
        response = generate([{'role': 'user', 'content': 'What is the solid color of the image? Reply in English.', 'images': [base64.b64encode(png).decode()]}], schema, max_tokens=128, config=config, api_key=key)
        if 'red' not in response['result']['color'].lower():
            raise ValueError('이미지 입력 확인에 실패했습니다. 이미지 지원 모델을 선택하세요.')
        return {k: response[k] for k in ('model', 'provider', 'seconds')}
    if config['provider'] == 'whisper_cpp':
        key = configured_settings().credentials.get(config) if key is None else key
        from .ai_control import run
        def probe(token):
            with inference.lease(speech=True, local=config.get('managed', False), cancellation=token), runtime_for(config).session(token):
                result = request_json(config['base_url'] + '/health', method='GET', cancellation=token,
                                      headers={'Authorization': 'Bearer ' + key} if key else None)
                if result.get('status') != 'ok':
                    raise ProviderError('전사 서버 상태 확인 실패')
        run(probe, kind='speech_check')
        return {'model': config['model'], 'provider': config['provider']}
    key = configured_settings().credentials.get(config) if key is None else key
    if not key:
        raise ProviderError('API 키를 입력하세요.')
    # Auth/model availability check only; real transcription is verified by tests.
    request_json(api_url(config['base_url'], '/models/whisper-1'), headers={'Authorization': 'Bearer ' + key}, method='GET')
    return {'model': config['model'], 'provider': config['provider']}
