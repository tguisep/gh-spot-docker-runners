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

## 2026-08-28 — `ghspot stats`, counted from the log

A report of what the fleet did: runners started, jobs served, failures, and time spent,
grouped by repository and by pool, over a window.

The one decision that shaped the rest is where the numbers come from. The obvious source is
the `runners` table, and it is the wrong one: rows are deleted as runners retire, and
`prune()` keeps only a few hundred terminal records. A report built on it would look right
and quietly stop covering the period being asked about. The event log is append-only and
nothing deletes from it, so that is the source.

One runner's story is at most six events, and the useful quantities are the gaps:

    registered ──wait──▶ took job ──busy──▶ retired
    └────────────────── alive ──────────────────┘

`wait` is what `min_idle` buys down; `busy / alive` is whether warm capacity is being used.
A runner with no `took job` at all is capacity that cost time and returned nothing, and it
is reported rather than averaged away.

### Notes for later

- `RunnerRegistered` gained a `pool` field so the report can group by pool — events carried
  the repository already but not the pool. It is **defaulted**, because a required field
  would make every previously written event fail to load and silently empty `ghspot history`.
- Live runners come from the projection, not the log: a runner working right now has no end
  event, so the log cannot see it. The two sources sit in one table, which is why `live` is
  the only column that is not derived from events.
- A window that excludes a runner's registration still catches its later events. Those group
  under `(unknown)` rather than being dropped, so the rows always sum to the total. Hiding
  them would make the report disagree with itself.
- Gaps are clamped at zero. Timestamps come from the daemon's clock, and a clock stepped
  backwards mid-run would otherwise subtract from an operator's totals.
- `_duration` in the config parser became `parse_duration`: `--since 7d` takes the same
  grammar as `poll_interval`, and two implementations of "10m" would eventually disagree.
- Failure reasons are collapsed to their first line and trimmed to 80 characters. Reasons
  carry ids and messages, and without that every failure looks unique and the tally says
  nothing.

## 2026-08-28 — `pm`, borrowed from php-fpm

"Keep four warm", "keep a band warm" and "start one only when there is work" are three
different intentions. All three could already be written with `min_idle` and `idle_timeout`,
and two of the three ways to write each were subtly wrong. So a pool now names its mode:

| `pm` | Applies |
|---|---|
| `dynamic` | `min_idle`, `max_idle`, `idle_timeout` — the default, and what the daemon always did |
| `static` | `max_runners` only. Exactly that many, never reaped |
| `ondemand` | `idle_timeout` only. Nothing warm |

**A key that does not apply to the mode is refused at load**, the way php-fpm refuses
`pm.min_spare_servers` under `pm = static`. A setting quietly doing nothing is worse than one
that will not load: the pool behaves unlike its configuration and nothing says so.

`max_idle` — php-fpm's `max_spare_servers` — is genuinely new, and the gap worth closing.
Before it, only `idle_timeout` bounded how many warm runners accumulated, so a burst of twelve
left twelve warm for the full timeout on a host that had gone back to needing one.

### Notes for later

- `ondemand` still waits out `idle_timeout` rather than reaping the instant a job ends. That
  matches php-fpm's `process_idle_timeout`, and a runner that just finished is the one most
  likely to be wanted next. The first version reaped immediately; the tests said so.
- The warm band is derived in the policy, not the parser: `static` means "the floor is
  `max_runners`", and the loop has to keep agreeing with that as a pool's ceiling changes.
- Nothing is reaped while work is queued, `max_idle` included. Reaping capacity in the same
  tick a pool is short of it would oscillate — the rule that already governed `idle_timeout`.
- The role's template has to know the same "which keys apply" table as the daemon, one layer
  out. It got that wrong first — it wrote `min_idle` under `ondemand`, which the parser
  refuses — and the render check caught it before CI did.
