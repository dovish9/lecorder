"""Data structures and cancellation primitives for transcription work."""

from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass, field
from typing import Any

from .config import DEFAULT_LLM_MODEL


SUPPORTED_MEDIA = frozenset({
    ".3g2", ".3gp", ".aac", ".aif", ".aiff", ".alac", ".amr", ".caf",
    ".flac", ".m4a", ".mka", ".mov", ".mp3", ".mp4", ".ogg", ".opus",
    ".wav", ".webm", ".wma",
})


@dataclass(frozen=True)
class Options:
    language: str
    course_name: str = "강의"
    recognition_hint: str = ""
    recognition_hint_tokens: int = 0
    recognition_hint_warning: str = ""
    review_context: str = ""
    note_keywords: tuple[str, ...] = ()
    format_text: bool = True
    use_llm: bool = True
    llm_model: str = DEFAULT_LLM_MODEL
    retry_low_confidence: bool = True


@dataclass(frozen=True)
class Suggestion:
    sentence_id: str
    original: str
    replacement: str
    reason: str
    confidence: float

    def public(self) -> dict[str, Any]:
        return {
            "sentence_id": self.sentence_id,
            "original": self.original,
            "replacement": self.replacement,
            "reason": self.reason,
            "confidence": self.confidence,
        }


@dataclass
class ReviewStats:
    requests: int = 0
    split_retries: int = 0
    prompt_tokens: int = 0
    output_tokens: int = 0


class TranscriptionCancelled(RuntimeError):
    """Raised when a user stops an active transcription job."""


class CancellationToken:
    """Coordinate cancellation with the currently running child process."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.RLock()
        self._process: subprocess.Popen[bytes] | None = None

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()
        with self._lock:
            process = self._process
            if process is not None and process.poll() is None:
                process.terminate()

    def check(self) -> None:
        if self.cancelled:
            raise TranscriptionCancelled("사용자가 전사 작업을 중단했습니다.")

    def run(
        self,
        args: list[str],
        *,
        input_data: bytes | None = None,
        stdout: int | None = subprocess.PIPE,
        stderr: int | None = subprocess.PIPE,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        self.check()
        process = subprocess.Popen(
            args,
            stdin=subprocess.PIPE if input_data is not None else None,
            stdout=stdout,
            stderr=stderr,
        )
        with self._lock:
            self._process = process
            if self.cancelled and process.poll() is None:
                process.terminate()
        try:
            try:
                output, errors = process.communicate(input=input_data, timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                raise
        finally:
            with self._lock:
                if self._process is process:
                    self._process = None
        self.check()
        completed = subprocess.CompletedProcess(args, process.returncode, output, errors)
        if process.returncode:
            raise subprocess.CalledProcessError(
                process.returncode, args, output=output, stderr=errors
            )
        return completed


@dataclass(frozen=True)
class TranscriptSegment:
    index: int
    start: float
    end: float
    text: str
    avg_logprob: float = 0.0
    no_speech_prob: float = 0.0
    retried: bool = False

    @property
    def sentence_id(self) -> str:
        return f"s{self.index:05d}"

    def public(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "start": self.start,
            "end": self.end,
            "text": self.text,
            "avg_logprob": self.avg_logprob,
            "no_speech_prob": self.no_speech_prob,
            "retried": self.retried,
        }


@dataclass(frozen=True)
class WhisperResult:
    text: str
    duration_seconds: float
    segments: tuple[TranscriptSegment, ...]
    low_confidence_segments: int = 0
    retry_attempted_groups: int = 0
    retry_accepted_groups: int = 0
    retry_processing_seconds: float = 0.0
    vad_fallback: bool = False
    repetition_detected: bool = False
    repetition_ratio: float = 0.0
    repetition_fallback_seconds: float = 0.0
    whisper_primary_seconds: float = 0.0
    vad_fallback_seconds: float = 0.0
    settings: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Result:
    text: str
    llm_chunks: int = 0
    fallback_chunks: int = 0
    suggestions: tuple[Suggestion, ...] = field(default_factory=tuple)
    segments: tuple[TranscriptSegment, ...] = field(default_factory=tuple)
    paragraph_breaks: tuple[int, ...] = field(default_factory=tuple)
    duration_seconds: float = 0.0
    low_confidence_segments: int = 0
    retry_attempted_groups: int = 0
    retry_accepted_groups: int = 0
    retry_processing_seconds: float = 0.0
    lowest_avg_logprob: float | None = None
    vad_fallback: bool = False
    repetition_detected: bool = False
    repetition_ratio: float = 0.0
    repetition_fallback_seconds: float = 0.0
    conversion_seconds: float = 0.0
    whisper_primary_seconds: float = 0.0
    vad_fallback_seconds: float = 0.0
    qwen_seconds: float = 0.0
    pipeline_settings: dict[str, str] = field(default_factory=dict)
    qwen_requests: int = 0
    qwen_split_retries: int = 0
    qwen_prompt_tokens: int = 0
    qwen_output_tokens: int = 0

    @property
    def summary(self) -> str:
        if not self.llm_chunks:
            return ""
        completed = self.llm_chunks - self.fallback_chunks
        edits = len(self.suggestions)
        if not self.fallback_chunks:
            return f" · Qwen3 제안 {edits}개 검토 대기"
        return (
            f" · Qwen3 {completed}/{self.llm_chunks}구간"
            f" · 제안 {edits}개 검토 대기"
        )
