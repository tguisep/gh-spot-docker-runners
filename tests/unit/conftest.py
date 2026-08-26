"""Factories for building domain objects in tests without ceremony."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ghspot.domain.model.job import QueuedJob
from ghspot.domain.model.labels import LabelSet
from ghspot.domain.model.pool import PoolSpec, RunnerPool
from ghspot.domain.model.runner import Runner, RunnerId, RunnerState
from ghspot.domain.model.target import RepositoryTarget

T0 = datetime(2026, 8, 26, 12, 0, 0, tzinfo=UTC)
REPO = RepositoryTarget("tguisep", "gh-spot-docker-runners")
LABELS = LabelSet.of("self-hosted", "linux", "x64", "home-vm")


def at(**offset: float) -> datetime:
    """A time relative to the fixed clock origin: ``at(minutes=5)``."""
    return T0 + timedelta(**offset)


def make_runner(
    name: str = "r1",
    *,
    state: RunnerState = RunnerState.PENDING,
    since: datetime | None = None,
    pool: str = "default",
    labels: LabelSet = LABELS,
    job_id: int | None = None,
) -> Runner:
    """A runner parked directly in ``state``, bypassing the transitions."""
    runner = Runner(
        id=RunnerId(name),
        name=f"ghspot-{pool}-{name}",
        pool=pool,
        repository=REPO,
        labels=labels,
        created_at=T0,
        state=state,
        state_changed_at=since or T0,
        github_runner_id=1 if state is not RunnerState.PENDING else None,
        current_job_id=job_id,
    )
    runner.pull_events()
    return runner


def make_spec(**overrides: object) -> PoolSpec:
    defaults: dict[str, object] = {
        "name": "default",
        "repository": REPO,
        "labels": LABELS,
        "min_idle": 0,
        "max_runners": 4,
        "idle_timeout": timedelta(minutes=10),
        "max_job_duration": timedelta(hours=2),
        "max_launch_per_tick": 4,
    }
    defaults.update(overrides)
    return PoolSpec(**defaults)  # type: ignore[arg-type]


def make_pool(*runners: Runner, **spec_overrides: object) -> RunnerPool:
    return RunnerPool.of(make_spec(**spec_overrides), runners)


def make_job(
    job_id: int = 1,
    *,
    labels: LabelSet | None = None,
    repository: RepositoryTarget = REPO,
    queued_at: datetime | None = None,
) -> QueuedJob:
    return QueuedJob(
        id=job_id,
        run_id=1000 + job_id,
        repository=repository,
        labels=labels or LabelSet.of("self-hosted", "linux"),
        queued_at=queued_at or T0,
    )


@pytest.fixture
def now() -> datetime:
    return T0
