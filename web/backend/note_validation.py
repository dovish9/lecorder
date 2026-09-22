"""Deterministic validation; unavailable validators never count as a pass."""
import json
import re
import shutil
import subprocess
from pathlib import Path


def code_issues(result):
    text = result['markdown']
    issues = []
    if re.search(r'(?:다음|아래|다음과 같|다음과 같은)\s*[:：]?\s*$', text):
        issues.append('본문이 다음 내용이나 수식을 예고한 채 끝납니다.')
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if re.match(r'^#{1,6}\s+\S', line):
            following = next((x for x in lines[index + 1:] if x.strip()), '')
            if not following or re.match(r'^#{1,6}\s', following):
                issues.append('내용이 없는 제목이 있습니다.')
                break
    if re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', text):
        issues.append('본문에 잘못된 제어 문자가 있습니다.')
    node = shutil.which('node') or next((str(p) for p in (Path('/opt/homebrew/bin/node'), Path('/usr/local/bin/node')) if p.is_file()), None)
    if not node:
        issues.append('수식 검증기를 실행할 Node.js가 없어 검증하지 못했습니다.')
    else:
        try:
            checked = subprocess.run([node, str(Path(__file__).resolve().parents[2] / 'scripts/check-note-math.mjs')],
                                     input=json.dumps(text), text=True, capture_output=True, timeout=10, check=True)
            issues.extend(json.loads(checked.stdout))
        except (OSError, subprocess.SubprocessError, ValueError):
            issues.append('수식 검증기 실행에 실패했습니다.')
    return issues
