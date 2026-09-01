---
title: "Run"
description: "Running it in the foreground, then as a service."
---

Interactively, to watch it work:

```bash
ghspot daemon
```

Then push a commit with a workflow targeting your labels:

```yaml
jobs:
  build:
    runs-on: [self-hosted, ubuntu-24.04]
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

## As a service

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

### stop, restart and reload are three different things

**The host is the master: when the daemon stops, the fleet stops.** A runner outliving the
process that made it keeps taking jobs with nothing enforcing `idle_timeout` or
`max_job_duration`, nothing reaping it, and a registration on GitHub that corresponds to
nothing watching.

| Command | Daemon | Runners |
|---|---|---|
| `systemctl reload ghspot` | Re-reads the configuration in place | **Untouched** — builds keep running |
| `systemctl restart ghspot` | Replaced | Retired, then rebuilt by the first tick |
| `systemctl stop ghspot` | Stopped | **Retired** — registrations deleted, containers removed |

Stop and restart cost the jobs in flight: they fail and have to be re-run. The deliberate
trade — a CI run can be replayed, a fleet nobody owns cannot be reasoned about.

Use `reload` for a configuration change. It applies the pools, labels and ceilings and leaves
every runner where it is, which is what makes changing a label routine rather than something
scheduled around the builds. A file that no longer parses is **refused**, logged as
`reload.rejected`, and the daemon carries on with what it had.

`TimeoutStopSec=300` covers finishing the current tick and stopping every container. They stop
concurrently, so it is one container's grace period rather than the sum of them.

If you installed elsewhere, point `ExecStart=` at your own path — `command -v ghspot` shows
it. The unit runs as the `ghspot` user, so a binary under *your* home directory will not be
readable by it; that is why `/opt/ghspot` is the default.

To upgrade later:

```bash
cd gh-spot-docker-runners && git pull
sudo /opt/ghspot/.venv/bin/pip install --quiet "$PWD"
sudo systemctl restart ghspot
```
