---
title: "Host capacity"
description: "Ceilings across every pool at once, and what happens when they bite."
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
disk_high_water = 85        # percent full of Docker's data directory
io_high_water = 90          # percent of the time that disk is busy
```

Two mechanisms, working on different things:

| | Counts | Applies to |
|---|---|---|
| **Ceilings** | Runners that exist — arithmetic, no measurement | `max_containers`, `max_cpus`, `max_memory` |
| **Backpressure** | What the host is actually doing — measured each tick | `cpu_high_water`, `memory_high_water`, `disk_high_water`, `io_high_water` |

## Ceilings

- `max_containers` **defaults to this machine's core count.** An unbounded host starts a
  container per queued job until it stops responding, and the first thing you learn is that the
  machine is gone. Cores is not a measurement — it is a defensible number the box names for
  itself.
- `max_containers = "unlimited"` lifts it on purpose, the way housekeeping spells `"never"`.
- `max_cpus` and `max_memory` only count pools that set `cpus` and `memory`. A fleet that sets
  neither is bounded by the count alone.
- Reservations, not usage: a pool with `cpus = 2.0` counts two whether the job uses them or not.

## Backpressure

At or above a high-water mark, **nothing new starts even where a pool has a free slot**. This
is the case arithmetic cannot see: everything else on the box, a job using far more than its
pool reserved, or a machine already struggling before the daemon woke up.

| Reading | Source | Note |
|---|---|---|
| CPU | One-minute load average as a percentage of cores | `100` = as much work queued as cores. Counts uninterruptible sleep, so heavy disk IO shows here — for deciding whether to pile more on, that is a feature |
| Memory | `MemAvailable` | The kernel's own estimate of what a new process could get. Not `MemTotal - MemFree`, which counts page cache as used and makes any working machine look 95% full |
| Disk (full) | `statvfs` on Docker's `DockerRootDir` | Not `/`. A host that gave Docker its own volume has two filesystems and only one of them fills. Counts against what is *usable*, since the blocks reserved for root are not space a container can have |
| Disk (busy) | `/proc/diskstats` for the device under that directory | The share of wall time the device spent with a request in flight — what `iostat -x` prints as `%util`, measured over the gap between two ticks |

### Why the disk needs its own mark

The other two watch what jobs *use*. The disk is where what they **leave behind** accumulates:
build caches, pulled images, anonymous volumes — all of it outliving the job that made it.

[Housekeeping](../housekeeping/) reclaims that on a schedule. A schedule does not help a host
that fills between sweeps, and a full disk stops the Engine dead: every launch fails with an
error naming neither the disk nor the cause. This is the mark that defers launches instead.

`ghspot doctor` reports it too, and says so above 90% even with no mark configured — because
at that point nothing else will.

### Full is not the same as busy

`disk_high_water` and `io_high_water` are two different failures of the same disk, and a host
can be comfortable on one while the other is the reason nothing finishes.

A device can be a tenth full and completely saturated. Nothing else here would notice: the
load average does pick up IO wait, but only once enough processes are stuck waiting on it,
which is well past the point where adding another runner makes every build on the box slower.

| | Says | Clears by |
|---|---|---|
| `disk_high_water` | The filesystem is filling up | Reclaiming space — housekeeping, or `docker system prune` |
| `io_high_water` | The device is not keeping up | Itself, once the work in flight drains |

**Leave `io_high_water` high.** Sustained 100% on a single spinning disk means work is
queueing. On an NVMe with a deep queue it can simply mean the device is being used properly,
because `%util` counts *any* request in flight and says nothing about how many more it could
have taken. 90 is a reasonable starting point; below about 70 you will defer launches on a
machine that was coping.

The reading is a **rate**, so unlike every other mark it needs two probes to exist at all: a
daemon's first tick always reports it as unknown. It is also unknown when the gap between two
probes was under a second or over five minutes — an hour's average utilisation is not an
answer to "is the disk busy now".

Once the mark is set, `ghspot doctor` names the device it reads:

```
✓ disk io probe        reading dm-1 (high water 90%)
```

That check exists for the one way this fails quietly: a mark set on a host whose Docker device
has no `/proc/diskstats` row — inside a container without the host's `/proc`, say — where the
gate never fires and nothing anywhere says so.

A reading the daemon could not take never blocks anything: an unmeasurable host falls back to
the ceilings. A mechanism that stops the fleet when its own probe breaks is worse than none.
