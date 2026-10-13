"""Isolated PDFium/Pillow worker: no platform command-line tools required.

PDFium is not thread safe. Each request gets its own cancellable process rather
than sharing PDFium handles between application worker threads.
"""
from contextlib import closing
import json
import sys


def pdf_page(source, number=None, destination=None):
    import pypdfium2 as pdfium
    with pdfium.PdfDocument(source) as document:
        if number is None:
            return {"pages": len(document)}
        document.init_forms()
        with closing(document[int(number) - 1]) as page:
            width, height = page.get_size()
            if destination:
                bitmap = page.render(scale=2200 / max(width, height))
                try:
                    bitmap.to_pil().save(destination, format="PNG")
                finally:
                    bitmap.close()
            return {}


def pdf_text(source, number):
    import pdfplumber
    with pdfplumber.open(source) as document:
        page = document.pages[int(number) - 1]
        # PDFium renders the crop box; normalize text to that same visible area.
        crop = page.cropbox
        # pdfplumber normalizes box axes for rotation but does not reflect the
        # crop offset for 180/270 degrees, unlike the rendered page.
        if page.rotation in (180, 270):
            left, top, right, bottom = page.bbox
            crop = (left + right - crop[2], top + bottom - crop[3],
                    left + right - crop[0], top + bottom - crop[1])
        crop = (max(page.bbox[0], crop[0]), max(page.bbox[1], crop[1]),
                min(page.bbox[2], crop[2]), min(page.bbox[3], crop[3]))
        if crop != page.bbox:
            page = page.crop(crop)
        x0, y0, _, _ = page.bbox
        words = []
        for word in page.extract_words():
            words.append({
                "text": word["text"],
                "box": [(word["x0"] - x0) / page.width,
                        (word["top"] - y0) / page.height,
                        (word["x1"] - word["x0"]) / page.width,
                        (word["bottom"] - word["top"]) / page.height],
            })
        return {"embedded": page.extract_text() or "", "embedded_boxes": words}


def image_page(source, destination):
    from PIL import Image, ImageOps
    from pillow_heif import register_heif_opener
    register_heif_opener()
    with Image.open(source) as original:
        image = ImageOps.exif_transpose(original)
        image.thumbnail((2200, 2200), Image.Resampling.LANCZOS)
        image.convert("RGB").save(destination, format="PNG")
    return {}


if __name__ == "__main__":
    request = json.load(sys.stdin)
    operation = request.pop("operation")
    functions = {"pdf": pdf_page, "image": image_page, "pdf_text": pdf_text}
    if operation not in functions:
        raise ValueError("Unknown document operation")
    result = functions[operation](**request)
    print(json.dumps(result, ensure_ascii=False))
