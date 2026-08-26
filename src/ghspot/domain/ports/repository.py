"""Persistence ports.

Storage holds a *projection*: the daemon rebuilds its view of the fleet from Docker and the
forge on every tick, so losing the store costs history, never correctness.
"""

from __future__ import annotations

from collections.abc import Sequence
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

    async def list_for_pool(self, pool: str) -> Sequence[Runner]: ...

    async def delete(self, runner_id: RunnerId) -> None:
        """Forget a runner entirely. Quiet if unknown."""
        ...


class EventLog(Protocol):
    """Append-only history, for `ghspot runner history` and post-mortems."""

    async def append(self, events: Sequence[DomainEvent]) -> None: ...

    async def recent(self, limit: int = 100) -> Sequence[DomainEvent]: ...
