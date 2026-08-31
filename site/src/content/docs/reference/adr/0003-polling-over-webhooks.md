---
title: "3. Poll the API for demand instead of receiving webhooks"
description: "An architecture decision, with what was rejected."
---

**Status:** accepted · 2026-08-26

## Context

The daemon needs to know when jobs are queued. GitHub offers `workflow_job` webhooks, which
arrive within a second. It also exposes the queue over REST.

The target host is a VM on a home server behind NAT. Receiving webhooks would require a
tunnel or a port forward, a public hostname, TLS, and webhook signature verification — a
second thing to operate and secure, before the first thing works at all.

## Decision

Poll `GET /actions/runs` and the jobs of each run, on an interval, using conditional
requests.

## Consequences

**Gained:**

- No inbound connectivity. The daemon works behind NAT with nothing exposed.
- No webhook secret to manage, no signature verification, no public endpoint to defend.
- A missed poll is self-correcting; a missed webhook is not.

**Given up:**

- Latency: up to one poll interval, 15 seconds by default.
- Steady-state API calls. This is affordable because of one detail that is easy to miss:
  conditional requests returning `304 Not Modified` **do not count against the rate limit**.
  Every GET carries the previous `ETag`, so watching an idle repository costs essentially
  nothing however short the interval.

## Notes

`in_progress` runs are polled alongside `queued` ones. A matrix leg is queued *after* its run
has started, and scanning only queued runs would miss it until the run finished.

`MAX_RUNS_PER_POLL` caps how many runs are examined. A backlog deeper than that is already
beyond what one home server will clear, and the next tick continues from there.

## Reversibility

This is the most reversible decision in the project, which is part of why it was acceptable.
A `workflow_job` webhook handler produces the same `QueuedJob` value objects the poller
produces. Adding one is a new adapter and a route; the scaling policy and the domain do not
change.
