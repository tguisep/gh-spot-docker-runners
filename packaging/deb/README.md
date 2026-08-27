# Debian packaging

## Build

```bash
packaging/deb/build-in-docker.sh          # safe anywhere; builds in a container
packaging/deb/verify.sh dist/*.deb        # install it on a clean system and check it
```

CI does the same on every pull request, and on a tag it publishes the result.

## Why the package bundles a Python interpreter

A virtualenv records the absolute path and version of the interpreter that created it. A
package built on Ubuntu 24.04 (python3.12) would therefore fail on 26.04 (python3.14), and
would also break if the host upgraded its distribution underneath it.

Bundling removes the coupling. The package carries a standalone CPython under
`/opt/ghspot/python` and a virtualenv at `/opt/ghspot/.venv` built from it. It costs about
35 MB and buys a package that runs on any glibc distribution — `verify.sh` proves the point
by installing it on a container with no system python at all.

## Why the build needs `/opt/ghspot`

Same reason: the virtualenv must be created at its final location, or the paths it records
are wrong. `build.sh` therefore writes to `/opt/ghspot` and needs a machine where that is
acceptable — a container, or a disposable CI runner. `build-in-docker.sh` is the safe
wrapper for a developer machine.

## Layout

| Path | Owner |
|---|---|
| `/opt/ghspot/python`, `/opt/ghspot/.venv` | dpkg |
| `/usr/bin/ghspot` | dpkg — a wrapper onto the virtualenv |
| `/lib/systemd/system/ghspot.service` | dpkg |
| `/etc/ghspot/config.toml` | dpkg **conffile** — edits survive upgrades |
| `/etc/ghspot/env` | the operator; never packaged, holds the credential |
| `/var/lib/ghspot/` | created by `postinst`, removed on purge |

## Maintainer scripts

- **postinst** creates the `ghspot` system account, adds it to `docker`, and prints the next
  steps. It deliberately does not enable the service: it cannot work until a repository and
  credential are configured, and starting a daemon that can only fail helps nobody.
- **prerm** stops the service with `SIGTERM`, so the daemon finishes its tick and leaves busy
  runners alone. Removing this package does not entitle it to fail someone's build.
- **postrm** removes state and the account only on `purge`. Credentials live in
  `/etc/ghspot`, so a plain `remove` must not delete them.

## Releasing

```bash
# 1. bump the version in pyproject.toml, commit
# 2. tag it — the workflow refuses a tag that disagrees with pyproject
git tag v0.2.0 && git push origin v0.2.0
```

The workflow builds amd64 and arm64, verifies each on a clean system, and publishes them to a
GitHub release with `SHA256SUMS`.
