"""Turning a tick's decisions into an answer to "why is my job still queued?".

`plan_scaling` says how many runners a pool wants. `admit` says how many the host will take.
Between them they already contain the answer, but only as counts — and a count does not tell
the person watching a build sit at *Waiting for a runner* whether they are behind three other
jobs, behind a `max_runners` they set last month, or behind a machine at 96% memory.

So this places every queued job in a line and names the specific thing in front of it. Pure,
like the two policies it reads from: the reconciler hands it what it already gathered, and no
extra request is made to the forge for any of it.

The line is per pool, oldest first — GitHub hands jobs out in roughly that order, and it is
the order an operator assumes. It is a *model* of the assignment rather than the assignment
itself: the daemon never assigns a job to a runner, it starts runners and GitHub decides. So
position 1 means "first in line for the next free runner in this pool", not a promise.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from ghspot.domain.model.job import QueuedJob
from ghspot.domain.model.pool import PoolSpec
from ghspot.domain.model.queue import (
    HostPressure,
    PoolPressure,
    QueueEntry,
    QueueSnapshot,
    WaitReason,
)
from ghspot.domain.model.target import RepositoryTarget
from ghspot.domain.policy.admission import Admission, CapacityLimits
from ghspot.domain.policy.priority import classify, urgency_of
from ghspot.domain.ports.backend import HostLoad


@dataclass(frozen=True, slots=True)
class PoolStanding:
    """One pool as the tick left it.

    Everything here was computed anyway — the reconciler passes it along rather than the
    explainer re-deriving it, which would mean a second opinion that could disagree with the
    one the loop actually acted on.
    """

    spec: PoolSpec
    available: int
    """Runners able to take a job now or very shortly."""

    active: int
    wanted: int
    """Runners the scaling policy asked for, before the host trimmed anything."""


def explain_queue(
    standings: Sequence[PoolStanding],
    demand: Mapping[RepositoryTarget, Sequence[QueuedJob]],
    admission: Admission,
    load: HostLoad,
    limits: CapacityLimits,
    now: datetime,
    *,
    notes: Sequence[str] = (),
    unreadable: Sequence[str] = (),
) -> QueueSnapshot:
    """Place every queued job against the pool that would serve it, and say what it waits on."""
    by_pool: dict[str, list[QueuedJob]] = {standing.spec.name: [] for standing in standings}
    homeless: list[QueuedJob] = []

    # Deduplicated by job id: the reconciler reads each repository once however many pools
    # serve it, but a caller that passed the same queue twice would otherwise double it.
    seen: set[int] = set()
    for jobs in demand.values():
        for job in jobs:
            if job.id in seen:
                continue
            seen.add(job.id)
            chosen = _best_pool(standings, job)
            if chosen is None:
                homeless.append(job)
            else:
                by_pool[chosen.spec.name].append(job)

    entries: list[QueueEntry] = []
    pressures: list[PoolPressure] = []

    for standing in standings:
        spec = standing.spec
        waiting = sorted(by_pool[spec.name], key=_rank)
        granted = admission.for_pool(spec.name)
        reason, detail = _why_blocked(standing, admission, granted)

        for position, job in enumerate(waiting, start=1):
            entries.append(
                _entry(
                    job,
                    pool=spec.name,
                    priority=spec.priority,
                    position=position,
                    reason=(
                        WaitReason.ASSIGNABLE
                        if position <= standing.available
                        else WaitReason.STARTING
                        if position <= standing.available + granted
                        else reason
                    ),
                    detail=(
                        ""
                        if position <= standing.available
                        else _starting_detail(position - standing.available, granted)
                        if position <= standing.available + granted
                        else detail
                    ),
                )
            )

        pressures.append(
            PoolPressure(
                pool=spec.name,
                repository=str(spec.repository),
                priority=spec.priority,
                queued=len(waiting),
                available=standing.available,
                active=standing.active,
                max_runners=spec.max_runners,
                launching=granted,
                wanted=standing.wanted,
                blocked_by=detail if len(waiting) > standing.available + granted else "",
            )
        )

    for job in sorted(homeless, key=_rank):
        entries.append(
            _entry(
                job,
                pool="",
                priority=0,
                position=0,
                reason=WaitReason.NO_POOL,
                detail=(
                    f"no pool serves {job.repository} with labels "
                    f"[{', '.join(job.labels.as_list())}]"
                ),
            )
        )

    # Most urgent first across the whole queue, oldest first within a class — the same order
    # the positions were handed out in, so the table reads top to bottom whichever pool a row
    # belongs to. The reader's first question is what important thing is stuck, and that
    # question does not stop at a pool boundary.
    entries.sort(key=lambda entry: (-entry.urgency, entry.queued_at, entry.job_id))

    return QueueSnapshot(
        taken_at=now,
        entries=tuple(entries),
        pools=tuple(pressures),
        host=_host_pressure(load, limits, admission.held_by),
        notes=tuple(notes),
        unreadable=tuple(unreadable),
    )


def _rank(job: QueuedJob) -> tuple[int, datetime, int]:
    """The order jobs are placed in: most urgent first, oldest first within a class.

    A model of the line, not a claim about it. GitHub hands a free runner to whichever job it
    chooses — roughly oldest first — so this says which job an operator should care about, not
    which one will actually go next. `policy/priority.py` has the long version.
    """
    return (-urgency_of(classify(job)), job.queued_at, job.id)


def _best_pool(standings: Sequence[PoolStanding], job: QueuedJob) -> PoolStanding | None:
    """The pool a job is counted against when several could serve it.

    Highest weight first, ties on the name, which is the order `admit` hands out contested
    slots in. Counting the job twice would double the apparent queue; counting it against an
    arbitrary pool would make the number move as the configuration is reordered.
    """
    candidates = [standing for standing in standings if standing.spec.can_serve(job)]
    if not candidates:
        return None
    return max(candidates, key=lambda standing: (standing.spec.priority, standing.spec.name))


def _why_blocked(
    standing: PoolStanding, admission: Admission, granted: int
) -> tuple[WaitReason, str]:
    """What is in front of the jobs this pool cannot cover this tick.

    Ordered most-specific-first. The host being held is checked before anything about the
    pool, because when it is held nothing about the pool matters — and the operator needs to
    read "the machine is full", not "your pool is".
    """
    spec = standing.spec

    if admission.held_by:
        return WaitReason.HOST_BUSY, admission.held_by

    if standing.active + granted >= spec.max_runners:
        return (
            WaitReason.POOL_AT_CAPACITY,
            f"pool is at max_runners={spec.max_runners} with {standing.active} up",
        )

    ceiling = admission.blocked.get(spec.name)
    if ceiling:
        return (
            WaitReason.HOST_AT_CAPACITY,
            f"the host refused the launch: {ceiling} reached (pool weight {spec.priority})",
        )

    if granted >= spec.max_launch_per_tick:
        return (
            WaitReason.TICK_LIMIT,
            f"max_launch_per_tick={spec.max_launch_per_tick} spreads the burst over several ticks",
        )

    return (
        WaitReason.CONTENDED,
        f"capacity went to another pool this tick (weight {spec.priority})",
    )


def _starting_detail(nth: int, granted: int) -> str:
    if granted == 1:
        return "a runner is starting for it"
    return f"a runner is starting for it ({nth} of {granted} launching)"


def _entry(
    job: QueuedJob,
    *,
    pool: str,
    priority: int,
    position: int,
    reason: WaitReason,
    detail: str,
) -> QueueEntry:
    work_class = classify(job)
    return QueueEntry(
        job_id=job.id,
        run_id=job.run_id,
        repository=str(job.repository),
        workflow=job.workflow_name,
        job_name=job.job_name,
        labels=tuple(job.labels.as_list()),
        queued_at=job.queued_at,
        url=job.url,
        work_class=work_class,
        urgency=urgency_of(work_class),
        pool=pool,
        priority=priority,
        position=position,
        reason=reason,
        detail=detail,
    )


def _host_pressure(load: HostLoad, limits: CapacityLimits, held_by: str) -> HostPressure:
    return HostPressure(
        cpu_percent=load.cpu_percent,
        memory_percent=load.memory_percent,
        disk_percent=load.disk_percent,
        containers_running=load.containers_running,
        cpu_high_water=limits.cpu_high_water,
        memory_high_water=limits.memory_high_water,
        disk_high_water=limits.disk_high_water,
        max_containers=limits.max_containers,
        max_cpus=limits.max_cpus,
        max_memory_bytes=limits.max_memory_bytes,
        holding=held_by,
    )
