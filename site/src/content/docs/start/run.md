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
