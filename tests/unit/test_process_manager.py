"""`pm`: how many runners a pool keeps, and when.

The three shapes, and the reason for naming them rather than leaving an operator to
assemble the same intent out of `min_idle` and `idle_timeout` — where two of the three ways
to write it would be subtly wrong.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta

from ghspot.domain.model.job import QueuedJob
from ghspot.domain.model.pool import ProcessManager
from ghspot.domain.model.runner import Runner, RunnerState
from ghspot.domain.policy.scaling import ScalePlan, plan_scaling

from .conftest import T0, at, make_job, make_pool, make_runner


def plan(*runners: Runner, demand: Sequence[QueuedJob] = (), **spec: object) -> ScalePlan:
    return plan_scaling(make_pool(*runners, **spec), demand, T0)


# ---------------------------------------------------------------- static


def test_static_fills_to_max_runners_with_nothing_queued() -> None:
    """The whole point: the runners are already there when the first job arrives."""
    result = plan(pm=ProcessManager.STATIC, max_runners=4, max_launch_per_tick=10)

    assert result.launch == 4
    assert any("max_runners=4" in reason for reason in result.reasons)


def test_static_never_reaps_an_idle_runner() -> None:
    """Reaping one would mean the next job waits for a container, which is what static buys
    its way out of."""
    idle = [
        make_runner(f"r{index}", state=RunnerState.IDLE, since=at(hours=-5)) for index in range(4)
    ]

    result = plan(
        *idle,
        pm=ProcessManager.STATIC,
        max_runners=4,
        idle_timeout=timedelta(minutes=1),
    )

    assert result.retire == ()


# ---------------------------------------------------------------- ondemand


def test_ondemand_keeps_nothing_warm() -> None:
    result = plan(pm=ProcessManager.ONDEMAND, min_idle=0, max_runners=4)

    assert result.launch == 0


def test_ondemand_starts_a_runner_when_a_job_is_queued() -> None:
    result = plan(pm=ProcessManager.ONDEMAND, max_runners=4, demand=[make_job()])

    assert result.launch == 1


def test_ondemand_still_waits_out_the_idle_timeout() -> None:
    """`ondemand` reaps a runner after `idle_timeout`, not the instant it goes
    idle — a runner that just finished is the one most likely to be wanted next."""
    fresh = make_runner("r1", state=RunnerState.IDLE, since=at(seconds=-30))

    result = plan(fresh, pm=ProcessManager.ONDEMAND, idle_timeout=timedelta(minutes=10))

    assert result.retire == ()


def test_ondemand_reaps_once_the_timeout_has_passed() -> None:
    stale = make_runner("r1", state=RunnerState.IDLE, since=at(hours=-2))

    result = plan(stale, pm=ProcessManager.ONDEMAND, idle_timeout=timedelta(minutes=10))

    assert result.retire == (stale.id,)


# ---------------------------------------------------------------- dynamic


def test_dynamic_is_what_the_daemon_always_did() -> None:
    result = plan(pm=ProcessManager.DYNAMIC, min_idle=2, max_runners=4)

    assert result.launch == 2


def test_max_idle_reaps_a_burst_without_waiting_for_the_timeout() -> None:
    """After a burst the timeout alone leaves every runner of that burst warm for its full
    length, on a host that has gone back to needing one."""
    warm = [
        make_runner(f"r{index}", state=RunnerState.IDLE, since=at(seconds=-5)) for index in range(5)
    ]

    result = plan(
        *warm,
        pm=ProcessManager.DYNAMIC,
        min_idle=1,
        max_idle=2,
        max_runners=8,
        idle_timeout=timedelta(hours=1),
    )

    assert len(result.retire) == 3
    assert any("max_idle=2" in reason for reason in result.reasons)


def test_the_longest_idle_runners_go_first() -> None:
    oldest = make_runner("oldest", state=RunnerState.IDLE, since=at(hours=-3))
    newest = make_runner("newest", state=RunnerState.IDLE, since=at(seconds=-5))

    result = plan(
        oldest,
        newest,
        pm=ProcessManager.DYNAMIC,
        min_idle=0,
        max_idle=1,
        idle_timeout=timedelta(hours=10),
    )

    assert result.retire == (oldest.id,)


def test_nothing_is_reaped_while_work_is_queued() -> None:
    """Unchanged by max_idle: reaping capacity in the same tick the pool is short of it would
    oscillate."""
    warm = [make_runner(f"r{i}", state=RunnerState.IDLE, since=at(seconds=-5)) for i in range(4)]

    result = plan(*warm, pm=ProcessManager.DYNAMIC, max_idle=1, demand=[make_job()])

    assert result.retire == ()


def test_without_max_idle_only_the_timeout_bounds_the_warm_ones() -> None:
    """What the daemon did before this existed, and still the default."""
    warm = [make_runner(f"r{i}", state=RunnerState.IDLE, since=at(seconds=-5)) for i in range(5)]

    result = plan(
        *warm,
        pm=ProcessManager.DYNAMIC,
        min_idle=0,
        max_runners=8,
        idle_timeout=timedelta(hours=1),
    )

    assert result.retire == ()
