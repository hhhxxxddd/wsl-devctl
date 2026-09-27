"""Bridge WSL's command line to the Windows-native PowerShell companion."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .errors import DevctlError


def _windows_script() -> str:
    script = Path(__file__).resolve().parents[2] / "scripts" / "wsl-devctl-win.ps1"
    if not script.is_file():
        raise DevctlError(f"Windows companion is not installed: {script}")
    if shutil.which("pwsh.exe") is None:
        raise DevctlError("Windows PowerShell 7 (pwsh.exe) is not available through WSL interop")
    if shutil.which("wslpath") is None:
        raise DevctlError("wslpath is required to reach the Windows companion")
    result = subprocess.run(
        ["wslpath", "-w", str(script)], capture_output=True, text=True, check=False
    )
    if result.returncode or not result.stdout.strip():
        raise DevctlError(f"Could not translate Windows companion path: {script}")
    return result.stdout.strip()


def run_windows(arguments: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    script = _windows_script()
    forwarded = list(arguments)
    if len(forwarded) >= 2 and forwarded[0] == "register":
        source = forwarded[1]
        if not re.match(r"^(?:[A-Za-z]:[\\/]|\\\\)", source):
            linux_path = Path(source).expanduser().resolve()
            converted = subprocess.run(
                ["wslpath", "-w", str(linux_path)], capture_output=True, text=True, check=False
            )
            if converted.returncode or not converted.stdout.strip():
                raise DevctlError(f"Could not translate project path for Windows: {source}")
            forwarded[1] = converted.stdout.strip()
    command = [
        "pwsh.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", script, *forwarded,
    ]
    return subprocess.run(command, capture_output=capture, text=True, check=False)


def windows_projects() -> list[dict[str, Any]]:
    result = run_windows(["list", "--json"], capture=True)
    if result.returncode:
        raise DevctlError(f"Could not list Windows projects: {result.stderr.strip()}")
    try:
        projects = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise DevctlError("Windows companion returned invalid JSON") from exc
    if not isinstance(projects, list) or any(not isinstance(item, dict) for item in projects):
        raise DevctlError("Windows companion returned an invalid project list")
    return projects
