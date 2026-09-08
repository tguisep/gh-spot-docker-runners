---
title: "State schema"
description: "The four tables, what each column holds, and why losing the file costs history rather than correctness."
---

One SQLite file, at `[daemon].state_db`. It is a **projection**, not the truth: the truth is
the containers running on this host and the runners GitHub lists. Delete the file and the next
tick adopts the running containers back from their own labels.

```bash
sqlite3 /var/lib/ghspot/state.db .schema
```

| | |
|---|---|
| Journal mode | WAL — the CLI reads while the daemon writes |
| Foreign keys | `ON`, which is what makes the cascade below a retention policy |
| `PRAGMA user_version` | `2` |

## `runners`

One row per runner the daemon has minted, terminal ones included until they are pruned.

| Column | Type | |
|---|---|---|
| `id` | TEXT PK | The daemon's own id. Random, not sequential — it becomes part of the runner's name on GitHub, and a name colliding with one still being torn down is rejected at registration |
| `name` | TEXT | `ghspot-{pool}-{id[:12]}`, as registered on GitHub |
| `pool` | TEXT | Indexed |
| `repository` | TEXT | `owner/name` |
| `labels` | TEXT | JSON array |
| `state` | TEXT | Indexed. See below |
| `created_at` | TEXT | ISO-8601 |
| `state_changed_at` | TEXT | ISO-8601. What `idle_timeout` and `max_job_duration` measure from |
| `github_runner_id` | INTEGER | `NULL` until registration succeeds |
| `container_id` | TEXT | `NULL` until the container exists |
| `current_job_id` | INTEGER | Filled in on demand when somebody asks for a job log, not during a tick |
| `failure_reason` | TEXT | Set with `state = 'failed'` |

States, in the order a runner moves through them:

| State | |
|---|---|
| `pending` | Decided on, nothing created yet |
| `registered` | A just-in-time config exists on GitHub, no container — the crash-critical window |
| `starting` | Container created, runner not yet connected |
| `idle` | Connected, waiting for work |
| `busy` | Running a job |
| `draining` | Asked to stop once the current job finishes |
| `retired` | Terminal. Container removed, registration deleted |
| `failed` | Terminal. `failure_reason` says why |

Legal moves are declared in `_TRANSITIONS`; the aggregate refuses anything else rather than
letting a bad move through and repairing it later.

## `runner_logs`

The tail of a retired runner's container output, taken between stopping it and removing it.

| Column | Type | |
|---|---|---|
| `runner_id` | TEXT PK | `REFERENCES runners(id) ON DELETE CASCADE` |
| `captured_at` | TEXT | ISO-8601 |
| `lines` | TEXT | Last 500 lines, capped at 256 KiB, keeping the end |

Its own table rather than a column on `runners`: every listing does `SELECT *` on that one, and
a log-sized `TEXT` beside twelve small columns would be read on every `ghspot runner list`.

The cascade **is** the retention policy. Pruning a runner takes its log with it, so nothing
else has to remember the table exists and a log cannot outlive what it describes.

## `events`

Append-only. What `ghspot stats` reads.

| Column | Type | |
|---|---|---|
| `id` | INTEGER PK | Autoincrement |
| `occurred_at` | TEXT | Indexed descending |
| `kind` | TEXT | The domain event class name, resolved back by `getattr` on load |
| `runner_id` | TEXT | Not a foreign key — history outlives the runner it describes |
| `payload` | TEXT | JSON, the event's own fields |

Kinds: `RunnerRegistered`, `RunnerStarted`, `RunnerCameOnline`, `RunnerTookJob`,
`RunnerRetired`, `RunnerFailed`.

## `queue_snapshot`

What the last tick saw waiting, and what it decided was in the way. **One row**, replaced
every tick.

| Column | Type | |
|---|---|---|
| `id` | INTEGER PK | `CHECK (id = 1)` — there is only ever one row |
| `taken_at` | TEXT | ISO-8601. The reader compares it against the poll interval to decide whether the reading is stale |
| `document` | TEXT | JSON: the queued jobs with their pool, priority, position and wait reason; each pool's pressure; the host readings and the limits they were judged against |

A JSON document rather than tables because nothing ever queries inside it: it is written
whole by one writer and read whole by any number of readers.

It exists because the reader and the writer are different processes. Only the daemon holds a
GitHub token, and `ghspot queue`, the API and the dashboard deliberately do not — so the
daemon writes down the answer it already paid for. Before this table, the `queued` column
every one of them rendered had nothing behind it and read zero however much CI was waiting.

No history is kept. The question asked of a queue is what is waiting *now*, and anything
worth keeping longer is already in `events`.

## What is lost if the file goes

| Lost | Kept |
|---|---|
| `ghspot stats` history | Every running runner, re-adopted from its container labels |
| Archived logs of retired runners | The pools, which come from the configuration |
| One tick's worth of queue visibility | The queue itself, which lives at GitHub — the next tick reads it again |

Correctness is never at stake, and a test asserts exactly that. Back the file up if you want
the usage history; nothing else depends on it surviving.
