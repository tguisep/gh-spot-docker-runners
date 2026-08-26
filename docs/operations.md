# Operations

## Requirements

- Linux host with Docker, and a user in the `docker` group
- Python 3.12 or newer
- A fine-grained GitHub personal access token

## Authentication

Two modes. Both need the same two permissions:

| Permission | Level | Why |
|---|---|---|
| Administration | Read and write | Minting just-in-time runner configurations |
| Actions | Read | Seeing which jobs are queued |

Neither credential ever enters a runner container — containers get a single-use config blob
and nothing else, in either mode.

### Personal access token — quickest to start

Create it at **Settings → Developer settings → Personal access tokens → Fine-grained tokens**,
scoped to the repositories you want runners for.

```bash
mkdir -p ~/.config/ghspot
install -m 600 /dev/null ~/.config/ghspot/token
printf '%s' 'github_pat_...' > ~/.config/ghspot/token
```

```toml
[github]
token_file = "~/.config/ghspot/token"
```

### GitHub App — preferred for anything long-lived

Better in three ways that matter for a daemon running continuously:

- **Rate limit belongs to the installation**, not to you. A PAT's 5000/hour is shared with
  everything else you do; an installation gets its own budget, which scales with the number
  of repositories and users it covers.
- **Permissions are the app's**, not everything your account can reach. A PAT scoped to two
  repositories still authenticates *as you*.
- **Tokens expire hourly on their own.** The daemon refreshes them; a leaked one dies
  without you doing anything.

Create one at **Settings → Developer settings → GitHub Apps → New GitHub App**:

1. Uncheck **Webhook → Active** — this project polls and needs no inbound endpoint.
2. Under **Repository permissions**, set *Administration: Read and write* and
   *Actions: Read*.
3. Create it, then **Generate a private key** and save the `.pem`.
4. **Install App** on your account, choosing the repositories you want runners for.

```bash
install -m 600 ~/Downloads/your-app.*.private-key.pem ~/.config/ghspot/app.pem
```

```toml
[github]
app_id = "123456"
private_key_file = "~/.config/ghspot/app.pem"
# installation_id = 98765432   # optional; discovered automatically
```

`installation_id` is worked out from the first configured repository, or from the app's
single installation. Set it explicitly only if the app is installed in several places.

`ghspot doctor` reports which mode is in use and, for an App, performs a real JWT exchange —
so a wrong app id or an unusable key surfaces there rather than an hour into a run.

### Supplying credentials from the environment

The environment always wins over the config file, so a systemd unit can inject secrets with
no file on disk:

| Variable | Mode |
|---|---|
| `GHSPOT_GITHUB_TOKEN` | Personal access token |
| `GHSPOT_GITHUB_APP_ID` | GitHub App |
| `GHSPOT_GITHUB_APP_PRIVATE_KEY` | GitHub App — `\n` escapes are accepted, since systemd `EnvironmentFile` cannot hold real newlines |

Credentials are never command-line arguments; that would put them in `ps` output.

## Install

```bash
git clone https://github.com/tguisep/gh-spot-docker-runners.git
cd gh-spot-docker-runners
uv sync                     # or: pip install -e .

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

```bash
sudo useradd --system --home /opt/ghspot --shell /usr/sbin/nologin ghspot
sudo usermod -aG docker ghspot
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

**`GitHub rejected the app assertion`.**
Either `app_id` does not match the private key, or the host clock is wrong. GitHub refuses a
JWT whose `iat` is in the future, so a clock running fast fails every request. Check with
`timedatectl` and confirm the app id on the app's settings page — it is the numeric **App
ID**, not the client id and not the installation id.

**Jobs cannot run `docker`.**
Set `docker_socket = true` for the pool, and confirm the image was built with the host's
`DOCKER_GID`. Inside a runner, `docker ps` should work as the `runner` user.

**The daemon exits immediately.**
Almost always configuration. Run `ghspot config validate` — it names the field.

## Backups

There is nothing to back up. The state database is a projection: delete it and the next tick
rebuilds the fleet from the containers' own labels. Back up `config.toml` and your credential — the token file or the app's private key.
