from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from wsl_devctl.config import parse_project
from wsl_devctl.controller import prepare_all
from wsl_devctl.errors import DevctlError
from wsl_devctl.paths import RuntimePaths

from .helpers import project_dict


class PrepareTests(unittest.TestCase):
    def test_maven_wrapper_is_not_installed_through_mise(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = project_dict(root)
            raw["backend"]["enabled"] = False
            raw["toolchain"] = {"provider": "mise", "java": True, "maven": True}
            raw["java"] = {"maven": {"executable": "auto", "repository": "user"}}
            project = parse_project(raw)
            runtime = RuntimePaths(
                root / "config", root / "state", root / "units", root / "README.md"
            )

            with (
                patch("wsl_devctl.controller.mise_inventory", return_value={"java": [{}]}),
                patch("wsl_devctl.controller.resolve_maven") as resolve,
                patch(
                    "wsl_devctl.controller.missing_mise_tools", return_value=("java@21",)
                ) as missing,
                patch("wsl_devctl.controller.install_mise_tools") as install,
            ):
                resolve.return_value = SimpleNamespace(executable="/cache/mvnw")
                prepare_all(runtime, project)

            missing.assert_called_once_with(project, ["java"])
            install.assert_called_once_with(project, ("java@21",))

    def test_prepare_rejects_required_tools_without_a_mise_declaration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = project_dict(root)
            raw["backend"]["enabled"] = False
            raw["toolchain"] = {"provider": "mise", "node": True}
            project = parse_project(raw)
            runtime = RuntimePaths(
                root / "config", root / "state", root / "units", root / "README.md"
            )

            with (
                patch("wsl_devctl.controller.mise_inventory", return_value={}),
                self.assertRaisesRegex(DevctlError, "not declared for: node"),
            ):
                prepare_all(runtime, project)


if __name__ == "__main__":
    unittest.main()
