---
title: "1. Register runners with just-in-time configurations"
description: "An architecture decision, with what was rejected."
---

**Status:** accepted · 2026-08-26

## Context

A self-hosted runner has to authenticate to GitHub before it can accept work. The
conventional approach — the one the reference project uses — passes a personal access token
into every container and runs `config.sh` inside it.

That token is readable by anything running in the container, which includes every step of
every job. On a repository that accepts pull requests from forks, that is a complete
compromise of the token's scope. Even on private repositories it is a broad credential
sitting somewhere it is not needed.

## Decision

Register runners from the host, using
`POST /repos/{owner}/{repo}/actions/runners/generate-jitconfig`. The container receives only
the resulting `encoded_jit_config` blob.

## Consequences

**Gained:**

- The token stays in the daemon process. A compromised job cannot register runners, read
  repository settings, or reach any other repository the token can see.
- Just-in-time runners are ephemeral by construction: one job, then the process exits and
  GitHub de-registers. No `--ephemeral` flag, no removal token, no cleanup step in the
  container.
- The GitHub runner id is known *before* the container exists. This is what makes crash
  recovery possible at all — every container can be tied back to its registration regardless
  of what order things happened in.

**Given up:**

- Runners cannot be reused across jobs. For a home server this is a feature — a clean
  environment per job — but it means container start time is on the critical path, which is
  what `min_idle` exists to hide.
- Registration is the daemon's job, so the daemon must be running for new runners to appear.
  Existing runners finish their jobs regardless.

## Alternatives rejected

**Token in the container, `config.sh --ephemeral`.** Simpler, and what most examples do. It
was rejected because the credential exposure is the one thing that cannot be fixed later
without redesigning provisioning.

**A registration-token broker on the host.** Short-lived tokens (one hour) handed to
containers on request. Narrows the window but does not close it, and adds a service to run.
JIT configs achieve strictly more for less.
