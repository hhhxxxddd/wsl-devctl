from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from wsl_devctl.paths import RuntimePaths
from wsl_devctl.state import read_json, resource_recovery_path, save_recovery


class RecoveryStateTests(unittest.TestCase):
    def test_build_attempts_are_counted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = RuntimePaths(root / "config", root / "state", root / "units", root / "README")
            save_recovery(
                paths,
                "dev-test",
                restart_backend=True,
                status="resource-building",
            )
            save_recovery(
                paths,
                "dev-test",
                restart_backend=True,
                status="resource-failed",
                exit_code=1,
            )
            value = read_json(resource_recovery_path(paths, "dev-test"))
            self.assertIsNotNone(value)
            self.assertEqual(value["attempts"], 1)


if __name__ == "__main__":
    unittest.main()
