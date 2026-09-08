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
queue — 5 job(s), 3 waiting on capacity, longest 15m00s  ·  read 4s ago
┏━━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━┳━━━━━━┳━━━━━━━━━┳━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━┓
┃ waiting ┃ job           ┃ kind           ┃ prio ┃ pool    ┃ # ┃ status           ┃ why                  ┃
┡━━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━╇━━━━━━╇━━━━━━━━━╇━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━┩
│     45s │ ci / test (1) │ default-branch │   10 │ default │ 1 │ assignable       │                      │
│   3m20s │ ci / deploy   │ manual         │    8 │ default │ 2 │ starting         │ a runner is starting │
│   4m20s │ ci / test (3) │ pull-request   │    6 │ default │ 3 │ pool-at-capacity │ at max_runners=3     │
│   5m00s │ ci / gpu      │ pull-request   │    6 │ gpu     │ 1 │ host-at-capacity │ the host refused:    │
│  15m00s │ ci / test (5) │ draft          │    4 │ default │ 4 │ pool-at-capacity │ at max_runners=3     │
└─────────┴───────────────┴────────────────┴──────┴─────────┴───┴──────────────────┴──────────────────────┘
```

Job names are hyperlinks. In a terminal that supports them, clicking one opens the job on
GitHub; in one that does not, it is a plain name and nothing is lost. The dashboard renders
the same link as an ordinary link, opening in a new tab so the page you are watching stays
where it is.

Below the jobs comes a row per pool — what it holds, what the scaling policy asked for, what
the host granted, and what held the rest back — then the host's own readings beside the
limits they were judged against, and the tick's notes verbatim.

```
host cpu 71% / 85%  memory 88% / 90%  disk 54% / 85%  disk io 96% / 90%  containers 4/6
```

`disk` is how full the filesystem is; `disk io` is how busy the device under it is. They are
[different failures](../../host/capacity/#full-is-not-the-same-as-busy) with different
remedies, and a host can be comfortable on one while the other is why nothing finishes. A
reading with no number was not measured — `disk io` is a rate, so a daemon's first tick never
has one.

The same thing lives on the dashboard's **queue** page and at `GET /queue`.

## What each status means

| Status | What it is | What to do |
|---|---|---|
| `assignable` | A runner is up and free for it | Nothing. The wait is GitHub handing the job over, not the fleet |
| `starting` | A runner is being launched for it right now | Nothing. It is paying for a container boot |
| `pool-at-capacity` | The pool is at `max_runners` | Raise `max_runners`, if the host has room |
| `tick-limit` | `max_launch_per_tick` is spreading a burst over several ticks | Nothing; it clears in seconds. Raise it if bursts are routine |
| `host-at-capacity` | A committed ceiling — `max_containers`, `max_cpus`, `max_memory` — refused the launch even though the pool had room | Raise the ceiling, or lower what a pool reserves |
| `host-busy` | Backpressure: the machine is at a high-water mark and **nothing** starts until it recovers | Read the host line below the table — it names which of cpu, memory, disk or disk io is over, beside the mark it is judged against |
| `contended` | Capacity existed and went to another pool this tick | Nothing, or raise this pool's `priority`. See [priority](../../pools/priority/) |
| `no-pool` | No configured pool serves those labels in that repository | The only one that never clears on its own — it is a configuration answer |

`no-pool` is worth watching for. Nothing is wrong with the fleet and nothing will ever
happen: a workflow asks for `windows` or `gpu`, no pool carries it, and the job waits until
somebody cancels it. Before this view it was invisible — the job appeared in no count at all.

## Kind and priority

Two jobs asking for the same labels are interchangeable to the fleet, and are not
interchangeable to the people waiting. So every queued job is classified from its run, and the
queue is read most urgent first, oldest first within a class.

| Kind | Prio | When |
|---|---|---|
| `default-branch` | 10 | A push or merge to the default branch. The build nobody can route around |
| `manual` | 8 | `workflow_dispatch` — somebody pressed a button and is watching the page |
| `pull-request` | 6 | A pull request open for review |
| `branch` | 5 | A push to any other branch, or a tag |
| `draft` | 4 | A draft pull request. The author has said it is not finished |
| `scheduled` | 2 | `schedule` — a nightly ten minutes late is still a nightly |

Nothing is configurable per job, and that is the point: a workflow that could declare its own
importance would declare the top of the scale, every time, and the ranking would mean nothing
within a week. The classification comes from what the run already says — its event, its
branch, and whether the pull request behind it is a draft.

`prio` here is the **job's** rank. The `prio` in the pools table below it is the pool's
`priority` weight, which is a different thing: one says how much this job matters, the other
how much its pool's launches matter when the host cannot satisfy every pool at once.

### What ranking does, and what it does not

It orders the queue **as read**, and nothing else.

The daemon does not hand jobs to runners. It starts runners, and GitHub decides which job each
one picks up, roughly in the order they were queued. So a merge cannot jump a draft inside
GitHub's own dispatch, and nothing here preempts a job already running.

What it does is answer the question you actually have when the queue is deep: *is this a
backlog somebody is waiting on, or is it forty draft-PR matrix legs?* Those want different
responses — raise `max_runners`, or leave it alone — and a queue sorted only by age cannot
tell them apart.

> **Draft detection needs `Pull requests: read`**, which the daemon does not otherwise
> require. Without it the listing is refused once, remembered, and never asked for again;
> drafts then read as ordinary pull requests. See
> [authentication](../../../start/authentication/).

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
