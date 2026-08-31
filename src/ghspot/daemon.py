"""The daemon: run the reconciliation loop until told to stop.

Shutdown is the interesting part, and the rule is that **the host is the master**: when the
daemon stops, the fleet stops. Anything else leaves runners taking jobs on a machine with
nothing watching them — no `idle_timeout`, no `max_job_duration`, no cleanup, and a
registration on GitHub that outlives the process that made it.

That costs the jobs in flight, which fail and have to be re-run. It is the deliberate trade:
a CI run can be replayed, and a fleet nobody owns cannot be reasoned about at all.

`SIGHUP` is the exception. Reloading re-reads the configuration and leaves every runner
exactly where it is, which is what makes changing a label or a ceiling a routine act rather
than something you schedule around the builds.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from collections.abc import Callable

from ghspot.application.dto import TickReport
from ghspot.composition import Application
from ghspot.infrastructure.config.settings import ConfigError
from ghspot.infrastructure.config.settings import load as load_settings
from ghspot.infrastructure.logging.setup import get_logger

log = get_logger("ghspot.daemon")


class Daemon:
    """Runs :meth:`ReconciliationService.tick` on an interval."""

    def __init__(
        self,
        application: Application,
        *,
        on_tick: Callable[[TickReport], None] | None = None,
    ) -> None:
        self._application = application
        self._interval = application.settings.daemon.poll_interval.total_seconds()
        self._stopping = asyncio.Event()
        self._reloading = asyncio.Event()
        self._stopping_sleep = asyncio.Event()
        self._on_tick = on_tick
        self.ticks = 0
        self.reloads = 0

    def request_stop(self) -> None:
        """Ask the loop to finish the tick it is in and then return."""
        self._stopping.set()

    def request_reload(self) -> None:
        """Ask the loop to re-read the configuration before its next tick."""
        self._reloading.set()
        self._stopping_sleep.set()

    async def run(self, max_ticks: int | None = None) -> None:
        """Reconcile until asked to stop.

        ``max_ticks`` exists for tests and for ``ghspot daemon --once``.
        """
        log.info(
            "daemon.started",
            pools=[pool.spec.name for pool in self._application.settings.pools],
            interval_seconds=self._interval,
        )

        while not self._stopping.is_set():
            if self._reloading.is_set():
                self._reload()

            await self._tick_once()
            self.ticks += 1

            if max_ticks is not None and self.ticks >= max_ticks:
                break
            await self._sleep_or_stop()

        await self._retire_the_fleet()
        log.info("daemon.stopped", ticks=self.ticks, reloads=self.reloads)

    async def _tick_once(self) -> None:
        try:
            report = await self._application.reconciler.tick()
        except Exception as error:
            # A tick that raises must not end the daemon: the next one re-observes reality
            # from scratch and will usually succeed. Nothing is remembered in between.
            log.error("tick.failed", error=str(error), exc_info=True)
            return

        self._report(report)
        if self._on_tick is not None:
            self._on_tick(report)

        await self._reclaim()

    async def _reclaim(self) -> None:
        """Clear what jobs left on the host, when it is due.

        Deliberately after reconciliation rather than before: reconciling is what the daemon
        is for, and housekeeping should never delay starting a runner someone is waiting on.
        """
        result = await self._application.housekeeping()
        if result is None:
            return

        for problem in result.errors:
            log.warning("housekeeping.error", detail=problem)

        if result.removed_anything:
            log.info(
                "housekeeping.done",
                containers=result.containers,
                images=result.images,
                volumes=result.volumes,
                reclaimed_mb=round(result.reclaimed_bytes / 1_000_000, 1),
            )

    def _report(self, report: TickReport) -> None:
        for problem in report.errors:
            log.warning("tick.error", detail=problem)

        if report.changed_anything or report.errors:
            log.info(
                "tick.done",
                launched=report.launched,
                retired=report.retired,
                terminated=report.terminated,
                repaired=report.repaired,
                queued=report.queued_jobs,
                seconds=round(report.duration_seconds, 3),
                notes=report.notes,
            )
        else:
            log.debug("tick.quiet", queued=report.queued_jobs)

    async def _sleep_or_stop(self) -> None:
        """Wait out the interval, but wake immediately on a stop or a reload."""
        self._stopping_sleep.clear()
        waiters = [
            asyncio.create_task(self._stopping.wait()),
            asyncio.create_task(self._stopping_sleep.wait()),
        ]
        try:
            await asyncio.wait(waiters, timeout=self._interval, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for waiter in waiters:
                waiter.cancel()

    def _reload(self) -> None:
        """Re-read the configuration and apply what can be applied without a restart.

        Pools, labels and ceilings — the things an operator changes — are swapped into the
        reconciler in place. Runners are not touched: a label change must not cost the builds
        that are running, which is the whole reason reload exists separately from restart.

        A file that no longer parses leaves the daemon on what it already had. Refusing to
        reload is recoverable; exiting on a typo is a fleet down for as long as nobody notices.
        """
        self._reloading.clear()
        source = self._application.settings.source
        try:
            fresh = load_settings(source) if source is not None else None
        except (ConfigError, OSError) as error:
            log.error("reload.rejected", error=str(error), source=str(source))
            return

        if fresh is None:
            return

        self._application.settings = fresh
        self._application.reconciler.replace_pools(fresh.pools, fresh.capacity)
        self._interval = fresh.daemon.poll_interval.total_seconds()
        self.reloads += 1
        log.info("reload.applied", pools=[pool.spec.name for pool in fresh.pools])

    async def _retire_the_fleet(self) -> None:
        """Take every runner down with the daemon.

        The host is the master. A runner outliving the process that made it keeps taking work
        with nothing enforcing its timeouts, nothing reaping it, and a registration on GitHub
        that no longer corresponds to anything watching.

        Concurrent, because each container is given its stop timeout to exit and doing that in
        sequence would blow through systemd's `TimeoutStopSec` on any real fleet.
        """
        runners = [r for r in await self._application.runners.list_active() if not r.is_terminal]
        if not runners:
            return

        log.info("daemon.retiring", count=len(runners))
        results = await asyncio.gather(
            *(self._application.retire(runner, reason="daemon stopping") for runner in runners),
            return_exceptions=True,
        )
        failed = [r for r in results if isinstance(r, BaseException)]
        if failed:
            # Said out loud rather than raised: the daemon is on its way out, and a container
            # that would not go is something to find in the journal, not a non-zero exit.
            log.error("daemon.retire_failed", count=len(failed), first=str(failed[0]))


async def run_forever(application: Application, max_ticks: int | None = None) -> int:
    """Run the daemon, and the API alongside it when one is configured.

    Both are cancelled together: an API still answering after the loop has stopped would
    report a fleet nobody is reconciling.
    """
    daemon = Daemon(application)
    loop = asyncio.get_running_loop()

    for signal_number in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(signal_number, daemon.request_stop)

    with contextlib.suppress(NotImplementedError):
        loop.add_signal_handler(signal.SIGHUP, daemon.request_reload)

    api_task = _start_api(application, daemon)

    try:
        await daemon.run(max_ticks=max_ticks)
    finally:
        if api_task is not None:
            api_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await api_task
        await application.aclose()
    return 0


def _start_api(application: Application, daemon: Daemon) -> asyncio.Task[None] | None:
    """Serve the REST API in the background, if ``daemon.api_bind`` is configured."""
    bind = application.settings.daemon.api_bind
    if not bind:
        return None

    host, _, port = bind.rpartition(":")
    if not host or not port.isdigit():
        log.error("api.bad_bind", bind=bind, hint="expected host:port, e.g. 127.0.0.1:8770")
        return None

    import uvicorn

    from ghspot.interfaces.api.app import create_app

    # The API borrows the daemon's application, so it reports the same fleet the loop is
    # reconciling rather than opening its own connections to Docker and GitHub.
    config = uvicorn.Config(
        create_app(application),
        host=host,
        port=int(port),
        log_config=None,
        lifespan="off",
    )
    log.info("api.listening", bind=bind)
    return asyncio.create_task(uvicorn.Server(config).serve())
