from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from wsl_devctl.config import parse_project, user_home
from wsl_devctl.drivers.maven import resolve_maven
from wsl_devctl.drivers.spring import artifact_jar, parse_coordinate

from .helpers import project_dict


class MavenTests(unittest.TestCase):
    def test_source_wrapper_is_mapped_to_cache_before_first_sync(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = project_dict(root)
            raw["java"] = {"maven": {"repository": "user", "executable": "auto"}}
            wrapper = root / "source/mvnw"
            wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
            project = parse_project(raw)
            runtime = resolve_maven(project)
            self.assertIsNotNone(runtime)
            self.assertEqual(runtime.executable, str(project.cache / "mvnw"))

    def test_user_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            raw = project_dict(Path(temporary))
            raw["java"] = {"maven": {"repository": "user", "executable": "mvn"}}
            project = parse_project(raw)
            runtime = resolve_maven(project)
            self.assertIsNotNone(runtime)
            assert runtime is not None
            self.assertEqual(runtime.repository, user_home(project.run_user) / ".m2/repository")
            self.assertIn(f"-Dmaven.repo.local={runtime.repository}", runtime.environment()["MAVEN_OPTS"])

    def test_project_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            raw = project_dict(Path(temporary))
            raw["java"] = {"maven": {"repository": "project"}}
            project = parse_project(raw)
            runtime = resolve_maven(project)
            assert runtime is not None
            self.assertIn(f"wsl-devctl/maven/{project.name}/repository", str(runtime.repository))

    def test_devtools_coordinate_maps_into_selected_repository(self) -> None:
        repository = Path("/tmp/repository")
        jar = artifact_jar(
            repository,
            "org.springframework.boot:spring-boot-devtools:4.1.0",
        )
        self.assertEqual(
            jar,
            repository
            / "org/springframework/boot/spring-boot-devtools/4.1.0/spring-boot-devtools-4.1.0.jar",
        )
        self.assertEqual(parse_coordinate("g:a:1"), ("g", "a", "1"))


if __name__ == "__main__":
    unittest.main()
