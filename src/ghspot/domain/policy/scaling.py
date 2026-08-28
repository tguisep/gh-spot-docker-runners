"""The scaling decision.

A pure function from (what the pool looks like, what is queued, what time it is) to a plan.
No I/O, no clock of its own, no randomness — so every interesting case is a table row in a
unit test rather than a Docker daemon and a live repository.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ghspot.domain.model.job import QueuedJob
from ghspot.domain.model.pool import PoolSpec, ProcessManager, RunnerPool
from ghspot.domain.model.runner import Runner, RunnerId, RunnerState


@dataclass(frozen=True, slots=True)
class ScalePlan:
    """What the reconciliation loop should do to a pool this tick."""

    pool: str
    launch: int = 0
    """How many new runners to start."""

    retire: tuple[RunnerId, ...] = ()
    """Idle runners to stop gracefully — they are not holding a job."""

    terminate: tuple[RunnerId, ...] = ()
    """Runners to kill outright because their job overran ``max_job_duration``."""

    reasons: tuple[str, ...] = field(default=())
    """Human-readable explanation, surfaced by ``ghspot pool status`` and the logs."""

    @property
    def is_noop(self) -> bool:
        return not (self.launch or self.retire or self.terminate)


def plan_scaling(pool: RunnerPool, demand: Sequence[QueuedJob], now: datetime) -> ScalePlan:
    """Decide how the pool should change.

    The shape of the decision:

    1. Keep enough runners to cover every queued job this pool can serve.
    2. On top of that, keep ``min_idle`` spare runners warm so the next job doesn't wait for a
       container to boot.
    3. Never exceed ``max_runners``, and never start more than ``max_launch_per_tick`` at once.
    4. Retire runners that have been idle longer than ``idle_timeout``, but never below
       ``min_idle``.
    5. Kill runners whose job has overrun ``max_job_duration``.

    Scale-up and scale-down are computed from the same snapshot but never both act on the same
    runner, so the plan cannot contradict itself.
    """
    spec = pool.spec
    reasons: list[str] = []
    keep = _warm_band(spec)

    servable = [job for job in demand if spec.can_serve(job)]
    overrunning = _overrunning(pool, now)

    # A runner about to be killed is not capacity, so it is excluded from the count.
    overrunning_ids = {runner.id for runner in overrunning}
    available = [runner for runner in pool.available if runner.id not in overrunning_ids]

    # 1. cover the queue
    uncovered = max(0, len(servable) - len(available))

    # 2. keep the warm floor on top of the queue
    spare_after_demand = len(available) + uncovered - len(servable)
    warm_shortfall = max(0, keep.floor - spare_after_demand)

    wanted = uncovered + warm_shortfall

    # 3. respect the ceilings
    headroom = max(0, spec.max_runners - (pool.active_count - len(overrunning)))
    launch = min(wanted, headroom, spec.max_launch_per_tick)

    if uncovered:
        reasons.append(f"{uncovered} queued job(s) with no runner available")
    if warm_shortfall:
        reasons.append(f"{warm_shortfall} runner(s) short of {keep.floor_name}={keep.floor}")
    if wanted > launch:
        capped_by = "max_runners" if headroom < spec.max_launch_per_tick else "max_launch_per_tick"
        reasons.append(f"wanted {wanted}, capped to {launch} by {capped_by}")

    # 4. reap the idle — only when nothing is waiting, so a burst is never starved by a reap
    retire: Sequence[Runner] = ()
    if servable or not keep.reaps_idle:
        # `static` never reaps: the whole point is that the runners are already there.
        pass
    else:
        retire = _idle_beyond_timeout(pool, keep.floor, now)
        if retire:
            reasons.append(f"{len(retire)} runner(s) idle beyond {_seconds(spec.idle_timeout)}s")

        surplus = _above_the_warm_ceiling(pool, keep.ceiling, [r.id for r in retire], now)
        if surplus:
            reasons.append(f"{len(surplus)} runner(s) above max_idle={keep.ceiling}")
            retire = [*retire, *surplus]

    # 5. kill the overrunning
    if overrunning:
        reasons.append(
            f"{len(overrunning)} runner(s) past max_job_duration={_seconds(spec.max_job_duration)}s"
        )

    return ScalePlan(
        pool=spec.name,
        launch=launch,
        retire=tuple(runner.id for runner in retire),
        terminate=tuple(runner.id for runner in overrunning),
        reasons=tuple(reasons),
    )


@dataclass(frozen=True, slots=True)
class _WarmBand:
    """How many runners a pool keeps warm, and whether idle ones are reaped at all."""

    floor: int
    ceiling: int | None
    reaps_idle: bool
    floor_name: str


def _warm_band(spec: PoolSpec) -> _WarmBand:
    """Turn the pool's `pm` into the two numbers the plan is built from.

    Here rather than in the configuration parser because it is a decision, not a validation:
    `static` means "the floor is max_runners" in a way the loop has to keep agreeing with as
    a pool's ceiling changes.
    """
    if spec.pm is ProcessManager.STATIC:
        # Every runner is a warm one, and none of them is ever idle enough to reap.
        return _WarmBand(
            floor=spec.max_runners, ceiling=None, reaps_idle=False, floor_name="max_runners"
        )
    if spec.pm is ProcessManager.ONDEMAND:
        return _WarmBand(floor=0, ceiling=None, reaps_idle=True, floor_name="min_idle")
    return _WarmBand(
        floor=spec.min_idle, ceiling=spec.max_idle, reaps_idle=True, floor_name="min_idle"
    )


def _above_the_warm_ceiling(
    pool: RunnerPool, ceiling: int | None, already_going: Sequence[RunnerId], now: datetime
) -> list[Runner]:
    """Warm runners beyond what the pool is allowed to keep.

    Reaped without waiting for `idle_timeout`, which is the point: after a burst the timeout
    alone leaves every runner of that burst warm for its full length, on a host that has gone
    back to needing one. Longest-idle first, the same order the timeout reaper uses.
    """
    if ceiling is None:
        return []
    going = set(already_going)
    idle = sorted(
        (runner for runner in pool.in_state(RunnerState.IDLE) if runner.id not in going),
        key=lambda runner: runner.idle_for(now),
        reverse=True,
    )
    return idle[: max(0, len(idle) - ceiling)]


def _overrunning(pool: RunnerPool, now: datetime) -> list[Runner]:
    limit = pool.spec.max_job_duration.total_seconds()
    return [
        runner
        for runner in pool.in_state(RunnerState.BUSY, RunnerState.DRAINING)
        if runner.busy_for(now) > limit
    ]


def _idle_beyond_timeout(pool: RunnerPool, floor: int, now: datetime) -> list[Runner]:
    """The idle runners worth reaping, longest-idle first, keeping ``floor`` alive."""
    limit = pool.spec.idle_timeout.total_seconds()
    idle = sorted(
        pool.in_state(RunnerState.IDLE),
        key=lambda runner: runner.idle_for(now),
        reverse=True,
    )
    disposable = max(0, len(idle) - floor)
    return [runner for runner in idle[:disposable] if runner.idle_for(now) > limit]


def _seconds(delta: timedelta) -> int:
    return int(delta.total_seconds())
