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
