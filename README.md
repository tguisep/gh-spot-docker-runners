# gh-spot-docker-runners

[![Python](https://github.com/tguisep/gh-spot-docker-runners/actions/workflows/python.yml/badge.svg?branch=main)](https://github.com/tguisep/gh-spot-docker-runners/actions/workflows/python.yml)
[![Packaging](https://github.com/tguisep/gh-spot-docker-runners/actions/workflows/packaging.yml/badge.svg?branch=main)](https://github.com/tguisep/gh-spot-docker-runners/actions/workflows/packaging.yml)
[![Docs](https://github.com/tguisep/gh-spot-docker-runners/actions/workflows/docs.yml/badge.svg?branch=main)](https://tguisep.github.io/gh-spot-docker-runners/)
[![Release](https://img.shields.io/github/v/release/tguisep/gh-spot-docker-runners?sort=semver)](https://github.com/tguisep/gh-spot-docker-runners/releases/latest)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

Self-hosted GitHub Actions runners as ephemeral Docker containers, on hardware you already own.

GitHub's free plan caps Actions minutes on private repositories; self-hosted runners do not
consume them. This turns one Linux host into an on-demand fleet: it watches your repositories
for queued jobs, starts a fresh container per job, and tears it down on both sides when the job
finishes.

What you deploy is a `.deb`, a systemd unit and one `config.toml`:

- **No control plane and no account anywhere.** One daemon on one host. Nothing to sign up for,
  nothing to phone home to.
- **No inbound ports.** It polls GitHub, so it runs behind NAT on a machine with no public
  address.
- **No credential in a container.** Runners get a single-use registration blob, never your
  token or your app's key.
- **Bounded by the host.** Ceilings on containers, CPU and memory across every pool, a load
  high-water mark that defers launches, and scheduled reclamation of what jobs leave behind.
- **Distribution-proof.** The package bundles its own interpreter, so an upgrade changing
  `python3` cannot break it. `apt install`, or the Ansible role for more than one host.

**[Documentation →](https://tguisep.github.io/gh-spot-docker-runners/)**

## Quick start

A Linux host with Docker, and a credential **scoped to the repositories it will serve** —
a fine-grained token or a GitHub App — carrying **Administration: read & write** (to register
runners) and **Actions: read** (to see queued jobs), and nothing else.

```bash
# The latest release for this machine's architecture, with its checksum.
ARCH="$(dpkg --print-architecture)"
curl -fsSL https://api.github.com/repos/tguisep/gh-spot-docker-runners/releases/latest \
  | grep -o '"browser_download_url": *"[^"]*"' | cut -d'"' -f4 \
  | grep -E "_${ARCH}\.deb$|/SHA256SUMS$" | xargs -n1 curl -fsSLO
sha256sum --ignore-missing -c SHA256SUMS

sudo apt install "./ghspot_"*"_${ARCH}.deb"   # bundles its own Python

sudo ghspot image build ubuntu-24.04   # the runner image, with this host's docker group
sudo ghspot setup                      # asks the four things that cannot be guessed
sudo ghspot doctor --config /etc/ghspot/config.toml
sudo systemctl enable --now ghspot
```

Then point a workflow at your labels:

```yaml
jobs:
  build:
    runs-on: [self-hosted, ubuntu-24.04]
```

Ask for the fewest labels that identify what you need — `linux` and `x64` are implied by
naming the OS, and each extra one is another thing a pool must carry before it can serve.

Installing from source or with Ansible, and everything after the first run:
**[Getting started](https://tguisep.github.io/gh-spot-docker-runners/start/requirements/)**.

## Security

Read [SECURITY.md](SECURITY.md) before pointing this at anything. The short version: with
`docker_socket = true` a job has **effective root on the host** — fine for repositories you
control, unacceptable for one that accepts fork pull requests.

## More

| | |
|---|---|
| [Documentation](https://tguisep.github.io/gh-spot-docker-runners/) | Install, configure, run, tune |
| [Troubleshooting](https://tguisep.github.io/gh-spot-docker-runners/reference/troubleshooting/) | Symptoms, causes, and what to do |
| [The host](https://tguisep.github.io/gh-spot-docker-runners/guides/host/capacity/) | Capacity ceilings, images, housekeeping |
| [Ansible role](deploy/ansible/README.md) | More than one host, configuration in version control |
| [Architecture](https://tguisep.github.io/gh-spot-docker-runners/reference/architecture/) | How the pieces fit, and the decisions behind them |
| [CONTRIBUTING.md](CONTRIBUTING.md) · [CONTEXT.md](CONTEXT.md) | Working on it, and why it is shaped this way |

Apache-2.0. See [LICENSE](LICENSE).
