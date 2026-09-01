# gh-spot-docker-runners

[![Python](https://github.com/tguisep/gh-spot-docker-runners/actions/workflows/python.yml/badge.svg?branch=main)](https://github.com/tguisep/gh-spot-docker-runners/actions/workflows/python.yml)
[![Packaging](https://github.com/tguisep/gh-spot-docker-runners/actions/workflows/packaging.yml/badge.svg?branch=main)](https://github.com/tguisep/gh-spot-docker-runners/actions/workflows/packaging.yml)
[![Docs](https://github.com/tguisep/gh-spot-docker-runners/actions/workflows/docs.yml/badge.svg?branch=main)](https://tguisep.github.io/gh-spot-docker-runners/)
[![Release](https://img.shields.io/github/v/release/tguisep/gh-spot-docker-runners?sort=semver)](https://github.com/tguisep/gh-spot-docker-runners/releases/latest)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

Self-hosted GitHub Actions runners as ephemeral Docker containers, managed by a single Python
daemon.

GitHub's free plan caps Actions minutes on private repositories; self-hosted runners do not
consume them. This turns one Linux host into an on-demand fleet: it watches your repositories
for queued jobs, starts a fresh container per job, and tears it down on both sides when the job
finishes. No credential ever enters a container.

**[Documentation →](https://tguisep.github.io/gh-spot-docker-runners/)**

## Quick start

A Linux host with Docker, and credentials with **Administration: read & write** and
**Actions: read** — a fine-grained token or a GitHub App.

```bash
sudo apt install ./ghspot_*.deb        # from the latest release; bundles its own Python

sudo ghspot image build ubuntu-24.04   # the runner image, with this host's docker group
sudo ghspot setup                      # asks the four things that cannot be guessed
sudo ghspot doctor --config /etc/ghspot/config.toml
sudo systemctl enable --now ghspot
```

Then point a workflow at your labels:

```yaml
jobs:
  build:
    runs-on: [self-hosted, linux, x64, ubuntu-24.04]
```

Installing from source or with Ansible, and everything after the first run:
**[Getting started](https://tguisep.github.io/gh-spot-docker-runners/start/requirements/)**.

## Security

Read [SECURITY.md](SECURITY.md) before pointing this at anything. The short version: with
`docker_socket = true` a job has **effective root on the host** — fine for repositories you
control, unacceptable for one that accepts fork pull requests.

## More

| | |
|---|---|
| [Documentation](https://tguisep.github.io/gh-spot-docker-runners/) | Install, configure, run, tune, troubleshoot |
| [Architecture](https://tguisep.github.io/gh-spot-docker-runners/reference/architecture/) | How the pieces fit, and the decisions behind them |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Layout rules and how to work on it |
| [CONTEXT.md](CONTEXT.md) | Project history |

Apache-2.0. See [LICENSE](LICENSE).
