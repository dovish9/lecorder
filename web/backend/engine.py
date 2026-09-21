from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import wave
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

import requests

from .config import OLLAMA_URL, WHISPER_URL
from .whisper_runtime import whisper_runtime
from .inference import inference
from .review_jobs import relevant_context
from .note_analysis import hint_terms
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
from .transcription import (
    CancellationToken,
    Options,
    Result,
    ReviewStats,
    Suggestion,
    TranscriptSegment,
    WhisperResult,
)


def port_ready(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.35):
            return True
    except OSError:
        return False


def ollama_ready(model: str) -> tuple[bool, bool]:
    try:
        response = requests.get(OLLAMA_URL.rsplit("/", 1)[0] + "/tags", timeout=(1, 3))
        response.raise_for_status()
        installed = {str(item.get("name") or item.get("model") or "") for item in response.json().get("models", [])}
        return True, model in installed
    except (requests.RequestException, ValueError, AttributeError):
        return False, False


class Transcriber:
    def transcribe(self, source: Path, options: Options, incoming: dict[str, str] | None = None,
                   progress: Callable[[str, int, int], None] | None = None,
                   cancellation: CancellationToken | None = None) -> Result:
        wav = source.with_name("converted-audio.wav")
        if cancellation:
            cancellation.check()
        conversion_started = time.monotonic()
        self._convert(source, wav, cancellation)
        conversion_seconds = time.monotonic() - conversion_started
        with inference.lease(speech=True, cancellation=cancellation), whisper_runtime.session(cancellation):
            options = self._with_note_hint(options)
            whispered = self._whisper_chunked(wav, options, incoming, progress, cancellation)
            if options.retry_low_confidence:
                whispered = self._retry_low_confidence(
                    wav, options, whispered, incoming, progress, cancellation
                )
        corrected_segments = whispered.segments
        items = [segment.text for segment in corrected_segments]
        qwen_started = time.monotonic()
        if options.use_llm:
            total, fallback, suggestions, breaks, review_stats = self._review_items(
                items, options, progress,
                {
                    f"s{position:05d}": segment.avg_logprob
                    for position, segment in enumerate(corrected_segments, 1)
                },
                cancellation,
            )
        else:
            total = fallback = 0
            suggestions = []
            breaks = set()
            review_stats = ReviewStats()
        qwen_seconds = time.monotonic() - qwen_started if options.use_llm else 0.0
        value = compose_text(items, breaks, options.format_text)
        return Result(
            text=value,
            llm_chunks=total,
            fallback_chunks=fallback,
            suggestions=tuple(suggestions),
            segments=corrected_segments,
            paragraph_breaks=tuple(
                index for index in range(1, len(items) + 1)
                if f"s{index:05d}" in breaks
            ),
            duration_seconds=whispered.duration_seconds,
            low_confidence_segments=whispered.low_confidence_segments,
            retry_attempted_groups=whispered.retry_attempted_groups,
            retry_accepted_groups=whispered.retry_accepted_groups,
            retry_processing_seconds=whispered.retry_processing_seconds,
            lowest_avg_logprob=min(
                (segment.avg_logprob for segment in whispered.segments), default=None
            ),
            vad_fallback=whispered.vad_fallback,
            repetition_detected=whispered.repetition_detected,
            repetition_ratio=whispered.repetition_ratio,
            repetition_fallback_seconds=whispered.repetition_fallback_seconds,
            conversion_seconds=conversion_seconds,
            whisper_primary_seconds=whispered.whisper_primary_seconds,
            vad_fallback_seconds=whispered.vad_fallback_seconds,
            qwen_seconds=qwen_seconds,
            pipeline_settings={
                **whispered.settings,
                "recognition_hint_tokens": str(options.recognition_hint_tokens),
                "recognition_hint_warning": options.recognition_hint_warning,
                "qwen_review_policy": (
                    "combined-asr-paragraph-v4" if options.format_text else "word-asr-v3"
                ),
                "qwen_edit_confidence": "0.80",
                "qwen_paragraph_review": str(options.format_text).lower(),
            },
            qwen_requests=review_stats.requests,
            qwen_split_retries=review_stats.split_retries,
            qwen_prompt_tokens=review_stats.prompt_tokens,
            qwen_output_tokens=review_stats.output_tokens,
        )

    @staticmethod
    def _convert(source: Path, destination: Path,
                 cancellation: CancellationToken | None = None) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("ffmpeg를 찾을 수 없습니다.")
        command = [ffmpeg, "-y", "-i", str(source), "-ar", "16000", "-ac", "1",
                   "-c:a", "pcm_s16le", str(destination)]
        if cancellation:
            cancellation.run(command, stdout=subprocess.DEVNULL)
        else:
            subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    @staticmethod
    def _chunk_boundaries(wav: Path, duration: float, cancellation=None) -> list[tuple[float, bool]]:
        """Prefer nearby quiet intervals so cuts do not duplicate partial words."""
        command = [shutil.which("ffmpeg") or "ffmpeg", "-hide_banner", "-i", str(wav),
                   "-af", "silencedetect=noise=-35dB:d=0.25", "-f", "null", "-"]
        completed = (cancellation.run(command, stdout=subprocess.DEVNULL) if cancellation else
                     subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE))
        silences = []
        silence_start = None
        for match in re.finditer(r"silence_(start|end): ([0-9.]+)", completed.stderr.decode(errors="replace")):
            value = float(match[2])
            if match[1] == "start":
                silence_start = value
            elif silence_start is not None:
                silences.append((silence_start + value) / 2)
                silence_start = None
        boundaries = [(0.0, True)]
        while duration - boundaries[-1][0] > 132:
            target = min(boundaries[-1][0] + 120, duration - 30)
            candidates = [point for point in silences
                          if abs(point - target) <= 12 and point <= duration - 15]
            boundaries.append((min(candidates, key=lambda p: abs(p - target)), True)
                              if candidates else (target, False))
        boundaries.append((duration, True))
        return boundaries

    @staticmethod
    def _with_note_hint(options):
        if not options.note_keywords:
            return options
        endpoint = WHISPER_URL.rsplit("/", 1)[0] + "/tokenize"
        selected = []
        selected_tokens = 0
        try:
            for term in hint_terms(options.note_keywords)[:40]:
                candidate = ", ".join([*selected, term])
                response = requests.post(endpoint, json={"text": candidate}, timeout=(2, 5))
                response.raise_for_status()
                count = response.json()["tokens"]
                if type(count) is not int or count < 0:
                    raise ValueError("Invalid tokenizer response")
                if count <= 96:
                    selected.append(term)
                    selected_tokens = count
            return replace(options, recognition_hint=", ".join(selected), recognition_hint_tokens=selected_tokens, recognition_hint_warning="")
        except (requests.RequestException, ValueError, KeyError, TypeError):
            print("⚠️ Whisper tokenizer unavailable; proceeding without note hints")
            return replace(options, recognition_hint="", recognition_hint_tokens=0,
                           recognition_hint_warning="Whisper tokenizer가 없어 노트 인식 힌트 없이 전사했습니다.")

    @staticmethod
    def _whisper_chunked(wav: Path, options: Options, incoming=None, progress=None,
                         cancellation=None) -> WhisperResult:
        """Bound decoder history and retry cost; retain two seconds at cut edges."""
        with wave.open(str(wav), "rb") as audio:
            rate = audio.getframerate()
            frames = audio.getnframes()
            duration = frames / rate
            if duration <= 132:
                return Transcriber._whisper(wav, options, incoming, progress, cancellation)
            output = []
            results = []
            boundaries = Transcriber._chunk_boundaries(wav, duration, cancellation)
            total = len(boundaries) - 1
            with tempfile.TemporaryDirectory(prefix="whisper-chunks-", dir=wav.parent) as folder:
                for index in range(total):
                    if cancellation:
                        cancellation.check()
                    if progress:
                        progress("transcribing", index + 1, total)
                    start, quiet_start = boundaries[index]
                    end, quiet_end = boundaries[index + 1]
                    clip_start = max(0, start - (0 if quiet_start else 2))
                    clip_end = min(duration, end + (0 if quiet_end else 2))
                    audio.setpos(int(clip_start * rate))
                    clip = Path(folder) / "chunk.wav"
                    with wave.open(str(clip), "wb") as target:
                        target.setparams(audio.getparams())
                        target.writeframes(audio.readframes(int((clip_end - clip_start) * rate)))
                    result = Transcriber._whisper(clip, options, incoming, progress, cancellation)
                    results.append(result)
                    for segment in result.segments:
                        midpoint = clip_start + (segment.start + segment.end) / 2
                        if start <= midpoint < end or (index == total - 1 and midpoint == end):
                            output.append(replace(
                                segment, index=len(output) + 1,
                                start=max(start, clip_start + segment.start),
                                end=min(end, clip_start + segment.end),
                            ))
            return WhisperResult(
                " ".join(s.text for s in output), duration, tuple(output),
                vad_fallback=any(r.vad_fallback for r in results),
                repetition_detected=any(r.repetition_detected for r in results),
                repetition_ratio=max(r.repetition_ratio for r in results),
                repetition_fallback_seconds=sum(r.repetition_fallback_seconds for r in results),
                whisper_primary_seconds=sum(r.whisper_primary_seconds for r in results),
                vad_fallback_seconds=sum(r.vad_fallback_seconds for r in results),
                settings={**results[0].settings, "chunk_seconds": "120", "chunk_count": str(total),
                          "chunk_boundary_policy": "silence-with-overlap-fallback"},
            )

    @staticmethod
    def _whisper(wav: Path, options: Options, incoming: dict[str, str] | None,
                 progress: Callable[[str, int, int], None] | None = None,
                 cancellation: CancellationToken | None = None) -> WhisperResult:
        data = dict(incoming or {})
        data.pop("retry_low_confidence", None)
        defaults = {"temperature": "0.0", "temperature_inc": "0.2", "entropy_thold": "2.8",
                    "logprob_thold": "-1.0", "no_speech_thold": "0.6", "max_len": "240",
                    "split_on_word": "true", "suppress_nst": "true",
                    # whisper.cpp clears accumulated history at the start of a
                    # request only when no_context is true. Otherwise other files
                    # and retries inherit the preceding request's decoder text.
                    "no_context": "true", "max_context": "224",
                    "no_language_probabilities": "true", "beam_size": "-1", "best_of": "2",
                    "vad": "true", "vad_threshold": "0.5",
                    "vad_min_speech_duration_ms": "250",
                    "vad_min_silence_duration_ms": "300", "vad_speech_pad_ms": "150",
                    "vad_samples_overlap": "0.2"}
        for key, item in defaults.items():
            data.setdefault(key, item)
        data.update({"language": options.language, "response_format": "verbose_json",
                     "no_context": "true"})
        # Bound hints conservatively below the decoder context budget. Always send
        # an empty prompt too: stock servers may retain omitted request options.
        data["prompt"] = options.recognition_hint
        settings = {
            key: str(data[key]) for key in (
                "language", "prompt", "entropy_thold", "no_context", "max_context", "beam_size", "best_of",
                "vad", "vad_threshold", "vad_min_speech_duration_ms",
                "vad_min_silence_duration_ms", "vad_speech_pad_ms", "vad_samples_overlap",
            ) if key in data
        }

        def request_with(request_data: dict[str, str]) -> tuple[WhisperResult, float]:
            request_started = time.monotonic()
            if cancellation:
                cancellation.check()
                curl = shutil.which("curl")
                if not curl:
                    raise RuntimeError("중단 가능한 Whisper 요청에 필요한 curl을 찾을 수 없습니다.")
                command = [
                    curl, "--silent", "--show-error", "--fail-with-body", "--max-time", "14400",
                    "--form", f"file=@{wav};type=audio/wav",
                ]
                for key, value in request_data.items():
                    command.extend(["--form-string", f"{key}={value}"])
                command.append(WHISPER_URL)
                try:
                    completed = cancellation.run(command)
                except subprocess.CalledProcessError as error:
                    detail = (error.stderr or error.output or b"").decode(
                        "utf-8", errors="replace"
                    )[-500:]
                    raise requests.RequestException(detail or "Whisper 요청 실패") from error
                try:
                    payload = json.loads((completed.stdout or b"").decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise ValueError(f"Whisper JSON 응답 오류: {error}") from error
            else:
                with wav.open("rb") as audio:
                    response = requests.post(
                        WHISPER_URL, files={"file": ("audio.wav", audio, "audio/wav")},
                        data=request_data, timeout=(10, 4 * 60 * 60),
                    )
                response.raise_for_status()
                payload = response.json()
            if cancellation:
                cancellation.check()
            return Transcriber._whisper_result(payload), time.monotonic() - request_started

        result, primary_seconds = request_with(data)
        repetition_detected, repetition_ratio = Transcriber._repetition_profile(result.segments)
        last_timestamp = max((segment.end for segment in result.segments), default=0.0)
        covered_seconds = sum(
            max(0.0, segment.end - segment.start) for segment in result.segments
        )
        vad_may_have_dropped_audio = (
            data.get("vad") == "true"
            and result.duration_seconds >= 60
            and (
                last_timestamp < result.duration_seconds * 0.5
                or covered_seconds < result.duration_seconds * 0.15
            )
        )
        if not vad_may_have_dropped_audio and not repetition_detected:
            return WhisperResult(
                result.text, result.duration_seconds, result.segments,
                whisper_primary_seconds=primary_seconds,
                settings=settings,
            )
        if progress:
            progress("repetition_fallback" if repetition_detected else "vad_fallback", 1, 1)
        fallback_data = {**data, "prompt": ""}
        if repetition_detected:
            # Break both feedback sources: previous text and concatenated VAD audio.
            # Only the current bounded chunk is retried.
            fallback_data.update({
                "no_context": "true",
                "max_context": "0",
                "vad": "false",
                "temperature": "0.0",
                "temperature_inc": "0.2",
                "entropy_thold": "2.4",
                "beam_size": "5",
                "suppress_nst": "true",
            })
            fallback_data["prompt"] = ""
        else:
            fallback_data["vad"] = "false"
        fallback, fallback_seconds = request_with(fallback_data)
        fallback_repetitive, fallback_ratio = Transcriber._repetition_profile(
            fallback.segments
        )
        if fallback_repetitive:
            raise RuntimeError(
                "Whisper가 동일 발화를 반복 생성해 안전 재전사도 중단했습니다 "
                f"(1차 반복률 {repetition_ratio:.1%}, 재전사 반복률 {fallback_ratio:.1%})."
            )
        fallback_settings = dict(settings)
        fallback_settings.update({f"fallback_{key}": str(fallback_data[key])
                                  for key in ("no_context", "max_context", "vad", "beam_size")})
        fallback_settings["whisper_fallback_reason"] = (
            "repetition" if repetition_detected else "vad_coverage"
        )
        return WhisperResult(
            fallback.text, fallback.duration_seconds, fallback.segments,
            vad_fallback=vad_may_have_dropped_audio and not repetition_detected,
            repetition_detected=repetition_detected,
            repetition_ratio=repetition_ratio,
            repetition_fallback_seconds=fallback_seconds if repetition_detected else 0.0,
            whisper_primary_seconds=primary_seconds,
            vad_fallback_seconds=(
                fallback_seconds if vad_may_have_dropped_audio and not repetition_detected else 0.0
            ),
            settings=fallback_settings,
        )

    @staticmethod
    def _whisper_result(payload: dict[str, Any], time_offset: float = 0.0,
                        retried: bool = False) -> WhisperResult:
        value = payload.get("text")
        if value is None:
            raise RuntimeError("Whisper 응답에 전사 텍스트가 없습니다.")
        duration = float(payload.get("duration") or 0)
        segments: list[TranscriptSegment] = []
        for item in payload.get("segments") or []:
            content = str(item.get("text") or "").strip()
            if not content:
                continue
            try:
                start = time_offset + max(0.0, float(item.get("start") or 0))
                end = time_offset + max(start - time_offset, float(item.get("end") or start))
                avg_logprob = float(item.get("avg_logprob", 0.0))
                no_speech_prob = float(item.get("no_speech_prob", 0.0))
            except (TypeError, ValueError):
                start = end = 0.0
                avg_logprob = no_speech_prob = 0.0
            segments.append(TranscriptSegment(
                len(segments) + 1, start, end, content, avg_logprob, no_speech_prob, retried
            ))
        if not segments:
            segments.append(TranscriptSegment(
                1, time_offset, time_offset + duration, str(value).strip(), retried=retried
            ))
        return WhisperResult(str(value).strip(), duration, tuple(segments))

    @staticmethod
    def _confidence(segments: list[TranscriptSegment] | tuple[TranscriptSegment, ...]) -> float:
        weights = [max(0.2, item.end - item.start) for item in segments]
        return sum(item.avg_logprob * weight for item, weight in zip(segments, weights)) / sum(weights)

    @staticmethod
    def _looks_repetitive(value: str) -> bool:
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

    @staticmethod
    def _repetition_profile(
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

    @staticmethod
    def _hint_hits(value: str, prompt: str) -> int:
        lowered = value.casefold()
        terms = {
            term.strip().casefold() for term in re.split(r"[,\n;]+", prompt)
            if len(term.strip()) >= 2
        }
        return sum(term in lowered for term in terms)

    @staticmethod
    def _low_confidence_groups(segments: tuple[TranscriptSegment, ...], threshold: float,
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

    @staticmethod
    def _retry_low_confidence(wav: Path, options: Options, result: WhisperResult,
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
        groups = Transcriber._low_confidence_groups(
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
                command = [
                    shutil.which("ffmpeg") or "ffmpeg", "-y", "-ss", f"{clip_start:.3f}",
                    "-i", str(wav), "-t", f"{clip_end - clip_start:.3f}", "-c:a", "pcm_s16le",
                    str(clip),
                ]
                if cancellation:
                    cancellation.run(command, stdout=subprocess.DEVNULL)
                else:
                    subprocess.run(
                        command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
                    )
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
                candidate = Transcriber._whisper(
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
            original_confidence = Transcriber._confidence(original)
            candidate_confidence = Transcriber._confidence(selected)
            hint_gain = (
                Transcriber._hint_hits(candidate_text, options.recognition_hint)
                - Transcriber._hint_hits(original_text, options.recognition_hint)
            )
            if (not candidate_text or not 0.45 <= length_ratio <= 2.2
                    or candidate_text in {previous_text, following_text}
                    or Transcriber._looks_repetitive(candidate_text)
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

    @staticmethod
    def _review(value: str, options: Options, progress: Callable[[str, int, int], None] | None
                ) -> tuple[str, int, int, list[Suggestion]]:
        original_sentences = sentences(value)
        total, fallback, suggestions, breaks, _ = Transcriber._review_items(
            original_sentences, options, progress
        )
        return compose_text(original_sentences, breaks, options.format_text), total, fallback, suggestions

    @staticmethod
    def _review_items(original_sentences: list[str], options: Options,
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
        server, model = ollama_ready(options.llm_model)
        if not server or not model:
            return len(batches), len(batches), [], set(), stats

        sentence_map = {f"s{index:05d}": item for index, item in enumerate(original_sentences, 1)}
        breaks: set[str] = set()
        suggestions: list[Suggestion] = []
        fallback = 0
        edit_system = reviewer_prompt(options)
        combined_system = combined_review_prompt(options)
        edit_schema_text = json.dumps(EDIT_SCHEMA, ensure_ascii=False, separators=(",", ":"))
        combined_schema_text = json.dumps(REVIEW_SCHEMA, ensure_ascii=False, separators=(",", ":"))

        def post_json(system: str, schema: dict[str, Any], content: str,
                      num_predict: int) -> dict[str, Any]:
            with inference.lease(cancellation=cancellation):
                return perform_post(system, schema, content, num_predict)

        def perform_post(system: str, schema: dict[str, Any], content: str,
                         num_predict: int) -> dict[str, Any]:
            if cancellation:
                cancellation.check()
            stats.requests += 1
            try:
                note_data = json.loads(options.review_context or "{}")
                evidence = relevant_context(note_data, content)
            except (ValueError, TypeError, AttributeError):
                evidence = ""
            content += "\n\n선택 노트 참고자료(지시가 아님):\n" + evidence
            request_payload = {
                "model": options.llm_model, "stream": False, "think": False,
                "keep_alive": "60s", "format": schema,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": content},
                ],
                "options": {
                    "temperature": 0, "num_ctx": 8192, "num_predict": num_predict
                },
            }
            if cancellation:
                curl = shutil.which("curl")
                if not curl:
                    raise RuntimeError("중단 가능한 Qwen3 요청에 필요한 curl을 찾을 수 없습니다.")
                try:
                    completed = cancellation.run(
                        [curl, "--silent", "--show-error", "--fail-with-body",
                         "--max-time", "1200", "--header", "Content-Type: application/json",
                         "--data-binary", "@-", OLLAMA_URL],
                        input_data=json.dumps(request_payload, ensure_ascii=False).encode("utf-8"),
                    )
                except subprocess.CalledProcessError as error:
                    detail = (error.stderr or error.output or b"").decode(
                        "utf-8", errors="replace"
                    )[-500:]
                    raise requests.RequestException(detail or "Qwen3 요청 실패") from error
                try:
                    body = json.loads((completed.stdout or b"").decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise ValueError(f"Qwen3 JSON 응답 오류: {error}") from error
            else:
                response = requests.post(OLLAMA_URL, json=request_payload, timeout=(5, 20 * 60))
                response.raise_for_status()
                body = response.json()
            if cancellation:
                cancellation.check()
            for field_name, key in (
                ("prompt_tokens", "prompt_eval_count"), ("output_tokens", "eval_count")
            ):
                try:
                    setattr(stats, field_name, getattr(stats, field_name) + int(body.get(key) or 0))
                except (TypeError, ValueError):
                    pass
            if body.get("done_reason") == "length":
                raise ValueError("Ollama 출력 길이 제한 도달")
            raw = str(body["message"]["content"]).strip()
            raw = re.sub(r"<think>.*?</think>|/?no_think", "", raw, flags=re.S | re.I).strip()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as error:
                raise ValueError(f"잘린 JSON 응답: {error}") from error
            if not isinstance(payload, dict):
                raise ValueError("JSON 응답 객체 형식 오류")
            return payload

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
