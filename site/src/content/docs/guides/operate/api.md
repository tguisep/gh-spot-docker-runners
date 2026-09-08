---
title: "The REST API"
description: "The same projection, over HTTP."
---

## The REST API

Set `api_bind` under `[daemon]` and the API is served in-process with the loop:

```bash
curl -s localhost:8770/health | jq
curl -s localhost:8770/pools | jq
curl -s localhost:8770/queue | jq                 # what is waiting, and why
curl -s -X POST localhost:8770/reconcile | jq   # tick now, don't wait
curl -s 'localhost:8770/runners?usage=true' | jq   # with CPU and memory
```

Interactive docs at `/docs`. **There is no authentication** — bind to localhost, or put a
reverse proxy with auth in front of it.

### `GET /queue`

What the last tick saw waiting, and the one thing standing in front of each job. Served from
the projection, so polling it costs nothing against the GitHub rate limit however many
dashboards are open.

```json
{
  "taken_at": "2026-09-08T09:14:03Z",
  "age_seconds": 4.1,
  "stale": false,
  "total": 5,
  "delayed": 3,
  "entries": [
    {
      "title": "ci / test (3.13)",
      "url": "https://github.com/owner/repo/actions/runs/1001/job/9912",
      "work_class": "default-branch",
      "urgency": 10,
      "pool": "default",
      "priority": 5,
      "position": 3,
      "reason": "pool-at-capacity",
      "detail": "pool is at max_runners=3 with 3 up",
      "waiting_seconds": 122.0,
      "delayed": true
    }
  ]
}
```

`url` is the forge's own link, so an Enterprise install points at its own host rather than at
github.com. `work_class` and `urgency` are the job's rank, derived from the run; `priority` is
the serving pool's weight, which is a different number about a different thing. Entries come
back most urgent first, oldest first within a class.

`taken_at` and `stale` are part of the answer, not metadata about it. The daemon writes a
reading every poll interval; with the daemon stopped, a client that renders only `entries`
shows an empty queue and a healthy fleet. `stale` is set once the reading is older than two
poll intervals, and `taken_at` is `null` when no tick has written one at all.
