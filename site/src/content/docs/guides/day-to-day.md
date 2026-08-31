---
title: "Day to day"
description: "Watching the fleet, reading usage, the dashboard and the API."
---

```bash
ghspot pool list                  # every pool and what it holds
ghspot pool status default        # one pool, with its runners
ghspot runner list                # live runners
ghspot runner list --all          # including retired and failed
ghspot runner logs <ref>          # container output
ghspot runner stop <ref>          # retire it on both sides
ghspot runner stop <ref> --force  # even if it is mid-job
```

`<ref>` accepts a runner id, a runner name, or a container id, whole or abbreviated —
whichever column you have in front of you.

`ghspot runner list` reads only the local database. It works when the token has expired or
Docker is down, which is when you most want to look.

## What the fleet has been doing

```bash
ghspot stats                  # everything the log holds
ghspot stats --since 7d       # a window: 24h, 7d, 30m
```

```
usage on builders-01 — since 2026-08-21 09:00 UTC, 812 event(s) read
 repository                       runners  jobs  fail  fail%     busy  avg job  avg wait  used  live
 tguisep/gh-spot-docker-runners        58    54     1     2%    6h12m    6m53s       22s   91%     2
 tguisep/other-project                 11     9     0     0%      47m    5m13s     1m40s   78%     -
 all                                   69    63     1     1%    6h59m    6m39s       33s   89%     2
```

| Column | What it is |
|---|---|
| `runners` | Registered with GitHub. Every runner starts here, so it is the denominator |
| `jobs` | Runners that were handed a job. A just-in-time runner takes at most one, so this is also jobs served |
| `fail`, `fail%` | Runners that never made it, and the share of the total |
| `busy` | Time between taking a job and going away: machine time actually spent on CI |
| `avg job` | `busy` divided by `jobs` |
| `avg wait` | Registration to being handed a job — what `min_idle` buys down |
| `used` | `busy` as a share of total time alive. Low means runners sat idle |
| `live` | In flight right now, from the projection rather than the log |

Two of these are worth acting on. A high **`avg wait`** means jobs are waiting on container
boot, which is what `min_idle = 1` removes. A low **`used`** means the opposite — runners are
being kept warm and not used, so `min_idle` is too high or `idle_timeout` too long.

The numbers come from the event log, not the runners table, so they cover runners that are
long gone. Two consequences worth knowing:

- A runner still working has no end event, so it contributes to `live` and to nothing else.
  Its time appears in the next report, not this one.
- A window narrow enough to exclude a runner's registration still sees its later events.
  Those group under `(unknown)` rather than being dropped, so the rows always add up to the
  total.

The same report is on the API when `api_bind` is set:

```bash
curl -s localhost:8770/stats | jq '.total'
curl -s 'localhost:8770/stats?since_seconds=604800' | jq '.by_repository'
```

Nothing prunes the event log, so a busy fleet grows it slowly and the report stays honest
about the whole period.

### Which host these numbers are about

Several hosts can serve one repository. Each daemon has its own state database and only ever
sees the runners it started itself, so **every figure here is about one machine** — the report
is not a fleet total, and adding two of them together is the only way to get one.

The report says which machine, and so do `/health`, `/stats`, `ghspot doctor` and the
dashboard header. It defaults to the system hostname; name it when that is a cloud instance id
or a container's:

```toml
[daemon]
host = "builders-01"
```

With Ansible, `ghspot_host` — left empty the daemon falls back to the hostname rather than to
an empty label.

## Watching instead of re-running

Every listing takes `--watch`, which repaints in place until interrupted:

```bash
ghspot pool status --watch 2
ghspot runner list --watch 2 --usage
```

This is what `watch ghspot pool status` is reaching for, without its two costs: `watch`
re-runs the whole command, so every refresh re-reads the configuration and reopens the
database, and it drops the colours unless told otherwise. Here the process stays up and only
the frame changes. Ctrl-C ends it.

## CPU and memory

```bash
ghspot runner list --usage
```

```
runner                pool     state   age     in state   cpu   memory          container
ghspot-default-9f2a   default  busy    4m12s   3m48s      182%  1.4GiB (35%)    3f9a1c2b4d5e
ghspot-default-7b81   default  idle    9m03s   6m11s        0%  184.2MiB (4%)   9c2e7a10bb33
```

Sampled from the Engine, one call per running container, so it is **off by default**: every
other listing here reads only the state database and works with Docker down. A runner with
no sample shows `-` rather than `0%` — a container that has gone is not using nothing, it is
not there.

The memory figure excludes the page cache, so a job that read a large file does not look
like a job that leaked. The CPU figure is per core the way `docker stats` reports it: 200%
is two cores saturated.

## The dashboard

Set `api_bind` and open `/ui`:

```
http://localhost:8770/ui
```

It covers the same ground as the CLI — pools and their capacity, runners with their state
and resource use, a live log tail, and the usage report — plus the two interventions:
stopping a runner, and forcing a tick.

| Page | What it is for |
|---|---|
| overview | Is the daemon healthy, are pools full, is work queueing |
| runners | What is running, with an optional CPU and memory column; **stop** (refused mid-job) or **kill** (SIGKILL, fails the build) |
| logs | Both logs for one runner, side by side — see below |
| stats | The usage report, over a window |

It polls; nothing is pushed. The log view re-reads the tail every two seconds, which reads
as live at a runner's log volume and costs nothing to hold open. Polling pauses while the
browser tab is hidden, so a dashboard left open overnight is not a steady stream of requests
against a home server.

### Two logs, and why they are not the same log

The logs page shows two panes because a runner has two logs on two different schedules:

| Pane | What it is | When |
|---|---|---|
| container | The job as it happens. The runner prints its work to stdout, so `docker logs` *is* the live job output | Now, and gone with the container seconds after the job ends |
| github | GitHub's own log, with timestamps and step structure | Written when the job **finishes**. Nothing exists before then |

GitHub has no endpoint that streams a running job's log — asking for one answers `404
BlobNotFound` until the job completes. So the left pane is the live view, and the right pane
says what it is waiting for and fills itself the moment the job ends.

The right pane is the one that matters afterwards: a just-in-time runner is removed as soon
as its job finishes, taking its container log with it. GitHub's copy is what remains.

Same thing from the CLI:

```bash
ghspot runner logs <ref>           # the container: the job as it happens
ghspot runner logs <ref> --job     # GitHub's, once the job has finished
```

```bash
curl -s localhost:8770/runners/<ref>/job-logs | jq
```

It costs one `Actions: read` call, a permission the daemon already has.

**The dashboard carries no authentication of its own**, because the API it talks to has none. The same
rule applies: bind to localhost, or put a proxy with auth in front.

The `.deb` installs it to `/usr/share/ghspot/web`, and the daemon serves whatever it finds
there. From a checkout, build it once:

```bash
cd web && npm ci && npm run build     # then it is served from web/dist
npm run dev                           # or a dev server on :5173, proxying to the daemon
```

`GHSPOT_WEB_ROOT` overrides the location. A package built on a machine without `npm` simply
has no dashboard; the daemon and the API are unaffected.

## The REST API

Set `api_bind` under `[daemon]` and the API is served in-process with the loop:

```bash
curl -s localhost:8770/health | jq
curl -s localhost:8770/pools | jq
curl -s -X POST localhost:8770/reconcile | jq   # tick now, don't wait
curl -s 'localhost:8770/runners?usage=true' | jq   # with CPU and memory
```

Interactive docs at `/docs`. **There is no authentication** — bind to localhost, or put a
reverse proxy with auth in front of it.
