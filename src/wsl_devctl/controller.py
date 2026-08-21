from __future__ import annotations

import fcntl

from .config import ProjectConfig
from .drivers.compose import compose_prepare
from .drivers.spring import classpath_overlay, java_environment, signal_reload
from .errors import DevctlError
from .gitstate import GitState
from .paths import RuntimePaths
from .process import exec_shell_as_user, log, shell_as_user, systemctl
from .state import (
    branch_failure_path,
    read_json,
    resource_recovery_path,
    save_recovery,
    source_state_path,
    write_json,
)
from .sync import sync_once
from .watcher import ChangeKind


UNIT_KINDS = ("sync", "compile", "backend", "frontend", "compose")


def unit(name: str, kind: str) -> str:
    return f"wsl-dev-{kind}@{name}.service"


def unit_active(name: str, kind: str) -> bool:
    return systemctl("is-active", "--quiet", unit(name, kind), check=False).returncode == 0


def runtime_environment(project: ProjectConfig, kind: str) -> dict[str, str]:
    result = project.environment(kind)
    result.update(java_environment(project))
    return result


def run_prepare(paths: RuntimePaths, project: ProjectConfig, kind: str) -> None:
    command = str(project.section(kind).get("prepare", "")).strip()
    if command:
        shell_as_user(
            project.run_user,
            command,
            cwd=project.workdir(kind),
            env=runtime_environment(project, kind),
        )
    if kind == "backend":
        classpath_overlay(paths, project)


def prepare_all(paths: RuntimePaths, project: ProjectConfig) -> None:
    if project.runtime_driver == "compose":
        compose_prepare(project)
        return
    for kind in ("backend", "frontend"):
        if project.enabled(kind):
            run_prepare(paths, project, kind)


def _compile_lock(paths: RuntimePaths, project: ProjectConfig):
    state = paths.project_state(project.name)
    state.mkdir(parents=True, exist_ok=True)
    return (state / "compile.lock").open("a+")


def compile_once(
    paths: RuntimePaths,
    project: ProjectConfig,
    *,
    command: str | None = None,
    reload_after: bool = True,
) -> int:
    selected = str(command or project.section("compile").get("command", "")).strip()
    if not selected:
        raise DevctlError("compile.command is not configured")
    with _compile_lock(paths, project) as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log("compile already running; change is coalesced")
            return 0
        result = shell_as_user(
            project.run_user,
            selected,
            cwd=project.workdir("compile"),
            env=runtime_environment(project, "backend"),
            check=False,
        )
        if result == 0 and reload_after:
            signal_reload(paths, project)
        return result


def quiesced_build(
    paths: RuntimePaths,
    project: ProjectConfig,
    command: str,
    *,
    reason: str,
) -> int:
    """Build repository-backed artifacts with the backend stopped."""

    with _compile_lock(paths, project) as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        pending = read_json(resource_recovery_path(paths, project.name)) or {}
        backend_was_active = unit_active(project.name, "backend")
        restore = bool(backend_was_active or pending.get("restart_backend", False))
        save_recovery(
            paths,
            project.name,
            restart_backend=restore,
            status=f"{reason}-building",
        )
        if backend_was_active:
            log(f"stopping backend before {reason} build")
            systemctl("stop", unit(project.name, "backend"), check=False)
        result = shell_as_user(
            project.run_user,
            command,
            cwd=project.workdir("compile"),
            env=runtime_environment(project, "backend"),
            check=False,
        )
        if result != 0:
            save_recovery(
                paths,
                project.name,
                restart_backend=restore,
                status=f"{reason}-failed",
                exit_code=result,
            )
            log(f"{reason} build failed; backend remains stopped")
            return result
        classpath_overlay(paths, project)
        if restore:
            started = systemctl("start", unit(project.name, "backend"), check=False)
            if started.returncode != 0:
                save_recovery(
                    paths,
                    project.name,
                    restart_backend=True,
                    status="backend-start-failed",
                    exit_code=started.returncode,
                )
                return started.returncode or 1
        resource_recovery_path(paths, project.name).unlink(missing_ok=True)
        return 0


def build_for_change(
    paths: RuntimePaths,
    project: ProjectConfig,
    kind: ChangeKind,
) -> int:
    options = project.section("compile")
    if kind == ChangeKind.SOURCE:
        return compile_once(paths, project)
    if kind == ChangeKind.RESOURCE:
        command = str(options.get("resource_command", "")).strip()
        if not command:
            return compile_once(paths, project)
        return quiesced_build(paths, project, command, reason="resource")
    command = str(options.get("structural_command") or options.get("resource_command", "")).strip()
    if not command:
        raise DevctlError(
            "compile.structural_command is required for deletion or build-structure changes"
        )
    return quiesced_build(paths, project, command, reason="structural")


def run_worker_process(paths: RuntimePaths, project: ProjectConfig, kind: str) -> None:
    command = str(project.section(kind).get("run", "")).strip()
    if not command:
        raise DevctlError(f"{kind}.run is not configured")
    if kind == "backend":
        recovery = read_json(resource_recovery_path(paths, project.name))
        if recovery is not None:
            raise DevctlError(
                "backend start blocked while recovery is pending "
                f"(status={recovery.get('status', 'unknown')})"
            )
        environment = runtime_environment(project, kind)
        environment["WSL_DEV_EXTRA_CLASSPATH"] = classpath_overlay(paths, project)
    else:
        environment = runtime_environment(project, kind)
    exec_shell_as_user(
        project.run_user,
        command,
        cwd=project.workdir(kind),
        env=environment,
    )


def save_git_state(paths: RuntimePaths, project: ProjectConfig, value: GitState | None) -> None:
    if value is not None:
        write_json(source_state_path(paths, project.name), value)


def rebuild_after_branch_switch(
    paths: RuntimePaths,
    project: ProjectConfig,
    previous: GitState,
    current: GitState,
) -> bool:
    runtime_kinds = ("compose",) if project.runtime_driver == "compose" else (
        "compile",
        "frontend",
        "backend",
    )
    active_before = {kind: unit_active(project.name, kind) for kind in runtime_kinds}
    label = (
        f"{previous['branch']}@{previous['head'][:12]} -> "
        f"{current['branch']}@{current['head'][:12]}"
    )
    log(f"Git branch transition detected: {label}")
    for kind in runtime_kinds:
        if active_before.get(kind, False):
            systemctl("stop", unit(project.name, kind), check=False)
    try:
        sync_once(project, itemize=True)
        if project.runtime_driver == "compose":
            compose_prepare(project)
        else:
            branch = project.section("branch_switch")
            for kind in ("backend", "frontend"):
                if not project.enabled(kind):
                    continue
                command = str(branch.get(f"{kind}_command", "")).strip()
                if command:
                    shell_as_user(
                        project.run_user,
                        command,
                        cwd=project.workdir(kind),
                        env=runtime_environment(project, kind),
                    )
                    if kind == "backend":
                        classpath_overlay(paths, project)
                else:
                    run_prepare(paths, project, kind)
        for kind in reversed(runtime_kinds):
            if active_before.get(kind, False):
                systemctl("start", unit(project.name, kind))
    except DevctlError as exc:
        failure = branch_failure_path(paths, project.name)
        failure.parent.mkdir(parents=True, exist_ok=True)
        failure.write_text(
            f"{label}\nrebuild failed: {exc}\n"
            f"recover with: wsl-devctl up --prepare {project.name}\n",
            encoding="utf-8",
        )
        save_git_state(paths, project, current)
        log(f"branch rebuild failed; runtime remains stopped: {failure}")
        return False
    branch_failure_path(paths, project.name).unlink(missing_ok=True)
    resource_recovery_path(paths, project.name).unlink(missing_ok=True)
    save_git_state(paths, project, current)
    log("branch rebuild complete; previous runtime state restored")
    return True


def enabled_units(project: ProjectConfig) -> list[str]:
    if project.runtime_driver == "compose":
        order = ("sync", "compose")
    else:
        order = ("sync", "backend", "frontend", "compile")
    return [kind for kind in order if project.enabled(kind)]
