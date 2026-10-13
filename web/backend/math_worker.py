"""Run the same vendored KaTeX as the browser in an isolated embedded V8."""
import json
from pathlib import Path
import sys
from py_mini_racer import MiniRacer


def check_math(text):
    root = Path(__file__).resolve().parents[2]
    source = (root / 'web/static/vendor/katex/katex.mjs').read_text(encoding='utf-8')
    # This pinned bundle is self-contained ESM with one final export statement.
    # Fail explicitly if a future bundle changes shape; never silently skip math.
    body, separator, exports = source.rpartition('\nexport {')
    if not separator or not exports.strip().endswith('};') or 'katex as default' not in exports:
        raise ValueError('Unsupported KaTeX bundle exports')
    with MiniRacer() as engine:
        engine.set_hard_memory_limit(128 * 1024 * 1024)
        engine.eval(body, timeout_sec=2)
        engine.eval(Path(__file__).with_name('check_math.js').read_text(encoding='utf-8'), timeout_sec=1)
        return engine.call('checkMath', text, timeout_sec=5)


if __name__ == '__main__':
    print(json.dumps(check_math(json.load(sys.stdin)), ensure_ascii=False))
