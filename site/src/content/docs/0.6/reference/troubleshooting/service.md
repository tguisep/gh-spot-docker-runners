---
title: Configuration and the service
description: The unit will not start, or ignores what you changed.
slug: 0.6/reference/troubleshooting/service
---

## A configuration change does nothing

Settings are read at startup and on reload — not on every tick.

```bash
sudo systemctl reload ghspot    # pools, labels, ceilings — runners untouched
```

`reload` is what you want. `restart` also works and also retires every runner, failing the
builds in flight. If a reload was refused, `journalctl -u ghspot | grep reload.rejected` names
the problem; the daemon keeps running the configuration it already had.

`/health` reports `config_stale: true` once the file is newer than what the daemon read, and
the dashboard shows a banner. `ghspot doctor` cannot tell you this: it reads the file itself,
so it always reports on disk rather than on what the daemon is running.

## `/ui` returns 404 but the API answers

The dashboard is not in the package. `ls /usr/share/ghspot/web` — if missing, it was built
without a usable node, as every release before this was fixed. Stopgap from a checkout:

```bash
cd web && npm ci && npm run build
sudo mkdir -p /usr/share/ghspot/web && sudo cp -a dist/. /usr/share/ghspot/web/
sudo systemctl restart ghspot
```

## `the runner image sources are not installed`

`ghspot image build` found neither `/usr/share/ghspot/images/runner` nor a checkout. Reinstall
the package, or point `GHSPOT_RUNNER_IMAGES` at a copy of `images/runner`.

## `pip: command not found` after creating a virtualenv

Debian and Ubuntu ship `python3` without `ensurepip`, so `python3 -m venv` produces a
virtualenv containing only symlinks. Install `python3-venv`, **delete** the incomplete
virtualenv, and recreate it — installing the package does not repair one that exists. The
`.deb` bundles its own interpreter and avoids this.
