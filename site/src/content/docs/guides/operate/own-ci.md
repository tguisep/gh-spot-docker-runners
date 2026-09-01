---
title: "Running this project's own CI on your runners"
description: "Pointing this repository's workflows at your own fleet."
---

The workflow in `.github/workflows/ci.yml` runs on the self-hosted fleet, which is the most
honest test the project has: if reconciliation breaks, CI stops.

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

| Event | Runs on | Why |
|---|---|---|
| `push` | Self-hosted | Requires write access |
| Pull request from a branch in this repository | Self-hosted | Requires write access |
| Pull request from a fork | GitHub-hosted | The author could be anyone |
| `workflow_dispatch` with `force_hosted` | GitHub-hosted | Manual escape hatch |

A `select-runner` job resolves this once and every other job reads its output, so the rule
lives in one place instead of being repeated — and forgotten — per job.

**This is not optional if your repository is public.** Deleting that logic and writing
`runs-on: [self-hosted, ...]` directly hands arbitrary code execution to anyone with a GitHub
account.

## When the fleet is down

CI queues rather than failing: a job with no matching runner waits, and GitHub fails it after
24 hours. To get a green build without waiting, re-run the workflow from the Actions tab with
**Run workflow → force_hosted**, which puts everything back on GitHub-hosted runners.

## Which jobs cannot move

Jobs running `docker run -v "${PWD}:/src"` stay on GitHub-hosted.

Inside a runner container the Docker client talks to the **host's** daemon, so a workspace
path resolves on the host, where it does not exist — Docker mounts an empty directory and the
job fails confusingly. `docker build` is unaffected: it streams its context from the client.

Moving them would require the runner's work directory to be a host bind mount at an identical
path inside the container. That is a change to the runner image, not to the workflow.
