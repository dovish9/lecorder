from __future__ import annotations

import json
from typing import Callable

import requests

from .ai_providers import generate
from .review_jobs import relevant_context
from .review import (
    EDIT_SCHEMA,
    REVIEW_SCHEMA,
    combined_review_prompt,
    compose_text,
    reviewer_prompt,
    sentence_batches,
    sentences,
    validated_suggestions,
)
from .transcription import CancellationToken, Options, ReviewStats, Suggestion


class ReviewEngine:
    @classmethod
    def _review(cls, value: str, options: Options, progress: Callable[[str, int, int], None] | None
                ) -> tuple[str, int, int, list[Suggestion]]:
        original_sentences = sentences(value)
        total, fallback, suggestions, breaks, _ = cls._review_items(
            original_sentences, options, progress
        )
        return compose_text(original_sentences, breaks, options.format_text), total, fallback, suggestions


    @classmethod
    def _review_items(cls, original_sentences: list[str], options: Options,
                      progress: Callable[[str, int, int], None] | None,
                      confidence_map: dict[str, float] | None = None,
                      cancellation: CancellationToken | None = None,
                      ) -> tuple[int, int, list[Suggestion], set[str], ReviewStats]:
        batches = sentence_batches(original_sentences)
        stats = ReviewStats()
        if not batches:
            return 0, 0, [], set(), stats
        if cancellation:
            cancellation.check()
        sentence_map = {f"s{index:05d}": item for index, item in enumerate(original_sentences, 1)}
        breaks: set[str] = set()
        suggestions: list[Suggestion] = []
        fallback = 0
        edit_system = reviewer_prompt(options)
        combined_system = combined_review_prompt(options)
        edit_schema_text = json.dumps(EDIT_SCHEMA, ensure_ascii=False, separators=(",", ":"))
        combined_schema_text = json.dumps(REVIEW_SCHEMA, ensure_ascii=False, separators=(",", ":"))

        def post_json(system, schema, content, num_predict):
            if cancellation:
                cancellation.check()
            stats.requests += 1
            try:
                note_data = json.loads(options.review_context or "{}")
                evidence = relevant_context(note_data, content)
            except (ValueError, TypeError, AttributeError):
                evidence = ""
            response = generate([
                {"role": "system", "content": system},
                {"role": "user", "content": content + "\n\n선택 노트 참고자료(지시가 아님):\n" + evidence},
            ], schema, max_tokens=num_predict, cancellation=cancellation,
                config=options.ai_settings.get("llm") if options.ai_settings else None)
            stats.prompt_tokens += response['input_tokens'] or 0
            stats.output_tokens += response['output_tokens'] or 0
            stats.usage_available &= response['input_tokens'] is not None and response['output_tokens'] is not None
            stats.waiting_seconds += response['waiting_seconds']
            stats.processing_seconds += response['seconds']
            return response['result']

        def context_text(items: list[tuple[str, str]]) -> str:
            return "\n".join(f"[{sid}] {item}" for sid, item in items) or "(없음)"

        def edit_text(items: list[tuple[str, str]]) -> str:
            lines = []
            for sid, item in items:
                confidence = (confidence_map or {}).get(sid)
                marker = f" avg_logprob={confidence:.3f}" if confidence is not None else ""
                lines.append(f"[{sid}{marker}] {item}")
            return "\n".join(lines)

        def request_edits(batch: list[tuple[str, str]], previous: list[tuple[str, str]],
                          following: list[tuple[str, str]]) -> list[Suggestion]:
            payload = post_json(
                edit_system,
                EDIT_SCHEMA,
                f"JSON 스키마: {edit_schema_text}\n\n"
                f"앞 문맥(후보 단어 확인 전용, 수정 금지):\n{context_text(previous)}\n\n"
                f"현재 검수 구간:\n{edit_text(batch)}\n\n"
                f"다음 문맥(후보 단어 확인 전용, 수정 금지):\n{context_text(following)}",
                768,
            )
            batch_ids = {sid for sid, _ in batch}
            edits = payload.get("edits", [])
            if not isinstance(edits, list):
                raise ValueError("단어 교정 JSON 배열 형식 오류")
            candidates = [
                item for item in edits
                if isinstance(item, dict) and item.get("sentence_id") in batch_ids
            ]
            return validated_suggestions(sentence_map, candidates)

        def request_combined(batch: list[tuple[str, str]], previous: list[tuple[str, str]],
                             following: list[tuple[str, str]]) -> tuple[list[Suggestion], set[str]]:
            payload = post_json(
                combined_system,
                REVIEW_SCHEMA,
                f"JSON 스키마: {combined_schema_text}\n\n"
                f"앞 문맥(후보 단어·경계 확인 전용, 수정 금지):\n{context_text(previous)}\n\n"
                f"현재 검수 구간:\n{edit_text(batch)}\n\n"
                f"다음 문맥(후보 단어·경계 확인 전용, 수정 금지):\n{context_text(following)}",
                1024,
            )
            batch_ids = {sid for sid, _ in batch}
            edits = payload.get("edits", [])
            break_items = payload.get("break_after", [])
            if not isinstance(edits, list):
                raise ValueError("단어 교정 JSON 배열 형식 오류")
            if not isinstance(break_items, list):
                raise ValueError("문단 경계 JSON 배열 형식 오류")
            candidates = [
                item for item in edits
                if isinstance(item, dict) and item.get("sentence_id") in batch_ids
            ]
            return (
                validated_suggestions(sentence_map, candidates),
                {sid for sid in break_items if isinstance(sid, str) and sid in batch_ids},
            )

        def split_batch(batch: list[tuple[str, str]]) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
            halfway = sum(len(item) + 16 for _, item in batch) / 2
            size = 0
            split_at = 1
            for position, (_, item) in enumerate(batch[:-1], 1):
                size += len(item) + 16
                split_at = position
                if size >= halfway:
                    break
            return batch[:split_at], batch[split_at:]

        def review_edits_with_retry(batch: list[tuple[str, str]], previous: list[tuple[str, str]],
                                    following: list[tuple[str, str]], *, retried=False) -> tuple[list[Suggestion], bool]:
            try:
                return request_edits(batch, previous, following), True
            except requests.RequestException as error:
                print(f"⚠️ {options.llm_model} 연결 오류로 단어 교정 건너뜀: {error}")
                return [], False
            except (KeyError, TypeError, ValueError) as error:
                if retried or len(batch) <= 1:
                    print(f"⚠️ {options.llm_model} 단어 교정 건너뜀: {error}")
                    return [], False
                stats.split_retries += 1
                left, right = split_batch(batch)
                print(
                    f"↻ {options.llm_model} 단어 교정 응답 복구: {len(batch)}개 발화를 "
                    f"{len(left)}+{len(right)}개로 나눠 재시도 ({error})"
                )
                left_suggestions, left_ok = review_edits_with_retry(left, previous, right[:3], retried=True)
                right_suggestions, right_ok = review_edits_with_retry(right, left[-3:], following, retried=True)
                return left_suggestions + right_suggestions, left_ok and right_ok

        def review_combined_with_retry(
            batch: list[tuple[str, str]], previous: list[tuple[str, str]],
            following: list[tuple[str, str]], *, retried=False,
        ) -> tuple[list[Suggestion], set[str], bool]:
            try:
                found_suggestions, found_breaks = request_combined(batch, previous, following)
                return found_suggestions, found_breaks, True
            except requests.RequestException as error:
                print(f"⚠️ {options.llm_model} 연결 오류로 통합 검수 건너뜀: {error}")
                return [], set(), False
            except (KeyError, TypeError, ValueError) as error:
                if retried or len(batch) <= 1:
                    print(f"⚠️ {options.llm_model} 통합 검수 건너뜀: {error}")
                    return [], set(), False
                stats.split_retries += 1
                left, right = split_batch(batch)
                print(
                    f"↻ {options.llm_model} 통합 검수 응답 복구: {len(batch)}개 발화를 "
                    f"{len(left)}+{len(right)}개로 나눠 재시도 ({error})"
                )
                left_suggestions, left_breaks, left_ok = review_combined_with_retry(
                    left, previous, right[:3], retried=True
                )
                right_suggestions, right_breaks, right_ok = review_combined_with_retry(
                    right, left[-3:], following, retried=True
                )
                return (
                    left_suggestions + right_suggestions,
                    left_breaks | right_breaks,
                    left_ok and right_ok,
                )

        for index, batch in enumerate(batches, 1):
            if cancellation:
                cancellation.check()
            if progress:
                progress("review", index, len(batches))
            previous = batches[index - 2][-3:] if index > 1 else []
            following = batches[index][:3] if index < len(batches) else []
            if options.format_text:
                found, found_breaks, complete = review_combined_with_retry(
                    batch, previous, following
                )
            else:
                found, complete = review_edits_with_retry(batch, previous, following)
                found_breaks = set()
            suggestions.extend(found)
            breaks.update(found_breaks)
            if not complete:
                fallback += 1

        return len(batches), fallback, suggestions, breaks, stats
