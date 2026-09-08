"""What is waiting, and why it is still waiting.

The reconciliation loop already knows all of this. Every tick it reads the queue from the
forge, works out which pool can serve each job, asks the scaling policy how many runners
that needs and the admission policy how many the host will take. Then it acts, and the
reasoning is thrown away — surviving only as a `notes` line in the journal.

This is that reasoning, kept. A snapshot is a *flat* value object on purpose: it is written
to the projection at the end of a tick and read back by the CLI and the API, which have no
forge client and must not grow one. Nothing here holds a `PoolSpec` or a `QueuedJob`, so
storing it is a serialisation and not a rehydration of the domain.

Like everything else in the projection it is derived, not owned: a lost snapshot costs one
tick of visibility, and the next tick writes a new one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from ghspot.domain.model.job import WorkClass


class WaitReason(StrEnum):
    """Why one queued job is not running yet.

    Ordered from "nothing is wrong" to "something is holding the whole host": an operator
    reading a queue full of `HOST_BUSY` has a different problem from one reading a queue full
    of `NO_POOL`, and the first question is always which of the two it is.
    """

    ASSIGNABLE = "assignable"
    """A runner is up and free for it. If this persists, the delay is GitHub's dispatch, not
    the fleet's capacity — the job is being offered a runner and has not taken it yet."""

    STARTING = "starting"
    """A runner is being launched for it right now, and it waits out a container boot."""

    POOL_AT_CAPACITY = "pool-at-capacity"
    """The pool that serves it is already at `max_runners`. Nothing starts until one frees."""

    TICK_LIMIT = "tick-limit"
    """The pool could grow, but `max_launch_per_tick` spreads the burst over several ticks.
    Self-clearing, and normally a matter of seconds."""

    HOST_AT_CAPACITY = "host-at-capacity"
    """A committed ceiling — `max_containers`, `max_cpus`, `max_memory` — was reached, so the
    host refused the launch even though the pool had room."""

    HOST_BUSY = "host-busy"
    """Backpressure: the machine's measured load is at a high-water mark, and *nothing*
    starts until it recovers. The one reason that is about the box rather than the pool."""

    CONTENDED = "contended"
    """Capacity existed but went to another pool this tick. Priority decides who wins; a job
    sitting here is losing a share it will get on a later tick."""

    NO_POOL = "no-pool"
    """No configured pool serves these labels in this repository. This one does not clear on
    its own — it is a configuration answer, not a capacity one."""

    @property
    def is_delayed(self) -> bool:
        """Whether this job is waiting on the fleet rather than on GitHub."""
        return self not in {WaitReason.ASSIGNABLE, WaitReason.STARTING}


@dataclass(frozen=True, slots=True)
class QueueEntry:
    """One queued job, placed against the pool that would serve it."""

    job_id: int
    run_id: int
    repository: str
    workflow: str
    job_name: str
    labels: tuple[str, ...]
    queued_at: datetime

    url: str = ""
    """The job's page on the forge. Empty only when the forge did not give one."""

    work_class: WorkClass = WorkClass.BRANCH
    """What kind of work this is — a merge to the default branch, a review, a draft."""

    urgency: int = 0
    """What that class is worth. The job's own rank, distinct from `priority` below, which
    belongs to the pool: one says how much this job matters, the other how much its pool's
    launches matter when the host cannot satisfy every pool at once."""

    pool: str = ""
    """The pool that would serve it, or empty when none can."""

    priority: int = 0
    """The serving pool's weight, carried here so the queue can be read on its own."""

    position: int = 0
    """1-based place in its pool's line: most urgent first, oldest first within a class.
    0 when no pool serves it."""

    reason: WaitReason = WaitReason.NO_POOL
    detail: str = ""
    """One sentence naming the specific limit, with its configured value in it."""

    def waiting_for(self, now: datetime) -> float:
        """Seconds queued at GitHub, never negative."""
        return max(0.0, (now - self.queued_at).total_seconds())


@dataclass(frozen=True, slots=True)
class PoolPressure:
    """One pool's side of the queue: what it is holding and what is holding it."""

    pool: str
    repository: str
    priority: int
    queued: int
    available: int
    """Runners able to take a job now or very shortly."""

    active: int
    max_runners: int
    launching: int
    """Runners the host admitted for this pool this tick."""

    wanted: int
    """Runners the scaling policy asked for, before the host trimmed it."""

    blocked_by: str = ""
    """Empty when nothing held this pool back."""

    @property
    def headroom(self) -> int:
        return max(0, self.max_runners - self.active)


@dataclass(frozen=True, slots=True)
class HostPressure:
    """The machine, and the limits it is being read against.

    Both halves matter: 78% memory means nothing until you know whether the high-water mark
    is 75 or 95. A reading that could not be taken stays ``None`` rather than becoming zero.
    """

    cpu_percent: float | None = None
    memory_percent: float | None = None
    disk_percent: float | None = None
    containers_running: int | None = None

    cpu_high_water: float | None = None
    memory_high_water: float | None = None
    disk_high_water: float | None = None

    max_containers: int | None = None
    max_cpus: float | None = None
    max_memory_bytes: int | None = None

    holding: str = ""
    """The backpressure sentence, when the host is refusing every launch. Empty otherwise."""


@dataclass(frozen=True, slots=True)
class QueueSnapshot:
    """The whole queue as one tick saw it."""

    taken_at: datetime
    entries: tuple[QueueEntry, ...] = ()
    pools: tuple[PoolPressure, ...] = ()
    host: HostPressure = field(default_factory=HostPressure)
    notes: tuple[str, ...] = ()
    """The scaling and admission reasons from the tick, verbatim, for the whole picture."""

    unreadable: tuple[str, ...] = ()
    """Repositories whose queue could not be read this tick. An empty queue and an unread one
    look identical from the outside, and only one of them means there is nothing to do."""

    @property
    def total(self) -> int:
        return len(self.entries)

    @property
    def delayed(self) -> int:
        return sum(1 for entry in self.entries if entry.reason.is_delayed)

    def for_pool(self, pool: str) -> tuple[QueueEntry, ...]:
        return tuple(entry for entry in self.entries if entry.pool == pool)

    def counts_by_pool(self) -> dict[str, int]:
        """Queued jobs per pool, which is what the pools table has always meant to show.

        Read from the pressure rows rather than by counting entries: the two agree when the
        snapshot is whole, and the count is the number the tick actually acted on. Counting
        the entries instead would make the pools table quietly depend on the job list still
        being complete.
        """
        return {pressure.pool: pressure.queued for pressure in self.pools}

    def oldest_by_pool(self, now: datetime) -> dict[str, float]:
        """The longest wait in each pool's line, in seconds."""
        oldest: dict[str, float] = {}
        for entry in self.entries:
            if not entry.pool:
                continue
            waited = entry.waiting_for(now)
            oldest[entry.pool] = max(oldest.get(entry.pool, 0.0), waited)
        return oldest
