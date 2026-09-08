"""The read side of the queue: what a reader with no forge client can see.

The bug these cover is the one an operator hit first — a `queued` column that read zero
however much CI was piled up, because every reader of it worked from the projection and the
projection had never been given the number.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from ghspot.application.queries.queue import GetQueue
from ghspot.application.queries.views import GetPoolStatus
from ghspot.domain.model.queue import (
    HostPressure,
    PoolPressure,
    QueueEntry,
    QueueSnapshot,
    WaitReason,
)
from tests.fakes.adapters import FakeClock, InMemoryQueueSnapshots, InMemoryRunnerRepository
from tests.unit.conftest import REPO, T0, at, make_spec


def entry(job_id: int = 1, *, pool: str = "default", **overrides: object) -> QueueEntry:
    defaults: dict[str, object] = {
        "job_id": job_id,
        "run_id": 1000 + job_id,
        "repository": str(REPO),
        "workflow": "ci",
        "job_name": "test",
        "labels": ("self-hosted", "linux"),
        "queued_at": T0,
        "pool": pool,
        "priority": 1,
        "position": 1,
        "reason": WaitReason.POOL_AT_CAPACITY,
        "detail": "pool is at max_runners=4 with 4 up",
    }
    defaults.update(overrides)
    return QueueEntry(**defaults)  # type: ignore[arg-type]


def pressure(pool: str = "default", **overrides: object) -> PoolPressure:
    defaults: dict[str, object] = {
        "pool": pool,
        "repository": str(REPO),
        "priority": 1,
        "queued": 1,
        "available": 0,
        "active": 4,
        "max_runners": 4,
        "launching": 0,
        "wanted": 1,
        "blocked_by": "pool is at max_runners=4 with 4 up",
    }
    defaults.update(overrides)
    return PoolPressure(**defaults)  # type: ignore[arg-type]


async def stored(snapshot: QueueSnapshot) -> InMemoryQueueSnapshots:
    store = InMemoryQueueSnapshots()
    await store.record(snapshot)
    return store


# ---------------------------------------------------------------- GetQueue


@pytest.mark.anyio
async def test_no_snapshot_reads_as_absent_not_as_an_empty_queue() -> None:
    view = await GetQueue(InMemoryQueueSnapshots(), FakeClock(T0))()

    assert not view.has_snapshot
    assert view.taken_at is None
    assert view.total == 0


@pytest.mark.anyio
async def test_the_age_of_the_reading_is_part_of_the_answer() -> None:
    store = await stored(QueueSnapshot(taken_at=T0, entries=(entry(),)))

    view = await GetQueue(store, FakeClock(at(seconds=6)), timedelta(seconds=15))()

    assert view.age_seconds == pytest.approx(6.0)
    assert not view.stale


@pytest.mark.anyio
async def test_a_reading_older_than_two_intervals_is_called_stale() -> None:
    """A stopped daemon leaves a correct-looking, empty, permanent queue behind it."""
    store = await stored(QueueSnapshot(taken_at=T0))

    view = await GetQueue(store, FakeClock(at(seconds=31)), timedelta(seconds=15))()

    assert view.stale


@pytest.mark.anyio
async def test_waiting_time_grows_with_the_reader_s_clock_not_the_snapshot_s() -> None:
    store = await stored(QueueSnapshot(taken_at=T0, entries=(entry(queued_at=T0),)))

    view = await GetQueue(store, FakeClock(at(minutes=5)))()

    assert view.entries[0].waiting_seconds == pytest.approx(300.0)
    assert view.longest_wait_seconds == pytest.approx(300.0)


@pytest.mark.anyio
async def test_only_a_wait_on_the_fleet_is_counted_as_delayed() -> None:
    snapshot = QueueSnapshot(
        taken_at=T0,
        entries=(
            entry(1, reason=WaitReason.ASSIGNABLE),
            entry(2, reason=WaitReason.STARTING),
            entry(3, reason=WaitReason.POOL_AT_CAPACITY),
        ),
    )

    view = await GetQueue(await stored(snapshot), FakeClock(T0))()

    assert view.total == 3
    assert view.delayed == 1


@pytest.mark.anyio
async def test_filtering_by_pool_keeps_the_jobs_no_pool_serves() -> None:
    """ "No pool serves this" is never the answer to a question about one pool, and hiding it
    behind a filter is exactly how it goes unnoticed for a week."""
    snapshot = QueueSnapshot(
        taken_at=T0,
        entries=(
            entry(1, pool="default"),
            entry(2, pool="gpu"),
            entry(3, pool="", reason=WaitReason.NO_POOL, position=0),
        ),
        pools=(pressure("default"), pressure("gpu")),
    )

    view = await GetQueue(await stored(snapshot), FakeClock(T0))("default")

    assert [item.job_id for item in view.entries] == [1, 3]
    assert [item.pool for item in view.pools] == ["default"]


@pytest.mark.anyio
async def test_the_host_reading_is_carried_through_with_its_limits() -> None:
    snapshot = QueueSnapshot(
        taken_at=T0,
        host=HostPressure(memory_percent=91.0, memory_high_water=90.0, holding="held"),
    )

    view = await GetQueue(await stored(snapshot), FakeClock(T0))()

    assert view.host.memory_percent == 91.0
    assert view.host.memory_high_water == 90.0
    assert view.host.holding == "held"


@pytest.mark.anyio
async def test_a_job_title_falls_back_to_whatever_github_named() -> None:
    snapshot = QueueSnapshot(
        taken_at=T0,
        entries=(
            entry(1, workflow="ci", job_name="test"),
            entry(2, workflow="", job_name="build"),
            entry(3, workflow="", job_name=""),
        ),
    )

    view = await GetQueue(await stored(snapshot), FakeClock(T0))()

    assert [item.title for item in view.entries] == ["ci / test", "build", "job 3"]


# ---------------------------------------------------------------- GetPoolStatus


@pytest.mark.anyio
async def test_the_pools_table_takes_its_queued_count_from_the_snapshot() -> None:
    """The defect itself: without the store this returned zero for every pool, always."""
    snapshot = QueueSnapshot(
        taken_at=T0,
        entries=(entry(1), entry(2, queued_at=at(minutes=-3))),
        pools=(pressure(queued=2),),
    )
    query = GetPoolStatus(InMemoryRunnerRepository(), FakeClock(T0), await stored(snapshot))

    views = await query([make_spec()])

    assert views[0].queued_jobs == 2
    assert views[0].oldest_wait_seconds == pytest.approx(180.0)


@pytest.mark.anyio
async def test_a_pool_with_nothing_queued_reads_zero_rather_than_missing() -> None:
    snapshot = QueueSnapshot(taken_at=T0, pools=(pressure(queued=0),))
    query = GetPoolStatus(InMemoryRunnerRepository(), FakeClock(T0), await stored(snapshot))

    views = await query([make_spec()])

    assert views[0].queued_jobs == 0
    assert views[0].oldest_wait_seconds == 0.0


@pytest.mark.anyio
async def test_pool_status_still_answers_with_no_queue_store() -> None:
    """What a database written by an older version looks like on its first read."""
    query = GetPoolStatus(InMemoryRunnerRepository(), FakeClock(T0))

    views = await query([make_spec()])

    assert views[0].queued_jobs == 0


@pytest.mark.anyio
async def test_an_explicit_count_wins_over_the_snapshot() -> None:
    snapshot = QueueSnapshot(taken_at=T0, entries=(entry(1),), pools=(pressure(queued=1),))
    query = GetPoolStatus(InMemoryRunnerRepository(), FakeClock(T0), await stored(snapshot))

    views = await query([make_spec()], {"default": 9})

    assert views[0].queued_jobs == 9
