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

## Documentation

Per `CLAUDE.md`: any change to structure, features or deployment updates `CONTEXT.md` and the
relevant page under `docs/`. A decision that would be expensive to reverse gets an ADR, and
the useful part of an ADR is the alternatives that were rejected.
