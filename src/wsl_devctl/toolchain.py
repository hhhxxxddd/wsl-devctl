from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from .config import ProjectConfig
from .errors import DevctlError
from .process import exec_as_user, log, run_as_user, shell_as_user

PROJECT_COMMANDS = frozenset(
    {
        "bun",
        "corepack",
        "java",
        "javac",
        "mvn",
        "node",
        "npm",
        "pnpm",
        "python",
        "python3",
        "uv",
        "yarn",
    }
)


def provider(project: ProjectConfig) -> str:
    return str(project.section("toolchain").get("provider", "system"))


def uses_mise(project: ProjectConfig) -> bool:
    return provider(project) == "mise"


def _mise_executable() -> str:
    executable = shutil.which("mise")
    if executable is None:
        raise DevctlError("mise toolchain provider requires mise on PATH")
    return executable


def _mise_environment(
    project: ProjectConfig,
    additions: Mapping[str, str] | None = None,
) -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if key.startswith("MISE_")}
    trusted = [str(project.source), str(project.cache)]
    existing = environment.get("MISE_TRUSTED_CONFIG_PATHS", "").strip()
    if existing:
        trusted.insert(0, existing)
    environment.update(
        {
            "MISE_AUTO_INSTALL": "false",
            "MISE_EXEC_AUTO_INSTALL": "false",
            "MISE_GLOBAL_CONFIG_FILE": "/dev/null",
            "MISE_NOT_FOUND_AUTO_INSTALL": "false",
            "MISE_NOT_FOUND_SYSTEM_FALLBACK": "false",
            "MISE_TRUSTED_CONFIG_PATHS": os.pathsep.join(trusted),
        }
    )
    if additions:
        environment.update({str(key): str(value) for key, value in additions.items()})
    return environment


def _source_workdir(project: ProjectConfig, kind: str) -> Path:
    relative = project.workdir(kind).relative_to(project.cache)
    return project.source / relative


def configuration_roots(project: ProjectConfig, *, source: bool = False) -> list[Path]:
    base = project.source if source else project.cache
    values = [base]
    for kind in ("backend", "frontend", "compile"):
        if kind != "compile" and not project.enabled(kind):
            continue
        candidate = _source_workdir(project, kind) if source else project.workdir(kind)
        if candidate.is_dir() and candidate not in values:
            values.append(candidate)
    return values


def run_mise(
    project: ProjectConfig,
    arguments: Sequence[str],
    *,
    cwd: Path,
    check: bool = True,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    return run_as_user(
        project.run_user,
        [_mise_executable(), *arguments],
        cwd=cwd,
        env=_mise_environment(project),
        check=check,
        capture=capture,
    )


def run_project(
    project: ProjectConfig,
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    check: bool = True,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    if not uses_mise(project):
        return run_as_user(
            project.run_user,
            command,
            cwd=cwd,
            env=env,
            check=check,
            capture=capture,
        )
    wrapped = [_mise_executable(), "exec", "--", *command]
    return run_as_user(
        project.run_user,
        wrapped,
        cwd=cwd,
        env=_mise_environment(project, env),
        check=check,
        capture=capture,
    )


def shell_project(
    project: ProjectConfig,
    command: str,
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    check: bool = True,
) -> int:
    if not uses_mise(project):
        return shell_as_user(
            project.run_user,
            command,
            cwd=cwd,
            env=env,
            check=check,
        )
    log(f"+ [{project.run_user}] ({cwd}) [mise] {command}")
    result = run_project(
        project,
        ["/bin/bash", "-c", command],
        cwd=cwd,
        env=env,
        check=False,
    )
    if check and result.returncode != 0:
        raise DevctlError(f"command failed with exit code {result.returncode}")
    return result.returncode


def exec_project_shell(
    project: ProjectConfig,
    command: str,
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
) -> None:
    if uses_mise(project):
        argv = [_mise_executable(), "exec", "--", "/bin/bash", "-c", f"exec {command}"]
        environment = _mise_environment(project, env)
        label = f"[mise] {command}"
    else:
        argv = ["/bin/bash", "-lc", f"exec {command}"]
        environment = env
        label = command
    exec_as_user(project.run_user, argv, cwd=cwd, env=environment, label=label)


def project_command_exists(project: ProjectConfig, command: str, *, cwd: Path) -> bool:
    result = run_project(
        project,
        ["/bin/bash", "-c", f"command -v {shlex.quote(command)}"],
        cwd=cwd,
        check=False,
        capture=True,
    )
    return result.returncode == 0


def command_workdir(project: ProjectConfig, command: str) -> Path:
    name = Path(command).name
    if name in {"node", "npm", "pnpm", "yarn", "bun", "corepack"}:
        kind = "frontend"
    else:
        kind = "backend"
    candidate = project.workdir(kind)
    return candidate if candidate.is_dir() else project.cache


def required_tools(project: ProjectConfig) -> tuple[str, ...]:
    raw = project.section("toolchain")
    values: list[str] = []
    for name in ("java", "maven", "node", "python", "uv"):
        if bool(raw.get(name, False)):
            values.append(name)
    manager = str(raw.get("package_manager", "")).strip()
    if manager and manager != "npm":
        values.append(manager)
    return tuple(dict.fromkeys(values))


def mise_inventory(project: ProjectConfig) -> dict[str, list[dict]]:
    if not uses_mise(project):
        return {}
    combined: dict[str, list[dict]] = {}
    seen: set[tuple[str, str, str]] = set()
    roots = configuration_roots(project, source=True)
    if project.cache.is_dir():
        roots.extend(root for root in configuration_roots(project) if root not in roots)
    for root in roots:
        result = run_mise(
            project,
            ["ls", "--current", "--json"],
            cwd=root,
            check=False,
            capture=True,
        )
        if result.returncode != 0:
            continue
        try:
            value = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict):
            continue
        for name, records in value.items():
            if not isinstance(records, list):
                continue
            for record in records:
                if not isinstance(record, dict):
                    continue
                source = record.get("source", {})
                source_path = str(source.get("path", "")) if isinstance(source, dict) else ""
                key = (str(name), str(record.get("version", "")), source_path)
                if key in seen:
                    continue
                seen.add(key)
                combined.setdefault(str(name), []).append(record)
    return combined


def missing_mise_tools(
    project: ProjectConfig,
    names: Sequence[str] | None = None,
) -> tuple[str, ...]:
    inventory = mise_inventory(project)
    selected = set(names or required_tools(project))
    values: list[str] = []
    for name, records in inventory.items():
        if name not in selected:
            continue
        for record in records:
            if not bool(record.get("installed", False)):
                version = str(record.get("version") or record.get("requested_version") or "unknown")
                values.append(f"{name}@{version}")
    return tuple(dict.fromkeys(values))


def install_mise_tools(
    project: ProjectConfig,
    tools: Sequence[str] | None = None,
) -> None:
    selected = tuple(dict.fromkeys(tools or required_tools(project)))
    if not selected:
        return
    for root in configuration_roots(project, source=True):
        result = run_mise(
            project,
            ["--yes", "install", *selected],
            cwd=root,
            check=False,
        )
        if result.returncode != 0:
            raise DevctlError(f"mise install failed with exit code {result.returncode}: {root}")
