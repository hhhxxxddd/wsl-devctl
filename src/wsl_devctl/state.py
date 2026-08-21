from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .paths import RuntimePaths


def _json_path(paths: RuntimePaths, name: str, filename: str) -> Path:
    return paths.project_state(name) / filename


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError):
        return {"status": "invalid"}
    return value if isinstance(value, dict) else None


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    temporary.chmod(0o644)
    os.replace(temporary, path)


def source_state_path(paths: RuntimePaths, name: str) -> Path:
    return _json_path(paths, name, "source-git-state.json")


def resource_recovery_path(paths: RuntimePaths, name: str) -> Path:
    return _json_path(paths, name, "resource-recovery.json")


def branch_failure_path(paths: RuntimePaths, name: str) -> Path:
    return paths.project_state(name) / "branch-rebuild.failed"


def save_recovery(
    paths: RuntimePaths,
    name: str,
    *,
    restart_backend: bool,
    status: str,
    exit_code: int | None = None,
) -> None:
    destination = resource_recovery_path(paths, name)
    previous = read_json(destination) or {}
    payload: dict[str, Any] = {
        "created_at": previous.get("created_at", time.time()),
        "updated_at": time.time(),
        "attempts": int(previous.get("attempts", 0)) + int(status.endswith("-building")),
        "restart_backend": bool(previous.get("restart_backend", False) or restart_backend),
        "status": status,
    }
    if exit_code is not None:
        payload["exit_code"] = exit_code
    write_json(destination, payload)
