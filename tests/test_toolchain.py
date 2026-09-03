from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wsl_devctl.config import parse_project
from wsl_devctl.toolchain import missing_mise_tools, required_tools, run_project

from .helpers import project_dict


class ToolchainTests(unittest.TestCase):
    def test_missing_tools_are_limited_to_project_requirements(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            raw = project_dict(Path(temporary))
            raw["toolchain"] = {
                "provider": "mise",
                "node": True,
                "package_manager": "pnpm",
            }
            project = parse_project(raw)
            inventory = {
                "node": [{"installed": False, "version": "22.1.0"}],
                "pnpm": [{"installed": True, "version": "10.0.0"}],
                "python": [{"installed": False, "version": "3.14.0"}],
            }

            with patch("wsl_devctl.toolchain.mise_inventory", return_value=inventory):
                missing = missing_mise_tools(project)

            self.assertEqual(missing, ("node@22.1.0",))

    def test_npm_is_supplied_by_the_declared_node_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            raw = project_dict(Path(temporary))
            raw["toolchain"] = {
                "provider": "mise",
                "node": True,
                "package_manager": "npm",
            }
            project = parse_project(raw)

            self.assertEqual(required_tools(project), ("node",))

    def test_mise_wraps_project_command_without_auto_install(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            raw = project_dict(Path(temporary))
            raw["toolchain"] = {"provider": "mise", "node": True}
            project = parse_project(raw)

            completed = subprocess.CompletedProcess([], 0, "", "")
            with (
                patch("wsl_devctl.toolchain._mise_executable", return_value="/usr/bin/mise"),
                patch("wsl_devctl.toolchain.run_as_user", return_value=completed) as invoke,
            ):
                run_project(
                    project,
                    ["node", "--version"],
                    cwd=project.source,
                    capture=True,
                )

            args, kwargs = invoke.call_args
            self.assertEqual(args[1], ["/usr/bin/mise", "exec", "--", "node", "--version"])
            self.assertEqual(kwargs["env"]["MISE_AUTO_INSTALL"], "false")
            self.assertEqual(kwargs["env"]["MISE_EXEC_AUTO_INSTALL"], "false")
            self.assertEqual(
                kwargs["env"]["MISE_GLOBAL_CONFIG_FILE"],
                "/etc/wsl-devctl/project-isolation.toml",
            )
            self.assertEqual(kwargs["env"]["MISE_NOT_FOUND_SYSTEM_FALLBACK"], "false")


if __name__ == "__main__":
    unittest.main()
