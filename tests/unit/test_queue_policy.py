"""Why a job is still queued, case by case.

Each of these is a support question — "the runner box is idle, why is my build waiting?" —
turned into a table row. The point of the policy is that the answer is always the *specific*
limit with its configured value in it, so every assertion here checks the sentence and not
just the category.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta

import pytest

from ghspot.domain.model.job import QueuedJob
from ghspot.domain.model.labels import LabelSet
from ghspot.domain.model.queue import QueueSnapshot, WaitReason
from ghspot.domain.model.target import RepositoryTarget
from ghspot.domain.policy.admission import Admission, CapacityLimits
from ghspot.domain.policy.queue import PoolStanding, explain_queue
from ghspot.domain.ports.backend import HostLoad
from tests.unit.conftest import REPO, T0, at, make_job, make_spec


def standing(available: int = 0, active: int = 0, wanted: int = 0, **spec: object) -> PoolStanding:
    return PoolStanding(spec=make_spec(**spec), available=available, active=active, wanted=wanted)


def explain(
    standings: Sequence[PoolStanding],
    jobs: Sequence[QueuedJob],
    *,
    admission: Admission | None = None,
    load: HostLoad | None = None,
    limits: CapacityLimits | None = None,
    unreadable: Sequence[str] = (),
) -> QueueSnapshot:
    """One tick's worth of inputs, read thirty seconds after the jobs were queued."""
    return explain_queue(
        standings,
        {REPO: jobs},
        admission or Admission(granted={s.spec.name: 0 for s in standings}),
        load or HostLoad(),
        limits or CapacityLimits(),
        at(seconds=30),
        unreadable=unreadable,
    )


# ---------------------------------------------------------------- the happy cases


def test_a_job_with_a_free_runner_is_not_called_delayed() -> None:
    snapshot = explain([standing(available=1, active=1)], [make_job(1)])

    entry = snapshot.entries[0]
    assert entry.reason is WaitReason.ASSIGNABLE
    assert not entry.reason.is_delayed
    assert snapshot.delayed == 0
    # Nothing to explain: the fleet has done its part and GitHub has the job.
    assert entry.detail == ""


def test_a_job_a_runner_is_starting_for_says_so() -> None:
    snapshot = explain(
        [standing(available=0, active=0, wanted=1)],
        [make_job(1)],
        admission=Admission(granted={"default": 1}),
    )

    entry = snapshot.entries[0]
    assert entry.reason is WaitReason.STARTING
    assert "a runner is starting for it" in entry.detail
    assert not entry.reason.is_delayed


def test_the_line_is_oldest_first_and_positions_start_at_one() -> None:
    jobs = [
        make_job(3, queued_at=at(seconds=20)),
        make_job(1, queued_at=T0),
        make_job(2, queued_at=at(seconds=10)),
    ]
    snapshot = explain([standing(available=1, active=1)], jobs)

    assert [entry.job_id for entry in snapshot.entries] == [1, 2, 3]
    assert [entry.position for entry in snapshot.entries] == [1, 2, 3]
    # Only the first has a runner; the rest are behind it.
    assert snapshot.entries[0].reason is WaitReason.ASSIGNABLE
    assert snapshot.entries[1].reason.is_delayed


# ---------------------------------------------------------------- the delays


def test_a_full_pool_names_max_runners_and_what_is_up() -> None:
    snapshot = explain([standing(available=0, active=4, max_runners=4)], [make_job(1)])

    entry = snapshot.entries[0]
    assert entry.reason is WaitReason.POOL_AT_CAPACITY
    assert entry.detail == "pool is at max_runners=4 with 4 up"


def test_a_burst_spread_over_ticks_says_which_setting_spread_it() -> None:
    snapshot = explain(
        [standing(available=0, active=0, wanted=2, max_runners=8, max_launch_per_tick=2)],
        [make_job(index) for index in range(1, 6)],
        admission=Admission(granted={"default": 2}),
    )

    later = [entry for entry in snapshot.entries if entry.reason.is_delayed]
    assert len(later) == 3
    assert "max_launch_per_tick=2" in later[0].detail
    assert later[0].reason is WaitReason.TICK_LIMIT


def test_a_host_ceiling_is_reported_as_the_host_refusing_not_the_pool_being_full() -> None:
    snapshot = explain(
        [standing(available=0, active=1, wanted=2, max_runners=8)],
        [make_job(1), make_job(2)],
        admission=Admission(granted={"default": 0}, blocked={"default": "max_cpus=4"}),
    )

    entry = snapshot.entries[0]
    assert entry.reason is WaitReason.HOST_AT_CAPACITY
    assert "max_cpus=4" in entry.detail


def test_backpressure_beats_every_other_explanation() -> None:
    # A pool that is also full would otherwise be told its own ceiling is the problem, and
    # making room in it would change nothing while the machine is at its high-water mark.
    held = "host cpu at 96% (high water 85%); deferring every launch until it recovers"
    snapshot = explain(
        [standing(available=0, active=4, max_runners=4)],
        [make_job(1)],
        admission=Admission(granted={"default": 0}, held_by=held),
        load=HostLoad(cpu_percent=96.0),
        limits=CapacityLimits(cpu_high_water=85.0),
    )

    assert snapshot.entries[0].reason is WaitReason.HOST_BUSY
    assert snapshot.entries[0].detail == held
    assert snapshot.host.holding == held
    assert snapshot.host.cpu_percent == 96.0
    assert snapshot.host.cpu_high_water == 85.0


def test_a_job_no_pool_serves_is_named_rather_than_dropped() -> None:
    # The one delay that never clears on its own, and the one that used to be invisible: the
    # job simply did not appear in any count.
    snapshot = explain(
        [standing(available=2, active=2)],
        [make_job(1, labels=LabelSet.of("self-hosted", "gpu"))],
    )

    entry = snapshot.entries[0]
    assert entry.reason is WaitReason.NO_POOL
    assert entry.pool == ""
    assert entry.position == 0
    assert "gpu" in entry.detail


def test_a_job_for_another_repository_belongs_to_no_pool_here() -> None:
    other = RepositoryTarget("someone", "else")
    snapshot = explain([standing(available=2, active=2)], [make_job(1, repository=other)])

    assert snapshot.entries[0].reason is WaitReason.NO_POOL


# ---------------------------------------------------------------- placement


def test_a_job_two_pools_could_serve_is_counted_once_against_the_heavier() -> None:
    # Counting it twice would double the apparent queue, which is the number an operator
    # sizes a machine from.
    light = standing(available=0, active=0, name="light", priority=1)
    heavy = standing(available=0, active=0, name="heavy", priority=10)
    snapshot = explain([light, heavy], [make_job(1)])

    assert len(snapshot.entries) == 1
    assert snapshot.entries[0].pool == "heavy"
    assert snapshot.entries[0].priority == 10
    assert snapshot.counts_by_pool() == {"light": 0, "heavy": 1}


def test_pressure_reports_what_each_pool_holds_and_asked_for() -> None:
    snapshot = explain(
        [standing(available=1, active=3, wanted=2, max_runners=4)],
        [make_job(1), make_job(2), make_job(3)],
        admission=Admission(granted={"default": 1}),
    )

    pressure = snapshot.pools[0]
    assert (pressure.queued, pressure.available, pressure.active) == (3, 1, 3)
    assert (pressure.wanted, pressure.launching) == (2, 1)
    assert "max_runners=4" in pressure.blocked_by


def test_a_pool_that_can_cover_its_queue_is_not_marked_as_held() -> None:
    snapshot = explain([standing(available=2, active=2)], [make_job(1), make_job(2)])

    assert snapshot.pools[0].blocked_by == ""
    assert snapshot.delayed == 0


# ---------------------------------------------------------------- the reading itself


def test_an_unread_repository_is_said_out_loud() -> None:
    # An empty queue and one nobody could read look identical, and only one of them means
    # there is nothing to do.
    snapshot = explain([standing()], [], unreadable=["tguisep/private"])

    assert snapshot.unreadable == ("tguisep/private",)
    assert snapshot.total == 0


def test_waiting_time_is_measured_against_the_reader_not_the_queue() -> None:
    snapshot = explain([standing(available=1, active=1)], [make_job(1, queued_at=T0)])

    # `explain` reads at T0 + 30s, and the job was queued at T0.
    assert snapshot.entries[0].waiting_for(at(seconds=30)) == pytest.approx(30.0)
    assert snapshot.oldest_by_pool(at(minutes=2)) == {"default": pytest.approx(120.0)}


@pytest.mark.parametrize(
    ("reason", "delayed"),
    [
        (WaitReason.ASSIGNABLE, False),
        (WaitReason.STARTING, False),
        (WaitReason.POOL_AT_CAPACITY, True),
        (WaitReason.TICK_LIMIT, True),
        (WaitReason.HOST_AT_CAPACITY, True),
        (WaitReason.HOST_BUSY, True),
        (WaitReason.CONTENDED, True),
        (WaitReason.NO_POOL, True),
    ],
)
def test_only_a_wait_on_the_fleet_counts_as_a_delay(reason: WaitReason, delayed: bool) -> None:
    assert reason.is_delayed is delayed


def test_an_empty_fleet_still_produces_a_readable_snapshot() -> None:
    snapshot = explain([], [])

    assert snapshot.total == 0
    assert snapshot.pools == ()
    assert snapshot.counts_by_pool() == {}


def test_idle_timeout_is_not_a_queue_reason() -> None:
    # A pool with room and a free runner explains nothing, however it is configured: the
    # reasons are about capacity, and adding a note here would be noise on every read.
    snapshot = explain(
        [standing(available=1, active=1, idle_timeout=timedelta(seconds=1))],
        [make_job(1)],
    )

    assert snapshot.entries[0].detail == ""
