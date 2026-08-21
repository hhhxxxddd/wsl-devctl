from __future__ import annotations

import shutil
import socket
import urllib.error
import urllib.request
from pathlib import Path

from .config import ProjectConfig, user_home
from .controller import unit_active
from .drivers.compose import compose_root
from .drivers.maven import maven_executable_exists, resolve_maven
from .drivers.spring import artifact_jar, spring_config
from .gitstate import snapshot as git_snapshot
from .process import run, run_as_user


def tcp_probe(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def http_probe(url: str, timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= response.status < 400
    except (OSError, urllib.error.URLError):
        return False


def listening_ports() -> set[int]:
    result = run(["ss", "-H", "-lnt"], capture=True, check=False)
    ports: set[int] = set()
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) < 4:
            continue
        try:
            ports.add(int(fields[3].rsplit(":", 1)[1]))
        except (IndexError, ValueError):
            continue
    return ports


def doctor(project: ProjectConfig) -> int:
    failures = 0

    def report(ok: bool, label: str, detail: str) -> None:
        nonlocal failures
        print(f"{'PASS' if ok else 'FAIL'} {label}: {detail}")
        failures += int(not ok)

    report(project.source.is_dir(), "source", str(project.source))
    report(project.cache.is_dir(), "cache", str(project.cache))
    for command in dict.fromkeys(
        ["bash", "rsync", "systemctl", "journalctl", "ss", "runuser"]
        + [str(value) for value in project.section("checks").get("commands", [])]
    ):
        ok = Path(command).is_file() if "/" in command else shutil.which(command) is not None
        report(ok, "command", command)
    if project.section("branch_switch").get("enabled", True):
        state = git_snapshot(project)
        detail = f"{state['branch']}@{state['head'][:12]}" if state else "not a readable Git worktree"
        report(state is not None, "git branch tracking", detail)
    maven = resolve_maven(project)
    if maven:
        report(maven_executable_exists(maven, project), "Maven executable", maven.executable)
        ancestor = maven.repository
        while not ancestor.exists() and ancestor != ancestor.parent:
            ancestor = ancestor.parent
        write_target = maven.repository if maven.repository.is_dir() else ancestor
        check_cwd = project.source if project.source.is_dir() else user_home(project.run_user)
        repository_ok = (
            write_target.is_dir()
            and run_as_user(
                project.run_user,
                ["test", "-w", str(write_target)],
                cwd=check_cwd,
                check=False,
            ).returncode
            == 0
        )
        report(repository_ok, "Maven repository", str(maven.repository))
        coordinate = str(spring_config(project).get("devtools", "")).strip()
        if coordinate:
            jar = artifact_jar(maven.repository, coordinate)
            report(jar.is_file(), "Spring DevTools artifact", str(jar))
    if project.runtime_driver == "compose":
        root = compose_root(project)
        report(root.is_dir(), "Compose workdir", str(root))
        for value in project.compose().get("files", ["compose.yaml"]):
            compose_file = root / str(value)
            report(compose_file.is_file(), "Compose file", str(compose_file))
        if shutil.which("docker"):
            version = run_as_user(
                project.run_user,
                ["docker", "compose", "version"],
                cwd=project.source,
                check=False,
                capture=True,
            )
        else:
            version = None
        report(
            version is not None and version.returncode == 0,
            "Docker Compose",
            (version.stdout.strip() if version and version.stdout.strip() else "unavailable"),
        )
        if shutil.which("docker"):
            info = run_as_user(
                project.run_user,
                ["docker", "info"],
                cwd=project.source,
                check=False,
                capture=True,
            )
        else:
            info = None
        report(
            info is not None and info.returncode == 0,
            "Docker engine",
            "reachable by project user" if info and info.returncode == 0 else "unreachable",
        )
    occupied = listening_ports()
    for kind in ("backend", "frontend"):
        port = int(project.section(kind).get("port", 0) or 0)
        if not port:
            continue
        own = unit_active(project.name, kind)
        report(port not in occupied or own, f"port {port}", "owned by project" if own else "free")
    checks = project.section("checks")
    for value in checks.get("tcp", []):
        host, raw_port = str(value).rsplit(":", 1)
        report(tcp_probe(host, int(raw_port)), "tcp", f"{host}:{raw_port}")
    for value in checks.get("http", []):
        report(http_probe(str(value)), "http", str(value))
    for value in checks.get("paths", []):
        report(Path(str(value)).exists(), "path", str(value))
    return 1 if failures else 0
