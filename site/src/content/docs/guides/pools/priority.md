---
title: "Priority"
description: "A pool's share of capacity when there is not enough to go round."
---

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
