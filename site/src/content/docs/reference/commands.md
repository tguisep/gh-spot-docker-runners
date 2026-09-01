---
title: "Commands"
description: "Every CLI command, on one page."
---

| | |
|---|---|
| `ghspot setup` | Write a first configuration by answering a few questions |
| `ghspot doctor` | Check everything the daemon needs |
| `ghspot daemon` | Run the reconciliation loop |
| `ghspot image build [variant]` | Build a runner image on this host |
| `ghspot image list` | The variants, and the base image each starts from |
| `ghspot pool list` | Pools and what they hold |
| `ghspot pool status [name]` | One pool, with its runners |
| `ghspot runner list [--all]` | Runners, from the local projection |
| `ghspot runner logs <ref>` | Container output, or `--job` for GitHub's own |
| `ghspot runner stop <ref>` | Retire on both sides |
| `ghspot runner stop --all` | Empty the host — `--pool` narrows it, `--force` takes busy ones |
| `ghspot stats [--since 7d]` | Runners, jobs, failures and time spent |
| `ghspot config validate` | Load the configuration and report what it means |

Every listing takes `--watch 2` to repaint in place, and `runner list` takes `--usage` for CPU
and memory per container.

`-c` / `--config` points any command at a specific file. Without it, the usual places are
searched — `./config.toml`, `~/.config/ghspot/config.toml`, `/etc/ghspot/config.toml`.

## Beyond the CLI

A REST API is served alongside the daemon when `api_bind` is set, with a
[web dashboard](../../guides/operate/dashboard/) at `/ui` covering the same ground. See
[the REST API](../../guides/operate/api/).
