"""Reclaim what jobs leave on the host.

A job reaches the host's Docker daemon through the mounted socket, so the images it builds,
the images it pulls and the volumes it creates are the host's afterwards — the runner
container being removed does not take them with it.

This bounds that. It does not eliminate it, and the difference matters: a job that leaves a
container *running* is never touched, because nothing distinguishes it from something the
operator started on purpose. Genuine zero-residue needs each runner to have its own Docker
daemon, which is a different architecture.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from ghspot.domain.errors import GhSpotError
from ghspot.domain.ports.backend import PruneReport, PruneRequest, RunnerBackend
from ghspot.domain.ports.system import Clock

_SIZE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgt]?)b?\s*$", re.IGNORECASE)
_SCALE = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}


def parse_size(value: str) -> int:
    """Turn ``10g`` into bytes. Sizes are written the way `docker` writes them."""
    match = _SIZE.match(value)
    if not match:
        raise ValueError(f"{value!r} is not a size. Use forms like '512m', '10g'.")
    amount, unit = match.groups()
    return int(float(amount) * _SCALE[unit.lower()])


class ReclaimHostSpace:
    """Run Docker's prune operations on a schedule, and remember when it last ran.

    The interval is kept here rather than in the loop so that "every hour" means an hour of
    wall clock, not a number of ticks — which would change meaning whenever the poll interval
    was tuned.
    """

    def __init__(
        self,
        backend: RunnerBackend,
        clock: Clock,
        *,
        every: timedelta,
        request: PruneRequest,
        enabled: bool = True,
    ) -> None:
        self._backend = backend
        self._clock = clock
        self._every = every
        self._request = request
        self._enabled = enabled and not request.is_noop
        self._last_run: datetime | None = None

    @property
    def due(self) -> bool:
        if not self._enabled:
            return False
        if self._last_run is None:
            return True
        return (self._clock.now() - self._last_run) >= self._every

    async def __call__(self, *, force: bool = False) -> PruneReport | None:
        """Reclaim if due. Returns ``None`` when there was nothing to do."""
        if not force and not self.due:
            return None
        if not self._enabled:
            return None

        # Recorded before the attempt, so a daemon failing to prune does not retry every
        # tick and bury the reason.
        self._last_run = self._clock.now()

        try:
            return await self._backend.prune(self._request)
        except GhSpotError as error:
            return PruneReport(errors=(str(error),))
