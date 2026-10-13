from __future__ import annotations

import os
import re
from .media import request as media_request
import subprocess
import time
from pathlib import Path
from typing import Callable

import requests

from .ai_settings import settings
from .transcription import CancellationToken, Options, TranscriptSegment, WhisperResult


class SpeechQuality:
    @classmethod
    def _confidence(cls, segments: list[TranscriptSegment] | tuple[TranscriptSegment, ...]) -> float:
        weights = [max(0.2, item.end - item.start) for item in segments]
        return sum(item.avg_logprob * weight for item, weight in zip(segments, weights)) / sum(weights)


    @classmethod
    def _looks_repetitive(cls, value: str) -> bool:
        words = value.lower().split()
        if len(words) < 12:
            return False
        six_word_phrases = [tuple(words[index:index + 6]) for index in range(len(words) - 5)]
        if len(six_word_phrases) != len(set(six_word_phrases)):
            return True
        for width in range(3, min(13, len(words) // 3 + 1)):
            phrases = [tuple(words[index:index + width]) for index in range(len(words) - width + 1)]
            if len(phrases) - len(set(phrases)) >= 3:
                return True
        return False


    @classmethod
    def _repetition_profile(cls,
        segments: list[TranscriptSegment] | tuple[TranscriptSegment, ...],
    ) -> tuple[bool, float]:
        """Detect a decoder collapse without relying on misleading confidence scores."""
        keys = [
            re.sub(r"[^\w]+", " ", segment.text.casefold(), flags=re.UNICODE).strip()
            for segment in segments
            if segment.text.strip()
        ]
        # A short chunk can collapse inside one segment or repeat fewer than
        # twelve segments. Require sustained exact repetition, not normal restatement.
        for key in keys:
            words = key.split()
            for width in range(1, min(32, len(words) // 4) + 1):
                for at in range(len(words) - width * 4 + 1):
                    phrase = words[at:at + width]
                    if len(" ".join(phrase)) >= 12 and words[at:at + width * 4] == phrase * 4:
                        return True, 1.0
        for previous_segment, segment in zip(segments, segments[1:]):
            if (segment.text.strip() == previous_segment.text.strip()
                    and len(segment.text.strip()) >= 20
                    and segment.end - segment.start < 0.5):
                return True, 2 / max(2, len(keys))
        run = 0
        previous = ""
        for key in keys:
            run = run + 1 if key == previous else 1
            previous = key
            if len(key) >= 20 and run >= 4:
                return True, run / len(keys)
        total = len(keys)
        if total < 12:
            return False, 0.0

        counts: dict[str, int] = {}
        for key in keys:
            if key:
                counts[key] = counts.get(key, 0) + 1
        if not counts:
            return False, 0.0

        significant = {
            key: count for key, count in counts.items()
            if count >= 8 and (len(key) >= 20 or count / total >= 0.20)
        }
        repeated_ratio = sum(significant.values()) / total
        dominant_key, dominant_count = max(counts.items(), key=lambda item: item[1])
        dominant_ratio = dominant_count / total
        global_collapse = (
            (len(dominant_key) >= 20 and dominant_count >= 12 and dominant_ratio >= 0.08)
            or (dominant_count >= 30 and dominant_ratio >= 0.35)
            or (sum(significant.values()) >= 20 and repeated_ratio >= 0.35)
        )
        window_size = min(24, total)
        local_collapse = False
        local_ratio = 0.0
        for start in range(total - window_size + 1):
            window_counts: dict[str, int] = {}
            for key in keys[start:start + window_size]:
                window_counts[key] = window_counts.get(key, 0) + 1
            local_key, local_count = max(window_counts.items(), key=lambda item: item[1])
            candidate_ratio = local_count / window_size
            local_ratio = max(local_ratio, candidate_ratio)
            if (
                local_count >= 8
                and candidate_ratio >= 0.40
                and (len(local_key) >= 20 or local_count >= 12)
            ):
                local_collapse = True
                break

        return global_collapse or local_collapse, max(repeated_ratio, local_ratio)


    @classmethod
    def _hint_hits(cls, value: str, prompt: str) -> int:
        lowered = value.casefold()
        terms = {
            term.strip().casefold() for term in re.split(r"[,\n;]+", prompt)
            if len(term.strip()) >= 2
        }
        return sum(term in lowered for term in terms)


    @classmethod
    def _low_confidence_groups(cls, segments: tuple[TranscriptSegment, ...], threshold: float,
                               limit: int = 12) -> list[tuple[int, int]]:
        suspects = [
            index for index, segment in enumerate(segments)
            if segment.avg_logprob < threshold and segment.text.strip()
        ]
        groups: list[tuple[int, int]] = []
        for index in suspects:
            if groups and index == groups[-1][1] + 1 and segments[index].end - segments[groups[-1][0]].start <= 28:
                groups[-1] = (groups[-1][0], index)
            else:
                groups.append((index, index))
            if len(groups) >= limit:
                break
        return groups


    @classmethod
    def _retry_low_confidence(cls, wav: Path, options: Options, result: WhisperResult,
                              incoming: dict[str, str] | None,
                              progress: Callable[[str, int, int], None] | None = None,
                              cancellation: CancellationToken | None = None,
                              ) -> WhisperResult:
        try:
            threshold = float(os.getenv("WHISPER_LOW_CONFIDENCE", "-0.65"))
        except ValueError:
            threshold = -0.65
        try:
            max_groups = max(0, int(os.getenv("WHISPER_RETRY_MAX_GROUPS", "4")))
        except ValueError:
            max_groups = 4
        try:
            max_seconds = max(0.0, float(os.getenv("WHISPER_RETRY_MAX_SECONDS", "90")))
        except ValueError:
            max_seconds = 90.0
        low_confidence_segments = sum(
            segment.avg_logprob < threshold and bool(segment.text.strip())
            for segment in result.segments
        )
        groups = cls._low_confidence_groups(
            result.segments, threshold, limit=max_groups
        ) if max_groups and max_seconds > 0 else []
        settings = dict(result.settings)
        settings.update({
            "low_confidence_threshold": str(threshold),
            "retry_max_groups": str(max_groups),
            "retry_max_seconds": str(max_seconds),
            "retry_beam_size": "5",
            "retry_no_context": "true",
        })
        if not groups:
            return WhisperResult(
                result.text, result.duration_seconds, result.segments,
                low_confidence_segments=low_confidence_segments,
                vad_fallback=result.vad_fallback,
                repetition_detected=result.repetition_detected,
                repetition_ratio=result.repetition_ratio,
                repetition_fallback_seconds=result.repetition_fallback_seconds,
                whisper_primary_seconds=result.whisper_primary_seconds,
                vad_fallback_seconds=result.vad_fallback_seconds,
                settings=settings,
            )
        expanded: list[tuple[int, int]] = []
        for first, last in groups:
            wider_first = max(0, first - 2)
            wider_last = min(len(result.segments) - 1, last + 2)
            if result.segments[wider_last].end - result.segments[wider_first].start <= 30:
                first, last = wider_first, wider_last
            if expanded and first <= expanded[-1][1] + 1:
                expanded[-1] = (expanded[-1][0], max(expanded[-1][1], last))
            else:
                expanded.append((first, last))
        groups = expanded[:max_groups]
        output = list(result.segments)
        shift = 0
        started = time.monotonic()
        attempted = 0
        accepted = 0
        for group_number, (first, last) in enumerate(groups, 1):
            if cancellation:
                cancellation.check()
            if group_number > 1 and time.monotonic() - started >= max_seconds:
                break
            if progress:
                progress("retry", group_number, len(groups))
            original = list(result.segments[first:last + 1])
            clip_start = max(0.0, original[0].start - 1.25)
            clip_end = min(result.duration_seconds, original[-1].end + 1.25)
            if clip_end - clip_start < 0.5:
                continue
            clip = wav.with_name(f"retry-{group_number:02d}.wav")
            attempted += 1
            try:
                media_request("clip", cancellation, source=wav, destination=clip,
                              start=clip_start, duration=clip_end - clip_start)
                data = dict(incoming or {})
                data.update({
                    "beam_size": "5", "temperature": "0.0", "temperature_inc": "0.0",
                    "entropy_thold": "2.4", "no_context": "true",
                    "retry_low_confidence": "false",
                })
                retry_options = Options(
                    language=options.language, course_name=options.course_name,
                    recognition_hint="",
                    format_text=options.format_text,
                    use_llm=False, llm_model=options.llm_model, retry_low_confidence=False,
                )
                candidate = cls._whisper(
                    clip, retry_options, data, cancellation=cancellation
                )
            except (OSError, subprocess.SubprocessError, requests.RequestException, ValueError, RuntimeError):
                if cancellation:
                    cancellation.check()
                continue
            finally:
                clip.unlink(missing_ok=True)

            selected = [
                segment for segment in candidate.segments
                if original[0].start <= clip_start + (segment.start + segment.end) / 2 <= original[-1].end
            ]
            if not selected:
                selected = list(candidate.segments)
            candidate_text = " ".join(segment.text for segment in selected).strip()
            original_text = " ".join(segment.text for segment in original).strip()
            previous_text = result.segments[first - 1].text.strip() if first else ""
            following_text = result.segments[last + 1].text.strip() if last + 1 < len(result.segments) else ""
            length_ratio = len(candidate_text) / max(1, len(original_text))
            original_confidence = cls._confidence(original)
            candidate_confidence = cls._confidence(selected)
            hint_gain = (
                cls._hint_hits(candidate_text, options.recognition_hint)
                - cls._hint_hits(original_text, options.recognition_hint)
            )
            if (not candidate_text or not 0.45 <= length_ratio <= 2.2
                    or candidate_text in {previous_text, following_text}
                    or cls._looks_repetitive(candidate_text)
                    or not (
                        candidate_confidence >= original_confidence + 0.05
                        or (hint_gain > 0 and candidate_confidence >= original_confidence - 0.25)
                    )):
                continue
            replacement = [
                TranscriptSegment(
                    0, max(original[0].start, clip_start + item.start),
                    max(
                        max(original[0].start, clip_start + item.start),
                        min(original[-1].end, clip_start + item.end),
                    ), item.text,
                    item.avg_logprob, item.no_speech_prob, True,
                ) for item in selected
            ]
            at_first, at_last = first + shift, last + shift
            output[at_first:at_last + 1] = replacement
            shift += len(replacement) - (last - first + 1)
            accepted += 1

        normalized = tuple(
            TranscriptSegment(index, item.start, item.end, item.text, item.avg_logprob,
                              item.no_speech_prob, item.retried)
            for index, item in enumerate(output, 1)
        )
        return WhisperResult(
            " ".join(item.text for item in normalized), result.duration_seconds, normalized,
            low_confidence_segments=low_confidence_segments,
            retry_attempted_groups=attempted,
            retry_accepted_groups=accepted,
            retry_processing_seconds=time.monotonic() - started,
            vad_fallback=result.vad_fallback,
            repetition_detected=result.repetition_detected,
            repetition_ratio=result.repetition_ratio,
            repetition_fallback_seconds=result.repetition_fallback_seconds,
            whisper_primary_seconds=result.whisper_primary_seconds,
            vad_fallback_seconds=result.vad_fallback_seconds,
            settings=settings,
        )
