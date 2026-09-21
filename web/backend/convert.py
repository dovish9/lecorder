"""Optional command-line wrapper for the dashboard's transcription pipeline."""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

from .config import LANGUAGES
from .engine import Transcriber
from .files import OutputFiles, safe_name
from .markdown import render_note
from .records import Recording
from .store import LectureStore
from .transcription import Options


def convert(path: str, title: str = "", language: str | None = None,
            use_llm: bool | None = None) -> Path:
    source = Path(path).expanduser().resolve(strict=True)
    if not source.is_file():
        raise ValueError(f"파일이 아닙니다: {source}")

    settings = LectureStore().get()
    files = OutputFiles(Path(settings.output_dir))
    selected_language = language or settings.language
    if selected_language not in LANGUAGES:
        raise ValueError(f"지원하지 않는 언어입니다: {selected_language}")
    selected_title = safe_name(title or source.stem)
    if not selected_title:
        raise ValueError("저장할 파일 이름을 만들 수 없습니다.")

    options = Options(
        language=selected_language,
        course_name=settings.course_name,
        format_text=settings.format_transcript,
        use_llm=settings.llm_enabled if use_llm is None else use_llm,
        llm_model=settings.llm_model,
    )
    print(f"🎙️ [1/3] {source.name} · 언어: {selected_language}")
    with tempfile.TemporaryDirectory(prefix="lecture-cli-") as temporary:
        working = Path(temporary) / source.name
        shutil.copy2(source, working)
        result = Transcriber().transcribe(
            working,
            options,
            progress=lambda stage, index, total: print(
                ("↻ 반복 붕괴 감지 · 안전 설정으로 전체 재전사 중"
                 if stage == "repetition_fallback" else
                 "↻ VAD 음성 누락 확인 · 전체 음성으로 재전사 중"
                 if stage == "vad_fallback" else
                 f"{'↻ 저신뢰 구간 재확인' if stage == 'retry' else '✨ [2/3] Qwen3 수정 제안 검수'} 중 ({index}/{total})")
            ),
        )

    if source.parent == files.folder:
        audio = source
        note = files.note(selected_title)
    else:
        audio, note = files.pair(selected_title, source.suffix)
        temporary_audio = files.folder / f".lecture-cli-{audio.name}"
        try:
            shutil.copy2(source, temporary_audio)
            temporary_audio.replace(audio)
        finally:
            temporary_audio.unlink(missing_ok=True)

    now = datetime.now().astimezone().isoformat(timespec="seconds")
    recording = Recording(
        id=f"cli-{uuid.uuid4().hex[:8]}", course_id=settings.course_id,
        course_name=settings.course_name, title=note.stem, source_kind="cli",
        extension=audio.suffix, status="completed", language=selected_language,
        prompt=settings.prompt, corrections=settings.corrections,
        format_transcript=settings.format_transcript,
        llm_enabled=settings.llm_enabled if use_llm is None else use_llm,
        llm_model=settings.llm_model, output_dir=settings.output_dir,
        audio_path=str(audio), note_path=str(note), duration_seconds=result.duration_seconds,
        transcript_text=result.text, created_at=now, completed_at=now,
    )
    note.write_text(
        render_note(recording, [item.public() for item in result.segments], set(result.paragraph_breaks)),
        encoding="utf-8",
    )
    print(f"📝 [3/3] 저장 완료: {note}{result.summary}")
    return note


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="기존 녹음파일을 전사해 설정된 출력 폴더에 저장합니다."
    )
    parser.add_argument("recording", help="오디오 또는 동영상 파일 경로")
    parser.add_argument("--title", default="", help="저장할 노트 이름")
    parser.add_argument("--language", choices=sorted(LANGUAGES),
                        help="기본값: 대시보드 설정")
    llm = parser.add_mutually_exclusive_group()
    llm.add_argument("--llm", dest="use_llm", action="store_true")
    llm.add_argument("--no-llm", dest="use_llm", action="store_false")
    parser.set_defaults(use_llm=None)
    return parser.parse_args()


if __name__ == "__main__":
    args = arguments()
    try:
        convert(args.recording, args.title, args.language, args.use_llm)
    except Exception as error:
        print(f"❌ {error}", file=sys.stderr)
        raise SystemExit(1) from error
