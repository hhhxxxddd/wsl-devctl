from __future__ import annotations

import re
import shutil
import time
from pathlib import Path

from ..config import ProjectConfig, require_within
from ..errors import DevctlError
from ..paths import RuntimePaths
from ..process import run, run_as_user
from .maven import MavenRuntime, resolve_maven


COORDINATE_PART_RE = re.compile(r"^[A-Za-z0-9_.+\-]+$")


def spring_config(project: ProjectConfig) -> dict:
    java = project.section("java")
    value = java.get("spring", {}) if java else {}
    if value and not isinstance(value, dict):
        raise DevctlError("[java.spring] must be a TOML table")
    return value


def parse_coordinate(value: str) -> tuple[str, str, str]:
    parts = value.split(":")
    if (
        len(parts) != 3
        or not all(parts)
        or not all(COORDINATE_PART_RE.fullmatch(part) for part in parts)
        or any(part in {".", ".."} for part in parts)
    ):
        raise DevctlError("java.spring.devtools must use group:artifact:version")
    return parts[0], parts[1], parts[2]


def artifact_jar(repository: Path, coordinate: str) -> Path:
    group, artifact, version = parse_coordinate(coordinate)
    return (
        repository
        / Path(group.replace(".", "/"))
        / artifact
        / version
        / f"{artifact}-{version}.jar"
    )


def ensure_devtools(project: ProjectConfig) -> Path | None:
    spring = spring_config(project)
    coordinate = str(spring.get("devtools", "")).strip()
    if not coordinate:
        return None
    maven = resolve_maven(project)
    if maven is None:
        raise DevctlError("java.spring.devtools requires [java.maven]")
    jar = artifact_jar(maven.repository, coordinate)
    if jar.is_file():
        return jar
    result = run_as_user(
        project.run_user,
        [maven.executable, "-q", f"-Dartifact={coordinate}", "dependency:get"],
        cwd=project.workdir("backend"),
        env=maven.environment(),
        check=False,
    )
    if result.returncode != 0 or not jar.is_file():
        raise DevctlError(f"cannot resolve Spring DevTools artifact: {coordinate}")
    return jar


def classpath_overlay(paths: RuntimePaths, project: ProjectConfig) -> str:
    spring = spring_config(project)
    modules = spring.get("classpath_modules", [])
    entries = spring.get("classpath_entries", ["com"])
    if not isinstance(modules, list) or not isinstance(entries, list):
        raise DevctlError("Spring classpath modules and entries must be arrays")
    parts: list[str] = []
    devtools = ensure_devtools(project)
    if devtools:
        parts.append(str(devtools))
    overlay_root = paths.project_state(project.name) / "classpath"
    overlay_root.mkdir(parents=True, exist_ok=True)
    for index, module in enumerate(modules):
        source = require_within(project.cache / str(module), project.cache, "classpath module")
        if not source.is_dir():
            raise DevctlError(f"compiled classpath module does not exist; run prepare: {source}")
        overlay = overlay_root / f"{index:02d}"
        overlay.mkdir(parents=True, exist_ok=True)
        allowed = {str(entry) for entry in entries}
        for existing in overlay.iterdir():
            if existing.name not in allowed and existing.name != "wsl-devctl-reload.trigger":
                if existing.is_dir() and not existing.is_symlink():
                    shutil.rmtree(existing)
                else:
                    existing.unlink()
        for raw_entry in entries:
            entry = str(raw_entry)
            if entry in ("", ".", "..") or "/" in entry or "\\" in entry:
                raise DevctlError(f"invalid java.spring.classpath_entries value: {entry!r}")
            target = source / entry
            destination = overlay / entry
            if not target.exists():
                if destination.is_dir() and not destination.is_symlink():
                    shutil.rmtree(destination)
                else:
                    destination.unlink(missing_ok=True)
                continue
            if destination.is_symlink():
                destination.unlink()
            destination.mkdir(parents=True, exist_ok=True)
            result = run(
                [
                    "rsync",
                    "-a",
                    "--delete",
                    "--delay-updates",
                    f"{target}/",
                    f"{destination}/",
                ],
                check=False,
                capture=True,
            )
            if result.returncode != 0:
                detail = result.stderr.strip() or result.stdout.strip()
                raise DevctlError(
                    f"cannot refresh Spring classpath overlay {destination}: {detail}"
                )
        parts.append(str(overlay))
    return ",".join(parts)


def signal_reload(paths: RuntimePaths, project: ProjectConfig) -> None:
    modules = spring_config(project).get("classpath_modules", [])
    if not modules:
        return
    classpath_overlay(paths, project)
    stamp = f"{time.time_ns()}\n"
    overlay_root = paths.project_state(project.name) / "classpath"
    for index, _module in enumerate(modules):
        marker = overlay_root / f"{index:02d}" / "wsl-devctl-reload.trigger"
        marker.write_text(stamp, encoding="utf-8")
        marker.chmod(0o644)


def java_environment(project: ProjectConfig) -> dict[str, str]:
    runtime: MavenRuntime | None = resolve_maven(project)
    return runtime.environment() if runtime else {}
