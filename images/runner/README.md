# The runner image

Ubuntu 24.04 plus the GitHub Actions runner. It holds no credentials, makes no API calls of
its own, and contains no logic beyond forwarding a signal.

## Build

```bash
docker build -t ghspot/runner:ubuntu-24.04 \
  --build-arg DOCKER_GID="$(getent group docker | cut -d: -f3)" \
  images/runner/
```

`DOCKER_GID` must match the host's `docker` group so the mounted socket is usable by the
unprivileged `runner` user. `ghspot doctor` checks this.

## Contract

| | |
|---|---|
| Input | `RUNNER_JIT_CONFIG` — a single-use just-in-time configuration blob |
| Behaviour | Runs exactly one job, then exits |
| On `SIGTERM` | Forwards it so the runner finishes the job it accepted |
| Exit 64 | `RUNNER_JIT_CONFIG` was not set |

Everything deciding *whether* a runner should exist lives in the daemon on the host, where it
is testable.

## Updating the runner version

`RUNNER_VERSION` and the two `RUNNER_SHA256_*` values are pinned in the `Dockerfile`. GitHub
requires runners to be no more than 30 days behind the current release. The checksums are
published in the release notes, so bumping does not mean trusting a download:

```bash
gh api repos/actions/runner/releases/latest \
  --jq '.tag_name, (.body | scan("BEGIN SHA linux-(x64|arm64) -->([0-9a-f]{64})"))'
```

Currently pinned: **v2.336.0**.
