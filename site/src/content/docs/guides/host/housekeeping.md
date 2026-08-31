---
title: "What a job leaves behind"
description: "Reclaiming the images, volumes and caches jobs leave on the host."
---

Runner containers are removed when their job ends, taking the working directory with them.
But with `docker_socket = true` a job talks to **your** Docker daemon, so what it does there
is yours afterwards:

| The job did this | After the runner is removed |
|---|---|
| Wrote files under `_work` | Gone |
| Pulled or built an image | **Still on the host** |
| Created a volume | **Still on the host** |
| Left a container running | **Still running** |
| `docker build` layers | **Still in the build cache** |

That is inherent to sharing the daemon, and it is the trade recorded in
[ADR 5](../../reference/adr/0005-docker-socket-over-dind.md).

## Housekeeping bounds it

The daemon reclaims unused Docker objects on a schedule:

```toml
[housekeeping]
every = "1h"
containers_older_than = "1h"     # stopped containers
images_older_than = "24h"        # unused images
volumes = true                   # anonymous volumes
build_cache_older_than = "24h"
keep_build_cache = "10g"
```

Three rules keep this from eating something that matters:

- Every age is a **floor**, so a running job cannot have what it is using removed underneath it.
- Runner images carry `io.ghspot.image=runner` and are **never** reclaimed — otherwise the
  daemon would eventually delete the images it starts runners from.
- Named volumes are left alone; a named volume is something somebody chose to create.

Set any age to `"never"` to disable that sweep, or `enabled = false` for all of it.

## What housekeeping does not guarantee

Two things it deliberately will not do:

- **A container the job left running is never touched.** Nothing distinguishes it from
  something you started on purpose, and guessing wrong means deleting somebody's database.
- **Nothing is removed immediately.** Residue is bounded by the interval and the age floors,
  not eliminated.

A real guarantee needs each runner to have its own Docker daemon — Docker-in-Docker — so there
is no shared state to leave anything in.

- Costs the shared layer cache: every job re-pulls what it needs.
- `RunnerBackend.create()` takes a `ContainerSpec`, so it is a new spec rather than a change to
  calling code — but it is an architectural change, and not currently implemented.

The narrow case is easy, though: a pool with `docker_socket = false` leaves nothing at all,
because the job never reaches the host daemon in the first place.
