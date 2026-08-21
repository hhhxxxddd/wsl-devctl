from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from wsl_devctl.config import parse_project
from wsl_devctl.drivers.spring import classpath_overlay, parse_coordinate, signal_reload
from wsl_devctl.errors import DevctlError
from wsl_devctl.paths import RuntimePaths

from .helpers import project_dict


class SpringOverlayTests(unittest.TestCase):
    def test_coordinate_rejects_path_traversal(self) -> None:
        with self.assertRaises(DevctlError):
            parse_coordinate("org.example:../../outside:1.0")

    def test_overlay_copies_only_configured_package_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = project_dict(root)
            raw["java"] = {
                "maven": {"repository": "user", "executable": "mvn"},
                "spring": {
                    "classpath_modules": ["backend/domain/target/classes"],
                    "classpath_entries": ["com"],
                },
            }
            project = parse_project(raw)
            classes = project.cache / "backend/domain/target/classes"
            (classes / "com/example").mkdir(parents=True)
            (classes / "application.yaml").write_text("ignored", encoding="utf-8")
            runtime = RuntimePaths(
                config_dir=root / "config",
                state_root=root / "state",
                unit_dir=root / "units",
                documentation=root / "README.md",
            )
            value = classpath_overlay(runtime, project)
            overlay = runtime.project_state(project.name) / "classpath/00"
            self.assertIn(str(overlay), value)
            self.assertTrue((overlay / "com").is_dir())
            self.assertFalse((overlay / "com").is_symlink())
            self.assertTrue((overlay / "com/example").is_dir())
            self.assertFalse((overlay / "application.yaml").exists())
            signal_reload(runtime, project)
            self.assertTrue((overlay / "wsl-devctl-reload.trigger").is_file())


if __name__ == "__main__":
    unittest.main()
