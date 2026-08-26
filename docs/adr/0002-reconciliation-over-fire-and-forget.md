# 2. Converge continuously instead of acting once

**Status:** accepted · 2026-08-26

## Context

Starting a runner means changing two systems — GitHub and Docker — with no transaction
between them, from a process that can be killed at any point.

A fire-and-forget design does the steps in order and hopes. Every interruption leaves debris
that nothing subsequently notices: a `SIGKILL`led container leaves a runner stuck `Offline`,
a crash mid-provision leaves a registration with nothing behind it, a `docker rm` by hand
leaves GitHub describing a runner that will never answer.

The reference project's answer is `delete-offline-runners.sh`, run manually. That works
because a human decides when the state is wrong — the system itself cannot tell.

## Decision

A single `tick()` runs on an interval. It observes Docker, GitHub and the local records,
rebuilds each pool from what it actually found, and moves reality toward the declared
configuration. Repair is not a separate mode; it is what the loop does.

## Consequences

**Gained:**

- Every drift case is handled by construction rather than by a script someone remembers.
- `tick()` is idempotent and crash-safe: whenever the daemon dies, the next tick re-derives
  everything. At most one tick of work is lost.
- Because nothing is carried between ticks, a tick that raises can simply be logged and
  dropped.
- The whole loop is testable against fakes, including crash injection.

**Given up:**

- Reaction is bounded below by the poll interval. Something happening 200ms after a tick
  waits for the next one.
- Steady-state API traffic, though conditional requests make an idle repository nearly free
  to watch (see ADR 3).
- More code than a shell script — offset by the fact that it can be tested.

## Alternatives rejected

**Event-driven with a durable queue.** React to Docker events and GitHub webhooks, persist
intent, retry on failure. Lower latency, but correctness then depends on the queue and the
retry logic being right, and recovering from a *missed* event still requires reconciliation.
The loop is the thing that has to work; adding events on top is an optimisation, not a
foundation.

**Fire-and-forget plus a cleanup command.** What the reference does. Rejected because
"remember to run the script" is not a design.
