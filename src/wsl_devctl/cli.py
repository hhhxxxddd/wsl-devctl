from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__
from .config import ProjectConfig, load_project, parse_project, read_toml
from .controller import (
    UNIT_KINDS,
    compile_once,
    enabled_units,
    prepare_all,
    save_git_state,
    unit,
    unit_active,
)
from .dependencies import apply_dependency_fixes, dependency_plan, describe_plan
from .discovery import build_project_config, default_name, discover
from .drivers.compose import compose_down, compose_healthy
from .drivers.spring import classpath_overlay
from .errors import DevctlError
from .gitstate import snapshot as git_snapshot
from .health import doctor, tcp_probe
from .paths import RuntimePaths
from .process import log, require_root, run, systemctl
from .state import branch_failure_path, read_json, resource_recovery_path
from .sync import ensure_cache, sync_once
from .tomlgen import render_toml
from .workers import dispatch_worker


def paths() -> RuntimePaths:
    return RuntimePaths.from_environment()


def _write_registration(
    runtime: RuntimePaths,
    project: ProjectConfig,
    content: str,
    *,
    force: bool,
) -> Path:
    runtime.config_dir.mkdir(parents=True, exist_ok=True)
    runtime.state_root.mkdir(parents=True, exist_ok=True)
    ensure_cache(project)
    destination = runtime.config_path(project.name)
    if destination.exists() and not force:
        raise DevctlError(f"project is already registered; use --force to replace: {project.name}")
    temporary = destination.with_suffix(".toml.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.chmod(0o644)
    os.replace(temporary, destination)
    runtime.project_state(project.name).mkdir(parents=True, exist_ok=True)
    systemctl("daemon-reload")
    return destination


def _start_project(runtime: RuntimePaths, project: ProjectConfig, *, prepare: bool) -> None:
    branch_failure = branch_failure_path(runtime, project.name)
    recovery = resource_recovery_path(runtime, project.name)
    if branch_failure.exists() and not prepare:
        raise DevctlError(
            f"branch rebuild failed; recover with: wsl-devctl start --prepare {project.name}"
        )
    if recovery.exists() and not prepare:
        raise DevctlError(
            f"recovery is pending; recover with: wsl-devctl start --prepare {project.name}"
        )
    if prepare:
        for kind in reversed(UNIT_KINDS):
            systemctl("stop", unit(project.name, kind), check=False)
    sync_once(project)
    if prepare:
        prepare_all(runtime, project)
        branch_failure.unlink(missing_ok=True)
        recovery.unlink(missing_ok=True)
    elif project.enabled("backend"):
        classpath_overlay(runtime, project)
    save_git_state(runtime, project, git_snapshot(project))
    order = enabled_units(project)
    for kind in order:
        systemctl("start", unit(project.name, kind))
    log(f"started {project.name}: {', '.join(order)}")
    for kind in ("backend", "frontend"):
        if project.enabled(kind):
            port = int(project.section(kind).get("port", 0) or 0)
            if port:
                log(f"{kind}: http://localhost:{port}/")
    if project.runtime_driver == "compose":
        log(f"inspect containers with: wsl-devctl status {project.name}")


def cmd_init(args: argparse.Namespace) -> None:
    detection = discover(args.source, args.runtime)
    name = args.name or default_name(detection.source)
    run_user = args.user or os.environ.get("SUDO_USER") or os.environ.get("USER") or ""
    if not run_user:
        raise DevctlError("cannot determine the project user; pass --user")
    raw = build_project_config(detection, name, run_user)
    content = render_toml(raw)
    project = parse_project(raw)
    if args.dry_run:
        print(f"Detected: {', '.join(detection.labels())}", file=sys.stderr)
        print(content, end="")
        return
    require_root("init")
    runtime = paths()
    destination = _write_registration(runtime, project, content, force=args.force)
    log(f"initialized {project.name}: {destination}")
    log(f"detected: {', '.join(detection.labels())}")
    if args.fix:
        remaining = apply_dependency_fixes(project)
        if not remaining.empty:
            for line in describe_plan(remaining):
                log(f"remaining dependency: {line}")
    else:
        plan = dependency_plan(project)
        for line in describe_plan(plan):
            log(f"dependency needed: {line}")
    if args.start:
        _start_project(runtime, project, prepare=True)


def cmd_register(args: argparse.Namespace) -> None:
    require_root("register")
    runtime = paths()
    source = Path(args.config).resolve(strict=True)
    raw = read_toml(source)
    project = parse_project(raw)
    content = source.read_text(encoding="utf-8")
    destination = _write_registration(runtime, project, content, force=args.force)
    log(f"registered {project.name}: {destination}")


def cmd_list(_args: argparse.Namespace) -> None:
    runtime = paths()
    for path in sorted(runtime.config_dir.glob("*.toml")):
        try:
            project = load_project(path.stem, runtime)
            states = [kind for kind in enabled_units(project) if unit_active(project.name, kind)]
            print(f"{project.name}\t{','.join(states) if states else 'stopped'}")
        except DevctlError as exc:
            print(f"{path.stem}\tINVALID: {exc}")


def cmd_sync(args: argparse.Namespace) -> None:
    project = load_project(args.name, paths())
    changed = sync_once(project, itemize=True)
    log("sync complete" + (" (changes applied)" if changed else " (already current)"))


def cmd_prepare(args: argparse.Namespace) -> None:
    require_root("prepare")
    runtime = paths()
    project = load_project(args.name, runtime)
    active = [kind for kind in UNIT_KINDS if unit_active(project.name, kind)]
    for kind in active:
        systemctl("stop", unit(project.name, kind), check=False)
    try:
        sync_once(project)
        prepare_all(runtime, project)
    except DevctlError:
        log("prepare failed; previously active runtime services remain stopped")
        raise
    save_git_state(runtime, project, git_snapshot(project))
    branch_failure_path(runtime, project.name).unlink(missing_ok=True)
    resource_recovery_path(runtime, project.name).unlink(missing_ok=True)
    for kind in enabled_units(project):
        if kind in active:
            systemctl("start", unit(project.name, kind))
    log("prepare complete")


def cmd_compile(args: argparse.Namespace) -> None:
    require_root("compile")
    runtime = paths()
    project = load_project(args.name, runtime)
    raise SystemExit(compile_once(runtime, project))


def cmd_up(args: argparse.Namespace) -> None:
    require_root("up")
    runtime = paths()
    project = load_project(args.name, runtime)
    _start_project(runtime, project, prepare=args.prepare)


def cmd_down(args: argparse.Namespace) -> None:
    require_root("down")
    project = load_project(args.name, paths())
    for kind in reversed(UNIT_KINDS):
        systemctl("stop", unit(project.name, kind), check=False)
    if project.runtime_driver == "compose":
        compose_down(project)
    log(f"stopped {project.name}")


def cmd_restart(args: argparse.Namespace) -> None:
    require_root("restart")
    runtime = paths()
    project = load_project(args.name, runtime)
    if resource_recovery_path(runtime, project.name).exists():
        raise DevctlError(f"recovery pending; use: wsl-devctl up --prepare {project.name}")
    sync_once(project)
    if project.runtime_driver == "compose":
        systemctl("stop", unit(project.name, "compose"), check=False)
        compose_down(project)
        systemctl("start", unit(project.name, "compose"))
    else:
        for kind in ("backend", "frontend"):
            if project.enabled(kind):
                systemctl("restart", unit(project.name, kind))
    log(f"restarted runtime services for {project.name}")


def cmd_status(args: argparse.Namespace) -> None:
    runtime = paths()
    project = load_project(args.name, runtime)
    kinds = enabled_units(project)
    result = run(
        ["systemctl", "--no-pager", "--full", "status", *[unit(project.name, kind) for kind in kinds]],
        check=False,
    )
    failures = 0
    print("\nRuntime health:")
    recovery = read_json(resource_recovery_path(runtime, project.name))
    if recovery is not None:
        failures += 1
        print(
            "FAIL recovery: "
            f"status={recovery.get('status', 'unknown')}, "
            f"attempts={recovery.get('attempts', 0)}"
        )
    for kind in ("backend", "frontend"):
        if not project.enabled(kind):
            continue
        active = unit_active(project.name, kind)
        port = int(project.section(kind).get("port", 0) or 0)
        reachable = not port or tcp_probe("127.0.0.1", port)
        healthy = active and reachable
        detail = f"active={str(active).lower()}"
        if port:
            detail += f", 127.0.0.1:{port}={'reachable' if reachable else 'unreachable'}"
        print(f"{'PASS' if healthy else 'FAIL'} {kind}: {detail}")
        failures += int(not healthy)
    if project.runtime_driver == "compose":
        active = unit_active(project.name, "compose")
        healthy, detail = compose_healthy(project)
        ok = active and healthy
        print(f"{'PASS' if ok else 'FAIL'} compose: active={str(active).lower()}, {detail}")
        failures += int(not ok)
    raise SystemExit(1 if failures else result.returncode)


def cmd_logs(args: argparse.Namespace) -> None:
    project = load_project(args.name, paths())
    command = ["journalctl"]
    for kind in enabled_units(project):
        command.extend(["-u", unit(project.name, kind)])
    command.extend(["-n", str(args.lines), "--no-pager"])
    if args.follow:
        command.append("--follow")
    os.execvp(command[0], command)


def cmd_show(args: argparse.Namespace) -> None:
    runtime = paths()
    project = load_project(args.name, runtime)
    print(runtime.config_path(project.name).read_text(encoding="utf-8"), end="")


def cmd_doctor(args: argparse.Namespace) -> None:
    project = load_project(args.name, paths())
    if args.fix:
        require_root("doctor --fix")
        apply_dependency_fixes(project)
    raise SystemExit(doctor(project))


def cmd_worker(args: argparse.Namespace) -> None:
    require_root("system worker")
    runtime = paths()
    dispatch_worker(runtime, load_project(args.name, runtime), args.kind)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="wsl-devctl",
        description="Run Windows-hosted projects from WSL ext4 build caches.",
    )
    result.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = result.add_subparsers(dest="command", required=True)
    initialize = sub.add_parser("init", help="detect, configure, and optionally start a project")
    initialize.add_argument("source")
    initialize.add_argument("--name")
    initialize.add_argument("--user")
    initialize.add_argument("--runtime", choices=("auto", "host", "compose"), default="auto")
    initialize.add_argument("--start", action="store_true")
    initialize.add_argument("--fix", action="store_true")
    initialize.add_argument("--force", action="store_true")
    initialize.add_argument("--dry-run", action="store_true")
    initialize.set_defaults(func=cmd_init)
    register = sub.add_parser("register", help="validate and install a TOML project config")
    register.add_argument("config")
    register.add_argument("--force", action="store_true")
    register.set_defaults(func=cmd_register)
    listing = sub.add_parser("list", help="list registered projects")
    listing.set_defaults(func=cmd_list)
    for name, func in (
        ("sync", cmd_sync),
        ("prepare", cmd_prepare),
        ("compile", cmd_compile),
        ("down", cmd_down),
        ("restart", cmd_restart),
        ("status", cmd_status),
        ("show", cmd_show),
    ):
        command = sub.add_parser(name)
        command.add_argument("name")
        command.set_defaults(func=func)
    up = sub.add_parser("up")
    up.add_argument("name")
    up.add_argument("--prepare", action="store_true")
    up.set_defaults(func=cmd_up)
    start = sub.add_parser("start", help="start a project")
    start.add_argument("name")
    start.add_argument("--prepare", action="store_true")
    start.set_defaults(func=cmd_up)
    stop = sub.add_parser("stop", help="stop a project")
    stop.add_argument("name")
    stop.set_defaults(func=cmd_down)
    doctor_parser = sub.add_parser("doctor")
    doctor_parser.add_argument("name")
    doctor_parser.add_argument("--fix", action="store_true")
    doctor_parser.set_defaults(func=cmd_doctor)
    logs = sub.add_parser("logs")
    logs.add_argument("name")
    logs.add_argument("--follow", "-f", action="store_true")
    logs.add_argument("--lines", "-n", type=int, default=100)
    logs.set_defaults(func=cmd_logs)
    worker = sub.add_parser("_worker")
    worker.add_argument("name")
    worker.add_argument("kind", choices=UNIT_KINDS)
    worker.set_defaults(func=cmd_worker)
    return result


def main() -> None:
    try:
        args = parser().parse_args()
        args.func(args)
    except DevctlError as exc:
        print(f"wsl-devctl: {exc}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
