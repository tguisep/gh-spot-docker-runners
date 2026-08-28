# Context

Running history of what this project is, what has been decided, and why. Newest section last.

## 2026-08-26 — Project inception

**Problem.** GitHub's free plan caps Actions minutes on private repositories. An Ubuntu 26.04 VM on a
home server can host runners instead; self-hosted runners don't consume Actions minutes.

**Prior art.** [`QMUL/github-actions-runner-docker`](https://github.com/QMUL/github-actions-runner-docker)
— a Dockerfile plus `start.sh`, scaled with `docker compose up --scale runner=N`, with a
`delete-offline-runners.sh` script to clear runners stuck `Offline` after a `SIGKILL`. Its limits are
structural rather than incidental:

- the registration PAT is baked into every container, readable by any job;
- nothing reconciles Docker state against GitHub state, so they drift — hence the cleanup script;
- `--scale N` is a human guess, not a response to queued jobs;
- all logic is shell inside a container, so none of it is testable.

**Direction.** A Python daemon owning the whole lifecycle — mint credentials, start the container,
watch the job, tear both sides down — built as ports and adapters so the domain is testable without
Docker or network.

### Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Scaling signal | Poll the GitHub API for queued jobs | No inbound ports; works behind home NAT. Webhooks remain a pluggable adapter. |
| Registration | Just-in-time config API | The PAT never enters a container; runners are inherently ephemeral; the GitHub runner id is known before the container exists, so correlation is always possible. |
| Interfaces | Typer CLI + FastAPI REST API | CLI is the operator surface; the API enables remote management and a future dashboard. |
| Docker access in jobs | Mount the host Docker socket | Jobs can build images and share the host layer cache. Accepted trade-off: a job with socket access has effective root on the host. |
| Scope | Personal repositories | Matches the free-plan use case. Org and enterprise scope are modelled but not implemented. |
| Source of truth | Docker labels + the GitHub API | SQLite is a projection rebuilt each tick, so a wiped database costs history, never correctness. |
| Naming | Repo `gh-spot-docker-runners`, package and CLI `ghspot` | Short to import and to type. |

### Out of scope for v1

Web dashboard, Prometheus metrics, organisation and enterprise runner groups, Docker-in-Docker.
Each is one adapter away by design.

## 2026-08-26 — v0.1 built

Nine increments, one branch and one pull request each, stacked in order:

| # | Branch | What landed |
|---|---|---|
| 1 | `chore/project-scaffold` | Layout, uv, ruff, mypy strict, pytest, CI, the layering test |
| 2 | `feat/domain-model` | Runner aggregate, pool invariants, scaling policy, ports |
| 3 | `feat/reconciliation` | Provisioning, retirement, the control loop, in-memory fakes |
| 4 | `feat/github-adapter` | REST client, ETag cache, error translation |
| 5 | `feat/docker-runner` | Container backend, runner image, entrypoint |
| 6 | `feat/persistence-config` | SQLite projection, config loading, logging |
| 7 | `feat/daemon-and-cli` | Composition root, daemon loop, CLI, systemd unit |
| 8 | `feat/rest-api` | FastAPI served in-process with the loop |
| 9 | `docs/architecture-and-operations` | Architecture, operations, ADRs, security |

### Things learned while building, worth not re-learning

**A top-level `docker/` directory shadows the `docker` package.** Anything importing the SDK
from the repository root resolved to the directory instead. mypy caught it. The directory is
now `images/`.

**`list_active()` could never return a terminal runner**, so `--all` and
`include_terminal=true` silently returned nothing. The repository port had no way to express
"everything"; it now has `list_all()`. Found by an API test, not by review.

**Two crash shapes need different handling.** A failed container start leaves no active
record, so the stray sweep can reap the registration immediately. A `SIGKILL` mid-provision
leaves the record `REGISTERED`, which from outside is indistinguishable from a slow boot —
hence the grace period. Writing one test for both was wrong, and the failure said so.

**CI could not run**: the repository was private on the free plan and its Actions minutes
were exhausted — the exact problem this project exists to solve. Made public on 2026-08-26,
after which CI passes. The `runner-image` job fails on branches below `feat/docker-runner`,
because that is where the Dockerfile lands; it resolves as the stack merges.

**The layering rule needed stating precisely, not loosening.** `interfaces` must reach
`infrastructure` — an entry point has to name a concrete adapter or nothing gets constructed.
The rule that carries the weight is that `domain` and `application` do not, and that is what
the test now says.

### Verified end to end

- The runner image builds, verifies its checksum, exits 64 without `RUNNER_JIT_CONFIG`,
  carries no credential-shaped environment variable, and runs unprivileged in the host's
  docker group.
- `doctor` correctly reports a rejected token and exits 1.
- `daemon --once` against an invalid token logs the failure, completes the tick, exits 0.
- The API answers over real HTTP alongside the loop.

### Not done

No end-to-end run against a live repository yet — that needs a real token on the VM. Until
then the GitHub side is covered by `respx` cassettes rather than by GitHub itself.

## 2026-08-26 — GitHub App authentication

Added as a second authentication mode, alongside the personal access token. For a daemon
polling continuously, an App is the better credential: the rate limit belongs to the
installation rather than to a person, the permissions are the app's rather than everything
the account can reach, and installation tokens expire hourly on their own. Recorded in
[ADR 6](docs/adr/0006-github-app-alongside-pat.md).

The structural change is small but real: `GitHubClient` used to set `Authorization` once in
its constructor. An installation token expires under a long-running daemon, so the header is
now built per request from a `TokenProvider`. `StaticTokenProvider` and
`GitHubAppTokenProvider` are the two implementations.

PAT support stays. The first five minutes with the project should not require creating a
GitHub App.

### Notes for later

- The App private key is validated by signing at construction, so a malformed PEM surfaces
  from `ghspot doctor` rather than as an opaque 401 an hour into a run.
- `installation_id` is discovered from the first configured repository, so the common
  single-installation case needs no extra setup.
- `GHSPOT_GITHUB_APP_PRIVATE_KEY` accepts `\n` escapes, because systemd `EnvironmentFile`
  cannot hold real newlines.
- Tests generate a real RSA keypair and verify the JWT against the public key. Stubbing the
  signing step would not have caught a malformed key, which is the likeliest failure here.

### Repository history note

Merging the nine-PR stack went wrong: `gh pr merge --delete-branch` removed each parent
branch while its child still pointed at it, and GitHub **auto-closed** #2, #4, #6 and #8
rather than merging them. No work was lost — `feat/rest-api` held the complete tree — and it
was landed on `main` as #10 after confirming byte-identity with the verified state. When
merging a stack, either merge without `--delete-branch` or retarget each child first.

## 2026-08-28 — Jetson support: a runtime rather than a device request

A Jetson reaches its GPU differently from a desktop with an NVIDIA card, and the difference
is not a detail. JetPack's container stack predates the Engine's device-request API, so
`--gpus` — and the `gpus` setting built on it — cannot work on a Tegra board at all. The GPU
is granted by running the container under JetPack's own runtime.

So pools gained a `runtime` key, plumbed through `RunnerTemplate` and `ContainerSpec` to the
Engine. `gpus` and `runtime` are alternatives, and on a Jetson only the second one works.

The image is the `ubuntu-20.04` variant plus two lines, not a base of its own. Two choices in
it are worth keeping:

- **Not `nvcr.io/nvidia/l4t-base:r32.7.1`**, the obvious base. It is Ubuntu 18.04, and the
  runner has shipped .NET 8 since v2.317, which needs glibc 2.28; 18.04 has 2.27. The board's
  own 18.04 userspace never constrained the image — a container brings its own — so the
  variant builds on 20.04, the oldest release GitHub still supports for a runner. It was
  briefly mistaken for a blocker on the whole feature, which it is not.
- **Both `ld.so.conf.d` and `LD_LIBRARY_PATH`**, which looks redundant and is not.
  `/etc/ld.so.cache` is generated at build time, when the tegra directories are still empty.
  `ld.so` reads the cache and then only its trusted defaults, so a path listed in
  `ld.so.conf` whose contents were mounted in afterwards is never searched. Without the
  environment variable the driver is present in the container and unreachable.

### Notes for later

- `doctor` now tells a Jetson from a desktop by `/etc/nv_tegra_release`, because the previous
  driver probe was `which nvidia-smi` — and there is no `nvidia-smi` on Tegra. On a Jetson it
  refuses a pool that sets `gpus` and names `runtime` as the fix.
- The control plane is fine on the board's 18.04: the `.deb` bundles an interpreter built
  against glibc 2.17, verified by reading the aarch64 build's ELF version requirements rather
  than by assuming.
- `build.sh` refuses to build an arm64 variant on x86-64, and skips it with a note when
  building every variant, so a developer machine still builds the rest.
- Untested on real hardware at time of writing: no Jetson was available. The board-side
  checks are the `docker info` runtime listing and the `ldconfig -p | grep libcuda` smoke
  test in [`docs/operations.md`](docs/operations.md).
