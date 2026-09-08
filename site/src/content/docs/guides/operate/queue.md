---
title: "The queue"
description: "What is waiting for a runner, and what each job is waiting on."
---

## The queue

A build sitting at *Waiting for a runner* has one of about eight causes, and they need
completely different things done about them. `ghspot queue` names which one it is.

```bash
ghspot queue
ghspot queue --pool gpu
ghspot queue --watch 2      # repaint in place while a burst drains
```

```
queue — 5 job(s), 3 waiting on capacity, longest 3m20s  ·  read 4s ago
┏━━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━┳━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ waiting ┃ job           ┃ pool    ┃ prio ┃ # ┃ status           ┃ why                               ┃
┡━━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━╇━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│     40s │ ci / test (1) │ default │    5 │ 1 │ assignable       │                                   │
│   1m20s │ ci / test (2) │ default │    5 │ 2 │ starting         │ a runner is starting for it       │
│   2m00s │ ci / test (3) │ default │    5 │ 3 │ pool-at-capacity │ pool is at max_runners=3 with 3 up │
│   2m40s │ ci / test (4) │ gpu     │   10 │ 1 │ host-at-capacity │ the host refused: max_cpus=8       │
│   3m20s │ ci / test (5) │ —       │    — │ — │ no-pool          │ no pool serves […, windows]       │
└─────────┴───────────────┴─────────┴──────┴───┴──────────────────┴───────────────────────────────────┘
```

Below the jobs comes a row per pool — what it holds, what the scaling policy asked for, what
the host granted, and what held the rest back — then the host's own readings beside the
limits they were judged against, and the tick's notes verbatim.

The same thing lives on the dashboard's **queue** page and at `GET /queue`.

## What each status means

| Status | What it is | What to do |
|---|---|---|
| `assignable` | A runner is up and free for it | Nothing. The wait is GitHub handing the job over, not the fleet |
| `starting` | A runner is being launched for it right now | Nothing. It is paying for a container boot |
| `pool-at-capacity` | The pool is at `max_runners` | Raise `max_runners`, if the host has room |
| `tick-limit` | `max_launch_per_tick` is spreading a burst over several ticks | Nothing; it clears in seconds. Raise it if bursts are routine |
| `host-at-capacity` | A committed ceiling — `max_containers`, `max_cpus`, `max_memory` — refused the launch even though the pool had room | Raise the ceiling, or lower what a pool reserves |
| `host-busy` | Backpressure: the machine is at a high-water mark and **nothing** starts until it recovers | Find what is loading the box. This is the one that is not about the pool |
| `contended` | Capacity existed and went to another pool this tick | Nothing, or raise this pool's `priority`. See [priority](../../pools/priority/) |
| `no-pool` | No configured pool serves those labels in that repository | The only one that never clears on its own — it is a configuration answer |

`no-pool` is worth watching for. Nothing is wrong with the fleet and nothing will ever
happen: a workflow asks for `windows` or `gpu`, no pool carries it, and the job waits until
somebody cancels it. Before this view it was invisible — the job appeared in no count at all.

## How fresh the reading is

The heading always says when the reading was taken, and the dashboard says so too. This is
not decoration.

The daemon is the only process holding a GitHub token. `ghspot queue`, the API and the
dashboard read the projection instead, so an expired token or a stopped Docker never takes
away your ability to see what is going on — and a dashboard open all day costs the rate limit
nothing. The price is that everything here is one poll interval behind at most.

With the daemon **stopped**, a view that hid its age would show an empty queue and a fleet
keeping up perfectly. So a reading older than two poll intervals is called out in red, and
before the first tick has run the view says the daemon has not looked yet rather than showing
you nothing and letting you draw the wrong conclusion.

```
queue — 5 job(s), 3 waiting on capacity  ·  read 4m12s ago — the daemon may not be running
```

A repository whose queue could not be read that tick — a revoked token, a deleted repo — is
named above the table for the same reason: an unread queue and an empty one look identical.

## Where the answer comes from

Nothing here is measured separately. Every tick already reads the queue, works out which pool
serves each job, asks [the scaling policy](../../../reference/architecture/scaling/) how many
runners that needs and the admission policy how many the host will take. That reasoning used
to be thrown away; now the tick writes it into the projection on its way out, so the reading
costs no extra request to GitHub and no extra call to Docker.

Two consequences worth knowing:

- **The position is a model, not a promise.** The daemon never assigns a job to a runner — it
  starts runners, and GitHub decides who gets what. Position 1 means "first in line for the
  next free runner in this pool", in the oldest-first order GitHub roughly uses.
- **A job two pools could serve is counted once**, against the heavier-weighted one. Counting
  it twice would double the number you would size a machine from.
