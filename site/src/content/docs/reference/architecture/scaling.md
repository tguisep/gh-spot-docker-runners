---
title: "Scaling"
description: "How many runners to keep, the demand signal, and where labels live."
---

## Scaling

`plan_scaling(pool, demand, now) -> ScalePlan` is a pure function. Its rules, in order:

1. Cover every queued job this pool can serve.
2. Keep `min_idle` runners warm **on top of** the queue.
3. Never exceed `max_runners`; never start more than `max_launch_per_tick` at once.
4. Reap runners idle beyond `idle_timeout`, never below `min_idle`.
5. Kill runners whose job overran `max_job_duration`.

Two anti-flapping rules are worth naming because they are not obvious:

- **Nothing is reaped while jobs are queued.** Reaping capacity in the same tick we are short
  of it would oscillate.
- **A plan never launches and reaps at once.** They are computed from one snapshot, so the
  plan cannot contradict itself.

Runners in `REGISTERED` and `STARTING` count as available capacity. Without that, a runner
booting for job A would be ignored and a second container started for the same job.

## Demand signal

The daemon polls rather than receiving webhooks, because a home server behind NAT cannot
expose an endpoint without a tunnel. Polling is affordable because of one detail:

> Every GET carries the `ETag` from the previous call, and a `304 Not Modified` **does not
> count against the rate limit.**

An idle repository is therefore nearly free to watch, even at a 15-second interval. Queued
jobs are gathered from `in_progress` runs as well as `queued` ones — a matrix leg is queued
after its run has already started, and would otherwise stay invisible.

Webhooks remain one adapter away: a `workflow_job` handler produces the same `QueuedJob`
value objects the poller does, and the policy would not change.

## Where the labels live

Correlation survives a daemon restart because it lives on the containers themselves:

| Label | Purpose |
|---|---|
| `io.ghspot.managed` | Finds this daemon's containers and nothing else |
| `io.ghspot.runner-id` | Ties a container to its record |
| `io.ghspot.github-runner-id` | Ties a container to its registration |
| `io.ghspot.pool` | Which pool it belongs to |
| `io.ghspot.created-at` | Rebuilding age after adoption |
