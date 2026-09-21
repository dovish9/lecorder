"""Source-backed terminology candidates and compact transcription hint filtering."""

import re
from collections import Counter
from .korean_terms import noun_terms

STOP = set(
    (
        "the and for with that this from into have are was were will can not you your then than using use page chapter lecture example figure table slide notes www http com org "
        "how what when where why which who whose these those its our their they them there here about before after same different between through without whether rather "
        "each every some any all also only just more most less much many now once still first next last one two three tells tell told find found make made take took "
        "let lets must would could should does doing done been being out off new way says said look know means result form case given shows show get got "
        "의미 문제 기준 다음 각각 같이 하 개 공통 경우 내용 방법 정도 결과 사용 위 아래 중 것 수 때 대한 있는 있다 없는 없다 이를 경우 위해 또는 그리고 따라서 것은 것은 다음 우리가 그림 예제 정리 설명"
    ).split()
)


def hint_terms(terms):
    """Keep ranked terminology, dropping filler and redundant inflected forms."""
    available = {term.casefold() for term in terms}
    selected = []
    seen = set()
    for term in terms:
        key = term.casefold().strip()
        if not key or key in seen or any(word in STOP for word in key.split()):
            continue
        # Keep the source spelling; only suppress a particle form when its base
        # also exists among the source-backed candidates.
        if re.fullmatch(r"[가-힣]+", key) and any(
            key.endswith(suffix) and key[: -len(suffix)] in available
            for suffix in ("의", "과", "와", "은", "는", "을", "를", "에서")
        ):
            continue
        seen.add(key)
        selected.append(term)
    return selected


def candidates(pages):
    terms = {}
    korean = noun_terms([page["text"] for page in pages])
    for page, nouns in zip(pages, korean):
        local = Counter(term for term in nouns if term not in STOP)
        # Build contiguous phrases inside stop-word boundaries, not arbitrary 3-word chunks.
        for line in page["text"].splitlines():
            words = re.findall(r"[A-Za-z][A-Za-z-]{2,}", line)
            for start, word in enumerate(words):
                if word.casefold() in STOP:
                    continue
                for size in (1, 2, 3):
                    parts = words[start : start + size]
                    if len(parts) != size or any(p.casefold() in STOP for p in parts):
                        break
                    term = " ".join(parts)
                    local[term] += 1
        for term, count in local.items():
            item = terms.setdefault(
                term.casefold(),
                {"term": term, "pages": [], "score": 0, "included": True},
            )
            if page["number"] not in item["pages"]:
                item["pages"].append(page["number"])
            item["score"] += count * (2 if " " in term else 1)
    ranked = sorted(
        terms.values(),
        key=lambda item: (-item["score"], -len(item["pages"]), item["term"]),
    )[:200]
    return [dict(item, id=f"k{index}") for index, item in enumerate(ranked, 1)]
