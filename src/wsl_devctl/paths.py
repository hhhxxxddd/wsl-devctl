from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimePaths:
    """System paths used by the installed controller.

    Environment overrides exist for tests and parallel development installs. The
    production installer deliberately uses the conventional system locations.
    """

    config_dir: Path
    state_root: Path
    unit_dir: Path
    documentation: Path

    @classmethod
    def from_environment(cls) -> "RuntimePaths":
        config_root = Path(os.environ.get("WSL_DEVCTL_CONFIG_ROOT", "/etc/wsl-devctl"))
        return cls(
            config_dir=Path(
                os.environ.get("WSL_DEVCTL_CONFIG_DIR", str(config_root / "projects.d"))
            ),
            state_root=Path(os.environ.get("WSL_DEVCTL_STATE_ROOT", "/var/lib/wsl-devctl")),
            unit_dir=Path(os.environ.get("WSL_DEVCTL_UNIT_DIR", "/etc/systemd/system")),
            documentation=Path(
                os.environ.get("WSL_DEVCTL_DOCUMENTATION", str(config_root / "README.md"))
            ),
        )

    def config_path(self, name: str) -> Path:
        return self.config_dir / f"{name}.toml"

    def project_state(self, name: str) -> Path:
        return self.state_root / name
