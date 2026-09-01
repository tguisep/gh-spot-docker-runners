---
title: 4. Treat storage as a projection, not the source of truth
description: An architecture decision, with what was rejected.
slug: 0.6/reference/adr/0004-storage-as-a-projection
---

**Status:** accepted · 2026-08-26

## Context

The daemon needs to remember which runners it created, so it can tie a container to its
registration and report history. The obvious design makes that database authoritative:
write intent, act, update.

That makes the database a single point of failure. Corrupt it, roll it back, or move the
daemon to another host, and the running fleet becomes unrecognisable — containers nothing
claims, registrations nothing will delete.

## Decision

Docker container labels and the GitHub API are the source of truth. SQLite holds a
projection, rebuilt on every tick, plus event history for the CLI.

Correlation lives on the containers themselves, in `io.ghspot.*` labels: the runner id, the
GitHub runner id, the pool, and the creation time.

## Consequences

**Gained:**

* Losing the database costs history, never correctness. The next tick reads the labels off
  the running containers and adopts them back.
* No migration story is needed for the fleet — only for the history table.
* No locking, no transactions spanning external calls, no write path that can wedge a tick.
  A failed write is not allowed to abort reconciliation.

**Given up:**

* History is lost with the file. Acceptable: it is diagnostic, not operational.
* Every tick re-reads container labels rather than trusting a cached view. Cheap — one
  `docker ps` — and it is what makes adoption work.

## Verification

`test_a_wiped_database_costs_history_not_correctness` and
`test_a_container_with_no_record_is_adopted` assert this directly. If the projection ever
becomes load-bearing, they fail.
