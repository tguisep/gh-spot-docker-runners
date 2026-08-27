# Operations

## Requirements

- Linux host with Docker, and a user in the `docker` group
- Python 3.12 or newer
- A fine-grained GitHub personal access token

## Authentication

The daemon needs **Administration: read & write** (to register and remove runners) and
**Actions: read** (to see queued jobs), on the repositories in your `config.toml`. Nothing
else.

Two ways to provide them:

| | Personal access token | GitHub App |
|---|---|---|
| Setup | ~2 minutes | ~10 minutes |
| Rate limit | 5000/hour, shared with everything else you do | Its own budget per installation |
| Token lifetime | Until it expires | ~1 hour, rotated automatically |

Use a token to try things out; use an App for anything left running.

**→ [`authentication.md`](authentication.md) walks through both, with the exact permissions
and why each one is needed.**

Whichever you choose, no credential ever enters a runner container — containers receive a
single-use config blob and nothing else.

```toml
# A token:
[github]
token_file = "~/.config/ghspot/token"

# Or an App:
[github]
app_id = "123456"
private_key_file = "~/.config/ghspot/app.pem"
```

The environment wins over the file, so a service manager can inject secrets without one:
`GHSPOT_GITHUB_TOKEN`, or `GHSPOT_GITHUB_APP_ID` and `GHSPOT_GITHUB_APP_PRIVATE_KEY`.

## Install

### From a .deb (recommended on Debian and Ubuntu)

Download the package for your architecture from the
[latest release](https://github.com/tguisep/gh-spot-docker-runners/releases/latest):

```bash
sudo apt install ./ghspot_0.1.0-1_amd64.deb
```

It bundles its own Python, so it does not use — or care about — the system interpreter. The
package works on any glibc distribution and cannot be broken by a distribution upgrade
changing `python3`.

What it installs:

| Path | |
|---|---|
| `/usr/bin/ghspot` | The command |
| `/opt/ghspot/` | Bundled interpreter and virtualenv |
| `/etc/ghspot/config.toml` | Configuration — a conffile, so your edits survive upgrades |
| `/lib/systemd/system/ghspot.service` | The unit |
| `/var/lib/ghspot/` | State database |

It creates a `ghspot` system user, adds it to the `docker` group, and **does not start the
daemon** — it cannot work until a repository and a credential are configured. Continue at
[Configure](#configure), then:

```bash
sudo ghspot doctor --config /etc/ghspot/config.toml
sudo systemctl enable --now ghspot
```

Removing the package keeps `/etc/ghspot`, so a reinstall does not lose your credentials.
`sudo apt purge ghspot` removes those too.

### From source

```bash
git clone https://github.com/tguisep/gh-spot-docker-runners.git
cd gh-spot-docker-runners
uv tool install .
```

That puts a standalone `ghspot` in `~/.local/bin`, usable from any directory. If your shell
cannot find it afterwards, that directory is not on your `PATH`:

```bash
uv tool update-shell     # adds it, then restart your shell
```

<details>
<summary>Other ways to install</summary>

```bash
# Working on the code: no install, run from the repository.
uv sync
uv run ghspot doctor

# Without uv, into a virtualenv of your own. On Debian and Ubuntu this needs
# python3-venv, or the virtualenv is created without pip in it.
sudo apt install -y python3-venv
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/ghspot doctor

# With pipx.
pipx install .
```

`uv sync` alone installs into `.venv/` **without** putting `ghspot` on your `PATH` — that is
why the plain command reports `command not found` after it. Use `uv run ghspot`, or install
with `uv tool install .`.

</details>

Then build the runner image, which both install methods need:

```bash
images/runner/build.sh ubuntu-24.04
```

`DOCKER_GID` must match the host's `docker` group, or the unprivileged `runner` user inside
the container cannot use the mounted socket.

## Configure

```bash
cp config.example.toml config.toml
$EDITOR config.toml
ghspot config validate
```

Then, before running anything:

```bash
ghspot doctor
```

Every check `doctor` performs is a failure that would otherwise appear as a pool quietly
never starting a runner. It verifies the config parses, Docker answers, the runner image
exists, the socket is present, the token resolves, and each repository is reachable with the
permissions the daemon needs. Failures print the command that fixes them.

## Run

Interactively, to watch it work:

```bash
ghspot daemon
```

Then push a commit with a workflow targeting your labels:

```yaml
jobs:
  build:
    runs-on: [self-hosted, linux, x64, home-vm]
    steps:
      - uses: actions/checkout@v4
      - run: echo "running on $(hostname)"
```

Within a poll interval a container appears, takes the job, and disappears. Both sides should
be empty afterwards:

```bash
docker ps -a --filter label=io.ghspot.managed=true    # empty
gh api repos/OWNER/REPO/actions/runners               # no ghspot-* runners
```

### As a service

Create the service user, and install the daemon somewhere it can reach — the unit expects a
virtualenv at `/opt/ghspot/.venv`, so that it does not depend on any human's home directory:

```bash
sudo useradd --system --home /opt/ghspot --shell /usr/sbin/nologin ghspot
sudo usermod -aG docker ghspot

# Debian and Ubuntu ship python3 without ensurepip, so `python3 -m venv` produces a
# virtualenv with no pip in it. This package is what supplies it.
sudo apt install -y python3-venv

sudo mkdir -p /opt/ghspot
sudo python3 -m venv /opt/ghspot/.venv

# Give pip the repository's path, not `.` — sudo does not reliably inherit your
# working directory.
sudo /opt/ghspot/.venv/bin/pip install --quiet "$PWD"

sudo chown -R ghspot:ghspot /opt/ghspot

# Confirm the path the unit will run:
/opt/ghspot/.venv/bin/ghspot version
```

> If `pip: command not found` appears, `python3-venv` was missing when the virtualenv was
> created. Remove it with `sudo rm -rf /opt/ghspot/.venv`, install the package, and create
> it again — an incomplete virtualenv is not repaired by installing the package afterwards.

Then the configuration and credentials:

```bash
sudo mkdir -p /etc/ghspot
sudo cp config.toml /etc/ghspot/

# Personal access token:
printf 'GHSPOT_GITHUB_TOKEN=%s\n' 'github_pat_...' | sudo tee /etc/ghspot/env > /dev/null

# Or a GitHub App — EnvironmentFile cannot hold real newlines, so escape them:
#   { printf 'GHSPOT_GITHUB_APP_ID=123456\n'
#     printf 'GHSPOT_GITHUB_APP_PRIVATE_KEY=%s\n' "$(awk '{printf "%s\\n", $0}' app.pem)"
#   } | sudo tee /etc/ghspot/env > /dev/null

sudo chmod 600 /etc/ghspot/env

sudo cp deploy/ghspot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ghspot
journalctl -u ghspot -f
```

The unit sets `TimeoutStopSec=300`. On stop the daemon finishes its current tick and leaves
busy runners alone — killing one fails a build that was about to pass — so that timeout is
how long systemd waits before insisting.

If you installed elsewhere, point `ExecStart=` at your own path — `command -v ghspot` shows
it. The unit runs as the `ghspot` user, so a binary under *your* home directory will not be
readable by it; that is why `/opt/ghspot` is the default.

To upgrade later:

```bash
cd gh-spot-docker-runners && git pull
sudo /opt/ghspot/.venv/bin/pip install --quiet "$PWD"
sudo systemctl restart ghspot
```

## Day to day

```bash
ghspot pool list                  # every pool and what it holds
ghspot pool status default        # one pool, with its runners
ghspot runner list                # live runners
ghspot runner list --all          # including retired and failed
ghspot runner logs <ref>          # container output
ghspot runner stop <ref>          # retire it on both sides
ghspot runner stop <ref> --force  # even if it is mid-job
```

`<ref>` accepts a runner id, a runner name, or a container id, whole or abbreviated —
whichever column you have in front of you.

`ghspot runner list` reads only the local database. It works when the token has expired or
Docker is down, which is when you most want to look.

### The REST API

Set `api_bind` under `[daemon]` and the API is served in-process with the loop:

```bash
curl -s localhost:8770/health | jq
curl -s localhost:8770/pools | jq
curl -s -X POST localhost:8770/reconcile | jq   # tick now, don't wait
```

Interactive docs at `/docs`. **There is no authentication** — bind to localhost, or put a
reverse proxy with auth in front of it.

## Runner images and labels

### Available variants

| Variant | Base | Ask for it with |
|---|---|---|
| `ubuntu-24.04` | `ubuntu:24.04` | `runs-on: [self-hosted, ubuntu-24.04]` |
| `ubuntu-22.04` | `ubuntu:22.04` | `runs-on: [self-hosted, ubuntu-22.04]` |
| `rhel-9` | `almalinux:9` | `runs-on: [self-hosted, rhel-9]` |
| `rhel-10` | `almalinux:10` | `runs-on: [self-hosted, rhel-10]` |

```bash
images/runner/build.sh                 # all of them
images/runner/build.sh rhel-9          # just one
images/runner/verify.sh rhel-9         # check the contract and the toolset
```

Each image carries the apt toolset GitHub installs on its own hosted runners, plus `git`,
`cmake`, `node`/`npm` and a working `pip`. Language toolchains are **not** preinstalled:
`actions/setup-python`, `setup-go`, `setup-java` and the rest fetch what they need at
runtime, so workflows using them work as-is, just slower on a cold runner than on GitHub's
toolcache-equipped images. See [`images/runner/README.md`](../images/runner/README.md) for
the full list and the handful of tools unavailable on RHEL 10.

The variant name is the image tag *and* the label. Keeping them the same string is what stops
a pool from advertising an OS it is not actually running.

### Name the OS in the labels

A bare `linux` tells a job nothing. Prefer the specific form, the way GitHub's own hosted
runners are labelled:

```toml
labels = ["self-hosted", "linux", "x64", "ubuntu-24.04", "home-vm"]
```

`linux` and `x64` are still worth keeping — plenty of workflows ask for them — but a job that
needs `dnf`, or a particular glibc, can now say so and be sure of what it gets.

Remember that a pool serves a job only when it carries **every** label the job asks for.
Adding `ubuntu-24.04` costs nothing; removing `linux` will strand any workflow still asking
for it.

### Serving several operating systems

One pool per image. A job asking for `rhel-9` will only ever land on a runner carrying it:

```toml
[[pool]]
name = "ubuntu"
repository = "you/your-project"
labels = ["self-hosted", "linux", "x64", "ubuntu-24.04"]
max_runners = 3
[pool.container]
image = "ghspot/runner:ubuntu-24.04"
docker_socket = true

[[pool]]
name = "rhel"
repository = "you/your-project"
labels = ["self-hosted", "linux", "x64", "rhel-9"]
max_runners = 1
[pool.container]
image = "ghspot/runner:rhel-9"
docker_socket = true
```

Both pools watch the same repository; the daemon polls it once per tick regardless of how
many pools point at it, and each pool only counts the jobs it can actually serve.

### Choosing a RHEL rebuild

`rhel-9` and `rhel-10` are built on AlmaLinux, a faithful RHEL rebuild with complete
repositories and no subscription. To build on something else:

```bash
docker build -f images/runner/rhel.Dockerfile \
  --build-arg BASE_IMAGE=registry.access.redhat.com/ubi9/ubi \
  --build-arg DOCKER_GID="$(getent group docker | cut -d: -f3)" \
  -t ghspot/runner:rhel-9 images/runner/
```

`rockylinux/rockylinux:9` and `quay.io/centos/centos:stream9` work the same way. Red Hat's
UBI is the closest to genuine RHEL, at the cost of a reduced package set — some things a
workflow expects are simply not in its repositories.

### The docker group id

`build.sh` detects the host's `docker` group and builds it in. If it does not match, jobs fail
with `permission denied` on `/var/run/docker.sock` — which looks nothing like an image
problem. The images now assert it at build time, because on the RHEL family the Docker CE
package creates its own `docker` group first and a plain `groupadd` silently does nothing.

If you move an image to another host whose `docker` group id differs, rebuild it there.

## Running this project's own CI on your runners

The workflow in `.github/workflows/ci.yml` runs on the self-hosted fleet, which is the most
honest test the project has: if reconciliation breaks, CI stops.

### Labels

The workflow asks for `[self-hosted, linux, x64, home-vm]`. A pool serves a job only when it
carries **every** label the job asks for, so the pool's `labels` must be a superset — which
leaves room for the OS label from the section above:

```toml
[[pool]]
labels = ["self-hosted", "linux", "x64", "ubuntu-24.04", "home-vm"]
```

Change the workflow and the pool together, or jobs queue forever with no runner to take
them.

### Fork pull requests never reach your machine

This matters more than anything else on this page.

GitHub is explicit that [self-hosted runners and public repositories are a dangerous
combination](https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners/about-self-hosted-runners#self-hosted-runner-security):
anyone can fork a public repository, open a pull request, and have their workflow execute on
your runner. With `docker_socket = true` that is **root on your home server, offered to
strangers**.

The workflow therefore picks its runner rather than hard-coding one:

| Event | Runs on | Why |
|---|---|---|
| `push` | Self-hosted | Requires write access |
| Pull request from a branch in this repository | Self-hosted | Requires write access |
| Pull request from a fork | GitHub-hosted | The author could be anyone |
| `workflow_dispatch` with `force_hosted` | GitHub-hosted | Manual escape hatch |

A `select-runner` job resolves this once and every other job reads its output, so the rule
lives in one place instead of being repeated — and forgotten — per job.

**This is not optional if your repository is public.** Deleting that logic and writing
`runs-on: [self-hosted, ...]` directly hands arbitrary code execution to anyone with a GitHub
account.

### When the fleet is down

CI queues rather than failing: a job with no matching runner waits, and GitHub fails it after
24 hours. To get a green build without waiting, re-run the workflow from the Actions tab with
**Run workflow → force_hosted**, which puts everything back on GitHub-hosted runners.

### Which jobs cannot move

Jobs that run `docker run -v "${PWD}:/src"` stay on GitHub-hosted. Inside a runner container
the Docker client talks to the *host's* daemon, so a workspace path is resolved on the host,
where it does not exist — Docker mounts an empty directory and the job fails confusingly.
`docker build` is unaffected, because it streams its context from the client.

Moving them would require the runner's work directory to be a host bind mount at an identical
path inside the container. That is a change to the runner image, not to the workflow.

## Tuning

| Setting | Raise it when | Lower it when |
|---|---|---|
| `poll_interval` | You are near the rate limit | Jobs wait too long to start |
| `min_idle` | Jobs wait on container boot | Idle containers waste memory |
| `max_runners` | The host has capacity to spare | Jobs are starving each other |
| `max_launch_per_tick` | Large matrices start too slowly | A burst overwhelms the host |
| `idle_timeout` | Runners churn between jobs | Idle runners linger too long |

`min_idle = 1` is the setting most worth having: it removes container boot time from the
critical path of the first job.

## Troubleshooting

**Jobs stay queued and no container appears.**
Labels are the usual cause — the runner must carry *every* label the job asks for. Compare
`ghspot pool list` against your `runs-on`. Then check `ghspot daemon` output for a
`tick.error`.

**A runner shows `Offline` on github.com.**
Leave it. The next tick either adopts it or deletes it. If it persists past a few ticks, look
for `tick.error` lines — the daemon only deletes runners whose name starts with `ghspot-`
and which are offline, so a hand-registered runner is never touched.

**`ImageNotFoundError`.**
The runner image is not built on this host. `ghspot doctor` prints the exact build command.

**Rate limited.**
`ForgeRateLimitedError` means the hourly budget is spent. Raise `poll_interval` or reduce the
number of pools. Conditional requests make idle polling nearly free, so this usually means
many repositories with constant activity. Switching from a personal access token to a GitHub
App gives the daemon its own budget instead of sharing yours.

**A credential or permission error.**
[`authentication.md`](authentication.md#when-permissions-are-wrong) has a table mapping each
message to its cause. The two that catch people out: `GitHub rejected the app assertion`
usually means a wrong App ID or a skewed host clock, and a permission change on a GitHub App
does not take effect until the installation *accepts* it.

**Jobs cannot run `docker`.**
Set `docker_socket = true` for the pool, and confirm the image was built with the host's
`DOCKER_GID`. Inside a runner, `docker ps` should work as the `runner` user.

**The daemon exits immediately.**
Almost always configuration. Run `ghspot config validate` — it names the field.

**`pip: command not found` after creating a virtualenv.**
Debian and Ubuntu ship `python3` without `ensurepip`, so `python3 -m venv` makes a
virtualenv containing only python symlinks. Install `python3-venv`, delete the incomplete
virtualenv, and create it again — installing the package does not repair one that already
exists. The `.deb` avoids this entirely by bundling its own interpreter.

**`ghspot: command not found`.**
`uv sync` installs into the repository's `.venv/` and does not put anything on your `PATH`.
Either install it properly with `uv tool install .`, or prefix commands with `uv run` from
inside the repository. If you did run `uv tool install .`, then `~/.local/bin` is missing
from your `PATH` — `uv tool update-shell` adds it.

## Backups

There is nothing to back up. The state database is a projection: delete it and the next tick
rebuilds the fleet from the containers' own labels. Back up `config.toml` and your credential — the token file or the app's private key.
