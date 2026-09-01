---
title: "Labels and routing"
description: "How a job reaches one pool and not another."
---

A pool serves a job when the pool carries **every** label the job asks for. Extra labels on
the pool are fine; a missing one is disqualifying.

| | |
|---|---|
| Job asks | `runs-on: [self-hosted, ubuntu-24.04]` |
| Pool carries | `["self-hosted", "linux", "x64", "ubuntu-24.04"]` |
| Result | Served — `linux` and `x64` are spare, not obstacles |

Ask for the fewest labels that identify what you need. Each extra one is another thing a pool
must carry before it can serve, and a job asking for a label nobody built queues until GitHub
gives up on it 24 hours later.

`requires_labels` inverts it for a scarce pool: a job must ask for those **by name** before it
is served. Without it, a GPU pool carrying `gpu-a100` also serves every job that never
mentioned a GPU.

```toml
requires_labels = ["gpu-a100"]
```

## Name the OS in the labels

A bare `linux` tells a job nothing. Prefer the specific form, the way GitHub's own hosted
runners are labelled:

```toml
labels = ["self-hosted", "linux", "x64", "ubuntu-24.04"]
```

`linux` and `x64` are worth carrying — plenty of workflows ask for them — but a job that needs
`dnf`, or a particular glibc, can now say so and be sure of what it gets. Carrying a label
costs nothing; *asking* for one is what narrows where a job can land.

Remember that a pool serves a job only when it carries **every** label the job asks for.
Adding `ubuntu-24.04` costs nothing; removing `linux` will strand any workflow still asking
for it.

## Serving several operating systems

One pool per image. A job asking for `rhel-9` will only ever land on a runner carrying it:

```toml
[[pool]]
name = "ubuntu"
repository = "you/your-project"
labels = ["self-hosted", "linux", "x64", "ubuntu-24.04"]
max_runners = 3
[pool.container]
image = "ghspot/runner:ubuntu-24.04"
docker_socket = true

[[pool]]
name = "rhel"
repository = "you/your-project"
labels = ["self-hosted", "linux", "x64", "rhel-9"]
max_runners = 1
[pool.container]
image = "ghspot/runner:rhel-9"
docker_socket = true
```

Both pools watch the same repository; the daemon polls it once per tick regardless of how
many pools point at it, and each pool only counts the jobs it can actually serve.
