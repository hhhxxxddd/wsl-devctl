from __future__ import annotations

import os
import pwd

from .config import ProjectConfig, require_descendant
from .errors import DevctlError
from .process import run_as_user


def ensure_cache(project: ProjectConfig) -> None:
    """Create the validated cache and make its top-level directory user-owned."""

    cache = require_descendant(project.cache, project.cache_root, "cache")
    cache.mkdir(parents=True, exist_ok=True)
    if os.geteuid() == 0:
        account = pwd.getpwnam(project.run_user)
        os.chown(cache, account.pw_uid, account.pw_gid)
    if cache.is_symlink():
        raise DevctlError(f"cache cannot be a symbolic link: {cache}")


def sync_command(project: ProjectConfig, *, itemize: bool) -> list[str]:
    # Revalidate immediately before assembling the destructive command.
    cache = require_descendant(project.cache, project.cache_root, "cache")
    command = ["rsync", "-a", "--delete", "--delay-updates"]
    if itemize:
        command.append("--itemize-changes")
    for pattern in project.section("sync").get("exclude", []):
        command.append(f"--exclude={pattern}")
    command.extend([f"{project.source}/", f"{cache}/"])
    return command


def sync_once(project: ProjectConfig, *, itemize: bool = False) -> bool:
    if not project.source.is_dir():
        raise DevctlError(f"source directory does not exist: {project.source}")
    ensure_cache(project)
    result = run_as_user(
        project.run_user,
        sync_command(project, itemize=itemize),
        cwd=project.source,
        check=False,
        capture=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise DevctlError(f"rsync failed with exit code {result.returncode}: {detail}")
    if itemize and result.stdout:
        print(result.stdout, end="")
    return bool(result.stdout.strip())
