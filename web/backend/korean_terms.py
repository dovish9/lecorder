"""Korean noun phrases from the locally compiled Kiwi helper (no Python wheel)."""
import json
import re
from .processes import CancellationToken
from .config import SCRIPT_ROOT
from .platforms import host

HELPER = host.executable(SCRIPT_ROOT / "dependencies/kiwi/bin", "kiwi-helper")
MODEL = SCRIPT_ROOT / "dependencies/kiwi/models/cong/base"


def noun_terms(texts, cancellation=None):
    """Load Kiwi once per document and preserve the source's compound spelling."""
    if not any(re.search(r"[가-힣]", text) for text in texts):
        return [[] for _ in texts]
    if not HELPER.is_file() or not (MODEL / "cong.mdl").is_file():
        raise RuntimeError("한국어 키워드 추출에는 Kiwi가 필요합니다. scripts/build-kiwi.sh를 실행하세요.")
    response = (cancellation or CancellationToken()).run(
        [str(HELPER), str(MODEL)], input_data=json.dumps(texts, ensure_ascii=False).encode("utf-8"), timeout=180,
    )
    documents = json.loads(response.stdout)
    if len(documents) != len(texts):
        raise ValueError("Kiwi 페이지 수 불일치")
    return [_phrases(text, tokens) for text, tokens in zip(texts, documents)]


def _phrases(text, tokens):
    # Kiwi uses UTF-16 positions; slice bytes to preserve offsets after emoji too.
    source = text.encode("utf-16-le")
    terms, group = [], []

    def flush():
        if not group:
            return
        start, end = group[0]["start"], group[-1]["start"] + group[-1]["length"]
        term = source[start * 2:end * 2].decode("utf-16-le").strip()
        if re.fullmatch(r"[가-힣]{2,24}", term):
            terms.append(term)
        group.clear()

    for token in tokens:
        if token["tag"] not in {"NNG", "NNP", "XR", "XSN", "XPN"}:
            flush()
            continue
        if group and (token["start"] != group[-1]["start"] + group[-1]["length"] or token["word"] != group[-1]["word"]):
            flush()
        group.append(token)
    flush()
    return terms
