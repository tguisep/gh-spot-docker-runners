---
title: Tuning
description: The handful of settings worth changing, and when.
slug: 0.6/guides/host/tuning
---

| Setting | Raise it when | Lower it when |
|---|---|---|
| `poll_interval` | You are near the rate limit | Jobs wait too long to start |
| `min_idle` | Jobs wait on container boot | Idle containers waste memory |
| `max_idle` | A burst is reaped too eagerly | Warm runners pile up after a burst |
| `max_runners` | The host has capacity to spare | Jobs are starving each other |
| `max_launch_per_tick` | Large matrices start too slowly | A burst overwhelms the host |
| `idle_timeout` | Runners churn between jobs | Idle runners linger too long |
| `capacity.max_containers` | The host has capacity to spare | The box is thrashing |
| `capacity.cpu_high_water` | Launches are deferred while the host is fine | The host is overloaded before anything defers |

`min_idle = 1` is the setting most worth having: it removes container boot time from the
critical path of the first job.
