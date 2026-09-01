---
title: Host capacity
description: Ceilings across every pool at once, and what happens when they bite.
slug: 0.6/guides/host/capacity
---

`max_runners` bounds one pool. Nothing bounds the *machine* — four pools with room to spare
each start runners at the same time, on one box. `[capacity]` closes that gap.

```toml
[capacity]
max_containers = 8          # runners across every pool
max_cpus = 12.0             # summed `cpus` of the runners that are up
max_memory = "24g"

cpu_high_water = 85         # at or above, nothing new starts
memory_high_water = 90
```

Two mechanisms, working on different things:

| | Counts | Applies to |
|---|---|---|
| **Ceilings** | Runners that exist — arithmetic, no measurement | `max_containers`, `max_cpus`, `max_memory` |
| **Backpressure** | What the host is actually doing — measured each tick | `cpu_high_water`, `memory_high_water` |

## Ceilings

* `max_containers` **defaults to this machine's core count.** An unbounded host starts a
  container per queued job until it stops responding, and the first thing you learn is that the
  machine is gone. Cores is not a measurement — it is a defensible number the box names for
  itself.
* `max_containers = "unlimited"` lifts it on purpose, the way housekeeping spells `"never"`.
* `max_cpus` and `max_memory` only count pools that set `cpus` and `memory`. A fleet that sets
  neither is bounded by the count alone.
* Reservations, not usage: a pool with `cpus = 2.0` counts two whether the job uses them or not.

## Backpressure

At or above a high-water mark, **nothing new starts even where a pool has a free slot**. This
is the case arithmetic cannot see: everything else on the box, a job using far more than its
pool reserved, or a machine already struggling before the daemon woke up.

| Reading | Source | Note |
|---|---|---|
| CPU | One-minute load average as a percentage of cores | `100` = as much work queued as cores. Counts uninterruptible sleep, so heavy disk IO shows here — for deciding whether to pile more on, that is a feature |
| Memory | `MemAvailable` | The kernel's own estimate of what a new process could get. Not `MemTotal - MemFree`, which counts page cache as used and makes any working machine look 95% full |

A reading the daemon could not take never blocks anything: an unmeasurable host falls back to
the ceilings. A mechanism that stops the fleet when its own probe breaks is worse than none.
