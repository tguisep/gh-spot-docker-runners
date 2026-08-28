# The runner images

The GitHub Actions runner on Ubuntu or the RHEL family. They hold no credentials, make no API
calls of their own, and contain no logic beyond forwarding a signal.

Both are built from the same `entrypoint.sh` and honour the same contract, so a workflow does
not have to care which one it landed on beyond the package manager it can call.

## Variants

| Variant | Base | Label a workflow asks for |
|---|---|---|
| `ubuntu-24.04` | `ubuntu:24.04` | `runs-on: [self-hosted, ubuntu-24.04]` |
| `ubuntu-22.04` | `ubuntu:22.04` | `runs-on: [self-hosted, ubuntu-22.04]` |
| `ubuntu-20.04` | `ubuntu:20.04` | `runs-on: [self-hosted, ubuntu-20.04]` |
| `rhel-9` | `almalinux:9` | `runs-on: [self-hosted, rhel-9]` |
| `rhel-10` | `almalinux:10` | `runs-on: [self-hosted, rhel-10]` |
| `jetson-r32` | `ghspot/runner:ubuntu-20.04` | `runs-on: [self-hosted, jetson-r32]` |

The variant name is the image tag *and* the label, so the two cannot drift apart. Serve more
than one by giving each its own pool — a job asking for `rhel-9` will only ever land on a
runner carrying it.

## What is installed

The apt toolset GitHub installs on its own `ubuntu-24.04` image, from
[`actions/runner-images`](https://github.com/actions/runner-images) —
`images/ubuntu/toolsets/toolset-2404.json`. The package lists in the Dockerfiles are grouped
exactly as upstream groups them (`vital`, `common`, `cmd`), so diffing against a future
toolset stays readable, and the RHEL images map the same list onto RHEL package names.

Beyond the upstream apt list:

| Added | Why |
|---|---|
| `git` | `actions/checkout` needs it, and it is not in the apt toolset |
| `python3`, `pip`, `venv` | Debian ships `python3` without `ensurepip`, so `pip` and `venv` are absent by default |
| `cmake` | Upstream installs it as a separate pinned tool rather than from apt |
| `node`, `npm` | Upstream keeps these in a toolcache. A workflow running `npm ci` without `actions/setup-node` is common, and the failure is obscure |
| `pipx` | Upstream installs it outside apt, at `PIPX_HOME=/opt/pipx` and `PIPX_BIN_DIR=/opt/pipx_bin`. Workflows do `pipx install poetry` and expect it |
| `gh` | GitHub's own images ship the CLI, so workflows use it freely — including this project's release workflow, which failed with `gh: command not found` the first time it ran on a self-hosted runner |
| `uv`, `uvx` | What this project is built with. `uvx ruff check` runs a tool with no virtualenv and no install |
| `mise` | Pins language versions per project, so a workflow can declare node or python in `.mise.toml` rather than depending on what the image happens to carry |

`pipx` is kept alongside them for parity with GitHub's images — a workflow written against
those does `pipx install …` and should not have to care. For anything new, `uvx` does the
same job without installing.

`pipx`'s directories are owned by the `runner` user here, unlike upstream: a job installing a
tool at runtime has to be able to write to them, and jobs do not run as root. `verify.sh`
checks that by actually installing something, since being on `PATH` and being usable are not
the same thing.

### Where the list came from, and staying honest about it

`upstream.lock.yml` pins the exact `actions/runner-images` revision our package lists were
transcribed from, so "the toolset GitHub ships" means a specific checkable thing rather than
whatever it was the day someone read it.

```bash
images/runner/sync-toolset.sh                 # against the pinned revision
images/runner/sync-toolset.sh --latest        # against upstream main
images/runner/sync-toolset.sh --update-lock   # repin, then report
```

It checks **both families**. The RHEL images are a second transcription — GitHub publishes no
RHEL images, so ours map the ubuntu toolset onto RHEL package names — and a second
transcription rots exactly like the first. The script carries that name mapping
(`shellcheck` → `ShellCheck`, `libssl-dev` → `openssl-devel`, and so on) along with the
handful of packages RHEL simply does not have.

It reports what upstream has that we lack, what we deliberately leave out, what we install
under a different name, and what we add on top. It never edits an image — which packages to
adopt is a judgement, and the script's job is to make the question visible.

CI runs it on every change to `images/runner/`, and weekly against upstream `main`.

This idea is borrowed from [`runs-on/runner-images-for-aws`](https://github.com/runs-on/runner-images-for-aws),
which pins upstream the same way. That project builds **AMIs for EC2** rather than container
images, so nothing in it could be used directly here — but the pinning pattern is the right
answer to a transcription that would otherwise rot quietly. `pipx` is what rotting looks
like: upstream had it, we did not, and the first anyone heard was a workflow failing on
`pipx: command not found`.

### Deliberately not installed

**Container-hostile packages.** `systemd-coredump` (drags in systemd), `pollinate` (a
boot-time entropy service) and `haveged` (an entropy daemon the kernel has not needed for
years) are skipped on every variant.

**Language toolchains.** No preinstalled Go, Java, Ruby, PHP, .NET, or alternative Pythons.
`actions/setup-python`, `setup-go`, `setup-java` and the rest download what they need at
runtime, so workflows using them already work — they are simply slower on a cold runner than
on GitHub's, which ships a toolcache. Replicating that toolcache would mean tens of
gigabytes per variant.

### Not available on every release

| Tool | Note |
|---|---|
| `upx`, `Xvfb` | Dropped in RHEL 10 with no replacement. Use an Ubuntu variant if a workflow needs them |
| `mediainfo`, `sphinxsearch` | RPM Fusion only; not installed on the RHEL variants |

`7z` and the emoji fonts were *renamed* rather than dropped in RHEL 10; both spellings are
listed and whichever exists is installed. The build prints which optional tools it got, so an
image is never quietly missing something.

### rhel-10 needs a modern CPU

RHEL 10 raised its baseline to the **x86-64-v3** microarchitecture level (AVX2, BMI, FMA).
On a machine without it the base image aborts on its first command:

```
Fatal glibc error: CPU does not support x86-64-v3
```

That looks like a broken Dockerfile and is not one, so `build.sh` checks first and says so.
The usual cause is a virtual machine presenting a generic CPU instead of the real one:

| Hypervisor | Fix |
|---|---|
| Proxmox / QEMU | Set the CPU type to `host` |
| VMware | Enable host CPU feature passthrough |
| libvirt | `<cpu mode='host-passthrough'/>` |

Check what a machine reports with:

```bash
/lib64/ld-linux-x86-64.so.2 --help | grep x86-64-v
```

If the physical CPU genuinely predates v3 — roughly pre-2015 Intel, pre-2017 AMD — use
`rhel-9`, which has no such requirement.

### jetson-r32 is arm64, and is built on the Ubuntu variant

The Jetson variant is the `ubuntu-20.04` image plus the two things a Tegra board needs to
find its GPU. Everything else — the toolset, the runner payload, the unprivileged user — is
already right there, so this is a thin layer rather than a base of its own. `build.sh` builds
`ubuntu-20.04` first if it is missing.

**Why not `nvcr.io/nvidia/l4t-base:r32.7.1`,** which is the obvious base. It is Ubuntu 18.04,
and the GitHub Actions runner has shipped .NET 8 since v2.317, which needs glibc 2.28.
Ubuntu 18.04 has 2.27. The board's own 18.04 userspace does not constrain the image — a
container brings its own — so this builds on 20.04, the oldest release
[GitHub still supports](https://docs.github.com/en/actions/reference/runners/self-hosted-runners#linux)
for a runner.

**Why 20.04 and not 24.04.** JetPack 4 mounts the host's Tegra driver into the container at
start, and those libraries are built against the board's 18.04 userspace. The closer the
container is to that, the less there is to go wrong.

**What the layer adds.** JetPack puts no driver in the image; `nvidia-container-runtime`
bind-mounts it in at container start, into `/usr/lib/aarch64-linux-gnu/tegra`. The image has
to be able to find it, which takes both of these:

| | Why |
|---|---|
| `/etc/ld.so.conf.d/nvidia-tegra.conf` | For anything that runs `ldconfig` itself |
| `LD_LIBRARY_PATH` | Because `/etc/ld.so.cache` is generated at build time, when those directories are still empty. `ld.so` consults the cache and then only its trusted defaults, so a path listed in `ld.so.conf` whose contents arrived afterwards is never searched — `libcuda.so.1` would be mounted in and still not found |

`verify.sh jetson-r32` checks for both.

It is an arm64 image, so `build.sh` refuses to build it on x86-64 rather than producing
something the board reports as `exec format error` much later. Build it on the Jetson, or
cross-build:

```bash
docker run --privileged --rm tonistiigi/binfmt --install arm64
docker buildx build --platform linux/arm64 \
  --file images/runner/jetson.Dockerfile \
  --build-arg BASE_IMAGE=ghspot/runner:ubuntu-20.04 \
  --tag ghspot/runner:jetson-r32 images/runner/
```

Building every variant on an x86-64 machine skips this one with a note rather than failing.

### Size

Roughly 2.6–2.7 GB per variant, against about 1.7 GB for the runner alone. That is what the
toolset costs. To trim it, remove packages from the relevant Dockerfile — but note that
`verify.sh` will then fail, which is the point: it is the list saying what the image promises.

## CI builds under a different name

CI builds these on **GitHub-hosted** runners, as `ghspot/runner-ci:<variant>`.

Hosted, because the images CI builds are throwaway, four variants at ~2.7 GB each would fill
a home server, and `rhel-10` cannot build on a host whose CPU lacks x86-64-v3.

Namespaced, because if that is ever routed back onto the fleet the build would run against
the *host's* Docker daemon — the same one serving real jobs — and building under the
operational tag would let a pull request silently replace the image those jobs run in.

If you find a stray `ghspot/runner:ci` on a host, it is a leftover from before this split and
is safe to delete.

## Verify

```bash
images/runner/verify.sh ubuntu-24.04
```

Checks the contract (refuses to start without a configuration, carries no credential, runs
unprivileged, docker gid matches the host, runner payload present) and that every required
tool resolves. CI runs it for each variant on every pull request.

## Build

```bash
images/runner/build.sh                 # every variant
images/runner/build.sh rhel-9          # just one
```

The script detects the host's `docker` group id and passes it in. That id must match, or the
unprivileged `runner` user cannot use the mounted socket — and the failure looks like a job
saying `permission denied` on `/var/run/docker.sock`, not like a build problem.

The images assert it at build time rather than letting it slip: on the RHEL family the Docker
CE package creates its own `docker` group first, so a plain `groupadd` silently does nothing
and the id ends up wrong.

### Which RHEL rebuild

`rhel.Dockerfile` takes `BASE_IMAGE`, defaulting to AlmaLinux — a faithful RHEL rebuild with
complete repositories and no subscription. Also valid:

```bash
docker build -f images/runner/rhel.Dockerfile \
  --build-arg BASE_IMAGE=registry.access.redhat.com/ubi9/ubi \
  --build-arg DOCKER_GID="$(getent group docker | cut -d: -f3)" \
  -t ghspot/runner:rhel-9 images/runner/
```

`rockylinux/rockylinux:9` and `quay.io/centos/centos:stream9` work the same way. Red Hat's own
UBI is the closest to genuine RHEL, at the cost of a reduced package set — some things a
workflow expects simply are not in its repositories.

## Contract

| | |
|---|---|
| Input | `RUNNER_JIT_CONFIG` — a single-use just-in-time configuration blob |
| Behaviour | Runs exactly one job, then exits |
| On `SIGTERM` | Forwards it so the runner finishes the job it accepted |
| Exit 64 | `RUNNER_JIT_CONFIG` was not set |

Everything deciding *whether* a runner should exist lives in the daemon on the host, where it
is testable.

## Updating the runner version

`RUNNER_VERSION` and the two `RUNNER_SHA256_*` values are pinned in each `Dockerfile`. GitHub
requires runners to be no more than 30 days behind the current release. The checksums are
published in the release notes, so bumping does not mean trusting a download:

```bash
gh api repos/actions/runner/releases/latest \
  --jq '.tag_name, (.body | scan("BEGIN SHA linux-(x64|arm64) -->([0-9a-f]{64})"))'
```

Currently pinned: **v2.336.0**.
