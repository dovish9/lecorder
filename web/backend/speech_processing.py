from __future__ import annotations
from .glossary import recognition_terms

from .media import request as media_request
from .inference import inference
from .execution_metrics import current_trace
from .ai_runtime import runtime_for
import tempfile
import time
import wave
import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from .ai_settings import current, configured_settings
from .ai_transport import request_json, ProviderError
from .transcription import CancellationToken, Options, TranscriptSegment, WhisperResult


def audio_identity(wav):
    from .services import current_services
    if not getattr(current_services(), 'history', None):
        return None, None
    with wav.open('rb') as source:
        value = hashlib.file_digest(source, 'sha256').hexdigest()
    with wave.open(str(wav), 'rb') as audio:
        return value, audio.getnframes() / audio.getframerate()


class SpeechProcessing:
    @classmethod
    def _convert(cls, source: Path, destination: Path,
                 cancellation: CancellationToken | None = None) -> None:
        media_request("transcode", cancellation, source=source, destination=destination)


    @classmethod
    def _chunk_boundaries(cls, wav: Path, duration: float, cancellation=None) -> list[tuple[float, bool]]:
        """Prefer nearby quiet intervals so cuts do not duplicate partial words."""
        silences = media_request("silences", cancellation, source=wav)["points"]
        boundaries = [(0.0, True)]
        while duration - boundaries[-1][0] > 132:
            target = min(boundaries[-1][0] + 120, duration - 30)
            candidates = [point for point in silences
                          if abs(point - target) <= 12 and point <= duration - 15]
            boundaries.append((min(candidates, key=lambda p: abs(p - target)), True)
                              if candidates else (target, False))
        boundaries.append((duration, True))
        return boundaries


    @classmethod
    def _with_note_hint(cls, options, cancellation=None):
        if not options.note_keywords:
            return options
        if current("speech")["provider"] == "openai":
            import tiktoken
            import base64
            from .config import ROOT
            vocab = ROOT / 'dependencies/tokenizers/multilingual.tiktoken'
            if not vocab.is_file():
                raise RuntimeError('Whisper tokenizer 설치가 필요합니다: scripts/install-tokenizer.py')
            ranks = {base64.b64decode(token): int(rank) for token, rank in (line.split() for line in vocab.read_bytes().splitlines())}
            tokenizer = tiktoken.Encoding(name='whisper-multilingual', pat_str=r"'s|'t|'re|'ve|'m|'ll|'d| ?\p{L}+| ?\p{N}+| ?[^\s\p{L}\p{N}]+|\s+(?!\S)|\s+", mergeable_ranks=ranks, special_tokens={})
            terms = []
            for term in recognition_terms(options.note_keywords):
                if len(tokenizer.encode(", ".join([*terms, term]))) <= 96:
                    terms.append(term)
            hint = ", ".join(terms)
            return replace(options, recognition_hint=hint, recognition_hint_tokens=len(tokenizer.encode(hint)))
        endpoint = current("speech")["base_url"] + "/tokenize"
        selected = []
        selected_tokens = 0
        try:
            for term in recognition_terms(options.note_keywords):
                candidate = ", ".join([*selected, term])
                config = current('speech')
                key = configured_settings().credentials.get(config) if not config.get('managed') else ''
                count = request_json(
                    endpoint, {'text': candidate},
                    headers={'Authorization': 'Bearer ' + key} if key else {},
                    cancellation=cancellation, timeout=5,
                )['tokens']
                if type(count) is not int or count < 0:
                    raise ValueError("Invalid tokenizer response")
                if count <= 96:
                    selected.append(term)
                    selected_tokens = count
            return replace(options, recognition_hint=", ".join(selected), recognition_hint_tokens=selected_tokens, recognition_hint_warning="")
        except (ProviderError, ValueError, KeyError, TypeError):
            print("⚠️ Whisper tokenizer unavailable; proceeding without note hints")
            return replace(options, recognition_hint="", recognition_hint_tokens=0,
                           recognition_hint_warning="Whisper tokenizer가 없어 노트 인식 힌트 없이 전사했습니다.")


    @classmethod
    def _whisper_chunked(cls, wav: Path, options: Options, incoming=None, progress=None,
                         cancellation=None) -> WhisperResult:
        """Bound decoder history and retry cost; retain two seconds at cut edges."""
        with wave.open(str(wav), "rb") as audio:
            rate = audio.getframerate()
            frames = audio.getnframes()
            duration = frames / rate
            if duration <= 132:
                return cls._whisper(wav, options, incoming, progress, cancellation)
            output = []
            results = []
            boundaries = cls._chunk_boundaries(wav, duration, cancellation)
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
                    result = cls._whisper(clip, options, incoming, progress, cancellation)
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


    @classmethod
    def _openai_speech(cls, wav, options, cancellation):
        from .ai_settings import settings
        from .ai_providers import api_url
        config = current('speech')
        key = configured_settings().credentials.get(config)
        if not key:
            raise RuntimeError('음성 전사 API 키가 없습니다.')
        fields = {'file': wav, 'model': config['model'], 'response_format': 'verbose_json',
                  'timestamp_granularities[]': 'segment', 'prompt': options.recognition_hint}
        if options.language != 'auto':
            fields['language'] = options.language
        started = time.monotonic()
        wait_before = current_trace()['waiting_seconds']
        from .ai_control import run
        from .request_history import request as tracked
        audio_hash, audio_seconds = audio_identity(wav)
        body = run(lambda token: tracked(lambda child: request_json(api_url(config['base_url'], '/audio/transcriptions'),
                            headers={'Authorization': 'Bearer ' + key}, multipart=fields,
                            cancellation=child, timeout=1200), config, 'speech',
                            [audio_hash, {k:v for k,v in fields.items() if k != 'file'}], token, audio_seconds), cancellation, kind='speech')
        if not isinstance(body.get('segments'), list) or (body.get('text', '').strip() and not body['segments']):
            raise ValueError('전사 API가 구간 타임스탬프를 반환하지 않았습니다.')
        for segment in body['segments']:
            if not isinstance(segment.get('start'), (int, float)) or not isinstance(segment.get('end'), (int, float)) or segment['end'] < segment['start']:
                raise ValueError('전사 API 타임스탬프 형식 오류')
        result = cls._whisper_result(body)
        segments = tuple(TranscriptSegment(
            i + 1, float(item['start']), float(item['end']), str(item['text']).strip(),
            item.get('avg_logprob'), item.get('no_speech_prob'), False,
        ) for i, item in enumerate(item for item in body['segments'] if str(item.get('text') or '').strip()))
        result = replace(result, segments=segments)
        return replace(result, whisper_primary_seconds=max(0, time.monotonic()-started-(current_trace()['waiting_seconds']-wait_before)),
                       settings={'provider': 'openai', 'model': config['model'], 'quality_retry': 'unsupported'})


    @classmethod
    def _whisper(cls, wav: Path, options: Options, incoming: dict[str, str] | None,
                 progress: Callable[[str, int, int], None] | None = None,
                 cancellation: CancellationToken | None = None) -> WhisperResult:
        if current("speech")["provider"] == "openai":
            return cls._openai_speech(wav, options, cancellation)
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
            wait_before = current_trace()['waiting_seconds']
            config = current('speech')
            from .ai_settings import configured_settings
            key = configured_settings().credentials.get(config) if not config.get('managed') else ''
            from .ai_control import run
            def request_once(token):
                with inference.lease(speech=True, cancellation=token,
                                     local=config.get('managed', False)), runtime_for(config).session(token):
                    payload = request_json(
                        config['base_url'] + '/inference',
                        headers={'Authorization': 'Bearer ' + key} if key else {},
                        multipart={'file': wav, **request_data}, cancellation=token, timeout=14400,
                    )
                    cls._whisper_result(payload)
                    return payload
            from .request_history import request as tracked
            audio_hash, audio_seconds = audio_identity(wav)
            payload = run(lambda token: tracked(request_once, config, 'speech',
                          [audio_hash, request_data], token, audio_seconds), cancellation, kind='speech')
            if cancellation:
                cancellation.check()
            return cls._whisper_result(payload), max(0, time.monotonic() - request_started - (current_trace()['waiting_seconds']-wait_before))

        result, primary_seconds = request_with(data)
        repetition_detected, repetition_ratio = cls._repetition_profile(result.segments)
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
        fallback_repetitive, fallback_ratio = cls._repetition_profile(
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


    @classmethod
    def _whisper_result(cls, payload: dict[str, Any], time_offset: float = 0.0,
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
