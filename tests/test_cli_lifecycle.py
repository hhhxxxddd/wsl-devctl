from __future__ import annotations

import argparse
import copy
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wsl_devctl.cli import cmd_rename, cmd_unregister, cmd_update
from wsl_devctl.config import load_project
from wsl_devctl.errors import DevctlError
from wsl_devctl.paths import RuntimePaths
from wsl_devctl.tomlgen import render_toml

from .helpers import project_dict


def completed(*_args, **_kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], 0, "", "")


class LifecycleTests(unittest.TestCase):
    def runtime(self, root: Path) -> RuntimePaths:
        return RuntimePaths(
            root / "config",
            root / "state",
            root / "units",
            root / "README",
        )

    def register_fixture(self, runtime: RuntimePaths, raw: dict) -> None:
        runtime.config_dir.mkdir(parents=True)
        runtime.project_state(str(raw["name"])).mkdir(parents=True)
        runtime.config_path(str(raw["name"])).write_text(render_toml(raw), encoding="utf-8")

    def test_update_replaces_config_and_preserves_active_kinds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = self.runtime(root)
            raw = project_dict(root)
            self.register_fixture(runtime, raw)
            replacement = copy.deepcopy(raw)
            replacement["backend"]["port"] = 9090
            replacement_path = root / "replacement.toml"
            replacement_path.write_text(render_toml(replacement), encoding="utf-8")

            calls: list[tuple[str, ...]] = []

            def systemctl(*arguments: str, **_kwargs):
                calls.append(arguments)
                return completed()

            with (
                patch("wsl_devctl.cli.require_root"),
                patch("wsl_devctl.cli.paths", return_value=runtime),
                patch(
                    "wsl_devctl.cli.unit_active",
                    side_effect=lambda _name, kind: kind in {"sync", "backend"},
                ),
                patch("wsl_devctl.cli.systemctl", side_effect=systemctl),
                patch("wsl_devctl.cli.sync_once"),
                patch("wsl_devctl.cli.git_snapshot", return_value=None),
            ):
                cmd_update(
                    argparse.Namespace(
                        name="dev-test",
                        config=str(replacement_path),
                        prepare=False,
                    )
                )

            updated = load_project("dev-test", runtime)
            self.assertEqual(updated.section("backend")["port"], 9090)
            self.assertIn(("stop", "wsl-dev-backend@dev-test.service"), calls)
            self.assertIn(("start", "wsl-dev-backend@dev-test.service"), calls)
            self.assertNotIn(("start", "wsl-dev-frontend@dev-test.service"), calls)

    def test_update_rejects_storage_identity_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = self.runtime(root)
            raw = project_dict(root)
            self.register_fixture(runtime, raw)
            replacement = copy.deepcopy(raw)
            other_source = root / "other-source"
            other_source.mkdir()
            replacement["source"] = str(other_source)
            replacement_path = root / "replacement.toml"
            replacement_path.write_text(render_toml(replacement), encoding="utf-8")

            with (
                patch("wsl_devctl.cli.require_root"),
                patch("wsl_devctl.cli.paths", return_value=runtime),
                self.assertRaisesRegex(DevctlError, "cannot change source"),
            ):
                cmd_update(
                    argparse.Namespace(
                        name="dev-test",
                        config=str(replacement_path),
                        prepare=False,
                    )
                )

    def test_rename_moves_registration_state_and_preserves_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = self.runtime(root)
            raw = project_dict(root)
            self.register_fixture(runtime, raw)
            marker = runtime.project_state("dev-test") / "marker"
            marker.write_text("state", encoding="utf-8")
            calls: list[tuple[str, ...]] = []

            def systemctl(*arguments: str, **_kwargs):
                calls.append(arguments)
                return completed()

            with (
                patch("wsl_devctl.cli.require_root"),
                patch("wsl_devctl.cli.paths", return_value=runtime),
                patch(
                    "wsl_devctl.cli.unit_active",
                    side_effect=lambda _name, kind: kind == "backend",
                ),
                patch("wsl_devctl.cli.systemctl", side_effect=systemctl),
            ):
                cmd_rename(argparse.Namespace(name="dev-test", new_name="renamed-test"))

            self.assertFalse(runtime.config_path("dev-test").exists())
            renamed = load_project("renamed-test", runtime)
            self.assertEqual(renamed.cache, (root / "cache-root/dev-test").resolve())
            self.assertEqual(
                (runtime.project_state("renamed-test") / "marker").read_text(encoding="utf-8"),
                "state",
            )
            self.assertIn(("start", "wsl-dev-backend@renamed-test.service"), calls)

    def test_unregister_keeps_cache_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = self.runtime(root)
            raw = project_dict(root)
            self.register_fixture(runtime, raw)
            cache = Path(raw["cache"])
            cache.mkdir(parents=True)
            (cache / "artifact").write_text("keep", encoding="utf-8")

            with (
                patch("wsl_devctl.cli.require_root"),
                patch("wsl_devctl.cli.paths", return_value=runtime),
                patch("wsl_devctl.cli.unit_active", return_value=False),
                patch("wsl_devctl.cli.systemctl", side_effect=completed),
            ):
                cmd_unregister(argparse.Namespace(name="dev-test", purge_cache=False))

            self.assertFalse(runtime.config_path("dev-test").exists())
            self.assertFalse(runtime.project_state("dev-test").exists())
            self.assertTrue((cache / "artifact").exists())

    def test_unregister_purges_cache_only_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = self.runtime(root)
            raw = project_dict(root)
            self.register_fixture(runtime, raw)
            cache = Path(raw["cache"])
            cache.mkdir(parents=True)

            with (
                patch("wsl_devctl.cli.require_root"),
                patch("wsl_devctl.cli.paths", return_value=runtime),
                patch("wsl_devctl.cli.unit_active", return_value=False),
                patch("wsl_devctl.cli.systemctl", side_effect=completed),
            ):
                cmd_unregister(argparse.Namespace(name="dev-test", purge_cache=True))

            self.assertFalse(cache.exists())


if __name__ == "__main__":
    unittest.main()
