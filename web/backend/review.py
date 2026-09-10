"""Pure transcript formatting and conservative review-validation rules."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any

from .transcription import Options, Suggestion

MIN_EDIT_CONFIDENCE = 0.80


def apply_rules(value: str, rules: str) -> str:
    for raw in rules.splitlines()[:100]:
        separator = "=>" if "=>" in raw else "="
        if separator not in raw:
            continue
        wrong, correct = (part.strip() for part in raw.split(separator, 1))
        if len(wrong) >= 2 and correct:
            value = value.replace(wrong, correct)
    return value


def sentences(value: str) -> list[str]:
    """Normalize whitespace while preserving every non-duplicate utterance."""
    clean = re.sub(r"[ \t]+", " ", value.replace("\r", "\n"))
    units = re.split(r"(?<=[.!?。！？])\s+|\n+", clean)
    output: list[str] = []
    for unit in units:
        item = unit.strip()
        if item and (not output or item != output[-1]):
            output.append(re.sub(r"\s+([,.!?。！？])", r"\1", item))
    return output


def sentence_batches(items: list[str], limit: int = 1800) -> list[list[tuple[str, str]]]:
    batches: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    size = 0
    for index, sentence in enumerate(items, 1):
        tagged = (f"s{index:05d}", sentence)
        cost = len(sentence) + 16
        if current and size + cost > limit:
            batches.append(current)
            current, size = [], 0
        current.append(tagged)
        size += cost
    if current:
        batches.append(current)
    return batches


EDIT_SCHEMA = {
    "type": "object",
    "properties": {
        "edits": {
            "type": "array", "maxItems": 4,
            "items": {
                "type": "object",
                "properties": {
                    "sentence_id": {"type": "string"},
                    "original": {"type": "string", "maxLength": 24},
                    "replacement": {"type": "string", "maxLength": 24},
                    "error_type": {
                        "type": "string",
                        "enum": ["phonetic_asr", "technical_term"],
                    },
                    "reason": {"type": "string", "maxLength": 80},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": [
                    "sentence_id", "original", "replacement", "error_type", "reason", "confidence"
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["edits"],
    "additionalProperties": False,
}


PARAGRAPH_SCHEMA = {
    "type": "object",
    "properties": {
        "break_after": {"type": "array", "maxItems": 6, "items": {"type": "string"}},
    },
    "required": ["break_after"],
    "additionalProperties": False,
}


REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "edits": EDIT_SCHEMA["properties"]["edits"],
        "break_after": PARAGRAPH_SCHEMA["properties"]["break_after"],
    },
    "required": ["edits", "break_after"],
    "additionalProperties": False,
}


def _language_rule(language: str) -> str:
    return {
        "ko": "한국어가 주 언어이며 실제 영어 전문용어는 영어로 보존한다.",
        "en": "The lecture is in English. Keep English technical terms in English.",
        "auto": "원문의 언어와 자연스러운 한영 혼용을 보존한다.",
    }[language]


def reviewer_prompt(options: Options) -> str:
    hints = options.prompt.strip()[:3000] or "별도 용어 힌트 없음"
    return f"""당신은 대학 강의 음성 인식(ASR)의 단어 오인식만 찾는 보수적인 검수자다.
과목명: {options.course_name}
목적: 실제 발화를 고치거나 다듬지 않고, 다른 단어로 잘못 인식된 부분만 최소 범위로 제안한다.
언어: {_language_rule(options.language)}
과목·강의 힌트: {hints}

규칙:
1. 최우선 절대 조건: edits는 original과 replacement를 실제로 소리 내어 읽었을 때 발음이 서로 비슷한 경우에만 제안한다. 발음이 비슷하지 않거나 발음 유사성을 판단할 수 없으면 뜻·문맥·철자·전문용어 여부·confidence와 관계없이 무조건 제안하지 않는다.
2. JSON의 edits만 반환한다. 제안 누락이 잘못된 제안보다 낫다. 조금이라도 애매하면 제안하지 않는다.
3. 자연스럽지 않거나 문법적으로 틀려도 실제 화자의 발화일 수 있으면 그대로 둔다. 문맥상 더 자연스러운 문장을 만들지 않는다.
4. 모든 제안은 original과 replacement의 실제 발음이 매우 가까워야 한다. 뜻이나 문맥만 비슷하고 발음이 다르면 절대 제안하지 않는다.
5. 허용 유형은 두 가지뿐이다. phonetic_asr: 발음이 매우 가까운 다른 단어로 인식됨. technical_term: 발음이 매우 가까운 전문용어가 다른 단어로 인식됨. technical_term도 발음 조건을 예외로 면제하지 않는다.
6. 강의 힌트는 참고할 철자 후보 목록이다. 힌트에 없는 전문용어도 제안할 수 있지만, 힌트에 있는 단어라도 original과 발음이 가깝지 않으면 제안하지 않는다.
7. 영어·영어의 한글 음역·한국어 표현 사이를 번역하거나 통일하지 않는다. 화자가 말한 언어와 표기를 보존한다. 예: population, 파퓰레이션, 모집단은 서로 교체하지 않는다.
8. 자연스럽거나 전문적인 표현을 만들기 위한 의미 교체를 하지 않는다. 예: 들어갈→포함될, 예측→추정은 발음이 다르므로 금지한다.
9. 맞춤법·활용·조사·어미·띄어쓰기·문장부호·문법·말투·반복·추임새·군더더기를 고치거나 지우지 않는다. 예: 돼→되 교정은 금지한다.
10. 원문에 없는 주어·목적어·설명·단어를 보충하지 않는다. 요약, 번역, 사실 교정, 숫자·수식 변경을 하지 않는다.
11. original과 replacement는 각각 1~3어절, 24자 이하의 가장 작은 연속 범위로 쓴다. 문장이나 절 전체를 제안하지 않는다.
12. confidence가 0.80 이상이고 두 표현의 비슷한 발음을 구체적으로 설명할 수 있는 제안만 최대 4개 반환한다. '문맥상 자연스러움', '전문용어로 정확함', '강의 힌트에 있음'만으로는 근거가 아니다.
13. Whisper avg_logprob가 낮은 발화를 우선 확인하되, 낮은 점수만으로 수정하지 않는다. 앞뒤 문맥은 후보 단어 확인에만 사용하고 수정 대상으로 삼지 않는다.
14. sentence_id는 반드시 현재 검수 구간의 ID를 사용하고 original은 해당 발화에 그대로 존재해야 한다.
15. 조건을 모두 충족하는 제안이 없으면 edits를 빈 배열로 반환한다. /no_think 같은 제어 문자열을 출력하지 않는다."""


def combined_review_prompt(options: Options) -> str:
    hints = options.prompt.strip()[:3000] or "별도 용어 힌트 없음"
    return f"""당신은 대학 강의 음성 인식(ASR)의 단어 오인식과 문단 경계만 검수한다.
과목명: {options.course_name}
언어: {_language_rule(options.language)}
과목·강의 힌트: {hints}

반환 규칙:
1. JSON의 edits와 break_after만 반환한다. 단어 교정과 문단 경계 외에는 원문을 바꾸지 않는다.
2. 조건을 만족하는 결과가 없으면 각 배열을 비워 반환한다. /no_think 같은 제어 문자열은 출력하지 않는다.

edits 규칙:
1. 최우선 절대 조건: edits는 original과 replacement를 실제로 소리 내어 읽었을 때 발음이 서로 비슷한 경우에만 제안한다. 발음이 비슷하지 않거나 발음 유사성을 판단할 수 없으면 뜻·문맥·철자·전문용어 여부·confidence와 관계없이 무조건 제안하지 않는다.
2. 제안 누락이 잘못된 제안보다 낫다. 조금이라도 애매하면 제안하지 않는다.
3. 실제 화자의 발화를 다듬거나 문법을 고치지 않는다. 뜻이나 문맥만 비슷한 단어로 교체하지 않는다.
4. phonetic_asr과 technical_term 유형만 허용하며, original과 replacement의 실제 발음이 매우 가까워야 한다. technical_term도 발음 조건을 예외로 면제하지 않는다.
5. 강의 힌트는 철자 후보일 뿐이며, 힌트에 있더라도 발음이 가깝지 않으면 제안하지 않는다.
6. 영어·영어의 한글 음역·한국어 표현을 번역하거나 통일하지 않는다. 예: population, 파퓰레이션, 모집단은 서로 교체하지 않는다.
7. 의미 교체, 맞춤법, 활용, 조사, 어미, 띄어쓰기, 문장부호, 말투, 반복, 추임새를 고치지 않는다. 예: 들어갈→포함될, 예측→추정, 돼→되는 금지한다.
8. 원문에 없는 내용을 보충하거나 요약·번역·사실 교정·숫자 변경을 하지 않는다.
9. original과 replacement는 각각 1~3어절, 24자 이하의 가장 작은 연속 범위로 쓴다.
10. confidence가 0.80 이상이고 비슷한 발음을 구체적으로 설명할 수 있는 제안만 최대 4개 반환한다.
11. Whisper avg_logprob는 확인 우선순위에만 사용하고, sentence_id와 original은 반드시 현재 검수 구간에 존재해야 한다.

break_after 규칙:
1. 의미상 한 문단이 끝나는 현재 검수 구간의 발화 ID만 최대 6개 반환한다.
2. 하위 주제 전환, 정의와 예시 사이의 전환, 질문·답변 종료, 계산·논증 단계 전환이 명확할 때만 나눈다.
3. 같은 개념의 설명·보충·재진술과 하나의 예시를 이루는 발화는 같은 문단에 둔다.
4. 글자 수나 발화 수를 맞추기 위해 나누지 않고, 짧은 추임새나 접속 표현만으로 나누지 않는다.
5. 앞뒤 문맥은 후보 단어 확인과 경계 판단에만 사용하며 수정 대상으로 삼지 않는다."""


def _numbers(value: str) -> list[str]:
    return re.findall(r"\d+(?:[.,]\d+)*%?", value)


def _changed_words(original: str, replacement: str) -> tuple[str, str]:
    before = original.split()
    after = replacement.split()
    while before and after and before[0].casefold() == after[0].casefold():
        before.pop(0)
        after.pop(0)
    while before and after and before[-1].casefold() == after[-1].casefold():
        before.pop()
        after.pop()
    return " ".join(before), " ".join(after)


def _phonetic_similarity(original: str, replacement: str) -> float:
    def key(value: str) -> str:
        decomposed = unicodedata.normalize("NFD", value.casefold())
        return "".join(character for character in decomposed if character.isalnum())

    before, after = key(original), key(replacement)
    if not before or not after:
        return 0.0
    return SequenceMatcher(None, before, after, autojunk=False).ratio()


def validated_suggestions(
    sentence_map: dict[str, str], candidates: list[dict[str, Any]]
) -> list[Suggestion]:
    accepted: list[Suggestion] = []
    used_originals: dict[str, set[str]] = {}
    banned_reason_terms = (
        "문법", "자연스럽", "명확", "적절", "말투", "문체", "중복", "불필요", "띄어쓰기",
        "grammar", "natural", "clarity", "style", "redundan", "spacing", "punctuation",
    )
    evidence_terms = {
        "phonetic_asr": ("발음", "소리", "phonetic", "sound", "homophone"),
        "technical_term": ("힌트", "반복", "전문용어", "용어", "glossary", "term", "repeated"),
    }
    for item in candidates[:4]:
        try:
            sentence_id = str(item["sentence_id"])
            original = str(item["original"]).strip()
            replacement = str(item["replacement"]).strip()
            error_type = str(item["error_type"]).strip()
            reason = str(item["reason"]).strip()[:80]
            confidence = float(item["confidence"])
            current = sentence_map[sentence_id]
        except (KeyError, TypeError, ValueError):
            continue
        if confidence < MIN_EDIT_CONFIDENCE or error_type not in {"phonetic_asr", "technical_term"}:
            continue
        if not original or not replacement or original == replacement:
            continue
        if len(original) > 24 or len(replacement) > 24:
            continue
        if len(original.split()) > 3 or len(replacement.split()) > 3:
            continue
        if re.search(r"[,.;:!?。！？]", original + replacement):
            continue
        if original not in current or original in used_originals.get(sentence_id, set()):
            continue
        normalized_original = re.sub(r"[\s\W_]+", "", original, flags=re.UNICODE).casefold()
        normalized_replacement = re.sub(r"[\s\W_]+", "", replacement, flags=re.UNICODE).casefold()
        if not normalized_original or normalized_original == normalized_replacement:
            continue
        if normalized_original in normalized_replacement or normalized_replacement in normalized_original:
            continue
        if _numbers(original) != _numbers(replacement):
            continue
        changed_original, changed_replacement = _changed_words(original, replacement)
        if not changed_original or not changed_replacement:
            continue
        if _phonetic_similarity(changed_original, changed_replacement) < 0.62:
            continue
        reason_key = reason.casefold()
        if any(term in reason_key for term in banned_reason_terms):
            continue
        if not any(term in reason_key for term in evidence_terms[error_type]):
            continue
        used_originals.setdefault(sentence_id, set()).add(original)
        accepted.append(Suggestion(sentence_id, original, replacement, reason, confidence))
    return accepted


def compose_text(items: list[str], breaks: set[str], format_text: bool) -> str:
    if not format_text:
        return " ".join(items).strip()
    output: list[str] = []
    paragraph: list[str] = []
    for index, item in enumerate(items, 1):
        paragraph.append(item)
        if f"s{index:05d}" in breaks:
            output.append(" ".join(paragraph))
            paragraph = []
    if paragraph:
        output.append(" ".join(paragraph))
    return "\n\n".join(output).strip()
