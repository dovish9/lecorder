#!/usr/bin/env python3
"""Run actual Whisper on a sample, keeping source and application data untouched.

Example: WHISPER_PORT=18080 python3 scripts/verify_transcription.py sample.mp4 --language en
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from web.backend.engine import Transcriber
from web.backend.transcription import CancellationToken, Options
from web.backend.whisper_runtime import whisper_runtime


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--language", choices=("ko", "en", "auto"), default="auto")
    parser.add_argument("--report", type=Path, help="Write timings, transcript and segments as JSON")
    parser.add_argument("--llm", action="store_true", help="Also run the configured Qwen review")
    args = parser.parse_args()
    if not args.source.is_file():
        parser.error(f"Audio file not found: {args.source}")
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="lecorder-verification-") as temporary:
            source = Path(temporary) / ("source" + args.source.suffix)
            shutil.copy2(args.source, source)
            result = Transcriber().transcribe(
                source, Options(language=args.language, use_llm=args.llm),
                progress=lambda stage, index, total: print(f"{stage} {index}/{total}", flush=True),
                cancellation=CancellationToken(),
            )
            assert result.text.strip(), "Empty transcript; inspect whether the sample contains speech"
            assert all(0 <= s.start <= s.end <= result.duration_seconds + .1 for s in result.segments)
            assert all(a.start <= b.start for a, b in zip(result.segments, result.segments[1:]))
            report = {**asdict(result), "source": str(args.source.resolve()),
                      "elapsed_seconds": time.monotonic() - started}
            if args.report:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"Audio: {result.duration_seconds:.1f}s; elapsed: {report['elapsed_seconds']:.1f}s")
            print(result.text)
    finally:
        whisper_runtime.close()


if __name__ == "__main__":
    main()
