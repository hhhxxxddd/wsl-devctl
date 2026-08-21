from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from wsl_devctl.config import parse_project
from wsl_devctl.errors import DevctlError

from .helpers import project_dict


class ConfigTests(unittest.TestCase):
    def test_valid_cache_is_canonical_and_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = parse_project(project_dict(root))
            self.assertEqual(project.cache, (root / "cache-root/dev-test").resolve())

    def test_dot_dot_cache_escape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = project_dict(root)
            raw["cache"] = str(root / "cache-root/../escaped")
            with self.assertRaisesRegex(DevctlError, "cache must stay below"):
                parse_project(raw)

    def test_symlink_cache_escape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root / "outside"
            outside.mkdir()
            cache_root = root / "cache-root"
            cache_root.mkdir()
            link = cache_root / "link"
            link.symlink_to(outside, target_is_directory=True)
            raw = project_dict(root)
            raw["cache"] = str(link / "dev-test")
            with self.assertRaisesRegex(DevctlError, "cache must stay below"):
                parse_project(raw)

    def test_workdir_escape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            raw = project_dict(Path(temporary))
            raw["backend"] = {"enabled": True, "workdir": "../other", "port": 8080}
            with self.assertRaisesRegex(DevctlError, "backend.workdir must stay within"):
                parse_project(raw)

    def test_invalid_port_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            raw = project_dict(Path(temporary))
            raw["backend"] = {"enabled": True, "workdir": "backend", "port": 70000}
            with self.assertRaisesRegex(DevctlError, "between 1 and 65535"):
                parse_project(raw)

    def test_source_and_cache_cannot_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = project_dict(root)
            raw["cache_root"] = str(root / "source")
            raw["cache"] = str(root / "source/cache")
            with self.assertRaisesRegex(DevctlError, "must not overlap"):
                parse_project(raw)

    def test_cache_root_cannot_be_filesystem_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            raw = project_dict(Path(temporary))
            raw["cache_root"] = "/"
            raw["cache"] = "/tmp/dev-test"
            with self.assertRaisesRegex(DevctlError, "cannot be /"):
                parse_project(raw)


if __name__ == "__main__":
    unittest.main()
