import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from web.backend.notes import NoteLibrary
from tests import support as fixtures
from web.backend.store import LectureStore


class NoteCase(unittest.TestCase):
    def setUp(self):
        for module in ('pipeline', 'review_jobs', 'notes', 'note_study', 'note_intake'):
            availability = patch(f"web.backend.{module}.require_llm", return_value=None)
            availability.start()
            self.addCleanup(availability.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.env, self.output = fixtures.RecorderCase.environment(self.root)
        self.store = LectureStore(self.root / "test.db", self.env)
        self.notes = NoteLibrary(self.store, self.root / "notes", False)


    def tearDown(self):
        self.notes.close()
        self.temporary.cleanup()


    def register(self):
        return self.notes.register(
            self.store.active_course_id(),
            [("slides.pdf", io.BytesIO(b"%PDF-1.7\nfixture"))],
        )


    def ready(self):
        note = self.register()
        page = {
            "number": 1,
            "text": "Electric field and Coulomb force",
            "image": "page-1.png",
            "error": "",
            "analysis": None,
        }
        self.notes._page(note["revision_id"], page)
        self.notes._update(
            note["revision_id"],
            status="ready",
            study_status="completed",
            page_count=1,
            keywords=json.dumps(
                [
                    {
                        "id": "k1",
                        "term": "Coulomb",
                        "pages": [1],
                        "score": 1,
                        "included": True,
                    }
                ]
            ),
        )
        return self.notes.get(note["id"])
