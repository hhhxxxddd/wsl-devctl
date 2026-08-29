from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wsl_devctl.cli import _generate_mise_config, cmd_help, parser
from wsl_devctl.errors import DevctlError


class MiseCliTests(unittest.TestCase):
    def test_generate_mise_uses_external_json_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            completed = subprocess.CompletedProcess(
                [],
                0,
                json.dumps({"action": "preview", "tools": {"java": {"version": "21"}}}),
                "",
            )
            with (
                patch("wsl_devctl.cli.shutil.which", return_value="/usr/local/bin/dev-tools"),
                patch("wsl_devctl.cli.subprocess.run", return_value=completed) as invoke,
            ):
                payload = _generate_mise_config(source, dry_run=True)

            self.assertEqual(payload["action"], "preview")
            command = invoke.call_args.args[0]
            self.assertEqual(command[:3], ["/usr/local/bin/dev-tools", "project", "init"])
            self.assertIn("--dry-run", command)
            self.assertIn("--json", command)

    def test_generate_mise_rejects_conflicts(self) -> None:
        completed = subprocess.CompletedProcess(
            [],
            2,
            json.dumps({"action": "conflict", "conflicts": [{"tool": "node"}]}),
            "",
        )
        with (
            patch("wsl_devctl.cli.shutil.which", return_value="dev-tools"),
            patch("wsl_devctl.cli.subprocess.run", return_value=completed),
            self.assertRaisesRegex(DevctlError, "declarations conflict"),
        ):
            _generate_mise_config(Path("/workspace"), dry_run=False)

    def test_parser_accepts_generate_mise(self) -> None:
        args = parser().parse_args(["init", "/workspace", "--generate-mise"])
        self.assertTrue(args.generate_mise)

    def test_help_command_prints_chinese_quick_reference(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            cmd_help(parser().parse_args(["help"]))

        value = output.getvalue()
        self.assertIn("Windows 源码 + WSL ext4", value)
        self.assertIn("wsl-devctl doctor <名称> --fix", value)


if __name__ == "__main__":
    unittest.main()
