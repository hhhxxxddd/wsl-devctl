from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from wsl_devctl.config import parse_project
from wsl_devctl.drivers.compose import compose_command, compose_project_name
from wsl_devctl.errors import DevctlError

from .helpers import project_dict


class ComposeTests(unittest.TestCase):
    def test_command_uses_bounded_files_and_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = project_dict(root)
            raw["runtime"] = {"driver": "compose"}
            raw["docker"] = {
                "compose": {
                    "workdir": ".",
                    "files": ["compose.yaml", "compose.dev.yaml"],
                    "profiles": ["dev"],
                }
            }
            project = parse_project(raw)
            command = compose_command(project, "up")
            self.assertEqual(command[:3], ["docker", "compose", "--project-name"])
            self.assertIn(str(project.cache / "compose.dev.yaml"), command)
            self.assertEqual(command[-3:], ["--profile", "dev", "up"])
            self.assertTrue(compose_project_name(project).startswith("wsl-dev-"))

    def test_compose_file_cannot_escape_workdir(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = project_dict(root)
            raw["runtime"] = {"driver": "compose"}
            raw["docker"] = {"compose": {"files": ["../outside.yaml"]}}
            with self.assertRaisesRegex(DevctlError, "files entry"):
                parse_project(raw)


if __name__ == "__main__":
    unittest.main()
