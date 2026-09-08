"""Reading back what the last tick saw waiting.

The daemon is the only process with a forge client, so it is the only one that can see the
queue. It writes down what it saw and why nothing was done about it; this reads that back for
the CLI and the API, neither of which holds a credential.

That indirection is the whole reason the queue used to show empty: the pools table has always
had a `queued` column, every reader of it worked from the projection alone, and the projection
never carried the number. It read zero because nothing had ever written anything else.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ghspot.application.dto import (
    HostPressureView,
    PoolPressureView,
    QueueEntryView,
    QueueView,
)
from ghspot.domain.model.queue import QueueEntry, QueueSnapshot
from ghspot.domain.ports.repository import QueueSnapshots
from ghspot.domain.ports.system import Clock

#: How many poll intervals a snapshot may fall behind before it is called stale. Two, so a
#: single slow tick — a big repository, a rate-limit pause — never raises a false alarm, and
#: a stopped daemon is still called out within about half a minute of default configuration.
STALE_AFTER_INTERVALS = 2


class GetQueue:
    """What is waiting, in what order, and what each job is waiting on.

    ``poll_interval`` is the daemon's, and is what makes "stale" mean something: a snapshot
    is expected to be replaced every interval, so one that is much older says the daemon is
    not running. Without it a reader cannot tell an empty queue from an absent daemon, which
    are the two states that look identical and mean opposite things.
    """

    def __init__(
        self,
        queue: QueueSnapshots,
        clock: Clock,
        poll_interval: timedelta = timedelta(seconds=15),
    ) -> None:
        self._queue = queue
        self._clock = clock
        self._poll_interval = poll_interval

    async def __call__(self, pool: str | None = None) -> QueueView:
        snapshot = await self._queue.latest()
        if snapshot is None:
            return QueueView()

        now = self._clock.now()
        age = max(0.0, (now - snapshot.taken_at).total_seconds())
        oldest = snapshot.oldest_by_pool(now)

        entries = [
            _entry(item, now)
            for item in snapshot.entries
            # An unplaced job is shown whatever pool was asked for: "no pool serves this" is
            # never the answer to a question about one pool, and hiding it behind a filter is
            # how it stays unnoticed.
            if pool is None or item.pool == pool or not item.pool
        ]
        pools = [
            PoolPressureView(
                pool=pressure.pool,
                repository=pressure.repository,
                priority=pressure.priority,
                queued=pressure.queued,
                available=pressure.available,
                active=pressure.active,
                max_runners=pressure.max_runners,
                launching=pressure.launching,
                wanted=pressure.wanted,
                blocked_by=pressure.blocked_by,
                oldest_wait_seconds=oldest.get(pressure.pool, 0.0),
            )
            for pressure in snapshot.pools
            if pool is None or pressure.pool == pool
        ]

        return QueueView(
            taken_at=snapshot.taken_at,
            age_seconds=age,
            stale=age > self._poll_interval.total_seconds() * STALE_AFTER_INTERVALS,
            entries=entries,
            pools=pools,
            host=_host(snapshot),
            notes=list(snapshot.notes),
            unreadable=list(snapshot.unreadable),
        )


def _entry(item: QueueEntry, now: datetime) -> QueueEntryView:
    return QueueEntryView(
        job_id=item.job_id,
        run_id=item.run_id,
        repository=item.repository,
        workflow=item.workflow,
        job_name=item.job_name,
        labels=list(item.labels),
        pool=item.pool,
        priority=item.priority,
        position=item.position,
        reason=item.reason,
        detail=item.detail,
        # Measured against the reader's clock, not the snapshot's: a job queued at 10:00 has
        # been waiting a little longer than the tick that saw it recorded, and the number an
        # operator compares against a GitHub page should be the one on the wall.
        waiting_seconds=item.waiting_for(now),
    )


def _host(snapshot: QueueSnapshot) -> HostPressureView:
    host = snapshot.host
    return HostPressureView(
        cpu_percent=host.cpu_percent,
        memory_percent=host.memory_percent,
        disk_percent=host.disk_percent,
        io_percent=host.io_percent,
        containers_running=host.containers_running,
        cpu_high_water=host.cpu_high_water,
        memory_high_water=host.memory_high_water,
        disk_high_water=host.disk_high_water,
        io_high_water=host.io_high_water,
        max_containers=host.max_containers,
        max_cpus=host.max_cpus,
        max_memory_bytes=host.max_memory_bytes,
        holding=host.holding,
    )
