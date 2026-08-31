---
title: "Runner images and labels"
description: "The variants, what is in them, and how labels route jobs."
---

## Available variants

| Variant | Base | Ask for it with |
|---|---|---|
| `ubuntu-24.04` | `ubuntu:24.04` | `runs-on: [self-hosted, ubuntu-24.04]` |
| `ubuntu-22.04` | `ubuntu:22.04` | `runs-on: [self-hosted, ubuntu-22.04]` |
| `rhel-9` | `almalinux:9` | `runs-on: [self-hosted, rhel-9]` |
| `rhel-10` | `almalinux:10` | `runs-on: [self-hosted, rhel-10]` |

```bash
ghspot image build                     # all of them
ghspot image build rhel-9              # just one
ghspot image list                      # the variants and their base images
```

From a checkout the script is there to call directly, which is what `ghspot image build`
does for you:

```bash
images/runner/build.sh rhel-9          # the same build
images/runner/verify.sh rhel-9         # check the contract and the toolset
```

Each image carries the apt toolset GitHub installs on its own hosted runners, plus `git`,
`cmake`, `node`/`npm` and a working `pip`. Language toolchains are **not** preinstalled:
`actions/setup-python`, `setup-go`, `setup-java` and the rest fetch what they need at
runtime, so workflows using them work as-is, just slower on a cold runner than on GitHub's
toolcache-equipped images. See [`images/runner/README.md`](https://github.com/tguisep/gh-spot-docker-runners/blob/main/images/runner/README.md) for
the full list and the handful of tools unavailable on RHEL 10.

The variant name is the image tag *and* the label. Keeping them the same string is what stops
a pool from advertising an OS it is not actually running.

## Name the OS in the labels

A bare `linux` tells a job nothing. Prefer the specific form, the way GitHub's own hosted
runners are labelled:

```toml
labels = ["self-hosted", "linux", "x64", "ubuntu-24.04", "home-vm"]
```

`linux` and `x64` are still worth keeping — plenty of workflows ask for them — but a job that
needs `dnf`, or a particular glibc, can now say so and be sure of what it gets.

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

## Choosing a RHEL rebuild

`rhel-9` and `rhel-10` are built on AlmaLinux, a faithful RHEL rebuild with complete
repositories and no subscription. To build on something else:

```bash
docker build -f images/runner/rhel.Dockerfile \
  --build-arg BASE_IMAGE=registry.access.redhat.com/ubi9/ubi \
  --build-arg DOCKER_GID="$(getent group docker | cut -d: -f3)" \
  -t ghspot/runner:rhel-9 images/runner/
```

`rockylinux/rockylinux:9` and `quay.io/centos/centos:stream9` work the same way. Red Hat's
UBI is the closest to genuine RHEL, at the cost of a reduced package set — some things a
workflow expects are simply not in its repositories.

## The docker group id

`build.sh` detects the host's `docker` group and builds it in. If it does not match, jobs fail
with `permission denied` on `/var/run/docker.sock` — which looks nothing like an image
problem. The images now assert it at build time, because on the RHEL family the Docker CE
package creates its own `docker` group first and a plain `groupadd` silently does nothing.

If you move an image to another host whose `docker` group id differs, rebuild it there.
