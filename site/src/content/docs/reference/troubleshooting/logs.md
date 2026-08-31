---
title: "Logs"
description: "Empty panes, missing job logs, and runners that vanished."
---

## A retired runner's logs are empty

Retiring removes the container and Docker drops its output with it. The daemon copies the last
500 lines into its state database between stopping and removing, so the CLI and dashboard still
answer — both say when you are reading that kept copy.

| Limit | |
|---|---|
| Retired before this existed | Nothing kept; there is no copy to go back for |
| Retention | Pruned with the runner record — the last 500 terminal runners |
| Ran a job? | GitHub's log outlives both: `ghspot runner logs <id> --job` |

## The GitHub pane says no job was found

Nothing records which job a runner takes while it works — GitHub's runner list reports *that* a
runner is busy, not which job. So it is searched for on demand, across the last 30 workflow
runs, matching `runner_name`. The answer is written back, so the search happens once per runner.

- A run older than those 30 is not found. Nothing is broken; the window does not reach it.
- A runner that never registered is never searched for — it cannot have taken a job.

## A job sits with no logs, and its runner has vanished

The runner was removed mid-job, so nothing reported back.

```bash
journalctl -u ghspot --since "1 hour ago" | grep -E "blind|unreachable|retired"
```

A tick that cannot reach Docker now does nothing rather than concluding no containers exist —
that conclusion used to tear down the fleet mid-job. A Docker restart during jobs is the usual
trigger. Seeing it on an older version means upgrade.
