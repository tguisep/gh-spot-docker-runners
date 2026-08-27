"""The scaling decision, exhaustively.

Every case here would otherwise need a Docker daemon and a live repository to reproduce.
That it doesn't is the return on the layering.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from ghspot.domain.model.labels import LabelSet
from ghspot.domain.model.runner import RunnerState
from ghspot.domain.model.target import RepositoryTarget
from ghspot.domain.policy.scaling import plan_scaling
from tests.unit.conftest import T0, at, make_job, make_pool, make_runner

# ---------------------------------------------------------------- scaling up


@pytest.mark.parametrize(
    ("queued", "idle", "busy", "min_idle", "max_runners", "expected_launch"),
    [
        # nothing queued, nothing warm wanted -> do nothing
        (0, 0, 0, 0, 4, 0),
        # one job, no runners -> start one
        (1, 0, 0, 0, 4, 1),
        # one job, one idle runner already waiting -> start nothing
        (1, 1, 0, 0, 4, 0),
        # three jobs, one idle -> start the two uncovered
        (3, 1, 0, 0, 4, 2),
        # busy runners are not capacity: they cannot take another job
        (2, 0, 2, 0, 8, 2),
        # a warm pool is kept even with an empty queue
        (0, 0, 0, 2, 4, 2),
        # min_idle is spare capacity *on top of* the queue
        (2, 0, 0, 1, 8, 3),
        # already warm enough
        (0, 2, 0, 2, 4, 0),
        # the ceiling wins over demand
        (10, 0, 0, 0, 3, 3),
        # busy runners consume the ceiling
        (10, 0, 2, 0, 3, 1),
        # at the ceiling, nothing starts
        (5, 0, 4, 0, 4, 0),
    ],
)
def test_launch_count(
    queued: int,
    idle: int,
    busy: int,
    min_idle: int,
    max_runners: int,
    expected_launch: int,
) -> None:
    runners = [make_runner(f"i{n}", state=RunnerState.IDLE) for n in range(idle)]
    runners += [make_runner(f"b{n}", state=RunnerState.BUSY) for n in range(busy)]
    pool = make_pool(*runners, min_idle=min_idle, max_runners=max_runners, max_launch_per_tick=99)
    demand = [make_job(n) for n in range(queued)]

    plan = plan_scaling(pool, demand, T0)

    assert plan.launch == expected_launch
    assert plan.pool == "default"


def test_runners_still_booting_count_as_capacity() -> None:
    """Otherwise the next tick starts a second runner for a job the first will take."""
    pool = make_pool(
        make_runner("a", state=RunnerState.REGISTERED),
        make_runner("b", state=RunnerState.STARTING),
    )

    assert plan_scaling(pool, [make_job(1), make_job(2)], T0).launch == 0


def test_a_burst_is_spread_across_ticks() -> None:
    """A twenty-leg matrix must not try to start twenty containers at once."""
    pool = make_pool(max_runners=20, max_launch_per_tick=3)
    demand = [make_job(n) for n in range(20)]

    plan = plan_scaling(pool, demand, T0)

    assert plan.launch == 3
    assert any("max_launch_per_tick" in reason for reason in plan.reasons)


def test_jobs_this_pool_cannot_serve_are_ignored() -> None:
    pool = make_pool(labels=LabelSet.of("self-hosted", "linux", "x64"))
    demand = [
        make_job(1, labels=LabelSet.of("self-hosted", "gpu")),
        make_job(2, labels=LabelSet.of("self-hosted"), repository=RepositoryTarget("x", "y")),
        make_job(3, labels=LabelSet.of("self-hosted", "linux")),
    ]

    plan = plan_scaling(pool, demand, T0)

    assert plan.launch == 1


# ---------------------------------------------------------------- scaling down


def test_runners_idle_past_the_timeout_are_retired() -> None:
    pool = make_pool(
        make_runner("stale", state=RunnerState.IDLE, since=T0),
        make_runner("fresh", state=RunnerState.IDLE, since=at(minutes=25)),
        idle_timeout=timedelta(minutes=10),
    )

    plan = plan_scaling(pool, [], at(minutes=30))

    assert plan.retire == ("stale",)
    assert plan.launch == 0


def test_min_idle_survives_the_reaper() -> None:
    pool = make_pool(
        *[make_runner(f"i{n}", state=RunnerState.IDLE, since=T0) for n in range(3)],
        min_idle=2,
        idle_timeout=timedelta(minutes=10),
    )

    plan = plan_scaling(pool, [], at(hours=1))

    assert len(plan.retire) == 1


def test_nothing_is_reaped_while_work_is_waiting() -> None:
    """Reaping capacity in the same tick that we are short of it would flap."""
    pool = make_pool(
        make_runner("stale", state=RunnerState.IDLE, since=T0),
        idle_timeout=timedelta(minutes=10),
        labels=LabelSet.of("self-hosted", "linux", "x64", "home-vm"),
    )

    plan = plan_scaling(pool, [make_job(1), make_job(2)], at(hours=1))

    assert plan.retire == ()
    assert plan.launch == 1


def test_a_plan_never_starts_and_reaps_at_once() -> None:
    pool = make_pool(
        *[make_runner(f"i{n}", state=RunnerState.IDLE, since=T0) for n in range(2)],
        min_idle=0,
        max_runners=10,
        idle_timeout=timedelta(minutes=10),
    )

    plan = plan_scaling(pool, [make_job(n) for n in range(5)], at(hours=1))

    assert not (plan.launch and plan.retire)


# ---------------------------------------------------------------- runaway jobs


def test_a_job_past_its_deadline_is_terminated() -> None:
    pool = make_pool(
        make_runner("hung", state=RunnerState.BUSY, since=T0),
        make_runner("fine", state=RunnerState.BUSY, since=at(hours=2, minutes=50)),
        max_job_duration=timedelta(hours=2),
    )

    plan = plan_scaling(pool, [], at(hours=3))

    assert plan.terminate == ("hung",)
    assert any("max_job_duration" in reason for reason in plan.reasons)


def test_a_draining_runner_is_still_held_to_the_deadline() -> None:
    pool = make_pool(
        make_runner("hung", state=RunnerState.DRAINING, since=T0),
        max_job_duration=timedelta(hours=2),
    )

    assert plan_scaling(pool, [], at(hours=3)).terminate == ("hung",)


def test_a_terminated_runner_frees_its_slot_immediately() -> None:
    """Otherwise a hung job blocks the queue for a whole extra tick."""
    pool = make_pool(
        make_runner("hung", state=RunnerState.BUSY, since=T0),
        max_runners=1,
        max_job_duration=timedelta(hours=2),
    )

    plan = plan_scaling(pool, [make_job(1)], at(hours=3))

    assert plan.terminate == ("hung",)
    assert plan.launch == 1


# ---------------------------------------------------------------- plan shape


def test_an_untroubled_pool_produces_a_noop() -> None:
    pool = make_pool(make_runner("a", state=RunnerState.IDLE, since=at(minutes=1)))

    plan = plan_scaling(pool, [], at(minutes=2))

    assert plan.is_noop
    assert plan.reasons == ()


def test_every_action_carries_a_reason() -> None:
    pool = make_pool(max_runners=1, max_launch_per_tick=1)

    plan = plan_scaling(pool, [make_job(1), make_job(2)], T0)

    assert not plan.is_noop
    assert plan.reasons


def test_a_protected_pool_does_not_scale_for_work_that_never_asked_for_it() -> None:
    """The reason requires_labels exists.

    Without it the pool counts a plain CPU job as its own and starts a GPU runner to serve
    something that would have been happy anywhere.
    """
    protected = make_pool(
        labels=LabelSet.of("self-hosted", "linux", "x64", "gpu-a100"),
        requires_labels=LabelSet.of("gpu-a100"),
        max_runners=4,
    )
    cpu_work = [make_job(n, labels=LabelSet.of("self-hosted", "linux", "x64")) for n in range(3)]

    assert plan_scaling(protected, cpu_work, T0).launch == 0


def test_a_protected_pool_still_scales_for_the_work_it_is_for() -> None:
    protected = make_pool(
        labels=LabelSet.of("self-hosted", "linux", "x64", "gpu-a100"),
        requires_labels=LabelSet.of("gpu-a100"),
        max_runners=4,
        max_launch_per_tick=4,
    )
    gpu_work = [
        make_job(n, labels=LabelSet.of("self-hosted", "linux", "x64", "gpu-a100")) for n in range(2)
    ]

    assert plan_scaling(protected, gpu_work, T0).launch == 2
