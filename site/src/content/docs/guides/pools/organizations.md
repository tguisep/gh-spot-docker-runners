---
title: "Organization-level pools"
description: "Registering a pool's runners at the organization instead of one repository."
---

A pool normally registers at one repository. `organization` registers it at the organization
instead, so its runners are shared across every repository under it rather than pinned to one:

```toml
[[pool]]
name = "org-shared"
organization = "your-org"
repositories = ["your-org/api", "your-org/web"]
labels = ["self-hosted", "linux", "x64", "ubuntu-24.04"]
max_runners = 2
[pool.container]
image = "ghspot/runner:ubuntu-24.04"
docker_socket = true
```

`repository` and `organization` are mutually exclusive — a pool is one or the other, never
both.

## Why `repositories` exists at all

Registering runners at the organization is the easy half. GitHub has no organization-scoped
equivalent of "queued jobs" — `list_queued_jobs` only exists per repository — so something
still has to say which repositories' queues the daemon watches to decide when to scale.

Two ways to say it, and an organization pool needs exactly one:

| | Cost | Upkeep |
|---|---|---|
| `repositories = [...]` | Bounded — scales with the list | You add a repository when you want it watched |
| `discover_repositories = true` | Scales with the organization | None — polls `GET /orgs/{org}/repos` every tick |

```toml
# Either this...
repositories = ["your-org/api", "your-org/web"]

# ...or this, not both.
discover_repositories = true
```

Start with an explicit list on anything but a small organization. `discover_repositories`
polls every repository the credentials can see, every tick — fine for a handful of
repositories, and a real cost on a large one.

## Runner groups

Organization-scoped registration takes a runner group; a repository-scoped one does not need
one, because only an organization plan has more than the implicit default. Unset, a pool's
runners join the organization's **Default** group:

```toml
runner_group = "Default"    # or a numeric id
```

## What still matches by repository

A queued job is always a concrete repository's job — GitHub does not have an org-scoped one —
so [labels and routing](../labels/) work exactly as before: an organization pool serves a job
when the job's repository is under the organization, the pool carries every label the job
asks for, and (if `requires_labels` is set) the job asked for them by name.

The one thing an organization pool cannot do that a repository pool can is show which job a
runner ran, after the fact — `ghspot runner logs --job` and the dashboard's GitHub-log pane
stay empty for these runners, because that search is also per-repository and there is no
single repository to search. The container's own live log is unaffected.

## Authenticating

A GitHub App used for an organization pool needs the organization permission
**"Self-hosted runners: read & write"**, in addition to whatever repository permissions your
other pools need — see [Authentication](../../../start/authentication/).

If this organization's App or token is entirely separate from what your other pools use — a
different installation, a different account — give it its own named credential instead of
widening the default one to cover both: see
[serving multiple credentials](../../../start/authentication/#serving-multiple-repositories-or-organizations-with-different-credentials).
