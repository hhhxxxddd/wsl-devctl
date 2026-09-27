# Architecture

`wsl-devctl` keeps source-of-truth files on Windows and supports two development environments.
WSL projects mirror source into an ext4 cache and run under systemd. Windows-native projects run
in their source directory with application-defined development commands; `.wsl-devctl/windows/`
holds only process state and logs. The WSL sync always excludes that directory.

The PowerShell CLI calls the WSL controller through `wsl.exe`. The native WSL CLI calls the Windows
PowerShell companion through WSL interop. Each side can list and control projects registered on
either side. Explicit `win` and `wsl` prefixes select an environment; unprefixed lifecycle
commands resolve the project name, preferring WSL if both environments use the same name.

## WSL mode

The installed systemd workers retain controller privileges so the sync and compile coordinators can
quiesce sibling units. Project-controlled commands and `rsync` execute as the configured `run_user`.
Personal single-user WSL installations may use root directly; shared environments can use an
unprivileged account.

State is separated into four classes:

- `/etc/wsl-devctl/projects.d`: root-owned project declarations.
- `/var/lib/wsl-devctl`: controller recovery and Git state.
- user cache: disposable ext4 source/build mirror.
- user Maven repository: persistent resolved and reactor-installed artifacts.

The Windows workspace is authoritative. `rsync --delete` is allowed only after canonicalizing the
cache and proving it is strictly below the configured cache root.

Project initialization separates discovery from runtime execution. Discovery inspects a bounded
portion of the source tree and emits deterministic TOML; the generated file remains the auditable
source of truth.

Two runtime drivers are currently supported:

- `host`: generic backend/frontend processes plus an optional compiler watcher.
- `compose`: a Docker Compose project running against the ext4 mirror.

Toolchain declarations describe requirements independently from the runtime driver. The `system`
provider consumes commands from WSL `PATH`. The `mise` provider runs project commands through
`mise exec`, so systemd workers resolve the same project-local versions without loading interactive
shell profiles. Automatic installation and system fallback are disabled for these executions.

The ownership boundary is intentional:

- `dev-tools` owns machine-level mise installation, shared defaults, upgrades, and user-facing
  environment management across Windows and WSL.
- Project repositories own reproducible versions in `mise.toml` or `.mise.toml`.
- `wsl-devctl` owns source mirroring, project preparation, systemd workers, hot reload, and health.
  It consumes toolchain declarations but does not become a general version manager.

Ubuntu package installation is performed only by the explicit installer or `doctor --fix`. A mise
installation is performed only by `doctor --fix`, `prepare`, or `start --prepare`. Ordinary startup,
sync, restart, and hot reload never install or upgrade tools.

## Windows-native mode

The per-user registry at `%LOCALAPPDATA%\wsl-devctl\registry.json` maps a project name to a
Windows source directory. The project's `wsl-devctl.windows.json` defines service workdirs,
PowerShell run commands, optional preparation commands, and optional TCP ports. The controller
starts a hidden worker outside the WSL interop process tree so calls from WSL return while the
Windows process keeps running. The worker runs in the source directory and retries an exited
service. A recorded PID and process start time prevent stale PID files from targeting a reused PID.

Runtime state and logs stay in `<project>/.wsl-devctl/windows/`; registration adds a local Git
exclude where possible, and WSL sync excludes `.wsl-devctl/` independently of project settings.
The Windows controller uses PowerShell 7 and does not require a Windows Python installation.
