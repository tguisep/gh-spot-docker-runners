---
title: "Requirements"
description: "What the host needs, and the credential it takes."
---

## The host

- Linux with Docker, and the account running the daemon in the `docker` group.
- Python 3.12 or newer **only if installing from source**. The `.deb` bundles its own
  interpreter, so a distribution upgrade changing `python3` cannot break it.
- Nothing inbound. The daemon polls GitHub, so it runs behind NAT on a machine with no public
  address.

## The credential

A fine-grained personal access token or a GitHub App, **scoped to the repositories it will
serve** — not to everything the account can reach.

| Permission | Why |
|---|---|
| Administration: read & write | Mint just-in-time runner registrations |
| Actions: read | See which jobs are queued |

Nothing else. [Authentication](../authentication/) walks through both, with the exact screens and
the errors each wrong setting produces.

A token is quickest to start with; an App is better for anything left running — its rate limit
belongs to the installation rather than to you personally, and its tokens expire hourly on
their own.

## What never reaches a container

Neither credential. Runners receive a single-use just-in-time configuration blob and nothing
else, in both modes — see [ADR 1](../../reference/adr/0001-just-in-time-registration/).
