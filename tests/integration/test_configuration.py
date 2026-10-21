from __future__ import annotations

import tempfile
from pathlib import Path

from web.backend.config import EnvironmentStore
from web.backend.store import LectureStore


from tests.support import RecorderCase

class ConfigurationTests(RecorderCase):
    def test_courses_hints_and_output_path_are_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, output = self.environment(root)
            store = LectureStore(root / "web/data/lecorder.db", environment)
            course = store.create_course("통계학")
            store.update_course(course.id, prompt="population, sample", corrections="표정=표본")
            active = LectureStore(store.path, environment).get()
            self.assertEqual(active.prompt, "")
            self.assertEqual(active.corrections, "")
            self.assertEqual(Path(active.output_dir).resolve(), output.resolve())


    def test_environment_resolves_relative_paths_from_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "lecorder"
            project.mkdir()
            environment_file = project / ".env"
            environment_file.write_text(
                "LECORDER_DIR=.\n"
                "WHISPER_CPP_DIR=../whisper.cpp\n"
                "LECORDER_OUTPUT_DIR=output\n",
                encoding="utf-8",
            )

            environment = EnvironmentStore(environment_file, project).get()

            self.assertEqual(Path(environment.project_dir), project.resolve())
            self.assertEqual(Path(environment.whisper_cpp_dir), (project / "dependencies/whisper.cpp").resolve())
            self.assertEqual(Path(environment.output_dir), (project / "output").resolve())


    def test_environment_update_removes_legacy_whisper_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment, output = self.environment(Path(temporary))
            with environment.path.open("a") as target:
                target.write("WHISPER_CPP_DIR=/obsolete/location\n")
            result = environment.update(output_dir=str(output))
            self.assertNotIn("WHISPER_CPP_DIR", environment.path.read_text())
            self.assertEqual(Path(result.whisper_cpp_dir), Path(result.project_dir) / "dependencies/whisper.cpp")


    def test_path_update_preserves_advanced_environment_options(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, output = self.environment(root)
            with environment.path.open("a", encoding="utf-8") as target:
                target.write(
                    "WHISPER_RETRY_MAX_GROUPS=6\n"
                    "WHISPER_LOW_CONFIDENCE=-0.7\n"
                    "OLLAMA_MODEL=custom-review:latest\n"
                )
            environment.update(output_dir=str(output))
            saved = environment.path.read_text(encoding="utf-8")
            self.assertIn("WHISPER_RETRY_MAX_GROUPS=6", saved)
            self.assertIn("WHISPER_LOW_CONFIDENCE=-0.7", saved)
            self.assertNotIn("OLLAMA_MODEL=", saved)

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
