# 5. Mount the host Docker socket rather than run Docker-in-Docker

**Status:** accepted · 2026-08-26

## Context

Most real workflows build images or run service containers, so jobs need Docker. Three ways
to provide it: mount the host socket, run a privileged `dind` sidecar per runner, or run
rootless `dind`.

## Decision

Mount `/var/run/docker.sock` into the runner container, off by default, enabled per pool with
`docker_socket = true`.

## Consequences

**Gained:**

- Jobs can build and run containers with no extra moving parts.
- The host's layer cache is shared, so repeat builds are fast.
- No privileged sidecar per runner, and no nested storage driver overhead.

**Given up:**

A job with access to the Docker socket has **effective root on the host**. It can start a
privileged container mounting `/`. There is no partial version of this: socket access is
host root.

That is an acceptable trade for repositories you control, where the code running in a job is
code you would have run anyway. It is **not** acceptable for a repository that accepts
workflow runs from forked pull requests, where an attacker chooses the code.

This is stated in `SECURITY.md`, in `config.example.toml`, and in the pool's own
documentation, because a reader who skims must not miss it.

## Alternatives rejected

**Privileged Docker-in-Docker.** Better isolation between jobs, but `--privileged` is also
close to host root, so it trades a clear risk for a less obvious one while adding a sidecar,
slower builds, and no shared cache.

**Rootless Docker-in-Docker.** Better isolation, and the right answer for untrusted code.
Rejected for v1 because it breaks a meaningful share of real workflows (some storage
drivers, some mounts, some networking) and would have made the first working version harder
to reach. `RunnerBackend.create()` takes a `ContainerSpec`, so adding it later is a new spec
variant, not a change to any caller.

## Revisit when

Anyone wants to point this at a repository that accepts fork pull requests. At that point
rootless DinD stops being optional.
