import json
import runpy
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from web.backend.note_extract import extract_page
from web.backend.transcription import CancellationToken

compact = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/compact-note-images.py'))['compact']


class NoteAssetTests(unittest.TestCase):
    def test_compaction_preserves_revisions_and_distinct_renders(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            database = root / 'db'
            folder = root / 'notes'
            with sqlite3.connect(database) as db:
                db.executescript('CREATE TABLE lecture_notes(id TEXT,revision_id TEXT); CREATE TABLE note_revisions(id TEXT,note_id TEXT,status TEXT,study_status TEXT); CREATE TABLE note_pages(revision_id TEXT,number INT,payload TEXT);')
                db.execute("INSERT INTO lecture_notes VALUES ('note','new')")
                for rev, content in [('old', b'same'), ('new', b'same'), ('different', b'other')]:
                    image = folder / 'note' / rev / 'page-1.png'
                    image.parent.mkdir(parents=True)
                    image.write_bytes(content)
                    db.execute("INSERT INTO note_revisions VALUES (?, 'note','ready','completed')", (rev,))
                    db.execute('INSERT INTO note_pages VALUES (?,1,?)', (rev,json.dumps({'image':rev+'/page-1.png','analysis':{'markdown':rev}})))
            compact(database,folder,root/'backup')
            with sqlite3.connect(database) as db:
                pages = {rev:json.loads(raw) for rev,raw in db.execute('SELECT revision_id,payload FROM note_pages')}
            self.assertEqual(pages['old']['image'],pages['new']['image'])
            self.assertNotEqual(pages['new']['image'],pages['different']['image'])
            self.assertEqual(len(list(folder.rglob('*.png'))),2)
            self.assertEqual(pages['old']['analysis']['markdown'],'old')
            self.assertEqual((folder/'note'/pages['different']['image']).read_bytes(),b'other')
            compact(database,folder,root/'backup2')
            self.assertEqual(len(list(folder.rglob('*.png'))),2)

    def test_existing_render_is_reused_but_ocr_runs_again(self):
        with tempfile.TemporaryDirectory() as work:
            folder = Path(work)
            (folder/'page-1.png').write_bytes(b'existing')
            with patch('web.backend.note_extract.run') as run, patch('web.backend.note_extract.ocr',return_value=[]) as ocr:
                for _ in range(2):
                    extract_page(folder/'source.png',None,folder,1,CancellationToken())
            run.assert_not_called()
            self.assertEqual(ocr.call_count,2)
