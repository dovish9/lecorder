"""Bounded external document conversion and page extraction adapters."""
from web.backend.runtime_config import get as runtime

import re
import tempfile
from difflib import SequenceMatcher
from pathlib import Path
from web.backend.processes import worker

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".heic"}
SUPPORTED_NOTES = IMAGE_EXTENSIONS | {".pdf"}


def document_request(token, **request):
    return worker.call("document", request, cancellation=token, timeout=180)


def prepare_pages(originals, folder, token):
    pages = []
    for source in originals:
        token.check()
        suffix = source.suffix.lower()
        if suffix not in SUPPORTED_NOTES:
            raise ValueError("지원하지 않는 강의노트 형식입니다. PDF 또는 이미지를 사용하세요.")
        if suffix == ".pdf":
            info = document_request(token, operation="pdf", source=str(source))
            pages.extend((source, n) for n in range(1, info["pages"] + 1))
        else:
            pages.append((source, None))
        if len(pages) > runtime('LECORDER_NOTE_MAX_PAGES'):
            raise ValueError(f"강의노트는 {runtime('LECORDER_NOTE_MAX_PAGES')}페이지까지 지원합니다.")
    return pages


def ocr(image, token, engine=None):
    from web.backend.execution_context import execution_resources
    from web.backend.ocr import NativeOCR
    services = execution_resources()
    adapter = services.ocr if services and engine is None else NativeOCR(engine=engine)
    return adapter.recognize(image, token)


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
        data = document_request(token, operation="pdf_text", source=str(source), number=page)
        embedded = data["embedded"]
        embedded_boxes = data["embedded_boxes"]
    # Publish only a complete render; reanalysis reuses the existing image.
    with tempfile.TemporaryDirectory(prefix="render-", dir=folder) as work:
        temporary = Path(work) / image.name
        if page is not None and not image.is_file():
            document_request(token, operation="pdf", source=str(source),
                             number=page, destination=str(temporary))
        elif not image.is_file():
            document_request(token, operation="image", source=str(source),
                             destination=str(temporary))
        token.check()
        if temporary.is_file():
            temporary.replace(image)
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
