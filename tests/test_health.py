from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.helpers import project_dict
from wsl_devctl.config import parse_project
from wsl_devctl.health import doctor_checks


class HealthTests(unittest.TestCase):
    def test_missing_mise_is_reported_without_running_project_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            raw = project_dict(Path(temporary))
            raw["toolchain"] = {"provider": "mise", "node": True}
            raw["checks"] = {"commands": ["node"]}
            project = parse_project(raw)

            with (
                patch("wsl_devctl.health.shutil.which", return_value=None),
                patch("wsl_devctl.health.listening_ports", return_value=set()),
                patch("wsl_devctl.health.project_command_exists") as command_exists,
            ):
                checks = doctor_checks(project)

            command_exists.assert_not_called()
            self.assertIn(
                {"ok": False, "label": "toolchain provider", "detail": "mise (unavailable)"},
                checks,
            )
            self.assertIn({"ok": False, "label": "command", "detail": "node"}, checks)


if __name__ == "__main__":
    unittest.main()
