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
[latest release](https://github.com/tguisep/gh-spot-docker-runners/releases/latest).
Releases are cut automatically from the commit history, so `latest` always matches `main` as
of its last release:

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

### With Ansible

For more than one host, or to keep the configuration in version control:

```bash
cd deploy/ansible
cp inventory/hosts.example.ini inventory/hosts.ini
ansible-vault create inventory/group_vars/runners/vault.yml
ansible-playbook -i inventory/hosts.ini playbook.yml --ask-vault-pass
```

The role installs the package, renders the configuration and credential, builds the runner
images, starts the service, and finishes by running `ghspot doctor` — so a green run means
the daemon can actually work. See [`deploy/ansible/README.md`](../deploy/ansible/README.md).

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

## GPUs

A pool can hand its jobs the host's GPUs:

```toml
[[pool]]
name = "gpu"
repository = "you/your-project"
labels = ["self-hosted", "linux", "x64", "gpu-a100"]

# Without this, a job asking only for [self-hosted, linux, x64] lands here and burns
# the GPU on work that never wanted one. See below — this matters more than it looks.
requires_labels = ["gpu-a100"]

max_runners = 1

[pool.container]
image = "ghspot/runner:ubuntu-24.04"
gpus = "all"          # or a count: 1  —  or specific ids: ["0", "1"]
```

`gpus` is the same selection `docker run --gpus` takes. Device ids are as `nvidia-smi -L`
numbers them.

### The host needs the NVIDIA Container Toolkit

Drivers alone are not enough — the Engine needs the toolkit to pass a device through:

```bash
# Ubuntu / Debian
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt update && sudo apt install -y nvidia-container-toolkit

sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

Check it before configuring a pool:

```bash
docker run --rm --gpus all ubuntu:24.04 nvidia-smi
```

`ghspot doctor` checks for the toolkit whenever a pool asks for GPUs, because without it
**every runner in that pool fails to start** — with an error about device requests that says
nothing about a missing toolkit.

### Stop the GPU taking CPU work

This is the part that catches people out.

Label matching is a **subset** rule: a runner serves a job when it carries every label the
job asked for. Extra labels on the runner are ignored. So a pool labelled
`self-hosted, linux, x64, gpu-a100` will happily accept a job asking for only
`self-hosted, linux, x64` — and your GPU spends the afternoon running unit tests.

`requires_labels` inverts the rule for the labels you name: the job must have asked for them.

```toml
labels = ["self-hosted", "linux", "x64", "gpu-a100"]
requires_labels = ["gpu-a100"]
```

Now the pool serves this:

```yaml
runs-on: [self-hosted, gpu-a100]
```

and refuses a plain `runs-on: [self-hosted, linux, x64]`.

**Name the hardware, not the category.** `gpu-a100` or `gpu-2080ti` rather than `gpu`, so a
workflow that needs 24 GB of VRAM cannot land on a card with 8. The label is the only thing a
workflow author can see.

Set `max_runners` to the number of GPUs you actually have. Two runners sharing one card both
run, and both are slower than either alone.

### What this does and does not prevent

`requires_labels` governs **which pool the daemon scales up for**, and that is the part that
wastes a GPU: without it, a queue of CPU jobs makes the daemon start GPU runners to serve
them.

It does not govern which runner GitHub hands a job to. GitHub also applies its own labels to
every self-hosted runner — `self-hosted`, the OS, the architecture — and those cannot be
removed. So a GPU runner that is *already up and idle* can still be handed a plain CPU job
by GitHub before it is reaped.

Two things shrink that window to almost nothing:

- **`min_idle = 0` on GPU pools.** A GPU runner then exists only while there is GPU work for
  it, rather than sitting idle waiting to be given something else.
- **A distinguishing label on your CPU pools too**, and workflows that ask for it. If nothing
  in your repository says `runs-on: [self-hosted, linux, x64]`, nothing can drift onto the
  GPU box in the first place.

### What the image does and does not carry

The toolkit injects the driver libraries, so `nvidia-smi` and anything CUDA-runtime works
inside a job without the image carrying drivers.

It does **not** provide the CUDA toolkit — there is no `nvcc`. Compiling CUDA means either a
container built for it, or a `setup-` action that fetches one. Baking CUDA into the runner
image would add several gigabytes to every variant for the sake of a minority of jobs.

## What a job leaves behind

Runner containers are removed when their job ends, taking the working directory with them.
But with `docker_socket = true` a job talks to **your** Docker daemon, so what it does there
is yours afterwards:

| The job did this | After the runner is removed |
|---|---|
| Wrote files under `_work` | Gone |
| Pulled or built an image | **Still on the host** |
| Created a volume | **Still on the host** |
| Left a container running | **Still running** |
| `docker build` layers | **Still in the build cache** |

That is inherent to sharing the daemon, and it is the trade recorded in
[ADR 5](adr/0005-docker-socket-over-dind.md).

### Housekeeping bounds it

The daemon reclaims unused Docker objects on a schedule:

```toml
[housekeeping]
every = "1h"
containers_older_than = "1h"     # stopped containers
images_older_than = "24h"        # unused images
volumes = true                   # anonymous volumes
build_cache_older_than = "24h"
keep_build_cache = "10g"
```

Every age is a floor, so a running job cannot have something it is using removed underneath
it. Runner images carry `io.ghspot.image=runner` and are **never** reclaimed — without that
the daemon would eventually delete the images it starts runners from. Named volumes are left
alone, since a named volume is something somebody chose to create.

Set any age to `"never"` to disable that sweep, or `enabled = false` for all of it.

### It is not a guarantee, and the difference matters

Two things housekeeping deliberately will not do:

- **A container the job left running is never touched.** Nothing distinguishes it from
  something you started on purpose, and guessing wrong means deleting somebody's database.
- **Nothing is removed immediately.** Residue is bounded by the interval and the age floors,
  not eliminated.

If you need a real guarantee that a job leaves nothing, the answer is not a bigger broom: it
is giving each runner its own Docker daemon, so there is no shared state to leave anything
in. That means Docker-in-Docker, and costs the shared layer cache — every job re-pulls what
it needs. `RunnerBackend.create()` takes a `ContainerSpec`, so it is a new spec rather than a
change to any calling code, but it is a real architectural change and not currently
implemented.

The narrow case is easy, though: a pool with `docker_socket = false` leaves nothing at all,
because the job never reaches the host daemon in the first place.

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

**A job sits with no logs, and its runner has vanished.**
The runner was removed while the job was running, so nothing ever reported back. Look for
`tick.blind` or `container backend unreachable` in the journal around the time it started:

```bash
journalctl -u ghspot --since "1 hour ago" | grep -E "blind|unreachable|retired"
```

A tick that cannot reach Docker now does nothing at all rather than concluding that no
containers exist — that conclusion used to tear down the fleet mid-job. If you see this on an
older version, upgrade. A Docker restart while jobs were running is the usual trigger.

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
