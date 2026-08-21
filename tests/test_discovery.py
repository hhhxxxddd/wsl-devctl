from __future__ import annotations

import getpass
import json
import tempfile
import tomllib
import unittest
from pathlib import Path

from wsl_devctl.config import parse_project
from wsl_devctl.discovery import build_project_config, default_name, discover
from wsl_devctl.tomlgen import render_toml


class DiscoveryTests(unittest.TestCase):
    def test_next_project_generates_safe_host_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "News Reader"
            source.mkdir()
            (source / "package.json").write_text(
                json.dumps(
                    {
                        "packageManager": "pnpm@10.0.0",
                        "dependencies": {"next": "16.0.0", "react": "19.0.0"},
                    }
                ),
                encoding="utf-8",
            )
            detection = discover(str(source))
            self.assertEqual(detection.framework, "next")
            self.assertEqual(detection.package_manager, "pnpm")
            raw = build_project_config(detection, default_name(source), getpass.getuser())
            self.assertEqual(raw["frontend"]["port"], 3000)
            self.assertIn("--hostname 0.0.0.0", raw["frontend"]["run"])
            self.assertIn("**/.next/", raw["sync"]["exclude"])
            rendered = render_toml(raw)
            parsed = tomllib.loads(rendered)
            project = parse_project(parsed)
            self.assertEqual(project.runtime_driver, "host")

    def test_vite_project_uses_vite_host_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "web"
            source.mkdir()
            (source / "package.json").write_text(
                json.dumps({"devDependencies": {"vite": "7.0.0"}}),
                encoding="utf-8",
            )
            (source / "package-lock.json").write_text("{}", encoding="utf-8")
            detection = discover(str(source))
            raw = build_project_config(detection, "dev-web", getpass.getuser())
            self.assertEqual(raw["frontend"]["port"], 5173)
            self.assertIn("--host 0.0.0.0", raw["frontend"]["run"])

    def test_compose_file_selects_compose_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "stack"
            source.mkdir()
            (source / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
            detection = discover(str(source))
            raw = build_project_config(detection, "dev-stack", getpass.getuser())
            self.assertEqual(raw["runtime"]["driver"], "compose")
            self.assertEqual(raw["docker"]["compose"]["files"], ["compose.yaml"])
            self.assertFalse(raw["backend"]["enabled"])

    def test_monorepo_selects_nested_next_and_root_package_manager(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "workspace"
            app = source / "apps/web"
            app.mkdir(parents=True)
            (source / "package.json").write_text(
                json.dumps({"packageManager": "pnpm@10.0.0", "workspaces": ["apps/*"]}),
                encoding="utf-8",
            )
            (app / "package.json").write_text(
                json.dumps({"dependencies": {"next": "16.0.0"}}),
                encoding="utf-8",
            )
            detection = discover(str(source), runtime="host")
            self.assertEqual(detection.package_file, app / "package.json")
            self.assertEqual(detection.package_manager, "pnpm")
            raw = build_project_config(detection, "dev-workspace", getpass.getuser())
            self.assertEqual(raw["frontend"]["workdir"], "apps/web")


if __name__ == "__main__":
    unittest.main()
