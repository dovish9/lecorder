import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageChops
from web.backend.note_extract import document_request
from web.backend.transcription import CancellationToken


def sample_pdf(path, rotation=0, crop=False):
    stream = b'BT /F1 18 Tf 60 120 Td (Hello world) Tj ET'
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        (f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] '
         f'/Rotate {rotation} ' + ('/CropBox [30 40 270 180] ' if crop else '') +
         '/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>').encode(),
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream',
    ]
    data = b'%PDF-1.4\n'; offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data)); data += f'{i} 0 obj\n'.encode() + obj + b'\nendobj\n'
    xref = len(data)
    data += b'xref\n0 6\n0000000000 65535 f \n'
    data += b''.join(f'{offset:010d} 00000 n \n'.encode() for offset in offsets[1:])
    data += f'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()
    path.write_bytes(data)


class PDFTextTests(unittest.TestCase):
    def test_text_boxes_match_rendered_crop_and_rotation_without_os_tools(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'PATH': ''}):
            root = Path(folder)
            for rotation in (0, 90, 180, 270):
                for crop in (False, True):
                    with self.subTest(rotation=rotation, crop=crop):
                        source, image = root/'sample.pdf', root/'page.png'
                        sample_pdf(source, rotation, crop)
                        token = CancellationToken()
                        data = document_request(token, operation='pdf_text', source=str(source), number=1)
                        self.assertEqual(len(data['embedded_boxes']), 2)
                        self.assertEqual(len(data['embedded'].replace(' ', '').replace('\n', '')), 10)
                        document_request(token, operation='pdf', source=str(source), number=1, destination=str(image))
                        with Image.open(image) as rendered:
                            ink = ImageChops.invert(rendered.convert('L')).point(lambda p: 255 if p > 100 else 0).getbbox()
                            boxes = data['embedded_boxes']
                            left = min(w['box'][0] for w in boxes) * rendered.width
                            top = min(w['box'][1] for w in boxes) * rendered.height
                            right = max(w['box'][0]+w['box'][2] for w in boxes) * rendered.width
                            bottom = max(w['box'][1]+w['box'][3] for w in boxes) * rendered.height
                            self.assertLessEqual(left, ink[0]+2)
                            self.assertLessEqual(top, ink[1]+2)
                            self.assertGreaterEqual(right, ink[2]-2)
                            self.assertGreaterEqual(bottom, ink[3]-2)
                            self.assertLess(max(ink[0]-left, ink[1]-top, right-ink[2], bottom-ink[3]), 45)

    def test_image_only_pdf_returns_empty_embedded_text(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder)/'scan.pdf'
            Image.new('RGB', (200, 300), 'white').save(source, 'PDF')
            result = document_request(CancellationToken(), operation='pdf_text', source=str(source), number=1)
            self.assertEqual(result, {'embedded': '', 'embedded_boxes': []})
