---
title: State schema
description: The three tables, what each column holds, and why losing the file
  costs history rather than correctness.
slug: 0.6/reference/architecture/schema
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
| `PRAGMA user_version` | `1` |

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

## What is lost if the file goes

| Lost | Kept |
|---|---|
| `ghspot stats` history | Every running runner, re-adopted from its container labels |
| Archived logs of retired runners | The pools, which come from the configuration |

Correctness is never at stake, and a test asserts exactly that. Back the file up if you want
the usage history; nothing else depends on it surviving.
