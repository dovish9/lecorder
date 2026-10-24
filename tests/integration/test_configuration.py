from __future__ import annotations

import tempfile
import os
from pathlib import Path
from unittest.mock import patch

from web.backend.config import EnvironmentStore
from web.backend.library.store import LectureStore


from tests.support import RecorderCase

class ConfigurationTests(RecorderCase):
    def test_unspecified_output_path_defaults_to_outputs(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {}, clear=True):
            root = Path(temporary)
            environment = EnvironmentStore(root / '.env', root).get()
            self.assertEqual(Path(environment.output_dir), (root / 'outputs').resolve())

    def test_course_language_and_output_path_are_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, output = self.environment(root)
            store = LectureStore(root / "web/data/lecorder.db", environment)
            course = store.courses.create_course("통계학")
            store.courses.update_course(course.id, language="en")
            active = LectureStore(store.path, environment).get()
            self.assertEqual(active.language, "en")
            self.assertEqual(Path(active.output_dir).resolve(), output.resolve())


    def test_environment_resolves_relative_paths_from_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "lecorder"
            project.mkdir()
            environment_file = project / ".env"
            environment_file.write_text(
                "LECORDER_DIR=.\n"
                "LECORDER_OUTPUT_DIR=output\n",
                encoding="utf-8",
            )

            environment = EnvironmentStore(environment_file, project).get()

            self.assertEqual(Path(environment.project_dir), project.resolve())
            self.assertEqual(Path(environment.whisper_cpp_dir), (project / "dependencies/whisper.cpp").resolve())
            self.assertEqual(Path(environment.output_dir), (project / "output").resolve())




    def test_path_update_preserves_advanced_environment_options(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, output = self.environment(root)
            with environment.path.open("a", encoding="utf-8") as target:
                target.write(
                    "LECORDER_AI_RETRIES=6\n"
                    "LECORDER_WHISPER_LOW_CONFIDENCE=-0.7\n"
                )
            environment.update(output_dir=str(output))
            saved = environment.path.read_text(encoding="utf-8")
            self.assertIn("LECORDER_AI_RETRIES=6", saved)
            self.assertIn("LECORDER_WHISPER_LOW_CONFIDENCE=-0.7", saved)

    def test_path_checks_do_not_require_local_models(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            (project / "web/backend").mkdir(parents=True)
            (project / "app.py").write_text("")
            output = Path(temporary) / "output"
            output.mkdir()
            store = EnvironmentStore(Path(temporary) / ".env", project)
            saved = store.update(project_dir=str(project), output_dir=str(output))
            self.assertEqual(saved.public()["checks"], {"project": True, "output": True})
            (project / "app.py").unlink()
            self.assertFalse(store.get().public()["checks"]["project"])
            with self.assertRaises(ValueError):
                store.update(project_dir=str(project), output_dir=str(output))
