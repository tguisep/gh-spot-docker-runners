---
title: "Running this project's own CI on your runners"
description: "Pointing this repository's workflows at your own fleet."
---

This repository can run its own CI on its own runners, which is the most honest test it has:
if reconciliation breaks, CI stops.

It is **off by default**. Every workflow gets its `runs-on` from `select-runner.yml`, and that
returns GitHub-hosted unless a repository variable says otherwise:

```
Settings → Secrets and variables → Actions → Variables
USE_SELF_HOSTED = true
```

A variable rather than a code change, so moving CI off the fleet — for a migration, a host
reboot, or a bad afternoon — is a toggle and not a pull request. Unset, nothing waits on a
machine that may be down.

## Labels

`select-runner.yml` asks for `[self-hosted, ubuntu-24.04]`. A pool serves a job only when it
carries **every** label the job asks for, so any pool whose labels are a superset can take this
repository's work — including the one the wizard writes:

```toml
[[pool]]
labels = ["self-hosted", "linux", "x64", "ubuntu-24.04"]
```

Two labels rather than four, because `linux` and `x64` are implied by `ubuntu-24.04` and every
extra one is another thing a pool has to carry before it can serve. Ask for a label nobody
built and the job queues until GitHub gives up on it, 24 hours later, with nothing in the log
to say why.

## Fork pull requests never reach your machine

This matters more than anything else on this page.

GitHub is explicit that [self-hosted runners and public repositories are a dangerous
combination](https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners/about-self-hosted-runners#self-hosted-runner-security):
anyone can fork a public repository, open a pull request, and have their workflow execute on
your runner. With `docker_socket = true` that is **root on your home server, offered to
strangers**.

The workflow therefore picks its runner rather than hard-coding one:

| Event | Asked for | `USE_SELF_HOSTED` | Runs on |
|---|---|---|---|
| Pull request from a fork | anything | anything | **GitHub-hosted** — the author could be anyone |
| `push`, or a pull request from a branch here | `hosted` | anything | GitHub-hosted |
| `push`, or a pull request from a branch here | `self-hosted` | anything | Self-hosted |
| `push`, or a pull request from a branch here | `auto` | `true` | Self-hosted |
| `push`, or a pull request from a branch here | `auto` | unset | GitHub-hosted |

The fork check comes first and **nothing below it can reach past it** — not the variable, not
the dispatch input. Turning the fleet on is a decision about your own branches, never about a
stranger's.

## Running one on demand

Every workflow takes **Run workflow** in the Actions tab. The five that can use the fleet —
Python, Dashboard, Ansible, Release, Upstream toolset — offer a **Where to run it** choice:

| | |
|---|---|
| `auto` | What `USE_SELF_HOSTED` says. The default |
| `hosted` | GitHub-hosted, this run only |
| `self-hosted` | The fleet, this run only |

Which is the answer to both "is my fleet actually working?" and "the fleet is wedged, get me a
green build" — without touching the variable and changing it for everyone else.

Docs, Packaging and Runner images take **Run workflow** too, with no choice: every job in them
is pinned to GitHub-hosted for a reason written at the job, and offering the fleet would only
offer a broken run.

A `select-runner` job resolves this once and every other job reads its output, so the rule
lives in one place instead of being repeated — and forgotten — per job.

**This is not optional if your repository is public.** Deleting that logic and writing
`runs-on: [self-hosted, ...]` directly hands arbitrary code execution to anyone with a GitHub
account.

## When the fleet is down

CI queues rather than failing: a job with no matching runner waits, and GitHub fails it after
24 hours with nothing in the log to say why.

Set `USE_SELF_HOSTED` to anything but `true`, or delete it, and re-run. Everything goes back to
GitHub-hosted immediately — including jobs already queued, once they are re-run.

## Which jobs cannot move

Jobs running `docker run -v "${PWD}:/src"` stay on GitHub-hosted.

Inside a runner container the Docker client talks to the **host's** daemon, so a workspace
path resolves on the host, where it does not exist — Docker mounts an empty directory and the
job fails confusingly. `docker build` is unaffected: it streams its context from the client.

Moving them would require the runner's work directory to be a host bind mount at an identical
path inside the container. That is a change to the runner image, not to the workflow.
