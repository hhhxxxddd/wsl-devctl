# Architecture

`wsl-devctl` keeps source-of-truth files on Windows and executes dependency-heavy workloads from an
ext4 cache inside WSL.

The installed systemd workers retain controller privileges so the sync and compile coordinators can
quiesce sibling units. All project-controlled commands and `rsync` execute as the configured
`run_user`, so Maven, Node, Python, and application code do not run as root.

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

Toolchain declarations describe requirements independently from the runtime driver. Ubuntu package
installation is performed only by the explicit installer or `doctor --fix`; ordinary start and sync
operations never install software.
