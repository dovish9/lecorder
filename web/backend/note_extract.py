"""Bounded external document conversion and page extraction adapters."""

import csv
import io
import json
import os
import re
import shutil
import subprocess
import struct
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher
from pathlib import Path
from .config import SCRIPT_ROOT

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".heic"}
SUPPORTED_NOTES = IMAGE_EXTENSIONS | {".pdf", ".pptx"}


def binary(name):
    found = shutil.which(name)
    if not found and name == "soffice":
        candidate = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
        found = str(candidate) if candidate.is_file() else None
    if not found:
        raise ValueError(f"{name} 도구가 없습니다. 설치 진단을 확인하세요.")
    return found


def run(command, token, timeout=180):
    # CancellationToken owns only the child process started for this request.
    return token.run(command, timeout=timeout).stdout.decode("utf-8", errors="replace")


def prepare_pages(originals, folder, token):
    pages = []
    for index, source in enumerate(originals):
        token.check()
        suffix = source.suffix.lower()
        if suffix == ".pptx":
            converted = folder / f"converted-{index}"
            converted.mkdir(exist_ok=True)
            profile = folder / f"office-profile-{index}"
            run(
                [
                    binary("soffice"),
                    f"-env:UserInstallation={profile.as_uri()}",
                    "--headless",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(converted),
                    str(source),
                ],
                token,
            )
            source = converted / (source.stem + ".pdf")
            if not source.is_file():
                raise ValueError("PPTX를 PDF로 변환하지 못했습니다.")
            suffix = ".pdf"
        if suffix == ".pdf":
            info = run([binary("pdfinfo"), str(source)], token)
            match = re.search(r"^Pages:\s+(\d+)", info, re.M)
            if not match:
                raise ValueError("PDF 페이지 수를 읽지 못했습니다.")
            pages.extend((source, n) for n in range(1, int(match.group(1)) + 1))
        else:
            pages.append((source, None))
        if len(pages) > 500:
            raise ValueError("강의노트는 500페이지까지 지원합니다.")
    return pages


def ocr(image, token, engine=None):
    engine = engine or os.getenv("LECORDER_OCR_ENGINE", "vision")
    if engine == "vision":
        helper = SCRIPT_ROOT / "dependencies/vision/bin/vision-ocr"
        if not helper.is_file():
            raise ValueError(
                "Vision OCR 도구가 없습니다. scripts/setup.sh를 실행하세요."
            )
        return json.loads(run([str(helper), str(image)], token))
    data = run(
        [binary("tesseract"), str(image), "stdout", "-l", "kor+eng", "tsv"], token
    )
    with image.open("rb") as stream:
        stream.seek(16)
        width, height = struct.unpack(">II", stream.read(8))
    grouped = {}
    for row in csv.DictReader(io.StringIO(data), delimiter="\t"):
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


def merge_text(embedded, lines, embedded_boxes=None):
    source_lines = [line.strip() for line in embedded.splitlines() if line.strip()]
    merged = list(source_lines)
    for line in lines:
        text = str(line.get("text", "")).strip()
        if not text:
            continue
        normalized = re.sub(r"\W+", "", text).casefold()
        if embedded_boxes and line.get("box"):
            x, y, width, height = line["box"]
            nearby = []
            for word in embedded_boxes:
                box = word.get("box")
                if (
                    box
                    and x - 0.005 <= box[0] + box[2] / 2 <= x + width + 0.005
                    and y - 0.005 <= box[1] + box[3] / 2 <= y + height + 0.005
                ):
                    nearby.append(word["text"])
            matching = re.sub(r"\W+", "", " ".join(nearby)).casefold()
            if (
                normalized
                and matching
                and (
                    normalized in matching
                    or SequenceMatcher(None, normalized, matching).ratio() >= 0.95
                )
            ):
                continue
        if any(
            normalized in re.sub(r"\W+", "", old).casefold()
            or SequenceMatcher(None, text.casefold(), old.casefold()).ratio() >= 0.86
            for old in merged
        ):
            continue
        merged.append(text)
    return "\n".join(merged)


def extract_page(source, page, folder, number, token, engine=None):
    image = folder / f"page-{number}.png"
    embedded = ""
    embedded_boxes = []
    if page is not None:
        embedded = run(
            [
                binary("pdftotext"),
                "-f",
                str(page),
                "-l",
                str(page),
                "-layout",
                str(source),
                "-",
            ],
            token,
        )
        boxes = run(
            [
                binary("pdftotext"),
                "-f",
                str(page),
                "-l",
                str(page),
                "-bbox",
                str(source),
                "-",
            ],
            token,
        )
        try:
            document = ET.fromstring(boxes)
            page_node = next(
                (node for node in document.iter() if node.tag.endswith("page")), None
            )
            if page_node is not None:
                width, height = float(page_node.attrib["width"]), float(
                    page_node.attrib["height"]
                )
                for node in page_node.iter():
                    if node.tag.endswith("word"):
                        x1, y1, x2, y2 = (
                            float(node.attrib[key])
                            for key in ("xMin", "yMin", "xMax", "yMax")
                        )
                        embedded_boxes.append(
                            {
                                "text": node.text or "",
                                **node.attrib,
                                "box": [
                                    x1 / width,
                                    y1 / height,
                                    (x2 - x1) / width,
                                    (y2 - y1) / height,
                                ],
                            }
                        )
        except ET.ParseError:
            pass
        run(
            [
                binary("pdftoppm"),
                "-f",
                str(page),
                "-l",
                str(page),
                "-scale-to",
                "2200",
                "-singlefile",
                "-png",
                str(source),
                str(image.with_suffix("")),
            ],
            token,
        )
    else:
        run(
            [
                "/usr/bin/sips",
                "-s",
                "format",
                "png",
                "-Z",
                "2200",
                str(source),
                "--out",
                str(image),
            ],
            token,
        )
    lines = ocr(image, token, engine)
    merged = merge_text(embedded, lines, embedded_boxes)
    uncertain = (
        bool(re.search(r"[=∫∑]", merged))
        or not merged.strip()
        or any(float(line.get("confidence", 0)) < 0.72 for line in lines)
    )
    return {
        "number": number,
        "embedded": embedded,
        "embedded_boxes": embedded_boxes,
        "ocr": lines,
        "text": merged,
        "image": image.name,
        "needs_review": uncertain,
        "error": "",
        "analysis": None,
        "detail": None,
    }
