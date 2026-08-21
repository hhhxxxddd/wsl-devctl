from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from wsl_devctl.config import parse_project
from wsl_devctl.sync import sync_command, sync_once

from .helpers import project_dict


class SyncTests(unittest.TestCase):
    def test_sync_copies_and_deletes_inside_cache_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = project_dict(root)
            project = parse_project(raw)
            (project.source / "hello.txt").write_text("one", encoding="utf-8")
            project.cache.mkdir(parents=True)
            (project.cache / "stale.txt").write_text("stale", encoding="utf-8")
            sync_once(project)
            self.assertEqual((project.cache / "hello.txt").read_text(encoding="utf-8"), "one")
            self.assertFalse((project.cache / "stale.txt").exists())
            command = sync_command(project, itemize=False)
            self.assertEqual(command[-1], f"{project.cache}/")


if __name__ == "__main__":
    unittest.main()
