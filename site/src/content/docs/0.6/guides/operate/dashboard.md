---
title: The dashboard
description: The web view, and the two logs it shows side by side.
slug: 0.6/guides/operate/dashboard
---

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
