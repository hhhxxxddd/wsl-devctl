from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wsl_devctl.config import parse_project
from wsl_devctl.dependencies import dependency_plan

from .helpers import project_dict


class DependencyTests(unittest.TestCase):
    def test_root_project_does_not_require_runuser_or_docker_group(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            raw = project_dict(Path(temporary))
            raw["run_user"] = "root"
            raw["runtime"] = {"driver": "compose"}
            raw["toolchain"] = {"provider": "system", "docker": True}
            project = parse_project(raw)

            def executable(command: str):
                return None if command in {"runuser", "docker"} else f"/usr/bin/{command}"

            with patch("wsl_devctl.dependencies.shutil.which", side_effect=executable):
                plan = dependency_plan(project)

            self.assertNotIn("util-linux", plan.apt_packages)
            self.assertFalse(plan.add_docker_group)

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

    def test_mise_project_does_not_install_language_apt_packages(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            raw = project_dict(Path(temporary))
            raw["toolchain"] = {
                "provider": "mise",
                "node": True,
                "package_manager": "pnpm",
            }
            project = parse_project(raw)
            inventory = {
                "node": [{"installed": False, "requested_version": "22"}],
                "pnpm": [{"installed": False, "requested_version": "10"}],
            }

            with (
                patch("wsl_devctl.dependencies.shutil.which", return_value="/usr/bin/tool"),
                patch("wsl_devctl.dependencies.mise_inventory", return_value=inventory),
                patch(
                    "wsl_devctl.dependencies.missing_mise_tools",
                    return_value=("node@22", "pnpm@10"),
                ),
            ):
                plan = dependency_plan(project)

            self.assertNotIn("nodejs", plan.apt_packages)
            self.assertNotIn("npm", plan.apt_packages)
            self.assertFalse(plan.install_corepack)
            self.assertEqual(plan.mise_tools, ("node@22", "pnpm@10"))


if __name__ == "__main__":
    unittest.main()
