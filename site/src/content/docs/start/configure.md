---
title: "Configure"
description: "Writing the first configuration, by wizard or by hand."
---

## The short way

```bash
sudo ghspot setup
```

It asks the handful of things that cannot be guessed — token or GitHub App, which repository,
what the pool is called, whether jobs may use Docker — writes an ordinary configuration file,
and tells you the three commands that come next. The `.deb` prints the same invitation after
install.

Nothing it writes is special: the output is a file you could have written by hand, and it
says where it put it. A token goes into a file of its own, created `0600` *before* anything is
written to it. A GitHub App's private key is pointed at, never copied.

What it writes is `config.example.toml` with your answers filled into it — the whole
commented reference, not the four lines you were asked for. Everything the wizard did not
ask about is left commented out at the value the daemon would have used anyway, so the file
you end up editing later already explains itself and there is nothing to go and look up. The
two exceptions are `cpus` and `memory`: the reference sets them to illustrate them, and
inheriting that would cap every job on the host at limits nobody chose, so those are
commented out.

It prints back only the settings that are live. The rest is in the file.

Then it offers to build the runner image you chose, because that is the one step nothing
works without — a pool whose image is missing starts no runners and says so only in the
daemon's log. The offer appears only when there is something to do: an image already present
is reported and skipped, and if Docker is unreachable or the image sources are not installed
the wizard says nothing and leaves `ghspot doctor` to report the real problem. Declining is
free; the instruction stays in the list. Accepting takes several minutes and streams the
build.

If `config.example.toml` is not installed — it ships at `/usr/share/doc/ghspot/` — the wizard
writes the short form instead rather than failing over a documentation file.

Run as root it writes `/etc/ghspot/config.toml`; as anyone else, `~/.config/ghspot/config.toml`.
It refuses to overwrite an existing file without `--force`.

The two questions that hand something away answer **no** by default: letting jobs use Docker
gives a job effective root on the host, and serving the dashboard puts an unauthenticated API
on it. Neither is a thing to acquire by pressing enter past the paragraph explaining it.
Turning either on later is one line in the file — `docker_socket` under `[pool.container]`,
`api_bind` under `[daemon]`.

If the daemon is already running with the packaged configuration, its dashboard says the same
thing at `/ui` — a fresh install shows a setup screen rather than an empty and unexplained
fleet.

## By hand

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

## Pools in their own files

One growing `config.toml` stops being reviewable somewhere around the fourth pool. Pools can
live one per file instead, the way php-fpm keeps them in `php-fpm.d`:

```toml
# /etc/ghspot/config.toml — above the first [section], see below
include = "/etc/ghspot/pools.d/*.toml"
```

```toml
# /etc/ghspot/pools.d/web.toml
[[pool]]
name = "web"
repository = "you/your-project"
labels = ["self-hosted", "linux", "x64"]

[pool.container]
image = "ghspot/runner:ubuntu-24.04"
```

The `.deb` creates `/etc/ghspot/pools.d` and ships the `include` commented out in the
conffile. The Ansible role writes a file per pool when `ghspot_pools_in_directory: true`, and
removes the file of a pool you delete from the inventory — nothing else would, since the
include is a glob.

**How files are merged**, which is where the surprises would otherwise be:

| | |
|---|---|
| Order | The glob is expanded and **sorted**, so the fleet a host ends up with does not depend on the order a directory happens to return |
| Merging | Files are **merged, never overridden**. Every pool found is a pool that runs — there is no last-one-wins, because a pool silently replaced by a file later in the alphabet is not something you would debug quickly |
| Duplicates | **Fatal**, naming both files. The same as `php-fpm` refusing to start rather than picking one |
| Scope | An included file defines **pools and nothing else**. `[github]`, `[daemon]` and the rest stay in the main file, and putting one in a pool file is an error rather than a question about which wins |
| Nothing matched | Not an error by itself — an empty `pools.d` on a host still being set up is normal. "At least one pool" still applies overall |

Pools defined in `config.toml` and pools in `pools.d` coexist; they are one set.

> **`include` has to sit above the first `[section]`.** In TOML a bare key belongs to
> whichever table precedes it, so written lower down it becomes `github.include` and does
> nothing. `ghspot config validate` refuses that rather than starting with no pools.
