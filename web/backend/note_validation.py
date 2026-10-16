"""Deterministic validation; unavailable validators never count as a pass."""
import json
import re
from .transcription import CancellationToken
import subprocess
from .processes import worker


def code_issues(result, cancellation=None):
    text = result['markdown']
    issues = []
    if re.search(r'(?:다음|아래|다음과 같|다음과 같은)\s*[:：]?\s*$', text):
        issues.append('본문이 다음 내용이나 수식을 예고한 채 끝납니다.')
    lines = text.splitlines()
    for index, line in enumerate(lines):
        heading = re.match(r'^(#{1,6})\s+\S', line)
        if heading:
            following = next((x for x in lines[index + 1:] if x.strip()), '')
            next_heading = re.match(r'^(#{1,6})\s', following)
            # A section may begin directly with a subsection. Each child is
            # checked independently, so empty leaves are still rejected.
            if not following or (next_heading and len(next_heading[1]) <= len(heading[1])):
                issues.append('내용이 없는 제목이 있습니다.')
                break
    if re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', text):
        issues.append('본문에 잘못된 제어 문자가 있습니다.')
    token = cancellation or CancellationToken()
    try:
        issues.extend(worker.call("math", text, cancellation=token, timeout=10))
    except (OSError, subprocess.SubprocessError, ValueError):
        issues.append('수식 검증기 실행에 실패했습니다.')
    return issues
