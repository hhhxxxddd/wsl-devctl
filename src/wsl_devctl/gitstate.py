from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from .config import ProjectConfig
from .process import run_as_user


GitState = dict[str, str]


def git_value(project: ProjectConfig, *arguments: str) -> str | None:
    result = run_as_user(
        project.run_user,
        ["git", "-c", f"safe.directory={project.source}", "-C", str(project.source), *arguments],
        cwd=project.source,
        check=False,
        capture=True,
    )
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def snapshot(project: ProjectConfig) -> GitState | None:
    if git_value(project, "rev-parse", "--is-inside-work-tree") != "true":
        return None
    return {
        "branch": git_value(project, "symbolic-ref", "--quiet", "HEAD") or "DETACHED",
        "head": git_value(project, "rev-parse", "--verify", "HEAD") or "UNBORN",
    }


def lock_paths(project: ProjectConfig) -> list[Path]:
    result: list[Path] = []
    for name in ("index.lock", "HEAD.lock", "shallow.lock"):
        value = git_value(project, "rev-parse", "--git-path", name)
        if value:
            path = Path(value)
            result.append(path if path.is_absolute() else project.source / path)
    return result


def busy(project: ProjectConfig) -> bool:
    return any(path.exists() for path in lock_paths(project))


def rebuild_required(previous: GitState, current: GitState) -> bool:
    if previous["branch"] != current["branch"]:
        return previous["head"] != current["head"]
    return current["branch"] == "DETACHED" and previous["head"] != current["head"]


def wait_for_quiet(
    project: ProjectConfig,
    *,
    stopped: Callable[[], bool] = lambda: False,
) -> GitState | None:
    options = project.section("branch_switch")
    settle = max(0.5, float(options.get("settle_ms", 1500)) / 1000)
    timeout = max(settle, float(options.get("timeout_seconds", 60)))
    deadline = time.monotonic() + timeout
    stable_since: float | None = None
    previous: dict[str, Any] | None = None
    while not stopped() and time.monotonic() < deadline:
        current = snapshot(project)
        if current is None or busy(project):
            previous = current
            stable_since = None
        elif current == previous:
            stable_since = stable_since or time.monotonic()
            if time.monotonic() - stable_since >= settle:
                return current
        else:
            previous = current
            stable_since = time.monotonic()
        time.sleep(0.25)
    return None
