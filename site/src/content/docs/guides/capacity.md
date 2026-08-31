---
title: "Keeping the host from being overloaded"
description: "Ceilings across every pool, and what happens when they bite."
---

`max_runners` bounds one pool. Nothing bounds the *machine* — four pools with room to spare
will each start runners at the same time, on one box. Three settings close that gap, and they
work at different levels:

```toml
[capacity]
max_containers = 8          # runners across every pool
max_cpus = 12.0             # summed `cpus` of the runners that are up
max_memory = "24g"

cpu_high_water = 85         # at or above, nothing new starts
memory_high_water = 90
```

## Ceilings, on what is committed

**`max_containers` defaults to this machine's core count.** A host with no ceiling at all will
cheerfully start a container per queued job until it stops responding, and the first thing an
operator learns is that the machine is gone. Cores is not a measurement of anything — it is a
defensible number the box can name for itself. Set it explicitly to something else, or
`max_containers = "unlimited"` to lift it on purpose, the way housekeeping spells `"never"`.

`max_containers`, `max_cpus` and `max_memory` are arithmetic over the runners that exist.
They need no measurement and cannot be wrong: a pool reserving `cpus = 2.0` counts two
against `max_cpus` whether the job uses them or not.

`max_containers` is the one that always applies. The other two only count pools that set
`cpus` and `memory`, so a fleet that sets neither is bounded by the count alone.

## Backpressure, on what is measured

`cpu_high_water` and `memory_high_water` are the other half. At or above them **nothing new
starts, even where a pool has a free slot** — which is the case the arithmetic cannot see:
everything else running on the box, a job using far more than its pool reserved, or a machine
already struggling before the daemon woke up.

| Reading | Where it comes from |
|---|---|
| CPU | The one-minute load average as a percentage of cores, so `100` means as much work queued as the machine has cores. It counts uninterruptible sleep, so heavy disk IO shows up here — for deciding whether to pile more on, that is a feature |
| Memory | `MemAvailable`, the kernel's own estimate of what a new process could get. Not `MemTotal - MemFree`, which counts the page cache as used and makes any working machine look 95% full |

A reading the daemon could not take never blocks anything. An unmeasurable host falls back to
the ceilings, which need no measurement — a careful mechanism that stops the fleet when its
probe breaks is worse than no mechanism.

## Priority is a share, not a rank

```toml
[[pool]]
name = "release"
priority = 10        # against another pool's 5, two thirds of the contested slots
```

A **weight**. A pool at 10 gets twice as many contested slots as one at 5 — not all of them
— and they are interleaved rather than handed out in blocks:

```
weights 10 and 5, six slots →  release  batch  release  release  batch  release
```

That interleaving is the point. Draining the heaviest pool first is what "priority" usually
means, and it makes the lighter pool wait until the heavier one is satisfied. On a fleet that
is always busy, "wait your turn" and "never" are then the same thing.

It only matters when the host cannot satisfy every pool at once; with capacity to spare it
changes nothing, so most pools leave it at the default of `1`. A pool that stops wanting
runners drops out and its share is redistributed, so this settles contention rather than
reserving a quota.

A pool too expensive for what is left does not block a cheaper one: if four CPUs will not fit
in the two remaining, that pool drops out for the tick and the others carry on.

**There is no queue to drain.** A pool refused this tick simply wants the same thing on the
next one, and the loop re-derives everything anyway. Being held back is not a lost launch,
and `ghspot pool status` and the daemon log say who was held back and by what:

```
[batch] held back by max_containers=8 (weight 1, 3 still wanted)
host cpu at 94% (high water 85%); deferring every launch until it recovers
```

Retiring and terminating are never held back. They *release* capacity, and refusing them is
what would turn a busy host into a stuck one.
