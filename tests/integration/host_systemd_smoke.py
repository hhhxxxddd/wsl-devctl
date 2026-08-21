from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "src"))

from wsl_devctl.config import parse_project  # noqa: E402
from wsl_devctl.tomlgen import render_toml  # noqa: E402


def run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    subprocess.run(command, check=True, env=env)


def available_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def wait_for_http(port: int) -> None:
    deadline = time.monotonic() + 15
    url = f"http://127.0.0.1:{port}/"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError(f"transient backend did not become ready: {url}")


def main() -> None:
    if os.geteuid() != 0:
        raise SystemExit("host_systemd_smoke.py must run as root")
    name = f"dev-host-smoke-{os.getpid()}"
    unit = f"wsl-devctl-v3-host-{os.getpid()}.service"
    with tempfile.TemporaryDirectory(prefix="wsl-devctl-host-smoke-") as temporary:
        root = Path(temporary)
        root.chmod(0o755)
        source = root / "source"
        source.mkdir(mode=0o755)
        (source / "index.html").write_text("host-smoke-v1\n", encoding="utf-8")
        cache_root = root / "cache"
        cache_root.mkdir(mode=0o755)
        config_dir = root / "config"
        config_dir.mkdir()
        state_root = root / "state"
        port = available_port()
        raw = {
            "name": name,
            "run_user": "nobody",
            "source": str(source),
            "cache_root": str(cache_root),
            "runtime": {"driver": "host"},
            "sync": {"enabled": True, "exclude": ["/.git/"]},
            "backend": {
                "enabled": True,
                "workdir": ".",
                "port": port,
                "run": f"python3 -m http.server {port} --bind 127.0.0.1",
            },
            "frontend": {"enabled": False, "workdir": "."},
            "compile": {"enabled": False, "workdir": "."},
            "branch_switch": {"enabled": False},
            "checks": {},
        }
        project = parse_project(raw)
        (config_dir / f"{name}.toml").write_text(render_toml(raw), encoding="utf-8")
        environment = dict(os.environ)
        environment.update(
            {
                "PYTHONPATH": str(REPOSITORY / "src"),
                "WSL_DEVCTL_CONFIG_DIR": str(config_dir),
                "WSL_DEVCTL_STATE_ROOT": str(state_root),
            }
        )
        run([sys.executable, "-m", "wsl_devctl", "sync", name], env=environment)
        if (project.cache / "index.html").read_text(encoding="utf-8") != "host-smoke-v1\n":
            raise RuntimeError("initial isolated sync did not copy source content")
        systemd_command = [
            "systemd-run",
            f"--unit={unit}",
            "--collect",
            "--property=Type=simple",
            "--property=Restart=no",
            f"--setenv=PYTHONPATH={REPOSITORY / 'src'}",
            f"--setenv=WSL_DEVCTL_CONFIG_DIR={config_dir}",
            f"--setenv=WSL_DEVCTL_STATE_ROOT={state_root}",
            sys.executable,
            "-m",
            "wsl_devctl",
            "_worker",
            name,
            "backend",
        ]
        try:
            run(systemd_command)
            wait_for_http(port)
            run(["systemctl", "is-active", "--quiet", unit])
            (source / "index.html").write_text("host-smoke-v2\n", encoding="utf-8")
            run([sys.executable, "-m", "wsl_devctl", "sync", name], env=environment)
            if (project.cache / "index.html").read_text(encoding="utf-8") != "host-smoke-v2\n":
                raise RuntimeError("second isolated sync did not update source content")
        finally:
            subprocess.run(
                ["systemctl", "stop", unit],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subprocess.run(
                ["systemctl", "reset-failed", unit],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    print("host systemd smoke: PASS")


if __name__ == "__main__":
    main()
