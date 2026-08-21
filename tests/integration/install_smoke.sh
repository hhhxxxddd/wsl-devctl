#!/usr/bin/env bash
set -euo pipefail

if [[ ! -d /workspace/src/wsl_devctl ]]; then
  echo "mount the repository read-only at /workspace" >&2
  exit 1
fi

install -m 0755 /workspace/tests/integration/fake-systemctl /usr/local/bin/systemctl
install -m 0755 /workspace/tests/integration/fake-rsync /usr/local/bin/rsync
bash /workspace/scripts/install.sh --no-deps
[[ $(/usr/local/bin/wsl-devctl --version) == "wsl-devctl 0.2.0" ]]
[[ -f /etc/systemd/system/wsl-dev-compose@.service ]]
[[ -f /etc/wsl-devctl/examples/dev-next.toml ]]
[[ -f /etc/wsl-devctl/examples/dev-docker-compose.toml ]]
/usr/local/bin/wsl-devctl init /workspace --user root --dry-run >/dev/null
bash /workspace/scripts/uninstall.sh
[[ ! -e /usr/local/bin/wsl-devctl ]]
[[ ! -e /opt/wsl-devctl/src ]]
echo "isolated install and uninstall smoke: PASS"
