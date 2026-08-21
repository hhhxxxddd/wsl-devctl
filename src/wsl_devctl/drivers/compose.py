from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any

from ..config import ProjectConfig, require_within
from ..errors import DevctlError
from ..process import exec_shell_as_user, run_as_user, shell_as_user


def compose_root(project: ProjectConfig) -> Path:
    relative = Path(str(project.compose().get("workdir", ".")))
    return require_within(project.cache / relative, project.cache, "docker.compose.workdir")


def compose_environment(project: ProjectConfig) -> dict[str, str]:
    value = project.compose().get("env", {})
    return {str(key): str(item) for key, item in value.items()}


def compose_project_name(project: ProjectConfig) -> str:
    configured = str(project.compose().get("project_name", "")).strip()
    if configured:
        value = configured
    else:
        value = f"wsl-dev-{project.name}"
    value = re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-_")
    if not value:
        raise DevctlError("docker.compose.project_name does not contain a usable name")
    return value[:63]


def compose_command(project: ProjectConfig, *arguments: str) -> list[str]:
    root = compose_root(project)
    command = ["docker", "compose", "--project-name", compose_project_name(project)]
    for value in project.compose().get("files", ["compose.yaml"]):
        path = require_within(root / str(value), root, "docker.compose.files entry")
        command.extend(["--file", str(path)])
    for value in project.compose().get("profiles", []):
        command.extend(["--profile", str(value)])
    command.extend(arguments)
    return command


def compose_prepare(project: ProjectConfig) -> None:
    options = project.compose()
    root = compose_root(project)
    environment = compose_environment(project)
    if bool(options.get("pull", False)):
        result = run_as_user(
            project.run_user,
            compose_command(project, "pull"),
            cwd=root,
            env=environment,
            check=False,
        )
        if result.returncode != 0:
            raise DevctlError(f"docker compose pull failed with exit code {result.returncode}")
    if bool(options.get("build", True)):
        result = run_as_user(
            project.run_user,
            compose_command(project, "build"),
            cwd=root,
            env=environment,
            check=False,
        )
        if result.returncode != 0:
            raise DevctlError(f"docker compose build failed with exit code {result.returncode}")


def compose_exec(project: ProjectConfig) -> None:
    arguments = ["up"]
    if bool(project.compose().get("build_on_start", False)):
        arguments.append("--build")
    if bool(project.compose().get("remove_orphans", True)):
        arguments.append("--remove-orphans")
    command = shlex.join(compose_command(project, *arguments))
    exec_shell_as_user(
        project.run_user,
        command,
        cwd=compose_root(project),
        env=compose_environment(project),
    )


def compose_down(project: ProjectConfig) -> int:
    arguments = ["down"]
    if bool(project.compose().get("remove_orphans", True)):
        arguments.append("--remove-orphans")
    timeout = int(project.compose().get("stop_timeout_seconds", 20))
    arguments.extend(["--timeout", str(max(1, timeout))])
    return shell_as_user(
        project.run_user,
        shlex.join(compose_command(project, *arguments)),
        cwd=compose_root(project),
        env=compose_environment(project),
        check=False,
    )


def compose_ps(project: ProjectConfig) -> list[dict[str, Any]]:
    result = run_as_user(
        project.run_user,
        compose_command(project, "ps", "--format", "json"),
        cwd=compose_root(project),
        env=compose_environment(project),
        check=False,
        capture=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []
    text = result.stdout.strip()
    try:
        value = json.loads(text)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            return [value]
    except json.JSONDecodeError:
        pass
    values: list[dict[str, Any]] = []
    for line in text.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            values.append(item)
    return values


def compose_healthy(project: ProjectConfig) -> tuple[bool, str]:
    services = compose_ps(project)
    if not services:
        return False, "no Compose containers found"
    failed: list[str] = []
    for service in services:
        name = str(service.get("Service") or service.get("Name") or "unknown")
        state = str(service.get("State", "")).lower()
        health = str(service.get("Health", "")).lower()
        if state != "running" or health in {"unhealthy", "starting"}:
            failed.append(f"{name}:{state or 'unknown'}/{health or 'no-healthcheck'}")
    if failed:
        return False, ", ".join(failed)
    return True, f"{len(services)} container(s) running"
