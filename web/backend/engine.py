from __future__ import annotations

import socket
import time
from pathlib import Path
from typing import Callable


from .ai_settings import current, using
from .ai_runtime import runtime_for
from .inference import inference
from .execution_metrics import current_trace
from .review import compose_text
from .transcription import CancellationToken, Options, Result, ReviewStats


def port_ready(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.35):
            return True
    except OSError:
        return False


from .speech_processing import SpeechProcessing
from .speech_quality import SpeechQuality
from .review_engine import ReviewEngine

class Transcriber(SpeechProcessing, SpeechQuality, ReviewEngine):
    @classmethod
    def review(cls, original_sentences, options, progress, confidence_map=None, cancellation=None):
        """Review existing segments without re-transcribing the source audio."""
        return cls._review_items(original_sentences, options, progress, confidence_map, cancellation)

    def transcribe(self, source, options, incoming=None, progress=None, cancellation=None):
        with using(options.ai_settings or None):
            return self._transcribe(source, options, incoming, progress, cancellation)

    def _transcribe(self, source: Path, options: Options, incoming: dict[str, str] | None = None,
                   progress: Callable[[str, int, int], None] | None = None,
                   cancellation: CancellationToken | None = None) -> Result:
        wav = source.with_name("converted-audio.wav")
        if cancellation:
            cancellation.check()
        conversion_started = time.monotonic()
        self._convert(source, wav, cancellation)
        conversion_seconds = time.monotonic() - conversion_started
        config = current("speech")
        from .ai_control import run
        def prepare(token):
            with inference.lease(speech=True, cancellation=token,
                                 local=config.get("managed", False)), runtime_for(config).session(token):
                return self._with_note_hint(options, token)
        options = run(prepare, cancellation, kind='speech_hint')
        whispered = self._whisper_chunked(wav, options, incoming, progress, cancellation)
        if options.retry_low_confidence and config["provider"] == "whisper_cpp":
            whispered = self._retry_low_confidence(
                wav, options, whispered, incoming, progress, cancellation
            )
        corrected_segments = whispered.segments
        items = [segment.text for segment in corrected_segments]
        qwen_started = time.monotonic()
        review_wait = current_trace()["waiting_seconds"]
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
        qwen_seconds = max(0, time.monotonic() - qwen_started - (current_trace()["waiting_seconds"] - review_wait)) if options.use_llm else 0.0
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
                (segment.avg_logprob for segment in whispered.segments if segment.avg_logprob is not None), default=None
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
