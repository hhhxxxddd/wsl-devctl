#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "uninstall.sh must run as root" >&2
  exit 1
fi

if systemctl list-units --state=active --plain --no-legend 'wsl-dev-*@*.service' | grep -q .; then
  echo "Refusing to uninstall while wsl-devctl units are active." >&2
  echo "Stop the registered projects explicitly before uninstalling." >&2
  exit 1
fi

if [[ -L /opt/wsl-devctl || -L /opt/wsl-devctl/src ]]; then
  echo "Refusing to remove a symbolic-link target below /opt/wsl-devctl" >&2
  exit 1
fi
if [[ -e /opt/wsl-devctl/src ]]; then
  if [[ $(readlink -f /opt/wsl-devctl/src) != /opt/wsl-devctl/src ]]; then
    echo "Unexpected installation target: /opt/wsl-devctl/src" >&2
    exit 1
  fi
fi

rm -f /usr/local/bin/wsl-devctl
rm -f /etc/systemd/system/wsl-dev-sync@.service
rm -f /etc/systemd/system/wsl-dev-compile@.service
rm -f /etc/systemd/system/wsl-dev-backend@.service
rm -f /etc/systemd/system/wsl-dev-frontend@.service
rm -f /etc/systemd/system/wsl-dev-compose@.service
rm -rf -- /opt/wsl-devctl/src
systemctl daemon-reload

echo "Removed executable, installed Python source, and unit templates."
echo "Configuration, state, Maven repositories, and project caches were preserved."
