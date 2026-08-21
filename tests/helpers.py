from __future__ import annotations

import getpass
from pathlib import Path


def project_dict(root: Path, **overrides):
    source = root / "source"
    cache_root = root / "cache-root"
    source.mkdir(parents=True, exist_ok=True)
    cache_root.mkdir(parents=True, exist_ok=True)
    value = {
        "name": "dev-test",
        "run_user": getpass.getuser(),
        "source": str(source),
        "cache_root": str(cache_root),
        "cache": str(cache_root / "dev-test"),
        "sync": {"enabled": True, "exclude": ["/.git/"]},
        "backend": {"enabled": True, "workdir": "backend", "port": 8080},
        "frontend": {"enabled": False, "workdir": "frontend"},
        "compile": {"enabled": False, "workdir": "backend"},
        "branch_switch": {"enabled": False},
        "checks": {},
    }
    value.update(overrides)
    return value
