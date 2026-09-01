---
title: Decisions
description: Why the shape of this thing is the shape it is.
slug: 0.6/reference/adr
---

One file per decision that would be expensive to reverse, or that a reader would otherwise
have to reconstruct from the code. Each records what was chosen, what was rejected, and what
it costs — the rejected alternatives are the useful part.

| # | Decision | Status |
|---|---|---|
| [0001](0001-just-in-time-registration/) | Register runners with just-in-time configs | Accepted |
| [0002](0002-reconciliation-over-fire-and-forget/) | Converge continuously instead of acting once | Accepted |
| [0003](0003-polling-over-webhooks/) | Poll the API for demand instead of receiving webhooks | Accepted |
| [0004](0004-storage-as-a-projection/) | Treat storage as a projection, not the source of truth | Accepted |
| [0005](0005-docker-socket-over-dind/) | Mount the host Docker socket rather than run Docker-in-Docker | Accepted |
| [0006](0006-github-app-alongside-pat/) | Support GitHub Apps alongside personal access tokens | Accepted |
