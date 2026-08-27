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
docker build -t ghspot/runner:ubuntu-24.04 \
  --build-arg DOCKER_GID="$(getent group docker | cut -d: -f3)" \
  images/runner/
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
