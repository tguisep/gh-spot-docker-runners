"""`ghspot stats`: folding the event log into what the fleet did.

The numbers matter more than most output here, because an operator sizes a pool from them.
So the cases below are mostly about the awkward shapes — a runner that never got a job, one
whose registration falls outside the window, a clock that went backwards.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from rich.text import Text

from ghspot.application.dto import StatsView, UsageStats
from ghspot.application.queries.stats import UNKNOWN, GatherStats, stories
from ghspot.domain.model import events as domain_events
from ghspot.domain.model.events import DomainEvent
from ghspot.domain.model.runner import Runner
from ghspot.domain.model.target import RepositoryTarget
from ghspot.interfaces.cli.render import stats_tables

from .conftest import REPO, at, make_runner

OTHER = RepositoryTarget("tguisep", "other-project")


def life(
    runner_id: str,
    *,
    pool: str = "default",
    repository: RepositoryTarget = REPO,
    registered: float = 0,
    took_job: float | None = None,
    retired: float | None = None,
    failed: float | None = None,
    reason: str = "container creation failed",
) -> list[DomainEvent]:
    """One runner's events, positioned in minutes from the clock origin."""
    written: list[DomainEvent] = [
        domain_events.RunnerRegistered(
            occurred_at=at(minutes=registered),
            runner_id=runner_id,
            runner_name=f"ghspot-{pool}-{runner_id}",
            github_runner_id=1,
            repository=repository,
            pool=pool,
        )
    ]
    if took_job is not None:
        written.append(
            domain_events.RunnerTookJob(
                occurred_at=at(minutes=took_job), runner_id=runner_id, job_id=7
            )
        )
    if retired is not None:
        written.append(
            domain_events.RunnerRetired(
                occurred_at=at(minutes=retired), runner_id=runner_id, reason="job finished"
            )
        )
    if failed is not None:
        written.append(
            domain_events.RunnerFailed(
                occurred_at=at(minutes=failed), runner_id=runner_id, reason=reason
            )
        )
    return written


class FakeLog:
    def __init__(self, events: Sequence[DomainEvent]) -> None:
        self.events = list(events)
        self.asked_for: object = "not asked"

    async def append(self, events: Sequence[DomainEvent]) -> None:  # pragma: no cover
        self.events.extend(events)

    async def recent(self, limit: int = 100) -> Sequence[DomainEvent]:  # pragma: no cover
        return self.events[-limit:]

    async def since(self, moment: object = None) -> Sequence[DomainEvent]:
        self.asked_for = moment
        return self.events


class FakeRunners:
    def __init__(self, *runners: Runner) -> None:
        self.runners = list(runners)

    async def list_active(self) -> Sequence[Runner]:
        return self.runners


class FakeClock:
    def now(self):  # type: ignore[no-untyped-def]
        return at(hours=2)


async def gather(events: Sequence[DomainEvent], *runners: Runner, since: object = None):  # type: ignore[no-untyped-def]
    query = GatherStats(FakeLog(events), FakeRunners(*runners), FakeClock())  # type: ignore[arg-type]
    return await query(since)  # type: ignore[arg-type]


# ---------------------------------------------------------------- the fold


def test_one_runners_events_fold_into_one_story() -> None:
    told = stories(life("r1", registered=0, took_job=1, retired=11))

    assert list(told) == ["r1"]
    story = told["r1"]
    assert story.repository == str(REPO)
    assert story.pool == "default"
    assert story.registered_at == at(minutes=0)
    assert story.took_job_at == at(minutes=1)
    assert story.ended_at == at(minutes=11)
    assert story.failed is False


def test_an_event_with_no_runner_id_is_ignored_rather_than_grouped() -> None:
    """Every event carries one today; a future one that does not must not become a story."""

    class Odd(DomainEvent):
        pass

    assert stories([Odd(occurred_at=at(minutes=1))]) == {}


async def test_the_gaps_between_events_are_the_numbers() -> None:
    view = await gather(life("r1", registered=0, took_job=1, retired=11))

    assert view.total.runners == 1
    assert view.total.jobs == 1
    assert view.total.wait_seconds == 60
    assert view.total.busy_seconds == 600
    assert view.total.alive_seconds == 660
    assert view.total.utilisation == pytest.approx(600 / 660)


async def test_a_runner_that_never_got_a_job_is_counted_as_idle_capacity() -> None:
    """The case worth seeing: capacity that cost time and returned nothing."""
    view = await gather(life("r1", registered=0, retired=10))

    assert view.total.runners == 1
    assert view.total.jobs == 0
    assert view.total.busy_seconds == 0
    assert view.total.alive_seconds == 600
    assert view.total.idle_runners == 1
    assert view.total.mean_busy_seconds == 0
    assert view.total.utilisation == 0


async def test_a_failure_is_counted_and_its_reason_tallied() -> None:
    view = await gather(
        [*life("r1", registered=0, took_job=1, retired=11), *life("r2", registered=2, failed=3)]
    )

    assert view.total.runners == 2
    assert view.total.failed == 1
    assert view.total.completed == 1
    assert view.total.failure_rate == 0.5
    assert view.failures == [("container creation failed", 1)]


async def test_failure_reasons_are_collapsed_to_one_line() -> None:
    """A reason carrying a traceback would otherwise make every failure look unique."""
    view = await gather(
        life("r1", registered=0, failed=1, reason="image not found\n  at line 3\n  at line 4")
    )

    assert view.failures == [("image not found", 1)]


async def test_a_runner_still_working_has_no_end_and_costs_nothing_yet() -> None:
    """No retirement event, so nothing can be attributed to it without inventing an end."""
    view = await gather(life("r1", registered=0, took_job=1))

    assert view.total.runners == 1
    assert view.total.jobs == 1
    assert view.total.busy_seconds == 0
    assert view.total.alive_seconds == 0
    assert view.total.wait_seconds == 60


async def test_a_clock_that_went_backwards_cannot_subtract_from_the_totals() -> None:
    view = await gather(life("r1", registered=10, took_job=5, retired=1))

    assert view.total.busy_seconds == 0
    assert view.total.alive_seconds == 0
    assert view.total.wait_seconds == 0


async def test_a_repeated_job_assignment_does_not_move_the_busy_period() -> None:
    events = [
        *life("r1", registered=0, took_job=1, retired=11),
        domain_events.RunnerTookJob(occurred_at=at(minutes=6), runner_id="r1", job_id=7),
    ]

    view = await gather(events)

    assert view.total.jobs == 1
    assert view.total.busy_seconds == 600


# ---------------------------------------------------------------- grouping


async def test_work_is_split_by_repository_and_by_pool() -> None:
    view = await gather(
        [
            *life("r1", pool="default", repository=REPO, registered=0, took_job=1, retired=11),
            *life("r2", pool="gpu", repository=OTHER, registered=0, took_job=1, retired=6),
            *life("r3", pool="gpu", repository=OTHER, registered=0, failed=1),
        ]
    )

    repositories = {row.key: row for row in view.by_repository}
    assert repositories[str(REPO)].runners == 1
    assert repositories[str(OTHER)].runners == 2
    assert repositories[str(OTHER)].failed == 1

    pools = {row.key: row for row in view.by_pool}
    assert pools["gpu"].jobs == 1
    assert pools["default"].busy_seconds == 600

    assert view.total.runners == 3


async def test_the_busiest_group_is_listed_first() -> None:
    view = await gather(
        [
            *life("r1", repository=OTHER, registered=0, retired=1),
            *life("r2", repository=REPO, registered=0, retired=1),
            *life("r3", repository=REPO, registered=0, retired=1),
        ]
    )

    assert [row.key for row in view.by_repository] == [str(REPO), str(OTHER)]


async def test_a_runner_registered_before_the_window_is_shown_not_dropped() -> None:
    """Otherwise the rows would silently disagree with the total."""
    view = await gather(
        [
            domain_events.RunnerRetired(
                occurred_at=at(minutes=1), runner_id="ghost", reason="job finished"
            )
        ]
    )

    assert [row.key for row in view.by_repository] == [UNKNOWN]
    assert view.total.runners == 1


# ---------------------------------------------------------------- live and window


async def test_running_runners_come_from_the_projection_not_the_log() -> None:
    """A runner working right now has no end event, so the log cannot see it at all."""
    view = await gather([], make_runner("live-one", pool="gpu"))

    assert view.total.live == 1
    assert {row.key: row.live for row in view.by_pool}["gpu"] == 1
    assert view.events_read == 0


async def test_the_window_is_passed_through_to_the_log() -> None:
    log = FakeLog([])
    query = GatherStats(log, FakeRunners(), FakeClock())  # type: ignore[arg-type]
    moment = at(minutes=30)

    await query(moment)

    assert log.asked_for == moment


async def test_the_window_is_reported_back_so_a_reader_knows_what_it_covers() -> None:
    view = await gather(life("r1", registered=0, retired=1), since=at(minutes=30))

    assert view.since == at(minutes=30)
    assert view.until == at(hours=2)


def test_the_report_says_which_machine_it_is_about() -> None:
    """Several hosts can serve one repository and each daemon counts only its own runners, so
    an unlabelled report is one you cannot put beside another."""
    view = StatsView(since=None, until=at(), total=UsageStats(key=""), host="builders-01")

    heading = stats_tables(view)[0]

    assert isinstance(heading, Text)
    assert "builders-01" in heading.plain
