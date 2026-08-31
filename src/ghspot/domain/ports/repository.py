"""Persistence ports.

Storage holds a *projection*: the daemon rebuilds its view of the fleet from Docker and the
forge on every tick, so losing the store costs history, never correctness.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from ghspot.domain.model.events import DomainEvent
from ghspot.domain.model.runner import Runner, RunnerId


class RunnerRepository(Protocol):
    """Where the daemon remembers the runners it has created."""

    async def save(self, runner: Runner) -> None:
        """Insert or update a runner record."""
        ...

    async def get(self, runner_id: RunnerId) -> Runner | None: ...

    async def list_active(self) -> Sequence[Runner]:
        """Every runner not yet retired, across all pools."""
        ...

    async def list_all(self) -> Sequence[Runner]:
        """Every runner still on record, terminal ones included.

        Distinct from :meth:`list_active` because history is what the operator wants when
        asking why a runner disappeared.
        """
        ...

    async def list_for_pool(self, pool: str) -> Sequence[Runner]: ...

    async def delete(self, runner_id: RunnerId) -> None:
        """Forget a runner entirely. Quiet if unknown."""
        ...


class RunnerLogArchive(Protocol):
    """The last thing a runner's container said, kept after the container is gone.

    Retiring a runner removes its container, and with it the only copy of its output. For a
    runner that finished a job that is survivable — GitHub keeps the job log, and it outlives
    the container by design. For one that *failed*, there is no job log and the container was
    the only witness, so without this the record says a runner failed and nothing at all about
    why.
    """

    async def store(self, runner_id: RunnerId, lines: str) -> None:
        """Keep ``lines`` as this runner's final output, replacing anything already kept."""
        ...

    async def fetch(self, runner_id: RunnerId) -> str | None:
        """What was kept, or ``None`` if nothing was."""
        ...


class EventLog(Protocol):
    """Append-only history, for `ghspot runner history` and post-mortems."""

    async def append(self, events: Sequence[DomainEvent]) -> None: ...

    async def recent(self, limit: int = 100) -> Sequence[DomainEvent]: ...

    async def since(self, moment: datetime | None = None) -> Sequence[DomainEvent]:
        """Everything recorded at or after ``moment``, oldest first.

        Oldest first because the readers fold events into a per-runner story, and a story is
        cheaper to assemble in the order it happened. ``None`` means the whole log.
        """
        ...
