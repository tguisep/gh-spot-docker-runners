"""The loop around the loop: intervals, shutdown, and surviving a bad tick."""

from __future__ import annotations

import asyncio
from dataclasses import replace

from ghspot.application.dto import TickReport
from ghspot.composition import Application
from ghspot.daemon import Daemon
from ghspot.infrastructure.config.settings import DaemonSettings, GitHubSettings, Settings
from tests.unit.conftest import T0


class StubReconciler:
    """Counts ticks, and can be told to fail."""

    def __init__(self, fail_times: int = 0) -> None:
        self.calls = 0
        self.fail_times = fail_times

    async def tick(self) -> TickReport:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("the daemon is having a bad day")
        return TickReport(started_at=T0, launched=1)


def build_daemon(reconciler: object, interval_seconds: float = 0.01) -> Daemon:
    settings = Settings(
        github=GitHubSettings(),
        daemon=DaemonSettings(
            poll_interval=__import__("datetime").timedelta(seconds=interval_seconds)
        ),
        pools=(),
    )
    application = Application(
        settings=settings,
        forge=None,  # type: ignore[arg-type]
        backend=None,  # type: ignore[arg-type]
        runners=None,  # type: ignore[arg-type]
        events=None,  # type: ignore[arg-type]
        reconciler=reconciler,  # type: ignore[arg-type]
        clock=None,  # type: ignore[arg-type]
        provision=None,  # type: ignore[arg-type]
        retire=None,  # type: ignore[arg-type]
    )
    return Daemon(application)


async def test_the_loop_ticks_until_the_limit() -> None:
    reconciler = StubReconciler()
    daemon = build_daemon(reconciler)

    await daemon.run(max_ticks=3)

    assert reconciler.calls == 3
    assert daemon.ticks == 3


async def test_a_tick_that_raises_does_not_end_the_daemon() -> None:
    """The next tick re-observes reality from scratch; nothing is carried between them."""
    reconciler = StubReconciler(fail_times=2)
    daemon = build_daemon(reconciler)

    await daemon.run(max_ticks=4)

    assert reconciler.calls == 4
    assert daemon.ticks == 4


async def test_a_stop_request_wakes_the_loop_out_of_its_sleep() -> None:
    """A daemon that only noticed SIGTERM after a 15s nap would look hung."""
    reconciler = StubReconciler()
    daemon = build_daemon(reconciler, interval_seconds=30)

    async def stop_soon() -> None:
        await asyncio.sleep(0.05)
        daemon.request_stop()

    await asyncio.wait_for(asyncio.gather(daemon.run(), stop_soon()), timeout=5)

    assert daemon.ticks == 1


async def test_a_stop_requested_before_the_loop_starts_runs_nothing() -> None:
    """The contract is to finish a tick already in flight, not to start one on the way out."""
    reconciler = StubReconciler()
    daemon = build_daemon(reconciler)
    daemon.request_stop()

    await daemon.run()

    assert reconciler.calls == 0


async def test_reports_are_handed_to_the_callback() -> None:
    reconciler = StubReconciler()
    daemon = build_daemon(reconciler)
    seen: list[TickReport] = []
    daemon._on_tick = seen.append

    await daemon.run(max_ticks=2)

    assert len(seen) == 2
    assert all(report.launched == 1 for report in seen)


def test_a_report_knows_whether_anything_happened() -> None:
    quiet = TickReport(started_at=T0)
    busy = TickReport(started_at=T0, repaired=1)

    assert not quiet.changed_anything
    assert busy.changed_anything
    assert replace(quiet, terminated=1).changed_anything
