"""Usage statistics, folded out of the event log.

The numbers come from events rather than from the runners table on purpose. The table is a
projection: rows are deleted as runners retire and pruned to a recent few hundred, so a
report built on it would quietly stop covering the window an operator asked about. The log
is append-only, which is the only thing here that can answer "what did last week cost".

One runner's story is at most six events, and the interesting quantities are the gaps
between them:

    registered ──wait──▶ took job ──busy──▶ retired
    └────────────────── alive ──────────────────┘

A runner that never took a job has no wait and no busy time, and is exactly the capacity
that cost something and returned nothing.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from ghspot.application.dto import StatsView, UsageStats
from ghspot.domain.model.events import (
    DomainEvent,
    RunnerFailed,
    RunnerRegistered,
    RunnerRetired,
    RunnerTookJob,
)
from ghspot.domain.ports.repository import EventLog, RunnerRepository
from ghspot.domain.ports.system import Clock

UNKNOWN = "(unknown)"
"""Where a runner's registration is outside the window, so its group cannot be named. Shown
rather than dropped: hiding it would make the totals disagree with the rows."""


@dataclass
class _Story:
    """One runner's life, assembled as the events arrive."""

    repository: str = UNKNOWN
    pool: str = UNKNOWN
    registered_at: datetime | None = None
    took_job_at: datetime | None = None
    ended_at: datetime | None = None
    failed: bool = False


@dataclass
class _Tally:
    """A mutable UsageStats, since folding wants to add in place."""

    runners: int = 0
    jobs: int = 0
    failed: int = 0
    completed: int = 0
    busy_seconds: float = 0.0
    alive_seconds: float = 0.0
    wait_seconds: float = 0.0
    waits_counted: int = 0
    live: int = 0

    def finish(self, key: str) -> UsageStats:
        return UsageStats(
            key=key,
            runners=self.runners,
            jobs=self.jobs,
            failed=self.failed,
            completed=self.completed,
            busy_seconds=self.busy_seconds,
            alive_seconds=self.alive_seconds,
            wait_seconds=self.wait_seconds,
            waits_counted=self.waits_counted,
            live=self.live,
        )


def _gap(start: datetime | None, end: datetime | None) -> float:
    """Seconds between two moments, never negative.

    Clamped rather than trusted: events are timestamped by the daemon's clock, and a clock
    stepped backwards mid-run would otherwise subtract from an operator's totals.
    """
    if start is None or end is None:
        return 0.0
    return max(0.0, (end - start).total_seconds())


def stories(events: Sequence[DomainEvent]) -> dict[str, _Story]:
    """Fold the log into one story per runner. Pure; the tests drive it directly."""
    found: dict[str, _Story] = {}

    for event in events:
        runner_id = getattr(event, "runner_id", None)
        if not isinstance(runner_id, str):
            continue
        story = found.setdefault(runner_id, _Story())

        if isinstance(event, RunnerRegistered):
            story.repository = str(event.repository)
            story.pool = event.pool or UNKNOWN
            story.registered_at = event.occurred_at
        elif isinstance(event, RunnerTookJob):
            # First assignment wins. A just-in-time runner takes one job, but a replayed or
            # duplicated event must not move the start of its busy period.
            story.took_job_at = story.took_job_at or event.occurred_at
        elif isinstance(event, RunnerRetired):
            story.ended_at = event.occurred_at
        elif isinstance(event, RunnerFailed):
            story.ended_at = event.occurred_at
            story.failed = True

    return found


def _add(tally: _Tally, story: _Story) -> None:
    tally.runners += 1
    if story.failed:
        tally.failed += 1
    elif story.ended_at is not None:
        tally.completed += 1

    if story.took_job_at is not None:
        tally.jobs += 1
        tally.wait_seconds += _gap(story.registered_at, story.took_job_at)
        tally.waits_counted += 1
        tally.busy_seconds += _gap(story.took_job_at, story.ended_at)

    tally.alive_seconds += _gap(story.registered_at, story.ended_at)


def _ranked(groups: dict[str, _Tally]) -> list[UsageStats]:
    """Busiest first, so the row an operator is looking for is the one at the top."""
    return sorted(
        (tally.finish(key) for key, tally in groups.items()),
        key=lambda stats: (stats.runners, stats.busy_seconds),
        reverse=True,
    )


class GatherStats:
    """`ghspot stats`: what the fleet did, and what it cost."""

    def __init__(
        self, events: EventLog, runners: RunnerRepository, clock: Clock, host: str = ""
    ) -> None:
        self._events = events
        self._runners = runners
        self._clock = clock
        self._host = host

    async def __call__(self, since: datetime | None = None) -> StatsView:
        recorded = await self._events.since(since)
        told = stories(recorded)

        by_repository: dict[str, _Tally] = {}
        by_pool: dict[str, _Tally] = {}
        total = _Tally()

        for story in told.values():
            _add(by_repository.setdefault(story.repository, _Tally()), story)
            _add(by_pool.setdefault(story.pool, _Tally()), story)
            _add(total, story)

        # Live counts come from the projection, not the log: a runner working right now has
        # no end event, so the log cannot see it at all.
        for runner in await self._runners.list_active():
            total.live += 1
            by_repository.setdefault(str(runner.repository), _Tally()).live += 1
            by_pool.setdefault(runner.pool, _Tally()).live += 1

        return StatsView(
            host=self._host,
            since=since,
            until=self._clock.now(),
            total=total.finish(""),
            by_repository=_ranked(by_repository),
            by_pool=_ranked(by_pool),
            failures=_failures(recorded),
            events_read=len(recorded),
        )


def _failures(events: Sequence[DomainEvent]) -> list[tuple[str, int]]:
    """Failure reasons, commonest first.

    Reasons carry ids and messages, so they are collapsed to their first line and trimmed —
    otherwise every failure looks unique and the tally says nothing.
    """
    counted: dict[str, int] = {}
    for event in events:
        if not isinstance(event, RunnerFailed):
            continue
        reason = (event.reason or "unknown").strip().splitlines()[0][:80] or "unknown"
        counted[reason] = counted.get(reason, 0) + 1
    return sorted(counted.items(), key=lambda pair: (pair[1], pair[0]), reverse=True)
