from __future__ import annotations

import os
import pwd
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import DevctlError
from .paths import RuntimePaths


NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
VALID_REPOSITORY_MODES = frozenset({"user", "project", "path"})
VALID_RUNTIME_DRIVERS = frozenset({"host", "compose"})


def validate_name(name: str) -> None:
    if not NAME_RE.fullmatch(name):
        raise DevctlError(f"invalid project name: {name!r}")


def user_home(user: str) -> Path:
    try:
        return Path(pwd.getpwnam(user).pw_dir)
    except KeyError as exc:
        raise DevctlError(f"run_user does not exist: {user}") from exc


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def require_descendant(path: Path, root: Path, label: str) -> Path:
    """Return a canonical path that is strictly below a canonical root.

    Resolving both values closes `..` and existing symlink escapes before any
    destructive rsync operation is assembled.
    """

    candidate = _resolved(path)
    boundary = _resolved(root)
    if candidate == boundary or boundary not in candidate.parents:
        raise DevctlError(f"{label} must stay below {boundary}: {candidate}")
    return candidate


def require_within(path: Path, root: Path, label: str) -> Path:
    candidate = _resolved(path)
    boundary = _resolved(root)
    if candidate != boundary and boundary not in candidate.parents:
        raise DevctlError(f"{label} must stay within {boundary}: {candidate}")
    return candidate


def _table(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise DevctlError(f"[{name}] must be a TOML table")
    return value


@dataclass(frozen=True)
class ProjectConfig:
    name: str
    source: Path
    cache: Path
    cache_root: Path
    run_user: str
    raw: dict[str, Any]

    def section(self, name: str) -> dict[str, Any]:
        return _table(self.raw, name)

    @property
    def runtime_driver(self) -> str:
        return str(self.section("runtime").get("driver", "host"))

    def compose(self) -> dict[str, Any]:
        docker = self.section("docker")
        value = docker.get("compose", {})
        if not isinstance(value, dict):
            raise DevctlError("[docker.compose] must be a TOML table")
        return value

    def enabled(self, kind: str) -> bool:
        if kind == "sync":
            return bool(self.section("sync").get("enabled", True))
        if kind == "compose":
            return self.runtime_driver == "compose"
        if self.runtime_driver == "compose" and kind in {"backend", "frontend", "compile"}:
            return False
        return bool(self.section(kind).get("enabled", False))

    def workdir(self, kind: str) -> Path:
        relative = Path(str(self.section(kind).get("workdir", ".")))
        if relative.is_absolute():
            raise DevctlError(f"{kind}.workdir must be relative")
        return require_within(self.cache / relative, self.cache, f"{kind}.workdir")

    def environment(self, kind: str) -> dict[str, str]:
        value = self.section(kind).get("env", {})
        if not isinstance(value, dict):
            raise DevctlError(f"{kind}.env must be a TOML table")
        result: dict[str, str] = {}
        for key, item in value.items():
            if not ENV_NAME_RE.fullmatch(str(key)):
                raise DevctlError(f"invalid environment variable in {kind}.env: {key!r}")
            result[str(key)] = str(item)
        return result


def _validate_port(value: Any, label: str) -> None:
    if value in (None, "", 0):
        return
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise DevctlError(f"{label} must be an integer") from exc
    if not 1 <= port <= 65535:
        raise DevctlError(f"{label} must be between 1 and 65535")


def _validate_java(raw: dict[str, Any], project: ProjectConfig) -> None:
    java = _table(raw, "java")
    if not java:
        return
    maven = java.get("maven", {})
    if not isinstance(maven, dict):
        raise DevctlError("[java.maven] must be a TOML table")
    mode = str(maven.get("repository", "user"))
    if mode not in VALID_REPOSITORY_MODES:
        raise DevctlError(f"java.maven.repository must be one of {sorted(VALID_REPOSITORY_MODES)}")
    if mode == "path":
        path_value = str(maven.get("repository_path", "")).strip()
        if not path_value or not Path(path_value).is_absolute():
            raise DevctlError("java.maven.repository_path must be absolute in path mode")
        if _resolved(Path(path_value)) == Path("/"):
            raise DevctlError("java.maven.repository_path cannot be /")
    spring = java.get("spring", {})
    if spring and not isinstance(spring, dict):
        raise DevctlError("[java.spring] must be a TOML table")
    modules = spring.get("classpath_modules", []) if isinstance(spring, dict) else []
    if not isinstance(modules, list):
        raise DevctlError("java.spring.classpath_modules must be an array")
    for module in modules:
        require_descendant(project.cache / str(module), project.cache, "classpath module")


def _validate_compose(project: ProjectConfig) -> None:
    runtime = project.section("runtime")
    driver = str(runtime.get("driver", "host"))
    if driver not in VALID_RUNTIME_DRIVERS:
        raise DevctlError(f"runtime.driver must be one of {sorted(VALID_RUNTIME_DRIVERS)}")
    compose = project.compose()
    if driver != "compose":
        return
    workdir = Path(str(compose.get("workdir", ".")))
    if workdir.is_absolute():
        raise DevctlError("docker.compose.workdir must be relative")
    compose_root = require_within(
        project.cache / workdir,
        project.cache,
        "docker.compose.workdir",
    )
    files = compose.get("files", ["compose.yaml"])
    if not isinstance(files, list) or not files:
        raise DevctlError("docker.compose.files must be a non-empty array")
    for value in files:
        path = Path(str(value))
        if str(value).strip() in {"", ".", ".."}:
            raise DevctlError("docker.compose.files entries must name a file")
        if path.is_absolute():
            raise DevctlError("docker.compose.files entries must be relative")
        require_within(compose_root / path, compose_root, "docker.compose.files entry")
    profiles = compose.get("profiles", [])
    if not isinstance(profiles, list):
        raise DevctlError("docker.compose.profiles must be an array")
    environment = compose.get("env", {})
    if not isinstance(environment, dict):
        raise DevctlError("docker.compose.env must be a TOML table")
    for key in environment:
        if not ENV_NAME_RE.fullmatch(str(key)):
            raise DevctlError(f"invalid environment variable in docker.compose.env: {key!r}")
    for key in ("build", "build_on_start", "pull", "remove_orphans"):
        if key in compose and not isinstance(compose[key], bool):
            raise DevctlError(f"docker.compose.{key} must be a boolean")
    timeout = compose.get("stop_timeout_seconds", 20)
    if not isinstance(timeout, int) or not 1 <= timeout <= 600:
        raise DevctlError("docker.compose.stop_timeout_seconds must be between 1 and 600")


def parse_project(raw: dict[str, Any]) -> ProjectConfig:
    name = str(raw.get("name", ""))
    validate_name(name)
    run_user = str(raw.get("run_user", os.environ.get("SUDO_USER") or os.environ.get("USER") or ""))
    if not run_user:
        raise DevctlError("run_user is required")
    home = user_home(run_user)
    source_value = str(raw.get("source", ""))
    if not source_value or not Path(source_value).is_absolute():
        raise DevctlError("source must be an absolute WSL path")
    source = _resolved(Path(source_value))
    cache_root_value = str(raw.get("cache_root", home / ".cache/wsl-devctl/build"))
    if not Path(cache_root_value).is_absolute():
        raise DevctlError("cache_root must be absolute")
    cache_root = _resolved(Path(cache_root_value))
    if cache_root == Path("/"):
        raise DevctlError("cache_root cannot be /")
    cache_value = str(raw.get("cache", cache_root / name))
    if not Path(cache_value).is_absolute():
        raise DevctlError("cache must be absolute")
    cache = require_descendant(Path(cache_value), cache_root, "cache")
    if source == cache or source in cache.parents or cache in source.parents:
        raise DevctlError(f"source and cache must not overlap: {source}, {cache}")
    project = ProjectConfig(
        name=name,
        source=source,
        cache=cache,
        cache_root=cache_root,
        run_user=run_user,
        raw=raw,
    )
    for table_name in (
        "runtime",
        "sync",
        "backend",
        "frontend",
        "compile",
        "branch_switch",
        "checks",
        "docker",
        "toolchain",
    ):
        _table(raw, table_name)
    for kind in ("backend", "frontend"):
        _validate_port(project.section(kind).get("port"), f"{kind}.port")
        project.environment(kind)
        project.workdir(kind)
    project.workdir("compile")
    _validate_java(raw, project)
    _validate_compose(project)
    return project


def read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as stream:
            value = tomllib.load(stream)
    except FileNotFoundError as exc:
        raise DevctlError(f"configuration does not exist: {path}") from exc
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise DevctlError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DevctlError(f"configuration root must be a TOML table: {path}")
    return value


def load_project(name: str, paths: RuntimePaths) -> ProjectConfig:
    validate_name(name)
    project = parse_project(read_toml(paths.config_path(name)))
    if project.name != name:
        raise DevctlError(f"config name mismatch in {paths.config_path(name)}")
    return project
