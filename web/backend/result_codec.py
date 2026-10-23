"""Versioned storage documents. Domain callers receive their original data shape."""
import json
from web.backend.versions import FORMAT_VERSIONS, POLICIES

class ResultFormatError(ValueError):
    pass

COLUMNS = {
    'segments_json': ('segments', 'speech'), 'breaks_json': ('breaks', 'speech'),
    'quality_json': ('quality', 'speech'), 'note_snapshot_json': ('note-snapshot', 'speech'),
    'keywords': ('keywords', 'glossary'), 'overview': ('overview', 'overview'),
    'glossary_data': ('glossary', 'glossary'), 'metrics': ('metrics', 'speech'),
}

def envelope(value, kind, policy):
    return {'format': kind, 'format_version': FORMAT_VERSIONS[kind],
            'generator_version': POLICIES.get(policy, policy), 'data': value}

def dumps(value, kind, policy):
    return json.dumps(envelope(value, kind, policy), ensure_ascii=False, allow_nan=False)

def loads(value, expected=None):
    try:
        document = json.loads(value) if isinstance(value, (str, bytes)) else value
        if not isinstance(document, dict) or set(document) != {'format','format_version','generator_version','data'}:
            raise ValueError('저장 봉투 누락')
        kind = document['format']
        if kind not in FORMAT_VERSIONS or (expected and kind != expected):
            raise ValueError('지원하지 않는 결과 종류')
        if type(document['format_version']) is not int or document['format_version'] != FORMAT_VERSIONS[kind]:
            raise ValueError('지원하지 않는 결과 버전')
        if not isinstance(document['generator_version'], str) or not document['generator_version']:
            raise ValueError('생성 정책 누락')
        expected_type = list if kind in {'segments','breaks','keywords','overview'} else dict
        if not isinstance(document['data'], expected_type):
            raise ValueError('결과 데이터 자료형이 올바르지 않습니다.')
        return document['data']
    except (ValueError, TypeError, KeyError) as error:
        raise ResultFormatError(f'자료 형식을 확인해야 합니다: {error}') from error

def encode_column(name, value):
    kind, policy = COLUMNS[name]
    return dumps(json.loads(value) if isinstance(value, str) else value, kind, policy)
