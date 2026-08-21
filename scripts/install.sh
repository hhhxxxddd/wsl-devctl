#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "install.sh must run as root" >&2
  exit 1
fi

install_dependencies=true
if [[ ${1:-} == "--no-deps" ]]; then
  install_dependencies=false
elif [[ $# -gt 0 ]]; then
  echo "usage: sudo bash scripts/install.sh [--no-deps]" >&2
  exit 2
fi

if ${install_dependencies}; then
  missing_packages=()
  command -v python3 >/dev/null 2>&1 || missing_packages+=(python3)
  command -v rsync >/dev/null 2>&1 || missing_packages+=(rsync)
  command -v git >/dev/null 2>&1 || missing_packages+=(git)
  command -v ss >/dev/null 2>&1 || missing_packages+=(iproute2)
  command -v runuser >/dev/null 2>&1 || missing_packages+=(util-linux)
  if [[ ${#missing_packages[@]} -gt 0 ]]; then
    if ! command -v apt-get >/dev/null 2>&1; then
      echo "Missing dependencies and apt-get is unavailable: ${missing_packages[*]}" >&2
      exit 1
    fi
    DEBIAN_FRONTEND=noninteractive apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y "${missing_packages[@]}"
  fi
fi

if ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
  echo "wsl-devctl requires Python 3.11 or newer" >&2
  exit 1
fi

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
install_root=/opt/wsl-devctl

if [[ -L "${install_root}" || -L "${install_root}/src" ]]; then
  echo "Refusing to install through a symbolic-link target below ${install_root}" >&2
  exit 1
fi
install -d -m 0755 "${install_root}/src/wsl_devctl"
if [[ $(readlink -f "${install_root}/src/wsl_devctl") != "${install_root}/src/wsl_devctl" ]]; then
  echo "Unexpected installation target: ${install_root}/src/wsl_devctl" >&2
  exit 1
fi
rsync -a --delete "${repo_root}/src/wsl_devctl/" "${install_root}/src/wsl_devctl/"
install -m 0755 "${repo_root}/scripts/wsl-devctl" /usr/local/bin/wsl-devctl

install -d -m 0755 /etc/wsl-devctl/projects.d /etc/wsl-devctl/examples /var/lib/wsl-devctl
install -m 0644 "${repo_root}"/systemd/wsl-dev-*.service /etc/systemd/system/
install -m 0644 "${repo_root}"/examples/dev-*.toml /etc/wsl-devctl/examples/
install -m 0644 "${repo_root}/README.md" /etc/wsl-devctl/README.md
install -m 0644 "${repo_root}/README.en.md" /etc/wsl-devctl/README.en.md

systemctl daemon-reload
echo "Installed wsl-devctl $(/usr/local/bin/wsl-devctl --version)"
echo "Create a project with: sudo wsl-devctl init <path> --fix --start"
echo "Existing projects were not registered, started, stopped, or migrated."
