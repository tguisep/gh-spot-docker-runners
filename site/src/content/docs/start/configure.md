---
title: "Configure"
description: "Writing the first configuration, by wizard or by hand."
---

## The short way

```bash
sudo ghspot setup
```

Asks the four things that cannot be guessed — credential, repository, pool name, whether jobs
may use Docker — then writes the file and says what to run next.

| | |
|---|---|
| Writes to | `/etc/ghspot/config.toml` as root, `~/.config/ghspot/config.toml` otherwise |
| Existing file | Refused without `--force` |
| Token | Its own file, created `0600` **before** anything is written to it |
| App private key | Pointed at, never copied |
| System install | Credentials chowned `root:ghspot 0640`, because the unit runs as `ghspot` |

### What lands in the file

`config.example.toml` with your answers substituted in — the whole commented reference, not
the four lines you were asked for. Settings you were not asked about keep the reference's
value, which is the daemon's own default.

Two exceptions, commented out instead: `cpus` and `memory`. The reference sets them to
illustrate them, and inheriting that would cap every job on the host at limits nobody chose.

If `config.example.toml` is missing — it ships at `/usr/share/doc/ghspot/` — the wizard writes
the short form rather than failing over a documentation file. Only live settings are echoed
back; the rest is in the file.

### Defaults that grant nothing

Both answer **no** unless you say otherwise:

- `let jobs use Docker` — a job with the socket has effective root on this host.
- `serve it` (the dashboard) — an unauthenticated API on this host.

Turning either on later is one line: `docker_socket` under `[pool.container]`, `api_bind`
under `[daemon]`.

### The build offer

The wizard then offers to build the image you chose — the one step nothing works without, since
a pool with no image starts no runners and says so only in the log.

| Condition | What happens |
|---|---|
| Image already present | Reported, no offer |
| Docker unreachable, or no image sources | Silent; `ghspot doctor` reports the real problem |
| Declined | Instruction stays in the next-steps list |
| Accepted | Streams the build, several minutes |

## By hand

```bash
cp config.example.toml config.toml
$EDITOR config.toml
ghspot config validate
ghspot doctor
```

`doctor` checks the config parses, Docker answers, the image exists, the socket is present, the
token resolves, and each repository is reachable with the permissions needed. Each is a failure
that would otherwise show up as a pool quietly never starting a runner. Failures print the
command that fixes them.

## Pools in their own files

One growing `config.toml` stops being reviewable around the fourth pool. Pools can live one
per file in a directory instead:

```toml
# /etc/ghspot/config.toml — above the first [section]
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

The `.deb` creates `/etc/ghspot/pools.d` and ships the `include` commented out. The Ansible
role writes a file per pool when `ghspot_pools_in_directory: true`, and deletes the file of a
pool removed from the inventory — nothing else would, since the include is a glob.

### How files are merged

| | |
|---|---|
| Order | Glob expanded and **sorted** — the fleet does not depend on directory order |
| Merging | **Merged, never overridden.** Every pool found runs; no last-one-wins |
| Duplicates | **Fatal**, naming both files |
| Scope | Pools and nothing else. `[github]`, `[daemon]` etc. stay in the main file |
| Nothing matched | Not an error. "At least one pool" still applies overall |

Pools in `config.toml` and pools in `pools.d` coexist as one set.

:::caution[`include` must sit above the first `[section]`]
In TOML a bare key belongs to the table above it, so written lower down it becomes
`github.include` and does nothing. `ghspot config validate` refuses that rather than starting
with no pools.
:::
