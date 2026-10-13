"""OCR adapters return text, normalized top-left boxes and confidence per line."""
import csv
import io
import json
import os
import shutil
import struct
from pathlib import Path
from typing import Protocol
from .config import SCRIPT_ROOT
from .platforms import host


class OCR(Protocol):
    def recognize(self, image: Path, cancellation) -> list[dict]: ...


class NativeOCR:
    def __init__(self, platform=None, engine=None):
        self.platform, self.engine = platform or host, engine

    def recognize(self, image, cancellation):
        return recognize(image, cancellation, self.engine, self.platform)


def binary(name):
    found = shutil.which(name)
    if not found:
        raise ValueError(f"{name} 도구가 없습니다. 설치 진단을 확인하세요.")
    return found


def run(command, token):
    return token.run(command, timeout=180).stdout.decode("utf-8", errors="replace")


def recognize(image, token, engine=None, platform=None):
    platform = platform or host
    engine = engine or os.getenv("LECORDER_OCR_ENGINE", "vision")
    if engine == "vision":
        if not platform.capabilities()["vision_ocr"]:
            raise ValueError("이 운영체제에서는 Vision OCR을 지원하지 않습니다. OCR 어댑터를 구성하세요.")
        helper = SCRIPT_ROOT / "dependencies/vision/bin/vision-ocr"
        if not helper.is_file():
            raise ValueError(
                "Vision OCR 도구가 없습니다. scripts/setup.sh를 실행하세요."
            )
        return json.loads(run([str(helper), str(image)], token))
    if engine != "tesseract":
        raise ValueError("지원하지 않는 OCR 엔진입니다.")
    data = run(
        [binary("tesseract"), str(image), "stdout", "-l", "kor+eng", "tsv"], token
    )
    with image.open("rb") as stream:
        stream.seek(16)
        width, height = struct.unpack(">II", stream.read(8))
    grouped = {}
    # Tesseract TSV contains literal quotes, not CSV-quoted fields.
    for row in csv.DictReader(io.StringIO(data), delimiter="\t", quoting=csv.QUOTE_NONE):
        if not row.get("text", "").strip():
            continue
        key = tuple(row.get(k) for k in ("block_num", "par_num", "line_num"))
        item = grouped.setdefault(key, {"text": "", "confidence": 1.0, "box": []})
        x, y, w, h = (int(row[k]) for k in ("left", "top", "width", "height"))
        box = [x / width, y / height, (x + w) / width, (y + h) / height]
        if not item["box"]:
            item["box"] = box
        else:
            old = item["box"]
            item["box"] = [
                min(old[0], box[0]),
                min(old[1], box[1]),
                max(old[2], box[2]),
                max(old[3], box[3]),
            ]
        item["text"] += " " + row["text"]
        item["confidence"] = min(
            item["confidence"], max(0, float(row.get("conf", 0))) / 100
        )
    for item in grouped.values():
        x, y, right, bottom = item["box"]
        item["box"] = [x, y, right - x, bottom - y]
    return list(grouped.values())
