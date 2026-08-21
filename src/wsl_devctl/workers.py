from __future__ import annotations

import signal
import time
from threading import Event

from .config import ProjectConfig
from .controller import (
    build_for_change,
    rebuild_after_branch_switch,
    run_worker_process,
    save_git_state,
)
from .drivers.compose import compose_exec
from .errors import DevctlError
from .gitstate import busy as git_busy
from .gitstate import rebuild_required, snapshot as git_snapshot, wait_for_quiet
from .paths import RuntimePaths
from .process import log
from .state import read_json, resource_recovery_path, source_state_path
from .sync import sync_once
from .watcher import (
    ChangeKind,
    classify_changes,
    configured_roots,
    structural_snapshot,
    tree_snapshot,
)


STOP = Event()


def install_signal_handlers() -> None:
    def stop(_signum: int, _frame: object) -> None:
        STOP.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)


def worker_sync(paths: RuntimePaths, project: ProjectConfig) -> None:
    interval = max(0.25, float(project.section("sync").get("interval_ms", 750)) / 1000)
    log(f"sync worker started: {project.source} -> {project.cache} ({interval:.2f}s)")
    previous_git = read_json(source_state_path(paths, project.name))
    initial = git_snapshot(project)
    if not previous_git or "branch" not in previous_git or "head" not in previous_git:
        previous_git = initial
        save_git_state(paths, project, initial)
    while not STOP.is_set():
        try:
            if git_busy(project):
                STOP.wait(interval)
                continue
            current = git_snapshot(project)
            if previous_git is not None and current is not None:
                enabled = bool(project.section("branch_switch").get("enabled", True))
                if enabled and rebuild_required(previous_git, current):
                    settled = wait_for_quiet(project, stopped=STOP.is_set)
                    if settled is None:
                        log("Git did not become stable before timeout; sync is deferred")
                        STOP.wait(interval)
                        continue
                    rebuild_after_branch_switch(paths, project, previous_git, settled)
                    previous_git = settled
                    STOP.wait(interval)
                    continue
            sync_once(project, itemize=True)
            if current != previous_git:
                save_git_state(paths, project, current)
                previous_git = current
        except DevctlError as exc:
            log(f"sync failed; retrying: {exc}")
        STOP.wait(interval)


def _recovery_kind(paths: RuntimePaths, project: ProjectConfig) -> ChangeKind | None:
    recovery = read_json(resource_recovery_path(paths, project.name))
    if recovery is None:
        return None
    status = str(recovery.get("status", ""))
    return ChangeKind.STRUCTURAL if status.startswith("structural") else ChangeKind.RESOURCE


def worker_compile(paths: RuntimePaths, project: ProjectConfig) -> None:
    options = project.section("compile")
    roots = configured_roots(project)
    if not roots:
        raise DevctlError("compile.watch is empty")
    suffixes = {
        str(value).lower()
        for value in options.get("extensions", [".java", ".xml", ".yaml", ".yml", ".properties"])
    }
    resources = {
        str(value).lower()
        for value in options.get("resource_extensions", [".xml", ".yaml", ".yml", ".properties"])
    }
    excludes = tuple(str(value) for value in options.get("exclude", []))
    poll = max(0.25, float(options.get("poll_ms", 500)) / 1000)
    debounce = max(0.1, float(options.get("debounce_ms", 1000)) / 1000)
    recovery_retry = max(5.0, float(options.get("recovery_retry_seconds", 30)))
    next_recovery = time.monotonic()
    previous = tree_snapshot(roots, suffixes, excludes)
    previous_structural = structural_snapshot(project)
    log(f"compile worker watching {len(previous)} files below {len(roots)} roots")
    while not STOP.wait(poll):
        current = tree_snapshot(roots, suffixes, excludes)
        current_structural = structural_snapshot(project)
        recovery_kind = _recovery_kind(paths, project)
        if current == previous and current_structural == previous_structural:
            if recovery_kind is not None and time.monotonic() >= next_recovery:
                log(f"{recovery_kind.value} recovery pending; retrying")
                result = build_for_change(paths, project, recovery_kind)
                log(f"recovery finished with exit code {result}")
                next_recovery = time.monotonic() + recovery_retry
            continue
        baseline = previous
        structural_baseline = previous_structural
        if STOP.wait(debounce):
            break
        current = tree_snapshot(roots, suffixes, excludes)
        current_structural = structural_snapshot(project)
        previous = current
        previous_structural = current_structural
        change = classify_changes(
            baseline,
            current,
            structural_baseline,
            current_structural,
            resources,
        )
        if recovery_kind is not None:
            kind = recovery_kind
        elif change is None:
            continue
        else:
            kind, changed = change
            log(f"{kind.value} change detected in {len(changed)} path(s)")
        result = build_for_change(paths, project, kind)
        log(f"{kind.value} build finished with exit code {result}")
        next_recovery = time.monotonic() + recovery_retry


def dispatch_worker(paths: RuntimePaths, project: ProjectConfig, kind: str) -> None:
    install_signal_handlers()
    if kind == "sync":
        worker_sync(paths, project)
    elif kind == "compile":
        worker_compile(paths, project)
    elif kind == "compose":
        compose_exec(project)
    else:
        run_worker_process(paths, project, kind)
