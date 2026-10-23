"""Structured note findings; diagnostic detail never doubles as user copy."""
VERSION = 'note-review-v2'
FINDING_SCHEMA = {
    'type': 'object', 'properties': {
        'target': {'type': 'string', 'enum': ['explanation', 'source', 'symbols', 'validation']},
        'severity': {'type': 'string', 'enum': ['error', 'warning', 'info']},
        'detail': {'type': 'string', 'maxLength': 1000},
        'message': {'type': 'string', 'maxLength': 160},
    }, 'required': ['target', 'severity', 'detail', 'message'], 'additionalProperties': False,
}


def finding(detail, message, target='explanation', severity='error'):
    return dict(target=target, severity=severity, detail=detail, message=message)


def validate_finding(item):
    if not isinstance(item, dict) or set(item) != set(FINDING_SCHEMA['required']):
        raise ValueError('검증 항목 형식 오류')
    for key, schema in FINDING_SCHEMA['properties'].items():
        value = item[key]
        if not isinstance(value, str) or ('enum' in schema and value not in schema['enum']) or len(value) > schema.get('maxLength', 1000):
            raise ValueError('검증 항목 값 오류')
    if not item['detail'].strip() or (visible(item) and not item['message'].strip()):
        raise ValueError('검증 항목 설명 누락')
    return item


def visible(item):
    return item['target'] != 'symbols' and item['severity'] in {'error', 'warning'}


def user_messages(findings):
    return list(dict.fromkeys(item['message'] for item in findings if visible(item)))


def retry_messages(findings):
    return list(dict.fromkeys(item['detail'] for item in findings if item['severity'] != 'info'))
