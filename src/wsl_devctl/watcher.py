from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable

from .config import ProjectConfig, require_within


EXCLUDED_DIRECTORIES = frozenset(
    {".git", ".codegraph", "node_modules", "target", "dist", "dist-prod", "__pycache__"}
)
EXCLUDED_FILES = frozenset({".flattened-pom.xml"})


class ChangeKind(str, Enum):
    SOURCE = "source"
    RESOURCE = "resource"
    STRUCTURAL = "structural"


@dataclass(frozen=True)
class FileStamp:
    modified_ns: int
    size: int


Snapshot = dict[str, FileStamp]


def _excluded(path: Path, root: Path, patterns: tuple[str, ...]) -> bool:
    if path.name in EXCLUDED_FILES:
        return True
    relative = path.relative_to(root).as_posix()
    return any(
        fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(path.name, pattern)
        for pattern in patterns
    )


def tree_snapshot(
    roots: Iterable[Path],
    suffixes: set[str],
    patterns: tuple[str, ...] = (),
) -> Snapshot:
    result: Snapshot = {}
    for root in roots:
        if not root.exists():
            continue
        for current, directories, files in os.walk(root):
            directories[:] = [name for name in directories if name not in EXCLUDED_DIRECTORIES]
            current_path = Path(current)
            for name in files:
                path = current_path / name
                try:
                    if suffixes and path.suffix.lower() not in suffixes:
                        continue
                    if _excluded(path, root, patterns):
                        continue
                    stat = path.stat()
                    result[str(path)] = FileStamp(stat.st_mtime_ns, stat.st_size)
                except FileNotFoundError:
                    continue
    return result


def structural_snapshot(project: ProjectConfig) -> Snapshot:
    compile_root = project.workdir("compile")
    patterns = project.section("compile").get(
        "structural",
        ["pom.xml", "**/pom.xml", ".mvn/**", "mvnw", "mvnw.cmd"],
    )
    result: Snapshot = {}
    for current, directories, files in os.walk(compile_root):
        directories[:] = [name for name in directories if name not in EXCLUDED_DIRECTORIES]
        current_path = Path(current)
        for name in files:
            path = current_path / name
            relative = path.relative_to(compile_root).as_posix()
            if not any(fnmatch.fnmatch(relative, str(pattern)) for pattern in patterns):
                continue
            stat = path.stat()
            result[str(path)] = FileStamp(stat.st_mtime_ns, stat.st_size)
    return result


def configured_roots(project: ProjectConfig) -> list[Path]:
    roots = []
    for value in project.section("compile").get("watch", []):
        roots.append(require_within(project.cache / str(value), project.cache, "compile.watch"))
    return roots


def classify_changes(
    previous: Snapshot,
    current: Snapshot,
    previous_structural: Snapshot,
    current_structural: Snapshot,
    resource_suffixes: set[str],
) -> tuple[ChangeKind, set[Path]] | None:
    structural_changed = previous_structural != current_structural
    changed = {
        Path(value)
        for value in previous.keys() | current.keys()
        if previous.get(value) != current.get(value)
    }
    deleted = {Path(value) for value in previous.keys() - current.keys()}
    if structural_changed or deleted:
        structural_paths = {
            Path(value)
            for value in previous_structural.keys() | current_structural.keys()
            if previous_structural.get(value) != current_structural.get(value)
        }
        return ChangeKind.STRUCTURAL, changed | structural_paths
    if not changed:
        return None
    if any(path.suffix.lower() in resource_suffixes for path in changed):
        return ChangeKind.RESOURCE, changed
    return ChangeKind.SOURCE, changed
