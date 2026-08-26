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
