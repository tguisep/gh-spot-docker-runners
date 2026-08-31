"""The loop around the loop: intervals, shutdown, and surviving a bad tick."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import cast

from ghspot.application.commands.housekeeping import ReclaimHostSpace
from ghspot.application.commands.retire import RetireRunner
from ghspot.application.dto import TickReport
from ghspot.composition import Application
from ghspot.daemon import Daemon
from ghspot.domain.model.runner import RunnerId, RunnerState
from ghspot.domain.ports.backend import PruneRequest
from ghspot.infrastructure.config.settings import DaemonSettings, GitHubSettings, Settings
from tests.fakes.adapters import (
    FakeBackend,
    FakeClock,
    FakeForge,
    InMemoryRunnerLogs,
    InMemoryRunnerRepository,
    RecordingPublisher,
)
from tests.unit.conftest import T0, make_runner

CONFIG = """
[github]
token_file = "/tmp/token"

[[pool]]
name = "{name}"
repository = "tguisep/gh-spot-docker-runners"
labels = ["self-hosted", "linux"]

[pool.container]
image = "ghspot/runner:ubuntu-24.04"
"""


class StubReconciler:
    """Counts ticks, and can be told to fail."""

    def __init__(self, fail_times: int = 0) -> None:
        self.calls = 0
        self.fail_times = fail_times
        self.pools: list[str] = []

    def replace_pools(self, pools: object, capacity: object = None) -> None:
        self.pools = [p.spec.name for p in pools]  # type: ignore[attr-defined]

    async def tick(self) -> TickReport:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("the daemon is having a bad day")
        return TickReport(started_at=T0, launched=1)


def build_daemon(
    reconciler: object,
    interval_seconds: float = 0.01,
    *,
    source: Path | None = None,
) -> Daemon:
    """A daemon over fakes. Shutdown retires the fleet, so the repository has to be real."""
    settings = Settings(
        github=GitHubSettings(),
        daemon=DaemonSettings(poll_interval=timedelta(seconds=interval_seconds)),
        pools=(),
        source=source,
    )
    clock = FakeClock(T0)
    backend = FakeBackend(now=T0)
    repository = InMemoryRunnerRepository()
    events = RecordingPublisher()
    application = Application(
        settings=settings,
        forge=FakeForge(),  # type: ignore[arg-type]
        backend=backend,  # type: ignore[arg-type]
        runners=repository,  # type: ignore[arg-type]
        events=events,  # type: ignore[arg-type]
        runner_logs=InMemoryRunnerLogs(),  # type: ignore[arg-type]
        reconciler=reconciler,  # type: ignore[arg-type]
        housekeeping=ReclaimHostSpace(
            backend=backend,
            clock=clock,
            every=timedelta(hours=1),
            request=PruneRequest(),
        ),
        clock=clock,  # type: ignore[arg-type]
        provision=None,  # type: ignore[arg-type]
        retire=RetireRunner(FakeForge(), backend, repository, clock, events),
    )
    return Daemon(application)


def _fake_repository(daemon: Daemon) -> InMemoryRunnerRepository:
    """The harness wires a fake in; Application declares the SQLite one."""
    return cast(InMemoryRunnerRepository, daemon._application.runners)


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


# ---------------------------------------------------------------- stop takes the fleet


async def test_stopping_retires_every_runner() -> None:
    """The host is the master. A runner outliving the daemon keeps taking jobs with nothing
    enforcing its timeouts, nothing reaping it, and a registration GitHub still honours."""
    daemon = build_daemon(StubReconciler())
    repository = _fake_repository(daemon)
    for name in ("r1", "r2"):
        await repository.save(make_runner(name, state=RunnerState.IDLE))

    await daemon.run(max_ticks=1)

    assert [r.state for r in repository.saved.values()] == [
        RunnerState.RETIRED,
        RunnerState.RETIRED,
    ]


async def test_a_busy_runner_goes_too() -> None:
    """The deliberate trade: the job fails and is re-run. Leaving it behind would leave a
    machine running somebody's build with nothing watching it finish."""
    daemon = build_daemon(StubReconciler())
    repository = _fake_repository(daemon)
    await repository.save(make_runner("r1", state=RunnerState.BUSY))

    await daemon.run(max_ticks=1)

    assert repository.saved[RunnerId("r1")].state is RunnerState.RETIRED


async def test_a_container_that_will_not_go_does_not_stop_the_shutdown() -> None:
    """The daemon is on its way out. A stuck container belongs in the journal, not in a
    non-zero exit that systemd will report as a failed unit."""
    daemon = build_daemon(StubReconciler())
    backend = cast(FakeBackend, daemon._application.backend)
    backend.fail_on.update({"stop", "remove"})
    await _fake_repository(daemon).save(make_runner("r1", state=RunnerState.IDLE))

    await daemon.run(max_ticks=1)  # must not raise


# ---------------------------------------------------------------- reload leaves them alone


async def test_reloading_applies_the_new_pools_without_touching_runners(tmp_path: Path) -> None:
    """The whole reason reload exists apart from restart: changing a label must not cost the
    builds that are running."""
    config = tmp_path / "config.toml"
    config.write_text(CONFIG.format(name="rebuilt"))
    daemon = build_daemon(StubReconciler(), source=config)
    repository = _fake_repository(daemon)
    await repository.save(make_runner("r1", state=RunnerState.BUSY))

    # Directly, not through run(): a loop that finishes retires the fleet, which is the very
    # thing reload exists to avoid and would hide what this asserts.
    daemon.request_reload()
    daemon._reload()

    assert daemon.reloads == 1
    assert [pool.spec.name for pool in daemon._application.settings.pools] == ["rebuilt"]
    assert repository.saved[RunnerId("r1")].state is RunnerState.BUSY


async def test_a_configuration_that_no_longer_parses_is_refused_not_fatal(
    tmp_path: Path,
) -> None:
    """Refusing is recoverable. Exiting on a typo is a fleet down until somebody notices."""
    config = tmp_path / "config.toml"
    config.write_text("this is not toml [[[")
    daemon = build_daemon(StubReconciler(), source=config)

    daemon.request_reload()
    daemon._reload()

    assert daemon.reloads == 0
    assert daemon._application.settings.pools == ()
