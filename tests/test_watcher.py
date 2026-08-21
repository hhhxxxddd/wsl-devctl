from __future__ import annotations

import unittest
from pathlib import Path

from wsl_devctl.watcher import ChangeKind, FileStamp, classify_changes


class WatcherTests(unittest.TestCase):
    def test_java_edit_is_source_change(self) -> None:
        previous = {"/p/A.java": FileStamp(1, 10)}
        current = {"/p/A.java": FileStamp(2, 11)}
        kind, _ = classify_changes(previous, current, {}, {}, {".xml"}) or (None, set())
        self.assertEqual(kind, ChangeKind.SOURCE)

    def test_resource_edit_requires_repository_build(self) -> None:
        previous = {"/p/Mapper.xml": FileStamp(1, 10)}
        current = {"/p/Mapper.xml": FileStamp(2, 12)}
        kind, _ = classify_changes(previous, current, {}, {}, {".xml"}) or (None, set())
        self.assertEqual(kind, ChangeKind.RESOURCE)

    def test_deleted_java_file_is_structural(self) -> None:
        previous = {"/p/Old.java": FileStamp(1, 10)}
        kind, changed = classify_changes(previous, {}, {}, {}, {".xml"}) or (None, set())
        self.assertEqual(kind, ChangeKind.STRUCTURAL)
        self.assertIn(Path("/p/Old.java"), changed)

    def test_pom_change_is_structural(self) -> None:
        old = {"/p/pom.xml": FileStamp(1, 10)}
        new = {"/p/pom.xml": FileStamp(2, 11)}
        kind, _ = classify_changes({}, {}, old, new, {".xml"}) or (None, set())
        self.assertEqual(kind, ChangeKind.STRUCTURAL)


if __name__ == "__main__":
    unittest.main()
