from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wsl_devctl.config import parse_project
from wsl_devctl.dependencies import dependency_plan

from .helpers import project_dict


class DependencyTests(unittest.TestCase):
    def test_node_project_plans_apt_and_corepack(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            raw = project_dict(Path(temporary))
            raw["toolchain"] = {"node": True, "package_manager": "pnpm"}
            project = parse_project(raw)

            def available(command: str):
                return None if command in {"node", "corepack"} else f"/usr/bin/{command}"

            with patch("wsl_devctl.dependencies.shutil.which", side_effect=available):
                plan = dependency_plan(project)
            self.assertIn("nodejs", plan.apt_packages)
            self.assertIn("npm", plan.apt_packages)
            self.assertTrue(plan.install_corepack)


if __name__ == "__main__":
    unittest.main()
