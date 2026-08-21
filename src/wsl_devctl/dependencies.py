from __future__ import annotations

import os
import shutil
from dataclasses import dataclass

from .config import ProjectConfig
from .drivers.maven import resolve_maven
from .errors import DevctlError
from .process import log, run, run_as_user


@dataclass(frozen=True)
class DependencyPlan:
    apt_packages: tuple[str, ...]
    install_corepack: bool
    enable_docker: bool
    add_docker_group: bool
    unresolved: tuple[str, ...]

    @property
    def empty(self) -> bool:
        return not (
            self.apt_packages
            or self.install_corepack
            or self.enable_docker
            or self.add_docker_group
            or self.unresolved
        )


def _compose_available(project: ProjectConfig) -> bool:
    if shutil.which("docker") is None:
        return False
    result = run_as_user(
        project.run_user,
        ["docker", "compose", "version"],
        cwd=project.source,
        check=False,
        capture=True,
    )
    return result.returncode == 0


def dependency_plan(project: ProjectConfig) -> DependencyPlan:
    toolchain = project.section("toolchain")
    packages: list[str] = []
    unresolved: list[str] = []
    if shutil.which("git") is None:
        packages.append("git")
    if shutil.which("rsync") is None:
        packages.append("rsync")
    if shutil.which("ss") is None:
        packages.append("iproute2")
    if shutil.which("runuser") is None:
        packages.append("util-linux")
    if bool(toolchain.get("java", False)) and shutil.which("java") is None:
        packages.append("default-jdk")
    maven = resolve_maven(project)
    if bool(toolchain.get("maven", False)) and maven and maven.executable == "mvn":
        if shutil.which("mvn") is None:
            packages.append("maven")
    if bool(toolchain.get("node", False)) and shutil.which("node") is None:
        packages.extend(["nodejs", "npm"])
    manager = str(toolchain.get("package_manager", ""))
    needs_corepack = manager in {"pnpm", "yarn"} and shutil.which("corepack") is None
    if manager == "bun" and shutil.which("bun") is None:
        unresolved.append(
            "Bun is required; automatic Bun installation is intentionally unsupported"
        )
    if bool(toolchain.get("python", False)) and shutil.which("python3") is None:
        packages.extend(["python3", "python3-venv", "python3-pip"])
    if bool(toolchain.get("uv", False)) and shutil.which("uv") is None:
        unresolved.append("uv is required; install it from the official uv distribution")
    docker_requested = project.runtime_driver == "compose" or bool(toolchain.get("docker", False))
    docker_missing = docker_requested and shutil.which("docker") is None
    compose_missing = docker_requested and not docker_missing and not _compose_available(project)
    if docker_missing:
        packages.extend(["docker.io", "docker-compose-v2"])
    elif compose_missing:
        packages.append("docker-compose-v2")
    add_group = False
    start_docker = docker_missing
    if docker_requested and not docker_missing:
        info = run_as_user(
            project.run_user,
            ["docker", "info"],
            cwd=project.source,
            check=False,
            capture=True,
        )
        detail = (info.stderr or "").lower()
        add_group = info.returncode != 0 and "permission denied" in detail
        if info.returncode != 0 and not add_group:
            service = run(
                ["systemctl", "cat", "docker.service"],
                check=False,
                capture=True,
            )
            if service.returncode == 0:
                start_docker = True
            else:
                unresolved.append(
                    "Docker CLI is present but its engine is unreachable; enable Docker Desktop "
                    "WSL integration or install a native Docker engine"
                )
    return DependencyPlan(
        apt_packages=tuple(dict.fromkeys(packages)),
        install_corepack=needs_corepack,
        enable_docker=start_docker,
        add_docker_group=add_group or docker_missing,
        unresolved=tuple(unresolved),
    )


def describe_plan(plan: DependencyPlan) -> list[str]:
    values: list[str] = []
    if plan.apt_packages:
        values.append("APT packages: " + ", ".join(plan.apt_packages))
    if plan.install_corepack:
        values.append("Node global tool: corepack")
    if plan.enable_docker:
        values.append("Docker Engine service will be enabled and started")
    if plan.add_docker_group:
        values.append("The project user will be added to the docker group")
    values.extend(plan.unresolved)
    return values


def apply_dependency_fixes(project: ProjectConfig) -> DependencyPlan:
    plan = dependency_plan(project)
    for line in describe_plan(plan):
        log(f"dependency fix: {line}")
    if plan.apt_packages:
        if shutil.which("apt-get") is None:
            raise DevctlError("automatic dependency installation currently requires apt-get")
        apt_environment = dict(os.environ)
        apt_environment["DEBIAN_FRONTEND"] = "noninteractive"
        run(["apt-get", "update"], env=apt_environment)
        run(
            ["apt-get", "install", "-y", *plan.apt_packages],
            env=apt_environment,
        )
    if plan.install_corepack:
        if shutil.which("npm") is None:
            raise DevctlError("npm is unavailable after dependency installation")
        run(["npm", "install", "--global", "corepack"])
        run(["corepack", "enable"])
    if plan.enable_docker:
        run(["systemctl", "enable", "--now", "docker"])
    if plan.add_docker_group:
        run(["usermod", "-aG", "docker", project.run_user])
        log("Docker group membership changed; start a new WSL login before using Docker")
    if plan.unresolved:
        raise DevctlError("automatic fixes incomplete: " + "; ".join(plan.unresolved))
    return dependency_plan(project)
