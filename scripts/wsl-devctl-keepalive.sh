#!/usr/bin/env bash
# A Windows-owned WSL session keeps systemd workloads alive after the CLI exits.
# Give the caller time to start its units, then leave when all devctl units stop.
sleep 30
while systemctl list-units --type=service --state=active,activating --no-legend 'wsl-dev-*@*.service' | grep -q .; do
    sleep 5
done
