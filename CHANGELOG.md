# Changelog

## 0.5.0 - Unreleased

- Added optional `init --generate-mise` integration with the external `dev-tools` CLI.
- Added `wsl-devctl help` as a concise Chinese quick reference for common workflows.
- Refused ambiguous project toolchains instead of silently installing global defaults.
- Kept Maven Wrapper authoritative and skipped redundant mise Maven installation during prepare.

## 0.4.0 - Unreleased

- Added `system` and `mise` toolchain providers so project workers can honor project-local runtime
  declarations without depending on interactive shell profiles.
- Added explicit mise dependency checks and installation through `prepare` or `doctor --fix` while
  keeping normal startup free of automatic installs and upgrades.
- Added machine-readable output for `init --dry-run`, `list`, `show`, `status`, and `doctor`.
- Made root a supported single-user project identity without requiring `runuser` or Docker group
  membership.
- Generated direct `pnpm` and Yarn commands instead of routing project execution through Corepack.

## 0.3.0 - Unreleased

- Added safe `update`, `rename`, and `unregister` commands for the complete project lifecycle.
- Made forced re-registration preserve and restart the project's previous runtime state.
- Kept build caches in place during project renames and made cache deletion explicit.
- Changed automatically generated registration names to the `local-*` convention while keeping
  the reusable configuration templates named `dev-*.toml`.
- Improved multi-module Spring Boot and Vite discovery for runnable modules, configured ports,
  dependency classpaths, and real source watch roots.

## 0.2.0 - Unreleased

- Added `init` discovery and deterministic TOML generation for Windows and WSL project paths.
- Added first-class Next.js, Vite, React, npm, pnpm, Yarn, and Bun detection.
- Preserved `.next`, `.turbo`, dependency, build, and language caches during source mirroring.
- Added a Docker Compose runtime with build, lifecycle, profile, health, and branch handling.
- Added dependency planning and explicit `doctor --fix` support using Ubuntu packages.
- Added `start` and `stop` commands while retaining the existing `up` and `down` interface.

## 0.1.0 - Unreleased

- Migrated the local single-file controller into an independent Python package.
- Added canonical cache-boundary validation before destructive synchronization.
- Kept system coordination privileged while running project workloads as `run_user`.
- Added explicit user, project, and custom Maven repository modes.
- Replaced absolute Spring DevTools JAR paths with Maven coordinates.
- Classified Java source, resource, deletion, and Maven structure changes.
- Added unbranded `dev-*.toml` examples and non-mutating installation guidance.
