---
title: "Troubleshooting"
description: "Symptoms, what causes them, and what to do."
---

| Symptom | Cause | Fix |
|---|---|---|
| Jobs stay queued, no container | Pool lacks a label the job asks for | Compare `ghspot pool list` with `runs-on` |
| Runner shows `Offline` on github.com | Normal between ticks | Leave it. Persisting past a few ticks → check `tick.error` |
| Daemon exits immediately | Configuration | `ghspot config validate` names the field |
| `ImageNotFoundError` | Image not built here | `ghspot image build <variant>` |
| Jobs cannot run `docker` | Socket off, or wrong `DOCKER_GID` in the image | `docker_socket = true`; rebuild the image on this host |
| `ghspot: command not found` | `uv sync` installs to `.venv/`, not `PATH` | `uv run ghspot`, or `uv tool install .` |

## Credentials

### `/etc/ghspot/token is readable by other users`

Check *which* others first. `0640 root:ghspot` is the packaged layout and is **correct** — the
daemon runs as `ghspot`, and a credential only root can read stops it starting.

The warning fires for genuinely wider access: any permission for `other`, group-write, or
group-read by a group that is not the daemon's. Its remedy is the packaged layout, not
`chmod 600`.

### `could not read the token ...: Permission denied`, unit will not start

The daemon runs as `ghspot`, not the root you ran the wizard with.

```bash
sudo chown root:ghspot /etc/ghspot/token && sudo chmod 640 /etc/ghspot/token
sudo systemctl reset-failed ghspot && sudo systemctl start ghspot
```

`reset-failed` is needed after five rapid failures hit `StartLimitBurst`. Current versions do
this at setup time, and `ghspot doctor` checks it — because `sudo ghspot doctor` otherwise
passes every file test as root while the service cannot start at all.

### Other credential or permission errors

[Authentication](../start/authentication.md#when-permissions-are-wrong) maps each message to its
cause. The two that catch people out:

- `GitHub rejected the app assertion` — wrong App ID, or a skewed host clock.
- A GitHub App permission change does not apply until the installation **accepts** it.

### Rate limited

`ForgeRateLimitedError` means the hourly budget is spent. Conditional requests make idle
polling nearly free, so this usually means many repositories with constant activity.

- Raise `poll_interval`, or reduce pools.
- Move from a personal access token to a GitHub App — its own budget instead of yours.

## Configuration and the service

### A configuration change does nothing

Settings are read **once, at startup** — pools, labels, limits, clients.

```bash
sudo systemctl restart ghspot
```

`/health` reports `config_stale: true` once the file is newer than what the daemon read, and
the dashboard shows a banner. `ghspot doctor` cannot tell you this: it reads the file itself,
so it always reports on disk rather than on what the daemon is running.

### `/ui` returns 404 but the API answers

The dashboard is not in the package. `ls /usr/share/ghspot/web` — if missing, it was built
without a usable node, as every release before this was fixed. Stopgap from a checkout:

```bash
cd web && npm ci && npm run build
sudo mkdir -p /usr/share/ghspot/web && sudo cp -a dist/. /usr/share/ghspot/web/
sudo systemctl restart ghspot
```

### `the runner image sources are not installed`

`ghspot image build` found neither `/usr/share/ghspot/images/runner` nor a checkout. Reinstall
the package, or point `GHSPOT_RUNNER_IMAGES` at a copy of `images/runner`.

### `pip: command not found` after creating a virtualenv

Debian and Ubuntu ship `python3` without `ensurepip`, so `python3 -m venv` produces a
virtualenv containing only symlinks. Install `python3-venv`, **delete** the incomplete
virtualenv, and recreate it — installing the package does not repair one that exists. The
`.deb` bundles its own interpreter and avoids this.

## Logs

### A retired runner's logs are empty

Retiring removes the container and Docker drops its output with it. The daemon copies the last
500 lines into its state database between stopping and removing, so the CLI and dashboard still
answer — both say when you are reading that kept copy.

| Limit | |
|---|---|
| Retired before this existed | Nothing kept; there is no copy to go back for |
| Retention | Pruned with the runner record — the last 500 terminal runners |
| Ran a job? | GitHub's log outlives both: `ghspot runner logs <id> --job` |

### The GitHub pane says no job was found

Nothing records which job a runner takes while it works — GitHub's runner list reports *that* a
runner is busy, not which job. So it is searched for on demand, across the last 30 workflow
runs, matching `runner_name`. The answer is written back, so the search happens once per runner.

- A run older than those 30 is not found. Nothing is broken; the window does not reach it.
- A runner that never registered is never searched for — it cannot have taken a job.

### A job sits with no logs, and its runner has vanished

The runner was removed mid-job, so nothing reported back.

```bash
journalctl -u ghspot --since "1 hour ago" | grep -E "blind|unreachable|retired"
```

A tick that cannot reach Docker now does nothing rather than concluding no containers exist —
that conclusion used to tear down the fleet mid-job. A Docker restart during jobs is the usual
trigger. Seeing it on an older version means upgrade.
