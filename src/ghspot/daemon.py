"""The daemon: run the reconciliation loop until told to stop.

Shutdown is the interesting part. A runner mid-job represents someone's CI run, so the
default is to stop starting new work, leave the busy runners alone, and let systemd's own
timeout decide how long to wait. Killing them would fail builds that were about to pass.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from collections.abc import Callable

from ghspot.application.dto import TickReport
from ghspot.composition import Application
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
        self._on_tick = on_tick
        self.ticks = 0

    def request_stop(self) -> None:
        """Ask the loop to finish the tick it is in and then return."""
        self._stopping.set()

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
            await self._tick_once()
            self.ticks += 1

            if max_ticks is not None and self.ticks >= max_ticks:
                break
            await self._sleep_or_stop()

        log.info("daemon.stopped", ticks=self.ticks)

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
        """Wait out the interval, but wake immediately on a stop request."""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stopping.wait(), timeout=self._interval)


async def run_forever(application: Application, max_ticks: int | None = None) -> int:
    """Run the daemon with signal handling. Returns a process exit code."""
    daemon = Daemon(application)
    loop = asyncio.get_running_loop()

    for signal_number in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(signal_number, daemon.request_stop)

    try:
        await daemon.run(max_ticks=max_ticks)
    finally:
        await application.aclose()
    return 0
