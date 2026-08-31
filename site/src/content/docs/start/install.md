---
title: "Install"
description: "From the .deb, with Ansible, or from source."
---

## From a .deb (recommended on Debian and Ubuntu)

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

## With Ansible

For more than one host, or to keep the configuration in version control:

```bash
cd deploy/ansible
cp inventory/hosts.example.ini inventory/hosts.ini
ansible-vault create inventory/group_vars/runners/vault.yml
ansible-playbook -i inventory/hosts.ini playbook.yml --ask-vault-pass
```

The role installs the package, renders the configuration and credential, builds the runner
images, starts the service, and finishes by running `ghspot doctor` — so a green run means
the daemon can actually work. See [`deploy/ansible/README.md`](https://github.com/tguisep/gh-spot-docker-runners/blob/main/deploy/ansible/README.md).

## From source

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
ghspot image build ubuntu-24.04     # `ghspot image list` names the variants
```

The image sources ship inside the package, so this works on a host with no clone. It runs
`images/runner/build.sh` from wherever they are installed — `/usr/share/ghspot/images/runner`
from the `.deb`, the checkout when you are in one — which is what keeps the packaged build and
the development build the same build. `--sources` overrides the search, as does
`GHSPOT_RUNNER_IMAGES`.

`DOCKER_GID` must match the host's `docker` group, or the unprivileged `runner` user inside
the container cannot use the mounted socket. The script detects it; you do not pass it.
