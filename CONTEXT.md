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
[ADR 6](https://tguisep.github.io/gh-spot-docker-runners/reference/adr/0006-github-app-alongside-pat/).

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

## 2026-08-28 — pools in their own files

One growing `config.toml` stops being reviewable somewhere around the fourth pool, so pools
can live one per file, the way php-fpm keeps them in `php-fpm.d`:

```toml
include = "/etc/ghspot/pools.d/*.toml"
```

The merge rules are php-fpm's, and they are the whole design:

- The glob is **sorted**, so the fleet does not depend on the order a directory returns.
- Files are **merged, never overridden**. There is no last-one-wins: a pool silently replaced
  by a file later in the alphabet is not something anyone would debug quickly.
- A **duplicate name is fatal**, naming both files. php-fpm refuses to start rather than
  picking one, and so does this — which definition won would be invisible in the running fleet.
- An included file defines **pools only**. Global sections stay in the main file, or which
  file wins becomes a question with no obvious answer from either of them.

### Notes for later

- **`include` has to sit above the first `[section]`.** In TOML a bare key belongs to whichever
  table precedes it, so written lower down it quietly becomes `github.include` and does
  nothing at all. That is now an error naming the section it landed in and saying where to
  move it — a misplaced directive that silently does nothing is the worst outcome available.
- A pattern matching nothing is not an error. An empty `pools.d` on a host still being set up
  is normal, and "at least one pool" already covers the case where that leaves none.
- The Ansible role gained a second template, and with it the chance for the two to disagree
  about the schema. The render check now renders both and loads them together; dropping a key
  from the per-pool template fails it with `directory: a pool file lost a key the inline form
  keeps`. The role also deletes the file of a pool removed from the inventory — the include is
  a glob, so nothing else would.
- Unrelated but found on the way: `fail()` printed error messages through Rich without
  escaping, so `[daemon]` and `[[pool]]` were read as style tags and vanished. The one part of
  a configuration error naming what is wrong was the part being eaten.

## 2026-08-28 — bounding the host, not just the pool

`max_runners` bounds one pool, and nothing bounded the machine. Four pools with room to spare
each start runners at the same time, on one box. Three mechanisms close that, and they are
deliberately separate because they fail differently.

**Ceilings** — `max_containers`, `max_cpus`, `max_memory` — are arithmetic over the runners
that exist. No measurement, so they cannot be wrong. `max_containers` is the one that always
applies; the resource ceilings only count pools that reserve `cpus` and `memory`.

**Backpressure** — `cpu_high_water`, `memory_high_water` — is a gate on what is measured, and
catches what the arithmetic cannot: everything else on the box, a job using far more than its
pool reserved, a machine already struggling. At or above the mark nothing starts, even where a
pool has a free slot.

**Priority** is a **share**, not a rank, and this was the correction that mattered. The first
version sorted pools by priority and drained the heaviest first — which is what "priority"
usually means, and it starves everyone else: a lighter pool waits until the heavier one is
satisfied, and on a fleet that is always busy that is the same as never.

It is now smooth weighted round robin, the algorithm nginx uses to spread requests across
upstreams. Each round every contender gains its weight in credit, the richest takes the slot
and pays the total back, so weights 10 and 5 produce `A B A A B A` — two thirds and one third,
interleaved, rather than `A A A A B B`.

A pool that stops wanting runners drops out and its share is redistributed: weights settle
contention, they do not reserve a quota. And a pool too expensive for what is left no longer
stops the allocation — four CPUs will not fit in two remaining, but one will, so the fat pool
drops out for the tick and the thin one carries on.

There is no queue to persist: a pool refused this tick wants the same thing on the next one,
and the loop re-derives everything anyway. The queue is the reconciliation loop.

### The shape this forced

`tick()` used to plan and act per pool in one pass. It cannot: how many runners the host can
take is a question about every pool at once. So every pool is now planned first — including
retiring and terminating, which happen in that first pass because they *release* capacity —
and launches are admitted afterwards against the whole picture.

Only launches are trimmed. Refusing the operations that free capacity is the one thing that
would turn a busy host into a stuck one, and there is a test for it.

### Notes for later

- **An unreadable host never blocks.** A probe that cannot see the machine degrades to the
  ceilings, which need no measurement. A careful mechanism that stops the fleet when its own
  probe breaks is worse than no mechanism.
- The probe is only taken when a high-water mark is configured *and* something wants to
  launch. It is an Engine call plus a `/proc` read, and a fleet with no limits should pay for
  neither.
- CPU is the one-minute load average over cores, not an instantaneous sample: it already
  covers everything on the box, and it is the number that says whether work is *queueing* for
  the CPU. Memory uses `MemAvailable`, because `MemTotal - MemFree` counts the page cache and
  makes any working machine look 95% full.
- Weights start at 1 and zero is refused rather than quietly read as one. A weight of nothing
  has no meaning in a proportional split, and somebody writing it means "never" — which is
  spelled by giving the other pools a much larger number.
- **`0 == False` in Python**, so the first version of the config parser read
  `max_containers = 0` as "not configured" — silently unlimited, the opposite of what anyone
  writing that means. The parser now tests identity. The test suite caught it, not review.

## 2026-08-28 — a dashboard, and the numbers behind it

An alternative to the CLI rather than a replacement: the same pools, runners, logs and usage
report, plus the two interventions — stop a runner, force a tick. React and TypeScript, built
by Vite, served by the daemon itself.

### Why /ui and not /

The dashboard's own routes are named after the same things the API's are — `/runners`,
`/pools`. Mounting it at the root would have the two shadow each other, and which one won
would depend on registration order. `/ui` costs one path segment and removes the class of bug;
`/` redirects there. The mount happens after every API route is registered, so nothing the
dashboard adds can shadow one.

It is optional in the strongest sense. The daemon does not build it, does not require it, and
starts normally when it is absent; a package built on a machine without `npm` simply has no
dashboard. A directory without an `index.html` is not a dashboard either — otherwise a
half-finished build would be mounted and 404 every page instead of reading as "not built".

### CPU and memory

`docker stats` per container, off by default everywhere: the CLI's listings and the API's
`/runners` are otherwise a file read, and sampling would put an Engine call per runner behind
them. `--usage` on the CLI, `?usage=true` on the API, a checkbox on the dashboard.

Two details in the derivation are worth keeping:

- **CPU is a rate and the Engine reports counters**, so it comes from the two snapshots every
  sample carries. A just-started container has identical snapshots, so the system delta is
  zero — reported as 0%, not as a division error.
- **Memory subtracts the page cache.** Without that, a job that merely read a large file looks
  like a job that leaked, because Linux charges cache to the cgroup until something needs the
  pages.

A runner with no sample keeps `None`, never zero. "Not measured" and "idle" are different
facts, and a container that has exited is not using nothing — it is not there.

### `--watch`

`watch ghspot pool status` was the habit this replaces. `watch` re-runs the whole command, so
every refresh re-reads the configuration and reopens the database, and it drops the colours
unless told otherwise. `--watch 2` keeps the process up and repaints the frame. Ctrl-C is how
it is meant to end, so it exits 0.

### Notes for later

- The log view polls the existing endpoint every two seconds rather than adding a streaming
  one. At a runner's log volume that is indistinguishable from a follow, and it holds no
  connection open per viewer. If logs ever get long enough for that to hurt, the endpoint to
  add is a real one, not a faster poll.
- Polling pauses while the browser tab is hidden, and a failed poll keeps the last good data
  on screen rather than blanking the page.
- `RunnerView` gained three optional fields rather than a nested object, because the CLI
  renders them as two columns and a nested null is more awkward to thread than three.
- The web tests mount the app against a stubbed `fetch`. A React app that throws on mount
  serves a 200 with an empty body, which every check short of opening it in a browser reports
  as healthy — this was the cheapest substitute available for actually opening it, which is
  still worth doing before release.

## 2026-08-28 — the forge's log, alongside the container's

A runner has two logs, and the difference between them is a schedule, not a format:

| | What | When |
|---|---|---|
| container | The job as it happens — the runner prints its work to stdout, so `docker logs` *is* the live job output | Now, and gone with the container seconds after the job ends |
| GitHub | Its own log, timestamped and grouped by step | Written when the job **finishes** |

**There is no live GitHub log, and this was checked rather than assumed.** Asking
`GET /repos/{o}/{r}/actions/jobs/{id}/logs` for a job in progress answers
`404 BlobNotFound`; the same request after it completes answers 200. So "live GitHub logs"
is not an implementation difficulty, it is a thing the API does not do.

What the endpoint buys instead is the half the container cannot give. A just-in-time runner
is removed the moment its job ends, taking its log with it — `ghspot runner logs` on a
retired runner had nothing to show. GitHub's copy is what remains.

The dashboard shows both panes side by side: the left one live, the right one saying what it
is waiting for and filling itself when the job ends.

### Notes for later

- **The download is two requests on purpose.** GitHub answers 302 with a signed URL on its
  blob store, and that host is not GitHub. Following the redirect with the Authorization
  header still attached would hand a credential that can register runners to somebody else's
  domain. The redirect is read, then fetched with a clean client — and there is a test that
  fails if the header ever appears on the second request.
- 404 is mapped to `None`, not to an error: a job in progress is the normal case, and it has
  to be distinguishable from an empty log so the UI can say which it is.
- The forge pane polls at ten seconds against the container's two. Until the job ends, every
  one of those requests is a 404 that spends rate limit to learn nothing.
- Only the tail is returned. A completed job's log runs to megabytes.

## 2026-08-29 — a first run that guides itself

What somebody has after `apt install ghspot` is a package and no idea what to write. Two
answers to that, one per place they might be standing.

**`ghspot setup`** asks the four things that cannot be guessed — which credential, where it
is, which repository, what the pool is called — and writes an ordinary configuration file.
Its whole output is a file an operator could have written by hand, and it says where it put
it. A token goes to a file created `0600` *before* anything is written to it; an App's private
key is pointed at, never copied.

**The dashboard** shows a setup screen when the daemon is up and the configuration is not
finished. Without it a fresh install shows a correct and completely useless picture: zero
pools, zero runners, and no clue that anything is missing.

### Why the package does not just run the wizard

A Debian install has to work unattended. A maintainer script that stops to ask questions
breaks `apt install -y` on every machine that images itself, so postinst prints the invitation
and nothing more.

### Notes for later

- `/health` gained `configured` and `setup_reason`, deliberately *not* folded into `status`.
  `status` is about whether the daemon can operate; a fresh install is not a broken one, and
  anything watching that endpoint would have started paging.
- "Unconfigured" is a narrower question than `doctor` asks. It is: has anyone finished filling
  the file in — a pool still pointing at the packaged `OWNER/REPOSITORY`, or no credential
  resolving. Not whether Docker works or the token has the right scopes.
- Escaping bit twice more. A label list is square brackets, and Rich reads those as style
  tags, so the one line telling somebody what to paste into their workflow was the line that
  vanished.
- The web suite grew a second render test and immediately failed on "found multiple elements":
  vitest is not running with globals, so testing-library never registered its own cleanup and
  the previous test's DOM was still mounted. Latent since the first render test.

## 2026-08-29 — a container ceiling by default

`capacity.max_containers` now defaults to the machine's core count rather than to no limit at
all. An unbounded host starts a container per queued job until it stops responding, and the
first thing an operator learns about it is that the machine is gone.

Cores is not a measurement of anything — it is a defensible number the box can name for
itself, and it is wrong in the safe direction. `max_containers = "unlimited"` lifts it on
purpose, spelled out the way `[housekeeping]` spells `"never"` rather than by writing a zero
and hoping.

## 2026-08-31 — the runner build comes with the daemon

Every "build the runner image" hint printed `images/runner/build.sh` verbatim: the wizard's
first next-step, `doctor`'s remedy, the `ImageNotFoundError` message, the postinst, the
dashboard's setup screen, the README quick start. That path is real only for somebody
standing in a clone, and the `.deb` shipped the daemon without `images/` — so on exactly the
host the package exists to serve, the instruction named a file that could not be there.

Two halves. The package now installs the sources to `/usr/share/ghspot/images/runner`, and
`ghspot image build <variant>` finds them — packaged location first, then the checkout, with
`GHSPOT_RUNNER_IMAGES` overriding both, the same shape `dashboard.find_root` already used for
`web/dist`. Every hint says `ghspot image build`, which is the one instruction true in both
places.

The CLI does not reimplement the build. It locates `build.sh` and runs it with stdio
inherited, so a `docker build`'s minutes of layer progress stay visible. `build.sh` grew a
`--list`, which is what `ghspot image list` calls — the variant table stays declared once.

`doctor`'s remedy used to be a hand-written `docker build` restating the `DOCKER_GID`
build-arg. It is now the same `ghspot image build` line; a remedy that drifts from the real
build is worse than no remedy.

### The wizard also had a sudo bug

Step 2 printed `ghspot doctor -c /etc/ghspot/config.toml` with no `sudo`, while step 3 knew
to ask for it. The file is `root:ghspot 0640` and the checks want the Docker socket, and the
operator reads that list in a shell where the wizard's own sudo has long expired.

### Notes for later

- The Ansible role lost its shallow checkout and the `git` it installed to make one, along
  with `ghspot_checkout`. It builds through `ghspot image build` now, which has the side
  effect of pinning the images to the installed version rather than to whatever `main` held.
- `subprocess` output does not reach Click's `CliRunner` buffer, because it goes to the real
  file descriptor. The tests use `capfd`, and a test that reached for `result.output` passed
  its exit-code assertion while silently checking an empty string.
- `from ... import setup as setup_module` in a test module collides with pytest's own xunit
  hook name and every test in the file errors before it runs.

## 2026-08-31 — `ghspot setup` fills in the reference

The wizard wrote eighteen lines: the four answers it asked for and nothing else. It parsed,
it ran, and it said nothing about the thirty settings it had not mentioned — the capacity
ceilings, the housekeeping, the pool modes. The file that arrives after `apt install` is the
one an operator is least equipped to go and research, and it arrived empty.

It now writes `config.example.toml` with the answers substituted into it. There is no second
copy of the prose to fall behind: the explanation beside a setting is the shipped one, and a
setting added to the reference turns up in the next configuration the wizard writes without
anybody remembering to add it in two places.

Only what was asked is substituted. Everything else keeps the reference's value — which for
`idle_timeout`, `max_job_duration` and `max_launch_per_tick` *is* the code's default, so the
file says out loud what the daemon would have done in silence. `cpus` and `memory` are the
exception and get commented out: unset they mean no limit, and inheriting the reference's
illustration would cap every job on the host at two cores.

### Notes for later

- The reference is found the same way the runner sources are, and both now prefer the
  checkout over the packaged copy. `IN_TREE` resolves only when the running code *is* the
  checkout's — from the installed `/usr/bin/ghspot` it points inside the virtualenv — so its
  existence already means "you are working in the tree", and quietly using the installed
  version instead is the surprise.
- The wizard echoes the file back after writing it. At eighteen lines that was a
  confirmation; at two hundred it buried the four next steps, which are the point of the
  ending. It prints the live settings only.
- Line surgery on a documentation file has three ways to go wrong and the tests name all
  three: matching a key in the wrong section, matching the commented-out `[[pool]]` the
  reference ends with as if it were real, and echoing a commented-out assignment back as its
  own trailing comment when switching it on.
- `config.example.toml` was carrying a stale `images/runner/build.sh` of its own.

## 2026-08-31 — the wizard offers to build the image

"Build the runner image" has always been the wizard's first next-step, and it is the one step
nothing works without: a pool whose image is missing starts no runners and says so only in the
daemon's log. Now that `ghspot image build` exists, the wizard asks instead of instructing.

Only when there is something to do. `_image_present` returns three answers rather than two —
built, not built, and *Docker could not say* — because treating an unreachable daemon as "not
built" would offer a build that cannot start, one step before `doctor` reports the real
problem properly. No image sources, no offer either.

Order matters: the configuration is validated first. Spending several minutes on an image for
a file that was never going to load is the wrong order to find that out in.

### Notes for later

- `_verify` split into `_validate` and `_next_steps`, and the step list is built rather than
  printed line by line — accepting the build drops the step that asks for it, and the rest
  renumber.
- A failed build keeps the step. Reporting it done because it was attempted would send the
  operator to `doctor` hunting a different problem.
- The unit tests now stub `_image_present` from an autouse fixture. Without it the suite asks
  the machine running it whether the image exists, so the offer appears on a developer's box
  and not in CI — the tests would have passed either way and covered different code.

## 2026-08-31 — the wizard stops granting things by default

`let jobs use Docker` and `serve it` both defaulted to yes, directly under the paragraphs
explaining that the first hands a job effective root on the host and the second puts an
unauthenticated API on it. The prompt argued one way and the default went the other, and
pressing enter through the wizard was enough to take both.

Both default to no now. Turning either on later is one line in a file the wizard hands over
fully commented, which is a much better place to make that decision than a `[y/n]` at the end
of a questionnaire.

## 2026-08-31 — every report says which machine it is about

Several hosts can serve one repository. Each daemon has its own state database and sees only
the runners it started, so every number `ghspot stats` prints is about one box — and an
unlabelled report is one you cannot put beside another. Three dashboards open in three tabs
were indistinguishable.

`[daemon].host` defaults to the system hostname and is settable, because a hostname is often
either meaningless (a cloud instance id) or not unique (a container's), and the name an
operator uses for a machine is the one they want to read in a report. Empty is refused rather
than accepted: it would leave every report unlabelled, which is the one thing this exists to
prevent.

It surfaces in `ghspot stats`, `ghspot doctor`, `/health`, `/stats`, the dashboard header on
every page, and the overview's facts.

### Notes for later

- The header, not just the overview. The failure this prevents is misreading a second tab, and
  a label that only appears on one page does not prevent it.
- The API test harness now pins a host rather than letting `/health` answer with whatever
  machine ran the suite.

### Not fixed here: two daemons on one repository delete each other's runners

`_delete_stray_registrations` deletes any runner GitHub lists whose name starts with
`ghspot-`, that is offline, and that this daemon has no record of. Another host's runners
match all three. Host A reaps host B's registrations, B re-registers, and the two fight for as
long as both are up.

The fix is for the name to carry the host — `ghspot-{host}-{pool}-{id}` — so the sweep can
scope to its own, which also makes ownership readable on github.com. It is deliberately not in
this change: it alters what is registered on GitHub and what the sweep is allowed to delete,
and registrations made before the change would no longer be reaped by it. That deserves its
own review rather than riding along with a display change.
## 2026-08-31 — a fresh apt install could not actually start

Two defects, both on the path the package exists to serve, and both invisible to CI.

**The dashboard was never in the released package.** `packaging/deb/build.sh` bundles it only
`if command -v npm`, and the release builds inside a container that installs
`ca-certificates curl xz-utils fakeroot dpkg-dev` — no node. So every release took the else
branch, `/usr/share/ghspot/web` was never created, nothing mounted at `/ui`, and the API
answered 404. The comment in `build.sh` claimed "CI has node, so released packages always
carry it", which was true of the workflow and not of the container the build actually runs in.

Node comes from the official tarball rather than apt: Ubuntu 24.04 ships node 18 and the
dashboard builds with vite 8, so `apt install nodejs npm` would have produced exactly the same
silent skip. The version is read out of `.mise.toml`, so the container and a developer machine
cannot drift.

And `verify.sh` no longer *reports* a missing dashboard, it fails on it. That note is the
reason this shipped: the packaging job was green for every release that had the bug.

**The wizard wrote a credential the daemon could not read.** `sudo ghspot setup` writes
`/etc/ghspot/token` as root at 0600; the unit runs as `ghspot`. The sequence the wizard itself
prints ended in `could not read the token from /etc/ghspot/token: [Errno 13] Permission
denied`. It also rewrote `config.toml` to 0644 root:root, undoing the 0640 root:ghspot the
package had set — widening the file every time it ran.

### Notes for later

- `ghspot doctor` passed while the service could not start, because `sudo ghspot doctor` reads
  the token as root. It now checks that the *service account* can read the credential. "ready"
  has to mean the daemon is ready, not that the person running it is.
- An App's key is pointed at rather than copied, so the wizard warns instead of chowning
  someone else's file.
- `ConfigurationDirectoryMode=0750` in the unit. The package creates `/etc/ghspot` 0750 and
  systemd's default is 0755, so every single start logged that the mode of the directory
  holding the credentials was wrong, on a unit that was fine.

## 2026-08-31 — a retired runner's last words

Retiring a runner removes its container, and Docker drops the output with it. Checked rather
than assumed: the same `logs()` call returns `the-last-thing-it-said` while the container
exists and `''` immediately after `remove()`. So the logs pane for anything retired was blank,
and the API said so by returning an empty string — the same answer it gave for "not started
yet" and "printed nothing", which is why nobody could tell which they were looking at.

A runner that *finished a job* was survivable: GitHub keeps that log and it outlives the
container by design, which is what the second pane is for. A runner that **failed** was not.
There is no job log, the container was the only witness, and the record said a runner failed
and nothing whatsoever about why.

`RetireRunner` now copies the tail between stopping the container and removing it — the moment
after it has said everything and before its output stops existing. Capped at 500 lines and
256 KiB, keeping the end, because the interesting part of a failure is where it stopped.

### Notes for later

- Its own table, not a column on `runners`: every listing does `SELECT *` on that one, and a
  log-sized TEXT beside twelve small columns would be read on every `ghspot runner list`.
- `REFERENCES runners(id) ON DELETE CASCADE` makes the existing prune the retention policy.
  Nothing else has to remember the archive exists, and it cannot outlive what it describes.
  Foreign keys were already on, so this needed no migration — only a `CREATE TABLE IF NOT
  EXISTS`, which existing databases pick up on the next `prepare()`.
- Capturing is never fatal. Cleanup that fails because a *diagnostic* could not be saved leaves
  a container running and a registration behind, which is far worse than the missing log.
- `LogsResponse` gained `source` and `reason`. An empty string answered three different
  questions identically; the reader needs to know whether to wait, look at GitHub, or accept
  that the evidence is gone.
- The dashboard stops polling once the source is `archive`. A frozen tail asked every two
  seconds gives the same answer forever.
- Runners retired before this shipped have nothing kept. There is no going back for it.

### Also fixed

`test_a_missing_image_says_how_to_build_it` still expected `build.sh` after the message became
`ghspot image build`. It only runs where busybox is already pulled, so CI skipped it and the
staleness sat there — found by pulling busybox to check the log behaviour above.

## 2026-08-31 — the dangerous button says what it does

The dashboard's second stop button was labelled "force", which named the API's query parameter
rather than the effect. The effect is `backend.kill()` — SIGKILL, no grace period — and, on a
busy runner, somebody's build failing. "kill" is what that is.

The 409 message is now written by the dashboard from what it already knows, rather than shown
from the API. The API's wording ends in "Pass force=true to stop it anyway", which is the right
advice for a client and the wrong advice for somebody looking at a button. Appending the
button's name to it produced the same sentence twice in two vocabularies.

### Notes for later

- The query parameter stays `force`. It is a published API and the two names do not have to
  agree: one is a flag, the other is a label on a red button.
- Still missing: `drain` — retire this runner but let its current job finish. The domain has
  the method and the state; nothing reaches it from the API, the CLI or the dashboard, so for a
  busy runner the only choices remain "refuse" and "kill the build".
## 2026-08-31 — which job did this runner run?

The dashboard's GitHub pane said "this runner is not running a job" for every runner that had
just finished one. The cause was not the message. In production code `assign_job` is called
exactly once:

    reconciliation.py:316   runner.assign_job(None, at=now)

Always `None`. So `current_job_id` was never once a real id, `/runners/{ref}/job-logs` gates on
it being set, and **the whole GitHub half of the logs page could never fire** — nor could
`ghspot runner logs --job`. Every call passing a real job id is in a test, which is precisely
why this stayed green for as long as it did: the tests build a state the production code never
reaches.

The reasoning in `assign_job`'s docstring was sound and is still true — the runner list says a
runner is busy without saying which job it took, and correlating on every tick buys the
reconciler nothing. The mistake was leaving a read path depending on a value that reasoning
guaranteed would be absent.

So it is asked for on demand: `find_job_for_runner` walks the last 30 runs newest-first for a
job whose `runner_name` matches, and stops at the first hit. Chosen over correlating during
ticks because it works for runners that retired before any of this existed — the ones somebody
is looking at when they go hunting for a log.

### Notes for later

- The answer is written back to the record. The logs page polls every ten seconds; a search
  per poll would spend the hourly budget on one open tab. `remember_job` exists for that and
  does not move the runner's state — `assign_job` would have claimed a retired runner was BUSY.
- A runner with no `github_runner_id` is never searched for. It cannot have been handed a job,
  so the walk could only prove what the record already says.
- Bounded at 30 runs. A run older than that is not found, and that is the honest trade: the
  alternative is an unbounded walk of a busy repository's history from a page nobody is
  necessarily watching.
- The old message blamed the runner ("is not running a job") for what was really the daemon
  not knowing. Both the CLI and the dashboard now say what actually happened.

## 2026-08-31 — three things a real install found

**Purge left directories behind.** dpkg removes the files it shipped and stops, so any
directory holding something it does not own survives as "not empty so not removed". Running the
daemon once leaves `__pycache__` throughout the bundled interpreter, which is hundreds of them.
`postrm` now takes `/opt/ghspot` on remove and `/usr/share/ghspot` on purge — both are entirely
ours. `verify.sh` creates two unowned files first, so the test fails without the fix; before,
it passed because a container that never ran the daemon has nothing to leave behind.

**The credential warning contradicted the packaging.** `_warn_if_world_readable` flagged
`mode & 0o077` — any group or other bit — and advised `chmod 600`. The package installs
credentials `0640 root:ghspot` on purpose, because the daemon runs as `ghspot`, so the check
fired on the correct layout and its advice would have broken the service. It now distinguishes
the three cases that actually matter: any access for `other`, group-write, and group-read by a
group that is not the daemon's.

**A configuration change did nothing, silently.** Settings are read once, at startup — pools,
labels, limits, every client. Editing the file changes nothing in a running daemon and nothing
said so, so adding a label looked identical to writing it wrong. `/health` now reports
`config_stale`, and the dashboard says it with the restart command.

### Notes for later

- Staleness is the newest mtime across *every* file that was read, `pools.d/*.toml` included. A
  change to a pool file is exactly as invisible as one to the main file.
- `ghspot doctor` cannot report this and does not try. It loads the file itself, so its
  `loaded_mtime` is always "now" — a check there would have passed unconditionally, which is
  worse than no check. Only a process holding settings from earlier can answer it.
- Reporting staleness is not reloading. The daemon still needs a restart; it now says so
  instead of leaving the operator to guess.

## 2026-08-31 — the documentation became a site

`docs/operations.md` had reached 1177 lines across fifteen top-level sections, which is not a
document anybody reads — it is a file people scroll through hoping to recognise something. It
is now fifteen pages under `site/`, built with Astro and Starlight, deployed to GitHub Pages.

The split follows the headings that were already there; nothing was rewritten. What changed is
that each section became addressable, the sidebar shows what exists without scrolling, and
Pagefind indexes the lot — searching 1177 lines of Markdown on GitHub was the only way to find
anything before.

`docs/` is gone rather than kept alongside. Two copies of an operations guide is precisely the
drift `CLAUDE.md` warns about, and the one nobody edits is the one everybody finds.

### Notes for later

- Links between pages are relative paths to the other `.md` file. Astro resolves those at build
  time, which is what survives the `/gh-spot-docker-runners` base path; a hand-written
  `/guides/gpus/` works on a dev server and 404s in production.
- `scripts/check-site-links.py` checks the built HTML against the pages actually generated,
  because a rename turns inbound links into 404s that the build is perfectly happy with. It
  reports 711 links today, and it fails when one breaks — checked by breaking one.
- Every inbound reference moved with it, including the ones that ship: `postinst` and
  `packaging/deb/config.toml` print a URL at operators, and the wizard and the dashboard's
  setup screen both name the authentication guide. A released `.deb` still points at the old
  blob URL; nothing can be done about the ones already out.
- Frontmatter values are quoted. "How runners are kept: `pm`" contains a colon, and an
  unquoted YAML title fails the build with a message about indentation.

## 2026-08-31 — the site, reorganised by domain and cut back

Two passes over the site from the previous entry.

**Grouped by domain rather than by document.** The first cut mirrored `operations.md`'s
headings, which is the order things were written in, not the order anybody needs them. Pages
now sit under the thing they are about: `guides/pools/`, `guides/host/`, `guides/operate/`. A
setting belongs to whichever owns it — `max_runners` bounds one pool, `max_containers` bounds
the machine, and they were on the same page.

Three pages split along that line:

- `images.md` → labels and routing (a pool concern) and building images (a host one).
- `capacity.md` → host ceilings, and `priority`, which is a pool's share of them.
- `day-to-day.md` → monitoring, the dashboard, and the API. At 1419 words it was the largest
  page on the site and covered three unrelated jobs.

**Cut back.** Paragraphs of 4+ lines went from 32 to 8, of which 3 are ADRs — decision records
argue, and that is what they are for. Enumerations became bullets, comparisons became tables,
and troubleshooting opens with a symptom/cause/fix table instead of fifteen bold headings.

### Notes for later

- Splitting pages breaks in-page anchors silently: `install.md` linked `[Configure](#configure)`
  from when both lived in one file, which renders as `href="#configure"` and so slips past the
  link checker, which only follows `href="/gh-spot-docker-runners..."`. Anchors are now checked
  against the headings of the page they are on.
- Moving a page deeper breaks its own outbound `../` links, and the first repair pass left them
  because the resolved path did not exist so it declined to guess. Resolving by filename fixed
  it; the check that every relative `.md` link resolves on disk is what caught it.
- `gpus.md` explained subset matching in full, which is now `pools/labels.md`'s job. Grouping by
  domain surfaces that kind of duplication — it was invisible while both were "a guide".

## 2026-08-31 — the analogy comes out

Every "the way php-fpm does it" is gone from the site, the configuration files, the Ansible
role, the source docstrings and the tests. Thirty-odd of them.

The analogy only ever helped readers who already knew php-fpm; for everyone else it explained
one unfamiliar thing with another, and it made the design read as derivative rather than
reasoned. Each one was replaced by what it was standing in for — `max_idle` is "an upper bound
on the warm band", not "`max_spare_servers`" — which is what a reader needed either way.

Left alone: the dated entries above, and `CHANGELOG.md`. One is a record of what was thought at
the time and the other is generated from commit subjects. Editing either would be falsifying a
log to match a later opinion.

## 2026-08-31 — the link checker was checking the wrong links

Splitting troubleshooting and architecture into sub-pages meant extending the link checker to
follow relative hrefs, and that immediately reported **22 broken links** on a site that had
been reporting zero.

The zero was true and useless. The checker only followed `href="/gh-spot-docker-runners/..."`,
which is what Starlight generates for its own sidebar and breadcrumbs — never what a page's
prose contains. Every content link had been invisible to it since the site was created.

What it found, once it could see them:

- **Astro does not rewrite `.md` links in prose.** `[Layers](./layers.md)` is emitted verbatim
  and 404s in production. Starlight rewrites its sidebar; it does not touch your paragraphs.
  `site/README.md` had been confidently telling the next person to write links that way.
- **A page is served one level deeper than its file.** `start/install.md` is at
  `/start/install/`, so its sibling is `../configure/` and not `./configure/`. Only `index.md`
  pages have a URL that matches their directory, which is why the index pages happened to work
  and nothing else did.
- A root-absolute `/guides/...` is always wrong under a base path — it works on the dev server
  and 404s in production, which is the worst way for a link to break.

All three shapes now fail the check, verified by planting one of each.

### Notes for later

- The lesson is not "the links were wrong". It is that a green check over the wrong subset
  reads exactly like a green check, and the subset it covered was the one nobody writes by
  hand. Worth asking of any checker: what does it *not* see?
- Moving a page deeper breaks its own outbound relative links, and this is the second time it
  has. The checker is the thing that catches it; nothing about the build will.

## 2026-08-31 — `ghspot runner stop --all`

One runner at a time was the only way to empty a host, which is not a thing anybody does by
hand for a fleet of ten. `--all` retires every runner, `--pool` narrows it, `--force` takes the
busy ones as well.

The interesting part is what it prints afterwards. `min_idle` is a floor the daemon maintains,
so emptying a pool that keeps one warm lasts exactly one poll interval. Without saying so the
command looks broken: you run it, the runners come back, you run it again. It reports how many
are returning and what to do instead — stop the daemon, or set `min_idle = 0` and reload.

### Notes for later

- Concurrent, like shutdown. Each container gets its stop timeout, and ten in sequence is
  minutes of waiting for something an operator ran to get their host back.
- Busy runners are **named**, not silently skipped. A count of "retired 8" that quietly meant
  nine is how somebody concludes the command is unreliable.
- `--pool` without `--all` is refused rather than ignored. It would silently do nothing, which
  is the worst possible answer to a command about stopping things.
## 2026-08-31 — the host is the master

`systemctl stop ghspot` left the fleet running. Containers kept taking jobs, registrations
stayed on GitHub, and nothing enforced `idle_timeout` or `max_job_duration` because the thing
that enforces them had exited. `ghspot pool status` showed them, correctly — they existed.

The old behaviour was deliberate and is now reversed on purpose. Leaving a busy runner alone
protects the build in flight; it also leaves a machine running somebody's CI with nobody
watching it finish, and a registration that outlives the process that made it. A CI run can be
replayed. A fleet nobody owns cannot be reasoned about.

So stop retires everything, busy included, and restart does the same on its way through.

**Reload is the exception**, and the reason it now exists. `SIGHUP` re-reads the configuration
and swaps pools, labels and ceilings into the reconciler in place, touching no runner —
changing a label should be routine, not something scheduled around the builds.

### Notes for later

- Retirement at shutdown is concurrent. Each container gets its `stop_timeout`; in sequence
  that is `TimeoutStopSec` blown on any real fleet, and systemd would `SIGKILL` the daemon
  midway through cleaning up.
- A configuration that no longer parses is **refused**, not fatal. Refusing is recoverable;
  exiting on a typo is a fleet down until somebody notices.
- A stuck container is logged, not raised. The daemon is on its way out, and a non-zero exit
  would have systemd report a failed unit for a container that would not stop.
- The Ansible role's single "Restart ghspot" handler became two. Every configuration task now
  notifies **Reload**; only installing the package restarts, because that is the one case where
  the running process is the thing being replaced. Left as it was, every `ansible-playbook` run
  that touched the config would have destroyed the fleet.
- The reload test calls `_reload()` directly rather than running the loop: a loop that finishes
  retires the fleet, which is precisely what reload exists to avoid and would have hidden what
  the test asserts.

## 2026-08-31 — v0.5.0 shipped with no packages

`gh release upload` failed with **HTTP 422: Cannot upload assets to an immutable release**, and
v0.5.0 has zero assets against v0.4.0's three. The README tells people to take the package from
the latest release; for v0.5.0 there was nothing there.

The ordering had always been wrong and only now had consequences. release-please **published**
the release, the packages then took minutes to build, and the upload came last. Publishing is
what makes a release immutable, so by the time the `.deb`s existed the release was sealed.

Draft first, published last. release-please leaves a draft, the packages and the notes go onto
it, and `gh release edit --draft=false --latest` seals it with everything already attached.

A build that fails now leaves a **draft** — recoverable, and invisible to anyone browsing
releases — rather than a published release nobody can install.

### Notes for later

- Nothing changed in the repository to cause this. Immutable releases became the behaviour
  underneath a workflow that had been publishing-then-uploading since it was written, and the
  first symptom was a release that looked fine and contained nothing.
- v0.5.0 itself cannot be repaired in place, for the same reason it broke: assets cannot be
  added to a published immutable release. It needs deleting and recreating from its tag, or
  superseding.

## 2026-08-31 — the draft has no tag

Making the release a draft fixed the immutability failure and broke the build in a new way: a
**draft release does not create its tag**. The tag appears when the release is published, which
is now the last thing the workflow does — so the build job's `ref: refs/tags/v0.5.1` had
nothing to check out, and both architectures failed.

The fix is `ref: ${{ github.sha }}`. That is the commit release-please released from, so it is
the same tree the tag will point at once it exists. The original comment — "a release builds
the tag, not the branch head, so the package cannot contain anything the release does not" —
still holds; the commit is simply the earlier name for the same thing.

The new ordering proved itself in the failure: v0.5.1 was left as an **empty draft**, invisible
to anyone browsing releases, rather than the published-and-useless state v0.5.0 ended in. That
is exactly what the draft was for, and it made the recovery a matter of attaching packages to
something that already existed.

### Notes for later

- v0.5.1 was completed by hand: `workflow_dispatch` at `version=0.5.1` built both packages
  (dispatch skips release-please, so it checks out `github.ref` and never touched the tag),
  then upload, notes, `--draft=false --latest`. Publishing created the tag.
- The published `.deb` was run through `packaging/deb/verify.sh` before this was called done.
  Thirteen checks on a clean Ubuntu 26.04, including the dashboard and the runner sources.
- v0.5.0 stays as it is: published, immutable, and empty. It cannot be repaired, and v0.5.1
  supersedes it.

## 2026-08-31 — the draft experiment, reverted

Making the release a draft to escape immutability was the wrong fix, and it did more damage
than the problem it solved.

release-please anchors each run on the **tag** it created last time. A draft release has no
tag — the tag appears at publish, which the draft ordering made the last step. So the next run
found no anchor and regenerated the changelog from the entire history. #76 proposed v0.6.0
containing every commit ever made; merged, that put 240 lines into a section whose real content
was one fix. #78 then did it again, proposing 247 lines for a release with **zero** commits.

The timing says it plainly: #78 was generated at 16:13:49Z, and v0.6.0's tag appeared at
16:15:44Z. Two minutes too late, every time.

So: back to the flow that shipped v0.2.0 through v0.4.0 with three assets each — release-please
publishes and tags atomically, then the packages upload. Immutable releases is turned off in
the repository settings, which is what made that flow fail at v0.5.0 in the first place.

### Notes for later

- The lesson is not "drafts are bad". It is that release-please's state lives in the tag, and
  anything that defers tag creation breaks the *next* run rather than the current one — a
  failure that appears one release later than its cause, which is why it was not obvious.
- Two releases are permanently wrong and cannot be repaired: v0.5.0 has no assets, and v0.6.0's
  release notes carry the whole history. Both are published and immutable. The CHANGELOG in the
  repository is corrected; the release pages are not.
- v0.5.1 and v0.6.0 both have their three assets, so the packages are fine throughout. Only the
  notes and one release's assets are wrong.

## 2026-09-01 — the diagrams were never rendering

`reference/architecture/index.md` and `lifecycle.md` have carried ```mermaid fences since the
site was created. Starlight does not render mermaid on its own and nothing had been added, so
both showed as plain code blocks. Nobody noticed, which is what happens to a page written once
and read never.

`astro-mermaid`, ahead of Starlight in the integration list because it rewrites the fences
before Starlight sees them.

### Notes for later

- Adding it incrementally left `package.json` and `package-lock.json` out of sync, and `npm ci`
  — which is what CI runs, and which refuses rather than resolving — failed on
  `Missing: @emnapi/runtime from lock file`, one of sharp's optional platform packages. The
  lockfile is regenerated from scratch.
- The mistake underneath: verifying with `npm install && npm run build` when CI runs `npm ci`.
  The forgiving command papers over exactly the state the strict one rejects. Check with the
  command that will actually run.
- Versions pinned exactly, like everything else in that file. A caret would let a minor bump
  change the docs build with no commit saying so.

## 2026-09-01 — the README stops being the documentation

193 lines down to 59. The site covers install, configure, run, tune and troubleshoot; a README
that repeats it is a second copy nobody updates, which is the drift the whole site was meant to
end.

What is left is what a README is for: badges, what this is, why it exists, and enough to get a
host running. Everything else moved to the site's landing page rather than being deleted — the
full differentiator list, the architecture diagram, the requirements, the command table, the
layer table.

The diagram went too. It is the best single explanation of the shape, which is an argument for
putting it where somebody reading about the shape will be — the landing page — not for keeping
a copy in front of people who came to install something.

The `Status: alpha` line is gone. Six releases with packages, a documentation site and a
verified install path is not alpha, and the line was telling people otherwise.

### Notes for later

- Badges are for `python.yml`, `packaging.yml` and `docs.yml`, plus release and licence. Each
  URL was checked for a 200 rather than assumed — a badge that 404s renders as a broken image
  and looks worse than none.

## 2026-09-01 — a documentation tree per release

The site now carries `starlight-versions`. The root tracks `main` and is labelled
**Unreleased**; each release is snapshotted into its own tree — `0.6/` — with a picker in the
header. 71 pages where there were 36.

Root-tracks-main rather than root-shows-latest, deliberately. A page is written once and
snapshots are archives; the alternative makes every edit a question about which trees get it.
The cost lands on the reader instead: somebody on the released `.deb` has to notice they want
`/0.6/`. With 63 doc files changed in a single day, keeping the writing side cheap was worth
more than the reader's extra click.

### Notes for later

- **Snapshot at the release commit, not afterwards.** The plugin copies the working tree, so a
  snapshot taken later documents features the release does not have. The 0.6 tree was corrected
  from the tag for exactly this: its `index.mdx` had already gained the content moved out of the
  README, which v0.6.0 never shipped.
- The plugin rewrites what it copies — it adds a routing `slug:` to every page's frontmatter,
  and its serialiser normalises bullet markers, thematic breaks and underscore escapes. Content
  compared page by page against the tag: 33 identical, 2 differing only in serialisation.
- Restoring a page by hand needs that `slug:` too. Without it Astro slugified the directory and
  served the page at `/06/`, which broke its every relative link. The link checker found it; a
  build and a glance at the picker would not have.
- Archived trees are archives. Editing one to fix a typo is a decision to maintain two copies,
  and is only worth it when the fix matters to somebody running that release.

## 2026-09-01 — fetching the right package without reading the release page

The install instructions said "take the package for your architecture from the latest release",
which is a browser task in the middle of a shell session. Both the README and the install page
now carry a command that resolves it.

```bash
ARCH="$(dpkg --print-architecture)"
curl -fsSL .../releases/latest \
  | grep -o '"browser_download_url": *"[^"]*"' | cut -d'"' -f4 \
  | grep -E "_${ARCH}\.deb$|/SHA256SUMS$" | xargs -n1 curl -fsSLO
sha256sum --ignore-missing -c SHA256SUMS
```

The filename carries the version, so `releases/latest/download/<name>` cannot be used and the
API call is unavoidable.

### Notes for later

- The first version scoped the grep to `https://[^"]*` and pulled **every URL in the release
  notes** — a changelog full of commit links, fed to `xargs curl`. It failed loudly with forty
  lines of `curl: (3) bad range in URL`. Scoping to `browser_download_url` is the fix, and it is
  the reason the snippet looks more careful than it needs to.
- `--ignore-missing` because `SHA256SUMS` lists both architectures and a host has one.
- The wget variant was written second and tested separately. Writing an untested variant beside
  a tested one is how half a snippet ships broken.

## 2026-09-01 — the README addresses an operator

It opened with "managed by a single Python daemon", which answers a question nobody
installing this is asking. What somebody deploying it wants to know is what lands on their
machine and what it will and will not do there.

So: a `.deb`, a systemd unit, one `config.toml` — then the five properties that decide whether
this fits an estate. No control plane and no account. No inbound ports, so it works behind NAT.
No credential in a container. Bounded by the host, not just by the pool. Bundles its own
interpreter, so a distribution upgrade cannot break it.

Python is still there in the badge and the source. It is not the headline, because the runtime
is the least interesting thing about a package that carries its own.

### Notes for later

- The `runs-on` example still asked for four labels. Every other example had moved to
  `[self-hosted, ubuntu-24.04]`; the README had not, because that change went through
  `select-runner.yml` and the pages describing it, and the README was not on the list.
- The link table points at troubleshooting and host capacity now, rather than architecture
  first. An operator reaches for the README when something is wrong, not when they want the
  layering explained.
- Every link checked for a 200 before committing. The site is versioned now, so a path that
  moved into `/0.6/` would still resolve at the root only by accident.
- The credential is described as **scoped to the repositories it will serve**, everywhere it is
  summarised. `config.example.toml` already said "scoped to the repositories below" and the
  authentication guide already said "Only select repositories"; the README and the landing page
  named the two permissions and stopped there, which reads as an account-wide grant. The scope
  is the more important half — a token with the right permissions over every repository you can
  reach is a worse credential than one with the same permissions over two.
## 2026-09-01 — two labels instead of four

`select-runner.yml` asked for `["self-hosted","linux","x64","home-vm"]`. It now asks for
`["self-hosted","ubuntu-24.04"]`.

`linux` and `x64` are implied by naming the OS, and `home-vm` was a label this repository's CI
required of every pool that wanted to serve it. Asking for it is what left jobs queued against
a runner nobody had built — the pool the wizard writes carries the OS label and nothing else,
so it could not take this project's own work until somebody noticed and added a label by hand.

One line, because every workflow's `runs-on` comes from that one output. The rule living in one
place is what made this a single edit rather than eight.

### Notes for later

- Carrying a label costs nothing; *asking* for one narrows where a job can land. The docs said
  the first half and left the second implied, which is the half that bites.
- The live runner carries `self-hosted,linux,x64,ubuntu-24.04,home-vm`, so the narrower request
  is a subset of what already exists — the change needs no fleet rebuild.
- The tests keep `home-vm`. There it is an arbitrary spare label proving the subset rule, not a
  convention being documented.

## 2026-09-01 — CI back on GitHub-hosted, the fleet opt-in

Every workflow's `runs-on` comes from `select-runner.yml`, and it now returns GitHub-hosted
unless the repository variable `USE_SELF_HOSTED` is `true`.

A variable rather than a code change, because the reason to move CI off the fleet is never
planned: a migration, a host reboot, a label that stopped matching. Making that a pull request
means the build stays broken until somebody writes one. Unset, nothing waits on a machine that
may be down — and a job with no matching runner does not fail, it queues for 24 hours with
nothing in the log to say why.

The fork check still comes first and the variable cannot override it. Turning the fleet on is a
decision about your own branches, never about a stranger's.

### Notes for later

- One line of routing, one place, because every workflow reads that output. The same property
  that made the label change a single edit.
- The page describing all this documented a `workflow_dispatch` input called `force_hosted` as
  the manual escape hatch. **It does not exist in any workflow** and, from the git history,
  never did. The table is now what the code actually does — and the escape hatch it promised is
  real this time, as the variable.

## 2026-09-01 — Run workflow, and where

Every workflow now takes `workflow_dispatch`. The five that route through `select-runner.yml`
also take a **Where to run it** choice — `auto`, `hosted`, `self-hosted` — passed through to
the selector, which grew a `workflow_call` input for it.

That answers two questions the repository variable cannot: "is the fleet actually working?" and
"the fleet is wedged, get me a green build" — neither of which should mean changing a setting
for everybody else.

Docs, Packaging and Runner images take the dispatch with **no** choice. Every job in them is
pinned to GitHub-hosted for a reason written at the job — bind-mounts that resolve on the host,
a privileged systemd container, a build that would clobber the operational image tag — so
offering the fleet would only offer a broken run.

### Notes for later

- The fork check is first and returns immediately, so neither the variable nor the dispatch
  input can reach past it. All twelve combinations of fork × choice × variable were run against
  the actual script: a fork asking for `self-hosted` still lands on GitHub-hosted.
- `inputs.runner || 'auto'` at each call site, because `inputs` is null on a `push` and a bare
  `inputs.runner` would pass an empty string into the selector's `case`.
- Adding a choice to a workflow whose jobs are pinned would have been the easy consistency, and
  wrong. The comment at each of those three says so, next to the `workflow_dispatch` rather
  than buried at the job.
## 2026-09-01 — the landing page stops being a document

It had become a dumping ground: a system diagram, six feature cards, a requirements list, the
full command table, the layer table, and a security callout. Everything that came out of the
README went onto it, and none of it was arranged for somebody arriving.

A landing page's job is to route. It is now three groups of link cards — start here, once it is
running, when something is wrong — over one paragraph saying what you deploy.

Everything else moved to where it is looked up rather than stumbled upon:

| Was on the landing page | Now |
|---|---|
| The host/GitHub diagram | `reference/architecture/` |
| The command table | `reference/commands/`, a page that did not exist |
| Requirements | `start/requirements/`, which was three thin bullets |
| The layer table | Dropped — `reference/architecture/layers/` already has it, better |

### Notes for later

- `start/requirements.md` said "a fine-grained GitHub personal access token" and stopped. It
  now carries the scope, the two permissions and why each, and the note that neither credential
  reaches a container. The landing page had the better version of a page that already existed,
  which is what happens when content lands where it fits rather than where it belongs.
- The commands table had no home at all. It existed only on the landing page, so anybody
  looking for "what can this thing do" had to find it there or not at all.
- **The depth trap caught me again**, on both new pages: a page is served one level deeper than
  its file, so `./authentication/` from `start/requirements.md` resolves to
  `/start/requirements/authentication/`. Four broken links, found by the checker, none visible
  in the build. It is written down in `site/README.md` and I still wrote them from the file's
  position rather than the page's.

## 2026-09-01 — the disk was the one thing nothing watched

`doctor` checked the credential, Docker, the image, the socket, GPUs and the token. Capacity
bounded containers, CPU and memory. Nothing anywhere looked at free space — and a disk filled
by build caches and pulled images is the failure that actually takes a runner host down. Every
launch then fails with an error naming neither the disk nor the cause.

Two halves, following the shape that was already there:

- `[capacity].disk_high_water`, alongside the CPU and memory marks. At or above it, launches
  are deferred.
- A `doctor` check, which reports above 90% even with no mark configured — because at that
  point nothing else will.

### Notes for later

- Measured on Docker's `DockerRootDir`, not `/`. A host that gave Docker its own volume has two
  filesystems and only one of them fills. Verified against `df`: 66.1% against its 67%.
- Counted against what is *usable*, not the raw total. The blocks reserved for root are not
  space a container can have, and counting them makes a disk look emptier than it is at exactly
  the moment that matters.
- Unreadable never blocks, like the other two probes. A careful mechanism that stops the fleet
  when its own probe breaks is worse than no mechanism.
- The other marks watch what jobs *use*; this one watches what they *leave*. Housekeeping
  reclaims that on a schedule, which does not help a host filling between sweeps.
- The Ansible template renders `ghspot_capacity` generically, so the new key needed no template
  change — and would equally have passed a *misspelled* key straight through to be ignored.
  The render check now asserts every ceiling arrives.
