from __future__ import annotations

import json
import os
import re
import shlex
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import validate_name
from .errors import DevctlError
from .process import run


COMPOSE_NAMES = ("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml")
IGNORED_DIRECTORIES = frozenset(
    {".git", ".next", ".turbo", "node_modules", "target", "dist", "build", ".venv"}
)
BASE_EXCLUDES = [
    "/.git/",
    "/.codegraph/",
    "**/node_modules/",
    "**/target/",
    "**/__pycache__/",
    "**/.venv/",
    "**/dist/",
    "**/build/",
]


@dataclass(frozen=True)
class Detection:
    source: Path
    runtime: str
    compose_file: Path | None = None
    package_file: Path | None = None
    framework: str | None = None
    package_manager: str | None = None
    frontend_port: int | None = None
    pom_file: Path | None = None
    spring_boot_pom: Path | None = None
    spring_boot: bool = False
    spring_boot_port: int = 8080
    spring_boot_profile: str | None = None
    spring_boot_version: str | None = None
    maven_watch: tuple[Path, ...] = ()
    classpath_modules: tuple[Path, ...] = ()
    pyproject_file: Path | None = None
    fastapi: bool = False

    def labels(self) -> list[str]:
        values = [self.runtime]
        for item in (self.framework, self.package_manager):
            if item:
                values.append(item)
        if self.spring_boot:
            values.append("spring-boot")
        elif self.pom_file:
            values.append("maven")
        if self.fastapi:
            values.append("fastapi")
        elif self.pyproject_file:
            values.append("python")
        return list(dict.fromkeys(values))


def normalize_source(value: str) -> Path:
    windows = re.fullmatch(r"([A-Za-z]):[\\/](.*)", value.strip())
    if windows:
        result = run(["wslpath", "-u", value], check=False, capture=True)
        if result.returncode == 0 and result.stdout.strip():
            value = result.stdout.strip()
        else:
            tail = windows.group(2).replace("\\", "/")
            value = f"/mnt/{windows.group(1).lower()}/{tail}"
    path = Path(value).expanduser().resolve(strict=False)
    if not path.is_dir():
        raise DevctlError(f"project source directory does not exist: {path}")
    return path


def default_name(source: Path) -> str:
    slug = re.sub(r"[^a-z0-9._-]+", "-", source.name.lower()).strip("-._")
    value = f"local-{slug or 'project'}"[:63]
    validate_name(value)
    return value


def _find(source: Path, names: set[str], max_depth: int = 3) -> list[Path]:
    result: list[Path] = []
    for current, directories, files in os.walk(source):
        current_path = Path(current)
        depth = len(current_path.relative_to(source).parts)
        directories[:] = [
            name
            for name in directories
            if name not in IGNORED_DIRECTORIES and depth < max_depth
        ]
        for name in files:
            if name in names:
                result.append(current_path / name)
    return sorted(result, key=lambda path: (len(path.relative_to(source).parts), str(path)))


def _package_details(path: Path, boundary: Path) -> tuple[str | None, str]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DevctlError(f"cannot inspect {path}: {exc}") from exc
    dependencies: dict[str, Any] = {}
    for key in ("dependencies", "devDependencies"):
        item = value.get(key, {})
        if isinstance(item, dict):
            dependencies.update(item)
    if "next" in dependencies:
        framework = "next"
    elif "vite" in dependencies:
        framework = "vite"
    elif "react-scripts" in dependencies:
        framework = "react-scripts"
    elif "react" in dependencies:
        framework = "react"
    else:
        framework = None
    current = path.parent
    while current == boundary or boundary in current.parents:
        package_path = current / "package.json"
        if package_path == path:
            package_value = value
        elif package_path.is_file():
            try:
                package_value = json.loads(package_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                package_value = {}
        else:
            package_value = {}
        configured = str(package_value.get("packageManager", "")).split("@", 1)[0]
        if configured in {"npm", "pnpm", "yarn", "bun"}:
            return framework, configured
        for filename, manager in (
            ("pnpm-lock.yaml", "pnpm"),
            ("yarn.lock", "yarn"),
            ("bun.lock", "bun"),
            ("bun.lockb", "bun"),
            ("package-lock.json", "npm"),
        ):
            if (current / filename).exists():
                return framework, manager
        if current == boundary:
            break
        current = current.parent
    return framework, "npm"


def _frontend_port(package_file: Path, framework: str | None) -> int | None:
    root = package_file.parent
    variable = "VITE_PORT" if framework == "vite" else "PORT"
    for path in (root / ".env.local", root / ".env.development", root / ".env"):
        if not path.is_file():
            continue
        match = re.search(
            rf"(?m)^\s*{re.escape(variable)}\s*=\s*['\"]?(\d+)",
            path.read_text(encoding="utf-8", errors="ignore"),
        )
        if match:
            return int(match.group(1))
    if framework != "vite":
        return None
    for path in sorted(root.glob("vite.config.*")):
        content = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in (
            r"['\"]VITE_PORT['\"]\s*,\s*['\"](\d+)['\"]",
            r"\bport\s*:\s*(\d+)",
        ):
            match = re.search(pattern, content)
            if match:
                return int(match.group(1))
    return None


def _xml_root(path: Path) -> ET.Element | None:
    try:
        return ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return None


def _direct_text(root: ET.Element, name: str) -> str | None:
    node = root.find(f"{{*}}{name}")
    if node is None or not node.text or not node.text.strip():
        return None
    return node.text.strip()


def _pom_details(path: Path) -> tuple[str | None, str, set[str]]:
    root = _xml_root(path)
    if root is None:
        return None, "jar", set()
    artifact = _direct_text(root, "artifactId")
    packaging = _direct_text(root, "packaging") or "jar"
    dependencies = {
        node.text.strip()
        for node in root.findall("./{*}dependencies/{*}dependency/{*}artifactId")
        if node.text and node.text.strip()
    }
    return artifact, packaging, dependencies


def _spring_boot_module(poms: list[Path]) -> Path | None:
    candidates = []
    marker = "<artifactId>spring-boot-maven-plugin</artifactId>"
    for path in poms:
        content = path.read_text(encoding="utf-8", errors="ignore")
        if marker not in content:
            continue
        has_application = any((path.parent / "src/main/resources").glob("application*"))
        candidates.append((not has_application, len(path.parts), str(path), path))
    return min(candidates)[-1] if candidates else None


def _spring_boot_properties(pom: Path) -> tuple[int, str | None]:
    resources = pom.parent / "src/main/resources"
    profile = "local" if any(resources.glob("application-local.*")) else None
    names = (
        "application-local.yaml",
        "application-local.yml",
        "application-dev.yaml",
        "application-dev.yml",
        "application.yaml",
        "application.yml",
        "application.properties",
    )
    for name in names:
        path = resources / name
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8", errors="ignore")
        if path.suffix == ".properties":
            match = re.search(r"(?m)^\s*server\.port\s*=\s*(\d+)", content)
        else:
            match = re.search(r"(?ms)^server:\s*\n(?:[ \t]+.*\n)*?[ \t]+port:\s*(\d+)", content)
        if match:
            return int(match.group(1)), profile
    return 8080, profile


def _spring_boot_version(poms: list[Path]) -> str | None:
    for path in poms:
        root = _xml_root(path)
        if root is None:
            continue
        node = root.find("./{*}properties/{*}spring.boot.version")
        if node is not None and node.text and node.text.strip():
            return node.text.strip()
    return None


def _maven_dependency_paths(
    poms: list[Path], boot_pom: Path, source: Path
) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    metadata = {path: _pom_details(path) for path in poms}
    by_artifact = {
        artifact: path
        for path, (artifact, _packaging, _dependencies) in metadata.items()
        if artifact
    }
    selected: set[Path] = {boot_pom}
    pending = [boot_pom]
    while pending:
        current = pending.pop()
        for artifact in metadata[current][2]:
            dependency = by_artifact.get(artifact)
            if dependency is not None and dependency not in selected:
                selected.add(dependency)
                pending.append(dependency)
    source_roots = tuple(
        path.parent / "src/main"
        for path in sorted(selected, key=str)
        if (path.parent / "src/main").is_dir()
    )
    classpath = tuple(
        path.parent / "target/classes"
        for path in sorted(selected - {boot_pom}, key=str)
        if metadata[path][1] != "pom" and (path.parent / "src/main").is_dir()
    )
    return (
        tuple(path.relative_to(source) for path in source_roots),
        tuple(path.relative_to(source) for path in classpath),
    )


def discover(source_value: str, runtime: str = "auto") -> Detection:
    source = normalize_source(source_value)
    compose_files = _find(source, set(COMPOSE_NAMES), max_depth=2)
    packages = _find(source, {"package.json"})
    poms = _find(source, {"pom.xml"})
    pyprojects = _find(source, {"pyproject.toml"})
    compose = compose_files[0] if compose_files else None
    package = None
    package_details: tuple[str | None, str] | None = None
    for candidate in packages:
        details = _package_details(candidate, source)
        if package is None:
            package, package_details = candidate, details
        if details[0] is not None:
            package, package_details = candidate, details
            break
    pom = poms[0] if poms else None
    boot_pom = _spring_boot_module(poms)
    pyproject = pyprojects[0] if pyprojects else None
    for candidate in pyprojects:
        if "fastapi" in candidate.read_text(encoding="utf-8", errors="ignore").lower():
            pyproject = candidate
            break
    framework = None
    package_manager = None
    if package_details:
        framework, package_manager = package_details
    frontend_port = _frontend_port(package, framework) if package else None
    spring_boot = boot_pom is not None
    spring_boot_port = 8080
    spring_boot_profile = None
    spring_boot_version = None
    maven_watch: tuple[Path, ...] = ()
    classpath_modules: tuple[Path, ...] = ()
    if pom and boot_pom:
        spring_boot_port, spring_boot_profile = _spring_boot_properties(boot_pom)
        spring_boot_version = _spring_boot_version(poms)
        maven_watch, classpath_modules = _maven_dependency_paths(poms, boot_pom, source)
    fastapi = False
    if pyproject:
        content = pyproject.read_text(encoding="utf-8", errors="ignore")
        fastapi = "fastapi" in content.lower()
    if runtime == "auto":
        selected = "compose" if compose else "host"
    elif runtime in {"host", "compose"}:
        selected = runtime
    else:
        raise DevctlError("runtime must be auto, host, or compose")
    if selected == "compose" and compose is None:
        raise DevctlError("Compose runtime requested, but no Compose file was found")
    return Detection(
        source=source,
        runtime=selected,
        compose_file=compose,
        package_file=package,
        framework=framework,
        package_manager=package_manager,
        frontend_port=frontend_port,
        pom_file=pom,
        spring_boot_pom=boot_pom,
        spring_boot=spring_boot,
        spring_boot_port=spring_boot_port,
        spring_boot_profile=spring_boot_profile,
        spring_boot_version=spring_boot_version,
        maven_watch=maven_watch,
        classpath_modules=classpath_modules,
        pyproject_file=pyproject,
        fastapi=fastapi,
    )


def _relative_directory(path: Path, source: Path) -> str:
    relative = path.parent.relative_to(source).as_posix()
    return relative or "."


def _node_commands(
    manager: str, framework: str | None, configured_port: int | None = None
) -> tuple[str, str, int]:
    if manager == "pnpm":
        executable, install = "corepack pnpm", "corepack pnpm install --frozen-lockfile"
    elif manager == "yarn":
        executable, install = "corepack yarn", "corepack yarn install --immutable"
    elif manager == "bun":
        executable, install = "bun", "bun install --frozen-lockfile"
    else:
        executable, install = "npm", "npm ci"
    if framework == "next":
        port = configured_port or 3000
        return install, f"{executable} run dev -- --hostname 0.0.0.0 --port {port}", port
    if framework == "vite":
        return install, f"{executable} run dev -- --host 0.0.0.0", configured_port or 5173
    if framework == "react-scripts":
        return install, f"{executable} run start", configured_port or 3000
    return install, f"{executable} run dev", configured_port or 3000


def build_project_config(detection: Detection, name: str, run_user: str) -> dict[str, Any]:
    validate_name(name)
    excludes = list(BASE_EXCLUDES)
    if detection.package_file:
        excludes.extend(["**/.next/", "**/.turbo/", "**/out/"])
    raw: dict[str, Any] = {
        "name": name,
        "run_user": run_user,
        "source": str(detection.source),
        "runtime": {"driver": detection.runtime},
        "sync": {"enabled": True, "interval_ms": 750, "exclude": excludes},
        "backend": {"enabled": False, "workdir": "."},
        "frontend": {"enabled": False, "workdir": "."},
        "compile": {"enabled": False, "workdir": "."},
        "branch_switch": {"enabled": True, "settle_ms": 1500, "timeout_seconds": 60},
        "checks": {"commands": ["git"]},
        "toolchain": {},
        "docker": {},
    }
    commands = raw["checks"]["commands"]
    if detection.runtime == "compose" and detection.compose_file:
        compose_root = detection.compose_file.parent
        raw["docker"] = {
            "compose": {
                "workdir": _relative_directory(detection.compose_file, detection.source),
                "files": [detection.compose_file.name],
                "build": True,
                "build_on_start": False,
                "pull": False,
                "remove_orphans": True,
                "stop_timeout_seconds": 20,
            }
        }
        raw["toolchain"] = {"docker": True}
        commands.append("docker")
        if compose_root != detection.source:
            raw["checks"]["paths"] = [str(detection.compose_file)]
        return raw
    if detection.package_file and detection.package_manager:
        prepare, start, port = _node_commands(
            detection.package_manager,
            detection.framework,
            detection.frontend_port,
        )
        frontend: dict[str, Any] = {
            "enabled": True,
            "workdir": _relative_directory(detection.package_file, detection.source),
            "port": port,
            "prepare": prepare,
            "run": start,
        }
        if detection.framework == "react-scripts":
            frontend["env"] = {"HOST": "0.0.0.0", "PORT": str(port)}
        raw["frontend"] = frontend
        raw["branch_switch"]["frontend_command"] = prepare
        raw["toolchain"].update(
            {"node": True, "package_manager": detection.package_manager}
        )
        package_command = (
            "corepack"
            if detection.package_manager in {"pnpm", "yarn"}
            else detection.package_manager
        )
        commands.extend(["node", package_command])
        raw["checks"]["tcp"] = [f"127.0.0.1:{port}"]
    if detection.pom_file:
        maven_workdir = _relative_directory(detection.pom_file, detection.source)
        raw["toolchain"].update({"java": True, "maven": True})
        commands.append("java")
        raw["java"] = {"maven": {"executable": "auto", "repository": "user"}}
        if detection.spring_boot and detection.spring_boot_pom:
            boot_directory = detection.spring_boot_pom.parent.relative_to(
                detection.pom_file.parent
            ).as_posix()
            selector = f"-pl {shlex.quote(boot_directory)} " if boot_directory != "." else ""
            profile = (
                f" -Dspring-boot.run.profiles={detection.spring_boot_profile}"
                if detection.spring_boot_profile
                else ""
            )
            extra_classpath = (
                ' -Dspring-boot.run.additional-classpath-elements="$WSL_DEV_EXTRA_CLASSPATH"'
                if detection.spring_boot_version
                else ""
            )
            raw["backend"] = {
                "enabled": True,
                "workdir": maven_workdir,
                "port": detection.spring_boot_port,
                "prepare": (
                    f'"$WSL_DEVCTL_MAVEN" -B -ntp {selector}-am '
                    "-DskipTests clean install"
                ),
                "run": (
                    f'"$WSL_DEVCTL_MAVEN" -B -ntp {selector}spring-boot:run'
                    f"{profile}{extra_classpath}"
                ),
            }
            raw["compile"] = {
                "enabled": True,
                "workdir": maven_workdir,
                "command": (
                    f'"$WSL_DEVCTL_MAVEN" -B -ntp {selector}-am -DskipTests compile'
                ),
                "resource_command": (
                    f'"$WSL_DEVCTL_MAVEN" -B -ntp {selector}-am -DskipTests install'
                ),
                "structural_command": (
                    f'"$WSL_DEVCTL_MAVEN" -B -ntp {selector}-am '
                    "-DskipTests clean install"
                ),
                "watch": [path.as_posix() for path in detection.maven_watch],
                "extensions": [".java", ".xml", ".yaml", ".yml", ".properties"],
                "resource_extensions": [".xml", ".yaml", ".yml", ".properties"],
                "structural": ["pom.xml", "**/pom.xml", ".mvn/**", "mvnw", "mvnw.cmd"],
            }
            if detection.spring_boot_version:
                raw["java"]["spring"] = {
                    "devtools": (
                        "org.springframework.boot:spring-boot-devtools:"
                        f"{detection.spring_boot_version}"
                    ),
                    "classpath_modules": [
                        path.as_posix() for path in detection.classpath_modules
                    ],
                    "classpath_entries": ["com"],
                }
            raw["branch_switch"]["backend_command"] = raw["backend"]["prepare"]
            raw["checks"].setdefault("tcp", []).append(
                f"127.0.0.1:{detection.spring_boot_port}"
            )
    if detection.pyproject_file and detection.fastapi and not raw["backend"]["enabled"]:
        python_workdir = _relative_directory(detection.pyproject_file, detection.source)
        use_uv = (detection.pyproject_file.parent / "uv.lock").exists()
        raw["toolchain"].update({"python": True, "uv": use_uv})
        if use_uv:
            prepare = "uv sync --frozen"
            start = "uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"
            commands.append("uv")
        else:
            prepare = "python3 -m pip install -e ."
            start = "python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"
            commands.append("python3")
        raw["backend"] = {
            "enabled": True,
            "workdir": python_workdir,
            "port": 8000,
            "prepare": prepare,
            "run": start,
        }
        raw["branch_switch"]["backend_command"] = prepare
        raw["checks"].setdefault("tcp", []).append("127.0.0.1:8000")
    raw["checks"]["commands"] = list(dict.fromkeys(commands))
    return raw
