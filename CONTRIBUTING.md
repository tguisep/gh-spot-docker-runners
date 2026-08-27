# Contributing

## Setting up

```bash
git clone https://github.com/tguisep/gh-spot-docker-runners.git
cd gh-spot-docker-runners
uv sync --all-extras
uv run pre-commit install
```

## The checks

```bash
uv run ruff format .          # format
uv run ruff check .           # lint
uv run mypy                   # types, strict
uv run pytest                 # unit and integration
uv run pytest -m docker       # needs a Docker daemon
uv run pytest -m e2e          # needs a real token and a throwaway repository
```

CI runs all of these except `e2e`.

## The one rule that matters

**`domain` and `application` import nothing concrete.** No `docker`, no `httpx`, no
`sqlite3`, no `fastapi`. Adapters implement the ports in `domain/ports/`; the composition
root in `composition.py` is the only module that knows which adapter is which.

`tests/unit/test_architecture.py` parses every module and enforces this. If it fails, the fix
is almost never to change the test.

This is not architecture for its own sake — it is why the whole reconciliation loop,
including crash recovery, is tested without a Docker daemon or a network.

## Where a change belongs

| If it is... | It goes in... |
|---|---|
| A rule about when runners should exist | `domain/policy/scaling.py` |
| A rule about what a runner may do next | `domain/model/runner.py` |
| Orchestration across several ports | `application/` |
| Talking to GitHub, Docker, or a file | `infrastructure/` |
| A command or an endpoint | `interfaces/` |

If a CLI command starts making decisions, those decisions belong one layer down where they
can be tested without a terminal.

## Tests

- **Domain and policy**: plain objects, no mocks. A new branch in `plan_scaling` should be a
  new row in the existing table.
- **Application**: the fakes in `tests/fakes/adapters.py`. `FakeForge.fail_on` and
  `FakeBackend.fail_on` put the daemon down at a chosen instant — that is how the crash-
  recovery cases are written.
- **Infrastructure**: `respx` for HTTP; `@pytest.mark.docker` for a real daemon.

Name tests after the behaviour, not the method: `test_a_hung_job_is_killed`, not
`test_plan_scaling_3`.

## Branches and commits

One branch per unit of work, cut from `main`, named `<type>/<short-kebab-summary>` using the
same types as commits: `feat/`, `fix/`, `refactor/`, `docs/`, `ci/`, `chore/`, `test/`,
`perf/`. Never commit to `main` directly.

Conventional commits: `<type>(<scope>): <imperative summary>`, subject under 72 characters,
no trailing period. Within a branch, one commit per domain — a change touching the API, the
CLI and the docs is three commits, not one blob and not one per file.

Put the *why* in the commit body when it is not obvious from the diff. The diff shows what
changed; the body is the only place the reasoning survives.

## Pull requests

One sentence of **What**, a table of changes by module, bullets for anything a reviewer
cannot see in the diff, one line of **Verification**. No narration of the journey — that is
what the commit body is for.

## What CI runs, and when

One workflow per concern, each with its own `paths:` filter, so a change only starts the work
it can affect:

| Workflow | Runs when |
|---|---|
| `python.yml` | `src/`, `tests/`, `pyproject.toml`, `uv.lock` |
| `runner-images.yml` | `images/runner/` |
| `packaging.yml` | `packaging/`, `deploy/`, `src/`, `docs/operations.md` |
| `upstream-toolset.yml` | `images/runner/`, and weekly |
| `release.yml` | pushes to `main` |

Each workflow also lists its own file, so a change to the gating is validated by the thing it
gates.

Two entries are deliberate rather than obvious:

- **`src/` starts packaging**, because the `.deb` embeds the application.
- **`docs/operations.md` starts packaging**, because `verify-source-install.sh` runs the
  commands that file tells people to run. Editing them without running them is exactly how
  they were wrong three times.

`select-runner.yml` is a reusable workflow rather than a job copied into each one — a rule
about where jobs may run is a rule that will be wrong in one of four copies. Note that
**GitHub Actions does not support YAML anchors**, so the path lists are spelled out under
both `push` and `pull_request` rather than shared.

### Which jobs run on the fleet

Most of them. The exceptions are deliberate, and each workflow says why at the job:

| Job | Runs on | Why |
|---|---|---|
| lint, test | Fleet | |
| upstream comparison | Fleet | |
| release PR, publish | Fleet | |
| `select-runner` | **Hosted** | It decides where everything else runs. On the fleet, a fleet that is down could not tell anyone to fall back off it |
| `packaging` | **Hosted** | Both jobs bind-mount the workspace, which silently mounts an empty directory on a self-hosted runner |
| `runner-images` | **Hosted** | Throwaway images would fill the host, and rhel-10 needs a CPU feature a generic VM does not expose |
| release `build` | **Hosted** | Builds the package, so the same bind-mount problem — and it needs an arm64 runner |

The bind-mount one is worth understanding before trying to "fix" it. On a self-hosted runner
the Docker client sits inside a container talking to the **host's** daemon, so a path like
`$PWD` is resolved against the host, where the workspace does not exist. Docker creates an
empty directory there and mounts that, and the build produces a package from nothing without
failing. Moving those jobs would require the runner's work directory to be a host bind mount
at an identical path — putting every job's files on the host disk.

### If you turn on branch protection

Require the checks from workflows that always run. A workflow filtered out by `paths` does
not report at all — neither success nor failure — so requiring it would leave a
documentation-only pull request waiting forever for a check that was correctly never started.

## Dependencies

Dependabot opens grouped pull requests weekly for Python packages, actions and base images.
Minor and patch bumps arrive together; a major arrives alone, because a major is a decision
rather than a chore.

The **runner tarball is not covered** — its version and checksum are pinned in each
Dockerfile and come from a GitHub release rather than a package manager.
`images/runner/sync-toolset.sh` watches that side, and CI asks weekly. GitHub requires
runners to be no more than 30 days behind.

## Releases

Releases are cut by [release-please](https://github.com/googleapis/release-please), driven by
the commit messages. There is nothing to tag by hand.

**Every merge to `main`** updates an open pull request titled `chore: release vX.Y.Z`, which
accumulates the changelog and the version bump. **Merging that pull request** is what cuts
the release: it creates the tag, publishes the GitHub release, and attaches the amd64 and
arm64 `.deb` packages.

So a release is two steps, not one. Merging a feature does not publish anything; it queues
the change for the next release.

### What your commit type does to the version

| Commit | Effect while pre-1.0 |
|---|---|
| `fix:` | Patch — `0.1.0` → `0.1.1` |
| `feat:` | Minor — `0.1.0` → `0.2.0` |
| `feat!:` or a `BREAKING CHANGE:` footer | Minor, not major, until 1.0 |
| `docs:`, `ci:`, `refactor:`, `perf:` | Appear in the changelog, no bump on their own |
| `chore:`, `test:` | Hidden from the changelog |

This is the practical reason the conventional-commit rules above are not decoration: the type
you write chooses the version number.

### The version lives in two files

`pyproject.toml` and `src/ghspot/__init__.py`. release-please updates both — the second
through the `x-release-please-version` marker on that line. Removing the marker breaks
nothing loudly; it silently leaves `__version__` behind while `pyproject.toml` moves on, so
`tests/unit/test_version.py` asserts both the agreement and the marker.

### Trying it without releasing

```bash
npx release-please release-pr \
  --token="$(gh auth token)" \
  --repo-url=tguisep/gh-spot-docker-runners \
  --config-file=release-please-config.json \
  --manifest-file=.release-please-manifest.json \
  --target-branch=main --dry-run
```

To build the packages without releasing anything, run the **Release** workflow manually from
the Actions tab; the `version` input only names what the package will claim.

## Documentation

Per `CLAUDE.md`: any change to structure, features or deployment updates `CONTEXT.md` and the
relevant page under `docs/`. A decision that would be expensive to reverse gets an ADR, and
the useful part of an ADR is the alternatives that were rejected.
