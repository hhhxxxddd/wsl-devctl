# Maven and Spring hot reload model

The Maven local repository is part of the runtime model, not merely a download cache.

## Change classes

| Change | Action | Runtime source |
|---|---|---|
| Existing Java source edited | `compile` | stable post-compile class overlays |
| XML/YAML/properties edited | quiesced `install` | Maven repository artifacts |
| Java source deleted or renamed | quiesced `clean install` | rebuilt overlays and artifacts |
| `pom.xml`, `.mvn`, or wrapper edited | quiesced `clean install` | rebuilt Maven graph |
| Git commit changes during branch switch | quiesced branch prepare | rebuilt Maven graph |

After a successful class-only compile, the controller refreshes stable package-root overlays and
writes a real reload marker into each one. Spring DevTools therefore sees only completed compiler
output instead of transient deletion/recreation inside Maven's `target/classes` directories.

## Repository modes

- `user`: `${HOME}/.m2/repository`; the default, shared by the configured workload user.
- `project`: `${HOME}/.cache/wsl-devctl/maven/<project>/repository`; isolated but larger.
- `path`: an explicit absolute repository for advanced setups.

Every prepare, compile, resource build, structural build, branch build, dependency resolution, and
runtime launch receives the same `MAVEN_OPTS=-Dmaven.repo.local=...` value. The selected executable
is exported as `WSL_DEVCTL_MAVEN`; project commands should invoke `"$WSL_DEVCTL_MAVEN"`.

Spring DevTools is configured by Maven coordinate. Absolute paths below `/root/.m2` do not belong in
portable examples.
