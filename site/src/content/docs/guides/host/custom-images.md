---
title: "Adding a runner image"
description: "Step by step: your own image for a pool, or a new variant in the project."
---

The four [shipped variants](../images/) cover an ordinary fleet. A job needing a toolchain
that is not in them — CUDA, a JDK, an internal build tool, a distribution nobody here builds
— needs an image of its own.

There are two ways to get one, and they are not the same job:

| | Use it for | Cost |
|---|---|---|
| [Your own image](#your-own-image) | Anything specific to you — your tools, your base, your registry | A Dockerfile and a pool. Nothing in this repository changes. |
| [A new variant](#a-new-variant-in-the-project) | A distribution or release everyone would want — the next Ubuntu LTS, another RHEL rebuild | Eight files, listed below, because the variant name appears in each of them |

Start with the first. The second is only worth it when the image belongs in the project
rather than on your host.

## The contract

Whichever route you take, the daemon expects the same five things of an image. Nothing else
about it is constrained — base, packages, size and registry are yours.

| The image must | Why |
|---|---|
| Read `RUNNER_JIT_CONFIG` from the environment and run the Actions runner with it | The daemon mints a single-use configuration per container. There is no registration step and no token to bake in. |
| Exit non-zero when that variable is absent | A runner that starts without a configuration is a container doing nothing, forever. `entrypoint.sh` exits `64`. |
| Run as a non-root user | A job has effective root on the host through the mounted socket already; it does not also need it inside the container. |
| Carry a `docker` group whose gid matches the host's | The mounted socket is otherwise unreadable, and the job fails with `permission denied` on `/var/run/docker.sock` — which looks nothing like an image problem. |
| Forward `SIGTERM` to the runner process | `docker stop` is how a draining runner is asked to finish its job rather than abandon it half-done. |

The shipped [`entrypoint.sh`](https://github.com/tguisep/gh-spot-docker-runners/blob/main/images/runner/entrypoint.sh)
is thirty lines and does all of it. Inheriting it is the whole reason the first route below
starts `FROM` a shipped image.

## Your own image

### 1. Start from a shipped variant

```dockerfile
# Dockerfile
FROM ghspot/runner:ubuntu-24.04

# Back to root to install, then back to the runner user. Jobs do not run as root, and an
# image that ends as root is one `USER runner` away from a job that does.
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
        openjdk-21-jdk \
    && rm -rf /var/lib/apt/lists/*
USER runner
```

The entrypoint, the runner payload, the `docker` group and the toolset all come with the
base, so the contract is honoured before you write a line. Build the base first, on this
host, so its `docker` gid is this host's:

```bash
ghspot image build ubuntu-24.04
```

### 2. Build it

```bash
docker build -t mycorp/runner:java21 .
```

Tag it under your own name, not `ghspot/runner:`. `ghspot image build` writes that namespace
and would overwrite yours on the next rebuild.

### 3. Check it against the contract

`verify.sh` takes a variant name and prefixes it with `$REGISTRY`, so it checks any image,
not only the shipped ones:

```bash
REGISTRY=mycorp/runner images/runner/verify.sh java21
```

It asserts the five contract points, that the image carries no credential-shaped variable,
and that every tool of the standard toolset is present and usable *by the runner user* —
being on `PATH` and being writable are not the same thing, which is how the `pipx`
permissions bug was found.

### 4. Give it a pool, and a label

```toml
[[pool]]
name = "java"
repository = "you/your-project"
labels = ["self-hosted", "linux", "x64", "ubuntu-24.04", "java21"]

# Without this, a job asking only for [self-hosted, linux, x64] can land here. Label
# matching is a subset rule: extra labels never stop a job, they only attract more.
# See Labels and routing.
requires_labels = ["java21"]

max_runners = 2

[pool.container]
image = "mycorp/runner:java21"
docker_socket = true
```

A workflow then asks for it by name:

```yaml
runs-on: [self-hosted, java21]
```

Keeping the label and the tag recognisably the same string is a convention rather than a
rule here, but it is the convention the shipped variants follow, and it is what stops a pool
from advertising an image it is not running. See [Labels and routing](../../pools/labels/).

### 5. Validate, then restart

```bash
ghspot config validate     # the file parses, and the pool is coherent
ghspot doctor              # among other things: is that image actually on this host?
sudo systemctl restart ghspot
```

:::note[`ghspot doctor` will suggest a build command that does not exist]
Its remedy for a missing image is always `ghspot image build <tag>` — for
`mycorp/runner:java21` it says `ghspot image build java21`, and there is no such variant.
The finding is right, the remedy is not: rebuild your own image with `docker build` instead.
:::

### Building from a bare base instead

Worth it only when you cannot start from a shipped image — a different distribution, or a
base your organisation requires. You then owe the contract yourself: copy
[`entrypoint.sh`](https://github.com/tguisep/gh-spot-docker-runners/blob/main/images/runner/entrypoint.sh)
in, install the Actions runner payload at `/home/runner`, create the user and the `docker`
group with the host's gid, and end on `USER runner`. The
[`rhel.Dockerfile`](https://github.com/tguisep/gh-spot-docker-runners/blob/main/images/runner/rhel.Dockerfile)
is the shortest worked example of doing it on a non-Debian base.

Pass the gid in rather than hardcoding it, or the image stops working the day you move it to
another host:

```bash
docker build --build-arg DOCKER_GID="$(getent group docker | cut -d: -f3)" -t mycorp/runner:custom .
```

### Where the image has to live

The daemon does not build anything. At launch the tag has to resolve on the host — built
locally, or pullable from a registry that host can reach. If it resolves as neither, the
runner never starts and the daemon says so:

```
the runner image 'mycorp/runner:java21' is not present
```

A private registry needs `docker login` as the user the daemon runs as, not as you.

## A new variant in the project

For a distribution or release that belongs in the project itself. The variant name is the
image tag *and* the label a workflow targets, so it appears in every file below — and the
ones that are only documentation are exactly the ones nothing will fail over.

### 1. The Dockerfile

A new release of a family already built is a build argument, not a file:
`ubuntu.Dockerfile` takes `UBUNTU_VERSION`, `rhel.Dockerfile` takes `BASE_IMAGE`. Only a new
family needs a new Dockerfile, and it should be grouped the way the existing two are — the
upstream package groups (`vital`, `common`, `cmd`) kept apart, so a future diff against the
toolset stays readable.

### 2. Declare it in `build.sh`

One line in `VARIANTS`, as `name:dockerfile:base-image`:

```bash
ubuntu-26.04:ubuntu.Dockerfile:ubuntu:26.04
```

That list is the single declaration of what exists: `ghspot image list` shells out to
`build.sh --list` rather than parsing a table out of anything, so adding the line is what
makes the variant real everywhere.

If the base image needs a microarchitecture level the oldest plausible host does not have,
add it to `MICROARCH_REQUIRED` too. `rhel-10` is there because its glibc aborts on the first
`RUN` with `Fatal glibc error: CPU does not support x86-64-v3`, which reads as a broken
Dockerfile rather than as a machine that cannot run that distribution at all.

### 3. Build and verify it

```bash
images/runner/build.sh ubuntu-26.04
images/runner/verify.sh ubuntu-26.04
```

`verify.sh` fails on a missing required tool. If the distribution genuinely does not package
one, move it to `OPTIONAL` with the reason — the report then says "not packaged for this
release" instead of failing, and the gap stays visible rather than being silently deleted
from the list.

### 4. Add it to CI

The matrix in [`.github/workflows/runner-images.yml`](https://github.com/tguisep/gh-spot-docker-runners/blob/main/.github/workflows/runner-images.yml):

```yaml
variant: [ubuntu-24.04, ubuntu-22.04, rhel-9, rhel-10, ubuntu-26.04]
```

A variant that is never built is a variant nobody finds out is broken.

### 5. Pin its toolset, if it is a new family

`upstream.lock.yml` maps each variant to the `actions/runner-images` toolset it was
transcribed from, and `sync-toolset.sh` reports the drift. A new Ubuntu release gets its own
toolset entry. A new non-Debian family needs its package-name mapping added to
`RHEL_EQUIVALENT` in `sync-toolset.sh`, or its half of the fleet goes unwatched — which is
the thing that file exists to prevent.

### 6. Say so in the three places that list variants

None of these break anything when they fall behind, which is why they are the ones to miss:

- [`images/runner/README.md`](https://github.com/tguisep/gh-spot-docker-runners/blob/main/images/runner/README.md) — the variants table
- `config.example.toml` — the `# Available:` comment above `[pool.container]`
- [Runner images](../images/) — the table on that page

### 7. Record it in `CONTEXT.md`

A section saying what the variant is for and what was decided about it — particularly
anything left out and why.

### The checklist

| Step | File |
|---|---|
| 1 | `images/runner/*.Dockerfile` — only for a new family |
| 2 | `images/runner/build.sh` — `VARIANTS`, and `MICROARCH_REQUIRED` if it needs one |
| 3 | `images/runner/verify.sh` — only if a required tool is genuinely unavailable |
| 4 | `.github/workflows/runner-images.yml` — the matrix |
| 5 | `images/runner/upstream.lock.yml`, `sync-toolset.sh` — a new toolset or name mapping |
| 6 | `images/runner/README.md`, `config.example.toml`, `site/…/guides/host/images.md` |
| 7 | `CONTEXT.md` |
