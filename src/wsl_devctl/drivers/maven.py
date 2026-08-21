from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path

from ..config import ProjectConfig, user_home
from ..errors import DevctlError
from ..process import run_as_user


@dataclass(frozen=True)
class MavenRuntime:
    executable: str
    repository: Path

    def environment(self) -> dict[str, str]:
        existing = os.environ.get("MAVEN_OPTS", "").strip()
        option = f"-Dmaven.repo.local={self.repository}"
        options = f"{existing} {option}".strip()
        return {
            "MAVEN_OPTS": options,
            "WSL_DEVCTL_MAVEN": self.executable,
            "WSL_DEVCTL_MAVEN_REPOSITORY": str(self.repository),
        }

    def command(self, *arguments: str) -> str:
        return shlex.join([self.executable, *arguments])


def _find_wrapper(start: Path, boundary: Path) -> Path | None:
    current = start.resolve(strict=False)
    root = boundary.resolve(strict=False)
    while current == root or root in current.parents:
        wrapper = current / "mvnw"
        if wrapper.is_file():
            return wrapper
        if current == root:
            break
        current = current.parent
    return None


def resolve_maven(project: ProjectConfig) -> MavenRuntime | None:
    java = project.section("java")
    if not java:
        return None
    raw = java.get("maven", {})
    if not isinstance(raw, dict):
        raise DevctlError("[java.maven] must be a TOML table")
    mode = str(raw.get("repository", "user"))
    home = user_home(project.run_user)
    if mode == "user":
        repository = home / ".m2/repository"
    elif mode == "project":
        repository = home / f".cache/wsl-devctl/maven/{project.name}/repository"
    elif mode == "path":
        repository = Path(str(raw["repository_path"]))
    else:
        raise DevctlError(f"unsupported Maven repository mode: {mode}")
    configured = str(raw.get("executable", "auto")).strip()
    if configured == "auto":
        wrapper = _find_wrapper(project.workdir("backend"), project.cache)
        if wrapper is None:
            relative_workdir = project.workdir("backend").relative_to(project.cache)
            source_wrapper = _find_wrapper(project.source / relative_workdir, project.source)
            if source_wrapper is not None:
                wrapper = project.cache / source_wrapper.relative_to(project.source)
        executable = str(wrapper) if wrapper else "mvn"
    else:
        executable = configured
    if "/" in executable:
        candidate = Path(executable)
        if not candidate.is_absolute():
            candidate = project.workdir("backend") / candidate
        executable = str(candidate.resolve(strict=False))
    return MavenRuntime(executable=executable, repository=repository.resolve(strict=False))


def maven_executable_exists(runtime: MavenRuntime, project: ProjectConfig) -> bool:
    if "/" in runtime.executable:
        candidate = Path(runtime.executable)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return True
        try:
            source_candidate = project.source / candidate.relative_to(project.cache)
        except ValueError:
            return False
        return source_candidate.is_file()
    environment = runtime.environment()
    cwd = project.workdir("backend")
    if not cwd.is_dir():
        cwd = project.cache
    result = run_as_user(
        project.run_user,
        ["/bin/bash", "-lc", f"command -v {shlex.quote(runtime.executable)}"],
        cwd=cwd,
        env=environment,
        check=False,
        capture=True,
    )
    return result.returncode == 0
