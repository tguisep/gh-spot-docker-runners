---
title: "How runners are kept: pm"
description: "static, dynamic and ondemand, and when each is right."
---

Three intentions, named rather than assembled by hand out of `min_idle` and `idle_timeout` —
where the same intent can be written three ways and two of them are subtly wrong. The names
and the semantics are php-fpm's.

```toml
[[pool]]
pm = "dynamic"       # the default
min_idle = 1
max_idle = 4
```

| `pm` | What it does | Applies |
|---|---|---|
| `dynamic` | Keeps between `min_idle` and `max_idle` warm, growing to cover the queue and shrinking when it empties. What the daemon has always done | `min_idle`, `max_idle`, `idle_timeout` |
| `static` | Exactly `max_runners`, always up, **never reaped**. The fastest possible first job, paid for continuously | `max_runners` only |
| `ondemand` | Nothing warm. A runner starts when a job is queued and goes away after `idle_timeout`. Cheapest, and every job pays container boot | `idle_timeout` |

**A key that does not apply to the mode is refused at load**, the way php-fpm refuses
`pm.min_spare_servers` under `pm = static`:

```
error [[pool]] (gpu): 'min_idle' does nothing under pm = "ondemand".
      ondemand keeps nothing warm; idle_timeout still decides how long a spent runner lingers.
```

A setting quietly doing nothing is worse than one that will not load: the pool behaves unlike
its configuration and nothing says so.

## `max_idle` is the knob that was missing

Before it, only `idle_timeout` bounded how many warm runners a pool accumulated. After a
burst of twelve jobs, twelve runners stay warm for the *full* timeout on a host that has gone
back to needing one. `max_idle` reaps the surplus straight away, longest-idle first:

```
[default] 3 runner(s) above max_idle=2
```

Nothing is reaped while work is queued, `max_idle` included — reaping capacity in the same
tick a pool is short of it would oscillate.

## Picking one

| | |
|---|---|
| A repository whose CI you wait on all day | `static`, sized to what you will actually pay for |
| Most things | `dynamic` with `min_idle = 1` — one warm runner takes container boot off the first job |
| A GPU pool, or anything scarce and expensive | `ondemand`, so the hardware is free between jobs |
