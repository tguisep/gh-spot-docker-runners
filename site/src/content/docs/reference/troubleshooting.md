---
title: "Troubleshooting"
description: "Symptoms, what causes them, and what to do."
---

**Jobs stay queued and no container appears.**
Labels are the usual cause — the runner must carry *every* label the job asks for. Compare
`ghspot pool list` against your `runs-on`. Then check `ghspot daemon` output for a
`tick.error`.

**A runner shows `Offline` on github.com.**
Leave it. The next tick either adopts it or deletes it. If it persists past a few ticks, look
for `tick.error` lines — the daemon only deletes runners whose name starts with `ghspot-`
and which are offline, so a hand-registered runner is never touched.

**`/etc/ghspot/token is readable by other users`.**
Check *which* others before acting on it. `0640 root:ghspot` is the packaged layout and is
correct — the daemon runs as `ghspot` and a credential only root can read stops it starting.
The warning fires for genuinely wider access: any permission for `other`, group-write, or
group-read by a group that is not the daemon's. The remedy it prints is the packaged layout,
not `chmod 600`, which would break the service.

**`could not read the token from /etc/ghspot/token: Permission denied`, and the unit will
not start.**
The daemon runs as `ghspot`, not as the root you ran the wizard with. Wizards from before
this was fixed left the credential `0600 root:root`:

```bash
sudo chown root:ghspot /etc/ghspot/token && sudo chmod 640 /etc/ghspot/token
sudo systemctl start ghspot
```

`ghspot setup` now does this for anything it writes under `/etc/ghspot`, and `ghspot doctor`
checks it — the check exists because running `doctor` under `sudo` otherwise passes every
file test as root while the service cannot start at all.

**`/ui` returns 404 but the API answers.**
The dashboard is not installed. `ls /usr/share/ghspot/web` — if it is missing, the package was
built without a usable node. Packages released before this was fixed all were. Build it from a
checkout as a stopgap:

```bash
cd web && npm ci && npm run build
sudo mkdir -p /usr/share/ghspot/web && sudo cp -a dist/. /usr/share/ghspot/web/
sudo systemctl restart ghspot
```

**A configuration change does nothing.**
Settings are read **once, at startup** — pools, labels, limits, the forge client, all of it.
Editing `config.toml` or a file under `pools.d/` changes nothing in a running daemon:

```bash
sudo systemctl restart ghspot
```

`/health` reports `config_stale: true` once the file is newer than what the daemon read, and
the dashboard shows a banner saying so, because the alternative was watching a label you just
added fail to appear with no way to tell a stale process from a bad file. Note that `ghspot
doctor` cannot tell you this: it reads the file itself, so it always reports on what is on
disk rather than on what the daemon is running.

**The GitHub pane says no job was found.**
Nothing records which job a runner takes while it works — GitHub's runner list reports *that*
a runner is busy without saying which job it took. So the job is looked up when you ask for it,
by searching the last 30 workflow runs for one whose `runner_name` matches. The answer is
written back to the runner's record, so the search happens once per runner and never again.

Two consequences. A runner whose run has scrolled past those 30 is not found — nothing is
broken, the search window simply does not reach it. And a runner that never registered is
never searched for, because it cannot have been handed a job.

**A retired runner's logs are empty.**
Retiring removes the container, and Docker drops its output with it — proven, not assumed: the
same `docker logs` call returns the output one moment and an empty string the next. The daemon
now copies the last 500 lines into its state database between stopping the container and
removing it, so `ghspot runner logs <id>` and the dashboard still answer for a runner that no
longer exists. Both say when what you are reading is that kept copy rather than a live one.

Two limits worth knowing. Runners retired before this existed have nothing kept — there is no
copy to go back for. And the archive is pruned with the runner record it belongs to, so it
lasts as long as the record does (the last 500 terminal runners). If the runner ran a job,
GitHub's own log outlives both: `ghspot runner logs <id> --job`.

**`ImageNotFoundError`.**
The runner image is not built on this host. Run `ghspot image build <variant>`; `ghspot doctor`
prints the exact command for the image the pool asks for.

**`the runner image sources are not installed`.**
`ghspot image build` found neither `/usr/share/ghspot/images/runner` nor a checkout. On a
packaged host that means the package is older than this command or the directory was removed
— reinstall it. Otherwise point `GHSPOT_RUNNER_IMAGES` at a copy of `images/runner`.

**Rate limited.**
`ForgeRateLimitedError` means the hourly budget is spent. Raise `poll_interval` or reduce the
number of pools. Conditional requests make idle polling nearly free, so this usually means
many repositories with constant activity. Switching from a personal access token to a GitHub
App gives the daemon its own budget instead of sharing yours.

**A credential or permission error.**
[`authentication.md`](../start/authentication.md#when-permissions-are-wrong) has a table mapping each
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
