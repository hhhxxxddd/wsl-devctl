from __future__ import annotations

import os
import pwd
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Mapping, Sequence

from .errors import DevctlError


def log(message: str) -> None:
    print(message, flush=True)


def run(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    capture: bool = False,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(command),
        cwd=cwd,
        check=False,
        text=True,
        capture_output=capture,
        env=dict(env) if env is not None else None,
    )
    if check and result.returncode != 0:
        rendered = shlex.join(command)
        raise DevctlError(f"command failed with exit code {result.returncode}: {rendered}")
    return result


def user_environment(user: str, additions: Mapping[str, str] | None = None) -> dict[str, str]:
    account = pwd.getpwnam(user)
    environment = {
        "HOME": account.pw_dir,
        "USER": user,
        "LOGNAME": user,
        "SHELL": account.pw_shell or "/bin/bash",
        "PATH": os.environ.get(
            "PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        ),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }
    for key in ("http_proxy", "https_proxy", "no_proxy", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"):
        if key in os.environ:
            environment[key] = os.environ[key]
    if additions:
        environment.update({str(key): str(value) for key, value in additions.items()})
    return environment


def _identity_command(user: str, command: Sequence[str], env: Mapping[str, str]) -> list[str]:
    assignments = [f"{key}={value}" for key, value in env.items()]
    if os.geteuid() == 0 and user != "root":
        if shutil.which("runuser") is None:
            raise DevctlError("runuser is required to execute project workloads without root")
        return ["runuser", "-u", user, "--", "/usr/bin/env", *assignments, *command]
    current = pwd.getpwuid(os.geteuid()).pw_name
    if current != user:
        raise DevctlError(f"run as root or configured project user {user}; current user is {current}")
    return ["/usr/bin/env", *assignments, *command]


def run_as_user(
    user: str,
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    check: bool = True,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    project_env = user_environment(user, env)
    return run(
        _identity_command(user, command, project_env),
        cwd=cwd,
        check=check,
        capture=capture,
    )


def shell_as_user(
    user: str,
    command: str,
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    check: bool = True,
) -> int:
    log(f"+ [{user}] ({cwd}) {command}")
    result = run_as_user(
        user,
        ["/bin/bash", "-lc", command],
        cwd=cwd,
        env=env,
        check=False,
    )
    if check and result.returncode != 0:
        raise DevctlError(f"command failed with exit code {result.returncode}")
    return result.returncode


def exec_shell_as_user(
    user: str,
    command: str,
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
) -> None:
    project_env = user_environment(user, env)
    argv = _identity_command(
        user,
        ["/bin/bash", "-lc", f"exec {command}"],
        project_env,
    )
    log(f"starting [{user}] ({cwd}) {command}")
    os.chdir(cwd)
    os.execvp(argv[0], argv)


def systemctl(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["systemctl", *arguments], check=check, capture=True)


def require_root(action: str) -> None:
    if os.geteuid() != 0:
        raise DevctlError(f"{action} requires root; run it with sudo")
