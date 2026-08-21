from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "src"))

from wsl_devctl.config import parse_project  # noqa: E402
from wsl_devctl.drivers.compose import (  # noqa: E402
    compose_command,
    compose_down,
    compose_healthy,
)
from wsl_devctl.process import run_as_user  # noqa: E402
from wsl_devctl.sync import sync_once  # noqa: E402


def wait_for_compose(project) -> None:
    deadline = time.monotonic() + 20
    detail = "not started"
    while time.monotonic() < deadline:
        healthy, detail = compose_healthy(project)
        if healthy:
            return
        time.sleep(0.5)
    raise RuntimeError(f"Compose smoke project did not become healthy: {detail}")


def main() -> None:
    if os.geteuid() != 0:
        raise SystemExit("compose_smoke.py must run as root")
    with tempfile.TemporaryDirectory(prefix="wsl-devctl-compose-smoke-") as temporary:
        root = Path(temporary)
        source = root / "source"
        source.mkdir()
        (source / "compose.yaml").write_text(
            "services:\n"
            "  idle:\n"
            "    image: busybox:1.36\n"
            "    command: [\"sh\", \"-c\", \"while true; do sleep 3600; done\"]\n",
            encoding="utf-8",
        )
        raw = {
            "name": f"dev-compose-smoke-{os.getpid()}",
            "run_user": "root",
            "source": str(source),
            "cache_root": str(root / "cache"),
            "runtime": {"driver": "compose"},
            "sync": {"enabled": True, "exclude": ["/.git/"]},
            "backend": {"enabled": False, "workdir": "."},
            "frontend": {"enabled": False, "workdir": "."},
            "compile": {"enabled": False, "workdir": "."},
            "branch_switch": {"enabled": False},
            "checks": {},
            "docker": {
                "compose": {
                    "files": ["compose.yaml"],
                    "build": False,
                    "pull": False,
                    "remove_orphans": True,
                }
            },
        }
        project = parse_project(raw)
        sync_once(project)
        try:
            result = run_as_user(
                project.run_user,
                compose_command(project, "up", "--detach"),
                cwd=project.cache,
                check=False,
                capture=True,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr or result.stdout)
            wait_for_compose(project)
        finally:
            if compose_down(project) != 0:
                raise RuntimeError("docker compose down failed")
    print("docker compose smoke: PASS")


if __name__ == "__main__":
    main()
